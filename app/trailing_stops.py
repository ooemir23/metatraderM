"""Client-side trailing stops. Restart/account changes require explicit rearming."""
import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)


class TrailingStops:
    def __init__(self, client):
        self.client = client
        self.configs = {}
        self.stopped = False

    def configure(self, ticket, points, expected_account=None):
        with self.client._lock.order():
            if not self.client.ensure_connected():
                raise ValueError('MT5 bağlı değil.')
            if self.stopped:
                raise ValueError('Uygulama durduruluyor; iz süren stop ayarlanamadı.')
            scope = self.client._bound_account(expected_account)
            if not points:
                self.configs.pop(ticket, None)
                return {'success': True, 'enabled': False, 'ticket': ticket}
            row = self.client._bridge('trailing_candidate', ticket, points, *scope, self.client.tick_clock_offset)
            if row['closed']:
                raise ValueError('Pozisyon artık açık değil.')
            self.configs[ticket] = dict(ticket=ticket, points=points, account=[*scope,self.client.account_type],
                identifier=row['identifier'], side=row['side'], status='armed', last_attempt=0)
            return {'success': True, 'enabled': True, 'ticket': ticket, 'points': points}

    def status(self):
        with self.client._lock:
            scope = self.client._bound_account(None)
            return [{k: v for k, v in item.items() if k not in ('account', 'last_attempt')}
                    for item in self.configs.values() if list(item['account']) == [*scope,self.client.account_type]]

    def step(self):
        # Shared order lock makes stop/disable/account switching serialize with sends.
        for ticket in tuple(self.configs):
            with self.client._lock.order():
                config = self.configs.get(ticket)
                if self.stopped or config is None:
                    continue
                if config['status'] == 'uncertain':
                    continue  # No repeated modification after an ambiguous broker result.
                try:
                    scope = self.client._bound_account(None)
                    if [*scope,self.client.account_type] != list(config['account']):
                        self.configs.pop(ticket, None)
                        continue
                    row = self.client._bridge('trailing_candidate', ticket, config['points'],
                                              *scope, self.client.tick_clock_offset)
                    if row['closed'] or (row['identifier'], row['side']) != (config['identifier'], config['side']):
                        self.configs.pop(ticket, None)
                        continue
                    if not row['ready'] or time.monotonic()-config['last_attempt'] < 10:
                        continue
                    config['last_attempt'] = time.monotonic()
                    result = self.client.manage_position(ticket, 'stops',
                        {key:row[key] for key in ('sl','tp','expected_sl','expected_tp')} | {'manual_mode':'MT5'},
                        'trailing-'+uuid.uuid4().hex, expected_account=config['account'])
                    config['status'] = ('uncertain' if result.get('uncertain') or result.get('pending')
                                        else 'armed' if result.get('success') else 'rejected')
                    config['message'] = str(result.get('error', ''))[:250]
                except Exception:
                    config['status'] = 'paused'
                    config['message'] = 'Hesap, pozisyon veya fiyat doğrulanamadı; gönderim yapılmadı.'
                    logger.debug('Trailing stop paused', exc_info=True)

    def stop(self):
        with self.client._lock.order():
            self.stopped = True
            self.configs.clear()

    async def run(self):
        self.stopped = False
        while not self.stopped:
            await asyncio.to_thread(self.step)
            await asyncio.sleep(1)
