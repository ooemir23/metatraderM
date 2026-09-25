import os
import hashlib
import logging
import time
import json
import threading
import math
import rpyc
import tempfile
import uuid
from pathlib import Path
from app import mt5_bridge
from app.order_journal import OrderJournal
from app.trading_lock import TradingLock
from app.reports import build_report
from typing import Dict, Any, List, Optional

TIMEFRAME_NAMES = {
    **{m: f"TIMEFRAME_M{m}" for m in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)},
    **{h * 60: f"TIMEFRAME_H{h}" for h in (1, 2, 3, 4, 6, 8, 12)},
    1440: "TIMEFRAME_D1", 10080: "TIMEFRAME_W1", 43200: "TIMEFRAME_MN1",
}

logger = logging.getLogger("MT5Client")
logger.setLevel(logging.INFO)

NATIVE_CLOSE_SCRIPT = '''
import MetaTrader5 as mt5

def _execute_close_deal(p, expected_login=0, expected_server=""):
    ticket, symbol = int(p.ticket), str(p.symbol)
    is_buy = p.type == 0
    for filling in (1, 0, 2):
        account = mt5.account_info()
        if expected_login and (account is None or int(account.login) != expected_login or str(account.server) != expected_server):
            return {"success": False, "ticket": ticket, "error": "Aktif MT5 hesabı değişti; kapatma engellendi."}
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return {"success": False, "ticket": ticket, "error": "Fiyat alınamadı"}
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "position": ticket,
            "symbol": symbol, "volume": float(p.volume),
            "type": 1 if is_buy else 0, "price": float(tick.bid if is_buy else tick.ask),
            "deviation": 50, "magic": int(getattr(p, "magic", 0)),
            "comment": "Close from HMA Web", "type_time": 0, "type_filling": filling,
        }
        result = mt5.order_send(request)
        if result is None:
            return {"success": False, "uncertain": True, "ticket": ticket, "error": "Kapatma sonucu belirsiz"}
        code = int(result.retcode)
        if code == 10009:
            return {"success": True, "ticket": ticket, "profit": float(p.profit)}
        if code != 10030:
            return {"success": False, "ticket": ticket, "partial": code == 10010,
                    "pending": code == 10008, "uncertain": code in (10012, 10031), "retcode": code, "error": str(result.comment)}
    return {"success": False, "ticket": ticket, "error": str(result.comment)}

def hma_native_close_filter(filter_type="all", expected_login=0, expected_server=""):
    account = mt5.account_info()
    if expected_login and (account is None or int(account.login) != expected_login or str(account.server) != expected_server):
        return {"success": False, "closed_count": 0, "total_matched": 0, "errors": ["Aktif MT5 hesabı değişti; kapatma engellendi."]}
    positions = mt5.positions_get()
    if positions is None:
        return {"success": False, "closed_count": 0, "total_matched": 0, "errors": ["No positions or MT5 error: " + str(mt5.last_error())]}

    targets = []
    for p in positions:
        profit = float(p.profit)
        if filter_type == "profit" and profit > 0:
            targets.append(p)
        elif filter_type == "loss" and profit < 0:
            targets.append(p)
        elif filter_type == "all":
            targets.append(p)

    if not targets:
        return {"success": True, "closed_count": 0, "total_matched": 0, "errors": []}

    closed_count = 0
    uncertain = pending = False
    errors = []

    for p in targets:
        r = _execute_close_deal(p, expected_login, expected_server)
        if r.get("success"):
            closed_count += 1
        else:
            uncertain = uncertain or r.get("uncertain", False)
            pending = pending or r.get("pending", False)
            errors.append("Ticket #" + str(p.ticket) + ": " + str(r.get("error")))

    return {
        "success": len(errors) == 0,
        "closed_count": closed_count,
        "uncertain": uncertain, "pending": pending,
        "total_matched": len(targets),
        "errors": errors
    }

def hma_native_close_ticket(ticket, expected_magic=None, expected_login=0, expected_server=""):
    ticket = int(ticket)
    account = mt5.account_info()
    if expected_login and (account is None or int(account.login) != expected_login or str(account.server) != expected_server):
        return {"success": False, "error": "Aktif MT5 hesabı değişti; kapatma engellendi."}
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        all_p = mt5.positions_get()
        if all_p:
            positions = [p for p in all_p if p.ticket == ticket]
    if not positions:
        return {"success": False, "error": "Position #" + str(ticket) + " bulunamadı"}

    if expected_magic is not None:
        account = mt5.account_info()
        if account is None or account.margin_mode != 2 or positions[0].magic != expected_magic:
            return {"success": False, "error": "Strateji sahipliği/hedging hesabı doğrulanamadı."}
    return _execute_close_deal(positions[0], expected_login, expected_server)
'''

NATIVE_HISTORY_SCRIPT = '''
import MetaTrader5 as mt5
import time
from datetime import datetime, timedelta, timezone

def hma_native_get_history(days=30, clock_offset=0, include_ai_entries=False):
    try:
        days_int = int(days) if days else 30
        to_date = datetime.now(timezone.utc) + timedelta(seconds=int(clock_offset))
        from_date = to_date - timedelta(days=days_int)
        
        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None:
            raise RuntimeError("İşlem geçmişi okunamadı")
            
        res = []
        for d in deals:
            deal_symbol = str(getattr(d, "symbol", ""))
            deal_entry = int(getattr(d, "entry", -1))
            deal_profit = float(getattr(d, "profit", 0.0))
            deal_type = int(getattr(d, "type", -1))
            
            # Filter: we want exit deals (entry in 1: OUT, 2: INOUT, 3: OUT_BY) or deals with realized profit/loss
            # Must have a trading symbol (excludes deposit/withdrawal balance deals)
            if deal_symbol != "" and (deal_entry in (1, 2, 3) or deal_profit != 0 or
                                      (include_ai_entries and int(getattr(d, "magic", 0)) == 123462)):
                t = int(getattr(d, "time", 0))
                try:
                    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"
                    date_str = time.strftime("%Y-%m-%d", time.localtime(t)) if t else "-"
                except Exception:
                    time_str = "-"
                    date_str = "-"
                    
                type_name = "BUY" if deal_type == 0 else ("SELL" if deal_type == 1 else str(deal_type))
                
                res.append({
                    "ticket": int(d.ticket),
                    "magic": int(getattr(d, "magic", 0)),
                    "order": int(d.order),
                    "position_id": int(getattr(d, "position_id", d.order)),
                    "symbol": deal_symbol,
                    "type": type_name,
                    "entry": deal_entry,
                    "volume": float(getattr(d, "volume", 0.0)),
                    "price": round(float(getattr(d, "price", 0.0)), 5),
                    "profit": round(deal_profit, 2),
                    "commission": round(float(getattr(d, "commission", 0.0)), 2),
                    "swap": round(float(getattr(d, "swap", 0.0)), 2),
                    "fee": round(float(getattr(d, "fee", 0.0)), 2),
                    "comment": str(getattr(d, "comment", "")),
                    "time": time_str,
                    "date": date_str,
                    "timestamp": t
                })
        res.reverse()
        return res
    except Exception:
        raise
'''

class MT5DataError(RuntimeError):
    pass


class MT5Client:
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.getenv("MT5_HOST", "metatrader5")
        self.port = int(port or os.getenv("MT5_PORT", "8001"))
        self.mt5 = None
        self.is_connected = False
        self.last_connect_attempt = 0
        self.reconnect_cooldown = 3
        self.last_error_msg = ""
        self.last_ping_time = 0
        self.last_auto_login_attempt = 0
        self.auto_login_cooldown = 20
        self._lock = TradingLock()
        self.journal = OrderJournal()
        self._reports_cache = {}
        self.daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "500"))
        self.max_order_lots = float(os.getenv("MAX_ORDER_LOTS", "0.10"))
        self.max_total_lots = float(os.getenv("MAX_TOTAL_OPEN_LOTS", "0.50"))
        self.max_open_orders = int(os.getenv("MAX_OPEN_ORDERS", "10"))
        self.ai_max_trade_risk_pct = float(os.getenv("AI_MAX_TRADE_RISK_PCT", "1"))
        self.tick_clock_offset = int(os.getenv('MT5_TICK_CLOCK_OFFSET_SECONDS', '0'))
        if abs(self.tick_clock_offset) > 14*3600 or self.tick_clock_offset % 3600:
            raise ValueError('MT5_TICK_CLOCK_OFFSET_SECONDS must be a whole-hour offset within 14 hours')
        self.automation_stopped = threading.Event()
        self._bridge_ready = False
        self._account_cache = None
        self._account_cache_time = 0.0
        self._positions_cache = None
        self._positions_cache_time = 0.0
        self._price_cache = {}
        self._price_cache_time = {}

        # Default broker credentials (Tickmill Demo)
        self.login_id = int(os.getenv("MT5_LOGIN", "0"))
        self.password = os.getenv("MT5_PASSWORD", "")
        self.server = os.getenv("MT5_SERVER", "Tickmill-Demo")
        self.account_type = os.getenv("MT5_ACCOUNT_TYPE", "").upper()

        # Load persisted credentials from volume if available
        self.load_credentials()

        self.conn = None
        self.connected_at = 0

    def load_credentials(self):
        for path in ["/config/credentials.json", "/tmp/credentials.json"]:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("login"): self.login_id = int(data["login"])
                    if data.get("password"): self.password = str(data["password"])
                    if data.get("server"): self.server = str(data["server"])
                    self.account_type = str(data.get("account_type") or self.account_type).upper()
                    logger.info(f"Loaded credentials from {path}: #{self.login_id} @ {self.server}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read {path}: {e}")

    def save_credentials(self):
        data = {
            "login": self.login_id,
            "password": self.password,
            "server": self.server,
            "account_type": self.account_type
        }
        for path in ["/config/credentials.json", "/tmp/credentials.json"]:
            try:
                d = os.path.dirname(path)
                if os.path.exists(d) or d == "/tmp":
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="w", dir=d, delete=False, encoding="utf-8") as f:
                            temp_path = f.name
                            os.chmod(temp_path, 0o600)
                            json.dump(data, f)
                        os.replace(temp_path, path)
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            os.unlink(temp_path)
                    logger.info(f"Saved credentials to {path}")
                    return
            except Exception as e:
                logger.warning(f"Could not save credentials to {path}: {e}")

    def _reset_connection(self):
        self._bridge_ready = False
        old_conn = self.conn
        self.conn = self.mt5 = None
        self.is_connected = False
        self.connected_at = self.last_ping_time = 0
        self._account_cache = self._positions_cache = None
        self._price_cache.clear()
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass

    def connect(self) -> bool:
        # One connection attempt at a time, including API and background callers.
        with self._lock:
            if self.is_connected and self.mt5 is not None:
                return True
            now = time.time()
            if now - self.last_connect_attempt < self.reconnect_cooldown:
                return False
            self.last_connect_attempt = now
            self._reset_connection()
            conn = stream = None
            try:
                stream = rpyc.SocketStream.connect(self.host, self.port, timeout=5)
                conn = rpyc.utils.factory.connect_stream(
                    stream, rpyc.classic.SlaveService,
                    config={"sync_request_timeout": 15},
                )
                mt5 = conn.modules.MetaTrader5
                ready = mt5.initialize(timeout=10000)
                if not ready:
                    ready = mt5.initialize(
                        path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
                        timeout=10000,
                    )
                if not ready or mt5.terminal_info() is None:
                    raise RuntimeError(f"MT5 başlatılamadı: {mt5.last_error()}")
                self.conn, self.mt5 = conn, mt5
                self.is_connected = True
                self.connected_at = self.last_ping_time = time.time()
                self.last_error_msg = ""
                logger.info("Connected to MT5 on %s:%s", self.host, self.port)
                return True
            except Exception as exc:
                self.last_error_msg = str(exc)
                logger.warning("MT5 connection failed: %s", exc)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                elif stream is not None:
                    stream.close()
                self._reset_connection()
                return False

    def login(self, login_id: int, password: str, server: str, account_type: str) -> Dict[str, Any]:
        if not self._lock.acquire(timeout=15.0):
            return {"success": False, "error": "MT5 meşgul, lütfen birkaç saniye sonra tekrar deneyin."}
        try:
            if not self.ensure_connected():
                self.connect()

            if not self.mt5:
                return {"success": False, "error": f"MT5 sunucusuna ulaşılamadı. ({self.last_error_msg})"}

            try:
                server_clean = str(server).strip()
                account_type = str(account_type).upper()
                if not server_clean or account_type not in ("DEMO", "REAL"):
                    return {"success": False, "error": "Geçerli sunucu ve Demo/Gerçek hesap türü seçin."}

                ok = False
                try:
                    ok = bool(self.mt5.initialize(login=int(login_id), password=str(password), server=server_clean, timeout=10000))
                except Exception:
                    ok = False

                if not ok:
                    try:
                        ok = bool(self.mt5.login(login=int(login_id), password=str(password), server=server_clean, timeout=10000))
                    except Exception:
                        ok = False

                if ok:
                    active = self.mt5.account_info()
                    if active is None or int(active.login) != int(login_id) or str(active.server) != server_clean:
                        return {"success": False, "error": "Giriş sonrası aktif broker hesabı doğrulanamadı."}
                    actual_mode = int(active.trade_mode)
                    if actual_mode != (0 if account_type == "DEMO" else 2):
                        self.invalidate_trading_cache()
                        actual_name = {0: "Demo", 1: "Yarışma", 2: "Gerçek"}.get(actual_mode, "Bilinmeyen")
                        return {"success": False, "error": f"Seçilen hesap türü broker hesabıyla uyuşmuyor (broker: {actual_name}). Doğru tür ve sunucuyla yeniden giriş yapın."}
                    self.login_id = int(login_id)
                    self.password = str(password)
                    self.server = server_clean
                    self.account_type = account_type
                    self.is_connected = True
                    self._account_cache = self._positions_cache = None
                    self._price_cache.clear()
                    self.save_credentials()
                    return {"success": True, "login": login_id, "server": server_clean, "account_type": account_type}
                else:
                    err = self.mt5.last_error()
                    return {"success": False, "error": f"Login hatası: {err}"}
            except Exception:
                return {"success": False, "error": "Giriş doğrulanamadı; broker sunucusu ve hesap türünü kontrol edin."}
        finally:
            self._lock.release()

    def ensure_connected(self) -> bool:
        with self._lock:
            if self.is_connected and self.mt5 is not None:
                if time.time() - self.last_ping_time < 5:
                    return True
                try:
                    info = self.mt5.terminal_info()
                    if info is not None:
                        self.last_ping_time = time.time()
                        return True
                    self.last_error_msg = "MT5 terminali yanıt vermiyor"
                except Exception as exc:
                    self.last_error_msg = str(exc)
                self._reset_connection()
            return self.connect()

    def get_account_info(self) -> Dict[str, Any]:
        now = time.time()
        if self._account_cache and (now - self._account_cache_time < 0.5) and self.is_connected:
            return self._account_cache

        if not self.ensure_connected():
            return {
                "connected": False,
                "login": self.login_id,
                "balance": 0.0,
                "equity": 0.0,
                "profit": 0.0,
                "margin": 0.0,
                "margin_free": 0.0,
                "margin_level": 0.0,
                "currency": "USD",
                "server": self.server,
                "error": self.last_error_msg or "MT5 Bağlantısı Bekleniyor",
                "connected_since": None,
                "server_time": time.strftime("%H:%M:%S")
            }

        with self._lock:
            try:
                acc = self.mt5.account_info()
                now = time.time()
                if acc is None and self.login_id and self.password and self.account_type in ("DEMO", "REAL"):
                    if now - self.last_auto_login_attempt > self.auto_login_cooldown:
                        self.last_auto_login_attempt = now
                        try:
                            logger.info(f"account_info returned None, attempting broker login #{self.login_id} @ {self.server}...")
                            self.mt5.login(login=int(self.login_id), password=str(self.password), server=str(self.server), timeout=10000)
                            acc = self.mt5.account_info()
                        except Exception as le:
                            logger.debug(f"Auto-login in get_account_info error: {le}")

                if acc is None:
                    return {
                        "connected": False,
                        "terminal_ready": True,
                        "error": "Broker hesabına giriş bekleniyor",
                        "login": self.login_id,
                        "server": self.server,
                        "connected_since": None,
                        "server_time": time.strftime("%H:%M:%S")
                    }

                res = {
                    "connected": True,
                    "login": acc.login,
                    "account_type": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(int(acc.trade_mode), "UNKNOWN"),
                    "account_mismatch": self.login_id <= 0 or int(acc.login) != self.login_id or str(acc.server) != self.server
                                        or int(acc.trade_mode) != {"DEMO": 0, "REAL": 2}.get(self.account_type),
                    "balance": round(acc.balance, 2),
                    "equity": round(acc.equity, 2),
                    "profit": round(acc.profit, 2),
                    "margin": round(acc.margin, 2),
                    "margin_free": round(acc.margin_free, 2),
                    "margin_level": round(acc.margin_level, 2) if acc.margin > 0 else 100.0,
                    "currency": acc.currency,
                    "server": acc.server,
                    "leverage": acc.leverage,
                    "connected_since": time.strftime("%H:%M:%S", time.localtime(self.connected_at)) if self.connected_at else time.strftime("%H:%M:%S"),
                    "server_time": time.strftime("%H:%M:%S")
                }
                self._account_cache = res
                self._account_cache_time = time.time()
                return res
            except Exception as e:
                logger.error(f"Error fetching account info: {e}")
                return {"connected": False, "error": str(e)}

    def invalidate_trading_cache(self):
        self._reports_cache.clear()
        self._account_cache = self._positions_cache = None
        self._price_cache.clear()

    def _bridge(self, operation, *args):
        if not self.conn:
            return getattr(mt5_bridge, operation)(self.mt5, *args)
        if not self._bridge_ready:
            source = Path(mt5_bridge.__file__).read_text(encoding="utf-8")
            self.conn.execute("import MetaTrader5 as mt5\n" + source)
            self._bridge_ready = True
        # Serialize remotely: never iterate an RPyC netref per row/field.
        return json.loads(str(self.conn.eval(
            f"json.dumps({operation}(mt5, *{args!r}), allow_nan=False)")))

    def _selected_account(self):
        expected_mode = {"DEMO": 0, "REAL": 2}.get(self.account_type)
        if self.login_id <= 0 or not self.server or expected_mode is None:
            raise MT5DataError("İşlem için panelden Demo veya Gerçek hesap seçerek yeniden giriş yapın.")
        active = self._bridge("account_status")
        if active != [self.login_id, self.server, expected_mode]:
            raise MT5DataError("Aktif MT5 hesabı, sunucusu veya türü kayıtlı seçimle uyuşmuyor; işlem engellendi.")
        return active[:2]

    def trade_preview(self, symbol, order_type, volume, sl_points=0, pending_type=None, entry_price=None):
        with self._lock:
            if not self.ensure_connected():
                return {'success': False, 'error': 'MT5 bağlı değil.'}
            try:
                login, server = self._selected_account()
                return self._bridge('trade_preview', symbol.upper(), order_type, volume, sl_points,
                                    pending_type, entry_price, login, server, self.tick_clock_offset)
            except (MT5DataError, Exception) as exc:
                return {'success': False, 'error': str(exc)}

    def get_symbol_spec(self, symbol):
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError('MT5 bağlı değil.')
            self._selected_account()
            try:
                return self._bridge('symbol_spec', symbol.upper())
            except Exception as exc:
                raise MT5DataError('Broker sembol özellikleri alınamadı.') from exc

    def broker_compatibility(self, symbol):
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError('MT5 bağlı değil.')
            login, server = self._selected_account()
            try:
                return self._bridge('broker_compatibility', symbol.upper(), login, server, self.tick_clock_offset)
            except Exception as exc:
                raise MT5DataError('Broker emir kontrolleri alınamadı.') from exc

    def get_risk_status(self):
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError('MT5 bağlı değil.')
            login, server = self._selected_account()
            try:
                limit = self.effective_daily_loss_limit(login, server)
                return self._bridge('risk_status', login, server, limit, self.tick_clock_offset)
            except Exception as exc:
                raise MT5DataError('Günlük risk durumu doğrulanamadı.') from exc

    def effective_daily_loss_limit(self, login, server):
        return self.journal.daily_loss_limit(login, server) or self.daily_loss_limit

    def set_daily_loss_limit(self, value, acknowledge_risk=False):
        with self._lock.order():
            if not self.ensure_connected():
                raise MT5DataError('MT5 bağlı değil.')
            login, server = self._selected_account()
            # Verify the broker state before persisting an account-scoped change.
            try:
                status = self._bridge('risk_status', login, server,
                                      self.effective_daily_loss_limit(login, server), self.tick_clock_offset)
            except Exception as exc:
                raise MT5DataError('Günlük risk durumu doğrulanamadı.') from exc
            if value > status['daily_loss_limit'] and not acknowledge_risk:
                raise MT5DataError('Günlük zarar limitini yükseltmek için açık risk onayı gerekli.')
            self.journal.set_daily_loss_limit(login, server, value, status['currency'])
            return {**status, 'daily_loss_limit': value,
                    'ratio': round(status['daily_loss'] / value, 4)}

    def get_positions(self, fresh=False) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            if not fresh and self._positions_cache is not None and now - self._positions_cache_time < .5 and self.is_connected:
                return self._positions_cache
            if not self.ensure_connected():
                raise MT5DataError("MT5 bağlı değil; pozisyonlar doğrulanamadı.")
            try:
                result = self._bridge("positions")
                self._positions_cache = result
                self._positions_cache_time = time.monotonic()
                return result
            except Exception as exc:
                self._positions_cache = None
                raise MT5DataError("Pozisyonlar okunamadı; yeni işlem engellendi.") from exc

    def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        if symbol in self._price_cache and (now - self._price_cache_time.get(symbol, 0) < 0.3) and self.is_connected:
            return self._price_cache[symbol]

        if not self.ensure_connected():
            return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}

        with self._lock:
            try:
                tick = self.mt5.symbol_info_tick(symbol)
                if tick is None:
                    return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}

                info = self.mt5.symbol_info(symbol)
                digits = info.digits if info else 5
                spread = info.spread if info else round((tick.ask - tick.bid) * (10 ** digits))

                res = {
                    "symbol": symbol,
                    "bid": round(tick.bid, digits),
                    "ask": round(tick.ask, digits),
                    "spread": spread,
                    "time": int(tick.time)-self.tick_clock_offset
                }
                self._price_cache[symbol] = res
                self._price_cache_time[symbol] = time.time()
                return res
            except Exception as e:
                logger.error(f"Error fetching symbol price for {symbol}: {e}")
                return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}

    def remote_order_send(self, request: Dict[str, Any]) -> Any:
        with self._lock:
            # A timeout may occur after the broker accepts the order. Never retry
            # through a second transport when the execution outcome is unknown.
            try:
                if self.conn:
                    self.conn.execute("import MetaTrader5 as mt5")
                    return self.conn.eval(f"mt5.order_send({repr(request)})")
                if self.mt5:
                    return self.mt5.order_send(request)
            except Exception:
                logger.exception("Order outcome unknown; automatic retry suppressed")
            return None

    def open_order(self, symbol: str, order_type: str, volume: float, sl_points: int = 0,
                   tp_points: int = 0, comment: str = "HMA Web App", *,
                   magic: int = mt5_bridge.MANUAL_MAGIC, request_id: Optional[str] = None,
                   stop_event=None, pending_type=None, entry_price=None) -> Dict[str, Any]:
        if order_type.upper() not in ("BUY", "SELL"):
            return {"success": False, "error": "Emir yönü BUY veya SELL olmalı"}
        if not math.isfinite(volume) or volume <= 0 or sl_points < 0 or tp_points < 0:
            return {"success": False, "error": "Geçersiz hacim veya SL/TP"}
        started = time.perf_counter()
        with self._lock.order():
            queue_ms = round((time.perf_counter() - started) * 1000, 1)
            if self.journal.trading_halted():
                return {"success": False, "reason_code": "trading_halted",
                        "error": "Yeni emirler güvenlik kilidi nedeniyle durduruldu. Hesabı ve günlük zarar limitini kontrol edin; ardından panelden devam edin."}
            if magic != mt5_bridge.MANUAL_MAGIC and (self.automation_stopped.is_set() or (stop_event and stop_event.is_set())):
                return {"success": False, "error": "Otomatik işlemler durduruldu."}
            if not self.ensure_connected():
                return {"success": False, "error": "MT5 bağlı değil."}
            payload = dict(symbol=symbol.upper(), order_type=order_type.upper(), volume=volume,
                           sl_points=sl_points, tp_points=tp_points, comment=comment, magic=magic, pending_type=pending_type, entry_price=entry_price)
            try:
                request_id = request_id or str(uuid.uuid4())
                account_scope = self._selected_account()
                daily_loss_limit = self.effective_daily_loss_limit(*account_scope)
                tag = "vm:" + hashlib.sha256((str(account_scope) + request_id).encode()).hexdigest()[:24]
                order_kind = {"BUY_LIMIT": 2, "SELL_LIMIT": 3, "BUY_STOP": 4, "SELL_STOP": 5}.get(pending_type, 0 if payload['order_type'] == 'BUY' else 1)
                metadata = {'account': account_scope, 'tag': tag, 'order': {
                    'symbol': payload['symbol'], 'magic': magic, 'type': order_kind, 'volume': volume}}
                res = self.journal.run(request_id, payload, lambda: self._bridge(
                    "open_deal", payload["symbol"], payload["order_type"], volume, sl_points, tp_points,
                    tag, magic, daily_loss_limit, account_scope[0], account_scope[1], 10, pending_type, entry_price,
                    self.max_order_lots, self.max_total_lots, self.max_open_orders, self.tick_clock_offset,
                    self.ai_max_trade_risk_pct),
                    metadata=metadata, queue_ms=queue_ms)
                return {**res, "queue_ms": queue_ms}
            except MT5DataError as exc:
                return {"success": False, "error": str(exc)}
            except Exception:
                logger.exception("Emir günlüğü/sonucu kaydedilemedi")
                return {"success": False, "uncertain": True,
                        "error": "Emir kaydı doğrulanamadı; broker durumunu kontrol edin."}
            finally:
                self.invalidate_trading_cache()

    def close_position(self, ticket: int, pos_data=None, *, expected_magic=None):
        with self._lock.order():
            try:
                return self._close_position(ticket, pos_data, expected_magic=expected_magic)
            finally:
                self.invalidate_trading_cache()

    def _close_position(self, ticket: int, pos_data: Optional[Dict[str, Any]] = None, *, expected_magic=None) -> Dict[str, Any]:
        with self._lock:
            if not self.ensure_connected():
                return {"success": False, "error": "MT5 is not connected"}
            try:
                self._selected_account()
            except MT5DataError as exc:
                return {"success": False, "error": str(exc)}

            if self.conn:
                try:
                    self.conn.execute(NATIVE_CLOSE_SCRIPT)
                    res = self.conn.eval(f"hma_native_close_ticket({int(ticket)}, {expected_magic!r}, {self.login_id}, {self.server!r})")
                    return dict(res)
                except Exception as e:
                    logger.error(f"Native close_position #{ticket} failed: {e}")
                    return {"success": False, "uncertain": True, "error": "Kapatma sonucu belirsiz; pozisyonu kontrol edin."}

            try:
                if expected_magic is not None:
                    account = self.mt5.account_info()
                    owned = next((p for p in self.get_positions(fresh=True) if p["ticket"] == ticket), None)
                    if account is None or account.margin_mode != 2 or owned is None or owned["magic"] != expected_magic:
                        return {"success": False, "error": "Strateji sahipliği/hedging hesabı doğrulanamadı."}
                    pos_data = owned
                if pos_data:
                    symbol = pos_data["symbol"]
                    volume = float(pos_data["volume"])
                    is_buy = (pos_data.get("type") == "BUY" or pos_data.get("type_raw") == 0)
                    profit = float(pos_data.get("profit", 0.0))
                else:
                    positions = self.get_positions(fresh=True)
                    matched = [p for p in positions if p["ticket"] == int(ticket)]
                    if not matched:
                        return {"success": False, "error": f"Position #{ticket} bulunamadı"}
                    pos = matched[0]
                    symbol = pos["symbol"]
                    volume = float(pos["volume"])
                    is_buy = (pos["type"] == "BUY" or pos.get("type_raw") == 0)
                    profit = float(pos.get("profit", 0.0))

                tick = self.mt5.symbol_info_tick(symbol)
                if not tick:
                    return {"success": False, "error": f"Could not get tick for {symbol}"}

                close_price = tick.bid if is_buy else tick.ask
                close_type = 1 if is_buy else 0

                info = self.mt5.symbol_info(symbol)
                fillings = []
                if info and hasattr(info, "filling_mode"):
                    if info.filling_mode & 2:
                        fillings.append(1)
                    if info.filling_mode & 1:
                        fillings.append(0)
                for f in [1, 0, 2]:
                    if f not in fillings:
                        fillings.append(f)

                last_res = None
                for f in fillings:
                    self._selected_account()
                    t = self.mt5.symbol_info_tick(symbol)
                    cp = (t.bid if is_buy else t.ask) if t else close_price
                    request = {
                        "action": 1,
                        "position": int(ticket),
                        "symbol": str(symbol),
                        "volume": float(volume),
                        "type": int(close_type),
                        "price": float(cp),
                        "deviation": 50,
                        "magic": 123456,
                        "comment": "Close from HMA Web",
                        "type_time": 0,
                        "type_filling": f
                    }
                    last_res = self.remote_order_send(request)
                    if last_res and last_res.retcode == 10009:
                        return {"success": True, "ticket": ticket, "profit": profit}
                    if last_res is None or last_res.retcode != 10030:
                        break

                code = int(last_res.retcode) if last_res else None
                return {"success": False, "retcode": code, "partial": code == 10010,
                        "pending": code == 10008, "uncertain": code in (None, 10012, 10031),
                        "error": str(last_res.comment) if last_res else "Kapatma sonucu belirsiz; pozisyonu kontrol edin."}
            except Exception as e:
                logger.error(f"Error closing position #{ticket}: {e}")
                return {"success": False, "error": str(e)}

    def close_all(self) -> Dict[str, Any]:
        return self.close_by_filter("all")

    def close_by_filter(self, filter_type: str = "all") -> Dict[str, Any]:
        with self._lock.order():
            try:
                return self._close_by_filter(filter_type)
            except MT5DataError as exc:
                return {"success": False, "closed_count": 0, "total_matched": 0, "errors": [str(exc)]}
            finally:
                self.invalidate_trading_cache()

    def _close_by_filter(self, filter_type: str = "all") -> Dict[str, Any]:
        """
        filter_type: 'all', 'profit', 'loss'
        """
        with self._lock:
            if not self.ensure_connected():
                return {"success": False, "closed_count": 0, "total_matched": 0, "errors": ["MT5 is not connected"]}
            try:
                self._selected_account()
            except MT5DataError as exc:
                return {"success": False, "closed_count": 0, "total_matched": 0, "errors": [str(exc)]}

            if self.conn:
                try:
                    self.conn.execute(NATIVE_CLOSE_SCRIPT)
                    res = self.conn.eval(f"hma_native_close_filter({repr(filter_type)}, {self.login_id}, {self.server!r})")
                    return {
                        "success": bool(res.get("success")),
                        "uncertain": bool(res.get("uncertain")), "pending": bool(res.get("pending")),
                        "closed_count": int(res.get("closed_count", 0)),
                        "total_matched": int(res.get("total_matched", 0)),
                        "errors": list(res.get("errors", []))
                    }
                except Exception as e:
                    logger.error(f"Native close_by_filter failed: {e}")
                    return {"success": False, "uncertain": True, "closed_count": 0, "errors": ["Kapatma sonucu belirsiz; pozisyonları kontrol edin."]}

            positions = self.get_positions(fresh=True)
            targets = []
            for p in positions:
                profit = float(p.get("profit", 0.0))
                if filter_type == "profit" and profit > 0:
                    targets.append(p)
                elif filter_type == "loss" and profit < 0:
                    targets.append(p)
                elif filter_type == "all":
                    targets.append(p)

            closed_count = 0
            uncertain = pending = False
            errors = []

            for p in targets:
                res = self.close_position(p["ticket"], pos_data=p)
                if res.get("success"):
                    closed_count += 1
                else:
                    uncertain = uncertain or res.get("uncertain", False)
                    pending = pending or res.get("pending", False)
                    errors.append(f"Ticket #{p['ticket']}: {res.get('error')}")

            return {
                "success": len(errors) == 0,
                "closed_count": closed_count,
                "uncertain": uncertain, "pending": pending,
                "total_matched": len(targets),
                "total_positions": len(positions),
                "errors": errors
            }

    def get_rates(self, symbol: str, timeframe: int = 15, count: int = 100) -> Optional[List[Dict[str, Any]]]:
        with self._lock:
            if not self.ensure_connected():
                return None

            try:
                name = TIMEFRAME_NAMES.get(timeframe)
                if name is None:
                    raise ValueError(f"Desteklenmeyen zaman dilimi: {timeframe}")
                return self._bridge("rates", symbol, name, count)
            except Exception as e:
                logger.error(f"Error fetching rates for {symbol}: {e}")
                return None

    def get_history(self, days: int = 30, include_ai_entries=False) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError("MT5 bağlı değil; geçmiş doğrulanamadı.")

            try:
                if self.conn:
                    self.conn.execute("import json\n" + NATIVE_HISTORY_SCRIPT)
                    return json.loads(str(self.conn.eval(
                        f"json.dumps(hma_native_get_history({int(days)}, {int(self.tick_clock_offset)}, {bool(include_ai_entries)}))")))
                elif self.mt5:
                    from datetime import datetime, timedelta, timezone
                    days_int = int(days) if days else 30
                    to_date = datetime.now(timezone.utc) + timedelta(seconds=self.tick_clock_offset)
                    from_date = to_date - timedelta(days=days_int)
                    deals = self.mt5.history_deals_get(from_date, to_date)
                    if deals is None:
                        raise RuntimeError("İşlem geçmişi okunamadı")
                    res = []
                    for d in deals:
                        deal_symbol = str(getattr(d, "symbol", ""))
                        deal_entry = int(getattr(d, "entry", -1))
                        deal_profit = float(getattr(d, "profit", 0.0))
                        deal_type = int(getattr(d, "type", -1))
                        if deal_symbol != "" and (deal_entry in (1, 2, 3) or deal_profit != 0 or
                                                  (include_ai_entries and int(getattr(d, "magic", 0)) == mt5_bridge.AI_MAGIC)):
                            t = int(getattr(d, "time", 0))
                            try:
                                time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"
                                date_str = time.strftime("%Y-%m-%d", time.localtime(t)) if t else "-"
                            except Exception:
                                time_str = "-"
                                date_str = "-"
                            type_name = "BUY" if deal_type == 0 else ("SELL" if deal_type == 1 else str(deal_type))
                            res.append({
                                "ticket": int(d.ticket),
                                "magic": int(getattr(d, "magic", 0)),
                                "order": int(d.order),
                                "position_id": int(getattr(d, "position_id", d.order)),
                                "symbol": deal_symbol,
                                "type": type_name,
                                "entry": deal_entry,
                                "volume": float(getattr(d, "volume", 0.0)),
                                "price": round(float(getattr(d, "price", 0.0)), 5),
                                "profit": round(deal_profit, 2),
                                "commission": round(float(getattr(d, "commission", 0.0)), 2),
                                "swap": round(float(getattr(d, "swap", 0.0)), 2),
                                "fee": round(float(getattr(d, "fee", 0.0)), 2),
                                "comment": str(getattr(d, "comment", "")),
                                "time": time_str,
                                "date": date_str,
                                "timestamp": t
                            })
                    res.reverse()
                    return res
                return []
            except Exception as e:
                raise MT5DataError("İşlem geçmişi okunamadı.") from e

    def get_reports(self, days: int = 30) -> Dict[str, Any]:
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError("MT5 bağlı değil.")
            try:
                activity = self._bridge("report_activity", days)
            except Exception as exc:
                raise MT5DataError("Rapor verisi okunamadı.") from exc
            key = (tuple(activity["account"]), days)
            # Activity is always fresh; only immutable, fully closed lifetimes are cached.
            cached = self._reports_cache.setdefault(key, {})
        candidate_ids = {r["position_id"] for r in activity["deals"]
                         if r["type"] in (0,1) and r["entry"] in (1,2,3) and r["position_id"]}
        histories = {}
        for position_id in candidate_ids - set(activity["open_ids"]):
            entry = cached.get(position_id)
            if entry and time.monotonic() - entry[0] < 30:
                histories[position_id] = entry[1]
                continue
            # Release between position histories so orders take precedence over long reports.
            with self._lock:
                try:
                    if not self.ensure_connected():
                        raise RuntimeError("Bağlantı kesildi")
                    rows = self._bridge("position_history", position_id, activity["account"])
                except Exception as exc:
                    raise MT5DataError("Pozisyon geçmişi tamamlanamadı; rapor gösterilmedi.") from exc
                cached[position_id] = (time.monotonic(), rows)
                histories[position_id] = rows
        return build_report(activity, histories)

    def get_pending_orders(self):
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError("MT5 bağlı değil.")
            try:
                return self._bridge("pending_orders")
            except Exception as exc:
                raise MT5DataError("Bekleyen emirler okunamadı.") from exc

    def get_live_snapshot(self, symbols):
        with self._lock:
            if not self.ensure_connected():
                raise MT5DataError("MT5 bağlı değil.")
            try:
                snapshot = self._bridge("live_snapshot", symbols, self.tick_clock_offset)
                account = snapshot["account"]
                actual_mode = int(account["trade_mode"])
                account["account_type"] = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(actual_mode, "UNKNOWN")
                account["account_mismatch"] = (self.login_id <= 0 or int(account["login"]) != self.login_id
                                               or str(account["server"]) != self.server
                                               or actual_mode != {"DEMO": 0, "REAL": 2}.get(self.account_type))
                return snapshot
            except Exception as exc:
                raise MT5DataError("Canlı veriler doğrulanamadı.") from exc

    def manage_position(self, ticket, operation, values, request_id):
        with self._lock.order():
            if not self.ensure_connected():
                return {"success": False, "error": "MT5 bağlı değil."}
            try:
                self._selected_account()
            except MT5DataError as exc:
                return {"success": False, "error": str(exc)}
            try:
                return self.journal.run(request_id, dict(operation=operation, ticket=ticket, values=values),
                    lambda: self._bridge("manage_position", ticket, operation, values, self.login_id, self.server,
                                         self.tick_clock_offset))
            finally:
                self.invalidate_trading_cache()

    def cancel_pending(self, ticket, request_id):
        with self._lock.order():
            if not self.ensure_connected():
                return {"success": False, "error": "MT5 bağlı değil."}
            try:
                self._selected_account()
            except MT5DataError as exc:
                return {"success": False, "error": str(exc)}
            try:
                return self.journal.run(request_id, dict(operation="cancel",ticket=ticket),
                    lambda: self._bridge("cancel_pending", ticket, self.login_id, self.server))
            finally:
                self.invalidate_trading_cache()

    def reconcile_orders(self):
        for row in self.journal.unresolved():
            with self._lock:
                if not self.is_connected:
                    return
                result = self._bridge('reconcile_open', row['metadata'], row['created'])
                if result:
                    self.journal.resolve(row['id'], result)
                    self.invalidate_trading_cache()
