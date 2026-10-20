"""Reverse crossover handling, signal-only mode and cross-age wording (bot and scanner agree).

HMA_Crossover_EA ProcessSignal: on a crossover it first closes the opposite EA position, then checks
whether a new one may be opened. Entry-only filters of the panel must not keep a position open
against the new crossover, and "Sadece Sinyal" (signal-only) must never send anything.
"""
import random
from unittest.mock import Mock

from app import signal_engine as se
from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot


def rates_with_cross(direction, n=600, seed=3):
    """Random walk whose last closed candle (rows[-2]) is an HMA14 x EMA34 crossover."""
    rnd = random.Random(seed)
    for _ in range(4000):
        p, rows = 1.10, []
        for i in range(n):
            o = p
            p += rnd.gauss(0, .0008)
            rows.append(dict(time=1_700_000_000 + i * 3600, open=o, high=max(o, p) + .0002,
                             low=min(o, p) - .0002, close=p, tick_volume=100))
        closes = [r['close'] for r in rows]
        if se.cross_at(se.hma_series(closes, 14), se.ema_series(closes, 34), n - 2) == direction:
            return rows
    raise AssertionError('no crossover generated')


BUY_ROWS = rates_with_cross('BUY')
SELL_BOT_POSITION = [{'ticket': 5, 'symbol': 'EURUSD', 'type': 'SELL', 'magic': HMA_MAGIC}]


def running_bot(positions=(), mode='AUTO_TRADE', rows=BUY_ROWS, **settings):
    client = Mock(login_id=7, server='demo', account_type='DEMO', max_order_lots=1.0)
    client.ensure_connected.return_value = True
    client.get_rates.return_value = rows
    client.has_unresolved_bot_order.return_value = False
    client.get_positions.return_value = list(positions)
    client.get_symbol_spec.return_value = {'point': .00001, 'digits': 5}
    client.get_symbol_price.return_value = {'spread': 5}
    client.close_position.return_value = {'success': True}
    client.open_order.return_value = {'success': True, 'ticket': 99, 'price': 1.1}
    bot = StrategyBot(client)
    bot.symbol, bot.symbols, bot.timeframe_minutes, bot.execution_mode = 'EURUSD', ['EURUSD'], 60, mode
    for key, value in settings.items():
        setattr(bot, key, value)
    bot.is_running = True
    bot.last_candle_times = {'EURUSD': rows[-3]['time']}   # running; a new candle has just closed
    return bot, client


def test_reverse_cross_closes_opposite_and_opens_like_the_ea():
    bot, client = running_bot(SELL_BOT_POSITION)
    client.get_positions.side_effect = [SELL_BOT_POSITION, []]   # closed, then re-read before opening
    bot._check_strategy_for_symbol('EURUSD')
    assert client.close_position.call_args.args[0] == 5
    assert client.open_order.call_args.args[:2] == ('EURUSD', 'BUY')


def test_entry_filter_does_not_keep_a_position_against_the_new_cross():
    bot, client = running_bot(SELL_BOT_POSITION, use_rsi_filter=True, rsi_buy_max=50.0)
    bot.calc_rsi = lambda *a, **k: 99.0                      # RSI blocks the new BUY entry
    bot._check_strategy_for_symbol('EURUSD')
    client.close_position.assert_called_once()              # the SELL still closes on the BUY cross
    client.open_order.assert_not_called()                   # ... but no BUY is opened
    assert 'RSI' in bot.symbol_status['EURUSD']['detail']


def test_signal_only_never_closes_or_opens_even_with_cross_confirmation():
    for extra in ({}, {'use_atr_filter': True, 'confirmation_bars': 1}):
        bot, client = running_bot(SELL_BOT_POSITION, mode='SIGNAL_ONLY', **extra)
        bot._check_strategy_for_symbol('EURUSD')
        client.close_position.assert_not_called()
        client.open_order.assert_not_called()


def test_unresolved_order_stops_before_anything_is_closed():
    bot, client = running_bot(SELL_BOT_POSITION)
    client.has_unresolved_bot_order.return_value = True
    bot._check_strategy_for_symbol('EURUSD')
    client.close_position.assert_not_called()
    client.open_order.assert_not_called()
    assert not bot.is_running and bot.runtime_state == 'ORDER_UNRESOLVED'


def test_failed_close_sends_no_new_order():
    bot, client = running_bot(SELL_BOT_POSITION)
    client.close_position.return_value = {'success': False, 'error': 'Market closed'}
    bot._check_strategy_for_symbol('EURUSD')
    client.open_order.assert_not_called()
    assert 'ters bot pozisyonu kapatılamadı' in bot.symbol_status['EURUSD']['detail']


def test_caught_signal_has_one_id_per_candle():
    bot, _ = running_bot(mode='SIGNAL_ONLY')
    bot._check_strategy_for_symbol('EURUSD')
    action = bot.pending_signal_actions[0]
    assert action['id'] == f"bot-EURUSD-60-BUY-{BUY_ROWS[-2]['time']}"


def test_cross_age_counts_like_the_scanner():
    bot, _ = running_bot()
    closes = [r['close'] for r in reversed(BUY_ROWS)]
    assert bot._bars_since_cross(closes)[0] == se.analyze(BUY_ROWS, {}, 60)['bars_ago'] == 1
    older = BUY_ROWS + [dict(BUY_ROWS[-1], time=BUY_ROWS[-1]['time'] + 3600 * k) for k in (1, 2)]
    # Two more candles without a cross: both say 3 candles ago (same counting).
    scan = se.analyze(older, {}, 60)
    bars = bot._bars_since_cross([r['close'] for r in reversed(older)])[0]
    assert scan['signal'] != 'BUY_CROSS' and bars == scan['bars_ago']


def test_reward_risk_pays_the_spread_once_per_side():
    ctx = se.Context(BUY_ROWS, {**se.DEFAULTS, 'sl_atr': 2, 'tp_atr': 3}, 60, spread_points=20, point=.00001)
    atr, spread = .001, .0002
    # Buy: opens at Ask, SL/TP trigger on Bid -> spread widens risk and shrinks reward.
    assert abs(ctx.reward_risk(atr, 'BUY') - (3 * atr - spread) / (2 * atr + spread)) < 1e-12
    # Sell: opens at Bid, SL/TP trigger on Ask -> distances stay 3/2; the spread is in the trigger.
    assert abs(ctx.reward_risk(atr, 'SELL') - 1.5) < 1e-12
