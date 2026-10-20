"""Second review: broker clock in the loss streak, indicator warm-up and the MT5 price model of
the strategy test."""
import random
from unittest.mock import Mock

from app import bot_performance, signal_engine as se
from app.backtest import simulate
from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot

OFFSET = 10800   # broker server = UTC+3 (MT5_TICK_CLOCK_OFFSET_SECONDS)


def deal(position, entry, profit, broker_time):
    return {'position_id': position, 'entry': entry, 'profit': profit, 'commission': 0, 'swap': 0, 'fee': 0,
            'magic': HMA_MAGIC, 'symbol': 'XAUUSD', 'position_type': 'BUY', 'timestamp': broker_time}


def test_loss_streak_compares_broker_deal_times_with_the_utc_start_time():
    start_utc = 1_792_400_000
    # Both losses closed one hour BEFORE the bot started (UTC); MT5 shows them at +3 h server time.
    before = start_utc - 3600 + OFFSET
    deals = [deal(1, 0, 0, before - 600), deal(1, 1, -50, before),
             deal(2, 0, 0, before - 300), deal(2, 1, -40, before + 60)]
    client = Mock(tick_clock_offset=OFFSET)
    client.get_history.return_value = deals
    client.get_positions.return_value = []
    bot = StrategyBot(client)
    bot.max_consecutive_losses, bot.is_running, bot.started_at = 2, True, start_utc
    assert bot.check_loss_streak(force=True) == 0
    assert bot.is_running, 'losses from before the start must not stop the bot'


def test_performance_times_are_utc():
    rows = bot_performance.closed_positions([deal(1, 0, 0, 1_000_000 + OFFSET), deal(1, 1, 5, 1_000_600 + OFFSET)],
                                            clock_offset=OFFSET)
    assert rows[0]['opened_at'] == 1_000_000 and rows[0]['closed_at'] == 1_000_600


def test_scanner_history_test_gets_the_warm_up_in_front_of_the_window(monkeypatch):
    from app import main
    counts = []
    monkeypatch.setattr(main.mt5_client, 'get_rates', lambda sym, tf, count: counts.append(count) or None)
    main._scan_symbols_sync(['XAUUSD'], 15, 14, 34, stats=True, stats_bars=500)
    assert counts[0] >= se.history_bars({}) + 500


def candles(n=900, seed=5):
    rnd = random.Random(seed)
    p, rows = 2000.0, []
    for i in range(n):
        o = p
        p += rnd.gauss(0, 2)
        rows.append(dict(time=i * 900, open=o, high=max(o, p) + .6, low=min(o, p) - .6, close=p))
    return rows


def test_backtest_trades_the_same_crossovers_as_the_bot_and_scanner():
    rows = candles()
    result = simulate(rows, 14, 'EMA', 34, sl_points=0, point=.01, start=450)
    closes = [r['close'] for r in rows]
    crosses = [i for i in range(450, len(rows) - 1) if se.cross_at(se.hma_series(closes, 14), se.ema_series(closes, 34), i)]
    assert result['trades'][0]['opened_at'] == crosses[0] + 1          # executed at the next candle open
    assert result['tested_candles'] == len(rows) - 450


def test_backtest_uses_mt5_bid_candles_and_ask_for_sells():
    # One SELL crossover, then a candle whose Bid high stays below the stop but Ask (Bid + spread) reaches it.
    rows = candles(600, seed=8)
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    i = next(i for i in range(450, 590) if se.cross_at(fast, slow, i) == 'SELL')
    rows = rows[:i + 3]
    entry = rows[i + 1]['open']                      # a sell opens at Bid
    rows[i + 1] = dict(rows[i + 1], high=entry + 1.0, low=entry - .5, close=entry)
    rows[i + 2] = dict(rows[i + 2], open=entry, high=entry + 1.6, low=entry - .5, close=entry)
    res = simulate(rows, 14, 'EMA', 34, sl_points=200, point=.01, spread_points=50, start=0)
    trade = res['trades'][-1]
    # SL = entry + 2.00; Bid high 1.60 + spread 0.50 = 2.10 -> stop on the Ask, loss = 200 points.
    assert trade['side'] == 'SELL' and trade['reason'] == 'stop' and trade['net_points'] == -200


def test_backtest_atr_stops_follow_the_bot_risk_mode():
    rows = candles()
    res = simulate(rows, 14, 'EMA', 34, sl_points=0, point=.01, start=450, sl_atr=2.0, tp_atr=3.0)
    atr = se.atr_series(rows, 14)
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    first = next(i for i in range(450, len(rows) - 1) if se.cross_at(fast, slow, i))
    trade = res['trades'][0]
    assert trade['opened_at'] == first + 1
    if trade['reason'] == 'stop':
        import math
        assert -trade['net_points'] >= math.ceil(atr[first] * 2 / .01) - 1e-6
