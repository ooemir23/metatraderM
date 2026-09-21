"""One MT5 snapshot per interval, shared by all connected dashboards."""
import asyncio
import time


class LiveFeed:
    def __init__(self, client):
        self.client = client
        self.listeners = {}
        self.changed = asyncio.Event()

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

    async def run(self):
        self.changed = asyncio.Event()
        while True:
            if not self.listeners:
                self.changed.clear()
                await self.changed.wait()
            symbols = sorted(set(self.listeners.values()))
            try:
                data = await asyncio.to_thread(self.client.get_live_snapshot, symbols)
            except Exception:
                data = {'error': 'Canlı veriler doğrulanamadı.', 'sampled_at': time.time()}
            self.publish(data)
            await asyncio.sleep(.5)
