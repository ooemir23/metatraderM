import os
import logging
import time
import json
import threading
import socket
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MT5Client")
logger.setLevel(logging.INFO)

try:
    from mt5linux import MetaTrader5
    USE_MT5LINUX = True
    logger.info("Using mt5linux MetaTrader5 bridge")
except ImportError:
    USE_MT5LINUX = False
    import rpyc
    logger.info("Using fallback rpyc client")

def is_port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

NATIVE_CLOSE_SCRIPT = '''
import MetaTrader5 as mt5

def _try_enable_algo():
    try:
        tinfo = mt5.terminal_info()
        if tinfo and not getattr(tinfo, "trade_allowed", True):
            import ctypes, time
            windll = getattr(ctypes, "windll", None)
            if windll:
                user32 = getattr(windll, "user32", None)
                if user32:
                    hwnd = user32.FindWindowW("MetaQuotes::MetaTrader::5.00", 0)
                    if not hwnd:
                        def enum_windows_callback(h, l):
                            length = user32.GetWindowTextLengthW(h)
                            if length > 0:
                                buff = ctypes.create_unicode_buffer(length + 1)
                                user32.GetWindowTextW(h, buff, length + 1)
                                if "MetaTrader" in buff.value:
                                    l.append(h)
                                    return False
                            return True
                        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.py_object)
                        found = []
                        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), found)
                        if found:
                            hwnd = found[0]
                    if hwnd:
                        user32.PostMessageW(hwnd, 0x0111, 32851, 0)
                        time.sleep(0.5)
    except Exception:
        pass

def _execute_close_deal(p):
    _try_enable_algo()
    ticket = int(p.ticket)
    symbol = str(p.symbol)
    volume = float(p.volume)
    is_buy = (p.type == 0)
    close_type = 1 if is_buy else 0
    magic = int(getattr(p, "magic", 0))

    # ORDER_FILLING_IOC (1) is confirmed supported by Tickmill symbol_info.
    attempts = []
    for dev in [50, 100, 200]:
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            continue
        close_price = float(tick.bid if is_buy else tick.ask)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": volume,
            "type": close_type,
            "price": close_price,
            "deviation": dev,
            "magic": magic,
            "comment": "Close from HMA Web",
            "type_time": 0,
            "type_filling": 1
        }

        res = mt5.order_send(req)
        if res and res.retcode in (10009, 10008, 10010, 0):
            return {"success": True, "ticket": ticket, "profit": float(p.profit), "comment": str(res.comment)}

        c = getattr(res, "comment", str(mt5.last_error()))
        rc = getattr(res, "retcode", -1)
        if rc == 10018 or "Market closed" in str(c):
            return {"success": False, "ticket": ticket, "error": "Piyasa şu anda kapalı (Market closed - 10018). Altın (XAUUSD) her gece 23:57 - 01:02 arası rollover tatilindedir. 01:02'de otomatik açılacaktır."}
        if rc == 10027 or "AutoTrading disabled" in str(c):
            _try_enable_algo()
            res2 = mt5.order_send(req)
            if res2 and res2.retcode in (10009, 10008, 10010, 0):
                return {"success": True, "ticket": ticket, "profit": float(p.profit), "comment": str(res2.comment)}
            return {"success": False, "ticket": ticket, "error": "MT5 terminalinde 'Algo Trading' (Algoritmik İşlem) butonu kapalı! Lütfen MT5 sekmesinde üst menüdeki 'Algo Trading' butonuna tıklayarak yeşil yapın."}
        attempts.append("dev" + str(dev) + "->" + str(rc) + ":" + str(c))

    last_err = " | ".join(attempts) if attempts else "Unknown error"
    return {"success": False, "ticket": ticket, "error": last_err}

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

        # Default broker credentials (Tickmill Demo)
        self.login_id = int(os.getenv("MT5_LOGIN", "25373161"))
        self.password = os.getenv("MT5_PASSWORD", "PpE&tgF6)8[>")
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
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    logger.info(f"Saved credentials to {path}")
                    return
            except Exception as e:
                logger.warning(f"Could not save credentials to {path}: {e}")

    def connect(self) -> bool:
        if not self._lock.acquire(timeout=5.0):
            return False
        try:
            now = time.time()
            if now - self.last_connect_attempt < self.reconnect_cooldown and not self.is_connected:
                return False

            self.last_connect_attempt = now
            ports_to_try = [self.port, 8001]
            ports_to_try = list(dict.fromkeys(ports_to_try))
        finally:
            self._lock.release()

        new_conn = None
        new_mt5 = None
        new_port = None
        success = False

        for p in ports_to_try:
            # Fast probe: if port is not accepting TCP, don't wait for OS socket timeout
            if not is_port_open(self.host, p, timeout=1.5):
                logger.debug(f"Port {self.host}:{p} is not open, skipping.")
                self.last_error_msg = f"Port {p} kapalı / MT5 başlatılıyor"
                continue

            # 1. Try rpyc.classic directly (cleanest, connects straight to Wine MetaTrader5)
            try:
                import threading
                res = []
                err = []
                def _connect_rpyc():
                    try:
                        c = rpyc.classic.connect(self.host, p)
                        try:
                            c._config["sync_request_timeout"] = 30
                            c._config["allow_all_attrs"] = True
                        except Exception:
                            pass
                        m = c.modules.MetaTrader5
                        res.append((c, m))
                    except Exception as e:
                        err.append(e)

                t = threading.Thread(target=_connect_rpyc)
                t.daemon = True
                t.start()
                t.join(5.0)
                if t.is_alive():
                    raise TimeoutError("rpyc connect timed out after 5s")
                if err:
                    raise err[0]
                
                if res:
                    conn, mt5 = res[0]
                    
                    # Step 2: Initialize MT5 (separate, longer timeout)
                    # This is needed so positions/history/account data work.
                    init_ok = [False]
                    def _init_mt5():
                        try:
                            ok = bool(mt5.initialize())
                            if not ok:
                                for p_cand in [
                                    "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
                                    "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.exe"
                                ]:
                                    try:
                                        if mt5.initialize(path=p_cand):
                                            ok = True
                                            break
                                    except Exception:
                                        pass
                            if not ok and self.login_id and self.password:
                                ok = bool(mt5.initialize(
                                    login=int(self.login_id),
                                    password=str(self.password),
                                    server=str(self.server)
                                ))
                            init_ok[0] = ok
                        except Exception as ie:
                            logger.debug(f"mt5.initialize() in connect: {ie}")

                    it = threading.Thread(target=_init_mt5)
                    it.daemon = True
                    it.start()
                    it.join(15.0)

                    self.conn = conn
                    self.mt5 = mt5
                    self.port = p
                    self.is_connected = True
                    self.connected_at = time.time()
                    self.last_ping_time = time.time()
                    self.last_error_msg = ""
                    if init_ok[0]:
                        logger.info(f"Connected and initialized MT5 on port {p}!")
                    else:
                        logger.info(f"Connected to RPyC on port {p}, MT5 init pending (will retry on login)")
                    return True

            except Exception as e:
                logger.debug(f"rpyc.classic on port {p} failed: {e}")
                self.last_error_msg = f"Port {p} error: {e}"

            # 2. Try mt5linux if available
            if USE_MT5LINUX:
                try:
                    logger.info(f"Connecting via mt5linux to {self.host}:{p}...")
                    
                    res_mt5 = []
                    err_mt5 = []
                    def _connect_mt5linux():
                        try:
                            client = MetaTrader5(host=self.host, port=p)
                            if client.initialize():
                                res_mt5.append(client)
                            elif client.initialize(login=int(self.login_id), password=str(self.password), server=str(self.server)):
                                res_mt5.append(client)
                        except Exception as e:
                            err_mt5.append(e)
                    
                    t2 = threading.Thread(target=_connect_mt5linux)
                    t2.daemon = True
                    t2.start()
                    t2.join(5.0)
                    if t2.is_alive():
                        raise TimeoutError("mt5linux connect timed out after 5s")
                    if err_mt5:
                        raise err_mt5[0]
                        
                    if res_mt5:
                        client = res_mt5[0]
                        if self._lock.acquire(timeout=2.0):
                            try:
                                self.mt5 = client
                                self.port = p
                                self.is_connected = True
                                self.last_ping_time = time.time()
                                self.last_error_msg = ""
                                logger.info(f"Connected to MT5 via mt5linux on port {p}!")
                            finally:
                                self._lock.release()
                        return True
                except Exception as e:
                    self.last_error_msg = f"Port {p} error: {e}"
                    logger.warning(self.last_error_msg)

        if self._lock.acquire(timeout=2.0):
            try:
                self.is_connected = False
            finally:
                self._lock.release()
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

                # Run initialize in a thread with timeout to prevent blocking
                login_result = [None]
                login_error = [None]
                def _do_login():
                    try:
                        ok = self.mt5.initialize(login=int(login_id), password=str(password), server=server_clean)
                        if not ok:
                            ok = self.mt5.login(login=int(login_id), password=str(password), server=server_clean)
                        login_result[0] = ok
                    except Exception as e:
                        login_error[0] = e

                lt = threading.Thread(target=_do_login)
                lt.daemon = True
                lt.start()
                lt.join(15.0)
                if lt.is_alive():
                    return {"success": False, "error": "MT5 login zaman aşımı (15s). Terminal henüz hazır olmayabilir, lütfen tekrar deneyin."}
                if login_error[0]:
                    return {"success": False, "error": str(login_error[0])}

                ok = login_result[0]
                if ok:
                    self.login_id = int(login_id)
                    self.password = str(password)
                    self.server = server_clean
                    self.is_connected = True
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
        now = time.time()
        if self.is_connected and self.mt5 is not None:
            if now - self.last_ping_time < 5.0:
                return True
            try:
                info = self.mt5.terminal_info()
                if info is not None:
                    self.last_ping_time = now
                    return True
                # If terminal_info is None, try quick non-blocking initialize
                try:
                    if self.mt5.initialize():
                        self.last_ping_time = now
                        return True
                except Exception:
                    pass
                return True
            except Exception:
                self.is_connected = False
                self.conn = None
                self.mt5 = None
                self.connected_at = 0

        return self.connect()

    def get_account_info(self) -> Dict[str, Any]:
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

        try:
            acc = self.mt5.account_info()
            now = time.time()
            if acc is None and self.login_id and self.password:
                if now - self.last_auto_login_attempt > self.auto_login_cooldown:
                    self.last_auto_login_attempt = now
                    try:
                        logger.info(f"account_info returned None, attempting broker login #{self.login_id} @ {self.server}...")
                        self.mt5.login(login=int(self.login_id), password=str(self.password), server=str(self.server))
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
            
            return {
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
        except Exception as e:
            logger.error(f"Error fetching account info: {e}")
            return {"connected": False, "error": str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.ensure_connected():
            return []

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
            return result
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        if not self.ensure_connected():
            return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}

        try:
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}
            
            info = self.mt5.symbol_info(symbol)
            digits = info.digits if info else 5
            spread = info.spread if info else round((tick.ask - tick.bid) * (10 ** digits))

            return {
                "symbol": symbol,
                "bid": round(tick.bid, digits),
                "ask": round(tick.ask, digits),
                "spread": spread,
                "time": tick.time
            }
        except Exception as e:
            logger.error(f"Error fetching symbol price for {symbol}: {e}")
            return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "spread": 0}

    def remote_order_send(self, request: Dict[str, Any]) -> Any:
        with self._lock:
            if self.conn:
                try:
                    self.conn.execute("import MetaTrader5 as mt5")
                    return self.conn.eval(f"mt5.order_send({repr(request)})")
                except Exception as e:
                    logger.warning(f"conn.eval order_send failed: {e}")
            if self.mt5:
                try:
                    return self.mt5.order_send(request)
                except Exception as e:
                    logger.error(f"mt5.order_send failed: {e}")
            return None

    def open_order(self, symbol: str, order_type: str, volume: float, sl_points: int = 0, tp_points: int = 0, comment: str = "HMA Web App") -> Dict[str, Any]:
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

                # Pre-emptively try enable AlgoTrading if disabled
                if self.conn:
                    try:
                        self.conn.execute(NATIVE_CLOSE_SCRIPT)
                        self.conn.eval("_try_enable_algo()")
                    except Exception:
                        pass

                result = self.remote_order_send(request)
                if result is None:
                    err = self.mt5.last_error()
                    return {"success": False, "error": f"order_send failed: {err}"}

                if result.retcode not in (10009, 10008):
                    request["type_filling"] = 0
                    result = self.remote_order_send(request)
                    if result.retcode not in (10009, 10008):
                        request["type_filling"] = 2
                        result = self.remote_order_send(request)
                        if result.retcode not in (10009, 10008):
                            err_comment = str(result.comment)
                            if result.retcode == 10027 or "AutoTrading disabled" in err_comment:
                                if self.conn:
                                    try:
                                        self.conn.eval("_try_enable_algo()")
                                        time.sleep(0.5)
                                        result2 = self.remote_order_send(request)
                                        if result2 and result2.retcode in (10009, 10008, 10010, 0):
                                            return {
                                                "success": True,
                                                "ticket": result2.order,
                                                "price": result2.price,
                                                "volume": result2.volume,
                                                "comment": result2.comment
                                            }
                                    except Exception:
                                        pass
                                err_comment = "MT5 terminalinde 'Algo Trading' (Algoritmik İşlem) kapalı! Lütfen MT5 sekmesinde üst menüdeki 'Algo Trading' butonunu aktif (yeşil) yapın."
                            return {"success": False, "retcode": result.retcode, "error": err_comment}

                return {
                    "success": True,
                    "ticket": result.order,
                    "price": result.price,
                    "volume": result.volume,
                    "comment": result.comment
                }
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
                    if last_res and last_res.retcode in (10009, 10008):
                        return {"success": True, "ticket": ticket, "profit": profit}

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
                rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
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

