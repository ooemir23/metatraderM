import os
import logging
import time
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

class MT5Client:
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.getenv("MT5_HOST", "metatrader5")
        self.port = int(port or os.getenv("MT5_PORT", "8001"))
        self.mt5 = None
        self.is_connected = False
        self.last_connect_attempt = 0
        self.reconnect_cooldown = 4
        self.last_error_msg = ""

        # Default broker credentials (Tickmill Demo)
        self.login_id = int(os.getenv("MT5_LOGIN", "25373151"))
        self.password = os.getenv("MT5_PASSWORD", "PpE&tgF6)8[>")
        self.server = os.getenv("MT5_SERVER", "Tickmill-Demo")

        self.conn = None

    def connect(self) -> bool:
        now = time.time()
        if now - self.last_connect_attempt < self.reconnect_cooldown and not self.is_connected:
            return False

        self.last_connect_attempt = now
        ports_to_try = [self.port, 8001, 18812]
        ports_to_try = list(dict.fromkeys(ports_to_try))

        for p in ports_to_try:
            # 1. Try rpyc.classic directly (cleanest, connects straight to Wine MetaTrader5)
            try:
                logger.info(f"Connecting via rpyc.classic to {self.host}:{p}...")
                conn = rpyc.classic.connect(self.host, p)
                mt5 = conn.modules.MetaTrader5
                
                # Check terminal / initialize
                ok = False
                try:
                    ok = mt5.initialize()
                except Exception:
                    ok = False

                if not ok:
                    try:
                        ok = mt5.initialize(login=int(self.login_id), password=str(self.password), server=str(self.server))
                    except Exception:
                        ok = False

                if ok or mt5.terminal_info() is not None:
                    self.conn = conn
                    self.mt5 = mt5
                    self.port = p
                    self.is_connected = True
                    self.last_error_msg = ""
                    logger.info(f"Connected to MT5 via rpyc.classic on port {p}!")
                    return True
            except Exception as e:
                logger.debug(f"rpyc.classic on port {p} failed: {e}")
                self.last_error_msg = f"Port {p} error: {e}"

            # 2. Try mt5linux if available
            if USE_MT5LINUX:
                try:
                    logger.info(f"Connecting via mt5linux to {self.host}:{p}...")
                    client = MetaTrader5(host=self.host, port=p)
                    if client.initialize():
                        self.mt5 = client
                        self.port = p
                        self.is_connected = True
                        self.last_error_msg = ""
                        logger.info(f"Connected to MT5 via mt5linux on port {p}!")
                        return True
                    elif client.initialize(login=int(self.login_id), password=str(self.password), server=str(self.server)):
                        self.mt5 = client
                        self.port = p
                        self.is_connected = True
                        self.last_error_msg = ""
                        logger.info(f"Connected to MT5 with credentials via mt5linux on port {p}!")
                        return True
                except Exception as e:
                    self.last_error_msg = f"Port {p} error: {e}"
                    logger.warning(self.last_error_msg)

        self.is_connected = False
        return False

    def login(self, login_id: int, password: str, server: str) -> Dict[str, Any]:
        if not self.ensure_connected():
            self.connect()

        if not self.mt5:
            return {"success": False, "error": f"MT5 sunucusuna ulaşılamadı. ({self.last_error_msg})"}

        try:
            self.login_id = int(login_id)
            self.password = str(password)
            self.server = str(server)

            ok = self.mt5.initialize(login=int(login_id), password=str(password), server=str(server))
            if not ok:
                ok = self.mt5.login(login=int(login_id), password=str(password), server=str(server))

            if ok:
                self.is_connected = True
                return {"success": True, "login": login_id, "server": server}
            else:
                err = self.mt5.last_error()
                return {"success": False, "error": f"Login hatası: {err}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ensure_connected(self) -> bool:
        if self.is_connected and self.mt5 is not None:
            try:
                info = self.mt5.terminal_info()
                if info is not None:
                    return True
            except Exception:
                self.is_connected = False
                self.conn = None
                self.mt5 = None

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
                "error": self.last_error_msg or "MT5 Bağlantısı Bekleniyor"
            }

        try:
            acc = self.mt5.account_info()
            if acc is None:
                return {
                    "connected": False,
                    "terminal_ready": True,
                    "error": "Broker hesabına giriş bekleniyor",
                    "login": self.login_id,
                    "server": self.server
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
                "leverage": acc.leverage
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

    def to_server_dict(self, d: Dict[str, Any]) -> Any:
        if self.conn and hasattr(self.conn, "modules"):
            try:
                return self.conn.modules["builtins"].dict(d)
            except Exception:
                try:
                    return self.conn.modules.builtins.dict(d)
                except Exception:
                    pass
        return d

    def open_order(self, symbol: str, order_type: str, volume: float, sl_points: int = 0, tp_points: int = 0, comment: str = "HMA Web App") -> Dict[str, Any]:
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
                "symbol": symbol,
                "volume": float(volume),
                "type": type_code,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 123456,
                "comment": comment,
                "type_time": 0,
                "type_filling": 1
            }

            srv_req = self.to_server_dict(request)
            result = self.mt5.order_send(srv_req)
            if result is None:
                err = self.mt5.last_error()
                return {"success": False, "error": f"order_send failed: {err}"}

            if result.retcode not in (10009, 10008):
                request["type_filling"] = 0
                srv_req = self.to_server_dict(request)
                result = self.mt5.order_send(srv_req)
                if result.retcode not in (10009, 10008):
                    return {"success": False, "retcode": result.retcode, "error": result.comment}

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

    def close_position(self, ticket: int) -> Dict[str, Any]:
        if not self.ensure_connected():
            return {"success": False, "error": "MT5 is not connected"}

        try:
            positions = self.mt5.positions_get(ticket=ticket)
            if not positions or len(positions) == 0:
                return {"success": False, "error": f"Position #{ticket} bulunamadı"}

            pos = positions[0]
            tick = self.mt5.symbol_info_tick(pos.symbol)
            if not tick:
                return {"success": False, "error": f"Could not get tick for {pos.symbol}"}

            is_buy = pos.type == 0
            close_price = tick.bid if is_buy else tick.ask
            close_type = 1 if is_buy else 0

            request = {
                "action": 1,
                "position": ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "price": close_price,
                "deviation": 20,
                "magic": 123456,
                "comment": "Close from HMA Web",
                "type_time": 0,
                "type_filling": 1
            }

            srv_req = self.to_server_dict(request)
            result = self.mt5.order_send(srv_req)
            if result and result.retcode in (10009, 10008):
                return {"success": True, "ticket": ticket, "profit": pos.profit}
            else:
                request["type_filling"] = 0
                srv_req = self.to_server_dict(request)
                result = self.mt5.order_send(srv_req)
                if result and result.retcode in (10009, 10008):
                    return {"success": True, "ticket": ticket, "profit": pos.profit}
                err_msg = result.comment if result else str(self.mt5.last_error())
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
            res = self.close_position(p["ticket"])
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
