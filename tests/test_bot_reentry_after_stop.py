"""Re-entry after SL / TP (user decision 2026-10-23): when the bot's own position of a timeframe instance is
closed by SL, TP or stop out (MT5 DEAL_REASON 4 / 5 / 6) and the strategy still shows a side, the bot opens it
on the next closed candle without a restart. A hand close never triggers it."""
from app import signal_engine as se
from app.mt5_bridge import HMA_MAGIC
from tests.test_bot_auto_trade_e2e import FakeMT5, live_bot, market


def setup(reason):
    rows = market()
    client = FakeMT5(rows)
    client.closes = []
    client.bot_recent_closes = lambda sym, hours=48: list(client.closes)
    bot = live_bot(client)
    bot.trend_entry_on_start = True
    bot.reenter_after_stop = True
    bot.started_at = 1
    client.now = 450
    bot._check_strategy_once()                        # trend entry at start
    first = client.opened[0]
    pos = client.positions.pop()                      # broker closed it
    client.closes = [{'position_id': pos['ticket'], 'comment': first['comment'], 'side': first['side'],
                      'close_time': rows[450]['time'] + 60, 'close_reason': reason}]
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    return rows, client, bot, fast, slow


def no_cross(fast, slow, i):
    return not se.cross_at(fast, slow, i)


def test_reenters_after_stop_loss_when_trend_holds():
    rows, client, bot, fast, slow = setup(4)
    assert no_cross(fast, slow, 450)
    client.now = 451
    bot._check_strategy_once()
    side = 'BUY' if fast[450] > slow[450] else 'SELL'
    assert [(o['candle'], o['side']) for o in client.opened[1:]] == [(451, side)]
    assert client.opened[1]['magic'] == HMA_MAGIC
    client.positions.pop()
    client.now = 452
    bot._check_strategy_once()                        # same close is not re-used: only one re-entry per close
    assert len(client.opened) == 2 or se.cross_at(fast, slow, 451)


def test_take_profit_also_reenters():
    rows, client, bot, fast, slow = setup(5)
    client.now = 451
    bot._check_strategy_once()
    assert len(client.opened) == 2


def test_hand_close_does_not_reenter():
    for reason in (0, 1, 2, 3):                       # desktop, mobile, web, panel/API close
        rows, client, bot, fast, slow = setup(reason)
        client.now = 451
        bot._check_strategy_once()
        assert len(client.opened) == 1, reason


def test_close_before_this_run_does_not_reenter():
    rows, client, bot, fast, slow = setup(4)
    bot.started_at = 10 ** 12
    client.now = 451
    bot._check_strategy_once()
    assert len(client.opened) == 1


def test_option_off_waits_for_crossover():
    rows, client, bot, fast, slow = setup(4)
    bot.reenter_after_stop = False
    client.now = 451
    bot._check_strategy_once()
    assert len(client.opened) == 1


def test_bridge_reports_close_reason_of_bot_positions():
    from types import SimpleNamespace
    from app import mt5_bridge

    def deal(ticket, pos, kind, entry, t, magic=HMA_MAGIC, comment='HMA Bot H1 SELL', reason=0, symbol='XAUUSD'):
        return SimpleNamespace(ticket=ticket, position_id=pos, type=kind, entry=entry, time=t, time_msc=t * 1000,
                               magic=magic, comment=comment, reason=reason, symbol=symbol)
    deals = [deal(1, 10, 1, 0, 100), deal(2, 10, 0, 1, 200, reason=4),           # bot SELL closed by SL
             deal(3, 11, 0, 0, 300, comment='HMA Bot M5 BUY'), deal(4, 11, 1, 1, 400, reason=3),
             deal(5, 12, 0, 0, 500, magic=0), deal(6, 12, 1, 1, 600, magic=0),    # manual: ignored
             deal(7, 13, 0, 0, 700)]                                             # still open: ignored
    mt5 = SimpleNamespace(account_info=lambda: SimpleNamespace(login=7, server='S', trade_mode=0),
                          history_deals_get=lambda *a: deals, positions_get=lambda: [SimpleNamespace(identifier=13)])
    rows = mt5_bridge.hma_recent_closes(mt5, 'XAUUSD', 48, 0, [7, 'S'])
    assert [(r['position_id'], r['comment'], r['close_reason']) for r in rows] == [(11, 'HMA Bot M5 BUY', 3), (10, 'HMA Bot H1 SELL', 4)]
