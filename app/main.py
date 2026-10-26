import os
import json
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional, Dict, Any, List, Literal, Union
from fastapi import FastAPI, HTTPException, Query, Request, Depends, Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field, ConfigDict

from app.mt5_client import MT5Client, TIMEFRAME_NAMES, MT5DataError
from app.security import protect_dashboard
from app.live_feed import LiveFeed
from app.trailing_stops import TrailingStops
from app.position_rules import PositionRules
from app.native_terminal import NativeTerminal
from app.strategy_bot import StrategyBot
from app.mevo import MevO
from app import security_sessions
from app.order_journal import state_path
from app.backtest import simulate
from app.strategy_research import Settings as ResearchSettings, compare as compare_research

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("HMATradingApp")

import asyncio
import math
import re
import time
import threading

mt5_client = MT5Client()
live_feed = LiveFeed(mt5_client)
trailing_stops = TrailingStops(mt5_client)
position_rules = PositionRules(mt5_client)
bot = StrategyBot(mt5_client, config_path=state_path('bot_settings.json'))
mevo = MevO(mt5_client)
flatten_active = threading.Event()
flatten_guard = threading.Lock()
bot_toggle_guard = asyncio.Lock()

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
                level = 0 if not status['daily_limit_enabled'] else 2 if status['ratio'] >= 1 else 1 if status['ratio'] >= .8 else 0
                if level > last_risk_alert:
                    mt5_client.journal.event('error' if level == 2 else 'warning', 'risk',
                        f"Daily loss {status['daily_loss']} / {status['daily_loss_limit']} {status['currency']}")
                    if level == 2:
                        bot.stop()
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
    # MT5 may be offline; the HTTP server must still start and expose status.
    task = asyncio.create_task(auto_reconnect_loop())
    feed_task = asyncio.create_task(live_feed.run())
    maintenance_task = asyncio.create_task(maintenance_loop())
    trailing_task = asyncio.create_task(trailing_stops.run())
    rules_task = asyncio.create_task(position_rules.run())
    try:
        yield
    finally:
        # Cancelling asyncio.to_thread does not stop its queued worker.
        mt5_client.automation_stopped.set()
        bot.stop()
        await asyncio.to_thread(trailing_stops.stop)
        await asyncio.to_thread(position_rules.stop)
        task.cancel()
        feed_task.cancel()
        maintenance_task.cancel()
        trailing_task.cancel()
        rules_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await feed_task
        with suppress(asyncio.CancelledError):
            await maintenance_task
        with suppress(asyncio.CancelledError):
            await trailing_task
        with suppress(asyncio.CancelledError):
            await rules_task
        logger.info("Shutting down HMA Trading Application...")

app = FastAPI(title="HMA Trading Dashboard", lifespan=lifespan)
native_terminal = NativeTerminal(login_mode_allowed=lambda:mt5_client.terminal_managed_login)
app.include_router(native_terminal.router)
app.middleware("http")(protect_dashboard)

@app.exception_handler(MT5DataError)
async def mt5_data_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

# Pydantic Schemas
class AccountBoundRequest(BaseModel):
    expected_account: Optional[tuple[int, str, Literal['DEMO', 'REAL']]] = None

class OrderRequest(AccountBoundRequest):
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    symbol: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')
    order_type: Literal["BUY", "SELL"]
    volume: float = Field(default=0.01, gt=0)
    sl_points: int = Field(default=0, ge=0)
    tp_points: int = Field(default=0, ge=0)
    sl_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    tp_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    deviation_points: int = Field(default=20, ge=0, le=100000)
    manual_mode: Literal['MT5','PANEL'] = 'PANEL'
    filling_policy: Literal['AUTO','FOK','IOC','RETURN'] = 'AUTO'
    stoplimit_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    time_type: Literal['GTC','DAY','SPECIFIED','SPECIFIED_DAY'] = 'GTC'
    expiration: int = Field(default=0, ge=0)

    comment: str = "HMA Web Trade"

class ActionRequest(AccountBoundRequest):
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

@app.post('/api/terminal/activate')
def activate_native_terminal(req: ActionRequest):
    try:
        native_terminal.desktop_authorization()
    except ValueError:
        raise HTTPException(status_code=503,detail='Orijinal terminal sunucu bağlantısı yapılandırılmadı.') from None
    mt5_client.use_native_terminal_login()
    mt5_client.journal.event('info','settings','Original terminal enabled; broker login is managed by MT5')
    return {'success':True,'terminal_managed_login':True}

class CloseRequest(ActionRequest):
    ticket: int = Field(gt=0)

class LoginRequest(BaseModel):
    login: int = Field(gt=0)
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)
    account_type: Literal["DEMO", "REAL"]

# The HMA bot works exactly like HMA_Crossover_EA.mq5: features the EA does not have are always off.
EA_ONLY_BOT_SETTINGS = {
    'use_atr_filter': False, 'use_h4_filter': False, 'confirmation_bars': 0, 'single_position': True,
    'risk_mode': 'POINTS', 'lot_mode': 'FIXED', 'max_consecutive_losses': 0,
    'trade_hours_enabled': False, 'trade_days': [0, 1, 2, 3, 4, 5, 6], 'blocked_windows': [],
}
EA_ONLY_SIGNAL_RULES = {'use_rsi_filter': False, 'max_spread_points': 0, 'use_breakeven': False, 'trailing_stop_points': 0}


def ea_only(settings: Dict[str, Any]) -> Dict[str, Any]:
    result = {**settings, **EA_ONLY_BOT_SETTINGS}
    if result.get('second_ma_type') == 'KAMA':          # the EA's 2nd MA is HMA / EMA / SMA / LWMA
        result['second_ma_type'] = 'EMA'
    return result


class BotConfigRequest(BaseModel):
    symbol: Optional[str] = None
    # User decisions 2026-10-23 (not EA inputs): trend entry on start, lot/SL/TP from profitable manual trades.
    trend_entry_on_start: Optional[bool] = None
    reenter_after_stop: Optional[bool] = None
    stop_profit_amount: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    copy_lot_override: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    stop_loss_amount: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    entry_mode: Optional[Literal["CROSSOVER", "MANUAL_STYLE", "MEVO"]] = None
    auto_lot_limits_demo: Optional[bool] = None
    fast_ma_type: Optional[Literal["HMA", "EMA", "SMA", "LWMA"]] = None
    extra_timeframes: Optional[List[int]] = None
    copy_manual_trades: Optional[bool] = None
    copy_manual_since: Optional[str] = Field(default=None, pattern=r'^20\d\d-[01]\d-[0-3]\d$')
    symbols: Optional[Union[List[str], str]] = None
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=43200)
    hma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    second_ma_type: Optional[Literal["EMA", "SMA", "LWMA", "HMA", "KAMA"]] = None   # KAMA only in old saved files
    second_ma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    kama_fast: Optional[int] = Field(default=None, ge=2, le=200)
    kama_slow: Optional[int] = Field(default=None, ge=2, le=200)
    atr_period: Optional[int] = Field(default=None, ge=2, le=200)
    use_atr_filter: Optional[bool] = None
    use_h4_filter: Optional[bool] = None
    single_position: Optional[bool] = None
    er_min: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confirmation_bars: Optional[int] = Field(default=None, ge=0, le=3)
    min_distance_atr: Optional[float] = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    risk_mode: Optional[Literal["POINTS", "ATR"]] = None
    atr_stop_multiplier: Optional[float] = Field(default=None, gt=0, le=20, allow_inf_nan=False)
    atr_target_multiplier: Optional[float] = Field(default=None, ge=0, le=50, allow_inf_nan=False)
    lot_size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    use_stop_loss: Optional[bool] = None
    sl_points: Optional[int] = Field(default=None, ge=0)
    use_take_profit: Optional[bool] = None
    tp_points: Optional[int] = Field(default=None, ge=0)
    close_opposite: Optional[bool] = None
    lot_mode: Optional[Literal["FIXED", "RISK"]] = None
    risk_pct: Optional[float] = Field(default=None, ge=0.05, le=5, allow_inf_nan=False)
    max_consecutive_losses: Optional[int] = Field(default=None, ge=0, le=20)
    trade_hours_enabled: Optional[bool] = None
    trade_start: Optional[str] = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    trade_end: Optional[str] = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    trade_utc_offset_min: Optional[int] = Field(default=None, ge=-720, le=840)
    trade_days: Optional[List[int]] = Field(default=None, min_length=1, max_length=7)
    blocked_windows: Optional[List[str]] = Field(default=None, max_length=20)

class MevOOpportunityRequest(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=6)
    timeframe: Literal["M5", "M15", "M30", "H1", "H4"] = "M15"


class PendingRequest(OrderRequest):
    filling_policy: Literal['AUTO','FOK','IOC','RETURN','BOC'] = 'AUTO'
    pending_type: Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP", "BUY_STOP_LIMIT", "SELL_STOP_LIMIT"]
    entry_price: float = Field(gt=0)

class PreviewRequest(AccountBoundRequest):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    sl_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    tp_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    deviation_points: int = Field(default=20, ge=0, le=100000)
    manual_mode: Literal['MT5','PANEL'] = 'PANEL'
    filling_policy: Literal['AUTO','FOK','IOC','RETURN','BOC'] = 'AUTO'
    stoplimit_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    time_type: Literal['GTC','DAY','SPECIFIED','SPECIFIED_DAY'] = 'GTC'
    expiration: int = Field(default=0, ge=0)


    symbol: str = Field(pattern=r"^[A-Za-z0-9_.#-]+$", max_length=32)
    order_type: Literal["BUY", "SELL"]
    volume: float = Field(gt=0, allow_inf_nan=False)
    sl_points: int = Field(default=0, ge=0)
    tp_points: int = Field(default=0, ge=0)
    pending_type: Optional[Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP", "BUY_STOP_LIMIT", "SELL_STOP_LIMIT"]] = None
    entry_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

class UnlockRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")

class DailyLimitRequest(AccountBoundRequest):
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    acknowledge_risk: bool = False

class TradingLockRequest(AccountBoundRequest):
    locked: bool
    acknowledge_unlimited: bool = False

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
    # ATR risk mode of the bot: stop/target = ATR(atr_period) x multiplier at the signal candle.
    sl_atr: float = Field(default=0, ge=0, le=20, allow_inf_nan=False)
    tp_atr: float = Field(default=0, ge=0, le=50, allow_inf_nan=False)
    atr_period: int = Field(default=14, ge=2, le=200)

@app.post('/api/backtest')
def run_backtest(req: BacktestRequest):
    if req.timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    from app import signal_engine
    warmup = signal_engine.history_bars({'hma_period': req.hma_period, 'ema_period': req.second_ma_period,
                                         'ma_type': req.second_ma_type, 'atr_period': req.atr_period})
    rates = mt5_client.get_rates(req.symbol, req.timeframe_minutes, req.candles + warmup + 1)
    if not rates or len(rates) < 2:
        raise HTTPException(status_code=503, detail='Mum verisi alınamadı.')
    # Last candle is still forming; never use it as historical evidence.
    rates = rates[:-1]
    first = max(0, len(rates) - req.candles)   # test window: the requested candles after the warm-up
    price = mt5_client.get_symbol_price(req.symbol)
    info = mt5_client.get_symbol_spec(req.symbol)
    if not info or not info.get('point') or not price.get('bid'):
        raise HTTPException(status_code=503, detail='Broker sembol özellikleri doğrulanamadı.')
    try:
        return simulate(rates, req.hma_period, req.second_ma_type, req.second_ma_period,
                        req.sl_points, req.tp_points, float(info['point']), float(price.get('spread', 0)),
                        req.commission_points, start=first, sl_atr=req.sl_atr, tp_atr=req.tp_atr,
                        atr_period=req.atr_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.post('/api/backtest/filters')
def run_filter_compare(req: BacktestRequest):
    """Plain crossover vs. HTF-trend / RSI-guard filters on the broker's candles. Read-only; changes no bot rule."""
    if req.timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    from app import signal_engine, filter_compare
    warmup = signal_engine.history_bars({'hma_period': req.hma_period, 'ema_period': req.second_ma_period,
                                         'ma_type': req.second_ma_type, 'atr_period': req.atr_period})
    rates = mt5_client.get_rates(req.symbol, req.timeframe_minutes, req.candles + warmup + 1)
    if not rates or len(rates) < 2:
        raise HTTPException(status_code=503, detail='Mum verisi alınamadı.')
    rates = rates[:-1]
    start = max(0, len(rates) - req.candles)
    price = mt5_client.get_symbol_price(req.symbol)
    info = mt5_client.get_symbol_spec(req.symbol)
    if not info or not info.get('point') or not price.get('bid'):
        raise HTTPException(status_code=503, detail='Broker sembol özellikleri doğrulanamadı.')
    htf_minutes = signal_engine.htf_minutes_for(req.timeframe_minutes, 0)
    htf_rates = None
    if htf_minutes and htf_minutes in TIMEFRAME_NAMES:
        htf_rates = mt5_client.get_rates(req.symbol, htf_minutes, 400)
        htf_rates = htf_rates[:-1] if htf_rates and len(htf_rates) > 1 else None
    try:
        rows = filter_compare.compare(rates, htf_rates, htf_minutes, req.timeframe_minutes,
            hma_period=req.hma_period, ma_type=req.second_ma_type, ma_period=req.second_ma_period,
            sl_points=req.sl_points, tp_points=req.tp_points, point=float(info['point']),
            spread_points=float(price.get('spread', 0)), commission_points=req.commission_points, start=start,
            sl_atr=req.sl_atr, tp_atr=req.tp_atr, atr_period=req.atr_period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {'symbol': req.symbol, 'timeframe_minutes': req.timeframe_minutes, 'htf_minutes': htf_minutes,
            'htf_available': bool(htf_rates), 'tested_candles': len(rates) - start, 'variants': rows,
            'disclaimer': 'Geçmiş test sonucudur; gelecekteki kârı garanti etmez. Swap, kayma ve geçmiş spread dahil değildir.'}

_PRESET_CACHE: Dict[Any, Any] = {}


@app.get('/api/strategy-presets')
async def strategy_presets(symbol: str = Query(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$'),
                           timeframe_minutes: int = Query(default=15),
                           candles: int = Query(default=2000, ge=300, le=3000),
                           commission_points: float = Query(default=0, ge=0, le=1000, allow_inf_nan=False)):
    """Widely used MA crossovers tested on this broker's candles with the bot's exits (app.strategy_presets)."""
    if timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    from app import signal_engine, strategy_presets as sp

    def run():
        # Choosing a common crossover makes the bot use its own lot / SL / TP (Bot Ayarları), so the test uses them.
        sl = bot.sl_points if bot.use_stop_loss else 0
        tp = bot.tp_points if bot.use_take_profit else 0
        exit_source = 'bot'
        warmup = max(signal_engine.history_bars({'hma_period': p['fast'], 'fast_type': p['fast_type'],
                                                 'ema_period': p['slow'], 'ma_type': p['slow_type']}) for p in sp.PRESETS)
        rates = mt5_client.get_rates(symbol, timeframe_minutes, candles + warmup + 1)
        if not rates or len(rates) < 300:
            raise HTTPException(status_code=503, detail='MT5 mum verisi alınamadı.')
        rates = rates[:-1]                          # the forming candle is never evidence
        start = max(0, len(rates) - candles)
        info = mt5_client.get_symbol_spec(symbol) or {}
        price = mt5_client.get_symbol_price(symbol) or {}
        if not info.get('point'):
            raise HTTPException(status_code=503, detail='Broker sembol özellikleri doğrulanamadı.')
        rows = sp.run_presets(rates, start, float(info['point']), float(price.get('spread') or 0),
                              sl_points=int(min(sl, 10000)), tp_points=int(min(tp, 10000)),
                              commission_points=commission_points)
        first, last = rates[start]['time'], rates[-1]['time']
        return {'symbol': symbol, 'timeframe_minutes': timeframe_minutes, 'candles': len(rates) - start,
                'from_time': first, 'to_time': last, 'sl_points': sl, 'tp_points': tp, 'exit_source': exit_source,
                'spread_points': float(price.get('spread') or 0), 'commission_points': commission_points,
                'presets': rows, 'checked_at': int(time.time()),
                'disclaimer': 'Geçmiş test sonucudur; gelecekteki kârı garanti etmez. Swap ve kayma dahil değildir.'}

    key = (symbol, timeframe_minutes, candles, commission_points, bot.sl_points, bot.tp_points, bot.use_stop_loss,
           bot.use_take_profit)
    hit = _PRESET_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    result = await asyncio.to_thread(run)
    _PRESET_CACHE.clear()
    _PRESET_CACHE[key] = (time.monotonic(), result)
    return result


class ResearchRequest(BaseModel):
    symbol: str = Field(default='XAUUSD', pattern=r'^[A-Za-z0-9_.#-]+$', max_length=32)
    start: int = Field(ge=946684800)
    end: int = Field(ge=946684800)
    hma_period: int = Field(default=14, ge=2, le=200)
    kama_period: int = Field(default=10, ge=2, le=200)
    kama_fast: int = Field(default=2, ge=2, le=200)
    kama_slow: int = Field(default=30, ge=2, le=200)
    atr_period: int = Field(default=14, ge=2, le=200)
    er_min: float = Field(default=.30, ge=0, le=1, allow_inf_nan=False)
    stop_atr: float = Field(default=2, gt=0, le=20, allow_inf_nan=False)
    target_atr: float = Field(default=3, ge=0, le=50, allow_inf_nan=False)
    spread_points: float = Field(default=38, ge=0, le=100000, allow_inf_nan=False)
    historical_spread: bool = True
    commission_points: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    slippage_points: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    swap_long_points_per_day: float = Field(default=0, ge=-100000, le=100000, allow_inf_nan=False)
    swap_short_points_per_day: float = Field(default=0, ge=-100000, le=100000, allow_inf_nan=False)
    lot_size: float = Field(default=.01, gt=0, le=10000, allow_inf_nan=False)
    initial_capital: float = Field(default=10000, gt=0, le=1e12, allow_inf_nan=False)
    min_train_trades: int = Field(default=20, ge=1, le=1000)


research_guard = threading.Lock()


@app.get('/api/chart/candles')
async def chart_candles(symbol: str = Query(default='EURUSD', pattern=r'^[A-Za-z0-9_.#-]+$', max_length=32),
                        timeframe_minutes: int = Query(default=15),
                        count: int = Query(default=600, ge=2, le=5000)):
    if timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen grafik zaman dilimi.')
    rows = await asyncio.to_thread(mt5_client.get_rates, symbol, timeframe_minutes, count)
    if not rows:
        raise HTTPException(status_code=503, detail='MT5 mum verisi alınamadı; broker bağlantısını kontrol edin.')
    return {'symbol': symbol, 'timeframe_minutes': timeframe_minutes,
            'rates': rows, 'source': 'MT5', 'time_basis': 'broker',
            'clock_offset_seconds': mt5_client.tick_clock_offset}


def _calc_rsi(closes_chronological, period=14):
    if len(closes_chronological) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes_chronological[i] - closes_chronological[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes_chronological)):
        diff = closes_chronological[i] - closes_chronological[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(0.0, diff)) / period
        avg_loss = (avg_loss * (period - 1) + max(0.0, -diff)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))  # MT5 iRSI: unrounded


def _calc_atr(rates_chronological, period=14):
    if len(rates_chronological) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(rates_chronological)):
        r, prev = rates_chronological[i], rates_chronological[i - 1]
        tr = max(float(r['high']) - float(r['low']),
                 abs(float(r['high']) - float(prev['close'])),
                 abs(float(r['low']) - float(prev['close'])))
        trs.append(tr)
    return sum(trs[-period:]) / period if len(trs) >= period else 0.0


MA_LABELS = {'EMA': 'EMA', 'SMA': 'SMA', 'LWMA': 'LWMA', 'HMA': 'HMA'}


def _scan_symbols_sync(symbol_list, timeframe_minutes, hma_period, ema_period, ma_type='EMA', rsi_period=14,
                       atr_period=14, sl_atr=2.0, tp_atr=3.0, confirm_close=True, rsi_filter=False,
                       rsi_buy_max=70.0, rsi_sell_min=30.0, *, stats=False, stats_bars=500, **filters):
    """HMA(fast) x MA(slow) crossover scan with optional filters (app.signal_engine).
    With confirm_close only closed candles can create a signal (no repaint, same as the bot and the
    MT5 EA); otherwise the forming candle is also checked."""
    from app import signal_engine
    settings = {**signal_engine.DEFAULTS, **filters, 'hma_period': hma_period, 'ema_period': ema_period,
                'fast_type': filters.get('fast_type') or 'HMA',
                'ma_type': ma_type, 'rsi_period': rsi_period, 'atr_period': atr_period, 'sl_atr': sl_atr,
                'tp_atr': tp_atr, 'confirm_close': confirm_close, 'rsi_filter': rsi_filter,
                'rsi_buy_max': rsi_buy_max, 'rsi_sell_min': rsi_sell_min}
    need = signal_engine.required_bars(settings)
    # Recursive indicators (EMA, RSI, ADX) need their MT5 warm-up before the FIRST candle that is
    # evaluated, so the history test window comes on top of it.
    count = max(120, need + 30, signal_engine.history_bars(settings) + ((stats_bars + 2) if stats else 0))
    htf = signal_engine.htf_minutes_for(timeframe_minutes, settings['htf_minutes']) if settings['htf_filter'] else None
    wants_spread = bool(settings['max_spread_points'] or settings['min_rr'] or stats)
    items = []
    for sym in symbol_list:
        try:
            rates = mt5_client.get_rates(sym, timeframe_minutes, count)
            if not rates or len(rates) < need:
                continue
            htf_rates = None
            if htf:
                htf_count = max(signal_engine.history_bars(settings), need + 10) + (count * timeframe_minutes) // htf
                htf_rates = mt5_client.get_rates(sym, htf, htf_count) if htf in TIMEFRAME_NAMES else None
            spread = point = None
            if wants_spread:
                quote = mt5_client.get_symbol_price(sym) or {}
                if quote.get('point') and (quote.get('bid') or 0) > 0:
                    spread, point = quote.get('spread'), quote.get('point')
            item = signal_engine.analyze(rates, settings, timeframe_minutes, htf_rates=htf_rates,
                                         utc_offset=mt5_client.tick_clock_offset, spread_points=spread,
                                         point=point, stats=stats, stats_bars=stats_bars)
            if item:
                items.append({'symbol': sym, **item})
        except Exception:
            logger.exception('Signal scan failed for %s', sym)
            continue
    return items


_NEXT_OPEN_CACHE: Dict[str, Any] = {}
_MTF_CACHE: Dict[Any, Any] = {}


@app.get('/api/market/next-open')
async def market_next_open(symbol: str = Query(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')):
    """Estimated reopening of a closed market from the broker's own M5 history (server clock)."""
    from app import market_hours
    # The closure pattern changes rarely: read the M5 history at most every 10 minutes per symbol
    # (shared by all dashboards), so it never competes with orders for the MT5 connection.
    cached = _NEXT_OPEN_CACHE.get(symbol)
    if cached and time.monotonic() - cached[0] < 600:
        times = cached[1]
    else:
        rates = await asyncio.to_thread(mt5_client.get_rates, symbol, 5, 8000)
        if not rates:
            raise HTTPException(status_code=503, detail='Mum geçmişi alınamadı.')
        times = [r['time'] for r in rates]
        _NEXT_OPEN_CACHE[symbol] = (time.monotonic(), times)
    server_now = int(time.time()) + int(mt5_client.tick_clock_offset)
    result = market_hours.next_open(times, server_now)
    return {'symbol': symbol, 'server_now': server_now, 'clock_offset_seconds': mt5_client.tick_clock_offset, **result}


MTF_TIMEFRAMES = (5, 15, 60, 240)


@app.get('/api/signals/mtf')
async def signals_multi_timeframe(symbol: str = Query(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$'),
                                  timeframes: str = Query(default='5,15,60,240', max_length=40),
                                  hma_period: int = Query(default=14, ge=2, le=200),
                                  fast_type: Literal['HMA', 'EMA', 'SMA', 'LWMA'] = Query(default='HMA'),
                                  ema_period: int = Query(default=34, ge=2, le=200),
                                  ma_type: Literal['EMA', 'SMA', 'LWMA', 'HMA'] = Query(default='EMA'),
                                  confirm_close: bool = Query(default=True)):
    """Same HMA x MA rule on several timeframes of one symbol (no filters): trend and last cross per timeframe."""
    try:
        tfs = list(dict.fromkeys(int(x) for x in timeframes.split(',') if x.strip()))
    except ValueError:
        raise HTTPException(status_code=422, detail='Geçersiz zaman dilimi listesi.')
    if not tfs or len(tfs) > 6 or any(tf not in TIMEFRAME_NAMES for tf in tfs):
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    settings = dict(hma_period=hma_period, fast_type=fast_type, ema_period=ema_period, ma_type=ma_type, confirm_close=confirm_close)

    def run():
        rows = []
        for tf in tfs:
            items = _scan_symbols_sync([symbol], tf, **settings)
            item = items[0] if items else None
            rows.append({'timeframe_minutes': tf, 'available': bool(item),
                         **({k: item.get(k) for k in ('signal', 'side', 'bars_ago', 'cross_side', 'hma', 'ema',
                                                      'price', 'digits', 'candle_time')} if item else {})})
        return rows
    key = (symbol, tuple(tfs), fast_type, hma_period, ema_period, ma_type, confirm_close)
    cached = _MTF_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        rows = cached[1]
    else:
        rows = await asyncio.to_thread(run)
        _MTF_CACHE[key] = (time.monotonic(), rows)
        for stale in [k for k, v in _MTF_CACHE.items() if time.monotonic() - v[0] > 300]:
            _MTF_CACHE.pop(stale, None)
    sides = [r['side'] for r in rows if r.get('side') in ('BUY', 'SELL')]
    aligned = sides[0] if sides and len(sides) == len(rows) and len(set(sides)) == 1 else None
    return {'symbol': symbol, 'timeframes': rows, 'aligned': aligned,
            'buy': sides.count('BUY'), 'sell': sides.count('SELL')}


@app.get('/api/signals/scan')
async def scan_signals(symbols: str = Query(min_length=1, max_length=320),
                       timeframe_minutes: int = Query(default=15),
                       hma_period: int = Query(default=14, ge=2, le=200),
                       fast_type: Literal['HMA', 'EMA', 'SMA', 'LWMA'] = Query(default='HMA'),
                       ema_period: int = Query(default=34, ge=2, le=200),
                       ma_type: Literal['EMA', 'SMA', 'LWMA', 'HMA'] = Query(default='EMA'),
                       rsi_period: int = Query(default=14, ge=2, le=100),
                       atr_period: int = Query(default=14, ge=2, le=100),
                       sl_atr: float = Query(default=2.0, ge=0, le=20),
                       tp_atr: float = Query(default=3.0, ge=0, le=50),
                       confirm_close: bool = Query(default=True),
                       rsi_filter: bool = Query(default=False),
                       rsi_buy_max: float = Query(default=70.0, ge=50, le=100),
                       rsi_sell_min: float = Query(default=30.0, ge=0, le=50),
                       htf_filter: bool = Query(default=False),
                       htf_minutes: int = Query(default=0),
                       adx_filter: bool = Query(default=False),
                       adx_period: int = Query(default=14, ge=2, le=100),
                       adx_min: float = Query(default=20.0, ge=0, le=100),
                       max_spread_points: int = Query(default=0, ge=0, le=100000),
                       cooldown_bars: int = Query(default=0, ge=0, le=200),
                       min_rr: float = Query(default=0.0, ge=0, le=20),
                       volume_filter: bool = Query(default=False),
                       volume_mult: float = Query(default=1.0, ge=0.1, le=10),
                       sessions: str = Query(default='', max_length=40),
                       stats: bool = Query(default=False),
                       stats_bars: int = Query(default=500, ge=100, le=3000)):
    if timeframe_minutes not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    if htf_minutes and (htf_minutes not in TIMEFRAME_NAMES or htf_minutes <= timeframe_minutes):
        raise HTTPException(status_code=422, detail='Üst zaman dilimi, grafik zaman diliminden büyük olmalı.')
    from app.signal_engine import SESSIONS
    session_list = tuple(dict.fromkeys(x.strip().upper() for x in sessions.split(',') if x.strip()))
    if any(x not in SESSIONS for x in session_list):
        raise HTTPException(status_code=422, detail='Geçersiz seans; ASIA, LONDON, NEWYORK kullanın.')
    raw_list = [s.strip() for s in symbols.split(',') if s.strip()]
    symbol_list = []
    for s in raw_list:
        if re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', s) and s not in symbol_list:
            symbol_list.append(s)
        if len(symbol_list) >= 8:
            break
    if not symbol_list:
        raise HTTPException(status_code=422, detail='Taranacak sembol seçilmedi.')
    settings = dict(hma_period=hma_period, fast_type=fast_type, ema_period=ema_period, ma_type=ma_type, rsi_period=rsi_period,
                    atr_period=atr_period, sl_atr=sl_atr, tp_atr=tp_atr, confirm_close=confirm_close,
                    rsi_filter=rsi_filter, rsi_buy_max=rsi_buy_max, rsi_sell_min=rsi_sell_min,
                    htf_filter=htf_filter, htf_minutes=htf_minutes, adx_filter=adx_filter, adx_period=adx_period,
                    adx_min=adx_min, max_spread_points=max_spread_points, cooldown_bars=cooldown_bars,
                    min_rr=min_rr, volume_filter=volume_filter, volume_mult=volume_mult, sessions=list(session_list))
    items = await asyncio.to_thread(_scan_symbols_sync, symbol_list, timeframe_minutes,
                                    **{**settings, 'sessions': session_list}, stats=stats, stats_bars=stats_bars)
    return {'timeframe_minutes': timeframe_minutes, 'hma_period': hma_period, 'ema_period': ema_period,
            'settings': settings, 'signals': items, 'checked_at': int(time.time())}



manual_style_guard = threading.Lock()
_MANUAL_STYLE_CACHE: Dict[Any, Any] = {}
MANUAL_STYLE_TTL = 60


def _manual_style_sync(symbol, timeframe_minutes, days):
    from app import manual_style, signal_engine
    s = manual_style.DEFAULTS
    trades = mt5_client.get_manual_trades(days=days)
    warmup = signal_engine.history_bars(s) + manual_style.VOL_BARS + 10
    count = min(20000, days * 1440 // timeframe_minutes + warmup + s['horizon'] + 2)
    rates = mt5_client.get_rates(symbol, timeframe_minutes, count)
    if not rates or len(rates) < warmup + 10:
        raise HTTPException(status_code=503, detail='MT5 mum verisi alınamadı; broker bağlantısını kontrol edin.')
    htf = signal_engine.HTF_AUTO.get(timeframe_minutes)
    htf_rates = None
    if htf in TIMEFRAME_NAMES:
        htf_rates = mt5_client.get_rates(symbol, htf, count * timeframe_minutes // htf + warmup)
    span_days = count * timeframe_minutes // 1440 + 2
    h4_rates = mt5_client.get_rates(symbol, 240, span_days * 6 + warmup)
    d1_rates = mt5_client.get_rates(symbol, 1440, span_days + warmup)
    quote = mt5_client.get_symbol_price(symbol) or {}
    spread = float(quote.get('spread') or 0) * float(quote.get('point') or 0)
    result = manual_style.analyze(trades, rates, timeframe_minutes, symbol=symbol, htf_rates=htf_rates,
                                  utc_offset=mt5_client.tick_clock_offset, spread=spread,
                                  h4_rates=h4_rates, d1_rates=d1_rates)
    symbols = {}
    for t in trades:
        symbols[t['symbol']] = symbols.get(t['symbol'], 0) + 1
    result.update(days=days, manual_symbols=symbols, spread=spread, time_basis='broker',
                  checked_at=int(time.time()))
    return result


@app.get('/api/manual-style')
async def manual_style_signal(symbol: str = Query(default='XAUUSD', pattern=r'^[A-Za-z0-9_.#-]+$', max_length=32),
                              timeframe_minutes: int = Query(default=15),
                              days: int = Query(default=90, ge=7, le=365),
                              refresh: bool = Query(default=False)):
    """Signals learned from positions opened by hand in the original MT5 terminal (closed candles only)."""
    if timeframe_minutes not in (1, 5, 15, 30, 60, 240):
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi.')
    key = (mt5_client.login_id, mt5_client.server, symbol, timeframe_minutes, days)
    hit = _MANUAL_STYLE_CACHE.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < MANUAL_STYLE_TTL:
        return hit[1]
    if not manual_style_guard.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='Manuel stil analizi zaten çalışıyor; birkaç saniye sonra tekrar deneyin.')
    try:
        result = await asyncio.to_thread(_manual_style_sync, symbol, timeframe_minutes, days)
    finally:
        manual_style_guard.release()
    _MANUAL_STYLE_CACHE.clear() if len(_MANUAL_STYLE_CACHE) > 32 else None
    _MANUAL_STYLE_CACHE[key] = (time.monotonic(), result)
    return result


def bot_manual_style(symbol, timeframe_minutes, candle_time, days=90):
    """Manual-style result for the bot (same analysis and cache as the card); recomputed once its live signal
    is older than the bot's last closed candle."""
    key = (mt5_client.login_id, mt5_client.server, symbol, timeframe_minutes, days)
    hit = _MANUAL_STYLE_CACHE.get(key)
    if hit and ((hit[1].get('signal') or {}).get('candle_time') == candle_time or
                (hit[1].get('status') == 'NOT_ENOUGH' and time.monotonic() - hit[0] < MANUAL_STYLE_TTL)):
        return hit[1]
    if not manual_style_guard.acquire(timeout=120):
        raise RuntimeError('manuel stil analizi meşgul')
    try:
        result = _manual_style_sync(symbol, timeframe_minutes, days)
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc
    finally:
        manual_style_guard.release()
    _MANUAL_STYLE_CACHE[key] = (time.monotonic(), result)
    return result


bot.manual_style_provider = bot_manual_style
bot.mevo_provider = lambda symbol, minutes: mevo.signal_for(symbol, minutes)
mevo.bot_position_ids = lambda: [r['ticket'] for r in list(bot.signal_history)
                                 if r.get('entry_mode') == 'MEVO' and r.get('success') and r.get('ticket')]


@app.get('/api/research/history')
def research_history(symbol: str = Query(default='XAUUSD', pattern=r'^[A-Za-z0-9_.#-]+$', max_length=32),
                     timeframe_minutes: int = Query(default=60),
                     start: int = Query(ge=946684800), end: int = Query(ge=946684800)):
    if timeframe_minutes not in (60, 240):
        raise HTTPException(status_code=422, detail='Araştırma yalnız H1/H4 destekler.')
    if end <= start or end - start > 5 * 366 * 86400:
        raise HTTPException(status_code=422, detail='En fazla beş yıllık artan tarih aralığı gerekli.')
    if not research_guard.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='Başka bir araştırma çalışıyor; tamamlanmasını bekleyin.')
    try:
        rows = mt5_client.get_research_rates(symbol.upper(), timeframe_minutes, start, end)
        return {'symbol': symbol.upper(), 'timeframe_minutes': timeframe_minutes,
                'requested_start': start, 'requested_end': end, 'count': len(rows),
                'clock_offset_seconds': mt5_client.tick_clock_offset,
                'rates': rows}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail='MT5 araştırma geçmişi alınamadı.') from exc
    finally:
        research_guard.release()


@app.post('/api/research/compare')
def research_compare(req: ResearchRequest):
    if req.end <= req.start or req.end - req.start > 5 * 366 * 86400:
        raise HTTPException(status_code=422, detail='En fazla beş yıllık artan tarih aralığı gerekli.')
    if not research_guard.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='Başka bir araştırma çalışıyor; tamamlanmasını bekleyin.')
    try:
        fields = req.model_dump(exclude={'symbol', 'start', 'end'})
        # Validate before fetching broker history.
        settings = ResearchSettings(**fields)
        settings.validate()
        spec = mt5_client.get_symbol_spec(req.symbol.upper())
        if not spec or not spec.get('point'):
            raise HTTPException(status_code=503, detail='Broker point değeri okunamadı.')
        if not spec.get('linear_profit') or not spec.get('contract_size'):
            raise HTTPException(status_code=503, detail='Broker sözleşme büyüklüğü/kâr hesaplama türü doğrulanamadı.')
        if not spec.get('account_currency') or spec.get('profit_currency') != spec['account_currency']:
            raise HTTPException(status_code=422, detail='Parasal araştırma için sembol kâr para birimi hesap para birimiyle aynı olmalı; tarihsel kur dönüşümü desteklenmiyor.')
        minimum, maximum, step = (float(spec.get(k, 0)) for k in ('volume_min', 'volume_max', 'volume_step'))
        if step <= 0 or not minimum <= req.lot_size <= maximum or abs(req.lot_size / step - round(req.lot_size / step)) > 1e-7:
            raise HTTPException(status_code=422, detail=f'Lot broker sınırlarına uymuyor: min {minimum}, max {maximum}, adım {step}.')
        settings = ResearchSettings(**fields, point=float(spec['point']),
                                    contract_size=float(spec['contract_size']), currency=spec['account_currency'])
        settings.validate()
        h1 = mt5_client.get_research_rates(req.symbol.upper(), 60, req.start, req.end)
        h4 = mt5_client.get_research_rates(req.symbol.upper(), 240, req.start, req.end)
        prices = [float(r[k]) for rows in (h1, h4) for r in rows for k in ('low', 'high')]
        if not prices:
            raise ValueError('MT5 araştırma verisi alınamadı.')
        # Include execution spread/slippage in the sampled range, without future account balances.
        cushion = (max(float(r.get('spread', 0)) for r in h1+h4) + req.slippage_points + 1) * settings.point
        rules = mt5_client.get_research_margin_rules(req.symbol.upper(), req.lot_size,
                                                     max(settings.point, min(prices)-cushion), max(prices)+cushion)
        settings = ResearchSettings(**fields, point=float(spec['point']),
                                    contract_size=float(spec['contract_size']), currency=spec['account_currency'],
                                    broker_rules=rules)
        result = compare_research(h1, h4, settings)
        result.update(symbol=req.symbol.upper(), requested_start=req.start, requested_end=req.end,
                      clock_offset_seconds=mt5_client.tick_clock_offset)
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning('Research data unavailable: %s', type(exc).__name__)
        raise HTTPException(status_code=503, detail='MT5 araştırma verisi alınamadı.') from exc
    finally:
        research_guard.release()


research_export_guard = threading.Lock()


@app.post('/api/research/export')
async def research_export(request: Request):
    from app.research_excel import excel_report, MIME
    if not research_export_guard.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='Başka bir Excel raporu hazırlanıyor; tamamlanmasını bekleyin.')
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16 * 1024 * 1024:
                raise HTTPException(status_code=413, detail='Excel rapor isteği çok büyük.')
        report = json.loads(body)
        try:
            content = await asyncio.to_thread(excel_report, report)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(status_code=422, detail='Geçersiz veya eksik araştırma raporu: ' + str(exc)) from exc
        return Response(content, media_type=MIME,
                        headers={'Content-Disposition': 'attachment; filename="XAUUSD-HMA-KAMA-ATR-report.xlsx"'})
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail='Geçersiz rapor JSON verisi.') from exc
    finally:
        research_export_guard.release()


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
    with mt5_client._lock:
        account = [mt5_client.login_id, mt5_client.server, mt5_client.account_type]
        if account[2] == 'REAL':
            username = request.state.dashboard_user
            token = request.cookies.get(security_sessions.COOKIE)
            if not security_sessions.session_expires(token, username):
                raise HTTPException(status_code=403, detail='Gerçek hesap işlemi için ikinci doğrulama gerekli.')
        return account

def bound_request_account(req, authorized_account=None):
    account = authorized_account or [mt5_client.login_id, mt5_client.server, mt5_client.account_type]
    if req.expected_account is not None and list(req.expected_account) != account:
        raise HTTPException(status_code=409, detail='Seçilen hesap değişti; ekranı yenileyip işlemi yeniden değerlendirin.')
    return account

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
    response = JSONResponse({'unlocked': False})
    response.delete_cookie(security_sessions.COOKIE, path='/')
    return response

class StopsRequest(CloseRequest):
    manual_mode: Literal['MT5','PANEL'] = 'PANEL'
    sl: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    tp: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    expected_sl: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    expected_tp: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

class PendingModifyRequest(CloseRequest):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    entry_price: float = Field(gt=0, allow_inf_nan=False)
    sl_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    tp_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    stoplimit_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    time_type: Literal['GTC','DAY','SPECIFIED','SPECIFIED_DAY'] = 'GTC'
    expiration: int = Field(default=0, ge=0)
    manual_mode: Literal['MT5','PANEL'] = 'PANEL'
    expected_state: tuple[int,str,int,int,float,float,float,float,float,int,int]

class PartialCloseRequest(CloseRequest):
    volume: float = Field(gt=0, allow_inf_nan=False)
    deviation_points: int = Field(default=50, ge=0, le=100000)
    filling_policy: Literal['AUTO','FOK','IOC','RETURN'] = 'AUTO'

class PositionCloseRequest(CloseRequest):
    model_config = ConfigDict(extra='forbid')
    deviation_points: int = Field(default=50, ge=0, le=100000)
    filling_policy: Literal['AUTO','FOK','IOC','RETURN'] = 'AUTO'

class CloseByRequest(CloseRequest):
    model_config = ConfigDict(extra='forbid')
    position_by: int = Field(gt=0)

class TrailingRequest(CloseRequest):
    model_config = ConfigDict(extra='forbid')
    distance_points: int = Field(ge=0, le=10000000)


@app.get('/api/health')
def system_health():
    """Read-only health findings (app.health). Each data source is read separately so one failure never hides the rest."""
    from app import health
    ctx = {'now': time.time()}

    def grab(name, fn):
        try:
            ctx[name] = fn()
        except Exception:
            ctx.setdefault('unreadable', []).append(name)

    grab('connected', lambda: bool(mt5_client.is_connected))
    grab('account_type', lambda: mt5_client.account_type)
    grab('trading_halted', lambda: bool(mt5_client.journal.trading_halted()))
    grab('bot', lambda: bot.get_status())
    grab('limits', lambda: mt5_client.lot_limits())
    grab('positions', lambda: mt5_client.get_positions())

    def quote():
        symbol = (ctx.get('bot') or {}).get('symbol') or bot.symbol
        from app import mt5_bridge as bridge
        with mt5_client._lock:
            tick = mt5_client.mt5.symbol_info_tick(symbol) if mt5_client.is_connected and mt5_client.mt5 is not None else None
        result = bridge.quote_status(tick, mt5_client.tick_clock_offset, 10, True)
        return {'symbol': symbol, 'state': result.get('state'), 'age_seconds': result.get('age_seconds')}
    if ctx.get('connected'):
        grab('quote', quote)
    findings = health.evaluate(ctx)
    for name in (ctx.get('unreadable', []) if ctx.get('connected') else []):   # offline: the connection finding covers it
        findings.append({'id': 'unreadable:' + name, 'level': 'warn', 'title': f'Kontrol okunamadı: {name}',
                         'detail': 'Bu bilgi okunamadığı için ilgili kontroller yapılamadı.', 'hint': '', 'target': ''})
    return {'checked_at': int(time.time()), 'level': health.overall(findings), 'findings': findings}


_LOSS_PROFILE_CACHE = {}


def loss_profile_data(days):
    """Closed manual + bot trades of the selected account; cached 5 minutes (history changes only when a trade closes)."""
    from app import loss_profile
    scope = mt5_client._selected_account()
    key = (tuple(scope), days)
    hit = _LOSS_PROFILE_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    rows = [*mt5_client.get_manual_trades(days=days), *mt5_client.get_bot_trades(days=days)]
    profile = {**loss_profile.build(rows), 'days': days}
    _LOSS_PROFILE_CACHE.clear()
    _LOSS_PROFILE_CACHE[key] = (time.monotonic(), profile)
    return profile


@app.get('/api/loss-profile')
def loss_profile_report(days: int = Query(default=90, ge=7, le=365)):
    try:
        return loss_profile_data(days)
    except MT5DataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get('/api/loss-profile/check')
def loss_profile_check(symbol: str = Query(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$'),
                       side: Literal['BUY', 'SELL'] = 'BUY', volume: float = Query(gt=0, le=100000),
                       entry_price: float = Query(default=0, ge=0), sl_price: float = Query(default=0, ge=0),
                       sl_points: int = Query(default=0, ge=0, le=10000000), days: int = Query(default=90, ge=7, le=365)):
    from app import loss_profile
    try:
        profile = loss_profile_data(days)
    except MT5DataError:
        return {'warnings': [], 'available': False}
    if sl_points and not sl_price:
        # Stop given as points: distance from the live quote (advisory only; an unreadable quote just skips the stop check).
        try:
            quote = mt5_client.get_symbol_price(symbol) or {}
            point = float((mt5_client.get_symbol_spec(symbol) or {}).get('point') or 0)
            entry_price = entry_price or float(quote.get('ask' if side == 'BUY' else 'bid') or 0)
            if point > 0 and entry_price > 0:
                sl_price = entry_price - sl_points * point if side == 'BUY' else entry_price + sl_points * point
        except Exception:
            pass
    return {'available': True, 'trades': profile['trades'], 'days': days,
            'warnings': loss_profile.check(profile, symbol, side, volume, entry_price or None, sl_price or None)}


class PositionRuleRequest(CloseRequest):
    model_config = ConfigDict(extra='forbid')
    profit_kind: Literal['percent', 'amount'] = 'amount'
    profit: float = Field(default=0, ge=0, le=1e9)
    loss_kind: Literal['percent', 'amount'] = 'amount'
    loss: float = Field(default=0, ge=0, le=1e9)


def trade_response(result):
    if result.get("success"):
        return result
    return JSONResponse(status_code=409 if result.get("conflict") else 400,
                        content={**result, "detail": result.get("error", "İşlem tamamlanamadı.")})


@app.get("/api/live")
async def live(symbol: str = Query(default="EURUSD", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.#-]+$")):
    try:
        queue = live_feed.subscribe(symbol)
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
                                      req.sl_points, req.pending_type, req.entry_price, tp_points=req.tp_points,
                                      expected_account=bound_request_account(req), sl_price=req.sl_price,
                                      tp_price=req.tp_price, deviation_points=req.deviation_points,
                                      stoplimit_price=req.stoplimit_price, time_type=req.time_type, expiration=req.expiration,
                                      manual_mode=req.manual_mode, filling_policy=req.filling_policy)
    return trade_response(result)


@app.post("/api/order/pending")
def place_pending(req: PendingRequest, account=Depends(require_real_unlock)):
    return trade_response(mt5_client.open_order(req.symbol, req.order_type, req.volume,
        sl_points=req.sl_points, tp_points=req.tp_points, comment="Panel pending", request_id=req.request_id,
        pending_type=req.pending_type, entry_price=req.entry_price,
        sl_price=req.sl_price, tp_price=req.tp_price, deviation_points=req.deviation_points,
        stoplimit_price=req.stoplimit_price, time_type=req.time_type, expiration=req.expiration,
        manual_mode=req.manual_mode, filling_policy=req.filling_policy,
        expected_account=bound_request_account(req, account)))


@app.post('/api/order/modify')
def modify_pending(req: PendingModifyRequest, account=Depends(require_real_unlock)):
    return trade_response(mt5_client.modify_pending(req.ticket,
        req.model_dump(exclude={'ticket','request_id','expected_account'},exclude_none=True),
        'pending-modify-'+req.request_id, expected_account=bound_request_account(req,account)))


@app.post("/api/order/cancel")
def cancel_pending(req: CloseRequest):
    return trade_response(mt5_client.cancel_pending(req.ticket, "cancel-" + req.request_id,
        expected_account=bound_request_account(req)))


@app.post("/api/position/stops")
def update_stops(req: StopsRequest, account=Depends(require_real_unlock)):
    if req.sl is None and req.tp is None:
        raise HTTPException(status_code=422, detail="En az bir SL/TP fiyatı gerekli.")
    return trade_response(mt5_client.manage_position(req.ticket, "stops",
        req.model_dump(exclude={"ticket", "request_id", "expected_account"}, exclude_none=True), "stops-" + req.request_id,
        expected_account=bound_request_account(req, account)))


@app.post("/api/position/partial-close")
def partial_close(req: PartialCloseRequest):
    values = {'volume':req.volume}
    if req.deviation_points != 50 or req.filling_policy != 'AUTO':
        values.update(deviation_points=req.deviation_points,filling_policy=req.filling_policy)
    return trade_response(mt5_client.manage_position(req.ticket, "partial", values, "partial-" + req.request_id,
        expected_account=bound_request_account(req)))

@app.post('/api/position/close-by')
def close_by(req: CloseByRequest):
    return trade_response(mt5_client.close_opposite(req.ticket,req.position_by,'close-by-'+req.request_id,
                                                   expected_account=bound_request_account(req)))

@app.get('/api/position/trailing')
def trailing_status():
    return trailing_stops.status()

@app.get('/api/position-rules')
def position_rules_status():
    return position_rules.status()

@app.post('/api/position-rules')
def configure_position_rule(req: PositionRuleRequest, account=Depends(require_real_unlock)):
    with mt5_client._lock.order():
        scope = bound_request_account(req,account)
        def configure():
            try:
                return position_rules.configure(req.ticket,req.profit_kind,req.profit,req.loss_kind,req.loss,scope)
            except (ValueError, MT5DataError) as exc:
                return {'success':False,'error':str(exc)}
        result = mt5_client.journal.run('rule-config-'+req.request_id,
            {'ticket':req.ticket,'p':[req.profit_kind,req.profit],'l':[req.loss_kind,req.loss],'account':scope},configure)
        if result.get('replayed') and (req.profit or req.loss) and req.ticket not in position_rules.rules:
            result.update(success=False,enabled=False,error='Uygulama yeniden başladı veya pozisyon değişti; hedefi yeni taleple kurun.')
        return trade_response(result)

@app.get('/api/position/{ticket}/capabilities')
def position_capabilities(ticket: int = Path(gt=0)):
    with mt5_client._lock:
        if not mt5_client.ensure_connected():
            raise HTTPException(status_code=503,detail='MT5 bağlı değil.')
        scope = mt5_client._bound_account(None)
        try:
            return mt5_client._bridge('position_capabilities',ticket,*scope)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

@app.get('/api/market/depth')
def market_depth(symbol: str = Query(min_length=1,max_length=32,pattern=r'^[A-Za-z0-9_.#-]+$')):
    with mt5_client._lock:
        if not mt5_client.ensure_connected():
            raise HTTPException(status_code=503,detail='MT5 bağlı değil.')
        scope = mt5_client._bound_account(None)
        try:
            return mt5_client._bridge('market_depth',symbol,*scope)
        except Exception as exc:
            raise HTTPException(status_code=503,detail='Broker derinlik verisi doğrulanamadı.') from exc

@app.post('/api/position/trailing')
def configure_trailing(req: TrailingRequest, account=Depends(require_real_unlock)):
    with mt5_client._lock.order():
        scope = bound_request_account(req,account)
        def configure():
            try:
                return trailing_stops.configure(req.ticket,req.distance_points,scope)
            except (ValueError, MT5DataError) as exc:
                return {'success':False,'error':str(exc)}
        result = mt5_client.journal.run('trailing-config-'+req.request_id,
            {'ticket':req.ticket,'points':req.distance_points,'account':scope},configure)
        if result.get('replayed') and req.distance_points and req.ticket not in trailing_stops.configs:
            result.update(success=False,enabled=False,error='Uygulama yeniden başladı veya pozisyon değişti; iz süren stopu yeni taleple etkinleştirin.')
        return trade_response(result)


# API Endpoints
@app.get("/api/operations/latency")
def operation_latency():
    return {**mt5_client.journal.latency(), 'live_snapshot': live_feed.stats()}

@app.get('/api/operations/events')
def operation_events(after: int = Query(default=0, ge=0)):
    return mt5_client.journal.events(after=after)

@app.get('/api/risk/status')
def account_risk_status():
    return mt5_client.get_risk_status()

class LotLimitsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_order_lots: Optional[float] = Field(default=None, ge=0.01, le=100, allow_inf_nan=False)
    max_total_lots: Optional[float] = Field(default=None, ge=0.01, le=1000, allow_inf_nan=False)
    max_open_orders: Optional[int] = Field(default=None, ge=1, le=200)


@app.get('/api/risk/lot-limits')
def get_lot_limits():
    return {**mt5_client.lot_limits(), 'defaults': dict(mt5_client.lot_limits_defaults)}


@app.post('/api/risk/lot-limits')
def change_lot_limits(req: LotLimitsRequest, account=Depends(require_real_unlock)):
    """Admin only (role middleware); real accounts need the TOTP session like other risk changes."""
    values = req.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status_code=422, detail='Değiştirilecek sınır yok.')
    try:
        return mt5_client.set_lot_limits(values)
    except MT5DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post('/api/risk/daily-limit')
def change_daily_loss_limit(req: DailyLimitRequest, account=Depends(require_real_unlock)):
    try:
        return mt5_client.set_daily_loss_limit(req.amount, req.acknowledge_risk,
            expected_account=bound_request_account(req, account))
    except MT5DataError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get("/api/operations/status")
def operation_status(request_id: str = Query(min_length=1, max_length=250)):
    return mt5_client.journal.status(request_id)

@app.get("/api/trading/status")
def trading_status():
    return {"new_orders_halted": mt5_client.journal.trading_halted(),
            "daily_limit_enabled": mt5_client.journal.daily_limit_enabled(mt5_client.login_id, mt5_client.server),
            "flatten_active": flatten_active.is_set()}

@app.post('/api/trading/lock')
def set_trading_lock(req: TradingLockRequest, account=Depends(require_real_unlock)):
    with mt5_client._lock.order():
        scope = bound_request_account(req, account)
        if scope != [mt5_client.login_id, mt5_client.server, mt5_client.account_type]:
            raise HTTPException(status_code=409, detail='Seçilen hesap değişti; işlem kilidi değiştirilmedi.')
        if not req.locked:
            if not req.acknowledge_unlimited:
                raise HTTPException(status_code=409, detail='Günlük zarar korumasını kapatmak için açık onay gerekli.')
            if flatten_active.is_set():
                raise HTTPException(status_code=409, detail='Toplu kapatma sürüyor; bitmesini bekleyin.')
            if not mt5_client.ensure_connected():
                raise HTTPException(status_code=503, detail='MT5 bağlantısı doğrulanamadı.')
            try:
                login, server = mt5_client._bound_account(scope)
                mt5_client.get_risk_status()
            except MT5DataError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            login, server = mt5_client.login_id, mt5_client.server
        mt5_client.journal.set_trading_mode(login, server, req.locked)
        if req.locked:
            mt5_client.automation_stopped.set()
            bot.stop()
        return {"new_orders_halted": req.locked, "daily_limit_enabled": req.locked}

@app.post("/api/trading/resume")
def resume_new_orders(account=Depends(require_real_unlock)):
    with mt5_client._lock.order():
        if flatten_active.is_set():
            raise HTTPException(status_code=409, detail="Toplu kapatma sürüyor; bitmesini bekleyin.")
        if not mt5_client.ensure_connected():
            raise HTTPException(status_code=503, detail="MT5 bağlantısı doğrulanamadı.")
        try:
            mt5_client._bound_account(account)
        except MT5DataError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        try:
            risk = mt5_client.get_risk_status()
        except MT5DataError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if risk['daily_limit_enabled'] and risk['daily_loss'] >= risk['daily_loss_limit']:
            raise HTTPException(status_code=409, detail='Günlük zarar limiti aşıldı. Yeni emirlere devam etmeden önce limiti gözden geçirin.')
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
    res = mt5_client.login(req.login, req.password, req.server, req.account_type)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Login failed"))
    mt5_client.journal.event('info', 'account', 'Broker account connected')
    return res

@app.get("/api/positions")
def get_positions():
    return mt5_client.get_positions(include_commission=True)

@app.get("/api/history")
def get_history(days: int = Query(default=30, ge=1, le=365)):
    return mt5_client.get_history(days=days)

@app.get("/api/reports")
def get_reports(days: int = Query(default=30, ge=1, le=365),
                period: Optional[str] = Query(default=None, pattern="^(day|week|month|year)$"),
                limit: int = Query(default=12, ge=1, le=90)):
    """Without `period`: the existing day/symbol report. With `period`: calendar period table
    (broker server day, week starts Monday)."""
    if period:
        return mt5_client.get_period_report(period, limit)
    return mt5_client.get_reports(days=days)

@app.get("/api/history/position/{position_id}")
def get_position_details(position_id: int):
    if position_id <= 0:
        raise HTTPException(status_code=422, detail="Geçersiz pozisyon numarası.")
    details = mt5_client.get_position_details(position_id)
    if not details['deals']:
        raise HTTPException(status_code=404, detail="Bu pozisyonun broker geçmişi bulunamadı.")
    return details

@app.get('/api/symbols')
def get_symbols(refresh: bool = Query(default=False)):
    return mt5_client.get_symbols(refresh=refresh)


@app.get("/api/price/{symbol}")
def get_price(symbol: str = Path(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')):
    return mt5_client.get_symbol_price(symbol)

@app.post("/api/order/open")
def open_order(req: OrderRequest, account=Depends(require_real_unlock)):
    res = mt5_client.open_order(
        symbol=req.symbol,
        order_type=req.order_type.upper(),
        volume=req.volume,
        sl_points=req.sl_points,
        tp_points=req.tp_points,
        comment=req.comment, request_id=req.request_id,
        sl_price=req.sl_price, tp_price=req.tp_price, deviation_points=req.deviation_points,
        stoplimit_price=req.stoplimit_price, time_type=req.time_type, expiration=req.expiration,
        manual_mode=req.manual_mode, filling_policy=req.filling_policy,
        expected_account=bound_request_account(req, account)
    )
    if not res.get("success"):
        return JSONResponse(status_code=409 if res.get("conflict") else 400,
                            content={**res, "detail": res.get("error", "Order failed")})
    return res

@app.post("/api/order/close")
def close_order(req: PositionCloseRequest):
    queued = time.perf_counter()
    with mt5_client._lock.order():
        queue_ms = round((time.perf_counter() - queued) * 1000, 1)
        account = bound_request_account(req)
        payload = {'ticket':req.ticket,'account':account}
        options = {}
        if req.deviation_points != 50 or req.filling_policy != 'AUTO':
            options = {'deviation_points':req.deviation_points,'filling_policy':req.filling_policy}
            payload.update(options)
        res = mt5_client.journal.run('close-'+req.request_id,payload,
                                     lambda:mt5_client.close_position(req.ticket,**options), queue_ms=queue_ms)
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
    account = bound_request_account(req)
    if not flatten_guard.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Toplu kapatma zaten sürüyor.")
    flatten_active.set()
    try:
        # Persist before waiting for MT5; queued manual and automated opens must stop too.
        mt5_client.journal.set_trading_halted(True)
        mt5_client.journal.event('warning', 'trading', 'Emergency close-all started')
        mt5_client.automation_stopped.set()
        bot.stop()
        with mt5_client._lock.order():
            if bound_request_account(req) != account:
                raise HTTPException(status_code=409, detail='Toplu kapatma beklerken hesap değişti; talep gönderilmedi.')
            return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "all", "cancel_pending": True, 'account': account},
                                         lambda: flatten_account(req.request_id))
    finally:
        flatten_active.clear()
        flatten_guard.release()

@app.post("/api/order/close-profit")
def close_profit_orders(req: ActionRequest):
    with mt5_client._lock.order():
        account = bound_request_account(req)
        return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "profit", 'account': account},
                                     lambda: mt5_client.close_by_filter("profit"))

@app.post("/api/order/close-loss")
def close_loss_orders(req: ActionRequest):
    with mt5_client._lock.order():
        account = bound_request_account(req)
        return mt5_client.journal.run("bulk-" + req.request_id, {"filter": "loss", 'account': account},
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
    # Candle times are MT5 server time; the UI shows them on the server clock, like MT5.
    return {**bot.get_status(), 'clock_offset_seconds': mt5_client.tick_clock_offset}

@app.post("/api/bot/toggle")
async def toggle_bot(request: Request):
    async with bot_toggle_guard:
        if bot.is_running:
            bot.stop()
        else:
            if bot.task is not None and not bot.task.done():
                raise HTTPException(status_code=409, detail='Önceki bot döngüsü bitiyor; tamamlanmasını bekleyin.')
            account = require_real_unlock(request)
            # Never shrink the lot silently: a fixed lot above the automation cap is refused with a reason.
            if bot.lot_mode != 'RISK' and bot.lot_size > mt5_client.max_order_lots + 1e-9:
                raise HTTPException(status_code=409, detail=(
                    f'Bot emir lotu {bot.lot_size:g}, otomatik bot için tek emir sınırı {mt5_client.max_order_lots:g} lot. '
                    'Lotu düşürün ya da Bot Ayarları > Lot ve risk bölümünden sınırı artırın. '
                    '"Şimdi AL/SAT" ile elle açılan işlemlerde bu sınır uygulanmaz.'))
            config = bot.config_snapshot()
            ready = await asyncio.to_thread(mt5_client.bot_readiness, config)
            if not ready.get('success'):
                raise HTTPException(status_code=409, detail=ready.get('error', 'Bot işlem yapmaya hazır değil.'))
            # The preflight runs in a worker; account/lock/unlock can change while it runs.
            with mt5_client._lock:
                if require_real_unlock(request) != account or ready['account'] != account:
                    raise HTTPException(status_code=409, detail='Hazırlık kontrolü sırasında hesap değişti; bot başlatılmadı.')
                if mt5_client.journal.trading_halted():
                    raise HTTPException(status_code=409, detail='Yeni emirler durduruldu; bot başlatılmadı.')
                try:
                    bot.start(expected_config=config)
                except RuntimeError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                bot.set_start_warnings(ready.get('warnings') or [])
            # Outside the MT5 lock: reading the manual history takes that lock in a worker thread.
            bot.set_start_notes(await manual_copy_warnings())
        return bot.get_status()

@app.post("/api/bot/config")
def update_bot_config(req: BotConfigRequest):
    if bot.is_running or (bot.task is not None and not bot.task.done()):
        raise HTTPException(status_code=409, detail="Ayarları değiştirmeden önce botu durdurun ve mevcut döngünün bitmesini bekleyin.")
    data = req.model_dump(exclude_unset=True, exclude_none=True)
    if data.get('second_ma_type') == 'KAMA':
        raise HTTPException(status_code=422, detail='MT5 EA 2. gösterge türü HMA, EMA, SMA veya LWMA olmalı.')
    data = ea_only(data)
    if data.get("timeframe_minutes", bot.timeframe_minutes) not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    if data.get("kama_fast", bot.kama_fast) >= data.get("kama_slow", bot.kama_slow):
        raise HTTPException(status_code=422, detail="KAMA hızlı periyodu yavaş periyottan küçük olmalı.")
    if data.get('use_h4_filter', bot.use_h4_filter) and (
        data.get('timeframe_minutes',bot.timeframe_minutes) != 60 or
        data.get('second_ma_type',bot.second_ma_type) != 'KAMA' or
        not data.get('use_atr_filter',bot.use_atr_filter)):
        raise HTTPException(status_code=422, detail='H4 filtresi H1, KAMA ve kesişim onayı gerektirir.')
    try:
        bot.update_config(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='Bot ayarları kaydedilemedi; mevcut ayarlar korundu.') from exc
    return bot.get_status()


def restore_bot_settings():
    if not bot.config_path.exists():
        return
    try:
        stored = BotConfigRequest(**json.loads(bot.config_path.read_text())).model_dump(exclude_none=True)
        if stored.get('timeframe_minutes',15) not in TIMEFRAME_NAMES or stored.get('kama_fast',2) >= stored.get('kama_slow',30):
            raise ValueError('Invalid saved settings')
        if stored.get('use_h4_filter') and (stored.get('timeframe_minutes') != 60 or stored.get('second_ma_type') != 'KAMA' or not stored.get('use_atr_filter')):
            raise ValueError('Invalid saved H4 filter')
        stored = ea_only(stored)
        bot.load_config(stored)
        bot.update_signal_rules(dict(EA_ONLY_SIGNAL_RULES))
    except Exception:
        logger.error('Saved bot settings could not be loaded; bot remains stopped with defaults.')


restore_bot_settings()


@app.get("/api/bot/live-signal")
async def get_bot_live_signal():
    return await asyncio.to_thread(bot.live_preview)


class BotSignalRulesRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    execution_mode: Optional[Literal["AUTO_TRADE", "SIGNAL_ONLY"]] = None
    use_rsi_filter: Optional[bool] = None
    rsi_buy_max: Optional[float] = Field(default=None, ge=50, le=95, allow_inf_nan=False)
    rsi_sell_min: Optional[float] = Field(default=None, ge=5, le=50, allow_inf_nan=False)
    max_spread_points: Optional[int] = Field(default=None, ge=0, le=100000)
    use_breakeven: Optional[bool] = None
    trailing_stop_points: Optional[int] = Field(default=None, ge=0, le=100000)


@app.post("/api/bot/signal-rules")
def update_bot_signal_rules(req: BotSignalRulesRequest, request: Request):
    data = {**req.model_dump(exclude_none=True), **EA_ONLY_SIGNAL_RULES}   # no EA equivalent: always off
    # Switching a running bot from signal-only to live orders needs a fresh real-account unlock.
    if data.get('execution_mode') == 'AUTO_TRADE' and bot.is_running and bot.execution_mode != 'AUTO_TRADE':
        require_real_unlock(request)
    try:
        return bot.update_signal_rules(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class BotSymbolActionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')

async def manual_copy_warnings():
    """Start note: which lot / SL / TP the bot copies from the profitable manual MT5 positions."""
    if not bot.copy_manual_trades:
        return []
    try:
        prof = await asyncio.to_thread(mt5_client.manual_copy_profile, bot.symbol, bot.copy_manual_since)
    except Exception as exc:
        return [f'Manuel işlem kopyası okunamadı ({exc}); bot kendi lot/SL/TP ayarını kullanır.']
    if prof.get('available') and bot.copy_lot_override > 0:
        prof = {**prof, 'manual_lot': prof['lot'], 'lot': float(bot.copy_lot_override)}
    bot.manual_copy = dict(prof)
    if not prof.get('available'):
        return [f"Manuel kopya kullanılamıyor: {prof.get('reason')} Bot kendi lot/SL/TP ayarını kullanır."]
    cap = mt5_client.max_order_lots
    if bot.auto_lot_limits_demo and mt5_client.account_type == 'DEMO':
        n = len(bot.instance_timeframes())
        capped = (f' (demo: bot emirleri için tam lot açılır; kayıtlı ayarlar değişmedi, gerçek hesapta uygulanmaz)') if cap and prof['lot'] > cap else ''
        mt5_client.bot_lot_override = (prof['lot'], prof['lot'] * n + mt5_client.max_total_lots)
    else:
        capped = f' (otomatik bot sınırı {cap:g} lot; sınırda açılır)' if cap and prof['lot'] > cap else ''
    return [f"Manuel kopya: {prof['since']} sonrası {prof['count']} kârlı {bot.symbol} pozisyonu → {prof['lot']:g} lot{capped}, "
            f"SL {str(prof['sl_points']) + ' p' if prof.get('sl_points') else f'bot ayarı ({bot.sl_points} p)' if bot.use_stop_loss else 'yok'}, "
            f"TP {str(prof['tp_points']) + ' p' if prof.get('tp_points') else f'bot ayarı ({bot.tp_points} p)' if bot.use_take_profit else 'yok'}."]


async def follow_selection(data: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """The bot trades what the panel shows: selected symbol, chart timeframe and the scanner's HMA × MA.
    A running bot changes like an MT5 EA re-initialised on a chart/input change; the new settings pass the
    same readiness checks (and real-account second factor) as a bot start, otherwise the bot keeps running
    on its current settings."""
    data = {k: v for k, v in data.items() if v is not None}
    if 'symbol' in data:
        data['symbol'] = data['symbol'].strip()
        data['symbols'] = [data['symbol']]
    if data.get('timeframe_minutes', bot.timeframe_minutes) not in TIMEFRAME_NAMES:
        raise HTTPException(status_code=422, detail='Desteklenmeyen zaman dilimi')
    try:
        async with bot_toggle_guard:
            if bot.is_running:
                old_config = bot.config_snapshot()
                config = bot.validated_config(data)
                if all(config.get(k) == old_config.get(k) for k in bot.LIVE_KEYS):
                    return bot.get_status()
                account = require_real_unlock(request)
                ready = await asyncio.to_thread(mt5_client.bot_readiness, config)
                if not ready.get('success'):
                    tf = str(TIMEFRAME_NAMES.get(config['timeframe_minutes'], config['timeframe_minutes'])).replace('TIMEFRAME_', '')
                    raise HTTPException(status_code=409, detail=(
                        f"{config['symbol']} {tf} için bot hazır değil: {ready.get('error', 'hazırlık kontrolü geçmedi')} "
                        f"Bot {old_config['symbol']} üzerinde mevcut ayarlarla çalışmaya devam ediyor."))
                with mt5_client._lock:
                    if require_real_unlock(request) != account or ready.get('account') != account:
                        raise HTTPException(status_code=409, detail='Hazırlık kontrolü sırasında hesap değişti; bot ayarları değiştirilmedi.')
                await asyncio.to_thread(bot.reinit_live, data, old_config)
                bot.set_start_warnings(ready.get('warnings') or [])
                bot.set_start_notes(await manual_copy_warnings())
            elif bot.task is not None and not bot.task.done():
                raise HTTPException(status_code=409, detail='Önceki bot döngüsü bitiyor; tamamlanmasını bekleyin.')
            else:
                bot.update_config(data)
        return bot.get_status()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='Bot ayarları kaydedilemedi; mevcut ayarlar korundu.') from exc


class BotFollowRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: Optional[str] = Field(default=None, min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=43200)
    hma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    second_ma_type: Optional[Literal["EMA", "SMA", "LWMA", "HMA"]] = None
    second_ma_period: Optional[int] = Field(default=None, ge=2, le=1000)
    fast_ma_type: Optional[Literal["HMA", "EMA", "SMA", "LWMA"]] = None
    entry_mode: Optional[Literal["CROSSOVER", "MANUAL_STYLE", "MEVO"]] = None
    copy_manual_trades: Optional[bool] = None
    # More EA instances on other timeframes (MT5: the same EA attached to more charts of the symbol).
    extra_timeframes: Optional[List[Literal[1, 5, 15, 30, 60, 240, 1440]]] = Field(default=None, max_length=6)


@app.post("/api/bot/follow")
async def follow_bot_selection(req: BotFollowRequest, request: Request):
    return await follow_selection(req.model_dump(exclude_none=True), request)


@app.post("/api/bot/add-symbol")
async def add_bot_symbol(req: BotSymbolActionRequest, request: Request):
    status = await follow_selection({'symbol': req.symbol}, request)
    return {"success": True, "symbols": status['symbols'], "symbol": status['symbol'], "is_running": status['is_running']}

@app.post("/api/bot/remove-symbol")
def remove_bot_symbol(req: BotSymbolActionRequest):
    try:
        symbols = bot.remove_symbol(req.symbol)
        return {"success": True, "symbols": symbols, "symbol": bot.symbol}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='Bot sembol listesi kaydedilemedi.') from exc

class BotExecuteSignalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_.#-]+$')
    side: Literal["BUY", "SELL"]
    # Stable per-signal key: repeated clicks on the same signal map to one journaled order.
    signal_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:#-]+$')
    price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    sl_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    tp_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    sl_points: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    tp_points: Optional[int] = Field(default=None, ge=0, le=10_000_000)

@app.post("/api/bot/execute-signal")
def execute_bot_signal(req: BotExecuteSignalRequest, account=Depends(require_real_unlock)):
    try:
        res = bot.execute_signal(req.symbol, req.side, signal_id=req.signal_id, expected_account=account,
                                 price=req.price, sl_price=req.sl_price, tp_price=req.tp_price,
                                 sl_points=req.sl_points, tp_points=req.tp_points)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return trade_response(res)

@app.get("/api/bot/performance")
def bot_performance_report(days: int = Query(default=30, ge=1, le=365)):
    """Closed HMA bot positions (automatic + confirmed signals): observed results, not a forecast."""
    from app import bot_performance
    try:
        deals = mt5_client.get_history(days=days)
        open_ids = [p.get('identifier', p.get('ticket')) for p in mt5_client.get_positions(fresh=True)]
    except Exception as exc:
        raise HTTPException(status_code=503, detail='MT5 işlem geçmişi okunamadı.') from exc
    rows = bot_performance.closed_positions(deals, bot.signal_history, open_ids,
                                            clock_offset=mt5_client.tick_clock_offset)
    return {'days': days, **bot_performance.summarize(rows, bot.trade_utc_offset_min)}


class BotHistoryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


@app.post("/api/bot/signal-history/delete")
def delete_bot_signal_history_entry(req: BotHistoryDeleteRequest):
    if not bot.delete_history_entry(req.id):
        raise HTTPException(status_code=404, detail='Kayıt bulunamadı; liste yenilendi.')
    return {"success": True, "signal_history": bot.signal_history}


@app.post("/api/bot/signal-history/clear")
def clear_bot_signal_history():
    bot.clear_history()
    return {"success": True, "signal_history": []}


@app.post("/api/bot/clear-pending-signal")
def clear_bot_pending_signal(data: Optional[Dict[str, Any]] = None):
    try:
        sym = (data or {}).get("symbol")
        bot.clear_pending_signal(symbol=sym)
        return {"success": True, "pending_signal_actions": bot.pending_signal_actions}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# MevO: manuel MT5 işlemlerinden öğrenir, fırsat sunar; emri kullanıcı sayfadan ya da bot MevO modunda açar
@app.get("/api/mevo/review")
def mevo_review():
    """MevO'nun açtırdığı kapalı işlemlerin kâr/zarar sebepleri ve alınan önlemler (broker geçmişi, kod hesabı)."""
    data = mevo.review(force=True)
    if data is None:
        raise HTTPException(status_code=503, detail="MevO işlem geçmişi okunamadı.")
    return data


@app.get("/api/mevo/status")
def mevo_status():
    return mevo.status()

class MevOKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)

@app.post("/api/mevo/key")
def mevo_key(req: MevOKeyRequest):
    res = mevo.set_key(req.api_key)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Anahtar kaydedilemedi"))
    return mevo.status()

@app.post("/api/mevo/learn")
def mevo_learn():
    res = mevo.learn()
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Öğrenme başarısız"))
    return res

@app.post("/api/mevo/opportunities")
def mevo_opportunities(req: MevOOpportunityRequest):
    minutes = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}[req.timeframe]
    res = mevo.opportunities(req.symbols, minutes)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Fırsat üretilemedi"))
    return res

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
