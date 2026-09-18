import asyncio
import logging
import math
import time
import threading
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger("StrategyBot")
logger.setLevel(logging.INFO)

class StrategyBot:
    def __init__(self, mt5_client):
        self.mt5 = mt5_client
        self.is_running = False
        self.task = None
        self._stop_event = threading.Event()

        # Bot Parameters (Defaults matching HMA_Crossover_EA)
        self.symbol = "EURUSD"
        self.timeframe_minutes = 15
        self.hma_period = 14
        self.second_ma_type = "EMA" # EMA, SMA, LWMA, HMA
        self.second_ma_period = 34
        self.lot_size = 0.01
        self.use_stop_loss = True
        self.sl_points = 200
        self.use_take_profit = False
        self.tp_points = 400
        self.close_opposite = True
        self.telegram_token = ""
        self.telegram_chat_id = ""

        # State tracking
        self.last_candle_time = 0
        self.last_signal = "YOK"
        self.last_signal_time = ""
        self.last_signal_price = 0.0
        self.current_hma = 0.0
        self.current_ma2 = 0.0
        self.logs: List[Dict[str, Any]] = []

    def log(self, message: str, level: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message, "level": level}
        self.logs.insert(0, entry)
        if len(self.logs) > 50:
            self.logs.pop()
        logger.info(f"[{level}] {message}")

    def send_telegram(self, text: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")

    def update_config(self, data: Dict[str, Any]):
        if "symbol" in data: self.symbol = data["symbol"].upper()
        if "timeframe_minutes" in data: self.timeframe_minutes = int(data["timeframe_minutes"])
        if "hma_period" in data: self.hma_period = max(2, int(data["hma_period"]))
        if "second_ma_type" in data: self.second_ma_type = data["second_ma_type"].upper()
        if "second_ma_period" in data: self.second_ma_period = max(2, int(data["second_ma_period"]))
        if "lot_size" in data: self.lot_size = max(0.01, float(data["lot_size"]))
        if "use_stop_loss" in data: self.use_stop_loss = bool(data["use_stop_loss"])
        if "sl_points" in data: self.sl_points = int(data["sl_points"])
        if "use_take_profit" in data: self.use_take_profit = bool(data["use_take_profit"])
        if "tp_points" in data: self.tp_points = int(data["tp_points"])
        if "close_opposite" in data: self.close_opposite = bool(data["close_opposite"])
        if "telegram_token" in data: self.telegram_token = str(data["telegram_token"]).strip()
        if "telegram_chat_id" in data: self.telegram_chat_id = str(data["telegram_chat_id"]).strip()

        self.log(f"Bot ayarları güncellendi: {self.symbol} | HMA: {self.hma_period} | 2.MA: {self.second_ma_type}({self.second_ma_period}) | Lot: {self.lot_size}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "symbol": self.symbol,
            "timeframe_minutes": self.timeframe_minutes,
            "hma_period": self.hma_period,
            "second_ma_type": self.second_ma_type,
            "second_ma_period": self.second_ma_period,
            "lot_size": self.lot_size,
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
            "has_telegram": bool(self.telegram_token and self.telegram_chat_id),
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
        if period < 2 or len(close_prices) < period + shift + 10:
            return 0.0

        half_period = int(math.floor(period / 2.0))
        sqrt_period = int(math.floor(math.sqrt(period)))

        diff_array = []
        for i in range(sqrt_period + shift + 1):
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
        await asyncio.to_thread(self.send_telegram, f"🤖 <b>HMA Crossover Bot Başlatıldı!</b>\nSembol: {self.symbol}\nZaman: M{self.timeframe_minutes}\nHMA: {self.hma_period}\n2. MA: {self.second_ma_type}({self.second_ma_period})")

        while self.is_running:
            try:
                await self.check_strategy()
            except Exception as e:
                logger.error(f"Strategy error: {e}")
                self.log(f"Hata: {str(e)}", "ERROR")

            await asyncio.sleep(5) # Kontrol aralığı: 5 saniye

    async def check_strategy(self):
        await asyncio.to_thread(self._check_strategy)

    def _check_strategy(self):
        if self._stop_event.is_set():
            return
        if not self.mt5.ensure_connected():
            return

        rates = self.mt5.get_rates(self.symbol, timeframe=self.timeframe_minutes, count=100)
        if not rates or len(rates) < max(self.hma_period, self.second_ma_period) + 15:
            return

        # rates are in chronological order, latest is last
        # We need latest closed bar (shift=1) and previous closed bar (shift=2)
        # Convert to reverse order (index 0 = current open candle, 1 = last closed candle, 2 = previous closed)
        rev_rates = list(reversed(rates))
        closes = [r["close"] for r in rev_rates]

        # Calculate for shift=1 (last closed candle)
        hma1 = self.calc_hma(closes, self.hma_period, shift=1)
        ma2_1 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=1)

        # Calculate for shift=2 (previous closed candle)
        hma2 = self.calc_hma(closes, self.hma_period, shift=2)
        ma2_2 = self.calc_second_ma(closes, self.second_ma_type, self.second_ma_period, shift=2)

        self.current_hma = hma1
        self.current_ma2 = ma2_1

        latest_closed_candle_time = rev_rates[1]["time"]
        if latest_closed_candle_time == self.last_candle_time:
            # Already checked this closed candle
            return

        self.last_candle_time = latest_closed_candle_time

        # Check crossover condition
        is_buy_cross = (hma2 <= ma2_2) and (hma1 > ma2_1)
        is_sell_cross = (hma2 >= ma2_2) and (hma1 < ma2_1)

        current_price = closes[1]
        now_str = time.strftime("%H:%M:%S")

        if is_buy_cross:
            self.last_signal = "BUY"
            self.last_signal_time = now_str
            self.last_signal_price = current_price
            self.log(f"🟢 [AL SİNYALİ] HMA({round(hma1, 5)}) > {self.second_ma_type}({round(ma2_1, 5)}) @ {current_price}", "SIGNAL")
            self.send_telegram(f"🟢 <b>[AL SİNYALİ] {self.symbol}</b>\nFiyat: {current_price}\nHMA: {round(hma1, 5)}\n2.MA: {round(ma2_1, 5)}")

            # Execute Trade
            if self._stop_event.is_set():
                return
            if self.close_opposite and not self.close_positions_by_type("SELL"):
                self.log("Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
                return
            if self._stop_event.is_set():
                return

            sl = self.sl_points if self.use_stop_loss else 0
            tp = self.tp_points if self.use_take_profit else 0
            res = self.mt5.open_order(self.symbol, "BUY", self.lot_size, sl_points=sl, tp_points=tp, comment="HMA Bot BUY")
            if res.get("success"):
                self.log(f"✅ BUY Emri Açıldı: #{res.get('ticket')} @ {res.get('price')}")
            else:
                self.log(f"❌ BUY Emri Başarısız: {res.get('error')}", "ERROR")

        elif is_sell_cross:
            self.last_signal = "SELL"
            self.last_signal_time = now_str
            self.last_signal_price = current_price
            self.log(f"🔴 [SAT SİNYALİ] HMA({round(hma1, 5)}) < {self.second_ma_type}({round(ma2_1, 5)}) @ {current_price}", "SIGNAL")
            self.send_telegram(f"🔴 <b>[SAT SİNYALİ] {self.symbol}</b>\nFiyat: {current_price}\nHMA: {round(hma1, 5)}\n2.MA: {round(ma2_1, 5)}")

            # Execute Trade
            if self._stop_event.is_set():
                return
            if self.close_opposite and not self.close_positions_by_type("BUY"):
                self.log("Ters pozisyon kapatılamadı; yeni emir gönderilmedi.", "ERROR")
                return
            if self._stop_event.is_set():
                return

            sl = self.sl_points if self.use_stop_loss else 0
            tp = self.tp_points if self.use_take_profit else 0
            res = self.mt5.open_order(self.symbol, "SELL", self.lot_size, sl_points=sl, tp_points=tp, comment="HMA Bot SELL")
            if res.get("success"):
                self.log(f"✅ SELL Emri Açıldı: #{res.get('ticket')} @ {res.get('price')}")
            else:
                self.log(f"❌ SELL Emri Başarısız: {res.get('error')}", "ERROR")

    def close_positions_by_type(self, pos_type: str):
        positions = self.mt5.get_positions()
        for p in positions:
            if p["symbol"] == self.symbol and p["type"] == pos_type:
                self.log(f"Ters sinyal nedeniyle #{p['ticket']} ({pos_type}) pozisyonu kapatılıyor...")
                if self._stop_event.is_set():
                    return False
                result = self.mt5.close_position(p["ticket"])
                if not result.get("success"):
                    return False
        return True

    def start(self):
        if not self.is_running and (self.task is None or self.task.done()):
            self._stop_event.clear()
            self.is_running = True
            self.task = asyncio.create_task(self.run_loop())

    def stop(self):
        self._stop_event.set()
        if self.is_running:
            self.is_running = False
            # Do not cancel a to_thread worker: it cannot be interrupted, and a
            # new start must wait until that worker has finished.
            self.log("🛑 HMA Kesişim Botu Durduruldu")
