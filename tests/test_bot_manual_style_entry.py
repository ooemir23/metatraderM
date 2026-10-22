"""'Manuel işlemlerim' in the Strategies dialog (2026-10-23): the bot enters on the manual-style signal
(app.manual_style, the same result as the 'Manuel Stil Sinyali' card) instead of the MA crossover."""
from app.mt5_bridge import HMA_MAGIC
from tests.test_bot_auto_trade_e2e import FakeMT5, live_bot, market


def run(plan, start=450, end=470):
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    bot.entry_mode = 'MANUAL_STYLE'
    calls = []

    def provider(sym, tf, candle_time):
        calls.append(candle_time)
        i = next(k for k, r in enumerate(rows) if r['time'] == candle_time)
        return {'status': 'OK', 'signal': {'side': plan.get(i, 'HOLD'), 'candle_time': candle_time, 'score': .7,
                                           'win_share': .8, 'experimental': True}}
    bot.manual_style_provider = provider
    for now in range(start, end):
        client.now = now
        bot._check_strategy_once()
    return client, bot, calls


def test_opens_on_new_manual_style_signal_and_reverses_on_opposite():
    # candle index -> signal on that closed candle (evaluated when candle index+1 opens)
    client, bot, _ = run({455: 'BUY', 456: 'BUY', 457: 'HOLD', 460: 'SELL'})
    assert [(o['candle'], o['side']) for o in client.opened] == [(456, 'BUY'), (461, 'SELL')]
    assert all(o['magic'] == HMA_MAGIC for o in client.opened)
    assert [c['candle'] for c in client.closed] == [461]          # reverse signal closes the BUY first
    assert [p['type'] for p in client.positions] == ['SELL']


def test_active_signal_at_start_opens_right_away():
    client, _, _ = run({449: 'SELL'}, start=450, end=452)
    assert [(o['candle'], o['side']) for o in client.opened] == [(450, 'SELL')]


def test_same_setup_again_only_after_hold():
    client, _, _ = run({452: 'BUY', 453: 'HOLD', 454: 'BUY'}, start=450, end=458)
    # BUY at 453 opens; after HOLD the next BUY is a new setup but a BUY position is already open
    assert [(o['candle'], o['side']) for o in client.opened] == [(453, 'BUY')]


def test_crossovers_do_not_trade_in_manual_style_mode():
    client, _, _ = run({}, start=450, end=650)
    assert client.opened == []


def test_stale_or_failing_analysis_is_retried_on_next_loop():
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    bot.entry_mode = 'MANUAL_STYLE'
    answers = iter([RuntimeError('busy'), 'ok'])

    def provider(sym, tf, candle_time):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return {'signal': {'side': 'BUY', 'candle_time': candle_time, 'score': .7}}
    bot.manual_style_provider = provider
    client.now = 450
    bot._check_strategy_once()
    assert client.opened == [] and 'busy' in bot.symbol_status['XAUUSD']['detail']
    bot._check_strategy_once()                      # same candle, retried
    assert [(o['candle'], o['side']) for o in client.opened] == [(450, 'BUY')]


def test_manual_style_mode_rejects_d1():
    import pytest
    from unittest.mock import Mock
    from app.strategy_bot import StrategyBot
    bot = StrategyBot(Mock())
    with pytest.raises(ValueError):
        bot.update_config({'entry_mode': 'MANUAL_STYLE', 'timeframe_minutes': 1440}, persist=False)
    bot.update_config({'entry_mode': 'MANUAL_STYLE', 'timeframe_minutes': 15}, persist=False)
    assert bot.entry_mode == 'MANUAL_STYLE'
