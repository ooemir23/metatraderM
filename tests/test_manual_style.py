"""Manual-style signals: learning from trades opened by hand in the original MT5 terminal."""
import base64
import math
import random
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import manual_style as ms
from app import mt5_bridge, signal_engine as se

T0 = 1785000000
TF = 15


def make_rates(n=3200, seed=7, drift=None):
    rnd = random.Random(seed)
    price, rows = 4000.0, []
    for i in range(n):
        step = rnd.gauss(0, 2.0) + (drift(i) if drift else 0.0)
        o = price
        price = max(100.0, price + step)
        rows.append(dict(time=T0 + i * TF * 60, open=o, high=max(o, price) + abs(rnd.gauss(0, 1)),
                         low=min(o, price) - abs(rnd.gauss(0, 1)), close=price, tick_volume=100))
    return rows


def trade(pid, side, rates, i, hold=8, symbol='XAUUSD', volume=1.0, offset=5):
    op, cp = rates[i + 1]['open'], rates[i + hold]['close']
    sgn = 1 if side == 'BUY' else -1
    return dict(position_id=pid, symbol=symbol, side=side, volume=volume, open_time=rates[i + 1]['time'] + offset,
                open_price=op, close_time=rates[i + hold]['time'], close_price=cp, net=round((cp - op) * sgn * 100, 2))


# ---------------------------------------------------------------- MT5 history (bridge)

def deal(ticket, position, kind, entry, t, price, volume=1.0, magic=0, comment='', profit=0.0, symbol='XAUUSD'):
    return SimpleNamespace(ticket=ticket, position_id=position, type=kind, entry=entry, time=t, time_msc=t * 1000,
                           price=price, volume=volume, magic=magic, comment=comment, profit=profit,
                           commission=-1.0, swap=0.0, fee=0.0, symbol=symbol, order=ticket)


def fake_mt5(deals, open_ids=()):
    return SimpleNamespace(
        account_info=lambda: SimpleNamespace(login=7, server='Tickmill-Demo', trade_mode=0, currency='USD'),
        history_deals_get=lambda *a, **k: deals,
        positions_get=lambda: [SimpleNamespace(identifier=i) for i in open_ids])


def test_manual_lifetimes_keeps_only_closed_terminal_manual_positions():
    deals = [
        # 1: manual, opened in two fills, closed in two: one row, VWAP prices, net = profit + costs
        deal(1, 100, 1, 0, 1000, 4150.0, 2.0), deal(2, 100, 1, 0, 1001, 4152.0, 2.0),
        deal(3, 100, 0, 1, 2000, 4140.0, 1.0, profit=1000), deal(4, 100, 0, 1, 2100, 4144.0, 3.0, profit=2400),
        deal(5, 200, 0, 0, 1000, 4100.0, magic=mt5_bridge.HMA_MAGIC), deal(6, 200, 1, 1, 1100, 4110.0, magic=mt5_bridge.HMA_MAGIC),
        deal(7, 300, 0, 0, 1000, 4100.0, comment='vm:panel'), deal(8, 300, 1, 1, 1100, 4110.0),   # panel order
        deal(9, 400, 0, 0, 1000, 4100.0), deal(10, 400, 1, 1, 1100, 4105.0, volume=0.5),            # partly open
        deal(11, 500, 0, 1, 900, 4100.0),                                                            # opening outside window
        deal(12, 600, 0, 0, 1000, 4100.0), deal(13, 600, 1, 1, 1200, 4101.0),                        # still open (listed)
        deal(14, 700, 2, 0, 1000, 0.0),                                                              # balance deal
    ]
    rows = mt5_bridge.manual_lifetimes(fake_mt5(deals, open_ids=[600]), 30, 10800, [7, 'Tickmill-Demo'])
    assert [r['position_id'] for r in rows] == [100]
    r = rows[0]
    assert r['side'] == 'SELL' and r['volume'] == 4.0
    assert r['open_time'] == 1000 and math.isclose(r['open_price'], 4151.0)
    assert r['close_time'] == 2100 and math.isclose(r['close_price'], 4143.0)
    assert r['net'] == 3400 - 4.0


def test_terminal_manual_rule_matches_panel_badge():
    assert mt5_bridge.is_terminal_manual(0, '')
    assert mt5_bridge.is_terminal_manual(0, 'sl 4100')
    for magic, comment in ((123461, ''), (0, 'HMA Bot'), (0, 'vm:abc'), (0, 'MevO AI'), (0, 'HMA Web')):
        assert not mt5_bridge.is_terminal_manual(magic, comment)


def test_manual_lifetimes_refuses_other_account():
    import pytest
    with pytest.raises(Exception):
        mt5_bridge.manual_lifetimes(fake_mt5([]), 30, 0, [8, 'Other-Server'])


# ---------------------------------------------------------------- learning

def test_positions_sent_together_are_one_decision():
    rates = make_rates(400)
    a, b = trade(1, 'SELL', rates, 300), trade(2, 'SELL', rates, 300, offset=40)
    c = trade(3, 'BUY', rates, 300, offset=50)              # other side: separate
    d = trade(4, 'SELL', rates, 310)                         # later: separate
    out = ms.merge_decisions([a, b, c, d], 120)
    assert [len(x['positions']) for x in out] == [2, 1, 1]
    assert out[0]['volume'] == 2.0 and out[0]['net'] == round(a['net'] + b['net'], 2)


def test_features_use_last_closed_candle_and_mt5_formulas():
    rates = make_rates(1200)
    m = ms.Market(rates, TF, ms.DEFAULTS)
    t = rates[1000]['time'] + 5                       # 5 s into candle 1000 -> last closed is 999
    assert m.last_closed_before(t) == 999
    assert m.last_closed_before(rates[1000]['time'] + TF * 60) == 1000
    closes = [r['close'] for r in rates]
    hma, ema = se.hma_series(closes, 14), se.ema_series(closes, 34)
    f_buy, f_sell = m.features(999, 'BUY'), m.features(999, 'SELL')
    assert f_buy['trend'] == (1.0 if hma[999] > ema[999] else -1.0)
    rsi = se.rsi_series(closes, 14)[999]
    assert f_sell['trend'] == -f_buy['trend']
    assert math.isclose(f_buy['rsi'], rsi) and math.isclose(f_sell['rsi'], 100 - rsi)
    assert math.isclose(f_buy['adx'], se.adx_series(rates, 14)[999])


def test_previous_day_levels_use_broker_server_days():
    rates = make_rates(1200)
    m = ms.Market(rates, TF, ms.DEFAULTS)
    i = 1000
    day = rates[i]['time'] // 86400
    prev = [r for r in rates if r['time'] // 86400 == day - 1]
    today = [r for r in rates if r['time'] // 86400 == day]
    assert math.isclose(m.pdh[i], max(r['high'] for r in prev))
    assert math.isclose(m.pdl[i], min(r['low'] for r in prev))
    assert math.isclose(m.dopen[i], today[0]['open'])
    f = m.features(i, 'SELL')
    a = m.atr[i]
    assert math.isclose(f['pdl'], max(-10, min(10, (m.pdl[i] - m.close[i]) / a)))
    assert ms.round_step(4150.3) == 10 and ms.round_step(1.3212) == 0.01


def test_findings_name_what_separates_wins_from_losses():
    rnd = random.Random(1)
    decisions = []
    for j in range(40):
        win = j % 2 == 0
        f = {k: rnd.gauss(0, 1) for k in ms.NAMES}
        f['london'] = 1.0 if win else 0.0          # wins all in London, losses never
        decisions.append(dict(win=win, features=f))
    base = [ms._vector({k: rnd.gauss(0, 1) for k in ms.NAMES}) for _ in range(300)]
    out = ms.findings(decisions, base, permutations=500)
    top = out['win_loss'][0]
    assert top['feature'] == 'london' and top['strength'] == 'belirgin'
    assert top['wins'] == 1.0 and top['losses'] == 0.0 and 'kazananlarda' in top['text']
    assert out['tested'] == len([k for k in ms.NAMES if ms.FEATURES[k][1] != 'hidden'])


def test_not_enough_decisions_gives_no_signal():
    rates = make_rates(1500)
    trades = [trade(i, 'SELL', rates, 1000 + 20 * i) for i in range(5)]
    r = ms.analyze(trades, rates, TF, symbol='XAUUSD')
    assert r['status'] == 'NOT_ENOUGH' and r['signal'] is None and '5/12' in r['message']


def test_random_entries_are_marked_unreliable():
    rates = make_rates(3200, seed=3)
    rnd = random.Random(5)
    trades = [trade(i, rnd.choice(['BUY', 'SELL']), rates, i) for i in range(1000, 3150, 53)]
    r = ms.analyze(trades, rates, TF, symbol='XAUUSD', spread=0.3)
    assert r['status'] == 'OK'
    assert r['verdict']['reliable'] is False
    if r['signal']:
        assert r['signal']['experimental'] is True
    assert r['history_test']['baseline']['count'] > 1000


def test_learns_a_consistent_habit_and_beats_entering_everywhere():
    # Market alternates long up/down legs; the trader sells in down legs after a bounce and wins.
    rates = make_rates(4000, seed=11, drift=lambda i: 1.4 * math.copysign(1, math.sin(i / 120)))
    closes = [r['close'] for r in rates]
    hma, ema = se.hma_series(closes, 14), se.ema_series(closes, 34)
    trades, pid, i = [], 1, 900
    while i < 3950:
        side = 'BUY' if hma[i] > ema[i] and hma[i] > hma[i - 1] else 'SELL' if hma[i] < ema[i] and hma[i] < hma[i - 1] else None
        if side:
            trades.append(trade(pid, side, rates, i))
            pid += 1
            i += 40
        else:
            i += 1
    r = ms.analyze(trades, rates, TF, symbol='XAUUSD')
    ht = r['history_test']
    assert r['status'] == 'OK' and ht['signals']['count'] >= 20
    assert ht['edge'] > 0.05 and ht['signals']['avg_move'] > ht['baseline']['avg_move']
    assert r['verdict']['reliable'] is True
    assert r['cross_check']['count'] == r['decisions']
    assert len(r['trades']) == r['decisions'] and r['positions'] == len(trades)


def test_walk_forward_never_uses_future_trades():
    rates = make_rates(3000, seed=2)
    trades = [trade(i, 'SELL', rates, 1000 + 30 * i) for i in range(40)]
    r = ms.analyze(trades, rates, TF, symbol='XAUUSD')
    first_close = min(t['close_time'] for t in trades)
    twelfth_close = sorted(t['close_time'] for t in trades)[11]
    for s in r['history_test']['recent']:
        assert s['candle_time'] + TF * 60 >= max(first_close, twelfth_close)


def test_other_symbols_are_not_mixed():
    rates = make_rates(2000)
    trades = [trade(i, 'SELL', rates, 1000 + 20 * i, symbol='GBPUSD') for i in range(30)]
    r = ms.analyze(trades, rates, TF, symbol='XAUUSD')
    assert r['decisions'] == 0 and r['status'] == 'NOT_ENOUGH'


# ---------------------------------------------------------------- API

def web():
    from app import main
    token = base64.b64encode(b'test:test-panel-password').decode()
    return main, TestClient(main.app, headers={'Authorization': 'Basic ' + token})


def test_api_returns_learned_signal_and_validates(monkeypatch):
    main, client = web()
    main._MANUAL_STYLE_CACHE.clear()
    rates = make_rates(3200, seed=3)
    rnd = random.Random(5)
    trades = [trade(i, rnd.choice(['BUY', 'SELL']), rates, i) for i in range(1000, 3150, 53)]
    get_trades = Mock(return_value=trades)
    get_rates = Mock(return_value=rates)
    monkeypatch.setattr(main.mt5_client, 'get_manual_trades', get_trades)
    monkeypatch.setattr(main.mt5_client, 'get_rates', get_rates)
    monkeypatch.setattr(main.mt5_client, 'get_symbol_price', lambda s: {'spread': 30, 'point': 0.01})
    assert client.get('/api/manual-style', params={'timeframe_minutes': 7}).status_code == 422
    assert client.get('/api/manual-style', params={'days': 3}).status_code == 422
    get_trades.assert_not_called()
    res = client.get('/api/manual-style', params={'symbol': 'XAUUSD', 'timeframe_minutes': 15, 'days': 30})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data['status'] == 'OK' and data['time_basis'] == 'broker'
    assert data['manual_symbols'] == {'XAUUSD': len(trades)}
    assert math.isclose(data['spread'], 0.3)
    assert data['verdict']['reliable'] is False
    get_trades.assert_called_once_with(days=30)
    # Cached for a minute; refresh=true recomputes.
    client.get('/api/manual-style', params={'symbol': 'XAUUSD', 'timeframe_minutes': 15, 'days': 30})
    assert get_trades.call_count == 1
    client.get('/api/manual-style', params={'symbol': 'XAUUSD', 'timeframe_minutes': 15, 'days': 30, 'refresh': True})
    assert get_trades.call_count == 2


def test_api_without_candles_does_not_invent_data(monkeypatch):
    main, client = web()
    main._MANUAL_STYLE_CACHE.clear()
    monkeypatch.setattr(main.mt5_client, 'get_manual_trades', lambda days: [])
    monkeypatch.setattr(main.mt5_client, 'get_rates', lambda *a: None)
    res = client.get('/api/manual-style', params={'refresh': True})
    assert res.status_code == 503 and 'MT5 mum verisi' in res.json()['detail']
