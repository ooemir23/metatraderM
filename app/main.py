import os
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.mt5_client import MT5Client
from app.strategy_bot import StrategyBot
from app.ai_advisor import DeepSeekAdvisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("HMATradingApp")

import asyncio
import time

mt5_client = MT5Client()
bot = StrategyBot(mt5_client)
ai_advisor = DeepSeekAdvisor(mt5_client)

async def auto_reconnect_loop():
    while True:
        try:
            connected = await asyncio.to_thread(mt5_client.ensure_connected)

            # Autopilot autonomous check
            if connected and ai_advisor.autopilot.get("enabled") and ai_advisor.autopilot.get("mode") == "FULL_AUTO":
                now = time.time()
                last_time = ai_advisor.autopilot.get("last_auto_trade_time", 0)
                # Check at most once every 300 seconds (5 minutes)
                if now - last_time >= 300:
                    symbols = ai_advisor.autopilot.get("allowed_symbols", ["EURUSD", "XAUUSD"])
                    for sym in symbols:
                        tick = await asyncio.to_thread(mt5_client.get_symbol_price, sym)
                        if tick and tick.get("bid"):
                            rates = await asyncio.to_thread(mt5_client.get_rates, sym, 15, 30) or []
                            open_pos = await asyncio.to_thread(mt5_client.get_positions)
                            adv = await asyncio.to_thread(ai_advisor.get_market_advice, sym, "M15", tick=tick, rates=rates, open_positions=open_pos)
                            if adv.get("success"):
                                rec = adv.get("recommendation", {})
                                conf = rec.get("confidence", 0)
                                action = rec.get("action")
                                min_conf = ai_advisor.autopilot.get("min_confidence", 80)
                                if action in ("BUY", "SELL") and conf >= min_conf:
                                    logger.info(f"AI FULL_AUTO triggering trade on {sym}: {action} (Confidence: {conf}%)")
                                    await asyncio.to_thread(ai_advisor.execute_recommendation, rec)
                                    break
        except Exception as e:
            logger.debug(f"auto_reconnect_loop error: {e}")
        await asyncio.sleep(4)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting HMA Trading Web Application...")
    mt5_client.connect()
    task = asyncio.create_task(auto_reconnect_loop())
    yield
    task.cancel()
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
    server: str = "Tickmill-Demo"

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

class AIAdviceRequest(BaseModel):
    symbol: Optional[str] = "EURUSD"
    timeframe: Optional[str] = "M15"

class AIExecuteRequest(BaseModel):
    recommendation: Optional[Dict[str, Any]] = None

class AIAutopilotRequest(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    min_confidence: Optional[int] = None
    max_lot: Optional[float] = None
    daily_loss_limit: Optional[float] = None
    allowed_symbols: Optional[List[str]] = None

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

@app.get("/api/history")
def get_history(days: int = 30):
    return mt5_client.get_history(days=days)

@app.get("/api/reports")
def get_reports(days: int = 30):
    return mt5_client.get_reports(days=days)

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

# DeepSeek AI Advisor & Autopilot Endpoints
@app.get("/api/ai/status")
def get_ai_status():
    return ai_advisor.get_status()

@app.post("/api/ai/learn")
def trigger_ai_learning():
    deals = mt5_client.get_history(days=90)
    rep = mt5_client.get_reports(days=90)
    stats = rep.get("summary", {}) if rep else {}
    res = ai_advisor.analyze_user_trades(deals, stats)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Öğrenme analizi başarısız"))
    return res

@app.post("/api/ai/advice")
def get_ai_advice(req: AIAdviceRequest):
    sym = (req.symbol or "EURUSD").upper()
    tick = mt5_client.get_symbol_price(sym)
    rates = mt5_client.get_rates(sym, 15, 30) or []
    open_pos = mt5_client.get_positions()
    hma_val = bot.current_hma if bot.symbol == sym else None
    ma2_val = bot.current_ma2 if bot.symbol == sym else None
    res = ai_advisor.get_market_advice(
        symbol=sym,
        timeframe_name=req.timeframe or "M15",
        tick=tick,
        rates=rates,
        open_positions=open_pos,
        hma_val=hma_val,
        ma2_val=ma2_val
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Tavsiye üretilemedi"))
    return res

@app.post("/api/ai/execute")
def execute_ai_advice(req: AIExecuteRequest):
    res = ai_advisor.execute_recommendation(req.recommendation)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "İşlem açılamadı"))
    return res

@app.post("/api/ai/autopilot")
def update_ai_autopilot(req: AIAutopilotRequest):
    data = req.model_dump(exclude_unset=True)
    return ai_advisor.update_autopilot(data)

@app.get("/api/debug/server-log")
def get_server_log():
    for p in ["/config/server.log", "/tmp/server.log"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                return {"success": True, "path": p, "log": [line.strip() for line in lines[-100:]]}
            except Exception as e:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": "server.log not found"}

# Static Files & SPA Routing
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    return FileResponse("app/static/index.html")

@app.get("/ai")
def read_ai_dashboard():
    return FileResponse("app/static/ai.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
