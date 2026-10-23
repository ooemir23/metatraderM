import asyncio
import logging
import math
import time
import threading
from typing import Dict, Any, List, Optional
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.mt5_bridge import HMA_MAGIC, MANUAL_MAGIC

# HMA_Crossover_EA InpDeviation: maximum slippage of the EA's own orders and closes (points).
EA_DEVIATION = 10
from app.mt5_client import MT5DataError, TIMEFRAME_NAMES
from app.strategy_research import Settings as IndicatorSettings, indicators
from app import signal_engine

logger = logging.getLogger("StrategyBot")
logger.setLevel(logging.INFO)

BOT_TIMEFRAMES = (1, 5, 15, 30, 60, 240, 1440)
MANUAL_STYLE_TIMEFRAMES = (1, 5, 15, 30, 60, 240)
MEVO_TIMEFRAMES = (5, 15, 30, 60, 240)      # MevO her kapanan mumda DeepSeek'e sorar: M1 token maliyeti nedeniyle yok
MEVO_MIN_CONFIDENCE = 65
MEVO_COOLDOWN_CANDLES = 3
_TF_CODES = {1: 'M1', 5: 'M5', 15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', 1440: 'D1'}
_TF_FROM_CODE = {v: k for k, v in _TF_CODES.items()}
_COMMENT_TF = re.compile(r'^HMA Bot (M1|M5|M15|M30|H1|H4|D1)\b')


def tf_code(minutes):
    return _TF_CODES.get(int(minutes), f'M{int(minutes)}')


class StrategyBot:
    def __init__(self, mt5_client, config_path=None):
        self.mt5 = mt5_client
        self.is_running = False
        self.task = None
        self._stop_event = threading.Event()
        self._check_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.runtime_state = 'STOPPED'
        self.runtime_detail = 'Bot durduruldu.'
        self.last_error = None

        # Bot Parameters (Defaults matching HMA_Crossover_EA)
        self.symbol = "EURUSD"
        self.symbols: List[str] = ["EURUSD"]
        self.timeframe_minutes = 15
        self.hma_period = 14
        self.fast_ma_type = "HMA"  # HMA (MT5 EA) or EMA / SMA / LWMA (user-approved presets, 2026-10-23)
        self.second_ma_type = "EMA" # EMA, SMA, LWMA, HMA
        self.second_ma_period = 34
        self.kama_fast = 2
        self.kama_slow = 30
        self.atr_period = 14
        self.use_atr_filter = False
        self.use_h4_filter = False
        self.single_position = True
        self.er_min = .30
        self.confirmation_bars = 0
        self.min_distance_atr = .05
        self.risk_mode = "POINTS"
        self.atr_stop_multiplier = 2.0
        self.atr_target_multiplier = 3.0
        self.current_atr = 0.0
        self.current_er = 0.0
        self._candidate = None
        self._candidates: Dict[str, Any] = {}
        self._trend_pending: Dict[str, bool] = {}
        self.lot_size = 0.01
        self.use_stop_loss = True
        self.sl_points = 200
        self.use_take_profit = False
        self.tp_points = 400
        self.close_opposite = True
        # User decisions (2026-10-23), not HMA_Crossover_EA behaviour:
        # - on start / settings change open the current trend side at once (the EA waits for a new crossover);
        # - lot and SL/TP come from the profitable hand-opened MT5 positions since copy_manual_since.
        self.trend_entry_on_start = True
        # After the bot's own position is closed by SL / TP / stop out, open the side the strategy still shows on
        # the next closed candle (user decision 2026-10-23; the EA would wait for a new crossover).
        self.reenter_after_stop = True
        self.copy_lot_override = 0.0     # manuel kopya açıkken lot yerine bu sabit lot (0 = manuel işlemlerden gelen lot)
        self.stop_profit_amount = 0.0    # bot bu çalıştırmada bu kadar kâra ulaşınca durur (hesap para birimi; 0 = kapalı)
        self.stop_loss_amount = 0.0      # bot bu çalıştırmada bu kadar zarara ulaşınca durur (0 = kapalı)
        self.session_pnl = None
        self._target_check_at = 0.0
        self._session_realized = 0.0
        self._reentry_for: Dict[str, int] = {}
        # More EA instances on other timeframes of the same symbol (each opens and closes only its own position).
        self.extra_timeframes: List[int] = []
        self.copy_manual_trades = True
        self.copy_manual_since = "2026-10-08"
        # DEMO only: open the full copied lot (the lot limits are raised for the bot's own orders, not saved).
        self.auto_lot_limits_demo = True
        # Entry rule: CROSSOVER (HMA_Crossover_EA) or MANUAL_STYLE (signals learned from the hand-opened MT5
        # trades, app.manual_style; the "Manuel işlemlerim" choice in the Strategies dialog, 2026-10-23).
        self.entry_mode = "CROSSOVER"
        self.manual_style_provider = None      # set by app.main: (symbol, tf) -> manual_style.analyze result
        self._ms_prev: Dict[str, Optional[str]] = {}
        # MEVO entry (user decision 2026-10-23): MevO (app.mevo, DeepSeek) opportunities opened by the bot.
        self.mevo_provider = None              # set by app.main: (symbol, tf_minutes) -> opportunity dict or None
        self._mevo_levels: Dict[str, Dict[str, float]] = {}
        self._mevo_last: Dict[str, int] = {}
        self.manual_copy: Optional[Dict[str, Any]] = None
        # Position sizing: FIXED = lot_size; RISK = risk_pct of equity lost at the stop.
        self.lot_mode = "FIXED"
        self.risk_pct = 0.5
        # Protection rules (automatic orders only).
        self.max_consecutive_losses = 0          # 0 = off
        self.trade_hours_enabled = False
        self.trade_start = "00:00"
        self.trade_end = "23:59"
        self.trade_utc_offset_min = 180          # Türkiye (UTC+3)
        self.trade_days = [0, 1, 2, 3, 4, 5, 6]  # Monday = 0
        self.blocked_windows: List[str] = []     # "HH:MM-HH:MM" daily or "YYYY-MM-DD HH:MM-HH:MM"
        self.started_at = 0
        self.start_warnings: List[str] = []
        self.start_notes: List[str] = []      # information at start (manual copy values), not problems
        self._run_nonce = 0
        # Per-symbol "what is the bot waiting for / why no trade" (shown in the bot card).
        self.symbol_status: Dict[str, Dict[str, Any]] = {}
        self._loss_check_at = 0.0

        # State tracking
        self.last_candle_time = 0
        self.last_candle_times: Dict[str, int] = {}
        self.last_signal = "YOK"
        self.last_signal_time = ""
        self.last_signal_price = 0.0
        self.current_hma = 0.0
        self.current_ma2 = 0.0
        self.current_rsi = 50.0
        self.execution_mode = "AUTO_TRADE"  # AUTO_TRADE or SIGNAL_ONLY
        self.use_rsi_filter = False
        self.rsi_buy_max = 70.0
        self.rsi_sell_min = 30.0
        self.max_spread_points = 0
        self.use_breakeven = False
        self.trailing_stop_points = 0
        self.pending_signal_action: Optional[Dict[str, Any]] = None
        self.pending_signal_actions: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.config_path = Path(config_path) if config_path else None
        # Signals sent to the bot (manual "Şimdi AL/SAT"/"Uygula" and automatic orders), newest first.
        self.history_path = self.config_path.with_name('bot_signal_history.json') if self.config_path else None
        self.signal_history: List[Dict[str, Any]] = self._load_history()

    def get_signal_rules(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "execution_mode": self.execution_mode,
                "use_rsi_filter": self.use_rsi_filter,
                "rsi_buy_max": self.rsi_buy_max,
                "rsi_sell_min": self.rsi_sell_min,
                "max_spread_points": self.max_spread_points,
                "use_breakeven": self.use_breakeven,
                "trailing_stop_points": self.trailing_stop_points,
                "current_rsi": round(self.current_rsi, 1),
                "pending_signal_action": self.pending_signal_action,
                "pending_signal_actions": getattr(self, "pending_signal_actions", []),
                "symbols": getattr(self, "symbols", [self.symbol]),
            }

    def update_signal_rules(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._state_lock:
            mode = str(data.get("execution_mode", self.execution_mode)).upper()
            if mode not in ("AUTO_TRADE", "SIGNAL_ONLY"):
                raise ValueError("Geçersiz çalışma modu.")
            self.execution_mode = mode
            if "use_rsi_filter" in data:
                self.use_rsi_filter = bool(data["use_rsi_filter"])
            if "rsi_buy_max" in data:
                val = float(data["rsi_buy_max"])
                if 50 <= val <= 95:
                    self.rsi_buy_max = val
            if "rsi_sell_min" in data:
                val = float(data["rsi_sell_min"])
                if 5 <= val <= 50:
                    self.rsi_sell_min = val
            if "max_spread_points" in data:
                val = int(data["max_spread_points"])
                if 0 <= val <= 100000:
                    self.max_spread_points = val
            if "use_breakeven" in data:
                self.use_breakeven = bool(data["use_breakeven"])
            if "trailing_stop_points" in data:
                val = int(data["trailing_stop_points"])
                if 0 <= val <= 100000:
                    self.trailing_stop_points = val
        self.log(f"Sinyal kuralları güncellendi: Mod={self.execution_mode} | RSI Filtre={'Açık' if self.use_rsi_filter else 'Kapalı'} | Başabaş={'Açık' if self.use_breakeven else 'Kapalı'} | İz Süren={self.trailing_stop_points}p")
        return self.get_signal_rules()

    @staticmethod
    def _parse_hhmm(value):
        if not isinstance(value, str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value.strip()):
            raise ValueError(f'Geçersiz saat: {value!r} (SS:DD)')
        hours, minutes = value.strip().split(':')
        return int(hours) * 60 + int(minutes)

    @classmethod
    def _parse_window(cls, text):
        """'HH:MM-HH:MM' (every day) or 'YYYY-MM-DD HH:MM-HH:MM' (that date) -> (date|None, start, end)."""
        text = str(text).strip()
        match = re.fullmatch(r'(?:(\d{4}-\d{2}-\d{2})\s+)?(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})', text)
        if not match:
            raise ValueError(f'Geçersiz engelli aralık: {text!r}. Örnek: 15:25-15:45 veya 2026-11-06 15:25-15:45')
        day = match.group(1)
        if day:
            try:
                datetime.strptime(day, '%Y-%m-%d')
            except ValueError as exc:
                raise ValueError(f'Geçersiz tarih: {day}') from exc
        return day, cls._parse_hhmm(match.group(2)), cls._parse_hhmm(match.group(3))

    def _validate_protection(self, c):
        if c['lot_mode'] not in ('FIXED', 'RISK'):
            raise ValueError('Lot modu FIXED veya RISK olmalı.')
        if isinstance(c['risk_pct'], bool) or not isinstance(c['risk_pct'], (int, float)) or not math.isfinite(c['risk_pct']) or not 0.05 <= c['risk_pct'] <= 5:
            raise ValueError('Risk yüzdesi 0.05 ile 5 arasında olmalı.')
        if c['lot_mode'] == 'RISK' and not c['use_stop_loss']:
            raise ValueError('Risk yüzdesiyle lot hesabı için Stop Loss açık olmalı.')
        if isinstance(c['max_consecutive_losses'], bool) or not isinstance(c['max_consecutive_losses'], int) or not 0 <= c['max_consecutive_losses'] <= 20:
            raise ValueError('Art arda kayıp sınırı 0 ile 20 arasında olmalı.')
        if not isinstance(c['trade_hours_enabled'], bool):
            raise ValueError('Geçersiz işlem saatleri ayarı.')
        self._parse_hhmm(c['trade_start']); self._parse_hhmm(c['trade_end'])
        if isinstance(c['trade_utc_offset_min'], bool) or not isinstance(c['trade_utc_offset_min'], int) or not -720 <= c['trade_utc_offset_min'] <= 840:
            raise ValueError('Saat dilimi farkı -720 ile 840 dakika arasında olmalı.')
        days = c['trade_days']
        if not isinstance(days, list) or not days or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
            raise ValueError('En az bir işlem günü seçilmeli.')
        c['trade_days'] = sorted(set(days))
        windows = c['blocked_windows']
        if not isinstance(windows, list) or len(windows) > 20:
            raise ValueError('En fazla 20 engelli aralık girilebilir.')
        c['blocked_windows'] = [str(w).strip() for w in windows if str(w).strip()]
        for window in c['blocked_windows']:
            self._parse_window(window)

    def trading_window_issue(self, now=None):
        """Reason string when automatic entries are not allowed right now, else None."""
        with self._state_lock:
            offset, enabled = self.trade_utc_offset_min, self.trade_hours_enabled
            start, end, days, windows = self.trade_start, self.trade_end, list(self.trade_days), list(self.blocked_windows)
        local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(minutes=offset)))
        minute = local.hour * 60 + local.minute
        names = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
        if local.weekday() not in days:
            return f'{names[local.weekday()]} işlem günü değil.'
        if enabled:
            a, b = self._parse_hhmm(start), self._parse_hhmm(end)
            inside = a <= minute <= b if a <= b else (minute >= a or minute <= b)
            if not inside:
                return f'İşlem saatleri dışında ({start}–{end}).'
        today = local.strftime('%Y-%m-%d')
        for window in windows:
            day, a, b = self._parse_window(window)
            if day and day != today:
                continue
            inside = a <= minute <= b if a <= b else (minute >= a or minute <= b)
            if inside:
                return f'Engelli zaman aralığında ({window}).'
        return None

    def order_lot(self, symbol, side, sl_points, expected_account=None, cap=None):
        """(lot, risk_money, error). RISK mode sizes from the stop; FIXED uses lot_size."""
        with self._state_lock:
            mode, pct, fixed = self.lot_mode, self.risk_pct, self.lot_size
        if mode != 'RISK':
            return fixed, None, None
        kwargs = {'expected_account': expected_account} if expected_account is not None else {}
        result = self.mt5.risk_lot(symbol, side, sl_points, pct, **kwargs)
        if not result.get('success'):
            return None, None, result.get('error') or 'Risk lotu hesaplanamadı.'
        lot, risk = float(result['lot']), result.get('risk_money')
        if cap is not None and math.isfinite(cap) and lot > cap:
            ratio = cap / lot
            lot, risk = cap, (round(risk * ratio, 2) if isinstance(risk, (int, float)) else None)
        return lot, risk, None

    def check_loss_streak(self, force=False):
        """Stop the bot after N consecutive losing positions since it was started."""
        with self._state_lock:
            limit, started, running = self.max_consecutive_losses, self.started_at, self.is_running
        if not limit or not running or not started:
            return None
        now = time.monotonic()
        if not force and now - self._loss_check_at < 30:
            return None
        self._loss_check_at = now
        try:
            from app import bot_performance
            deals = self.mt5.get_history(days=7)
            open_ids = [p.get('identifier', p.get('ticket')) for p in self.mt5.get_positions(fresh=True)]
            offset = getattr(self.mt5, 'tick_clock_offset', 0)
            offset = offset if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 0
            # Deal times are broker server time, started_at is UTC: compare on one clock.
            rows = bot_performance.closed_positions(deals, self.signal_history, open_ids, clock_offset=offset)
            streak = bot_performance.loss_streak(rows, since=started)
        except Exception as exc:
            self.log(f'Art arda kayıp kontrolü yapılamadı: {exc}', 'ERROR')
            return None
        if streak >= limit:
            self.log(f'🛑 Art arda {streak} kayıp (sınır {limit}); bot durduruldu. Piyasayı değerlendirip yeniden başlatın.', 'ERROR')
            self.stop()
            with self._state_lock:
                self.runtime_state, self.runtime_detail = 'LOSS_STREAK', f'Art arda {streak} kayıp; bot durduruldu.'
        return streak

    def check_session_target(self, force=False):
        """Stop the bot when this run's net result (closed bot positions since the start + floating P/L of the
        bot's open positions) reaches the profit target or the loss limit. App-only safety/automation feature
        (not in HMA_Crossover_EA). Open positions are left as they are: SL/TP keep working, like a stopped EA."""
        with self._state_lock:
            profit_t, loss_t, started, running = self.stop_profit_amount, self.stop_loss_amount, self.started_at, self.is_running
        if not running or not started or not (profit_t or loss_t):
            return None
        now = time.monotonic()
        try:
            if force or now - self._target_check_at >= 5:       # closed positions: history read every 5 s
                self._target_check_at = now
                from app import bot_performance
                days = min(90, int((time.time() - started) // 86400) + 2)
                deals = self.mt5.get_history(days=days)
                open_rows = self.mt5.get_positions(fresh=True)
                offset = getattr(self.mt5, 'tick_clock_offset', 0)
                offset = offset if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 0
                rows = bot_performance.closed_positions(
                    deals, self.signal_history, [p.get('identifier', p.get('ticket')) for p in open_rows], clock_offset=offset)
                self._session_realized = round(sum(r['net'] for r in rows if r['closed_at'] >= started), 2)
            floating = sum(float(p.get('profit') or 0) + float(p.get('swap') or 0) + float(p.get('commission') or 0)
                           for p in self.mt5.get_positions() if int(p.get('magic') or 0) == HMA_MAGIC)
        except Exception as exc:
            self.log(f'Kâr/zarar hedefi kontrol edilemedi: {exc}', 'ERROR')
            return None
        total = round(self._session_realized + floating, 2)
        self.session_pnl = total
        hit = None
        if profit_t and total >= profit_t:
            hit = ('PROFIT_TARGET', f'🎯 Kâr hedefi doldu: bu çalıştırmada {total} (hedef {profit_t}); bot durduruldu. Açık pozisyonlar SL/TP ile sürer.')
        elif loss_t and total <= -loss_t:
            hit = ('LOSS_LIMIT', f'🛑 Zarar sınırı doldu: bu çalıştırmada {total} (sınır -{loss_t}); bot durduruldu. Açık pozisyonlar SL/TP ile sürer.')
        if hit:
            self.log(hit[1], 'ERROR' if hit[0] == 'LOSS_LIMIT' else 'INFO')
            self.stop()
            with self._state_lock:
                self.runtime_state, self.runtime_detail = hit[0], hit[1].split(' ', 1)[1]
        return total

    def load_config(self, data):
        self.update_config(data, persist=False)

    def config_snapshot(self):
        keys = ('symbol','timeframe_minutes','hma_period','fast_ma_type','second_ma_type','second_ma_period',
                'kama_fast','kama_slow','atr_period','use_atr_filter','use_h4_filter','single_position',
                'er_min','confirmation_bars','min_distance_atr','risk_mode','atr_stop_multiplier',
                'atr_target_multiplier','lot_size','use_stop_loss','sl_points','use_take_profit','tp_points','close_opposite',
                'lot_mode','risk_pct','max_consecutive_losses','trade_hours_enabled','trade_start','trade_end',
                'trade_utc_offset_min','trade_days','blocked_windows','trend_entry_on_start','copy_manual_trades','copy_manual_since','extra_timeframes','auto_lot_limits_demo','entry_mode','reenter_after_stop','stop_profit_amount','stop_loss_amount','copy_lot_override')
        with self._state_lock:
            snap = {key: (list(getattr(self, key)) if isinstance(getattr(self, key), list) else getattr(self, key)) for key in keys}
            snap['symbols'] = list(getattr(self, 'symbols', [self.symbol]))
            return snap

    def persist_config(self, data):
        if not self.config_path:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.bot-settings-',dir=self.config_path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, allow_nan=False)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.config_path)
        finally:
            if os.path.exists(name): os.unlink(name)

    HISTORY_LIMIT = 50

    def _load_history(self) -> List[Dict[str, Any]]:
        try:
            if self.history_path and self.history_path.exists():
                rows = json.loads(self.history_path.read_text())
                if isinstance(rows, list):
                    rows = [r for r in rows if isinstance(r, dict)][:self.HISTORY_LIMIT]
                    for r in rows:   # older records had no id; one is needed to delete a single row
                        r.setdefault('id', uuid.uuid4().hex)
                    return rows
        except Exception:
            logger.error('Bot signal history could not be read; starting empty.')
        return []

    def _persist_history(self):
        if not self.history_path:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(prefix='.bot-history-', dir=self.history_path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(self.signal_history, stream, allow_nan=False)
                os.replace(name, self.history_path)
            finally:
                if os.path.exists(name): os.unlink(name)
        except Exception:
            logger.error('Bot signal history could not be saved.')

    def record_signal(self, *, source, symbol, side, lot, result, sl_points=0, tp_points=0, signal_id=None, risk_money=None):
        def number(value):
            try:
                value = float(value)
                return value if math.isfinite(value) else None
            except (TypeError, ValueError):
                return None
        entry = {
            'id': uuid.uuid4().hex, 'time': int(time.time()), 'source': source, 'symbol': symbol, 'side': side, 'lot': number(lot),
            'sl_points': int(sl_points or 0), 'tp_points': int(tp_points or 0), 'signal_id': signal_id,
            'success': bool(result.get('success')), 'ticket': result.get('ticket'), 'price': number(result.get('price')),
            'pending': bool(result.get('pending') or result.get('uncertain')),
            'risk_money': number(risk_money),
            # Where the trade came from (shown in the position details): entry mode + the MA pair it was opened with.
            'entry_mode': self.entry_mode if source == 'AUTO' else None,
            'strategy': f"{self.fast_ma_type} {self.hma_period} × {self.second_ma_type} {self.second_ma_period}" if source == 'AUTO' else None,
            'error': None if result.get('success') else str(result.get('error') or 'Emir gönderilemedi.')[:240],
        }
        with self._state_lock:
            self.signal_history.insert(0, entry)
            del self.signal_history[self.HISTORY_LIMIT:]
            self._persist_history()
        return entry

    def delete_history_entry(self, entry_id: str) -> bool:
        """Remove one row from the sent-signal list (the MT5 position itself is not touched)."""
        with self._state_lock:
            before = len(self.signal_history)
            self.signal_history = [r for r in self.signal_history if r.get('id') != entry_id]
            if len(self.signal_history) == before:
                return False
            self._persist_history()
            return True

    def clear_history(self):
        with self._state_lock:
            self.signal_history = []
            self._persist_history()

    def log(self, message: str, level: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message, "level": level}
        self.logs.insert(0, entry)
        if len(self.logs) > 50:
            self.logs.pop()
        logger.info(f"[{level}] {message}")

    def add_symbol(self, sym: str) -> List[str]:
        """Bot trades exactly one symbol: the selected one (like an MT5 EA on its chart)."""
        with self._state_lock:
            if self.is_running or self._check_lock.locked() or (self.task is not None and not self.task.done()):
                raise RuntimeError('Bot sembolünü değiştirmeden önce botu durdurun.')
            sym = sym.strip()
            if not re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', sym):
                raise ValueError("Geçersiz sembol.")
            if self.symbol != sym or list(getattr(self, 'symbols', [])) != [sym]:
                self.symbol, self.symbols = sym, [sym]
                self.last_candle_time, self.last_candle_times = 0, {}
                self.log(f"🎯 Bot sembolü: {sym}")
                self.persist_config(self.config_snapshot())
            return [sym]

    # Settings a running bot may change: the chart it is attached to and the EA crossover inputs.
    LIVE_KEYS = ('symbol', 'symbols', 'timeframe_minutes', 'entry_mode', 'copy_manual_trades', 'fast_ma_type', 'hma_period', 'second_ma_type', 'second_ma_period', 'extra_timeframes')

    def reinit_live(self, data: Dict[str, Any], expected_config=None, timeout: float = 30.0) -> Dict[str, Any]:
        """Running bot follows the panel selection, like an MT5 EA: a chart symbol/period change
        (REASON_CHARTCHANGE) or an input change (REASON_PARAMETERS) runs OnDeinit + OnInit. Positions stay
        open, and a crossover on a candle that closed before the change is not traded (OnInit sets
        m_lastBarTime to the current bar)."""
        if not isinstance(data, dict) or not data or set(data) - set(self.LIVE_KEYS):
            raise ValueError('Çalışan botta yalnız sembol, zaman dilimi ve HMA/2. MA değiştirilebilir.')
        # Wait for the current check (it may be sending an order) so the change never lands mid-order.
        if not self._check_lock.acquire(timeout=timeout):
            raise RuntimeError('Bot döngüsü meşgul (emir gönderiliyor olabilir); ayar değiştirilmedi.')
        try:
            with self._state_lock:
                if not self.is_running:
                    raise RuntimeError('Bot çalışmıyor.')
                if expected_config is not None and self.config_snapshot() != expected_config:
                    raise RuntimeError('Hazırlık kontrolü sırasında bot ayarları değişti; ayar değiştirilmedi.')
                before = self.config_snapshot()
                candidate = self.validated_config(data)
                changed = [k for k in self.LIVE_KEYS if candidate.get(k) != before.get(k)]
                if not changed:
                    return candidate
                self.persist_config(candidate)        # OSError: nothing changed in memory
                for key, value in candidate.items():
                    setattr(self, key, value)
                if changed == ['copy_manual_trades']:
                    # Only the order size source changes (manual copy or the bot's own lot / SL / TP); the
                    # running instances and their candle markers stay as they are.
                    if not self.copy_manual_trades:
                        self.manual_copy = None
                        if getattr(self.mt5, 'bot_lot_override', None) is not None:
                            self.mt5.bot_lot_override = None
                    self.log('📏 Lot ve SL/TP kaynağı: ' + ('kârlı manuel işlemler.' if self.copy_manual_trades else
                             f'bot ayarı ({self.lot_size:g} lot, SL {self.sl_points if self.use_stop_loss else "yok"}, TP {self.tp_points if self.use_take_profit else "yok"}).'))
                    return candidate
                if changed == ['extra_timeframes']:
                    # Attaching / removing EA instances on other charts leaves the running ones untouched;
                    # a new instance starts with OnInit on its own timeframe.
                    added = sorted(set(self.extra_timeframes) - set(before['extra_timeframes']))
                    removed = sorted(set(before['extra_timeframes']) - set(self.extra_timeframes))
                    for tf in removed:
                        for sym in self.symbols:
                            k = self._instance_key(sym, tf)
                            for store in (self.last_candle_times, self._trend_pending, self._candidates, self.symbol_status):
                                store.pop(k, None)
                    self.log(f"🧩 Bot zaman dilimleri: {', '.join(tf_code(t) for t in self.instance_timeframes())}"
                             + (f" (eklendi: {', '.join(tf_code(t) for t in added)})" if added else '')
                             + (f" (çıkarıldı: {', '.join(tf_code(t) for t in removed)}; açık pozisyonları olduğu gibi kalır)" if removed else ''))
                    return candidate
                self.last_candle_time, self.last_candle_times = 0, {}
                self._candidate, self._candidates = None, {}
                self._trend_pending = {}
                self._ms_prev = {}
                self._mevo_levels, self._mevo_last = {}, {}
                self._run_nonce = time.time_ns()
                self._reentry_for = {}
                self.symbol_status = {}
                self.current_hma = self.current_ma2 = 0.0
                self.current_atr = self.current_er = 0.0
                if before['symbol'] != self.symbol:
                    self.last_signal, self.last_signal_time, self.last_signal_price = "YOK", "", 0.0
                self.runtime_state, self.runtime_detail, self.last_error = 'WAITING_DATA', f'{self.symbol}: Yeni kapanmış mum verisi bekleniyor.', None
                tf = TIMEFRAME_NAMES.get(self.timeframe_minutes, f'M{self.timeframe_minutes}').replace('TIMEFRAME_', '')
                note = (f"{before['symbol']} pozisyonları olduğu gibi kalır; " if before['symbol'] != self.symbol else 'Açık pozisyonlar olduğu gibi kalır; ')
                self.log(f"🎯 Bot seçimi izledi (çalışırken): {self.symbol} {tf} · {self.fast_ma_type}({self.hma_period}) × "
                         f"{self.second_ma_type}({self.second_ma_period}). {note}bir sonraki kapanmış mumdaki kesişim bekleniyor.")
                return candidate
        finally:
            self._check_lock.release()

    def switch_symbol_live(self, sym: str, expected_config=None, timeout: float = 30.0) -> List[str]:
        sym = sym.strip()
        if not re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', sym):
            raise ValueError("Geçersiz sembol.")
        return list(self.reinit_live({'symbol': sym, 'symbols': [sym]}, expected_config, timeout)['symbols'])

    def remove_symbol(self, sym: str) -> List[str]:
        with self._state_lock:
            if self.is_running or self._check_lock.locked() or (self.task is not None and not self.task.done()):
                raise RuntimeError('Sembol listesini değiştirmeden önce botu durdurun; yeni semboller başlangıç kontrolünden geçmelidir.')
            sym = sym.strip()
            if hasattr(self, 'symbols') and sym in self.symbols:
                if len(self.symbols) > 1:
                    self.symbols.remove(sym)
                    if self.symbol == sym:
                        self.symbol = self.symbols[0]
                    self.log(f"➖ Bot izleme listesinden çıkarıldı: {sym} (Kalan: {', '.join(self.symbols)})")
                    self.persist_config(self.config_snapshot())
                else:
                    self.log(f"⚠️ {sym} son semboldür ve kaldırılamaz. En az 1 sembol kalmalıdır.", "WARNING")
            return list(self.symbols)

    def update_config(self, data: Dict[str, Any], persist=True):
        # Settings are validated and persisted as one snapshot before publication.
        # Direct callers receive the same safety guarantees as the HTTP endpoint.
        with self._state_lock:
            if self.is_running or self._check_lock.locked() or (self.task is not None and not self.task.done()):
                raise ValueError('Ayarları değiştirmeden önce botu durdurun ve mevcut döngünün bitmesini bekleyin.')
            candidate = self.validated_config(data)
            if persist:
                self.persist_config(candidate)
            for key, value in candidate.items():
                setattr(self, key, value)
            self._candidate = None
            self.current_atr = self.current_er = 0.0
            self.last_candle_time = 0
            self.last_candle_times = {}
        self.log(f"Bot ayarları güncellendi: {', '.join(self.symbols)} | HMA: {self.hma_period} | 2.MA: {self.second_ma_type}({self.second_ma_period}) | Lot: {self.lot_size}")

    def validated_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Current settings + data, validated; nothing is applied."""
        with self._state_lock:
            candidate = self.config_snapshot()
            if not isinstance(data, dict) or set(data) - set(candidate):
                raise ValueError('Bilinmeyen bot ayarı.')
            candidate.update(data)
            if candidate.get('entry_mode') not in ('CROSSOVER', 'MANUAL_STYLE', 'MEVO'):
                raise ValueError('Geçersiz bot ayarı: entry_mode')
            if candidate['entry_mode'] == 'MANUAL_STYLE' and any(t not in MANUAL_STYLE_TIMEFRAMES for t in
                    [candidate['timeframe_minutes'], *(candidate.get('extra_timeframes') or [])]):
                raise ValueError('Manuel işlem stili M1, M5, M15, M30, H1 ve H4 zaman dilimlerinde çalışır.')
            if candidate['entry_mode'] == 'MEVO' and any(t not in MEVO_TIMEFRAMES for t in
                    [candidate['timeframe_minutes'], *(candidate.get('extra_timeframes') or [])]):
                raise ValueError('MevO girişi M5, M15, M30, H1 ve H4 zaman dilimlerinde çalışır (token maliyeti).')
            if candidate.get('fast_ma_type') not in ('HMA', 'EMA', 'SMA', 'LWMA'):
                raise ValueError('Geçersiz bot ayarı: fast_ma_type')
            for key in ('symbol', 'second_ma_type', 'risk_mode'):
                if not isinstance(candidate[key], str):
                    raise ValueError(f'Geçersiz bot ayarı: {key}')
                # Broker symbol names are case-sensitive (EURUSD.a, BTCUSDm); only enums are normalised.
                candidate[key] = candidate[key].strip() if key == 'symbol' else candidate[key].upper()
            if not re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', candidate['symbol']):
                raise ValueError('Geçersiz sembol.')
            if 'symbols' in candidate:
                raw_syms = candidate['symbols']
                if isinstance(raw_syms, str):
                    sym_list = [s.strip() for s in raw_syms.split(',') if s.strip()]
                elif isinstance(raw_syms, list):
                    sym_list = [str(s).strip() for s in raw_syms if str(s).strip()]
                else:
                    sym_list = []
                valid_syms = [s for s in sym_list if re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', s)]
                if valid_syms:
                    if candidate['symbol'] not in valid_syms:
                        valid_syms.insert(0, candidate['symbol'])
                    candidate['symbols'] = valid_syms
                else:
                    candidate['symbols'] = [candidate['symbol']]
            else:
                candidate['symbols'] = [candidate['symbol']]

            if candidate['second_ma_type'] not in ('EMA', 'SMA', 'LWMA', 'HMA', 'KAMA') or candidate['risk_mode'] not in ('POINTS', 'ATR'):
                raise ValueError('Geçersiz gösterge veya risk modu.')
            bounds = {'timeframe_minutes': (1, 43200), 'hma_period': (2, 1000), 'second_ma_period': (2, 1000),
                      'kama_fast': (2, 200), 'kama_slow': (2, 200), 'atr_period': (2, 200),
                      'confirmation_bars': (0, 3), 'sl_points': (0, None), 'tp_points': (0, None)}
            for key, (low, high) in bounds.items():
                value = candidate[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
                    raise ValueError(f'Geçersiz bot ayarı: {key}')
            extras = candidate.get('extra_timeframes')
            if not isinstance(extras, list) or any(isinstance(t, bool) or not isinstance(t, int) or t not in BOT_TIMEFRAMES for t in extras):
                raise ValueError('Geçersiz bot ayarı: extra_timeframes (M1, M5, M15, M30, H1, H4, D1)')
            candidate['extra_timeframes'] = sorted(set(extras) - {candidate['timeframe_minutes']})
            if not isinstance(candidate.get('copy_manual_since'), str) or not re.fullmatch(r'20\d\d-[01]\d-[0-3]\d', candidate['copy_manual_since']):
                raise ValueError('Geçersiz bot ayarı: copy_manual_since (YYYY-AA-GG)')
            try:
                datetime.strptime(candidate['copy_manual_since'], '%Y-%m-%d')
            except ValueError as exc:
                raise ValueError('Geçersiz bot ayarı: copy_manual_since (YYYY-AA-GG)') from exc
            for key in ('use_stop_loss', 'use_take_profit', 'close_opposite', 'use_atr_filter', 'use_h4_filter', 'single_position',
                        'trend_entry_on_start', 'copy_manual_trades', 'auto_lot_limits_demo', 'reenter_after_stop'):
                if not isinstance(candidate[key], bool):
                    raise ValueError(f'Geçersiz bot ayarı: {key}')
            for key in ('stop_profit_amount', 'stop_loss_amount', 'copy_lot_override'):
                value = candidate[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f'Geçersiz bot ayarı: {key}')
            for key, low, high, inclusive in (('lot_size', 0, None, False), ('er_min', 0, 1, True),
                    ('min_distance_atr', 0, 2, True), ('atr_stop_multiplier', 0, 20, False), ('atr_target_multiplier', 0, 50, True)):
                value = candidate[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low or (not inclusive and value == low) or (high is not None and value > high):
                    raise ValueError(f'Geçersiz bot ayarı: {key}')
            if candidate['timeframe_minutes'] not in TIMEFRAME_NAMES or candidate['kama_fast'] >= candidate['kama_slow']:
                raise ValueError('Geçersiz zaman dilimi veya KAMA periyotları.')
            if candidate['use_h4_filter'] and (candidate['timeframe_minutes'] != 60 or candidate['second_ma_type'] != 'KAMA' or not candidate['use_atr_filter']):
                raise ValueError('H4 filtresi H1, KAMA ve kesişim onayı gerektirir.')
            if not candidate['single_position']:
                raise ValueError('Bot aynı sembolde tek pozisyon destekler.')
            if candidate['risk_mode'] == 'POINTS' and ((candidate['use_stop_loss'] and candidate['sl_points'] <= 0) or (candidate['use_take_profit'] and candidate['tp_points'] <= 0)):
                raise ValueError('Etkin stop/hedef mesafesi pozitif olmalı.')
            if candidate['risk_mode'] == 'ATR' and candidate['use_take_profit'] and candidate['atr_target_multiplier'] <= 0:
                raise ValueError('Etkin ATR hedef çarpanı pozitif olmalı.')
            self._validate_protection(candidate)
            return candidate

    def _sym_status(self, sym, state, detail, tf=None, **extra):
        with self._state_lock:
            row = self.symbol_status.setdefault(sym, {})
            row.update(extra)
            row.update(state=state, detail=detail, updated=int(time.time()), timeframe_minutes=int(tf or self.timeframe_minutes))

    def _instance_key(self, sym, tf):
        """State key of one EA instance: the symbol for the bot timeframe, 'SYM@M5' for an extra one."""
        return sym if int(tf) == int(self.timeframe_minutes) else f'{sym}@{tf_code(tf)}'

    def instance_timeframes(self):
        return [int(self.timeframe_minutes)] + [int(t) for t in self.extra_timeframes if int(t) != int(self.timeframe_minutes)]

    def _owner_tf(self, position):
        """Timeframe instance that opened a bot position, from its comment ('HMA Bot M5 BUY'); positions
        without a timeframe tag (older bot orders, approved signals) belong to the bot timeframe."""
        match = _COMMENT_TF.match(str(position.get('comment') or ''))
        return _TF_FROM_CODE[match.group(1)] if match else int(self.timeframe_minutes)

    def _bars_since_cross(self, closes, limit=60):
        """(bars since last HMA x MA crossover on closed candles, direction 1/-1) or (None, 0)."""
        try:
            for shift in range(1, limit):
                h1, h2 = self.calc_fast(closes, shift=shift), self.calc_fast(closes, shift=shift + 1)
                m1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=shift)
                m2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=shift + 1)
                if not all(math.isfinite(v) and v > 0 for v in (h1, h2, m1, m2)):
                    return None, 0
                # Same count as the scanner: 1 = the last closed candle.
                if h2 <= m2 and h1 > m1:
                    return shift, 1
                if h2 >= m2 and h1 < m1:
                    return shift, -1
        except Exception:
            pass
        return None, 0

    def set_start_notes(self, notes):
        with self._state_lock:
            self.start_notes = [str(n)[:400] for n in notes][:10]
        for note in self.start_notes:
            self.log(f'ℹ️ {note}')

    def set_start_warnings(self, warnings):
        with self._state_lock:
            self.start_warnings = [str(w)[:300] for w in warnings][:20]
        for warning in self.start_warnings:
            self.log(f'⚠️ Başlangıç uyarısı: {warning}')

    def _safe_window_issue(self):
        try:
            return self.trading_window_issue()
        except Exception:
            return None

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            return self._status_snapshot()

    def _status_snapshot(self):
        return {
            "is_running": self.is_running,
            "runtime_state": self.runtime_state,
            "runtime_detail": self.runtime_detail,
            "last_error": self.last_error,
            "symbol": self.symbol,
            "symbols": getattr(self, "symbols", [self.symbol]),
            "timeframe_minutes": self.timeframe_minutes,
            "hma_period": self.hma_period,
            "fast_ma_type": self.fast_ma_type,
            "entry_mode": self.entry_mode,
            "second_ma_type": self.second_ma_type,
            "second_ma_period": self.second_ma_period,
            "lot_size": self.lot_size,
            **{key: getattr(self, key) for key in ("kama_fast", "kama_slow", "atr_period",
                "use_atr_filter", "use_h4_filter", "single_position", "er_min", "confirmation_bars", "min_distance_atr", "risk_mode",
                "atr_stop_multiplier", "atr_target_multiplier", "current_atr", "current_er")},
            "pending_confirmation": self._candidate is not None,
            "use_stop_loss": self.use_stop_loss,
            "sl_points": self.sl_points,
            "use_take_profit": self.use_take_profit,
            "tp_points": self.tp_points,
            "close_opposite": self.close_opposite,
            "trend_entry_on_start": self.trend_entry_on_start,
            "reenter_after_stop": self.reenter_after_stop,
            "stop_profit_amount": self.stop_profit_amount,
            "copy_lot_override": self.copy_lot_override,
            "stop_loss_amount": self.stop_loss_amount,
            "session_pnl": self.session_pnl,
            "extra_timeframes": list(self.extra_timeframes),
            "instance_timeframes": self.instance_timeframes(),
            "copy_manual_trades": self.copy_manual_trades,
            "auto_lot_limits_demo": self.auto_lot_limits_demo,
            "bot_lot_override": (lambda o: list(o) if isinstance(o, (list, tuple)) else None)(getattr(self.mt5, 'bot_lot_override', None)),
            "copy_manual_since": self.copy_manual_since,
            "manual_copy": dict(self.manual_copy) if isinstance(self.manual_copy, dict) else None,
            **{key: (list(getattr(self, key)) if isinstance(getattr(self, key), list) else getattr(self, key)) for key in (
                "lot_mode", "risk_pct", "max_consecutive_losses", "trade_hours_enabled", "trade_start", "trade_end",
                "trade_utc_offset_min", "trade_days", "blocked_windows")},
            "trading_window_issue": self._safe_window_issue(),
            "start_warnings": list(self.start_warnings) if self.is_running else [],
            "start_notes": list(self.start_notes) if self.is_running else [],
            "execution_mode": self.execution_mode,
            "max_order_lots": (lambda cap: cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) else None)(getattr(self.mt5, "max_order_lots", None)),
            "max_total_lots": (lambda cap: cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) else None)(getattr(self.mt5, "max_total_lots", None)),
            "symbol_status": {self._instance_key(sym, tf): dict(self.symbol_status.get(self._instance_key(sym, tf), {}))
                              for sym in getattr(self, "symbols", [self.symbol]) for tf in self.instance_timeframes()},
            "last_signal": self.last_signal,
            "last_signal_time": self.last_signal_time,
            "last_signal_price": self.last_signal_price,
            "current_hma": round(self.current_hma, 5),
            "current_ma2": round(self.current_ma2, 5),
            "pending_signal_actions": getattr(self, "pending_signal_actions", []),
            "logs": self.logs[:15],
            "signal_history": self.signal_history[:50]
        }

    def history_bars(self) -> int:
        """Bars to read so EMA/RSI/AMA (recursive in MT5) equal MT5's values on the full chart."""
        need = [100, 5 * max(self.hma_period, self.second_ma_period, self.kama_slow, self.atr_period) + 20,
                signal_engine.wilder_warmup(14)]
        if self.second_ma_type == "EMA":
            need.append(signal_engine.ema_warmup(self.second_ma_period))
        if self.fast_ma_type == "EMA":
            need.append(signal_engine.ema_warmup(self.hma_period))
        if self.second_ma_type == "KAMA":
            slow = 2.0 / (self.kama_slow + 1.0)
            need.append(int(math.ceil(23.0 / (slow * slow))) + self.second_ma_period)
        return min(signal_engine.MAX_HISTORY_BARS, max(need))

    # WMA (Weighted Moving Average)
    @staticmethod
    def calc_wma(prices: List[float], period: int, shift: int = 0) -> float:
        if len(prices) < period + shift or period <= 0:
            return 0.0
        subset = prices[shift:shift + period]
        weights = list(range(period, 0, -1))
        w_sum = sum(weights)
        if w_sum == 0:
            return 0.0
        return sum(p * w for p, w in zip(subset, weights)) / w_sum

    # HMA (Hull Moving Average)
    @classmethod
    def calc_hma(cls, close_prices: List[float], period: int, shift: int = 0) -> float:
        sqrt_period = int(math.floor(math.sqrt(period))) if period >= 2 else 0
        if period < 2 or shift < 0 or len(close_prices) < period + shift + sqrt_period - 1:
            return 0.0

        half_period = int(math.floor(period / 2.0))

        diff_array = []
        for i in range(sqrt_period + shift):
            wma_half = cls.calc_wma(close_prices, half_period, i)
            wma_full = cls.calc_wma(close_prices, period, i)
            diff_array.append(2.0 * wma_half - wma_full)

        # WMA(sqrt_period) of diff_array
        if len(diff_array) < sqrt_period + shift:
            return 0.0
        return cls.calc_wma(diff_array, sqrt_period, shift)

    def calc_fast(self, close_prices: List[float], shift: int = 0) -> float:
        """Fast line of the crossover: HMA like the MT5 EA, or EMA / SMA / LWMA (fast_ma_type) for the
        common MA-crossover presets. Same formulas as the scanner (signal_engine.fast_series)."""
        if self.fast_ma_type == 'HMA':
            return self.calc_hma(close_prices, self.hma_period, shift)
        return self.calc_second_ma(close_prices, self.fast_ma_type, self.hma_period, shift)

    # Secondary MA
    @classmethod
    def calc_second_ma(cls, close_prices: List[float], ma_type: str, period: int, shift: int = 0) -> float:
        if len(close_prices) < period + shift or period <= 0:
            return 0.0

        subset = close_prices[shift:shift + period]
        if ma_type == "SMA":
            return sum(subset) / len(subset)
        elif ma_type == "LWMA":
            return cls.calc_wma(close_prices, period, shift)
        elif ma_type == "HMA":
            return cls.calc_hma(close_prices, period, shift)
        else: # Default: EMA, exactly MT5 iMA MODE_EMA (seeded with the first price)
            k = 2.0 / (period + 1.0)
            history = list(reversed(close_prices[shift:]))  # chronological
            if len(history) < period:
                return 0.0
            ema = float(history[0])
            for price in history[1:]:
                ema = (price * k) + (ema * (1.0 - k))
            return ema

    @staticmethod
    def calc_rsi(closes_chrono: List[float], period: int = 14) -> float:
        if len(closes_chrono) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, period + 1):
            diff = closes_chrono[i] - closes_chrono[i - 1]
            gains.append(max(0.0, diff))
            losses.append(max(0.0, -diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        for i in range(period + 1, len(closes_chrono)):
            diff = closes_chrono[i] - closes_chrono[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(0.0, diff)) / period
            avg_loss = (avg_loss * (period - 1) + max(0.0, -diff)) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))  # MT5 iRSI: unrounded

    def live_preview(self) -> Dict[str, Any]:
        try:
            rates = self.mt5.get_rates(self.symbol, timeframe=self.timeframe_minutes,
                                       count=self.history_bars())
            if not rates or len(rates) < max(self.hma_period, self.second_ma_period) + 15:
                return {"available": False, "symbol": self.symbol, **self.get_signal_rules()}
            rev_closes = [float(r["close"]) for r in reversed(rates)]
            closed = rates[:-1]
            study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
                kama_period=self.second_ma_period, kama_fast=self.kama_fast,
                kama_slow=self.kama_slow, atr_period=self.atr_period))
            atr = float(study["atr"][-1] or 0.0) if study["atr"] else 0.0
            er = float(study["er"][-1] or 0.0) if study["er"] else 0.0
            if self.second_ma_type == "KAMA":
                hma1 = float(study["hma"][-1] or 0.0)
                ma2_1 = float(study["kama"][-1] or 0.0)
            else:
                hma1 = self.calc_fast(rev_closes, shift=1)
                ma2_1 = self.calc_second_ma(rev_closes, self.second_ma_type, self.second_ma_period, shift=1)
            rsi = self.calc_rsi([float(r["close"]) for r in closed], 14)
            with self._state_lock:
                if not self.is_running:
                    self.current_hma, self.current_ma2 = hma1, ma2_1
                    self.current_atr, self.current_er, self.current_rsi = atr, er, rsi
            trend = "BUY" if hma1 > ma2_1 else "SELL" if hma1 < ma2_1 else "HOLD"
            gap = abs(hma1 - ma2_1)
            return {
                "available": True,
                "symbol": self.symbol,
                "timeframe_minutes": self.timeframe_minutes,
                "hma": round(hma1, 5),
                "ma2": round(ma2_1, 5),
                "rsi": rsi,
                "atr": round(atr, 5),
                "er": round(er, 2),
                "trend": trend,
                "gap": round(gap, 5),
                **self.get_signal_rules(),
            }
        except Exception:
            return {"available": False, "symbol": self.symbol, **self.get_signal_rules()}

    def _apply_position_protection(self, expected_account=None):
        """Breakeven / trailing stop for the bot's own positions (bridge keys: price_open, price_current)."""
        with self._state_lock:
            use_breakeven, trail_pts = self.use_breakeven, int(self.trailing_stop_points or 0)
            active_symbols = set(getattr(self, 'symbols', [self.symbol]))
            default_risk_pts = self.sl_points if self.use_stop_loss and self.sl_points > 0 else 100
        if not (use_breakeven or trail_pts > 0):
            return
        try:
            positions = self.mt5.get_positions(fresh=True)
        except Exception as exc:
            self._protection_error(f'Pozisyon koruması: pozisyonlar okunamadı ({exc}).')
            return
        for p in positions:
            sym = p.get("symbol")
            if sym not in active_symbols or p.get("magic") != HMA_MAGIC or self._stop_event.is_set():
                continue
            try:
                spec = self.mt5.get_symbol_spec(sym) or {}
                point = float(spec.get("point") or 0)
                digits = int(spec.get("digits") if spec.get("digits") is not None else 5)
                side = p.get("type")
                open_p = float(p.get("price_open") or 0)
                curr_p = float(p.get("price_current") or 0)
                curr_sl = float(p.get("sl") or 0)
                curr_tp = float(p.get("tp") or 0)
                if point <= 0 or open_p <= 0 or curr_p <= 0 or side not in ("BUY", "SELL"):
                    continue
                direction = 1 if side == "BUY" else -1
                profit_pts = direction * (curr_p - open_p) / point
                # 1R = the position's own initial stop distance; symbol-agnostic, unlike the primary symbol's ATR.
                risk_pts = direction * (open_p - curr_sl) / point if curr_sl > 0 else 0
                trigger_pts = risk_pts if risk_pts > 0 else default_risk_pts
                new_sl = None
                if use_breakeven and profit_pts >= trigger_pts and (curr_sl == 0 or direction * (curr_sl - open_p) < 0):
                    new_sl = round(open_p + direction * 2 * point, digits)
                if trail_pts > 0 and profit_pts > trail_pts:
                    trail_sl = round(curr_p - direction * trail_pts * point, digits)
                    base = new_sl if new_sl is not None else curr_sl
                    if base == 0 or direction * (trail_sl - base) > 0:
                        new_sl = trail_sl
                if new_sl is None or abs(new_sl - curr_sl) < point:
                    continue
                kwargs = {'expected_account': expected_account} if expected_account is not None else {}
                # Same ticket + same target level = same request id: cached positions cannot resend it.
                request_id = f"bot-prot-{int(p['ticket'])}-{new_sl:.{max(0, digits)}f}"
                res = self.mt5.manage_position(int(p["ticket"]), "stops", {"sl": new_sl, "tp": curr_tp},
                                               request_id, **kwargs)
                if res.get("success"):
                    self.log(f"🛡️ [{sym}] #{p['ticket']} stop {curr_sl or '—'} → {new_sl} taşındı.")
                else:
                    self._protection_error(f"[{sym}] #{p['ticket']} stop taşınamadı: {res.get('error') or 'broker reddetti'}")
            except Exception as exc:
                self._protection_error(f"[{sym}] pozisyon koruması hatası: {exc}")

    def _protection_error(self, message):
        # Log each distinct failure once instead of every 0.5 s loop.
        if getattr(self, '_last_protection_error', None) != message:
            self._last_protection_error = message
            self.log(message, "ERROR")

    async def run_loop(self):
        symbols_str = ", ".join(getattr(self, 'symbols', [self.symbol]))
        self.log(f"🚀 HMA Kesişim Botu Başlatıldı ({symbols_str} · M{self.timeframe_minutes})")

        while self.is_running:
            try:
                await asyncio.to_thread(self.check_loss_streak)
                if self.is_running:
                    await asyncio.to_thread(self.check_session_target)
                if not self.is_running:
                    break
                await self.check_strategy()
            except Exception as e:
                logger.error(f"Strategy error: {e}")
                self.runtime_state, self.runtime_detail, self.last_error = 'ERROR', str(e), str(e)
                self.log(f"Hata: {str(e)}", "ERROR")

            await asyncio.sleep(0.5) # Yeni kapanmış mumu hızlı algıla; RPC verisi toplu aktarılır.

    async def check_strategy(self):
        await asyncio.to_thread(self._check_strategy)

    def _check_strategy(self):
        with self._state_lock:
            if not self._check_lock.acquire(blocking=False):
                return
        try:
            self._check_strategy_once()
        finally:
            if self._stop_event.is_set() and self.runtime_state != 'ORDER_UNRESOLVED':
                self.runtime_state, self.runtime_detail = 'STOPPED', 'Bot durduruldu.'
            self._check_lock.release()

    def _check_strategy_once(self):
        if self._stop_event.is_set():
            return
        account = [getattr(self.mt5, key, None) for key in ('login_id', 'server', 'account_type')]
        expected_account = account if isinstance(account[0], int) and account[0] > 0 and all(isinstance(v, str) for v in account[1:]) else None
        if not self.mt5.ensure_connected():
            self.runtime_state, self.runtime_detail = 'WAITING_CONNECTION', 'MT5 bağlantısı bekleniyor.'
            self.last_candle_time = 0
            self._candidate = None
            self._candidates = {}
            return

        self._apply_position_protection(expected_account=expected_account)

        symbols = list(getattr(self, 'symbols', [self.symbol]))
        if not symbols:
            symbols = [self.symbol]

        for sym in symbols:
            for tf in self.instance_timeframes():
                if self._stop_event.is_set():
                    return
                self._check_strategy_for_symbol(sym, expected_account=expected_account, tf=tf)

    def _check_strategy_for_symbol(self, sym: str, expected_account=None, tf=None):
        # One EA instance per chart timeframe (MT5: the same EA attached to an M1, an M5 and an H1 chart).
        # The bot timeframe is the primary instance; extra_timeframes add more, each with its own position.
        tf = int(tf or self.timeframe_minutes)
        key = self._instance_key(sym, tf)
        primary = key == sym and tf == int(self.timeframe_minutes)
        label = f"{sym} {tf_code(tf)}"

        def status(state, detail, **extra):
            self._sym_status(key, state, detail, tf=tf, symbol=sym, **extra)
        if not primary:
            # Extra instances read candles only when their next candle can have closed (saves MT5 requests;
            # the bot timeframe keeps polling every loop). Times are broker server time, like MT5 bars.
            offset = getattr(self.mt5, 'tick_clock_offset', None)
            seen = self.last_candle_times.get(key, 0)
            if seen and isinstance(offset, (int, float)) and not isinstance(offset, bool) and not self._trend_pending.get(key):
                if time.time() + offset < seen + 2 * tf * 60:
                    return
        rates = self.mt5.get_rates(sym, timeframe=tf,
                                   count=self.history_bars())
        if self._stop_event.is_set():
            return
        required = max(self.hma_period + int(math.sqrt(self.hma_period)) + 1,
                       self.second_ma_period + (int(math.sqrt(self.second_ma_period)) + 1 if self.second_ma_type == 'HMA' else 2))
        if not rates or len(rates) < max(required, max(self.hma_period, self.second_ma_period) + 15):
            status('WAITING_DATA', 'Yeterli kapanmış mum verisi yok (piyasa kapalı olabilir).')
            if primary:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', f'{sym}: Yeterli kapanmış mum verisi bekleniyor.'
            return
        if any(not isinstance(r.get('time'), (int, float)) or not math.isfinite(r['time']) or
               not isinstance(r.get('close'), (int, float)) or not math.isfinite(r['close']) or r['close'] <= 0 for r in rates) or any(
               rates[i]['time'] >= rates[i+1]['time'] for i in range(len(rates)-1)):
            if primary:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', f'{sym}: Mum verisi sıralı ve geçerli değil.'
                self._candidate = None
            return

        rev_rates = list(reversed(rates))
        closes = [r["close"] for r in rev_rates]
        latest_closed_candle_time = rev_rates[1]["time"]

        last_t = self.last_candle_times.get(key, 0)
        if primary:
            # The single-symbol marker is still reset/advanced by older code paths;
            # never process a candle older than either marker.
            last_t = max(last_t, self.last_candle_time or 0)
        start_trend = 0
        if self.is_running and not last_t:
            hma1_init = self.calc_fast(closes, shift=1)
            hma2_init = self.calc_fast(closes, shift=2)
            ma2_1_init = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)
            ma2_2_init = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)
            # HMA_Crossover_EA OnInit: m_lastBarTime = current bar, so a crossover on a candle that closed
            # before the start is never traded; the first signal is the candle that closes after the start.
            # User decision (2026-10-23, differs from the EA): with trend_entry_on_start the bot opens the
            # current trend side (HMA vs 2nd MA on the last closed candle) right away instead of waiting.
            trend_way = 1 if hma1_init > ma2_1_init else -1 if hma1_init < ma2_1_init else 0
            bars, way = self._bars_since_cross(closes)
            trend = 'AL' if hma1_init > ma2_1_init else 'SAT'
            since = f'son kesişim {bars} mum önce' if bars is not None else 'yakın zamanda kesişim yok'
            self.last_candle_times[key] = latest_closed_candle_time
            if primary:
                self.last_candle_time = latest_closed_candle_time
            if self.entry_mode in ('MANUAL_STYLE', 'MEVO'):
                start_trend = 0   # the manual-style / MevO signal of this candle decides below (an active one opens at start)
            elif self.trend_entry_on_start and trend_way and not self.use_atr_filter:
                # Kept until the trend position is opened (or one already is): a rejected first try (market
                # closed, broker reject) is retried on the next closed candle while no new crossover appears.
                self._trend_pending[key] = True
                start_trend = trend_way
                status('TREND_ENTRY',
                    f"Başlangıç: mevcut {trend} trendi ({since}) yönünde hemen işlem açılıyor "
                    "(ayarınız: başlangıçta trend yönünde gir; MT5 EA yeni kesişimi beklerdi).",
                    candle_time=latest_closed_candle_time, trend=trend)
            else:
                status('WAITING_CROSS',
                    f"Başlangıçtan önce kapanmış mumdaki sinyal işlenmez (MT5 EA ile aynı); mevcut {trend} trendi ({since}). Bir sonraki kesişim bekleniyor: "
                    f"{'SAT (aşağı)' if trend == 'AL' else 'AL (yukarı)'}.", candle_time=latest_closed_candle_time, trend=trend)
                if primary:
                    self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', 'Yeni kapanmış mumdaki sinyal bekleniyor.'
                return

        if not start_trend and latest_closed_candle_time <= last_t and not (self.entry_mode in ('MANUAL_STYLE', 'MEVO') and not last_t):
            return

        study = None
        current_atr, current_er = 0.0, 0.0
        if self.second_ma_type == "KAMA" or self.use_atr_filter or self.risk_mode == "ATR":
            closed = rates[:-1]
            study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
                kama_period=self.second_ma_period, kama_fast=self.kama_fast,
                kama_slow=self.kama_slow, atr_period=self.atr_period))
            if any(study[key][-1] is None or study[key][-2] is None for key in ('hma', 'kama', 'atr', 'er')):
                if primary:
                    self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge hesaplaması için kapanmış mum bekleniyor.'
                return
            current_atr, current_er = study['atr'][-1], study['er'][-1]
            if primary:
                self.current_atr, self.current_er = current_atr, current_er

        if self.second_ma_type == "KAMA":
            hma1, hma2 = study['hma'][-1], study['hma'][-2]
            ma2_1, ma2_2 = study['kama'][-1], study['kama'][-2]
        else:
            hma1 = self.calc_fast(closes, shift=1)
            hma2 = self.calc_fast(closes, shift=2)
            ma2_1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)
            ma2_2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)

        if any(not math.isfinite(value) for value in (hma1, hma2, ma2_1, ma2_2)):
            if primary:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge değerleri doğrulanamadı.'
            return

        rsi = self.calc_rsi([float(r["close"]) for r in rates[:-1]], 14)
        if primary:
            self.current_hma, self.current_ma2 = hma1, ma2_1
            self.current_rsi = rsi
            self.runtime_state, self.runtime_detail, self.last_error = 'WAITING_SIGNAL', 'Kesişim sinyali bekleniyor.', None
            self.last_candle_time = latest_closed_candle_time

        self.last_candle_times[key] = latest_closed_candle_time
        cross = 1 if hma2 <= ma2_2 and hma1 > ma2_1 else -1 if hma2 >= ma2_2 and hma1 < ma2_1 else 0
        if self.entry_mode == 'MANUAL_STYLE':
            cross = self._manual_style_cross(sym, tf, key, latest_closed_candle_time, status)
            if cross is None:
                return
            start_trend = 0
            self._trend_pending.pop(key, None)
            if not cross:
                return
        if self.entry_mode == 'MEVO':
            cross = self._mevo_cross(sym, tf, key, latest_closed_candle_time, status)
            if cross is None:
                return
            start_trend = 0
            self._trend_pending.pop(key, None)
            if not cross:
                return
        if cross:
            self._trend_pending.pop(key, None)
        elif self._trend_pending.get(key) and (self.trend_entry_on_start or self.reenter_after_stop):
            start_trend = 1 if hma1 > ma2_1 else -1 if hma1 < ma2_1 else 0
        elif not start_trend and self._reentry_allowed(sym, tf, key):
            start_trend = 1 if hma1 > ma2_1 else -1 if hma1 < ma2_1 else 0
            if start_trend:
                self._trend_pending[key] = True
                status('TREND_ENTRY', f"Botun {label} pozisyonu SL/TP ile kapandı; {'AL' if start_trend == 1 else 'SAT'} trendi sürdüğü için "
                       "yeniden giriliyor (ayarınız: kapanınca sinyal varsa yeniden gir).", candle_time=latest_closed_candle_time)
        direction = cross or start_trend
        closed_on_cross = False
        if not cross and not start_trend:
            trend = 'AL' if hma1 > ma2_1 else 'SAT'
            bars, _ = self._bars_since_cross(closes)
            since = f'son kesişim {bars} mum önce' if bars is not None else 'yakın zamanda kesişim yok'
            status('WAITING_CROSS', f"Kesişim yok; {trend} trendi sürüyor ({since}). "
                             f"Bot yeni kesişimde işlem açar: {'SAT (aşağı)' if trend == 'AL' else 'AL (yukarı)'} bekleniyor.",
                             candle_time=latest_closed_candle_time, trend=trend)

        # MT5 EA ProcessSignal: a reverse crossover first closes the opposite bot position, then the
        # entry rules decide. Entry-only filters (RSI, spread, ER/ATR confirmation, hours) must not keep
        # a position open against the new crossover. "Sadece Sinyal" never sends anything.
        if cross and self.close_opposite and self.execution_mode == "AUTO_TRADE" and not self._stop_event.is_set():
            if self.mt5.has_unresolved_bot_order(sym):
                self.log(f'[{label}] Önceki bot emri belirsiz; MT5 mutabakatı bekleniyor.', 'ERROR')
                self.stop()
                self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', f'{sym} bot emri belirsiz; MT5 mutabakatı bekleniyor.'
                return
            if not self.close_positions_by_type("SELL" if cross == 1 else "BUY", sym=sym, expected_account=expected_account, tf=tf):
                status('SKIPPED', f"{'AL' if cross == 1 else 'SAT'} kesişimi: ters bot pozisyonu kapatılamadı, yeni emir gönderilmedi.")
                self.log(f"[{label}] Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
                return
            closed_on_cross = True

        if self.use_atr_filter:
            direction = 0
            if cross:
                self._candidates[key] = {'side': cross, 'time': latest_closed_candle_time}
                if primary:
                    self._candidate = self._candidates[key]

            cand = self._candidates.get(key)
            if cand:
                side = cand['side']
                age = sum(r['time'] > cand['time'] for r in rates[:-1])
                valid = (current_atr > 0 and current_er >= self.er_min and
                         side * (hma1 - ma2_1) > 0 and
                         side * (hma1 - ma2_1) >= self.min_distance_atr * current_atr and
                         side * (hma1 - hma2) > 0 and side * (ma2_1 - ma2_2) > 0)
                if valid and self.use_h4_filter:
                    valid = self.h4_confirmation(side, latest_closed_candle_time + tf * 60, sym=sym)
                if age > self.confirmation_bars:
                    status('SKIPPED', 'Kesişim oldu ama onay süresi doldu; sinyal iptal edildi (filtre).')
                    self.log(f"[{label}] Onay süresi doldu; kesişim sinyali iptal edildi.")
                    self._candidates.pop(key, None)
                    if primary:
                        self._candidate = None
                elif valid:
                    direction = side
                    self._candidates.pop(key, None)
                    if primary:
                        self._candidate = None
                elif age == self.confirmation_bars:
                    status('SKIPPED', 'Kesişim oldu ama ER/eğim/ATR onayı gelmedi; sinyal iptal edildi (filtre).')
                    self.log(f"[{label}] ER/eğim/ATR mesafesi onayı gelmedi; sinyal iptal edildi.")
                    self._candidates.pop(key, None)
                    if primary:
                        self._candidate = None
                else:
                    status('WAITING_CONFIRMATION', f'Kesişim var; ER/eğim/ATR onayı bekleniyor ({age}/{self.confirmation_bars} mum).')
                    if primary:
                        self.runtime_state, self.runtime_detail = 'WAITING_CONFIRMATION', 'ER/eğim/ATR mesafesi onayı bekleniyor.'
                    self.log(f"[{label}] Kesişim onayı bekleniyor: {age}/{self.confirmation_bars} mum.")

        if not direction or self._stop_event.is_set():
            return

        side = "BUY" if direction == 1 else "SELL"
        if self.use_rsi_filter:
            if side == "BUY" and rsi > self.rsi_buy_max:
                status('SKIPPED', f'AL kesişimi RSI filtresine takıldı ({rsi:.1f} > {self.rsi_buy_max}).')
                self.log(f"⚠️ [{label}] RSI aşırı alımda ({rsi:.1f} > {self.rsi_buy_max}); AL sinyali filtrelendi.")
                return
            if side == "SELL" and rsi < self.rsi_sell_min:
                status('SKIPPED', f'SAT kesişimi RSI filtresine takıldı ({rsi:.1f} < {self.rsi_sell_min}).')
                self.log(f"⚠️ [{label}] RSI aşırı satımda ({rsi:.1f} < {self.rsi_sell_min}); SAT sinyali filtrelendi.")
                return

        if self.max_spread_points > 0:
            try:
                quote = self.mt5.get_symbol_price(sym) or {}
                spread = float(quote.get("spread") or 0)
                if spread > self.max_spread_points:
                    status('SKIPPED', f'{side} kesişimi yüksek spread nedeniyle atlandı ({spread:.0f} > {self.max_spread_points} puan).')
                    self.log(f"⚠️ [{label}] Spread yüksek ({spread:.0f} > {self.max_spread_points} puan); {side} sinyali atlandı.")
                    return
            except Exception:
                pass

        sl, tp = self.order_distances(sym)
        if sl is None:
            status('SKIPPED', f'{side} kesişimi: ATR stop/hedef mesafesi hesaplanamadı.')
            return
        copied_lot, sl, tp, copy_note = self.copied_order_settings(sym, sl, tp)
        if self.entry_mode == 'MEVO' and key in self._mevo_levels:
            sl, tp, level_note = self._mevo_order_points(sym, key, sl, tp)
            copy_note = (copy_note + ' | ' if copy_note else '') + level_note
        entry_kind = 'trend girişi' if start_trend and not cross else 'kesişim'

        if primary:
            self.last_signal = side
            self.last_signal_time = time.strftime("%H:%M:%S")
            self.last_signal_price = closes[1]

        self.log(f"[{label} {side} SİNYALİ · {entry_kind}] HMA({hma1:.5f}) / {self.second_ma_type}({ma2_1:.5f}) | RSI: {rsi:.1f}"
                 + (f" | {copy_note}" if copy_note else ''), "SIGNAL")

        if self.execution_mode == "SIGNAL_ONLY":
            action = {
                "id": f"bot-{sym}-{tf}-{side}-{latest_closed_candle_time}",
                "symbol": sym,
                "side": side,
                "price": closes[1],
                "sl_points": sl,
                "tp_points": tp,
                "lot_size": copied_lot if copied_lot is not None else self.lot_size,
                "time": time.strftime("%H:%M:%S"),
                "rsi": round(rsi, 1),
            }
            curr_actions = getattr(self, 'pending_signal_actions', [])
            self.pending_signal_actions = [a for a in curr_actions if a.get('symbol') != sym]
            self.pending_signal_actions.insert(0, action)
            if len(self.pending_signal_actions) > 20:
                self.pending_signal_actions.pop()
            self.pending_signal_action = action
            self._trend_pending.pop(key, None)
            status('AWAITING_APPROVAL', f'{side} sinyali yakalandı; "Sadece Sinyal" modunda onayınız bekleniyor.')
            if primary:
                self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', f'{sym} {side} sinyali yakalandı; onayınız bekleniyor.'
            self.log(f"🔔 [{label} {side} SİNYALİ YAKALANDI · ONAY BEKLİYOR] Fiyat: {closes[1]:.5f} (Sadece Sinyal Modu)", "SIGNAL")
            return

        if self.close_opposite and not closed_on_cross and not self.close_positions_by_type("SELL" if direction == 1 else "BUY", sym=sym, expected_account=expected_account, tf=tf):
            status('SKIPPED', f'{side} kesişimi: ters bot pozisyonu kapatılamadı, yeni emir gönderilmedi.')
            self.log(f"[{label}] Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
            return

        if self._stop_event.is_set():
            return

        if self.mt5.has_unresolved_bot_order(sym):
            self.log(f'[{label}] Önceki bot emri belirsiz; MT5 mutabakatı bekleniyor.', 'ERROR')
            self.stop()
            self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', f'{sym} bot emri belirsiz; MT5 mutabakatı bekleniyor.'
            return

        if self.single_position:
            owned = self.mt5.get_positions(fresh=True)
            # HMA_Crossover_EA HasOpenPosition(orderType): only a bot position in the SAME direction blocks.
            # An opposite one was closed above when close_opposite is on; otherwise MT5 opens the hedge.
            if any(p['symbol'] == sym and p.get('magic') == HMA_MAGIC and p.get('type') == side and self._owner_tf(p) == tf for p in owned):
                status('SKIPPED', f'{side} kesişimi atlandı: bu sembolde aynı yönde bir bot pozisyonu açık '
                                 '("Şimdi AL/SAT" ile açılanlar dahil). MT5 EA kuralı: aynı yönde ikinci pozisyon açılmaz.')
                self.log(f'Bot {label} {side} pozisyonu zaten açık; yeni giriş atlandı.')
                self._trend_pending.pop(key, None)
                return

        issue = self.trading_window_issue()
        if issue:
            status('SKIPPED', f'{side} kesişimi koruma kuralına takıldı: {issue}')
            self.log(f'⏸️ [{label} {side}] {issue} Yeni giriş yapılmadı.')
            if primary:
                self.runtime_state, self.runtime_detail = 'OUTSIDE_HOURS', issue
            return

        if copied_lot is not None:
            lot, risk_money, lot_error = copied_lot, None, None
        else:
            lot, risk_money, lot_error = self.order_lot(sym, side, sl, expected_account,
                                                        cap=getattr(self.mt5, 'max_order_lots', None))
        if lot is None:
            status('SKIPPED', f'{side} kesişimi: {lot_error}')
            self.log(f'⚠️ [{label} {side}] {lot_error}', 'ERROR')
            self.record_signal(source='AUTO', symbol=sym, side=side, lot=None, sl_points=sl, tp_points=tp,
                               result={'success': False, 'error': lot_error})
            return

        kwargs = {'expected_account': expected_account} if expected_account is not None else {}
        if primary:
            self.runtime_state, self.runtime_detail = 'SUBMITTING', f'{sym} emir sonucu bekleniyor.'

        res = self.mt5.open_order(sym, side, lot, sl_points=sl, tp_points=tp,
            comment=f"HMA Bot {tf_code(tf)} {side}", magic=HMA_MAGIC, request_id=self.order_id(side, sym=sym, tf=tf), stop_event=self._stop_event,
            deviation_points=EA_DEVIATION, **kwargs)
        self.record_signal(source='AUTO', symbol=sym, side=side, lot=lot, result=res, sl_points=sl, tp_points=tp,
                           risk_money=risk_money)

        if res.get("success"):
            self._trend_pending.pop(key, None)
            status('ORDER_OPENED', f"{side} emri açıldı: #{res.get('ticket')} @ {res.get('price')} ({lot} lot).")
            if primary:
                self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', f'{sym} emir kabul edildi; yeni sinyal bekleniyor.'
            self.log(f"✅ {label} {side} Emri Açıldı: #{res.get('ticket')} @ {res.get('price')}")
        else:
            status('REJECTED', f"{side} emri reddedildi: {res.get('error')}")
            if primary:
                self.runtime_state, self.runtime_detail, self.last_error = 'ORDER_REJECTED', res.get('error'), res.get('error')
            self.log(f"❌ {label} {side} Emri Başarısız: {res.get('error')}", "ERROR")

        if res.get('uncertain') or res.get('pending') or res.get('partial'):
            self.log(f'{sym} emir sonucu kesinleşmedi; bot durduruldu. MT5 durumunu kontrol edin.', 'ERROR')
            self.stop()
            self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', f'{sym} emir kesinleşmedi; MT5 durumunu kontrol edin.'

    def h4_confirmation(self, side, decision, sym=None):
        target_sym = sym or self.symbol
        rows = self.mt5.get_rates(target_sym, timeframe=240,
            count=5*max(self.hma_period,self.second_ma_period,self.kama_slow,self.atr_period)+20)
        closed = [r for r in rows if r['time']+14400 <= decision]
        if len(closed) < 2 or decision-(closed[-1]['time']+14400) >= 14400:
            return False
        study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
            kama_period=self.second_ma_period,kama_fast=self.kama_fast,kama_slow=self.kama_slow,atr_period=self.atr_period))
        er, kama = study['er'], study['kama']
        return (er[-1] is not None and kama[-1] is not None and kama[-2] is not None
                and er[-1] >= self.er_min and side*(kama[-1]-kama[-2]) > 0)

    STOP_REASONS = (4, 5, 6)   # MT5 DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO

    def _reentry_allowed(self, sym, tf, key):
        """True once per closed position: this timeframe instance has no open bot position and its last
        position, closed during this run, was closed by SL / TP / stop out. A hand close (desktop, mobile,
        web or the panel's Kapat) never triggers a re-entry; the next crossover does as usual."""
        if not self.reenter_after_stop:
            return False
        try:
            owned = self.mt5.get_positions(fresh=True)
            if not isinstance(owned, list):
                return False
            if any(p.get('symbol') == sym and p.get('magic') == HMA_MAGIC and self._owner_tf(p) == tf for p in owned):
                return False
            closes = self.mt5.bot_recent_closes(sym)
        except Exception:
            return False
        if not isinstance(closes, list):
            return False
        mine = [c for c in closes if isinstance(c, dict) and self._owner_tf({'comment': c.get('comment')}) == tf]
        if not mine:
            return False
        last = mine[0]
        if last.get('close_reason') not in self.STOP_REASONS:
            return False
        offset = getattr(self.mt5, 'tick_clock_offset', 0)
        offset = offset if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 0
        if self.started_at and int(last.get('close_time') or 0) - offset < self.started_at - 60:
            return False      # closed before this run started
        if self._reentry_for.get(key) == last.get('position_id'):
            return False      # already re-entered after this close
        self._reentry_for[key] = last.get('position_id')
        return True

    def _ms_retry(self, key, candle_time):
        """Evaluate this closed candle again on the next loop (marker just below it, not a re-init)."""
        self.last_candle_times[key] = int(candle_time) - 1
        if '@' not in key and self.last_candle_time == candle_time:
            self.last_candle_time = int(candle_time) - 1

    def _mevo_cross(self, sym, tf, key, candle_time, status):
        """1 / -1 when MevO has a BUY / SELL opportunity for this closed candle that passes the bot's checks; 0 when
        there is none; None when it cannot be evaluated. MevO is asked only while this instance has no own position
        (token cost), at most once per closed candle (MevO caches it) and not within a few candles of the last entry."""
        from app.mevo import blocking_warnings
        if self.mevo_provider is None:
            self._ms_retry(key, candle_time)
            status('WAITING_DATA', 'MevO hazır değil.', candle_time=candle_time)
            return None
        try:
            owned = self.mt5.get_positions(fresh=True)
        except Exception as exc:
            status('WAITING_DATA', f'Pozisyonlar okunamadı: {exc}', candle_time=candle_time)
            self._ms_retry(key, candle_time)
            return None
        if any(p['symbol'] == sym and p.get('magic') == HMA_MAGIC and self._owner_tf(p) == tf for p in owned):
            status('WAITING_CROSS', 'MevO: bu zaman diliminin pozisyonu açık; yeni fırsat için MevO\'ya sorulmadı (token tasarrufu).',
                   candle_time=candle_time)
            return 0
        last = self._mevo_last.get(key)
        if last and int(candle_time) - last < MEVO_COOLDOWN_CANDLES * tf * 60:
            status('WAITING_CROSS', f'MevO: son girişten sonra {MEVO_COOLDOWN_CANDLES} mum beklenir.', candle_time=candle_time)
            return 0
        try:
            item = self.mevo_provider(sym, tf)
        except Exception as exc:
            status('WAITING_DATA', f'MevO fırsatı alınamadı: {exc}', candle_time=candle_time)
            self._ms_retry(key, candle_time)
            return None
        if not item or item.get('action') not in ('BUY', 'SELL'):
            status('WAITING_CROSS', f"MevO: fırsat yok (bekle). {(item or {}).get('reason', '')}".strip(), candle_time=candle_time)
            return 0
        conf = int(item.get('confidence') or 0)
        need = max(MEVO_MIN_CONFIDENCE, int(item.get('min_confidence') or 0))     # MevO kötü gidişte eşiği kendisi yükseltir
        if conf < need:
            status('SKIPPED', f"MevO {item['action']} fırsatı model güveni %{conf} < %{need}; atlandı.", candle_time=candle_time)
            return 0
        blocked = blocking_warnings(item.get('warnings'))
        if blocked:
            status('SKIPPED', f"MevO {item['action']} fırsatı geçmiş zarar uyarısı nedeniyle atlandı: {blocked[0]}", candle_time=candle_time)
            self.log(f"🛑 [{sym} {tf_code(tf)}] MevO fırsatı geçmiş zarar uyarısıyla engellendi: {blocked[0]}", 'WARNING')
            return 0
        self._mevo_levels[key] = {'sl': float(item['sl']), 'tp': float(item['tp']), 'entry': float(item['entry'])}
        self._mevo_last[key] = int(candle_time)
        self.log(f"🧠 [{sym} {tf_code(tf)}] MevO {'AL' if item['action'] == 'BUY' else 'SAT'} fırsatı (model güveni %{conf}): "
                 f"{item.get('reason', '')}", 'SIGNAL')
        return 1 if item['action'] == 'BUY' else -1

    def _mevo_order_points(self, sym, key, sl, tp):
        """SL/TP points from MevO's price levels (distance to its entry); bot values stay when the point is unknown."""
        lv = self._mevo_levels.get(key) or {}
        try:
            point = float((self.mt5.get_symbol_spec(sym) or {}).get('point') or 0)
        except Exception:
            point = 0.0
        if not (math.isfinite(point) and point > 0 and lv):
            return sl, tp, 'MevO seviyeleri çevrilemedi, bot SL/TP ayarı kullanıldı'
        new_sl = int(math.ceil(abs(lv['entry'] - lv['sl']) / point))
        new_tp = int(math.ceil(abs(lv['tp'] - lv['entry']) / point))
        return new_sl, new_tp, f'MevO seviyeleri: SL {new_sl} p, TP {new_tp} p'

    def _manual_style_cross(self, sym, tf, key, candle_time, status):
        """1 / -1 when the manual-style signal (app.manual_style, the same result as the 'Manuel Stil Sinyali'
        card) turns to BUY / SELL on this closed candle, like a new crossover arrow; 0 when it holds;
        None when it cannot be evaluated (status says why). One signal per setup: the same side again only
        after the signal went back to HOLD."""
        provider = self.manual_style_provider
        if provider is None:
            self._ms_retry(key, candle_time)
            status('WAITING_DATA', 'Manuel işlem stili hesaplanamıyor (servis hazır değil).', candle_time=candle_time)
            return None
        try:
            result = provider(sym, tf, candle_time)
        except Exception as exc:
            status('WAITING_DATA', f'Manuel işlem stili hesaplanamadı: {exc}', candle_time=candle_time)
            self._ms_retry(key, candle_time)
            return None
        sig = (result or {}).get('signal')
        if not sig:
            status('WAITING_DATA', (result or {}).get('message') or 'Manuel işlem stili için yeterli karar yok.', candle_time=candle_time)
            return None
        if int(sig.get('candle_time') or 0) != int(candle_time):
            status('WAITING_DATA', 'Manuel işlem stili son kapanmış mumu henüz değerlendirmedi; tekrar denenecek.', candle_time=candle_time)
            self._ms_retry(key, candle_time)
            return None
        side = sig.get('side') if sig.get('side') in ('BUY', 'SELL') else None
        prev = self._ms_prev.get(key)
        self._ms_prev[key] = side
        tag = ' (deneysel)' if sig.get('experimental') else ''
        if not side:
            status('WAITING_CROSS', f"Manuel stil{tag}: sinyal yok (bekle). Benzer durumda manuel işlemlerinin skoru "
                   f"{sig.get('score', 0):.2f}; senin tarzına benzeyen bir AL/SAT kurulumu bekleniyor.", candle_time=candle_time)
            return 0
        if side == prev and self._reentry_allowed(sym, tf, key):
            self.log(f"🧠 [{sym} {tf_code(tf)}] Pozisyon SL/TP ile kapandı; manuel stil {'AL' if side == 'BUY' else 'SAT'} kurulumu sürüyor, yeniden giriliyor.", 'SIGNAL')
            return 1 if side == 'BUY' else -1
        if side == prev:
            status('WAITING_CROSS', f"Manuel stil{tag}: {'AL' if side == 'BUY' else 'SAT'} kurulumu sürüyor; aynı kurulumda yeni işlem açılmaz.",
                   candle_time=candle_time)
            return 0
        self.log(f"🧠 [{sym} {tf_code(tf)}] Manuel stil{tag} {'AL' if side == 'BUY' else 'SAT'} sinyali "
                 f"(skor {sig.get('score', 0):.2f}, benzer kârlı oran {sig.get('win_share', 0):.0%}).", 'SIGNAL')
        return 1 if side == 'BUY' else -1

    def copied_order_settings(self, sym, sl, tp):
        """(lot or None, sl, tp, note): lot / SL / TP of the profitable hand-opened MT5 positions
        (app.manual_copy). Falls back to the bot settings when there is no usable profile."""
        if not self.copy_manual_trades:
            self.manual_copy = None
            return None, sl, tp, ''
        try:
            prof = self.mt5.manual_copy_profile(sym, self.copy_manual_since)
        except Exception as exc:
            prof = {'available': False, 'reason': f'Manuel işlem geçmişi okunamadı ({exc}).'}
        if not isinstance(prof, dict):
            prof = {'available': False, 'reason': 'Manuel işlem profili doğrulanamadı.'}
        if not prof.get('available'):
            self.manual_copy = dict(prof)
            note = f"manuel kopya yok: {prof.get('reason', '')} Bot ayarı kullanıldı."
            return None, sl, tp, note
        if self.copy_lot_override > 0:       # kullanıcı: manuel işlemleri kopyala, yalnız lotu değiştir
            prof = {**prof, 'manual_lot': prof['lot'], 'lot': float(self.copy_lot_override)}
        lot = float(prof['lot'])
        cap = getattr(self.mt5, 'max_order_lots', None)
        capped = ''
        demo_full = bool(self.auto_lot_limits_demo) and getattr(self.mt5, 'account_type', None) == 'DEMO'
        base_total = getattr(self.mt5, 'max_total_lots', None)
        if demo_full and isinstance(base_total, (int, float)) and not isinstance(base_total, bool):
            # Room for one copied lot per timeframe instance on top of the saved total (other positions).
            self.mt5.bot_lot_override = (lot, lot * len(self.instance_timeframes()) + float(base_total))
        else:
            demo_full = False
            if getattr(self.mt5, 'bot_lot_override', None) is not None:
                self.mt5.bot_lot_override = None
        if not demo_full and isinstance(cap, (int, float)) and not isinstance(cap, bool) and math.isfinite(cap) and 0 < cap < lot:
            capped = f' (manuel {lot:g} lot, otomatik bot sınırı {cap:g} lot)'
            lot = float(cap)
        if prof.get('sl_points') and (self.use_stop_loss or sl):
            sl = int(prof['sl_points'])
        if prof.get('tp_points'):
            tp = int(prof['tp_points'])
        self.manual_copy = {**prof, 'applied_lot': lot, 'applied_sl': sl, 'applied_tp': tp, 'capped': bool(capped),
                            'demo_full_lot': demo_full}
        note = (f"manuel kopya ({prof['count']} kârlı pozisyon, {prof['since']} sonrası): {lot:g} lot{capped}, "
                f"SL {sl or 'yok'} p, TP {tp or 'yok'} p")
        if capped:
            self.log(f'⚠️ [{sym}] Kopyalanan lot sınırda tutuldu{capped}.', 'WARNING')
        return lot, sl, tp, note

    def order_distances(self, sym=None):
        target_sym = sym or self.symbol
        if self.risk_mode == "POINTS":
            return (self.sl_points if self.use_stop_loss else 0,
                    self.tp_points if self.use_take_profit else 0)
        try:
            point = float(self.mt5.get_symbol_spec(target_sym)['point'])
            if not math.isfinite(point) or point <= 0 or not math.isfinite(self.current_atr) or self.current_atr <= 0:
                raise ValueError('Geçersiz point/ATR')
            sl = math.ceil(self.current_atr * self.atr_stop_multiplier / point) if self.use_stop_loss else 0
            tp = math.ceil(self.current_atr * self.atr_target_multiplier / point) if self.use_take_profit and self.atr_target_multiplier > 0 else 0
            return sl, tp
        except Exception:
            error = f'{target_sym} ATR stop/hedef mesafesi doğrulanamadı; emir gönderilmedi.'
            self.runtime_state, self.runtime_detail, self.last_error = 'ERROR', error, error
            self.log(error, 'ERROR')
            return None, None

    def close_positions_by_type(self, pos_type: str, sym=None, *, expected_account=None, tf=None):
        """Close the bot's positions of one side; with tf only those of that timeframe instance
        (each MT5 EA instance closes only its own position on a reverse signal)."""
        target_sym = sym or self.symbol
        try:
            positions = self.mt5.get_positions(fresh=True)
        except MT5DataError as exc:
            self.log(str(exc), "ERROR")
            return False
        for p in positions:
            if p["symbol"] == target_sym and p["type"] == pos_type and p.get("magic") == HMA_MAGIC and (tf is None or self._owner_tf(p) == int(tf)):
                self.log(f"Ters sinyal nedeniyle #{p['ticket']} ({pos_type} - {target_sym}) pozisyonu kapatılıyor...")
                if self._stop_event.is_set():
                    return False
                kwargs = {'expected_account': expected_account, 'stop_event': self._stop_event} if expected_account is not None else {}
                result = self.mt5.close_position(p["ticket"], expected_magic=HMA_MAGIC, deviation_points=EA_DEVIATION, **kwargs)
                if result.get('uncertain') or result.get('pending') or result.get('partial'):
                    self.stop()
                    self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', 'Ters pozisyonun kapatılması kesinleşmedi; MT5 durumunu kontrol edin.'
                    return False
                if not result.get("success"):
                    self.runtime_state, self.runtime_detail, self.last_error = 'ORDER_REJECTED', result.get('error'), result.get('error')
                    return False
        return True

    def execute_signal(self, symbol: str, side: str, *, signal_id: str, expected_account,
                       price: Optional[float] = None, sl_price: Optional[float] = None, tp_price: Optional[float] = None,
                       sl_points: Optional[int] = None, tp_points: Optional[int] = None) -> Dict[str, Any]:
        """Manually confirmed signal: MT5 broker rules (like the order ticket), the bot's lot mode,
        account binding and one request id per signal."""
        symbol = symbol.strip()
        side = side.strip().upper()
        if side not in ("BUY", "SELL") or not re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', symbol):
            raise ValueError("Geçersiz sembol veya yön.")
        if (sl_price is not None or tp_price is not None) and (sl_points is not None or tp_points is not None):
            raise ValueError("SL/TP fiyat veya puan olarak gönderilmeli; ikisi birlikte değil.")
        with self._state_lock:
            default_sl = self.sl_points if self.use_stop_loss else 0
            default_tp = self.tp_points if self.use_take_profit else 0
            use_stop_loss = self.use_stop_loss
        if sl_price is not None or tp_price is not None:
            if price is None or not math.isfinite(price) or price <= 0:
                raise ValueError("Fiyat bazlı SL/TP için sinyal fiyatı gerekli.")
            point = float((self.mt5.get_symbol_spec(symbol) or {}).get("point") or 0)
            if not math.isfinite(point) or point <= 0:
                raise ValueError("Sembolün point değeri doğrulanamadı.")
            direction = 1 if side == "BUY" else -1
            if sl_price is not None and direction * (price - sl_price) <= 0:
                raise ValueError("Stop Loss sinyal yönünün yanlış tarafında.")
            if tp_price is not None and direction * (tp_price - price) <= 0:
                raise ValueError("Kâr Al sinyal yönünün yanlış tarafında.")
            sl_pts = int(round(abs(price - sl_price) / point)) if sl_price is not None else default_sl
            tp_pts = int(round(abs(tp_price - price) / point)) if tp_price is not None else default_tp
        else:
            sl_pts = default_sl if sl_points is None else int(sl_points)
            tp_pts = default_tp if tp_points is None else int(tp_points)
        if sl_pts < 0 or tp_pts < 0:
            raise ValueError("SL/TP mesafesi negatif olamaz.")
        if use_stop_loss and sl_pts <= 0:
            raise ValueError("Bot ayarlarında Stop Loss açık; geçerli bir stop mesafesi gerekli.")
        if self.mt5.has_unresolved_bot_order(symbol):
            raise ValueError("Önceki bot emrinin sonucu belirsiz; MT5 mutabakatını bekleyin.")
        # A crossover the bot caught in "Sadece Sinyal" mode, approved by the user, is the bot's own
        # trade: the EA's reverse-signal rule applies (close the opposite bot position first).
        # Scanner "Şimdi AL/SAT" clicks are manual one-click orders and leave other positions alone.
        with self._state_lock:
            close_opposite = self.close_opposite
        if close_opposite and str(signal_id).startswith('bot-'):
            opposite = "SELL" if side == "BUY" else "BUY"
            if not self.close_positions_by_type(opposite, sym=symbol, expected_account=expected_account):
                raise ValueError("Ters yöndeki bot pozisyonu kapatılamadı; yeni emir gönderilmedi.")
        lot, risk_money, lot_error = self.order_lot(symbol, side, sl_pts, expected_account)
        if lot is None:
            self.record_signal(source='MANUAL', symbol=symbol, side=side, lot=None, sl_points=sl_pts, tp_points=tp_pts,
                               signal_id=signal_id, result={'success': False, 'error': lot_error})
            raise ValueError(lot_error)
        identity = (tuple(expected_account), symbol, side, signal_id)
        request_id = "sig-" + hashlib.sha256(repr(identity).encode()).hexdigest()
        bot_trade = str(signal_id).startswith('bot-')
        # A signal the bot caught and the user approved is the bot's own trade (bot magic, EA deviation).
        # "Şimdi AL/SAT" from the scanner is a manual one-click order: like a trade opened by hand in MT5,
        # the EA does not manage it (manual magic) and it never blocks or gets closed by bot signals.
        extra = {'deviation_points': EA_DEVIATION} if bot_trade else {}
        res = self.mt5.open_order(symbol, side, lot, sl_points=sl_pts, tp_points=tp_pts,
                                  comment=f"HMA Sinyal {side}" if bot_trade else f"Web Sinyal {side}",
                                  magic=HMA_MAGIC if bot_trade else MANUAL_MAGIC, **extra,
                                  request_id=request_id, expected_account=expected_account,
                                  # Manually confirmed: same broker-only rules as the MT5 order ticket.
                                  manual_mode="MT5")
        self.record_signal(source='MANUAL', symbol=symbol, side=side, lot=lot, result=res,
                           sl_points=sl_pts, tp_points=tp_pts, signal_id=signal_id, risk_money=risk_money)
        if res.get("success"):
            self.log(f"🎯 Sinyal onaylandı ve açıldı: {symbol} {side} #{res.get('ticket')} @ {res.get('price')} (Lot: {lot})")
            with self._state_lock:
                self.pending_signal_actions = [a for a in self.pending_signal_actions
                                               if not (a.get('symbol') == symbol and a.get('side') == side)]
                self.pending_signal_action = self.pending_signal_actions[0] if self.pending_signal_actions else None
        else:
            self.log(f"❌ Sinyal emri açılamadı ({symbol} {side}): {res.get('error')}", "ERROR")
        return res

    def clear_pending_signal(self, symbol: Optional[str] = None):
        with self._state_lock:
            if symbol:
                sym = symbol.strip()
                self.pending_signal_actions = [a for a in getattr(self, 'pending_signal_actions', []) if a.get('symbol') != sym]
            else:
                self.pending_signal_actions = []
            self.pending_signal_action = self.pending_signal_actions[0] if self.pending_signal_actions else None

    def order_id(self, direction, sym=None, tf=None):
        target_sym = sym or self.symbol
        tf = int(tf or self.timeframe_minutes)
        key = self._instance_key(target_sym, tf)
        last_t = self.last_candle_times.get(key, self.last_candle_time if key == target_sym else 0)
        # One order per signal within a run; a restart / re-init is a new EA run (MT5 OnInit), so a signal on the
        # same candle after a restart gets a new id instead of colliding with the earlier run's journal entry.
        identity = (self.mt5.login_id, self.mt5.server, target_sym, tf,
                    last_t, direction) + ((self._run_nonce,) if self._run_nonce else ())
        return "hma-" + hashlib.sha256(repr(identity).encode()).hexdigest()

    def start(self, expected_config=None):
        with self._state_lock:
            if expected_config is not None and self.config_snapshot() != expected_config:
                raise RuntimeError('Hazırlık kontrolü sırasında bot ayarları değişti; bot başlatılmadı.')
            if not self.is_running and not self._check_lock.locked() and (self.task is None or self.task.done()):
                self.mt5.automation_stopped.clear()
                self._stop_event.clear()
                self.last_candle_time = 0
                self.last_candle_times = {}
                self._candidates = {}
                self._trend_pending = {}
                self._ms_prev = {}
                self._mevo_levels, self._mevo_last = {}, {}
                self.started_at = int(time.time())
                self._run_nonce = time.time_ns()
                self._reentry_for = {}
                self.symbol_status = {}
                self._loss_check_at = 0.0
                self._target_check_at, self._session_realized, self.session_pnl = 0.0, 0.0, None
                self.is_running = True
                self.runtime_state, self.runtime_detail, self.last_error = 'WAITING_DATA', 'Yeni kapanmış mum verisi bekleniyor.', None
                self.task = asyncio.create_task(self.run_loop())

    def stop(self):
        with self._state_lock:
            self._stop_event.set()
            self._candidate = None
            self.runtime_state, self.runtime_detail = 'STOPPED', 'Bot durduruldu.'
            if self.is_running:
                self.is_running = False
                # Do not cancel a to_thread worker: it cannot be interrupted, and a
                # new start must wait until that worker has finished.
                self.log("🛑 HMA Kesişim Botu Durduruldu")
