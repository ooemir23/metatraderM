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
logger = logging.getLogger("VoltaTradingApp")

mt5_client = MT5Client()
bot = StrategyBot(mt5_client)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Volta Trading Web Application...")
    # Try initial connection in background
    mt5_client.connect()
    yield
    logger.info("Shutting down Volta Trading Application...")
    bot.stop()

app = FastAPI(title="Volta Trading Dashboard", lifespan=lifespan)

# Pydantic Schemas
class OrderRequest(BaseModel):
    symbol: str
    order_type: str # "BUY" or "SELL"
    volume: float = 0.01
    sl_points: int = 0
    tp_points: int = 0
    comment: str = "Web Trade"

class CloseRequest(BaseModel):
    ticket: int

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
    return mt5_client.close_all()

@app.get("/api/bot/status")
def get_bot_status():
    return bot.get_status()

@app.post("/api/bot/toggle")
def toggle_bot():
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
