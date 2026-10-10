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
        self.lot_size = 0.01
        self.use_stop_loss = True
        self.sl_points = 200
        self.use_take_profit = False
        self.tp_points = 400
        self.close_opposite = True

        # State tracking
        self.last_candle_time = 0
        self.last_signal = "YOK"
        self.last_signal_time = ""
        self.last_signal_price = 0.0
        self.current_hma = 0.0
        self.current_ma2 = 0.0
        self.logs: List[Dict[str, Any]] = []
        self.config_path = Path(config_path) if config_path else None

    def load_config(self, data):
        self.update_config(data, persist=False)

    def config_snapshot(self):
        keys = ('symbol','timeframe_minutes','hma_period','second_ma_type','second_ma_period',
                'kama_fast','kama_slow','atr_period','use_atr_filter','use_h4_filter','single_position',
                'er_min','confirmation_bars','min_distance_atr','risk_mode','atr_stop_multiplier',
                'atr_target_multiplier','lot_size','use_stop_loss','sl_points','use_take_profit','tp_points','close_opposite')
        with self._state_lock:
            return {key:getattr(self,key) for key in keys}

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

    def log(self, message: str, level: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message, "level": level}
        self.logs.insert(0, entry)
        if len(self.logs) > 50:
            self.logs.pop()
        logger.info(f"[{level}] {message}")

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
                candidate[key] = candidate[key].upper()
            if not re.fullmatch(r'[A-Z0-9_.#-]{1,32}', candidate['symbol']):
                raise ValueError('Geçersiz sembol.')
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
            if persist:
                self.persist_config(candidate)
            for key, value in candidate.items():
                setattr(self, key, value)
            self._candidate = None
            self.current_atr = self.current_er = 0.0
            self.last_candle_time = 0
        self.log(f"Bot ayarları güncellendi: {self.symbol} | HMA: {self.hma_period} | 2.MA: {self.second_ma_type}({self.second_ma_period}) | Lot: {self.lot_size}")

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
            "last_signal": self.last_signal,
            "last_signal_time": self.last_signal_time,
            "last_signal_price": self.last_signal_price,
            "current_hma": round(self.current_hma, 5),
            "current_ma2": round(self.current_ma2, 5),
            "logs": self.logs[:15]
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

    async def run_loop(self):
        self.log(f"🚀 HMA Kesişim Botu Başlatıldı ({self.symbol} M{self.timeframe_minutes})")

        while self.is_running:
            try:
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
            return

        rates = self.mt5.get_rates(self.symbol, timeframe=self.timeframe_minutes,
                                   count=max(100, 5 * max(self.hma_period, self.second_ma_period, self.kama_slow, self.atr_period) + 20))
        if self._stop_event.is_set():
            return
        required = max(self.hma_period + int(math.sqrt(self.hma_period)) + 1,
                       self.second_ma_period + (int(math.sqrt(self.second_ma_period)) + 1 if self.second_ma_type == 'HMA' else 2))
        if not rates or len(rates) < max(required, max(self.hma_period, self.second_ma_period) + 15):
            self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Yeterli kapanmış mum verisi bekleniyor.'
            return
        if any(not isinstance(r.get('time'), (int, float)) or not math.isfinite(r['time']) or
               not isinstance(r.get('close'), (int, float)) or not math.isfinite(r['close']) or r['close'] <= 0 for r in rates) or any(
               rates[i]['time'] >= rates[i+1]['time'] for i in range(len(rates)-1)):
            self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Mum verisi sıralı ve geçerli değil.'
            self._candidate = None
            return

        # rates are in chronological order, latest is last
        # We need latest closed bar (shift=1) and previous closed bar (shift=2)
        # Convert to reverse order (index 0 = current open candle, 1 = last closed candle, 2 = previous closed)
        rev_rates = list(reversed(rates))
        closes = [r["close"] for r in rev_rates]

        latest_closed_candle_time = rev_rates[1]["time"]
        if self.is_running and not self.last_candle_time:
            # Starting/restarting seeds the current bar; do not trade an old crossover.
            self.last_candle_time = latest_closed_candle_time
            self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', 'Yeni kapanmış mumdaki sinyal bekleniyor.'
            return
        if latest_closed_candle_time <= self.last_candle_time:
            return

        study = None
        if self.second_ma_type == "KAMA" or self.use_atr_filter or self.risk_mode == "ATR":
            closed = rates[:-1]
            study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
                kama_period=self.second_ma_period, kama_fast=self.kama_fast,
                kama_slow=self.kama_slow, atr_period=self.atr_period))
            if any(study[key][-1] is None or study[key][-2] is None for key in ('hma', 'kama', 'atr', 'er')):
                self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge hesaplaması için kapanmış mum bekleniyor.'
                return
            self.current_atr, self.current_er = study['atr'][-1], study['er'][-1]
        if self.second_ma_type == "KAMA":
            hma1, hma2 = study['hma'][-1], study['hma'][-2]
            ma2_1, ma2_2 = study['kama'][-1], study['kama'][-2]
        else:
            hma1 = self.calc_hma(closes, self.hma_period, shift=1)
            hma2 = self.calc_hma(closes, self.hma_period, shift=2)
            ma2_1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)
            ma2_2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)
        if any(not math.isfinite(value) for value in (hma1, hma2, ma2_1, ma2_2)):
            self.runtime_state, self.runtime_detail = 'WAITING_DATA', 'Gösterge değerleri doğrulanamadı.'
            return
        self.current_hma, self.current_ma2 = hma1, ma2_1
        self.runtime_state, self.runtime_detail, self.last_error = 'WAITING_SIGNAL', 'Kesişim sinyali bekleniyor.', None
        self.last_candle_time = latest_closed_candle_time
        cross = 1 if hma2 <= ma2_2 and hma1 > ma2_1 else -1 if hma2 >= ma2_2 and hma1 < ma2_1 else 0
        direction = cross
        closed_on_cross = False
        if self.use_atr_filter:
            direction = 0
            if cross:
                self._candidate = {'side': cross, 'time': latest_closed_candle_time}
                # A raw reverse closes owned positions even when entry confirmation fails.
                if self.close_opposite:
                    if not self.close_positions_by_type("SELL" if cross == 1 else "BUY", expected_account=expected_account):
                        self._candidate = None
                        self.log("Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
                        return
                    closed_on_cross = True
            if self._candidate:
                side = self._candidate['side']
                age = sum(r['time'] > self._candidate['time'] for r in rates[:-1])
                valid = (self.current_atr > 0 and self.current_er >= self.er_min and
                         side * (hma1 - ma2_1) > 0 and
                         side * (hma1 - ma2_1) >= self.min_distance_atr * self.current_atr and
                         side * (hma1 - hma2) > 0 and side * (ma2_1 - ma2_2) > 0)
                if valid and self.use_h4_filter:
                    valid = self.h4_confirmation(side, latest_closed_candle_time + self.timeframe_minutes*60)
                if age > self.confirmation_bars:
                    self.log("Onay süresi doldu; kesişim sinyali iptal edildi.")
                    self._candidate = None
                elif valid:
                    direction = side
                    self._candidate = None
                elif age == self.confirmation_bars:
                    self.log("ER/eğim/ATR mesafesi onayı gelmedi; sinyal iptal edildi.")
                    self._candidate = None
                else:
                    self.runtime_state, self.runtime_detail = 'WAITING_CONFIRMATION', 'ER/eğim/ATR mesafesi onayı bekleniyor.'
                    self.log(f"Kesişim onayı bekleniyor: {age}/{self.confirmation_bars} mum.")
        if not direction or self._stop_event.is_set():
            return
        side = "BUY" if direction == 1 else "SELL"
        sl, tp = self.order_distances()
        if sl is None:
            return
        self.last_signal = side
        self.last_signal_time = time.strftime("%H:%M:%S")
        self.last_signal_price = closes[1]
        self.log(f"[{side} SİNYALİ] HMA({hma1:.5f}) / {self.second_ma_type}({ma2_1:.5f}) | ATR: {self.current_atr:.5f}", "SIGNAL")
        if self.close_opposite and not closed_on_cross and not self.close_positions_by_type("SELL" if direction == 1 else "BUY", expected_account=expected_account):
            self.log("Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
            return
        if self._stop_event.is_set():
            return
        if self.mt5.has_unresolved_bot_order(self.symbol):
            self.log('Önceki bot emri belirsiz; MT5 mutabakatı bekleniyor.', 'ERROR')
            self.stop()
            self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', 'Önceki bot emri belirsiz; MT5 mutabakatı bekleniyor.'
            return
        if self.single_position:
            owned = self.mt5.get_positions(fresh=True)
            pending = self.mt5.get_pending_orders()
            if any(p['symbol'] == self.symbol and p.get('magic') == HMA_MAGIC for p in owned+pending):
                self.log('Bot pozisyonu zaten açık; yeni giriş atlandı.')
                return
        kwargs = {'expected_account': expected_account} if expected_account is not None else {}
        self.runtime_state, self.runtime_detail = 'SUBMITTING', 'Emir sonucu bekleniyor.'
        res = self.mt5.open_order(self.symbol, side, self.lot_size, sl_points=sl, tp_points=tp,
            comment=f"HMA Bot {side}", magic=HMA_MAGIC, request_id=self.order_id(side), stop_event=self._stop_event, **kwargs)
        if res.get("success"):
            self.runtime_state, self.runtime_detail = 'WAITING_SIGNAL', 'Emir kabul edildi; yeni sinyal bekleniyor.'
            self.log(f"✅ {side} Emri Açıldı: #{res.get('ticket')} @ {res.get('price')}")
        else:
            self.runtime_state, self.runtime_detail, self.last_error = 'ORDER_REJECTED', res.get('error'), res.get('error')
            self.log(f"❌ {side} Emri Başarısız: {res.get('error')}", "ERROR")
        if res.get('uncertain') or res.get('pending') or res.get('partial'):
            self.log('Emir sonucu kesinleşmedi; bot durduruldu. MT5 durumunu kontrol edin.', 'ERROR')
            self.stop()
            self.runtime_state, self.runtime_detail = 'ORDER_UNRESOLVED', 'Emir sonucu kesinleşmedi; MT5 durumunu kontrol edin.'

    def h4_confirmation(self, side, decision):
        rows = self.mt5.get_rates(self.symbol, timeframe=240,
            count=5*max(self.hma_period,self.second_ma_period,self.kama_slow,self.atr_period)+20)
        closed = [r for r in rows if r['time']+14400 <= decision]
        if len(closed) < 2 or decision-(closed[-1]['time']+14400) >= 14400:
            return False
        study = indicators(closed, IndicatorSettings(hma_period=self.hma_period,
            kama_period=self.second_ma_period,kama_fast=self.kama_fast,kama_slow=self.kama_slow,atr_period=self.atr_period))
        er, kama = study['er'], study['kama']
        return (er[-1] is not None and kama[-1] is not None and kama[-2] is not None
                and er[-1] >= self.er_min and side*(kama[-1]-kama[-2]) > 0)

    def order_distances(self):
        if self.risk_mode == "POINTS":
            return (self.sl_points if self.use_stop_loss else 0,
                    self.tp_points if self.use_take_profit else 0)
        try:
            point = float(self.mt5.get_symbol_spec(self.symbol)['point'])
            if not math.isfinite(point) or point <= 0 or not math.isfinite(self.current_atr) or self.current_atr <= 0:
                raise ValueError('Geçersiz point/ATR')
            sl = math.ceil(self.current_atr * self.atr_stop_multiplier / point) if self.use_stop_loss else 0
            tp = math.ceil(self.current_atr * self.atr_target_multiplier / point) if self.use_take_profit and self.atr_target_multiplier > 0 else 0
            return sl, tp
        except Exception:
            error = 'ATR stop/hedef mesafesi doğrulanamadı; emir gönderilmedi.'
            self.runtime_state, self.runtime_detail, self.last_error = 'ERROR', error, error
            self.log(error, 'ERROR')
            return None, None

    def close_positions_by_type(self, pos_type: str, *, expected_account=None):
        try:
            positions = self.mt5.get_positions(fresh=True)
        except MT5DataError as exc:
            self.log(str(exc), "ERROR")
            return False
        for p in positions:
            if p["symbol"] == self.symbol and p["type"] == pos_type and p.get("magic") == HMA_MAGIC:
                self.log(f"Ters sinyal nedeniyle #{p['ticket']} ({pos_type}) pozisyonu kapatılıyor...")
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

    def order_id(self, direction):
        identity = (self.mt5.login_id, self.mt5.server, self.symbol, self.timeframe_minutes,
                    self.last_candle_time, direction)
        return "hma-" + hashlib.sha256(repr(identity).encode()).hexdigest()

    def start(self, expected_config=None):
        with self._state_lock:
            if expected_config is not None and self.config_snapshot() != expected_config:
                raise RuntimeError('Hazırlık kontrolü sırasında bot ayarları değişti; bot başlatılmadı.')
            if not self.is_running and not self._check_lock.locked() and (self.task is None or self.task.done()):
                self.mt5.automation_stopped.clear()
                self._stop_event.clear()
                self.last_candle_time = 0
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
