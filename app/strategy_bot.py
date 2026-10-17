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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.mt5_bridge import HMA_MAGIC
from app.mt5_client import MT5DataError, TIMEFRAME_NAMES
from app.strategy_research import Settings as IndicatorSettings, indicators

logger = logging.getLogger("StrategyBot")
logger.setLevel(logging.INFO)

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
        self.lot_size = 0.01
        self.use_stop_loss = True
        self.sl_points = 200
        self.use_take_profit = False
        self.tp_points = 400
        self.close_opposite = True
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
        # Signals sent to the bot (manual "Bota Gönder"/"Uygula" and automatic orders), newest first.
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
            rows = bot_performance.closed_positions(deals, self.signal_history, open_ids)
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

    def load_config(self, data):
        self.update_config(data, persist=False)

    def config_snapshot(self):
        keys = ('symbol','timeframe_minutes','hma_period','second_ma_type','second_ma_period',
                'kama_fast','kama_slow','atr_period','use_atr_filter','use_h4_filter','single_position',
                'er_min','confirmation_bars','min_distance_atr','risk_mode','atr_stop_multiplier',
                'atr_target_multiplier','lot_size','use_stop_loss','sl_points','use_take_profit','tp_points','close_opposite',
                'lot_mode','risk_pct','max_consecutive_losses','trade_hours_enabled','trade_start','trade_end',
                'trade_utc_offset_min','trade_days','blocked_windows')
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
                    return [r for r in rows if isinstance(r, dict)][:self.HISTORY_LIMIT]
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
            'time': int(time.time()), 'source': source, 'symbol': symbol, 'side': side, 'lot': number(lot),
            'sl_points': int(sl_points or 0), 'tp_points': int(tp_points or 0), 'signal_id': signal_id,
            'success': bool(result.get('success')), 'ticket': result.get('ticket'), 'price': number(result.get('price')),
            'pending': bool(result.get('pending') or result.get('uncertain')),
            'risk_money': number(risk_money),
            'error': None if result.get('success') else str(result.get('error') or 'Emir gönderilemedi.')[:240],
        }
        with self._state_lock:
            self.signal_history.insert(0, entry)
            del self.signal_history[self.HISTORY_LIMIT:]
            self._persist_history()
        return entry

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
        with self._state_lock:
            if self.is_running or self._check_lock.locked() or (self.task is not None and not self.task.done()):
                raise RuntimeError('Sembol listesini değiştirmeden önce botu durdurun; yeni semboller başlangıç kontrolünden geçmelidir.')
            sym = sym.strip()
            if not re.fullmatch(r'[A-Za-z0-9_.#-]{1,32}', sym):
                raise ValueError("Geçersiz sembol.")
            if not hasattr(self, 'symbols') or not self.symbols:
                self.symbols = [self.symbol]
            if sym not in self.symbols:
                self.symbols.append(sym)
                self.log(f"➕ Bot izleme listesine eklendi: {sym} (Aktif: {', '.join(self.symbols)})")
                self.persist_config(self.config_snapshot())
            return list(self.symbols)

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
            candidate = self.config_snapshot()
            if not isinstance(data, dict) or set(data) - set(candidate):
                raise ValueError('Bilinmeyen bot ayarı.')
            candidate.update(data)
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
            for key in ('use_stop_loss', 'use_take_profit', 'close_opposite', 'use_atr_filter', 'use_h4_filter', 'single_position'):
                if not isinstance(candidate[key], bool):
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
            if persist:
                self.persist_config(candidate)
            for key, value in candidate.items():
                setattr(self, key, value)
            self._candidate = None
            self.current_atr = self.current_er = 0.0
            self.last_candle_time = 0
            self.last_candle_times = {}
        self.log(f"Bot ayarları güncellendi: {', '.join(self.symbols)} | HMA: {self.hma_period} | 2.MA: {self.second_ma_type}({self.second_ma_period}) | Lot: {self.lot_size}")

    def _sym_status(self, sym, state, detail, **extra):
        with self._state_lock:
            row = self.symbol_status.setdefault(sym, {})
            row.update(extra)
            row.update(state=state, detail=detail, updated=int(time.time()), timeframe_minutes=self.timeframe_minutes)

    def _bars_since_cross(self, closes, limit=60):
        """(bars since last HMA x MA crossover on closed candles, direction 1/-1) or (None, 0)."""
        try:
            for shift in range(1, limit):
                h1, h2 = self.calc_hma(closes, self.hma_period, shift=shift), self.calc_hma(closes, self.hma_period, shift=shift + 1)
                m1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=shift)
                m2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=shift + 1)
                if not all(math.isfinite(v) and v > 0 for v in (h1, h2, m1, m2)):
                    return None, 0
                if h2 <= m2 and h1 > m1:
                    return shift - 1, 1
                if h2 >= m2 and h1 < m1:
                    return shift - 1, -1
        except Exception:
            pass
        return None, 0

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
            **{key: (list(getattr(self, key)) if isinstance(getattr(self, key), list) else getattr(self, key)) for key in (
                "lot_mode", "risk_pct", "max_consecutive_losses", "trade_hours_enabled", "trade_start", "trade_end",
                "trade_utc_offset_min", "trade_days", "blocked_windows")},
            "trading_window_issue": self._safe_window_issue(),
            "start_warnings": list(self.start_warnings) if self.is_running else [],
            "execution_mode": self.execution_mode,
            "max_order_lots": (lambda cap: cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) else None)(getattr(self.mt5, "max_order_lots", None)),
            "max_total_lots": (lambda cap: cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) else None)(getattr(self.mt5, "max_total_lots", None)),
            "symbol_status": {sym: dict(self.symbol_status.get(sym, {})) for sym in getattr(self, "symbols", [self.symbol])},
            "last_signal": self.last_signal,
            "last_signal_time": self.last_signal_time,
            "last_signal_price": self.last_signal_price,
            "current_hma": round(self.current_hma, 5),
            "current_ma2": round(self.current_ma2, 5),
            "pending_signal_actions": getattr(self, "pending_signal_actions", []),
            "logs": self.logs[:15],
            "signal_history": self.signal_history[:20]
        }

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
        else: # Default: EMA
            k = 2.0 / (period + 1.0)
            # EMA over full history up to shift
            history = close_prices[shift:]
            history = list(reversed(history)) # chronological
            if len(history) < period:
                return 0.0
            ema = sum(history[:period]) / period
            for price in history[period:]:
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
        return round(100.0 - (100.0 / (1.0 + rs)), 1)

    def live_preview(self) -> Dict[str, Any]:
        try:
            rates = self.mt5.get_rates(self.symbol, timeframe=self.timeframe_minutes,
                                       count=max(100, 5 * max(self.hma_period, self.second_ma_period, self.kama_slow, self.atr_period) + 20))
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
                hma1 = self.calc_hma(rev_closes, self.hma_period, shift=1)
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
            if self._stop_event.is_set():
                break
            self._check_strategy_for_symbol(sym, expected_account=expected_account)

    def _check_strategy_for_symbol(self, sym: str, expected_account=None):
        rates = self.mt5.get_rates(sym, timeframe=self.timeframe_minutes,
                                   count=max(100, 5 * max(self.hma_period, self.second_ma_period, self.kama_slow, self.atr_period) + 20))
        if self._stop_event.is_set():
            return
        required = max(self.hma_period + int(math.sqrt(self.hma_period)) + 1,
                       self.second_ma_period + (int(math.sqrt(self.second_ma_period)) + 1 if self.second_ma_type == 'HMA' else 2))
        if not rates or len(rates) < max(required, max(self.hma_period, self.second_ma_period) + 15):
            self._sym_status(sym, 'WAITING_DATA', 'Yeterli kapanmış mum verisi yok (piyasa kapalı olabilir).')
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', f'{sym}: Yeterli kapanmış mum verisi bekleniyor.'
            return
        if any(not isinstance(r.get('time'), (int, float)) or not math.isfinite(r['time']) or
               not isinstance(r.get('close'), (int, float)) or not math.isfinite(r['close']) or r['close'] <= 0 for r in rates) or any(
               rates[i]['time'] >= rates[i+1]['time'] for i in range(len(rates)-1)):
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', f'{sym}: Mum verisi sıralı ve geçerli değil.'
                self._candidate = None
            return

        rev_rates = list(reversed(rates))
        closes = [r["close"] for r in rev_rates]
        latest_closed_candle_time = rev_rates[1]["time"]

        last_t = self.last_candle_times.get(sym, 0)
        if sym == self.symbol:
            # The single-symbol marker is still reset/advanced by older code paths;
            # never process a candle older than either marker.
            last_t = max(last_t, self.last_candle_time or 0)
        if self.is_running and not last_t:
            hma1_init = self.calc_hma(closes, self.hma_period, shift=1)
            hma2_init = self.calc_hma(closes, self.hma_period, shift=2)
            ma2_1_init = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)
            ma2_2_init = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)
            fresh_cross_on_last_bar = (
                self.second_ma_type != "KAMA"
                and not self.use_atr_filter
                and ((hma2_init <= ma2_2_init and hma1_init > ma2_1_init) or (hma2_init >= ma2_2_init and hma1_init < ma2_1_init))
            )
            if not fresh_cross_on_last_bar:
                bars, way = self._bars_since_cross(closes)
                trend = 'AL' if hma1_init > ma2_1_init else 'SAT'
                since = f'son kesişim {bars} mum önce' if bars is not None else 'yakın zamanda kesişim yok'
                self._sym_status(sym, 'WAITING_CROSS',
                    f"Mevcut {trend} trendine geç girilmez ({since}); bir sonraki kesişim bekleniyor: "
                    f"{'SAT (aşağı)' if trend == 'AL' else 'AL (yukarı)'}.", candle_time=latest_closed_candle_time, trend=trend)
                self.last_candle_times[sym] = latest_closed_candle_time
                if sym == self.symbol:
                    self.last_candle_time = latest_closed_candle_time
                    self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', 'Yeni kapanmış mumdaki sinyal bekleniyor.'
                return

        if latest_closed_candle_time <= last_t:
            return

        study = None
        current_atr, current_er = 0.0, 0.0
        if self.second_ma_type == "KAMA" or self.use_atr_filter or self.risk_mode == "ATR":
            closed = rates[:-1]
            study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
                kama_period=self.second_ma_period, kama_fast=self.kama_fast,
                kama_slow=self.kama_slow, atr_period=self.atr_period))
            if any(study[key][-1] is None or study[key][-2] is None for key in ('hma', 'kama', 'atr', 'er')):
                if sym == self.symbol:
                    self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge hesaplaması için kapanmış mum bekleniyor.'
                return
            current_atr, current_er = study['atr'][-1], study['er'][-1]
            if sym == self.symbol:
                self.current_atr, self.current_er = current_atr, current_er

        if self.second_ma_type == "KAMA":
            hma1, hma2 = study['hma'][-1], study['hma'][-2]
            ma2_1, ma2_2 = study['kama'][-1], study['kama'][-2]
        else:
            hma1 = self.calc_hma(closes, self.hma_period, shift=1)
            hma2 = self.calc_hma(closes, self.hma_period, shift=2)
            ma2_1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)
            ma2_2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)

        if any(not math.isfinite(value) for value in (hma1, hma2, ma2_1, ma2_2)):
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge değerleri doğrulanamadı.'
            return

        rsi = self.calc_rsi([float(r["close"]) for r in rates[:-1]], 14)
        if sym == self.symbol:
            self.current_hma, self.current_ma2 = hma1, ma2_1
            self.current_rsi = rsi
            self.runtime_state, self.runtime_detail, self.last_error = 'WAITING_SIGNAL', 'Kesişim sinyali bekleniyor.', None
            self.last_candle_time = latest_closed_candle_time

        self.last_candle_times[sym] = latest_closed_candle_time
        cross = 1 if hma2 <= ma2_2 and hma1 > ma2_1 else -1 if hma2 >= ma2_2 and hma1 < ma2_1 else 0
        direction = cross
        closed_on_cross = False
        if not cross:
            trend = 'AL' if hma1 > ma2_1 else 'SAT'
            bars, _ = self._bars_since_cross(closes)
            since = f'son kesişim {bars} mum önce' if bars is not None else 'yakın zamanda kesişim yok'
            self._sym_status(sym, 'WAITING_CROSS', f"Kesişim yok; {trend} trendi sürüyor ({since}). "
                             f"Bot yeni kesişimde işlem açar: {'SAT (aşağı)' if trend == 'AL' else 'AL (yukarı)'} bekleniyor.",
                             candle_time=latest_closed_candle_time, trend=trend)

        if self.use_atr_filter:
            direction = 0
            if cross:
                self._candidates[sym] = {'side': cross, 'time': latest_closed_candle_time}
                if sym == self.symbol:
                    self._candidate = self._candidates[sym]
                if self.close_opposite:
                    if not self.close_positions_by_type("SELL" if cross == 1 else "BUY", sym=sym, expected_account=expected_account):
                        self._candidates.pop(sym, None)
                        if sym == self.symbol:
                            self._candidate = None
                        self.log(f"[{sym}] Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
                        return
                    closed_on_cross = True

            cand = self._candidates.get(sym)
            if cand:
                side = cand['side']
                age = sum(r['time'] > cand['time'] for r in rates[:-1])
                valid = (current_atr > 0 and current_er >= self.er_min and
                         side * (hma1 - ma2_1) > 0 and
                         side * (hma1 - ma2_1) >= self.min_distance_atr * current_atr and
                         side * (hma1 - hma2) > 0 and side * (ma2_1 - ma2_2) > 0)
                if valid and self.use_h4_filter:
                    valid = self.h4_confirmation(side, latest_closed_candle_time + self.timeframe_minutes * 60, sym=sym)
                if age > self.confirmation_bars:
                    self._sym_status(sym, 'SKIPPED', 'Kesişim oldu ama onay süresi doldu; sinyal iptal edildi (filtre).')
                    self.log(f"[{sym}] Onay süresi doldu; kesişim sinyali iptal edildi.")
                    self._candidates.pop(sym, None)
                    if sym == self.symbol:
                        self._candidate = None
                elif valid:
                    direction = side
                    self._candidates.pop(sym, None)
                    if sym == self.symbol:
                        self._candidate = None
                elif age == self.confirmation_bars:
                    self._sym_status(sym, 'SKIPPED', 'Kesişim oldu ama ER/eğim/ATR onayı gelmedi; sinyal iptal edildi (filtre).')
                    self.log(f"[{sym}] ER/eğim/ATR mesafesi onayı gelmedi; sinyal iptal edildi.")
                    self._candidates.pop(sym, None)
                    if sym == self.symbol:
                        self._candidate = None
                else:
                    self._sym_status(sym, 'WAITING_CONFIRMATION', f'Kesişim var; ER/eğim/ATR onayı bekleniyor ({age}/{self.confirmation_bars} mum).')
                    if sym == self.symbol:
                        self.runtime_state, self.runtime_detail = 'WAITING_CONFIRMATION', 'ER/eğim/ATR mesafesi onayı bekleniyor.'
                    self.log(f"[{sym}] Kesişim onayı bekleniyor: {age}/{self.confirmation_bars} mum.")

        if not direction or self._stop_event.is_set():
            return

        side = "BUY" if direction == 1 else "SELL"
        if self.use_rsi_filter:
            if side == "BUY" and rsi > self.rsi_buy_max:
                self._sym_status(sym, 'SKIPPED', f'AL kesişimi RSI filtresine takıldı ({rsi:.1f} > {self.rsi_buy_max}).')
                self.log(f"⚠️ [{sym}] RSI aşırı alımda ({rsi:.1f} > {self.rsi_buy_max}); AL sinyali filtrelendi.")
                return
            if side == "SELL" and rsi < self.rsi_sell_min:
                self._sym_status(sym, 'SKIPPED', f'SAT kesişimi RSI filtresine takıldı ({rsi:.1f} < {self.rsi_sell_min}).')
                self.log(f"⚠️ [{sym}] RSI aşırı satımda ({rsi:.1f} < {self.rsi_sell_min}); SAT sinyali filtrelendi.")
                return

        if self.max_spread_points > 0:
            try:
                quote = self.mt5.get_symbol_price(sym) or {}
                spread = float(quote.get("spread") or 0)
                if spread > self.max_spread_points:
                    self._sym_status(sym, 'SKIPPED', f'{side} kesişimi yüksek spread nedeniyle atlandı ({spread:.0f} > {self.max_spread_points} puan).')
                    self.log(f"⚠️ [{sym}] Spread yüksek ({spread:.0f} > {self.max_spread_points} puan); {side} sinyali atlandı.")
                    return
            except Exception:
                pass

        sl, tp = self.order_distances(sym)
        if sl is None:
            self._sym_status(sym, 'SKIPPED', f'{side} kesişimi: ATR stop/hedef mesafesi hesaplanamadı.')
            return

        if sym == self.symbol:
            self.last_signal = side
            self.last_signal_time = time.strftime("%H:%M:%S")
            self.last_signal_price = closes[1]

        self.log(f"[{sym} {side} SİNYALİ] HMA({hma1:.5f}) / {self.second_ma_type}({ma2_1:.5f}) | RSI: {rsi:.1f}", "SIGNAL")

        if self.execution_mode == "SIGNAL_ONLY":
            action = {
                "id": f"{sym}-{side}-{int(time.time())}",
                "symbol": sym,
                "side": side,
                "price": closes[1],
                "sl_points": sl,
                "tp_points": tp,
                "lot_size": self.lot_size,
                "time": time.strftime("%H:%M:%S"),
                "rsi": round(rsi, 1),
            }
            curr_actions = getattr(self, 'pending_signal_actions', [])
            self.pending_signal_actions = [a for a in curr_actions if a.get('symbol') != sym]
            self.pending_signal_actions.insert(0, action)
            if len(self.pending_signal_actions) > 20:
                self.pending_signal_actions.pop()
            self.pending_signal_action = action
            self._sym_status(sym, 'AWAITING_APPROVAL', f'{side} sinyali yakalandı; "Sadece Sinyal" modunda onayınız bekleniyor.')
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', f'{sym} {side} sinyali yakalandı; onayınız bekleniyor.'
            self.log(f"🔔 [{sym} {side} SİNYALİ YAKALANDI · ONAY BEKLİYOR] Fiyat: {closes[1]:.5f} (Sadece Sinyal Modu)", "SIGNAL")
            return

        if self.close_opposite and not closed_on_cross and not self.close_positions_by_type("SELL" if direction == 1 else "BUY", sym=sym, expected_account=expected_account):
            self._sym_status(sym, 'SKIPPED', f'{side} kesişimi: ters bot pozisyonu kapatılamadı, yeni emir gönderilmedi.')
            self.log(f"[{sym}] Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
            return

        if self._stop_event.is_set():
            return

        if self.mt5.has_unresolved_bot_order(sym):
            self.log(f'[{sym}] Önceki bot emri belirsiz; MT5 mutabakatı bekleniyor.', 'ERROR')
            self.stop()
            self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', f'{sym} bot emri belirsiz; MT5 mutabakatı bekleniyor.'
            return

        if self.single_position:
            owned = self.mt5.get_positions(fresh=True)
            pending = self.mt5.get_pending_orders()
            if any(p['symbol'] == sym and p.get('magic') == HMA_MAGIC for p in owned + pending):
                self._sym_status(sym, 'SKIPPED', f'{side} kesişimi atlandı: bu sembolde zaten bir bot pozisyonu açık '
                                 '("Bota Gönder" ile açılanlar dahil). Aynı sembolde tek bot pozisyonu tutulur.')
                self.log(f'Bot {sym} pozisyonu zaten açık; yeni giriş atlandı.')
                return

        issue = self.trading_window_issue()
        if issue:
            self._sym_status(sym, 'SKIPPED', f'{side} kesişimi koruma kuralına takıldı: {issue}')
            self.log(f'⏸️ [{sym} {side}] {issue} Yeni giriş yapılmadı.')
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'OUTSIDE_HOURS', issue
            return

        lot, risk_money, lot_error = self.order_lot(sym, side, sl, expected_account,
                                                    cap=getattr(self.mt5, 'max_order_lots', None))
        if lot is None:
            self._sym_status(sym, 'SKIPPED', f'{side} kesişimi: {lot_error}')
            self.log(f'⚠️ [{sym} {side}] {lot_error}', 'ERROR')
            self.record_signal(source='AUTO', symbol=sym, side=side, lot=None, sl_points=sl, tp_points=tp,
                               result={'success': False, 'error': lot_error})
            return

        kwargs = {'expected_account': expected_account} if expected_account is not None else {}
        if sym == self.symbol:
            self.runtime_state, self.runtime_detail = 'SUBMITTING', f'{sym} emir sonucu bekleniyor.'

        res = self.mt5.open_order(sym, side, lot, sl_points=sl, tp_points=tp,
            comment=f"HMA Bot {side}", magic=HMA_MAGIC, request_id=self.order_id(side, sym=sym), stop_event=self._stop_event, **kwargs)
        self.record_signal(source='AUTO', symbol=sym, side=side, lot=lot, result=res, sl_points=sl, tp_points=tp,
                           risk_money=risk_money)

        if res.get("success"):
            self._sym_status(sym, 'ORDER_OPENED', f"{side} emri açıldı: #{res.get('ticket')} @ {res.get('price')} ({lot} lot).")
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', f'{sym} emir kabul edildi; yeni sinyal bekleniyor.'
            self.log(f"✅ {sym} {side} Emri Açıldı: #{res.get('ticket')} @ {res.get('price')}")
        else:
            self._sym_status(sym, 'REJECTED', f"{side} emri reddedildi: {res.get('error')}")
            if sym == self.symbol:
                self.runtime_state, self.runtime_detail, self.last_error = 'ORDER_REJECTED', res.get('error'), res.get('error')
            self.log(f"❌ {sym} {side} Emri Başarısız: {res.get('error')}", "ERROR")

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

    def close_positions_by_type(self, pos_type: str, sym=None, *, expected_account=None):
        target_sym = sym or self.symbol
        try:
            positions = self.mt5.get_positions(fresh=True)
        except MT5DataError as exc:
            self.log(str(exc), "ERROR")
            return False
        for p in positions:
            if p["symbol"] == target_sym and p["type"] == pos_type and p.get("magic") == HMA_MAGIC:
                self.log(f"Ters sinyal nedeniyle #{p['ticket']} ({pos_type} - {target_sym}) pozisyonu kapatılıyor...")
                if self._stop_event.is_set():
                    return False
                kwargs = {'expected_account': expected_account, 'stop_event': self._stop_event} if expected_account is not None else {}
                result = self.mt5.close_position(p["ticket"], expected_magic=HMA_MAGIC, **kwargs)
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
        lot, risk_money, lot_error = self.order_lot(symbol, side, sl_pts, expected_account)
        if lot is None:
            self.record_signal(source='MANUAL', symbol=symbol, side=side, lot=None, sl_points=sl_pts, tp_points=tp_pts,
                               signal_id=signal_id, result={'success': False, 'error': lot_error})
            raise ValueError(lot_error)
        identity = (tuple(expected_account), symbol, side, signal_id)
        request_id = "sig-" + hashlib.sha256(repr(identity).encode()).hexdigest()
        res = self.mt5.open_order(symbol, side, lot, sl_points=sl_pts, tp_points=tp_pts,
                                  comment=f"HMA Sinyal {side}", magic=HMA_MAGIC,
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

    def order_id(self, direction, sym=None):
        target_sym = sym or self.symbol
        last_t = self.last_candle_times.get(target_sym, self.last_candle_time)
        identity = (self.mt5.login_id, self.mt5.server, target_sym, self.timeframe_minutes,
                    last_t, direction)
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
                self.started_at = int(time.time())
                self.symbol_status = {}
                self._loss_check_at = 0.0
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
