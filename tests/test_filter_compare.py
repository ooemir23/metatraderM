import math

from app import filter_compare
from app.backtest import simulate


def candles(n=400):
    rows = []
    for k in range(n):
        base = 100 + 8*math.sin(k/9) + k*0.02
        rows.append(dict(time=1_700_000_000 + k*900, open=base, high=base+0.6, low=base-0.6, close=base+0.1*math.sin(k)))
    return rows


def test_filter_blocks_entries_but_plain_matches_simulate():
    rates = candles()
    plain = simulate(rates, 14, 'SMA', 20, 50, 0, .01, 0, 0, start=60)
    blocked = simulate(rates, 14, 'SMA', 20, 50, 0, .01, 0, 0, start=60, entry_filter=lambda i, side: False)
    allowed = simulate(rates, 14, 'SMA', 20, 50, 0, .01, 0, 0, start=60, entry_filter=lambda i, side: True)
    assert plain['total_trades'] > 3 and blocked['total_trades'] == 0
    assert allowed['all'] == plain['all']


def test_compare_variants_without_htf_skips_trend_rows_and_rsi_only_reduces_trades():
    rates = candles()
    rows = filter_compare.compare(rates, None, 60, 15, hma_period=14, ma_type='SMA', ma_period=20, sl_points=50, tp_points=0,
                                  point=.01, spread_points=0, commission_points=0, start=60)
    assert [r['key'] for r in rows] == ['plain', 'rsi']
    assert rows[1]['count'] <= rows[0]['count']
