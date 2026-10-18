"""One MT5 snapshot per interval, shared by all connected dashboards."""
import asyncio
import statistics
import time
from collections import deque

# MT5 redraws on every tick; the panel samples MT5 up to 10 times a second.
FASTEST_INTERVAL = .10
# Orders jump the queue but wait for a running snapshot, so MT5 stays idle at least
# IDLE_FACTOR times as long as one snapshot takes (snapshot uses at most 1/3 of the time).
IDLE_FACTOR = 2.0
SLOWEST_INTERVAL = .25   # never slower than before


class LiveFeed:
    def __init__(self, client):
        self.client = client
        self.listeners = {}
        self.changed = asyncio.Event()
        self.durations = deque(maxlen=200)
        self.last_interval = SLOWEST_INTERVAL

    def subscribe(self, symbol):
        if len(self.listeners) >= 32:
            raise ValueError('Canlı bağlantı sınırına ulaşıldı.')
        queue = asyncio.Queue(maxsize=1)
        self.listeners[queue] = symbol
        self.changed.set()
        return queue

    def unsubscribe(self, queue):
        self.listeners.pop(queue, None)

    def publish(self, value):
        for queue in tuple(self.listeners):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(value)

    @staticmethod
    def pause_after(took):
        """Time to wait after a snapshot that took `took` seconds."""
        if took * (1 + IDLE_FACTOR) >= SLOWEST_INTERVAL:
            return max(SLOWEST_INTERVAL - took, took * IDLE_FACTOR)
        return max(FASTEST_INTERVAL - took, took * IDLE_FACTOR)

    def stats(self):
        values = sorted(self.durations)
        if not values:
            return {'count': 0, 'interval_ms': round(self.last_interval * 1000, 1)}
        return {'count': len(values), 'p50_ms': round(statistics.median(values) * 1000, 1),
                'p95_ms': round(values[int(.95 * (len(values) - 1))] * 1000, 1),
                'max_ms': round(values[-1] * 1000, 1), 'interval_ms': round(self.last_interval * 1000, 1)}

    async def run(self):
        self.changed = asyncio.Event()
        while True:
            if not self.listeners:
                self.changed.clear()
                await self.changed.wait()
            symbols = sorted(set(self.listeners.values()))
            started = time.perf_counter()
            try:
                data = await asyncio.to_thread(self.client.get_live_snapshot, symbols)
            except Exception:
                data = {'error': 'Canlı veriler doğrulanamadı.', 'sampled_at': time.time()}
            took = time.perf_counter() - started
            self.durations.append(took)
            self.publish(data)
            pause = self.pause_after(took)
            self.last_interval = took + pause
            await asyncio.sleep(pause)
