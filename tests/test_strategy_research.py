import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import mt5_bridge
from app.strategy_research import (Settings, closed_higher_indices, compare,
                                   indicators, run_segment, validate_rates)


def history(count=3200, seconds=3600):
    rows = []
    for i in range(count):
        o = 2000 + 40 * math.sin(i / 24) + 3 * math.sin(i / 3)
        c = 2000 + 40 * math.sin((i + 1) / 24) + 3 * math.sin((i + 1) / 3)
        rows.append(dict(time=1700006400 + i * seconds, open=o, high=max(o, c) + 2,
                         low=min(o, c) - 2, close=c, spread=25, tick_volume=10))
    return rows


def aggregate(rows):
    return [dict(time=rows[i]['time'], open=rows[i]['open'], high=max(r['high'] for r in rows[i:i+4]),
                 low=min(r['low'] for r in rows[i:i+4]), close=rows[i+3]['close'], spread=25)
            for i in range(0, len(rows) - 3, 4)]


def test_indicator_prefix_does_not_read_future_and_er_bounds():
    rows = history(250)
    whole, prefix = indicators(rows, Settings()), indicators(rows[:180], Settings())
    for key in whole:
        assert whole[key][:180] == prefix[key]
    assert all(0 <= e <= 1 + 1e-12 for e in whole['er'] if e is not None)
    assert all(a > 0 for a in whole['atr'] if a is not None)


def test_kama_and_wilder_atr_hand_calculation():
    rows = [dict(time=i * 3600, open=100+i, high=102+i, low=99+i, close=100+i) for i in range(30)]
    s = Settings(kama_period=2, kama_fast=2, kama_slow=30, atr_period=2)
    data = indicators(rows, s)
    assert data['er'][2] == 1
    assert data['kama'][1] == 100.5
    assert data['kama'][2] == pytest.approx(100.5 + (2/3)**2 * 1.5)
    assert data['atr'][2] == 3
    flat = [dict(r, open=100, high=100, low=100, close=100) for r in rows]
    assert indicators(flat, s)['er'][2] == 0


def test_h4_not_visible_before_close():
    higher = [dict(time=0), dict(time=14400)]
    lower = [dict(time=t) for t in (0, 7200, 10800, 14400, 25200)]
    assert closed_higher_indices(lower, higher) == [-1, -1, 0, 0, 1]


def confirmation_data():
    rows = [dict(time=i*3600, open=100, high=100.2, low=99.8, close=100) for i in range(10)]
    kama = [100 + i * .001 for i in range(10)]
    offsets = [-.1, -.1, .02, .05, .2, .3, .4, .5, .6, .7]
    study = {'hma': [k+d for k, d in zip(kama, offsets)], 'kama': kama,
             'atr': [1.] * 10, 'er': [1.] * 10}
    s = Settings(point=.01, spread_points=0, target_atr=0, stop_atr=10)
    return rows, study, s


def test_candidate_expires_or_confirms_at_next_open():
    rows, study, s = confirmation_data()
    expired = run_segment(rows, study, 3600, 0, 36000, s, wait=1, distance=.1)
    confirmed = run_segment(rows, study, 3600, 0, 36000, s, wait=2, distance=.1)
    assert expired['stats']['count'] == 0
    assert expired['signals']['filtered_at_deadline'] == 1
    assert confirmed['trades'][0]['signal_time'] == 5*3600
    assert confirmed['trades'][0]['entry_time'] == 5*3600


def test_reverse_closes_even_when_new_entry_filter_fails():
    rows, study, s = confirmation_data()
    study['hma'][5:] = [k-.1 for k in study['kama'][5:]]
    study['er'][5:] = [0.] * 5
    result = run_segment(rows, study, 3600, 0, 36000, s)
    assert len(result['trades']) == 1
    assert result['trades'][0]['reason'] == 'opposite crossover'
    assert result['trades'][0]['exit_time'] == 6*3600


def test_bid_ask_costs_and_adverse_drawdown():
    rows, study, s = confirmation_data()
    s = replace(s, spread_points=10, commission_points=5, slippage_points=2)
    r = run_segment(rows, study, 3600, 0, 36000, s)
    assert r['stats']['net_points'] == pytest.approx(-19)
    assert r['stats']['max_drawdown_points'] >= 39 - 1e-6


def test_both_limits_touched_counts_stop_and_gap_fill():
    rows, study, s = confirmation_data()
    rows[3].update(open=100, close=100, low=95, high=105)
    r = run_segment(rows, study, 3600, 0, 36000, replace(s, stop_atr=1, target_atr=1))
    assert r['trades'][0]['reason'] == 'stop'
    assert r['trades'][0]['net_points'] == -100


def test_split_positions_do_not_cross_boundary():
    rows, study, s = confirmation_data()
    first = run_segment(rows, study, 3600, 0, 5*3600, s)
    second = run_segment(rows, study, 3600, 5*3600, 10*3600, s)
    assert first['trades'][0]['reason'] == 'segment end'
    assert first['trades'][0]['exit_time'] == 5*3600
    assert second['stats']['count'] == 0


def test_holdout_changes_cannot_change_selection():
    rows = history()
    s = Settings(min_train_trades=1)
    before = compare(rows, aggregate(rows), s)
    boundary = before['segments']['holdout']['start']
    changed = [dict(r, open=r['open']*2, close=r['close']*2, high=r['high']*2, low=r['low']*2)
               if r['time'] >= boundary else r for r in rows]
    after = compare(changed, aggregate(changed), s)
    assert before['selected'] == after['selected']
    assert len(before['comparisons']) == 51
    assert before['comparisons'] == after['comparisons']
    assert before['assessment']['status'] in ('failed_validation', 'insufficient_evidence', 'positive_simulation', 'no_candidate')


def test_reject_bad_data_or_missing_spread():
    rows = history()
    duplicate = [rows[0]] + rows
    with pytest.raises(ValueError, match='zaman'):
        validate_rates(duplicate, 3600)
    rows[0] = dict(rows[0], high=1)
    with pytest.raises(ValueError, match='OHLC'):
        validate_rates(rows, 3600)
    with pytest.raises(ValueError, match='zaman diliminden'):
        validate_rates(history(seconds=900), 3600)
    rows = history()
    del rows[0]['spread']
    with pytest.raises(ValueError, match='spread'):
        compare(rows, aggregate(rows), Settings(historical_spread=True))


def test_bridge_excludes_forming_bar_and_paginates(monkeypatch):
    monkeypatch.setattr(mt5_bridge.time, 'time', lambda: 1700000000)
    calls = []
    def copy(symbol, tf, start, end):
        calls.append((start, end))
        return [(int(start.timestamp()), 100, 102, 99, 101, 1, 25, 0),
                (1699999900, 100, 102, 99, 101, 1, 25, 0)]
    mt5 = SimpleNamespace(TIMEFRAME_H1=1, copy_rates_range=copy)
    rows = mt5_bridge.research_rates(mt5, 'XAUUSD', 'TIMEFRAME_H1', 1680000000, 1700000000)
    assert len(calls) == 3
    assert all(r['time'] + 3600 <= 1700000000 for r in rows)
    assert all(r['spread'] == 25 for r in rows)


def test_api_validates_and_never_places_orders(monkeypatch):
    import app.main as main
    rows = history()
    calls = []
    def fetch(symbol, tf, start, end):
        calls.append(tf)
        return rows if tf == 60 else aggregate(rows)
    monkeypatch.setattr(main.mt5_client, 'get_research_rates', fetch)
    monkeypatch.setattr(main.mt5_client, 'get_symbol_spec', lambda _: {'point': .01, 'contract_size': 100, 'linear_profit': True,
        'profit_currency': 'USD', 'account_currency': 'USD', 'volume_min': .01, 'volume_max': 100, 'volume_step': .01})
    monkeypatch.setattr(main.mt5_client, 'get_research_margin_rules', lambda *args: margin_rules(volume=args[1]))
    def forbid(*args, **kwargs):
        raise AssertionError('Research cannot send orders')
    monkeypatch.setattr(main.mt5_client, 'open_order', forbid)
    web = TestClient(main.app)
    auth = ('test', 'test-panel-password')
    body = {'start': rows[0]['time'], 'end': rows[-1]['time']+3600, 'min_train_trades': 1}
    assert web.post('/api/research/compare', json=body).status_code == 401
    assert web.post('/api/research/compare', auth=auth, json=dict(body, end=body['start'])).status_code == 422
    assert not calls
    assert web.post('/api/research/compare', auth=auth, json=dict(body, lot_size=.015)).status_code == 422
    assert web.post('/api/research/compare', auth=auth, json=dict(body, initial_capital=0)).status_code == 422
    assert not calls
    r = web.post('/api/research/compare', auth=auth, json=body)
    assert r.status_code == 200, r.text
    assert calls == [60, 240]
    assert r.json()['assumptions']['live_trading_enabled'] is False
    assert r.json()['settings']['contract_size'] == 100
    assert r.json()['baseline_holdout']['stats']['currency'] == 'USD'
    response = web.get('/api/research/history', auth=auth,
                       params={'start': body['start'], 'end': body['end'], 'timeframe_minutes': 60})
    assert response.json()['count'] == len(rows)
    response = web.get('/api/research/history', auth=auth,
                       params={'start': body['start'], 'end': body['end'], 'timeframe_minutes': 240})
    assert response.json()['count'] == len(aggregate(rows))
    assert web.get('/api/research/history', auth=auth,
                   params={'start': body['start'], 'end': body['end'], 'timeframe_minutes': 15}).status_code == 422
    main.research_guard.acquire()
    try:
        assert web.post('/api/research/compare', auth=auth, json=body).status_code == 429
    finally:
        main.research_guard.release()


def test_client_rejects_account_change_during_chunked_history(monkeypatch):
    from app.mt5_client import MT5Client, MT5DataError
    monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
    client = MT5Client()
    monkeypatch.setattr(client, 'ensure_connected', lambda: True)
    identities = iter([[1, 'broker-a', 0], [2, 'broker-b', 0]])
    calls = []
    def bridge(operation, *args):
        calls.append(operation)
        if operation == 'account_status':
            return next(identities)
        return []
    monkeypatch.setattr(client, '_bridge', bridge)
    with pytest.raises(MT5DataError, match='hesabı değişti'):
        client.get_research_rates('XAUUSD', 60, 1700000000, 1710000000)
    assert calls == ['account_status', 'research_rates', 'account_status']


def test_money_stats_use_contract_lot_and_all_trades():
    from app.strategy_research import stats
    settings = Settings(point=.01, contract_size=100, currency='USD', lot_size=.2, initial_capital=1000)
    result = stats([{'net_points': x} for x in (100, -40, 0)], 120, settings)
    assert (result['wins'], result['losses'], result['breakeven']) == (1, 1, 1)
    assert result['gross_profit'] == 20
    assert result['gross_loss'] == 8
    assert result['net_profit'] == 12
    assert result['final_capital'] == 1012
    assert result['return_pct'] == 1.2
    assert result['max_drawdown_money'] == 24


def test_lot_scales_money_without_changing_signals_and_retains_every_trade():
    rows = history()
    settings = Settings(contract_size=100, currency='USD', min_train_trades=1)
    first = compare(rows, aggregate(rows), settings)
    larger = compare(rows, aggregate(rows), replace(settings, lot_size=.1))
    assert first['selected']['config'] == larger['selected']['config']
    for key in ('selected_holdout', 'baseline_holdout'):
        small, big = first[key], larger[key]
        assert len(small['trades']) == small['stats']['count']
        assert small['stats']['count'] == big['stats']['count']
        assert big['stats']['net_profit'] == pytest.approx(small['stats']['net_profit'] * 10, abs=.06)
        if small['trades']:
            assert all(t['lot_size'] == .01 for t in small['trades'])
            assert small['trades'][-1]['balance_after'] == small['stats']['final_capital']
            assert all(t['entry_price'] > 0 and t['exit_price'] > 0 for t in small['trades'])
    assert len(first['selected_trades']['train']) == first['selected']['train']['count']
    assert len(first['selected_trades']['validation']) == first['selected']['validation']['count']


def test_research_has_no_ten_trade_limit():
    rows = history()
    result = compare(rows, aggregate(rows), Settings(min_train_trades=1))
    training = result['selected_trades']['train']
    assert len(training) == result['selected']['train']['count'] > 10
    assert result['selected_signals']['train']['confirmed'] >= len(training)
    assert all('validation_signals' in comparison for comparison in result['comparisons'])


def test_trade_diagnostics_reconcile_bid_movement_and_signed_costs():
    rows = history()
    settings = Settings(historical_spread=True, commission_points=7, slippage_points=3,
                        swap_long_points_per_day=2, swap_short_points_per_day=-1,
                        min_train_trades=1, contract_size=100, currency='USD')
    result = compare(rows, aggregate(rows), settings)
    trades = result['baseline_holdout']['trades']
    assert trades and {t['side'] for t in trades} == {'BUY', 'SELL'}
    for trade in trades:
        d = trade['diagnostics']
        assert d['market_points'] - sum(d['cost_points'].values()) == pytest.approx(trade['net_points'], abs=2e-6)
        assert d['cost_points']['spread'] == 25
        assert d['cost_points']['slippage'] == (3 if trade['reason'] == 'target' else 6)
        assert d['entry_atr'] > 0 and 0 <= d['entry_er'] <= 1


def margin_rules(volume=.01, mode='PERCENT', stop=20):
    return {'mode':mode, 'margin_call':100, 'stop_out':stop, 'volume':volume,
            'models':{side:{'intercept':50, 'slope':0} for side in ('BUY','SELL')},
            'tick_size':.01, 'stops_level_points':0, 'leverage':100}


def margin_scenario(**changes):
    rows, study, s = confirmation_data()
    return rows, study, replace(s, contract_size=100, currency='USD', initial_capital=51,
                               stop_atr=95, broker_rules=margin_rules(), **changes)


@pytest.mark.parametrize('mode,expected', [('PERCENT',59),('MONEY',69)])
def test_stopout_uses_margin_or_money_not_initial_capital(mode, expected):
    rows, study, s = margin_scenario()
    s = replace(s, broker_rules=margin_rules(mode=mode))
    rows[3].update(low=50)
    result = run_segment(rows, study, 3600, 0, 36000, s)
    trade = result['trades'][0]
    assert trade['reason'] == 'stop out'
    assert trade['exit_price'] == pytest.approx(expected)
    assert trade['balance_after'] == (10 if mode == 'PERCENT' else 20)
    assert result['stats']['stop_outs'] == 1
    assert result['stats']['margin_calls'] == 1
    assert trade['diagnostics']['margin']['used_margin'] == 50


def test_stop_before_stopout_and_short_stopout():
    rows, study, s = margin_scenario()
    rows[3].update(low=50)
    r = run_segment(rows, study, 3600, 0, 36000, replace(s,stop_atr=1))
    assert r['trades'][0]['reason'] == 'stop'
    assert r['stats']['stop_outs'] == 0
    study['hma'] = [2*k-h for k,h in zip(study['kama'],study['hma'])]
    rows[3].update(low=99,high=150)
    r = run_segment(rows, study, 3600, 0, 36000, s, filtered=False)
    assert r['trades'][0]['side'] == 'SELL'
    assert r['trades'][0]['reason'] == 'stop out'
    assert r['trades'][0]['exit_price'] == pytest.approx(141)


def test_gap_stopout_can_exceed_threshold_and_margin_rejects_entries():
    rows, study, s = margin_scenario()
    rows[4].update(open=40,low=39,high=100)
    r = run_segment(rows, study, 3600, 0, 36000, s)
    assert r['trades'][0]['reason'] == 'stop out'
    assert r['trades'][0]['exit_time'] == rows[4]['time']
    assert r['stats']['final_capital'] == -9  # No invented negative balance protection.
    r = run_segment(rows, study, 3600, 0, 36000, replace(s, initial_capital=49))
    assert not r['trades'] and r['stats']['rejected_margin'] == 1
    r = run_segment(rows, study, 3600, 0, 36000, replace(s,broker_rules={**margin_rules(),'stops_level_points':10000}))
    assert not r['trades'] and r['stats']['rejected_stops'] == 1


def test_bridge_calibrates_asymmetric_margin_and_rejects_missing_nonlinear_rules():
    account = SimpleNamespace(login=1,server='demo',margin_mode=2,margin_so_mode=0,
                              margin_so_call=100,margin_so_so=30,leverage=100)
    info = SimpleNamespace(volume_min=.01,volume_max=100,volume_step=.01,margin_initial=0,
                           margin_maintenance=0,point=.01,trade_tick_size=.01,trade_stops_level=10)
    mt5 = SimpleNamespace(account_info=lambda:account,symbol_info=lambda _:info,
                          order_calc_margin=lambda side,sym,vol,price: vol*price*(1 if side==0 else 2))
    rules = mt5_bridge.research_margin_rules(mt5,'XAUUSD',.01,1000,5000,1,'demo')
    assert rules['stop_out'] == 30
    assert rules['models']['BUY']['slope'] == pytest.approx(.01)
    assert rules['models']['SELL']['slope'] == pytest.approx(.02)
    mt5.order_calc_margin=lambda *args:None
    with pytest.raises(ValueError,match='unavailable'):
        mt5_bridge.research_margin_rules(mt5,'XAUUSD',.01,1000,5000,1,'demo')
    mt5.order_calc_margin=lambda side,sym,vol,price: price**2
    with pytest.raises(ValueError,match='Nonlinear'):
        mt5_bridge.research_margin_rules(mt5,'XAUUSD',.01,1000,5000,1,'demo')
