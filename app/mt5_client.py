import os
import logging
import time
import json
import threading
import math
import rpyc
import tempfile
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

def _execute_close_deal(p):
    ticket, symbol = int(p.ticket), str(p.symbol)
    is_buy = p.type == 0
    for filling in (1, 0, 2):
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
                    "pending": code == 10008, "retcode": code, "error": str(result.comment)}
    return {"success": False, "ticket": ticket, "error": str(result.comment)}

def hma_native_close_filter(filter_type="all"):
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
    errors = []

    for p in targets:
        r = _execute_close_deal(p)
        if r.get("success"):
            closed_count += 1
        else:
            errors.append("Ticket #" + str(p.ticket) + ": " + str(r.get("error")))

    return {
        "success": len(errors) == 0,
        "closed_count": closed_count,
        "total_matched": len(targets),
        "errors": errors
    }

def hma_native_close_ticket(ticket):
    ticket = int(ticket)
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        all_p = mt5.positions_get()
        if all_p:
            positions = [p for p in all_p if p.ticket == ticket]
    if not positions:
        return {"success": False, "error": "Position #" + str(ticket) + " bulunamadı"}

    return _execute_close_deal(positions[0])
'''

NATIVE_HISTORY_SCRIPT = '''
import MetaTrader5 as mt5
import time
from datetime import datetime, timedelta

def hma_native_get_history(days=30):
    try:
        days_int = int(days) if days else 30
        from_date = datetime.now() - timedelta(days=days_int)
        to_date = datetime.now() + timedelta(days=2)
        
        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None or len(deals) == 0:
            deals = mt5.history_deals_get(datetime(2020, 1, 1), to_date)
        if deals is None or len(deals) == 0:
            deals = mt5.history_deals_get(0, 2147483647)
            
        if deals is None:
            return []
            
        res = []
        for d in deals:
            deal_symbol = str(getattr(d, "symbol", ""))
            deal_entry = int(getattr(d, "entry", -1))
            deal_profit = float(getattr(d, "profit", 0.0))
            deal_type = int(getattr(d, "type", -1))
            
            # Filter: we want exit deals (entry in 1: OUT, 2: INOUT, 3: OUT_BY) or deals with realized profit/loss
            # Must have a trading symbol (excludes deposit/withdrawal balance deals)
            if deal_symbol != "" and (deal_entry in (1, 2, 3) or deal_profit != 0):
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
    except Exception as e:
        return []
'''

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
        self._lock = threading.RLock()
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
                    logger.info(f"Loaded credentials from {path}: #{self.login_id} @ {self.server}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read {path}: {e}")

    def save_credentials(self):
        data = {
            "login": self.login_id,
            "password": self.password,
            "server": self.server
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
                    stream, rpyc.SlaveService,
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

    def login(self, login_id: int, password: str, server: str) -> Dict[str, Any]:
        if not self._lock.acquire(timeout=15.0):
            return {"success": False, "error": "MT5 meşgul, lütfen birkaç saniye sonra tekrar deneyin."}
        try:
            if not self.ensure_connected():
                self.connect()

            if not self.mt5:
                return {"success": False, "error": f"MT5 sunucusuna ulaşılamadı. ({self.last_error_msg})"}

            try:
                server_clean = str(server).strip()
                if "tickmill" in server_clean.lower() and "demo" in server_clean.lower():
                    server_clean = "Tickmill-Demo"
                elif "tickmill" in server_clean.lower() and "live" in server_clean.lower():
                    server_clean = "Tickmill-Live"

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
                    self.login_id = int(login_id)
                    self.password = str(password)
                    self.server = server_clean
                    self.is_connected = True
                    self._account_cache = self._positions_cache = None
                    self._price_cache.clear()
                    self.save_credentials()
                    return {"success": True, "login": login_id, "server": server_clean}
                else:
                    err = self.mt5.last_error()
                    return {"success": False, "error": f"Login hatası: {err}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
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
                if acc is None and self.login_id and self.password:
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

    def get_positions(self) -> List[Dict[str, Any]]:
        now = time.time()
        if self._positions_cache is not None and (now - self._positions_cache_time < 0.5) and self.is_connected:
            return self._positions_cache

        if not self.ensure_connected():
            return []

        with self._lock:
            try:
                positions = self.mt5.positions_get()
                if positions is None:
                    return []

                result = []
                for p in positions:
                    result.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == 0 else "SELL",
                        "type_raw": p.type,
                        "volume": p.volume,
                        "price_open": round(p.price_open, 5),
                        "price_current": round(p.price_current, 5),
                        "sl": round(p.sl, 5),
                        "tp": round(p.tp, 5),
                        "profit": round(p.profit, 2),
                        "swap": round(p.swap, 2),
                        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.time))
                    })
                self._positions_cache = result
                self._positions_cache_time = time.time()
                return result
            except Exception as e:
                logger.error(f"Error fetching positions: {e}")
                return []

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
                    "time": tick.time
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

    def open_order(self, symbol: str, order_type: str, volume: float, sl_points: int = 0, tp_points: int = 0, comment: str = "HMA Web App") -> Dict[str, Any]:
        if order_type.upper() not in ("BUY", "SELL"):
            return {"success": False, "error": "Emir yönü BUY veya SELL olmalı"}
        if not math.isfinite(volume) or volume <= 0 or sl_points < 0 or tp_points < 0:
            return {"success": False, "error": "Geçersiz hacim veya SL/TP"}
        with self._lock:
            if not self.ensure_connected():
                return {"success": False, "error": f"MT5 bağlı değil: {self.last_error_msg}"}

            try:
                self.mt5.symbol_select(symbol, True)
                tick = self.mt5.symbol_info_tick(symbol)
                info = self.mt5.symbol_info(symbol)
                if not tick or not info:
                    return {"success": False, "error": f"Symbol {symbol} bulunamadı veya kapalı"}

                point = info.point
                digits = info.digits
                is_buy = order_type.upper() == "BUY"

                price = tick.ask if is_buy else tick.bid
                type_code = 0 if is_buy else 1

                sl = 0.0
                tp = 0.0
                if sl_points > 0:
                    sl = round(price - (sl_points * point) if is_buy else price + (sl_points * point), digits)
                if tp_points > 0:
                    tp = round(price + (tp_points * point) if is_buy else price - (tp_points * point), digits)

                request = {
                    "action": 1,
                    "symbol": str(symbol),
                    "volume": float(volume),
                    "type": int(type_code),
                    "price": float(price),
                    "sl": float(sl),
                    "tp": float(tp),
                    "deviation": 20,
                    "magic": 123456,
                    "comment": str(comment),
                    "type_time": 0,
                    "type_filling": 1
                }

                for filling in (1, 0, 2):
                    request["type_filling"] = filling
                    result = self.remote_order_send(request)
                    if result is None:
                        return {"success": False, "uncertain": True,
                                "error": "Emir sonucu belirsiz; tekrar göndermeden önce pozisyonları kontrol edin."}
                    code = int(result.retcode)
                    if code in (10008, 10009, 10010):
                        return {"success": True, "partial": code == 10010,
                                "retcode": code, "ticket": result.order,
                                "price": result.price, "volume": result.volume,
                                "comment": str(result.comment)}
                    if code != 10030:  # Only an explicit invalid filling rejection is safe to retry.
                        break
                return {"success": False, "retcode": int(result.retcode), "error": str(result.comment)}
            except Exception as e:
                logger.error(f"Error opening order: {e}")
                return {"success": False, "error": str(e)}

    def close_position(self, ticket: int, pos_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if not self.ensure_connected():
                return {"success": False, "error": "MT5 is not connected"}

            if self.conn:
                try:
                    self.conn.execute(NATIVE_CLOSE_SCRIPT)
                    res = self.conn.eval(f"hma_native_close_ticket({int(ticket)})")
                    return dict(res)
                except Exception as e:
                    logger.error(f"Native close_position #{ticket} failed: {e}")
                    return {"success": False, "uncertain": True, "error": "Kapatma sonucu belirsiz; pozisyonu kontrol edin."}

            try:
                if pos_data:
                    symbol = pos_data["symbol"]
                    volume = float(pos_data["volume"])
                    is_buy = (pos_data.get("type") == "BUY" or pos_data.get("type_raw") == 0)
                    profit = float(pos_data.get("profit", 0.0))
                else:
                    positions = self.get_positions()
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

                err_msg = last_res.comment if last_res else str(self.mt5.last_error())
                return {"success": False, "error": err_msg}
            except Exception as e:
                logger.error(f"Error closing position #{ticket}: {e}")
                return {"success": False, "error": str(e)}

    def close_all(self) -> Dict[str, Any]:
        return self.close_by_filter("all")

    def close_by_filter(self, filter_type: str = "all") -> Dict[str, Any]:
        """
        filter_type: 'all', 'profit', 'loss'
        """
        with self._lock:
            if not self.ensure_connected():
                return {"success": False, "closed_count": 0, "total_matched": 0, "errors": ["MT5 is not connected"]}

            if self.conn:
                try:
                    self.conn.execute(NATIVE_CLOSE_SCRIPT)
                    res = self.conn.eval(f"hma_native_close_filter({repr(filter_type)})")
                    return {
                        "success": bool(res.get("success")),
                        "closed_count": int(res.get("closed_count", 0)),
                        "total_matched": int(res.get("total_matched", 0)),
                        "errors": list(res.get("errors", []))
                    }
                except Exception as e:
                    logger.error(f"Native close_by_filter failed: {e}")
                    return {"success": False, "uncertain": True, "closed_count": 0, "errors": ["Kapatma sonucu belirsiz; pozisyonları kontrol edin."]}

            positions = self.get_positions()
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
            errors = []

            for p in targets:
                res = self.close_position(p["ticket"], pos_data=p)
                if res.get("success"):
                    closed_count += 1
                else:
                    errors.append(f"Ticket #{p['ticket']}: {res.get('error')}")

            return {
                "success": len(errors) == 0,
                "closed_count": closed_count,
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
                rates = self.mt5.copy_rates_from_pos(symbol, getattr(self.mt5, name), 0, count)
                if rates is None or len(rates) == 0:
                    return None

                result = []
                for r in rates:
                    result.append({
                        "time": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "tick_volume": int(r[5])
                    })
                return result
            except Exception as e:
                logger.error(f"Error fetching rates for {symbol}: {e}")
                return None

    def get_history(self, days: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.ensure_connected():
                return []

            try:
                if self.conn:
                    self.conn.execute(NATIVE_HISTORY_SCRIPT)
                    raw = self.conn.eval(f"hma_native_get_history({int(days)})")
                    if not raw:
                        return []
                    # Ensure plain python dicts
                    return [dict(d) for d in raw]
                elif self.mt5:
                    from datetime import datetime, timedelta
                    days_int = int(days) if days else 30
                    from_date = datetime.now() - timedelta(days=days_int)
                    to_date = datetime.now() + timedelta(days=2)
                    deals = self.mt5.history_deals_get(from_date, to_date)
                    if deals is None or len(deals) == 0:
                        deals = self.mt5.history_deals_get(datetime(2020, 1, 1), to_date)
                    if deals is None or len(deals) == 0:
                        deals = self.mt5.history_deals_get(0, 2147483647)
                    if deals is None:
                        return []
                    res = []
                    for d in deals:
                        deal_symbol = str(getattr(d, "symbol", ""))
                        deal_entry = int(getattr(d, "entry", -1))
                        deal_profit = float(getattr(d, "profit", 0.0))
                        deal_type = int(getattr(d, "type", -1))
                        if deal_symbol != "" and (deal_entry in (1, 2, 3) or deal_profit != 0):
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
                logger.error(f"Error fetching history: {e}")
                return []

    def get_reports(self, days: int = 30) -> Dict[str, Any]:
        deals = self.get_history(days=days)
        total_trades = len(deals)
        
        empty_res = {
            "summary": {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_profit": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "profit_factor": 0.0,
                "today_profit": 0.0,
                "today_trades": 0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "total_volume": 0.0,
                "total_swap": 0.0,
                "total_commission": 0.0
            },
            "daily": [],
            "by_symbol": []
        }
        
        if total_trades == 0:
            return empty_res

        today_str = time.strftime("%Y-%m-%d")

        winning_deals = [d for d in deals if d["profit"] > 0]
        losing_deals = [d for d in deals if d["profit"] < 0]

        winning_count = len(winning_deals)
        losing_count = len(losing_deals)
        win_rate = round((winning_count / total_trades) * 100, 1) if total_trades > 0 else 0.0

        gross_profit = sum(d["profit"] for d in winning_deals)
        gross_loss = sum(d["profit"] for d in losing_deals)
        total_profit = sum(d["profit"] for d in deals)
        total_swap = sum(d.get("swap", 0.0) for d in deals)
        total_comm = sum(d.get("commission", 0.0) for d in deals)
        total_vol = sum(d.get("volume", 0.0) for d in deals)

        profit_factor = round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else (999.0 if gross_profit > 0 else 0.0)

        avg_win = round(gross_profit / winning_count, 2) if winning_count > 0 else 0.0
        avg_loss = round(gross_loss / losing_count, 2) if losing_count > 0 else 0.0

        best_trade = max((d["profit"] for d in deals), default=0.0)
        worst_trade = min((d["profit"] for d in deals), default=0.0)

        # Today's profit & trades
        today_deals = [d for d in deals if d.get("date") == today_str]
        today_profit = sum(d["profit"] for d in today_deals)
        today_trades = len(today_deals)

        # Group by date
        daily_dict = {}
        for d in deals:
            d_date = d.get("date", "Unknown")
            if d_date not in daily_dict:
                daily_dict[d_date] = {
                    "date": d_date,
                    "is_today": (d_date == today_str),
                    "trades_count": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "profit": 0.0,
                    "swap": 0.0,
                    "commission": 0.0,
                    "volume": 0.0
                }
            entry = daily_dict[d_date]
            entry["trades_count"] += 1
            p = d["profit"]
            entry["profit"] += p
            if p > 0:
                entry["winning_trades"] += 1
                entry["gross_profit"] += p
            elif p < 0:
                entry["losing_trades"] += 1
                entry["gross_loss"] += p
            entry["swap"] += d.get("swap", 0.0)
            entry["commission"] += d.get("commission", 0.0)
            entry["volume"] += d.get("volume", 0.0)

        daily_list = []
        for date_k, val in sorted(daily_dict.items(), reverse=True):
            val["profit"] = round(val["profit"], 2)
            val["gross_profit"] = round(val["gross_profit"], 2)
            val["gross_loss"] = round(val["gross_loss"], 2)
            val["swap"] = round(val["swap"], 2)
            val["commission"] = round(val["commission"], 2)
            val["volume"] = round(val["volume"], 2)
            val["win_rate"] = round((val["winning_trades"] / val["trades_count"]) * 100, 1) if val["trades_count"] > 0 else 0.0
            daily_list.append(val)

        # Group by symbol
        symbol_dict = {}
        for d in deals:
            sym = d.get("symbol", "Other")
            if sym not in symbol_dict:
                symbol_dict[sym] = {
                    "symbol": sym,
                    "trades_count": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "profit": 0.0,
                    "volume": 0.0
                }
            s_entry = symbol_dict[sym]
            s_entry["trades_count"] += 1
            p = d["profit"]
            s_entry["profit"] += p
            if p > 0:
                s_entry["winning_trades"] += 1
            elif p < 0:
                s_entry["losing_trades"] += 1
            s_entry["volume"] += d.get("volume", 0.0)

        symbol_list = []
        for sym_k, val in sorted(symbol_dict.items(), key=lambda x: x[1]["profit"], reverse=True):
            val["profit"] = round(val["profit"], 2)
            val["volume"] = round(val["volume"], 2)
            val["win_rate"] = round((val["winning_trades"] / val["trades_count"]) * 100, 1) if val["trades_count"] > 0 else 0.0
            symbol_list.append(val)

        return {
            "summary": {
                "total_trades": total_trades,
                "winning_trades": winning_count,
                "losing_trades": losing_count,
                "win_rate": win_rate,
                "total_profit": round(total_profit, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "profit_factor": profit_factor,
                "today_profit": round(today_profit, 2),
                "today_trades": today_trades,
                "avg_profit": avg_win,
                "avg_loss": avg_loss,
                "best_trade": round(best_trade, 2),
                "worst_trade": round(worst_trade, 2),
                "total_volume": round(total_vol, 2),
                "total_swap": round(total_swap, 2),
                "total_commission": round(total_comm, 2)
            },
            "daily": daily_list,
            "by_symbol": symbol_list
        }
