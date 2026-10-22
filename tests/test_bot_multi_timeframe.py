"""One EA instance per timeframe (user decision 2026-10-23). MT5: the same EA attached to an H1 and an H4 chart
of one symbol; each trades its own crossovers and, on a reverse signal, closes only its own position."""
import base64
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import signal_engine as se
from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot
from tests.test_bot_auto_trade_e2e import FakeMT5, live_bot, market


class MultiTF(FakeMT5):
    def __init__(self, by_tf):
        super().__init__(by_tf[60])
        self.by_tf = by_tf

    def get_rates(self, symbol, timeframe, count):
        visible = self.by_tf[timeframe][:self.now + 1]
        return [dict(r) for r in visible[-count:]]

    def open_order(self, symbol, side, lot, **kw):
        res = super().open_order(symbol, side, lot, **kw)
        self.positions[-1]['comment'] = kw.get('comment', '')
        self.opened[-1]['comment'] = kw.get('comment', '')
        return res


def crosses(rows, start):
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    return [(i, se.cross_at(fast, slow, i)) for i in range(start, len(rows) - 1) if se.cross_at(fast, slow, i)]


def test_each_timeframe_trades_its_own_crossovers_and_closes_only_its_own_position():
    by_tf = {60: market(seed=21), 240: market(seed=5)}
    client = MultiTF(by_tf)
    bot = live_bot(client)
    bot.extra_timeframes = [240]
    for now in range(450, 650):
        client.now = now
        bot._check_strategy_once()
    for tf, code in ((60, 'H1'), (240, 'H4')):
        mine = [o for o in client.opened if o['comment'].startswith(f'HMA Bot {code} ')]
        assert [(o['candle'] - 1, o['side']) for o in mine] == crosses(by_tf[tf], 450), code
    # At most one open position per instance; one per timeframe can be open at the same time.
    tags = [p['comment'].split()[2] for p in client.positions]
    assert sorted(tags) == ['H1', 'H4']
    # Every close was a reverse signal of the same instance.
    by_ticket = {o_ticket: o for o_ticket, o in zip(range(1001, 1001 + len(client.opened)), client.opened)}
    for c in client.closed:
        opened = by_ticket[c['ticket']]
        reopen = [o for o in client.opened if o['candle'] == c['candle'] and o['comment'].split()[2] == opened['comment'].split()[2]]
        assert reopen and reopen[0]['side'] != c['side']


def test_untagged_older_bot_position_belongs_to_the_bot_timeframe():
    bot = StrategyBot(Mock())
    bot.timeframe_minutes, bot.extra_timeframes = 1, [5, 60]
    assert bot._owner_tf({'comment': 'HMA Bot BUY'}) == 1
    assert bot._owner_tf({'comment': 'HMA Bot M5 SELL'}) == 5
    assert bot._owner_tf({'comment': 'HMA Bot H1 BUY'}) == 60
    assert bot.instance_timeframes() == [1, 5, 60]


def test_adding_a_timeframe_while_running_keeps_the_running_instance():
    bot = StrategyBot(Mock(login_id=7, server='demo', account_type='DEMO'))
    bot.is_running, bot.timeframe_minutes = True, 1
    bot.symbol, bot.symbols = 'XAUUSD', ['XAUUSD']
    bot.persist_config = lambda data: None
    bot.last_candle_times = {'XAUUSD': 123}
    bot.reinit_live({'extra_timeframes': [5, 60]})
    assert bot.extra_timeframes == [5, 60] and bot.last_candle_times == {'XAUUSD': 123}
    bot.last_candle_times['XAUUSD@M5'] = 99
    bot.reinit_live({'extra_timeframes': [60]})
    assert bot.last_candle_times == {'XAUUSD': 123}
    with pytest.raises(ValueError):
        bot.reinit_live({'extra_timeframes': [7]})
    # The bot timeframe is never listed twice.
    bot.reinit_live({'extra_timeframes': [1, 60]})
    assert bot.extra_timeframes == [60]


def test_api_accepts_timeframe_instances(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    for key in ('is_running', 'extra_timeframes', 'timeframe_minutes', 'symbol', 'symbols'):
        monkeypatch.setattr(main.bot, key, getattr(main.bot, key))
    monkeypatch.setattr(main.bot, 'is_running', False)
    monkeypatch.setattr(main.bot, 'persist_config', lambda data: None)
    token = base64.b64encode(b'test:test-panel-password').decode()
    client = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    res = client.post('/api/bot/follow', json={'extra_timeframes': [5, 60]})
    assert res.status_code == 200, res.text
    assert res.json()['instance_timeframes'][1:] == [5, 60]
    assert client.post('/api/bot/follow', json={'extra_timeframes': [7]}).status_code == 422
