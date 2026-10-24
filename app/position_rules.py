"""Per-position auto-close rules (profit target / loss limit as percent of margin or fixed amount).

Not in MT5 (user request, 2026-10-24): the app closes the position at market like the Close button
when the net result (profit + swap) reaches the target or the loss limit. Rules are kept in memory only;
a restart or account change drops them (like trailing stops) and the panel shows the armed rules.
"""
import asyncio
import logging
import math
import uuid

logger = logging.getLogger(__name__)
MAX_AMOUNT = 1e9


def _value(kind, amount):
    if amount in (None, 0, ''):
        return None
    if kind not in ('percent', 'amount'):
        raise ValueError('Hedef türü yüzde veya tutar olmalı.')
    amount = float(amount)
    if not math.isfinite(amount) or amount <= 0 or amount > (1000 if kind == 'percent' else MAX_AMOUNT):
        raise ValueError('Hedef değeri geçerli değil.')
    return amount


class PositionRules:
    def __init__(self, client):
        self.client = client
        self.rules = {}
        self.stopped = False

    def _scope(self, expected=None):
        return [*self.client._bound_account(expected), self.client.account_type]

    def configure(self, ticket, profit_kind, profit, loss_kind, loss, expected_account=None):
        profit, loss = _value(profit_kind, profit), _value(loss_kind, loss)
        with self.client._lock.order():
            if not self.client.ensure_connected():
                raise ValueError('MT5 bağlı değil.')
            if self.stopped:
                raise ValueError('Uygulama durduruluyor.')
            scope = self._scope(expected_account)
            if profit is None and loss is None:
                self.rules.pop(ticket, None)
                return {'success': True, 'enabled': False, 'ticket': ticket}
            row = self.client._bridge('position_rule_state', ticket, *scope[:2])
            if row['closed']:
                raise ValueError('Pozisyon artık açık değil.')
            if (profit_kind == 'percent' and profit or loss_kind == 'percent' and loss) and row['margin'] <= 0:
                raise ValueError('Bu sembolde teminat hesaplanamadı; yüzde yerine tutar girin.')
            self.rules[ticket] = dict(ticket=ticket, account=scope, identifier=row['identifier'], side=row['side'],
                profit_kind=profit_kind, profit=profit, loss_kind=loss_kind, loss=loss, status='armed', message='')
            return {'success': True, 'enabled': True, **self._public(self.rules[ticket])}

    @staticmethod
    def _public(rule):
        return {k: v for k, v in rule.items() if k != 'account'}

    def status(self):
        with self.client._lock:
            scope = self._scope(None)
            return [self._public(r) for r in self.rules.values() if list(r['account']) == scope]

    @staticmethod
    def hit(rule, state):
        """'profit' / 'loss' / None for the current net result."""
        result, margin = state['profit'], state['margin']
        target = rule['profit'] and (rule['profit']*margin/100 if rule['profit_kind'] == 'percent' else rule['profit'])
        limit = rule['loss'] and (rule['loss']*margin/100 if rule['loss_kind'] == 'percent' else rule['loss'])
        if target and result >= target:
            return 'profit'
        if limit and result <= -limit:
            return 'loss'
        return None

    def step(self):
        for ticket in tuple(self.rules):
            with self.client._lock.order():
                rule = self.rules.get(ticket)
                if self.stopped or rule is None or rule['status'] in ('uncertain', 'closing'):
                    continue
                try:
                    scope = self._scope(None)
                    if scope != list(rule['account']):
                        self.rules.pop(ticket, None)
                        continue
                    state = self.client._bridge('position_rule_state', ticket, *scope[:2])
                    if state['closed'] or (state['identifier'], state['side']) != (rule['identifier'], rule['side']):
                        self.rules.pop(ticket, None)
                        continue
                    reason = self.hit(rule, state)
                    if not reason:
                        rule['status'] = 'armed'
                        continue
                    result = self.client.journal.run('rule-close-%d-%s' % (ticket, uuid.uuid4().hex), {'ticket': ticket},
                        lambda: self.client.close_position(ticket, expected_account=rule['account']))
                    if result.get('success') and not result.get('pending') and not result.get('partial'):
                        self.rules.pop(ticket, None)
                    else:
                        rule['status'] = 'uncertain' if result.get('uncertain') or result.get('pending') or result.get('partial') else 'rejected'
                        rule['message'] = str(result.get('error', ''))[:250]
                    logger.info('Position rule %s closed #%s: %s', reason, ticket, result.get('success'))
                except Exception:
                    rule['status'] = 'paused'
                    rule['message'] = 'Hesap, pozisyon veya fiyat doğrulanamadı; kapatma yapılmadı.'
                    logger.debug('Position rule paused', exc_info=True)

    def stop(self):
        with self.client._lock.order():
            self.stopped = True
            self.rules.clear()

    async def run(self):
        self.stopped = False
        while not self.stopped:
            await asyncio.to_thread(self.step)
            await asyncio.sleep(1)
