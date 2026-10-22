"""User decisions (2026-10-23), on top of the HMA_Crossover_EA crossover:
1. on start / settings change the bot opens the current trend side at once (the EA would wait for a new
   crossover); a rejected first try is retried on the next closed candle;
2. lot and SL/TP come from the profitable hand-opened MT5 positions since 2026-10-08 (app.manual_copy)."""
from app import manual_copy, signal_engine as se
from app.mt5_bridge import HMA_MAGIC
from tests.test_bot_auto_trade_e2e import FakeMT5, live_bot, market

DAY = manual_copy.since_epoch('2026-10-08')


def trend_run(start=450, reject_first=False):
    rows = market()
    client = FakeMT5(rows)
    if reject_first:
        real, calls = client.open_order, []

        def open_order(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return {'success': False, 'error': 'Market closed'}
            return real(*a, **k)
        client.open_order = open_order
    bot = live_bot(client)
    bot.trend_entry_on_start = True
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    return rows, client, bot, fast, slow


def test_start_opens_current_trend_side_immediately():
    rows, client, bot, fast, slow = trend_run()
    client.now = 450
    bot._check_strategy_once()
    trend = 'BUY' if fast[449] > slow[449] else 'SELL'      # last closed candle at start
    assert [(o['candle'], o['side'], o['magic']) for o in client.opened] == [(450, trend, HMA_MAGIC)]
    bot._check_strategy_once()                                 # same candle: nothing more
    assert len(client.opened) == 1


def test_trend_entry_not_repeated_when_bot_position_already_open():
    rows, client, bot, fast, slow = trend_run()
    trend = 'BUY' if fast[449] > slow[449] else 'SELL'
    client.positions.append({'ticket': 1, 'identifier': 1, 'symbol': 'XAUUSD', 'type': trend, 'magic': HMA_MAGIC,
                             'volume': 1.0, 'price_open': 4100.0})
    client.now = 450
    bot._check_strategy_once()
    assert client.opened == []
    client.now = 451
    bot._check_strategy_once()
    assert client.opened == [] or client.opened[0]['candle'] - 1 == 450 and se.cross_at(fast, slow, 450)


def test_rejected_trend_entry_is_retried_on_next_closed_candle():
    rows, client, bot, fast, slow = trend_run(reject_first=True)
    client.now = 450
    bot._check_strategy_once()
    assert client.opened == []
    client.now = 451
    bot._check_strategy_once()
    side = 'BUY' if fast[450] > slow[450] else 'SELL'
    assert [(o['candle'], o['side']) for o in client.opened] == [(451, side)]
    client.now = 452
    bot._check_strategy_once()
    assert len(client.opened) == 1 or se.cross_at(fast, slow, 451)   # afterwards only crossovers trade


def test_after_trend_entry_bot_trades_crossovers_like_the_ea():
    rows, client, bot, fast, slow = trend_run()
    for now in range(450, len(rows)):
        client.now = now
        bot._check_strategy_once()
    crosses = [(i, se.cross_at(fast, slow, i)) for i in range(450, len(rows) - 1) if se.cross_at(fast, slow, i)]
    assert [(o['candle'] - 1, o['side']) for o in client.opened[1:]] == crosses


# ---------------------------------------------------------------- manual copy profile

def trade(pid, side, vol, op, cp, net, t, sl=0.0, tp=0.0, symbol='XAUUSD'):
    return dict(position_id=pid, symbol=symbol, side=side, volume=vol, open_time=t, open_price=op,
                close_time=t + 600, close_price=cp, net=net, sl=sl, tp=tp)


def test_profile_uses_profitable_manual_positions_since_date():
    trades = [
        trade(1, 'BUY', 5, 4100, 4107, 3500, DAY + 100),           # same decision as 2 and 3 (within 120 s)
        trade(2, 'BUY', 5, 4100, 4107, 3500, DAY + 130),
        trade(3, 'BUY', 5, 4100, 4107, 3500, DAY + 160),
        trade(4, 'SELL', 10, 4200, 4192, 8000, DAY + 9000),
        trade(5, 'SELL', 10, 4210, 4204, 6000, DAY + 20000),
        trade(6, 'BUY', 5, 4000, 3990, -5000, DAY + 30000),         # loss: ignored
        trade(7, 'BUY', 50, 4000, 4010, 5000, DAY - 86400),         # before 8 Oct: ignored
        trade(8, 'BUY', 1, 1.1, 1.2, 50, DAY + 100, symbol='EURUSD'),
    ]
    prof = manual_copy.profile(trades, 'XAUUSD', '2026-10-08', 0.01)
    assert prof['available'] and prof['count'] == 5 and prof['decisions'] == 3
    assert prof['lot'] == 10            # decisions: 15, 10, 10 lot -> median 10
    # No TP on record -> median exit distance in the trade direction: 700, 700, 700, 800, 600 points.
    assert prof['tp_points'] == 700 and prof['tp_source'] == 'manual_exit'
    assert prof['sl_points'] is None and prof['sl_source'] == 'bot_setting'


def test_profile_uses_recorded_sl_tp_and_needs_enough_trades():
    trades = [trade(i, 'BUY', 1, 4100, 4105, 500, DAY + i * 1000, sl=4095, tp=4106) for i in range(1, 5)]
    prof = manual_copy.profile(trades, 'XAUUSD', '2026-10-08', 0.01)
    assert (prof['sl_points'], prof['tp_points'], prof['tp_source']) == (500, 600, 'manual_tp')
    assert not manual_copy.profile(trades[:2], 'XAUUSD', '2026-10-08', 0.01)['available']


def test_bot_order_uses_copied_lot_sl_tp_and_respects_lot_cap():
    rows, client, bot, fast, slow = trend_run()
    bot.copy_manual_trades = True
    bot.auto_lot_limits_demo = False      # saved limits apply (always so on a real account)
    client.max_order_lots = 3.0
    client.manual_copy_profile = lambda sym, since: {'available': True, 'symbol': sym, 'since': since, 'count': 9,
        'decisions': 4, 'lot': 5.0, 'sl_points': 1300, 'tp_points': 700, 'sl_source': 'manual_sl', 'tp_source': 'manual_exit'}
    client.now = 450
    bot._check_strategy_once()
    order = client.opened[0]
    assert (order['lot'], order['sl_points'], order['tp_points']) == (3.0, 1300, 700)   # capped, never above the limit
    assert bot.manual_copy['applied_lot'] == 3.0 and bot.manual_copy['capped']


def test_bot_falls_back_to_its_settings_without_manual_profile():
    rows, client, bot, fast, slow = trend_run()
    bot.copy_manual_trades = True
    client.manual_copy_profile = lambda sym, since: {'available': False, 'reason': 'az işlem'}
    client.now = 450
    bot._check_strategy_once()
    assert client.opened[0]['lot'] == 1.0
    assert bot.manual_copy == {'available': False, 'reason': 'az işlem'}


def test_demo_account_opens_full_copied_lot_without_changing_saved_limits():
    rows, client, bot, fast, slow = trend_run()
    bot.copy_manual_trades = bot.auto_lot_limits_demo = True
    bot.extra_timeframes = [240]
    client.max_order_lots, client.max_total_lots = 2.0, 2.0
    client.manual_copy_profile = lambda sym, since: {'available': True, 'symbol': sym, 'since': since, 'count': 152,
        'decisions': 40, 'lot': 22.5, 'sl_points': None, 'tp_points': 289}
    client.now = 450
    bot._check_strategy_for_symbol('XAUUSD', tf=60)
    assert client.opened[0]['lot'] == 22.5
    assert client.bot_lot_override == (22.5, 22.5 * 2 + 2.0)       # one copied lot per timeframe + saved total
    assert (client.max_order_lots, client.max_total_lots) == (2.0, 2.0)
    assert bot.manual_copy['demo_full_lot'] and not bot.manual_copy['capped']


def test_lot_override_only_for_demo_bot_orders():
    from app.mt5_client import MT5Client
    from app.mt5_bridge import MANUAL_MAGIC
    c = object.__new__(MT5Client)
    c.max_order_lots, c.max_total_lots, c.max_open_orders = 2.0, 2.0, 10
    c.bot_lot_override, c.account_type = (22.5, 47.0), 'DEMO'
    assert c._limits_for(HMA_MAGIC) == (22.5, 47.0, 10)
    assert c._limits_for(MANUAL_MAGIC) == (2.0, 2.0, 10)        # manual/other orders keep the saved limits
    c.account_type = 'REAL'
    assert c._limits_for(HMA_MAGIC) == (2.0, 2.0, 10)          # never on a real account


def test_restart_on_same_candle_gets_a_new_order_id():
    """Stop + start within one candle: the trend entry of the new run must not reuse the earlier run's id
    (the order journal rejects an id with different lot/SL/TP: 'Aynı emir kimliği farklı işlem için kullanılamaz')."""
    import asyncio
    import threading
    rows, client, bot, fast, slow = trend_run()
    client.automation_stopped = threading.Event()
    client.now = 450
    ids = []

    async def run_twice():
        for _ in range(2):
            bot.is_running = False
            bot.task = None
            bot.start()
            bot.task.cancel()
            ids.append(bot.order_id('SELL', sym='XAUUSD', tf=60))
            await asyncio.sleep(0)
    asyncio.run(run_twice())
    assert ids[0] != ids[1]
    same_run = bot.order_id('SELL', sym='XAUUSD', tf=60)
    assert same_run == ids[1]          # within one run the id stays the same (one order per signal)
