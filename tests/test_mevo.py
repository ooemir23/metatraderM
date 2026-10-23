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
    mt5 = Mock(login_id=7, server='demo', tick_clock_offset=0)
    mt5.get_bot_trades.return_value = []
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
    assert '"closes"' in sent and '"candles_ohlc"' not in sent
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
    m._acct()['profile']['digest'] = 'changed'          # önbelleği atla, günlük sınıra çarp
    assert 'sınırına' in m.learn()['error']


def test_api_key_is_never_stored_in_state_or_status(tmp_path, monkeypatch):
    m, _ = make(tmp_path)
    monkeypatch.setattr(mod.requests, 'post', Mock(return_value=reply('{"summary":"x"}')))
    m.learn()
    assert 'test-key' not in (tmp_path / 'mevo.json').read_text() and 'test-key' not in str(m.status())


def test_same_candle_and_unchanged_history_cost_no_tokens(tmp_path, monkeypatch):
    m, mt5 = make(tmp_path)
    post = Mock(side_effect=[reply('{"summary":"x","rules":["r"]}'),
                             reply('{"opportunities":[{"symbol":"XAUUSD","action":"WAIT","reason":"no"}]}')])
    monkeypatch.setattr(mod.requests, 'post', post)
    monkeypatch.setattr(mod, 'MIN_INTERVAL', 0)
    m.learn()
    assert m.learn()['cached'] and post.call_count == 1          # aynı geçmiş: yeniden öğrenme yok
    m.opportunities(['XAUUSD'])
    again = m.opportunities(['XAUUSD'])
    assert post.call_count == 2 and again['success'] and not again['used_model']   # aynı kapanmış mum: model çağrılmaz
    # Modele ham 40 mum değil kısa özet gider.
    sent = post.call_args_list[1].kwargs['json']['messages'][1]['content']
    assert len(sent) < 1500 and '"closes"' in sent


def test_key_is_validated_for_free_stored_privately_and_beats_env(tmp_path, monkeypatch):
    m, _ = make(tmp_path)
    m.api_key = None
    m._explicit_key = None
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'old-env-key')
    get = Mock(return_value=Mock(status_code=401))
    monkeypatch.setattr(mod.requests, 'get', get)
    assert 'reddetti' in m.set_key('sk-badbadbad')['error'] and m.api_key == 'old-env-key'
    get.return_value = Mock(status_code=200)
    assert m.set_key('sk-goodgoodgood')['success']
    assert m.api_key == 'sk-goodgoodgood' and m.key_source() == 'panel'
    assert 'sk-goodgoodgood' not in str(m.status())
    import os, stat
    assert stat.S_IMODE(os.stat(m._key_path).st_mode) == 0o600


def test_bot_losses_are_explained_by_difference_from_manual_winners():
    # Manuel kârlı: yalnız BUY, saat 10, lot 1, SL %0.5 uzakta, ~10 dk tutma.
    manual = [trade(i, net=8, vol=1.0, hour=10, hold=600, sl=1990, tp=2020) for i in range(4)]
    bad = [
        trade(20, side='SELL', net=-30, vol=1.0, hour=15, hold=600),                          # saat + yön
        trade(21, net=-20, vol=5.0, hour=10, hold=60, sl=1999.5),                              # lot, dar SL, erken stop
        trade(22, net=5, vol=1.0, hour=10),                                                   # kârlı bot: sayılmaz
    ]
    bad[1]['close_reason'] = 4
    c = mod.compare_bot_with_manual(manual, bad)
    keys = {x['key']: x for x in c['causes']}
    assert c['bot_count'] == 3 and c['bot_losses'] == 2 and c['bot_loss_total'] == -50.0
    assert set(keys) == {'hour', 'side', 'big_lot', 'tight_sl', 'early_stop', 'no_sl'}
    assert keys['side']['loss'] == -30.0 and keys['big_lot']['loss'] == -20.0
    assert mod.compare_bot_with_manual(manual, [trade(30, symbol='EURUSD', net=-5)])['causes'][0]['key'] == 'no_ref'


def test_trade_warnings_are_free_and_cite_bot_history():
    manual = [trade(i, net=8, hour=10, sl=1990) for i in range(4)]
    cmp_ = mod.compare_bot_with_manual(manual, [trade(20, side='SELL', net=-30, hour=15)])
    sell = {'symbol': 'XAUUSD', 'action': 'SELL'}
    w = mod.trade_warnings(sell, cmp_, 15, 0.1)
    assert any('15:00' in x and 'zarar edildi' in x for x in w) and any('BUY' in x for x in w) and any('SL' in x for x in w)
    assert mod.trade_warnings({'symbol': 'XAUUSD', 'action': 'BUY'}, cmp_, 10, 0.5) == []
    assert mod.trade_warnings({'symbol': 'XAUUSD', 'action': 'WAIT'}, cmp_, 15, 0.1) == []


def test_learn_sends_bot_summary_not_details_and_opportunities_carry_warnings(tmp_path, monkeypatch):
    m, mt5 = make(tmp_path)
    mt5.get_bot_trades.return_value = [trade(40, side='SELL', net=-12, hour=3)]
    post = Mock(side_effect=[reply('{"summary":"x","bot_loss_reasons":["saat"]}'),
                             reply('{"opportunities":[{"symbol":"XAUUSD","action":"SELL","confidence":60,"sl":2010,"tp":1980}]}'),
                             reply('{"summary":"y"}')])
    monkeypatch.setattr(mod.requests, 'post', post)
    monkeypatch.setattr(mod, 'MIN_INTERVAL', 0)
    assert m.learn()['success']
    sent = post.call_args_list[0].kwargs['json']['messages'][1]['content']
    assert '"loss_analysis"' in sent and '"reference"' not in sent and '"by_symbol"' not in sent
    res = m.opportunities(['XAUUSD'])
    assert res['items'][0]['warnings']          # manuel işlemler BUY baskın → SAT fırsatına uyarı
    mt5.get_bot_trades.side_effect = RuntimeError('x')
    m._acct()['profile']['digest'] = 'z'
    assert m.learn()['success']                 # bot geçmişi okunamazsa yalnız manuel öğrenme sürer


def test_manual_losing_trades_are_analysed_too():
    wins = [trade(i, net=8, vol=1.0, hour=10, hold=600, sl=1990) for i in range(4)]
    losers = [trade(10, net=-40, vol=1.0, hour=22, hold=3600),                    # saat, uzun tutma, SL yok
              trade(11, side='SELL', net=-25, vol=1.0, hour=10, hold=600, sl=1990)]  # yön
    c = mod.compare_bot_with_manual(wins + losers, [])
    m = c['manual']
    assert (m['count'], m['wins'], m['losses'], m['loss_total']) == (6, 4, 2, -65.0)
    keys = {x['key']: x for x in m['causes']}
    assert {'hour', 'held_long', 'no_sl', 'side'} <= set(keys) and keys['hour']['loss'] == -40.0
    w = mod.trade_warnings({'symbol': 'XAUUSD', 'action': 'BUY'}, c, 22, 0.5)
    assert w and 'manuel' in w[0]          # uyarı manuel zararlardan da beslenir
