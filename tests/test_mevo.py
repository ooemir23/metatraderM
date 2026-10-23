"""MevO: yalnız danışman; manuel MT5 işlemlerinden öğrenir, fırsat sunar, emir açmaz."""
from unittest.mock import Mock

import pytest

from app import mevo as mod


def trade(i, symbol='XAUUSD', side='BUY', net=10.0, vol=1.0, hour=10, hold=600, sl=0, tp=0, price=2000.0):
    t0 = 1_700_000_000 - (1_700_000_000 % 86400) + i * 86400 + hour * 3600
    return dict(position_id=i, symbol=symbol, side=side, volume=vol, open_time=t0, close_time=t0 + hold,
                open_price=price, close_price=price + 1, net=net, sl=sl, tp=tp, close_reason=0)


def test_stats_use_only_given_manual_trades_and_are_computed_by_code():
    rows = [trade(1, net=10, sl=1990, tp=2020), trade(2, side='SELL', net=-4, vol=3, hour=14),
            trade(3, net=6, vol=1, hold=1200)]
    s = mod.compute_stats(rows)
    assert s['count'] == 3 and s['wins'] == 2 and s['net'] == 12.0 and s['win_rate'] == 66.7
    g = s['symbols']['XAUUSD']
    assert (g['buy'], g['sell'], g['median_lot'], g['median_hold_min']) == (2, 1, 1.0, 10.0)
    assert g['with_sl'] == 1 and g['median_sl_pct'] == pytest.approx(0.5)
    assert s['hours']['10']['count'] == 2 and s['hours']['14']['net'] == -4.0


@pytest.mark.parametrize('item,ok', [
    ({'symbol': 'X', 'action': 'BUY', 'sl': 99, 'tp': 103, 'confidence': 70}, True),
    ({'symbol': 'X', 'action': 'SELL', 'sl': 101, 'tp': 97, 'confidence': 70}, True),
    ({'symbol': 'X', 'action': 'BUY', 'sl': 101, 'tp': 103}, False),      # SL girişin üstünde
    ({'symbol': 'X', 'action': 'SELL', 'sl': 99, 'tp': 97}, False),
    ({'symbol': 'X', 'action': 'BUY', 'sl': 'x', 'tp': 103}, False),
    ({'symbol': 'X', 'action': 'BUY', 'sl': float('nan'), 'tp': 103}, False),
])
def test_opportunity_levels_are_validated_against_the_broker_price(item, ok):
    r = mod.validate_opportunity(item, {'bid': 100.0, 'ask': 100.0})
    assert (r['action'] != 'WAIT') is ok
    if ok:
        assert r['rr'] > 0


def make(tmp_path, post=None, trades=None):
    mt5 = Mock(login_id=7, server='demo')
    mt5.get_manual_trades.return_value = trades if trades is not None else [trade(i, net=5) for i in range(6)]
    mt5.get_symbol_price.return_value = {'bid': 2000.0, 'ask': 2000.4}
    mt5.get_rates.return_value = [dict(time=i * 900, open=2000, high=2001, low=1999, close=2000) for i in range(60)]
    m = mod.MevO(mt5, api_key='test-key', path=str(tmp_path / 'mevo.json'))
    return m, mt5


def reply(content):
    r = Mock()
    r.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                           'usage': {'total_tokens': 50}}
    return r


def test_learn_then_opportunities_never_opens_orders(tmp_path, monkeypatch):
    m, mt5 = make(tmp_path)
    post = Mock(side_effect=[reply('{"summary":"x","rules":["r"]}'),
                             reply('{"opportunities":[{"symbol":"XAUUSD","action":"BUY","confidence":70,'
                                   '"sl":1990,"tp":2020,"matched_rule":"r","reason":"ok"}]}')])
    monkeypatch.setattr(mod.requests, 'post', post)
    monkeypatch.setattr(mod, 'MIN_INTERVAL', 0)
    assert m.learn()['success']
    res = m.opportunities(['XAUUSD'])
    assert res['success'] and res['items'][0]['action'] == 'BUY' and res['items'][0]['entry'] == 2000.4
    mt5.open_order.assert_not_called()
    # Yalnız MT5'ten elle açılan işlemler istenir ve oluşan mum gönderilmez.
    mt5.get_manual_trades.assert_called_once_with(days=mod.LEARN_DAYS)
    sent = post.call_args_list[1].kwargs['json']['messages'][1]['content']
    assert '"candles_ohlc"' in sent
    # Durum yeniden başlatmada hesap bazlı geri gelir.
    again = mod.MevO(mt5, api_key='k', path=str(tmp_path / 'mevo.json'))
    assert again.status()['profile'] and again.status()['opportunities']['items']


def test_learning_needs_enough_manual_trades_and_a_key(tmp_path, monkeypatch):
    post = Mock(side_effect=AssertionError('no paid call expected'))
    monkeypatch.setattr(mod.requests, 'post', post)
    m, _ = make(tmp_path, trades=[trade(1)])
    assert 'en az' in m.learn()['error']
    m2, _ = make(tmp_path)
    m2.api_key = ''
    assert 'API anahtarı' in m2.learn()['error']
    assert m2.opportunities(['XAUUSD'])['error'].startswith('Önce')


def test_profile_does_not_cross_accounts_and_limits_apply(tmp_path, monkeypatch):
    m, mt5 = make(tmp_path)
    monkeypatch.setattr(mod.requests, 'post', Mock(return_value=reply('{"summary":"x"}')))
    monkeypatch.setattr(mod, 'MIN_INTERVAL', 0)
    assert m.learn()['success']
    mt5.login_id = 8
    assert m.status()['profile'] is None
    mt5.login_id = 7
    m.daily_call_limit = 1
    assert 'sınırına' in m.learn()['error']


def test_api_key_is_never_stored_in_state_or_status(tmp_path, monkeypatch):
    m, _ = make(tmp_path)
    monkeypatch.setattr(mod.requests, 'post', Mock(return_value=reply('{"summary":"x"}')))
    m.learn()
    assert 'test-key' not in (tmp_path / 'mevo.json').read_text() and 'test-key' not in str(m.status())
