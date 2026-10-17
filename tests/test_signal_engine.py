"""Signal engine: indicator parity with the bot, each filter, and the history test."""
import base64
import math
import random
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import main, signal_engine as se
from app.strategy_bot import StrategyBot


def series(n=260, seed=7):
    rnd = random.Random(seed)
    price, rows = 100.0, []
    for i in range(n):
        o = price
        price = max(1.0, price + rnd.uniform(-0.6, 0.6) + 0.15 * math.sin(i / 9))
        rows.append({'time': 1_700_000_000 + i * 900, 'open': o, 'high': max(o, price) + rnd.uniform(0, .3),
                     'low': min(o, price) - rnd.uniform(0, .3), 'close': round(price, 5), 'tick_volume': rnd.randint(50, 150)})
    return rows


@pytest.mark.parametrize('ma_type, period', [('EMA', 34), ('SMA', 20), ('LWMA', 21), ('HMA', 30)])
def test_series_match_the_bot_calculations(ma_type, period):
    closes = [r['close'] for r in series()]
    rev = list(reversed(closes))
    fast, slow = se.hma_series(closes, 14), se.ma_series(closes, ma_type, period)
    for shift in (0, 1, 2, 17, 60):
        i = len(closes) - 1 - shift
        assert fast[i] == pytest.approx(StrategyBot.calc_hma(rev, 14, shift=shift), rel=1e-12)
        assert slow[i] == pytest.approx(StrategyBot.calc_second_ma(rev, ma_type, period, shift=shift), rel=1e-12)


def test_rsi_and_atr_match_the_previous_scanner():
    rows = series()
    closes = [r['close'] for r in rows]
    assert se.rsi_series(closes, 14)[-2] == main._calc_rsi(closes[:-1], 14)
    assert se.atr_series(rows, 14)[-2] == pytest.approx(main._calc_atr(rows[:-1], 14))


def test_ema_uses_the_mt5_seed():
    """MT5 iMA MODE_EMA starts from the first price, not from an SMA."""
    out = se.ema_series([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None] and out[2:] == [2.25, 3.125, 4.0625]


def test_history_bars_make_recursive_indicators_equal_to_full_history():
    """With history_bars() candles the value equals MT5's value computed on a much longer chart."""
    import math as _m
    closes = [100 + 5 * _m.sin(i / 7) + (i % 13) * .1 for i in range(6000)]
    s = {**se.DEFAULTS, 'ema_period': 100, 'rsi_period': 14}
    n = se.history_bars(s)
    assert se.ema_series(closes[-n:], 100)[-1] == pytest.approx(se.ema_series(closes, 100)[-1], rel=1e-9, abs=1e-9)
    assert se.rsi_series(closes[-n:], 14)[-1] == pytest.approx(se.rsi_series(closes, 14)[-1], rel=1e-9, abs=1e-9)
    assert n <= se.MAX_HISTORY_BARS


def test_adx_is_bounded_and_high_in_a_steady_trend():
    trend = [{'time': i, 'open': 100 + i, 'high': 101 + i, 'low': 99.5 + i, 'close': 100.8 + i} for i in range(120)]
    flat = [{'time': i, 'open': 100, 'high': 100.5 + (i % 2) * .1, 'low': 99.5 - (i % 2) * .1, 'close': 100} for i in range(120)]
    up, side = se.adx_series(trend, 14), se.adx_series(flat, 14)
    assert up[0] == 0.0 and all(0 <= v <= 100 for v in up if v is not None)  # MT5 iADX bar 0 = 0
    assert up[-1] > 40 and side[-1] < 20


def first_cross(rows, s):
    """Index of a closed candle with a crossover, and a rates slice ending one candle after it."""
    s = {**se.DEFAULTS, **s}
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, s['hma_period']), se.ma_series(closes, s['ma_type'], s['ema_period'])
    for i in range(len(rows) - 2, 60, -1):
        if se.cross_at(fast, slow, i):
            return rows[:i + 2], se.cross_at(fast, slow, i)
    raise AssertionError('no cross in sample')


def analyze(rows, **kw):
    extra = {k: kw.pop(k) for k in ('htf_rates', 'spread_points', 'point', 'utc_offset', 'stats') if k in kw}
    return se.analyze(rows, kw, 15, **extra)


def test_plain_cross_passes_and_filters_block_with_reason():
    rows, side = first_cross(series(), {})
    base = analyze(rows)
    assert base['signal'] == side + '_CROSS' and not base['filters']
    adx = analyze(rows, adx_filter=True, adx_min=100)
    assert adx['signal'] == 'FILTERED' and adx['filters'] == ['adx'] and 'yatay piyasa' in adx['reason']
    spread = analyze(rows, max_spread_points=10, spread_points=25, point=.001)
    assert spread['filters'] == ['spread'] and 'Spread 25 puan > 10' in spread['reason']
    assert analyze(rows, max_spread_points=30, spread_points=25, point=.001)['signal'] == side + '_CROSS'
    rr = analyze(rows, min_rr=2, sl_atr=2, tp_atr=3)
    assert rr['filters'] == ['rr'] and rr['reward_risk'] == 1.5
    vol = analyze([*rows[:-2], {**rows[-2], 'tick_volume': 1}, rows[-1]], volume_filter=True)
    assert vol['filters'] == ['volume']


def test_session_filter_uses_utc_candle_time():
    rows, side = first_cross(series(), {})
    hour = ((rows[-2]['time']) % 86400) // 3600
    inside = next(k for k, (a, b) in se.SESSIONS.items() if a <= hour < b) if any(
        a <= hour < b for a, b in se.SESSIONS.values()) else None
    outside = next(k for k, (a, b) in se.SESSIONS.items() if not a <= hour < b)
    blocked = analyze(rows, sessions=(outside,))
    assert blocked['filters'] == ['session'] and f'{hour:02d}:00 UTC' in blocked['reason']
    if inside:
        assert analyze(rows, sessions=(inside,))['signal'] == side + '_CROSS'
    # Broker time 3 h ahead of UTC moves the candle into another hour.
    shifted = analyze(rows, sessions=(outside,), utc_offset=3 * 3600)
    assert f'{(hour - 3) % 24:02d}:00 UTC' in shifted['reason'] or not shifted['filters']


def test_cooldown_blocks_a_cross_soon_after_the_previous_one():
    rows = series(400, seed=3)
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ma_series(closes, 'EMA', 34)
    crosses = [i for i in range(60, len(rows) - 1) if se.cross_at(fast, slow, i)]
    gap, i = min((b - a, b) for a, b in zip(crosses, crosses[1:]))
    item = analyze(rows[:i + 2], cooldown_bars=gap)
    assert item['filters'] == ['cooldown'] and f'{gap} mum önce' in item['reason']
    assert analyze(rows[:i + 2], cooldown_bars=gap - 1)['filters'] == [] if gap > 1 else True


def test_higher_timeframe_trend_must_agree():
    rows, side = first_cross(series(), {})
    up = [{'time': rows[0]['time'] - 3600 * 80 + k * 3600, 'open': 100 + k, 'high': 101 + k, 'low': 99 + k, 'close': 100.5 + k}
          for k in range(80 + len(rows) // 4)]
    down = [{**r, 'close': 300 - r['close'], 'open': 300 - r['open'], 'high': 300 - r['low'], 'low': 300 - r['high']} for r in up]
    agree, disagree = (up, down) if side == 'BUY' else (down, up)
    ok = analyze(rows, htf_filter=True, htf_rates=agree)
    assert ok['signal'] == side + '_CROSS' and 'H1 trendi' in ok['reason']
    bad = analyze(rows, htf_filter=True, htf_rates=disagree)
    assert bad['filters'] == ['htf'] and 'Üst zaman dilimi (H1)' in bad['reason']
    assert analyze(rows, htf_filter=True, htf_rates=[])['filters'] == ['htf'], 'unknown HTF trend blocks'


def test_htf_uses_only_closed_higher_bars():
    s = {**se.DEFAULTS}
    rates = [{'time': k * 3600, 'close': 100 + k} for k in range(60)] + [{'time': 60 * 3600, 'close': 1}]
    htf = se.HtfTrend(rates, 60, s)
    assert htf.at(60 * 3600 + 900) == 'BUY', 'the forming H1 bar (close=1) is ignored'


def test_history_stats_count_wins_losses_and_filters():
    rows = series(600, seed=11)
    item = analyze(rows, stats=True)
    st = item['stats']
    assert st['available'] and st['crosses'] >= st['passed'] > 0
    assert st['wins'] + st['losses'] + st['open'] == st['passed']
    if st['wins'] + st['losses']:
        assert st['avg_r'] == pytest.approx((st['wins'] * 1.5 - st['losses']) / (st['wins'] + st['losses']), abs=.01)
    filtered = analyze(rows, stats=True, adx_filter=True, adx_min=100)['stats']
    assert filtered['passed'] == 0 and filtered['filter_counts'] == {'adx': filtered['crosses']}
    assert analyze(rows, stats=True, tp_atr=0)['stats']['available'] is False


def test_history_stats_sl_first_when_a_candle_touches_both():
    rows = [{'time': i, 'open': 100, 'high': 100.2, 'low': 99.8, 'close': 100, 'tick_volume': 1} for i in range(80)]
    ctx = se.Context(rows, {**se.DEFAULTS}, 15)
    ctx.fast = [None] * 60 + [1.0] + [2.0] * 19
    ctx.slow = [None] * 60 + [1.5] * 20
    ctx.atr = [1.0] * 80
    rows[62] = {**rows[62], 'high': 110, 'low': 90}
    st = se.history_stats(ctx)
    assert st['passed'] == 1 and st['losses'] == 1 and st['wins'] == 0


def web():
    token = base64.b64encode(b'test:test-panel-password').decode()
    return TestClient(main.app, headers={'Authorization': 'Basic ' + token})


def test_endpoint_passes_filters_and_stats(monkeypatch):
    rows = series(700)
    calls = []
    monkeypatch.setattr(main.mt5_client, 'get_rates', Mock(side_effect=lambda s, tf, n: calls.append((tf, n)) or rows[-n:]))
    monkeypatch.setattr(main.mt5_client, 'get_symbol_price', Mock(return_value={'bid': 1, 'ask': 1.0002, 'spread': 20, 'point': .00001}))
    res = web().get('/api/signals/scan', params={'symbols': 'EURUSD', 'timeframe_minutes': 15, 'htf_filter': 'true',
                                                 'adx_filter': 'true', 'sessions': 'london,NEWYORK', 'stats': 'true',
                                                 'stats_bars': 300, 'max_spread_points': 50, 'min_rr': 1})
    assert res.status_code == 200
    body = res.json()
    assert body['settings']['sessions'] == ['LONDON', 'NEWYORK']
    item = body['signals'][0]
    assert item['spread_points'] == 20 and 'stats' in item and item['stats']['bars'] == 300
    assert (60, calls[1][1]) == calls[1], 'auto higher timeframe for M15 is H1'


@pytest.mark.parametrize('params', [{'sessions': 'TOKYO'}, {'htf_minutes': 15}, {'htf_minutes': 7}, {'min_rr': -1},
                                    {'cooldown_bars': 500}, {'stats_bars': 50}])
def test_endpoint_rejects_invalid_filters(monkeypatch, params):
    monkeypatch.setattr(main.mt5_client, 'get_rates', Mock(return_value=series()))
    assert web().get('/api/signals/scan', params={'symbols': 'EURUSD', 'timeframe_minutes': 15, **params}).status_code == 422
