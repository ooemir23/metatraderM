import os
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.mt5_client import MT5Client
from app.strategy_bot import StrategyBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("HMATradingApp")

mt5_client = MT5Client()
bot = StrategyBot(mt5_client)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting HMA Trading Web Application...")
    mt5_client.connect()
    yield
    logger.info("Shutting down HMA Trading Application...")
    bot.stop()

app = FastAPI(title="HMA Trading Dashboard", lifespan=lifespan)

# Pydantic Schemas
class OrderRequest(BaseModel):
    symbol: str
    order_type: str # "BUY" or "SELL"
    volume: float = 0.01
    sl_points: int = 0
    tp_points: int = 0
    comment: str = "HMA Web Trade"

class CloseRequest(BaseModel):
    ticket: int

class LoginRequest(BaseModel):
    login: int
    password: str
    server: str = "demo.mt5tickmill.com"

class BotConfigRequest(BaseModel):
    symbol: Optional[str] = None
    timeframe_minutes: Optional[int] = None
    hma_period: Optional[int] = None
    second_ma_type: Optional[str] = None
    second_ma_period: Optional[int] = None
    lot_size: Optional[float] = None
    use_stop_loss: Optional[bool] = None
    sl_points: Optional[int] = None
    use_take_profit: Optional[bool] = None
    tp_points: Optional[int] = None
    close_opposite: Optional[bool] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

# API Endpoints
@app.get("/api/account")
def get_account():
    return mt5_client.get_account_info()

@app.post("/api/account/login")
def account_login(req: LoginRequest):
    res = mt5_client.login(req.login, req.password, req.server)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Login failed"))
    return res

@app.get("/api/positions")
def get_positions():
    return mt5_client.get_positions()

@app.get("/api/price/{symbol}")
def get_price(symbol: str):
    return mt5_client.get_symbol_price(symbol.upper())

@app.post("/api/order/open")
def open_order(req: OrderRequest):
    res = mt5_client.open_order(
        symbol=req.symbol.upper(),
        order_type=req.order_type.upper(),
        volume=req.volume,
        sl_points=req.sl_points,
        tp_points=req.tp_points,
        comment=req.comment
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Order failed"))
    return res

@app.post("/api/order/close")
def close_order(req: CloseRequest):
    res = mt5_client.close_position(req.ticket)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Close failed"))
    return res

@app.post("/api/order/close-all")
def close_all_orders():
    return mt5_client.close_by_filter("all")

@app.post("/api/order/close-profit")
def close_profit_orders():
    return mt5_client.close_by_filter("profit")

@app.post("/api/order/close-loss")
def close_loss_orders():
    return mt5_client.close_by_filter("loss")

@app.get("/api/debug/inspect")
def debug_inspect():
    res = {"has_conn": mt5_client.conn is not None}
    if mt5_client.conn:
        try:
            mt5_client.conn.execute("""
def _inspect():
    import MetaTrader5 as mt5
    positions = mt5.positions_get(ticket=331610653)
    if not positions:
        positions = mt5.positions_get()
    
    pos_data = None
    if positions:
        p = positions[0]
        pos_data = {k: getattr(p, k) for k in dir(p) if not k.startswith("_") and not callable(getattr(p, k))}
    
    tick = mt5.symbol_info_tick("XAUUSD")
    sym = mt5.symbol_info("XAUUSD")
    sym_dict = {
        "filling_mode": getattr(sym, "filling_mode", None) if sym else None,
        "trade_exemode": getattr(sym, "trade_exemode", None) if sym else None,
        "trade_mode": getattr(sym, "trade_mode", None) if sym else None,
        "order_mode": getattr(sym, "order_mode", None) if sym else None
    }
    
    variations = []
    if pos_data and tick:
        base = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(pos_data["ticket"]),
            "symbol": pos_data["symbol"],
            "volume": float(pos_data["volume"]),
            "type": 0,
            "price": float(tick.ask),
            "deviation": 50,
            "magic": int(getattr(pos_data, "magic", 0)),
            "comment": "test",
        }
        
        configs = [
            {"desc": "type_filling=1 (IOC), type_time=GTC", "extra": {"type_filling": 1, "type_time": 0}},
            {"desc": "type_filling=0 (FOK), type_time=GTC", "extra": {"type_filling": 0, "type_time": 0}},
            {"desc": "type_filling=2 (RETURN), type_time=GTC", "extra": {"type_filling": 2, "type_time": 0}},
            {"desc": "type_filling=fm directly", "extra": {"type_filling": int(getattr(sym, "filling_mode", 2)), "type_time": 0}},
            {"desc": "type_filling=1, no type_time", "extra": {"type_filling": 1}},
            {"desc": "type_filling=0, no type_time", "extra": {"type_filling": 0}},
            {"desc": "type_filling=2, no type_time", "extra": {"type_filling": 2}},
            {"desc": "no type_filling, no type_time", "extra": {}},
            {"desc": "no type_filling, type_time=GTC", "extra": {"type_time": 0}},
            {"desc": "mt5.ORDER_FILLING_IOC", "extra": {"type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1), "type_time": 0}},
            {"desc": "mt5.ORDER_FILLING_FOK", "extra": {"type_filling": getattr(mt5, "ORDER_FILLING_FOK", 0), "type_time": 0}},
            {"desc": "mt5.ORDER_FILLING_RETURN", "extra": {"type_filling": getattr(mt5, "ORDER_FILLING_RETURN", 2), "type_time": 0}},
        ]
        
        for c in configs:
            req = dict(base)
            req.update(c["extra"])
            chk = mt5.order_check(req)
            if chk:
                variations.append({
                    "config": c["desc"],
                    "retcode": int(chk.retcode),
                    "comment": str(chk.comment)
                })
            else:
                variations.append({
                    "config": c["desc"],
                    "retcode": -1,
                    "error": str(mt5.last_error())
                })
                
    return {
        "pos": pos_data,
        "sym": sym_dict,
        "variations": variations
    }
""")
            res["data"] = mt5_client.conn.eval("_inspect()")
        except Exception as e:
            res["error"] = str(e)
    return res

@app.get("/api/bot/status")
def get_bot_status():
    return bot.get_status()

@app.post("/api/bot/toggle")
async def toggle_bot():
    if bot.is_running:
        bot.stop()
    else:
        bot.start()
    return {"is_running": bot.is_running}

@app.post("/api/bot/config")
def update_bot_config(req: BotConfigRequest):
    data = req.model_dump(exclude_unset=True)
    bot.update_config(data)
    return bot.get_status()

# Static Files & SPA Routing
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    return FileResponse("app/static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
