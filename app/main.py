import os
import json
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional, Dict, Any, List, Literal
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from app.mt5_client import MT5Client, TIMEFRAME_NAMES, MT5DataError
from app.mt5_bridge import AI_MAGIC
from app.ai_performance import summarize as summarize_ai_performance
from app.security import protect_dashboard
from app.live_feed import LiveFeed
from app.strategy_bot import StrategyBot
from app.ai_advisor import DeepSeekAdvisor
from app import security_sessions
from app.backtest import simulate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("HMATradingApp")

import asyncio
import time
import threading

mt5_client = MT5Client()
live_feed = LiveFeed(mt5_client)
bot = StrategyBot(mt5_client)
ai_advisor = DeepSeekAdvisor(mt5_client)
flatten_active = threading.Event()
flatten_guard = threading.Lock()

async def auto_reconnect_loop():
    was_connected = False
    while True:
        try:
            if not mt5_client.is_connected:
                connected = await asyncio.to_thread(mt5_client.connect)
            else:
                connected = await asyncio.to_thread(mt5_client.ensure_connected)
            if was_connected and not connected:
                mt5_client.journal.event('error', 'connection', 'MT5 connection lost')
            elif connected and not was_connected:
                mt5_client.journal.event('info', 'connection', 'MT5 connection restored')
            was_connected = bool(connected)
            if mt5_client.account_type == 'REAL' and not security_sessions.active_session():
                if bot.is_running:
                    bot.stop()
                    mt5_client.journal.event('warning', 'security', 'Real-account automation stopped: unlock expired')
                if ai_advisor.autopilot.get('enabled'):
                    ai_advisor.update_autopilot({'enabled': False})
                    mt5_client.journal.event('warning', 'security', 'Real-account AI autopilot stopped: unlock expired')

            # Autopilot autonomous check
            if connected and os.getenv("DASHBOARD_PASSWORD") and not mt5_client.journal.trading_halted() and ai_advisor.autopilot.get("enabled") and ai_advisor.autopilot.get("mode") == "FULL_AUTO":
                if not ai_advisor.autopilot_budget_available():
                    ai_advisor.update_autopilot({'enabled': False})
                    mt5_client.journal.event('warning', 'ai', 'AI autopilot stopped at reserved daily budget or unknown usage')
                    await asyncio.sleep(5)
                    continue
                now = time.time()
                last_time = ai_advisor.autopilot.get("last_auto_trade_time", 0)
                # Scan once per M15 bucket; trade cooldown is separate from analysis cadence.
                if now - last_time >= 300 and ai_advisor.claim_autopilot_cycle():
                    symbols = ai_advisor.autopilot.get("allowed_symbols", ["EURUSD", "XAUUSD"])
                    generation = ai_advisor.autopilot_generation
                    for sym in symbols:
                        if not ai_advisor.autopilot_budget_available():
                            ai_advisor.update_autopilot({'enabled': False})
                            mt5_client.journal.event('warning', 'ai', 'AI autopilot stopped at reserved daily budget or unknown usage')
                            break
                        tick = await asyncio.to_thread(mt5_client.get_symbol_price, sym)
                        if tick and tick.get("bid"):
                            rates = await asyncio.to_thread(mt5_client.get_rates, sym, 15, 30) or []
                            open_pos = await asyncio.to_thread(mt5_client.get_positions)
                            adv = await asyncio.to_thread(ai_advisor.get_market_advice, sym, "M15", tick=tick, rates=rates, open_positions=open_pos, source="autopilot")
                            if adv.get("success") and not adv.get("cached"):
                                rec = adv.get("recommendation", {})
                                conf = rec.get("confidence", 0)
                                action = rec.get("action")
                                min_conf = ai_advisor.autopilot.get("min_confidence", 80)
                                if action in ("BUY", "SELL") and conf >= min_conf:
                                    logger.info(f"AI FULL_AUTO triggering trade on {sym}: {action} (Confidence: {conf}%)")
                                    await asyncio.to_thread(ai_advisor.execute_recommendation, rec, automatic=True, generation=generation)
                                    break
        except Exception as e:
            logger.debug(f"auto_reconnect_loop error: {e}")
        await asyncio.sleep(5)

async def maintenance_loop():
    from app.maintenance import create_backup
    last_backup = 0
    last_risk_alert = 0
    while True:
        try:
            if time.time() - last_backup >= 86400:
                backup_path = await asyncio.to_thread(create_backup)
                logger.info("Doğrulanmış durum yedeği oluşturuldu: %s", backup_path)
                last_backup = time.time()
        except Exception:
            logger.exception("Yedekleme tamamlanamadı")
        try:
            if mt5_client.is_connected:
                await asyncio.to_thread(mt5_client.reconcile_orders)
        except Exception:
            logger.exception("Emir doğrulama tamamlanamadı")
        try:
            if mt5_client.is_connected and mt5_client.login_id:
                status = await asyncio.to_thread(mt5_client.get_risk_status)
                level = 2 if status['ratio'] >= 1 else 1 if status['ratio'] >= .8 else 0
                if level > last_risk_alert:
                    mt5_client.journal.event('error' if level == 2 else 'warning', 'risk',
                        f"Daily loss {status['daily_loss']} / {status['daily_loss_limit']} {status['currency']}")
                    if level == 2:
                        bot.stop()
                        ai_advisor.update_autopilot({'enabled': False})
                last_risk_alert = level
        except Exception:
            logger.debug('Risk alert data unavailable')
        await asyncio.sleep(30)

def ensure_optimized_server_py():
    p = "/config/server.py"
    if os.path.exists(p) or os.path.exists("/config"):
        try:
            clean_code = '''import rpyc
from rpyc.utils.server import ThreadedServer
import time
import sys

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] >>> RPYC SUNUCUSU BASLATILDI (0.0.0.0:8001) <<<", flush=True)
try:
    server = ThreadedServer(
        rpyc.SlaveService,
        hostname="0.0.0.0",
        port=8001,
        reuse_addr=True,
        protocol_config={"allow_all_attrs": True, "sync_request_timeout": 30}
    )
    server.start()
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server error: {e}", file=sys.stderr, flush=True)
    sys.exit(1)
'''
            needs_update = False
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                if "_lazy_mt5_init" in content or "sync_request_timeout" not in content:
                    needs_update = True
            else:
                needs_update = True

            if needs_update:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(clean_code)
                logger.info("Updated /config/server.py with clean RPyC server.")
        except Exception as e:
            logger.warning(f"ensure_optimized_server_py error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting HMA Trading Web Application...")
    security_sessions.initialize()
    ensure_optimized_server_py()
    if mt5_client.journal.trading_halted():
        mt5_client.automation_stopped.set()
        ai_advisor.update_autopilot({"enabled": False})
    # MT5 may be offline; the HTTP server must still start and expose status.
    task = asyncio.create_task(auto_reconnect_loop())
    feed_task = asyncio.create_task(live_feed.run())
    maintenance_task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        bot.stop()
        task.cancel()
        feed_task.cancel()
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await feed_task
        with suppress(asyncio.CancelledError):
            await maintenance_task
        logger.info("Shutting down HMA Trading Application...")

app = FastAPI(title="HMA Trading Dashboard", lifespan=lifespan)
app.middleware("http")(protect_dashboard)

@app.exception_handler(MT5DataError)
async def mt5_data_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

# Pydantic Schemas
class OrderRequest(BaseModel):
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    model_config = ConfigDict(allow_inf_nan=False)
    symbol: str
    order_type: Literal["BUY", "SELL"]
    volume: float = Field(default=0.01, gt=0)
    sl_points: int = Field(default=0, ge=0)
    tp_points: int = Field(default=0, ge=0)
    comment: str = "HMA Web Trade"

class ActionRequest(BaseModel):
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

class CloseRequest(ActionRequest):
    ticket: int = Field(gt=0)

class LoginRequest(BaseModel):
    login: int = Field(gt=0)
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)
    account_type: Literal["DEMO", "REAL"]

class BotConfigRequest(BaseModel):
    symbol: Optional[str] = None
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=43200)
    hma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    second_ma_type: Optional[Literal["EMA", "SMA", "LWMA", "HMA"]] = None
    second_ma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    lot_size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    use_stop_loss: Optional[bool] = None
    sl_points: Optional[int] = Field(default=None, ge=0)
    use_take_profit: Optional[bool] = None
    tp_points: Optional[int] = Field(default=None, ge=0)
    close_opposite: Optional[bool] = None

class AIAdviceRequest(BaseModel):
    symbol: Optional[str] = "EURUSD"
    timeframe: Optional[str] = "M15"
    language: Literal["tr", "en"] = "tr"

class AILearnRequest(BaseModel):
    language: Literal["tr", "en"] = "tr"

class AIExecuteRequest(BaseModel):
    recommendation: Dict[str, Any]

class AIAutopilotRequest(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[Literal["ADVISORY", "SEMI_AUTO", "FULL_AUTO"]] = None
    min_confidence: Optional[int] = None
    max_lot: Optional[float] = Field(default=None, gt=0, le=5, allow_inf_nan=False)
    daily_loss_limit: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    allowed_symbols: Optional[List[str]] = None
    confirm_real_full_auto: bool = False

class PendingRequest(OrderRequest):
    pending_type: Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]
    entry_price: float = Field(gt=0)

class PreviewRequest(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9_.#-]+$", max_length=32)
    order_type: Literal["BUY", "SELL"]
    volume: float = Field(gt=0, allow_inf_nan=False)
    sl_points: int = Field(ge=0)
    pending_type: Optional[Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]] = None
    entry_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

class UnlockRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")

class BacktestRequest(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9_.#-]+$", max_length=32)
    timeframe_minutes: int = 15
    candles: int = Field(default=600, ge=100, le=1000)
    hma_period: int = Field(default=14, ge=2, le=100)
    second_ma_type: Literal['EMA','SMA','LWMA','HMA'] = 'EMA'
    second_ma_period: int = Field(default=34, ge=2, le=100)
    sl_points: int = Field(default=200, ge=0, le=10000)
    tp_points: int = Field(default=0, ge=0, le=10000)
    commission_points: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)

@app.post('/api/backtest')
def run_backtest(req: BacktestRequest):
    if req.timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    rates = mt5_client.get_rates(req.symbol.upper(), req.timeframe_minutes, req.candles + 1)
    if not rates or len(rates) < 2:
        raise HTTPException(status_code=503, detail='Mum verisi alınamadı.')
    # Last candle is still forming; never use it as historical evidence.
    rates = rates[:-1]
    price = mt5_client.get_symbol_price(req.symbol.upper())
    info = mt5_client.get_symbol_spec(req.symbol.upper())
    if not info or not info.get('point') or not price.get('bid'):
        raise HTTPException(status_code=503, detail='Broker sembol özellikleri doğrulanamadı.')
    try:
        return simulate(rates, req.hma_period, req.second_ma_type, req.second_ma_period,
                        req.sl_points, req.tp_points, float(info['point']), float(price.get('spread', 0)),
                        req.commission_points)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.get('/api/broker/diagnostics')
def broker_diagnostics(symbol: str = Query(default='EURUSD', pattern=r'^[A-Za-z0-9_.#-]+$', max_length=32)):
    spec = mt5_client.get_symbol_spec(symbol)
    checks = mt5_client.broker_compatibility(symbol)
    account = mt5_client.get_account_info()
    return {'account_verified': bool(account.get('connected') and not account.get('account_mismatch')),
            'account_type': spec['account_type'], 'position_mode': 'HEDGING' if spec['account_mode'] == 2 else 'NETTING_OR_EXCHANGE',
            'strategy_automation_supported': spec['account_mode'] == 2,
            'market_order_supported': bool(spec['order_mode'] & 1),
            'limit_order_supported': bool(spec['order_mode'] & 2),
            'stop_order_supported': bool(spec['order_mode'] & 4),
            'gtc_supported': bool(spec['expiration_mode'] & 1),
            'fok_supported': bool(spec['filling_mode'] & 1),
            'ioc_supported': bool(spec['filling_mode'] & 2),
            'quote_age_seconds': max(0, int(time.time()-(spec['tick_time']-mt5_client.tick_clock_offset))),
            'symbol': spec, 'dry_run': checks}

def require_real_unlock(request: Request):
    if mt5_client.account_type == 'REAL':
        username = request.state.dashboard_user
        token = request.cookies.get(security_sessions.COOKIE)
        if not security_sessions.session_expires(token, username):
            raise HTTPException(status_code=403, detail='Gerçek hesap işlemi için ikinci doğrulama gerekli.')

@app.get('/api/security/status')
def security_status(request: Request):
    expires = security_sessions.session_expires(request.cookies.get(security_sessions.COOKIE),
                                                request.state.dashboard_user)
    return {'real_account_requires_otp': True, 'unlocked': bool(expires),
            'seconds_remaining': max(0, int(expires - time.time()))}

@app.post('/api/security/unlock')
def security_unlock(req: UnlockRequest, request: Request):
    username = request.state.dashboard_user
    if not security_sessions.allow_attempt(username):
        raise HTTPException(status_code=429, detail='Çok fazla deneme; beş dakika bekleyin.')
    valid = security_sessions.verify_totp(req.code, username=username)
    security_sessions.record_attempt(username, valid)
    if not valid:
        raise HTTPException(status_code=401, detail='Doğrulama kodu geçersiz.')
    token, expires = security_sessions.unlock(username)
    response = JSONResponse({'unlocked': True, 'seconds_remaining': int(expires - time.time())})
    response.set_cookie(security_sessions.COOKIE, token, max_age=security_sessions.SESSION_SECONDS,
                        secure=True, httponly=True, samesite='strict', path='/')
    return response

@app.post('/api/security/lock')
def security_lock(request: Request):
    security_sessions.revoke(request.cookies.get(security_sessions.COOKIE))
    if mt5_client.account_type == 'REAL' and not security_sessions.active_session():
        bot.stop()
        ai_advisor.update_autopilot({'enabled': False})
    response = JSONResponse({'unlocked': False})
    response.delete_cookie(security_sessions.COOKIE, path='/')
    return response

class StopsRequest(CloseRequest):
    sl: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    tp: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    expected_sl: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    expected_tp: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

class PartialCloseRequest(CloseRequest):
    volume: float = Field(gt=0, allow_inf_nan=False)


def trade_response(result):
    if result.get("success"):
        return result
    return JSONResponse(status_code=409 if result.get("conflict") else 400,
                        content={**result, "detail": result.get("error", "İşlem tamamlanamadı.")})


@app.get("/api/live")
async def live(symbol: str = Query(default="EURUSD", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.#-]+$")):
    try:
        queue = live_feed.subscribe(symbol.upper())
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    async def events():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=10)
                    yield "data: " + json.dumps(data, allow_nan=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            live_feed.unsubscribe(queue)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@app.get("/api/orders")
def pending_orders():
    return mt5_client.get_pending_orders()

@app.post("/api/trade/preview")
def trade_preview(req: PreviewRequest):
    result = mt5_client.trade_preview(req.symbol, req.order_type, req.volume,
                                      req.sl_points, req.pending_type, req.entry_price)
    return trade_response(result)


@app.post("/api/order/pending")
def place_pending(req: PendingRequest, _: None = Depends(require_real_unlock)):
    return trade_response(mt5_client.open_order(req.symbol.upper(), req.order_type, req.volume,
        sl_points=req.sl_points, tp_points=req.tp_points, comment="Panel pending", request_id=req.request_id,
        pending_type=req.pending_type, entry_price=req.entry_price))


@app.post("/api/order/cancel")
def cancel_pending(req: CloseRequest):
    return trade_response(mt5_client.cancel_pending(req.ticket, "cancel-" + req.request_id))


@app.post("/api/position/stops")
def update_stops(req: StopsRequest, _: None = Depends(require_real_unlock)):
    if req.sl is None and req.tp is None:
        raise HTTPException(status_code=422, detail="En az bir SL/TP fiyatı gerekli.")
    return trade_response(mt5_client.manage_position(req.ticket, "stops",
        req.model_dump(exclude={"ticket", "request_id"}, exclude_none=True), "stops-" + req.request_id))


@app.post("/api/position/partial-close")
def partial_close(req: PartialCloseRequest):
    return trade_response(mt5_client.manage_position(req.ticket, "partial", {"volume":req.volume}, "partial-" + req.request_id))


# API Endpoints
@app.get("/api/operations/latency")
def operation_latency():
    return mt5_client.journal.latency()

@app.get('/api/operations/events')
def operation_events(after: int = Query(default=0, ge=0)):
    return mt5_client.journal.events(after=after)

@app.get('/api/risk/status')
def account_risk_status():
    return mt5_client.get_risk_status()

@app.get("/api/operations/status")
def operation_status(request_id: str = Query(min_length=1, max_length=250)):
    return mt5_client.journal.status(request_id)

@app.get("/api/trading/status")
def trading_status():
    return {"new_orders_halted": mt5_client.journal.trading_halted(),
            "flatten_active": flatten_active.is_set()}

@app.post("/api/trading/resume")
def resume_new_orders(_: None = Depends(require_real_unlock)):
    with mt5_client._lock.order():
        if flatten_active.is_set():
            raise HTTPException(status_code=409, detail="Toplu kapatma sürüyor; bitmesini bekleyin.")
        if not mt5_client.ensure_connected():
            raise HTTPException(status_code=503, detail="MT5 bağlantısı doğrulanamadı.")
        try:
            mt5_client._selected_account()
        except MT5DataError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        mt5_client.journal.set_trading_halted(False)
        mt5_client.journal.event('info', 'trading', 'New orders resumed')
        return {"new_orders_halted": False}

@app.get("/api/account")
def get_account():
    return mt5_client.get_account_info()

@app.get('/api/auth/me')
def current_operator(request: Request):
    return {'username': request.state.dashboard_user, 'role': request.state.dashboard_role}

@app.post("/api/account/login")
def account_login(req: LoginRequest, request: Request):
    if req.account_type == 'REAL':
        username = request.state.dashboard_user
        if not security_sessions.session_expires(request.cookies.get(security_sessions.COOKIE), username):
            raise HTTPException(status_code=403, detail='Gerçek hesaba giriş için ikinci doğrulama gerekli.')
    mt5_client.automation_stopped.set()
    bot.stop()
    ai_advisor.update_autopilot({"enabled": False})
    res = mt5_client.login(req.login, req.password, req.server, req.account_type)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Login failed"))
    ai_advisor.sync_account_scope()
    mt5_client.journal.event('info', 'account', 'Broker account connected')
    return res

@app.get("/api/positions")
def get_positions():
    return mt5_client.get_positions()

@app.get("/api/history")
def get_history(days: int = Query(default=30, ge=1, le=365)):
    return mt5_client.get_history(days=days)

@app.get("/api/reports")
def get_reports(days: int = Query(default=30, ge=1, le=365)):
    return mt5_client.get_reports(days=days)

@app.get("/api/price/{symbol}")
def get_price(symbol: str):
    return mt5_client.get_symbol_price(symbol.upper())

@app.post("/api/order/open")
def open_order(req: OrderRequest, _: None = Depends(require_real_unlock)):
    res = mt5_client.open_order(
        symbol=req.symbol.upper(),
        order_type=req.order_type.upper(),
        volume=req.volume,
        sl_points=req.sl_points,
        tp_points=req.tp_points,
        comment=req.comment, request_id=req.request_id
    )
    if not res.get("success"):
        return JSONResponse(status_code=409 if res.get("conflict") else 400,
                            content={**res, "detail": res.get("error", "Order failed")})
    return res

@app.post("/api/order/close")
def close_order(req: CloseRequest):
    res = mt5_client.journal.run("close-" + req.request_id, {"ticket": req.ticket},
                                 lambda: mt5_client.close_position(req.ticket))
    if not res.get("success"):
        return JSONResponse(status_code=400, content={**res, "detail": res.get("error", "Close failed")})
    return res

def flatten_account(request_id):
    with mt5_client._lock.order():
        errors, cancelled = [], 0
        uncertain = pending = False
        try:
            orders = mt5_client.get_pending_orders()
            for order in orders:
                result = mt5_client.cancel_pending(order["ticket"], f"flatten-{request_id}-{order['ticket']}")
                if result.get("success") and not result.get("pending") and not result.get("partial"):
                    cancelled += 1
                else:
                    errors.append(f"Emir #{order['ticket']}: {result.get('error') or 'İptal doğrulanamadı'}")
                    uncertain |= bool(result.get("uncertain"))
                    pending |= bool(result.get("pending"))
        except MT5DataError as exc:
            errors.append(str(exc))
        # Close exposure even if a pending-order query/cancel failed.
        result = mt5_client.close_by_filter("all")
        try:
            remaining_orders = mt5_client.get_pending_orders()
            remaining_positions = mt5_client.get_positions(fresh=True)
            if remaining_orders or remaining_positions:
                errors.append(f"Brokerda hâlâ {len(remaining_positions)} pozisyon ve {len(remaining_orders)} bekleyen emir var.")
        except MT5DataError as exc:
            errors.append("Kapatma sonrası broker durumu doğrulanamadı: " + str(exc))
        result["errors"] = errors + result.get("errors", [])
        result["success"] = bool(result.get("success")) and not result["errors"]
        result["cancelled_count"] = cancelled
        result["uncertain"] = bool(result.get("uncertain")) or uncertain
        result["pending"] = bool(result.get("pending")) or pending
        return result


@app.post("/api/order/close-all")
def close_all_orders(req: ActionRequest):
    if not flatten_guard.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Toplu kapatma zaten sürüyor.")
    flatten_active.set()
    try:
        # Persist before waiting for MT5; queued manual and automated opens must stop too.
        mt5_client.journal.set_trading_halted(True)
        mt5_client.journal.event('warning', 'trading', 'Emergency close-all started')
        mt5_client.automation_stopped.set()
        bot.stop()
        ai_advisor.update_autopilot({"enabled": False})
        return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "all", "cancel_pending": True},
                                     lambda: flatten_account(req.request_id))
    finally:
        flatten_active.clear()
        flatten_guard.release()

@app.post("/api/order/close-profit")
def close_profit_orders(req: ActionRequest):
    return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "profit"},
                                 lambda: mt5_client.close_by_filter("profit"))

@app.post("/api/order/close-loss")
def close_loss_orders(req: ActionRequest):
    return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "loss"},
                                 lambda: mt5_client.close_by_filter("loss"))

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
async def toggle_bot(request: Request):
    if bot.is_running:
        bot.stop()
    else:
        require_real_unlock(request)
        if mt5_client.journal.trading_halted():
            raise HTTPException(status_code=409, detail="Yeni emirler durduruldu; önce işlemlere devam edin.")
        bot.start()
    return {"is_running": bot.is_running}

@app.post("/api/bot/config")
def update_bot_config(req: BotConfigRequest):
    if bot.is_running or (bot.task is not None and not bot.task.done()):
        raise HTTPException(status_code=409, detail="Ayarları değiştirmeden önce botu durdurun ve mevcut döngünün bitmesini bekleyin.")
    data = req.model_dump(exclude_unset=True, exclude_none=True)
    if data.get("timeframe_minutes", bot.timeframe_minutes) not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    bot.update_config(data)
    return bot.get_status()

# DeepSeek AI Advisor & Autopilot Endpoints
@app.get("/api/ai/status")
def get_ai_status():
    return ai_advisor.get_status()

@app.get('/api/ai/performance')
def get_ai_performance():
    # Broker history is account-specific. Report closed AI positions only.
    with mt5_client._lock:
        mt5_client._selected_account()
        account_type = mt5_client.account_type
        deals = mt5_client.get_history(days=90)
        open_ids = [p['ticket'] for p in mt5_client.get_positions(fresh=True)]
    return {'account_type': account_type, 'days': 90,
            **summarize_ai_performance(deals, ai_advisor.execution_records, open_ids),
            'currency': mt5_client.get_account_info().get('currency', ''),
            'basis': 'Brokerın 90 günlük geçmişinde AI etiketi bulunan ve tamamen kapanmış pozisyonlar. Model güveni başarı olasılığı değildir; geçmiş maliyetleri veya eşleşmeyen emirler eksik olabilir.'}

@app.post("/api/ai/learn")
def trigger_ai_learning(req: Optional[AILearnRequest] = None):
    deals = mt5_client.get_history(days=90)
    res = ai_advisor.analyze_user_trades(deals, language=req.language if req else "tr")
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Öğrenme analizi başarısız"))
    return res

@app.post("/api/ai/advice")
def get_ai_advice(req: AIAdviceRequest):
    sym = (req.symbol or "EURUSD").upper()
    tick = mt5_client.get_symbol_price(sym)
    timeframe = (req.timeframe or "M15").upper()
    minutes = next((m for m, name in TIMEFRAME_NAMES.items() if name == "TIMEFRAME_" + timeframe), None)
    if minutes is None:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    rates = mt5_client.get_rates(sym, minutes, 30) or []
    open_pos = mt5_client.get_positions()
    hma_val = bot.current_hma if bot.symbol == sym else None
    ma2_val = bot.current_ma2 if bot.symbol == sym else None
    res = ai_advisor.get_market_advice(
        symbol=sym,
        timeframe_name=timeframe,
        tick=tick,
        rates=rates,
        open_positions=open_pos,
        hma_val=hma_val,
        ma2_val=ma2_val,
        language=req.language
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Tavsiye üretilemedi"))
    return res

@app.post("/api/ai/execute")
def execute_ai_advice(req: AIExecuteRequest, _: None = Depends(require_real_unlock)):
    res = ai_advisor.execute_recommendation(req.recommendation)
    if not res.get("success"):
        return JSONResponse(status_code=400, content={**res, "detail": res.get("error", "İşlem açılamadı")})
    return res

@app.post("/api/ai/autopilot")
def update_ai_autopilot(req: AIAutopilotRequest, request: Request):
    data = req.model_dump(exclude_unset=True, exclude={"confirm_real_full_auto"})
    if data.get('enabled', ai_advisor.autopilot.get('enabled')):
        require_real_unlock(request)
    if data.get("enabled") and mt5_client.journal.trading_halted():
        raise HTTPException(status_code=409, detail="Yeni emirler durduruldu; önce işlemlere devam edin.")
    enabled = data.get("enabled", ai_advisor.autopilot.get("enabled"))
    mode = data.get("mode", ai_advisor.autopilot.get("mode"))
    if enabled and mode == "FULL_AUTO":
        with mt5_client._lock:
            if not mt5_client.ensure_connected():
                raise HTTPException(status_code=503, detail="MT5 bağlantısı doğrulanamadı.")
            try:
                mt5_client._selected_account()
            except MT5DataError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
        if mt5_client.account_type == "REAL" and not req.confirm_real_full_auto:
            raise HTTPException(status_code=409, detail="Gerçek hesapta tam otomatik işlem için açık onay gerekli.")
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

@app.get("/api/debug/diagnostic")
def get_diagnostic():
    import socket
    info = {}
    
    # 1. Environment
    host = os.getenv("MT5_HOST", "metatrader5")
    info["env"] = {
        "MT5_HOST": host,
        "MT5_PORT": os.getenv("MT5_PORT", "8001"),
        "client_connected": mt5_client.is_connected,
        "client_last_error": mt5_client.last_error_msg,
    }
    
    # 2. DNS & TCP probe
    try:
        ip = socket.gethostbyname(host)
        info["dns"] = {"host": host, "ip": ip}
    except Exception as e:
        info["dns"] = {"host": host, "error": str(e)}
        
    for p in [8001, 3000, 3001]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            err = s.connect_ex((host, p))
            s.close()
            info[f"tcp_{host}_{p}"] = "OPEN" if err == 0 else f"ERR_{err}"
        except Exception as e:
            info[f"tcp_{host}_{p}"] = str(e)
            
    # 3. Files in /config
    try:
        info["config_files"] = os.listdir("/config") if os.path.exists("/config") else "No /config"
    except Exception as e:
        info["config_files"] = str(e)
        
    # 4. mt5_watchdog.log
    for wp in ["/config/mt5_watchdog.log", "/tmp/mt5_watchdog.log"]:
        if os.path.exists(wp):
            try:
                with open(wp, "r", encoding="utf-8", errors="replace") as f:
                    info["watchdog_log"] = [l.strip() for l in f.readlines()[-30:]]
            except Exception as e:
                info["watchdog_log"] = str(e)
            break
            
    # 5. Direct RPyC test
    try:
        import rpyc
        c = rpyc.classic.connect(host, 8001)
        c._config["sync_request_timeout"] = 30
        info["rpyc_connect"] = "SUCCESS"
        
        try:
            m = c.modules.MetaTrader5
            info["mt5_module"] = "IMPORTED"
            
            try:
                v = m.version()
                info["mt5_version"] = list(v) if v else None
            except Exception as ve:
                info["mt5_version_error"] = str(ve)
                
            try:
                tinfo = m.terminal_info()
                if tinfo:
                    info["terminal_info"] = {k: getattr(tinfo, k) for k in dir(tinfo) if not k.startswith("_") and not callable(getattr(tinfo, k))}
                else:
                    info["terminal_info"] = None
                    info["terminal_last_error"] = str(m.last_error())
            except Exception as te:
                info["terminal_info_error"] = str(te)
                
            try:
                acc = m.account_info()
                if acc:
                    info["account_info"] = {k: getattr(acc, k) for k in dir(acc) if not k.startswith("_") and not callable(getattr(acc, k))}
                else:
                    info["account_info"] = None
                    info["account_last_error"] = str(m.last_error())
            except Exception as ae:
                info["account_info_error"] = str(ae)
        except Exception as me:
            info["mt5_module_error"] = str(me)
            
        c.close()
    except Exception as re:
        info["rpyc_connect_error"] = str(re)
        
    return info

@app.get("/api/debug/wine-info")
def get_wine_info():
    import rpyc
    host = os.getenv("MT5_HOST", "metatrader5")
    res = {}
    try:
        c = rpyc.classic.connect(host, 8001)
        c._config["sync_request_timeout"] = 20
        
        py_os = c.modules.os
        py_sys = c.modules.sys
        
        res["python_exe"] = str(py_sys.executable)
        res["env_user"] = str(py_os.environ.get("USERNAME", py_os.environ.get("USER")))
        res["env_wineprefix"] = str(py_os.environ.get("WINEPREFIX"))
        res["cwd"] = str(py_os.getcwd())
        
        # Check running processes
        try:
            p = c.modules.subprocess.run("tasklist", capture_output=True, text=True, shell=True)
            res["tasklist"] = [l.strip() for l in p.stdout.splitlines() if "terminal" in l.lower() or "python" in l.lower() or "wine" in l.lower()]
        except Exception as te:
            res["tasklist_err"] = str(te)
            
        m = c.modules.MetaTrader5
        
        # Test 1: initialize without path
        try:
            ok1 = bool(m.initialize())
            res["init_default"] = {"ok": ok1, "error": list(m.last_error()) if m.last_error() else None}
        except Exception as e1:
            res["init_default_err"] = str(e1)
            
        # Test 2: initialize with common Windows paths
        paths_to_test = [
            "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
            "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.exe",
            "terminal64.exe"
        ]
        for p_test in paths_to_test:
            try:
                ok_p = bool(m.initialize(path=p_test))
                res[f"init_{p_test}"] = {"ok": ok_p, "error": list(m.last_error()) if m.last_error() else None}
                if ok_p:
                    break
            except Exception as ep:
                res[f"init_{p_test}_err"] = str(ep)
                
        # Terminal & account info
        try:
            tinfo = m.terminal_info()
            res["terminal_info"] = {k: getattr(tinfo, k) for k in dir(tinfo) if not k.startswith("_") and not callable(getattr(tinfo, k))} if tinfo else None
        except Exception as e:
            res["terminal_info_err"] = str(e)
            
        try:
            acc = m.account_info()
            res["account_info"] = {k: getattr(acc, k) for k in dir(acc) if not k.startswith("_") and not callable(getattr(acc, k))} if acc else None
        except Exception as e:
            res["account_info_err"] = str(e)

        c.close()
    except Exception as ge:
        res["general_error"] = str(ge)
    return res

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
