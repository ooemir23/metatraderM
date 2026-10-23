"""MevO öğrenme/önlem: MAE, spread/volatilite, eşzamanlılık, kâr geri verme, ders defteri, uyarlanır eşik."""
from types import SimpleNamespace as NS

import pytest

from app import mevo as mod
from app import mt5_bridge as bridge


def t(i, net, **kw):
    base = dict(position_id=i, symbol='XAUUSD', side='BUY', volume=1.0, open_time=1_000_000 + i * 10_000,
                close_time=1_000_000 + i * 10_000 + 600, open_price=2000.0, close_price=2001.0, net=net,
                sl=1990.0, tp=2020.0, close_reason=0)
    base.update(kw)
    return base


WINNERS = [t(i, 10, mae_pct=0.10 + i * .01, spread_pct=.002, atr_pct=.05, conc_n=1) for i in range(1, 6)]


def ref():
    return mod.winner_reference(WINNERS)


def test_winner_reference_has_context_stats_from_broker_bars():
    r = ref()['XAUUSD']
    assert r['mae_p75'] == pytest.approx(0.14, abs=1e-6)
    assert r['spread_ratio_p90'] == pytest.approx(.04) and r['conc_max'] == 1
    assert r['atr_lo'] == r['atr_hi'] == pytest.approx(.05)


def test_enrich_counts_overlapping_open_trades_and_attaches_context():
    a = t(1, 5); b = t(2, -5, open_time=a['open_time'] + 100)       # b opens while a is open
    rows = mod.enrich_trades([a, b], {'2': {'mae_pct': .3, 'junk': 9}}, [a, b])
    assert rows[0]['conc_n'] == 0 and rows[1]['conc_n'] == 1 and rows[1]['conc_lot'] == 1.0
    assert rows[1]['mae_pct'] == .3 and 'junk' not in rows[1]


@pytest.mark.parametrize('loss,cause', [
    (dict(sl=1998.0, close_reason=4), 'noise_sl'),                        # SL %0.1 < winners' MAE p75 %0.14
    (dict(spread_pct=.01, atr_pct=.05), 'wide_spread'),
    (dict(atr_pct=.01), 'low_vol'), (dict(atr_pct=.2), 'high_vol'),
    (dict(conc_n=3), 'crowded'),
    (dict(mfe_pct=.6), 'giveback'),                                        # TP %1.0 -> half = %0.5
])
def test_loss_causes_from_context(loss, cause):
    trade = t(9, -8, **loss)
    assert cause in mod.loss_causes(trade, ref())


def test_context_causes_are_silent_without_data():
    assert 'noise_sl' not in mod.loss_causes(t(9, -8, close_reason=4), ref())
    assert mod.context_causes(t(9, -8), {}) == []


def test_lessons_need_repeated_losses_and_guards_come_from_winners():
    causes = [{'key': 'noise_sl', 'label': mod.CAUSE_LABELS['noise_sl'], 'count': 3, 'loss': -30.0},
              {'key': 'crowded', 'label': mod.CAUSE_LABELS['crowded'], 'count': 1, 'loss': -5.0}]
    lessons = mod.build_lessons(causes)
    assert [l['key'] for l in lessons] == ['noise_sl'] and 'Önlem' in lessons[0]['text']
    g = mod.build_guards(ref())['XAUUSD']
    assert g['min_sl_pct'] == pytest.approx(.154, abs=1e-4) and g['max_open'] == 2


def test_adaptive_policy_raises_min_confidence_after_losses_and_recovers():
    bad = [t(i, -5 if i % 5 else 2) for i in range(1, 6)]
    assert mod.adaptive_policy(bad)['min_confidence'] == 75
    mixed = [t(i, 10) for i in range(1, 4)] + [t(i, -4) for i in range(4, 9)] + [t(9, 3)]
    assert mod.adaptive_policy(mixed)['min_confidence'] in (65, 70, 75)
    good = [t(i, 10) for i in range(1, 8)]
    assert mod.adaptive_policy(good)['min_confidence'] == 65
    assert mod.adaptive_policy([])['min_confidence'] == 65


def test_sl_guard_widens_too_tight_stop_and_keeps_tp():
    item = {'action': 'BUY', 'entry': 2000.0, 'sl': 1999.0, 'tp': 2010.0, 'rr': 10.0}
    mod.apply_sl_guard(item, {'min_sl_pct': .1})
    assert item['sl'] == pytest.approx(1998.0) and item['tp'] == 2010.0 and item['rr'] == 5.0 and item['sl_original'] == 1999.0
    sell = {'action': 'SELL', 'entry': 2000.0, 'sl': 2001.0, 'tp': 1990.0}
    mod.apply_sl_guard(sell, {'min_sl_pct': .1})
    assert sell['sl'] == pytest.approx(2002.0)
    ok = {'action': 'BUY', 'entry': 2000.0, 'sl': 1990.0, 'tp': 2010.0}
    assert 'adjusted' not in mod.apply_sl_guard(ok, {'min_sl_pct': .1})


def test_live_warnings_block_only_when_history_shows_repeated_losses():
    comparison = {'reference': ref(), 'by_symbol': {'XAUUSD': {'crowded': {'count': 2, 'loss': -20.0}}},
                  'manual_by_symbol': {}}
    item = {'symbol': 'XAUUSD', 'action': 'BUY'}
    w = mod.trade_warnings(item, comparison, 0, .2, [], {'conc_n': 4, 'spread_pct': .002, 'atr_pct': .05})
    assert any('4 açık işlem' in x for x in w) and mod.blocking_warnings(w)
    comparison['by_symbol'] = {'XAUUSD': {'crowded': {'count': 1, 'loss': -5.0}}}
    w = mod.trade_warnings(item, comparison, 0, .2, [], {'conc_n': 4})
    assert any('4 açık işlem' in x for x in w) and not mod.blocking_warnings(w)
    w = mod.trade_warnings(item, comparison, 0, .05, [], {})                # SL %0.05 < winners MAE
    assert any('ters hareketinden' in x for x in w)


class Rates(list):
    pass


def test_bridge_trade_contexts_measure_mae_mfe_spread_and_range():
    # M1 bars: [time, open, high, low, close, tick_volume, spread]
    m1 = [(0, 2000, 2003, 1999.0, 2001, 5, 20), (60, 2001, 2005, 2000.5, 2004, 5, 22)]
    m5 = [(i * 300, 2000, 2001.5, 1999.5, 2000, 5, 20) for i in range(10)]
    mt5 = NS(TIMEFRAME_M1=1, TIMEFRAME_M5=5,
             account_info=lambda: NS(login=7, server='s'),
             copy_rates_range=lambda sym, tf, a, b: m1 if tf == 1 else m5,
             symbol_info=lambda sym: NS(point=0.01))
    spec = dict(position_id=11, symbol='XAUUSD', side='BUY', open_time=1_000_000, close_time=1_000_120, open_price=2000.0)
    out = bridge.trade_contexts(mt5, [spec], (7, 's'))['11']
    assert out['mae_pct'] == pytest.approx(0.05) and out['mfe_pct'] == pytest.approx(0.25)
    assert out['spread_pct'] == pytest.approx(0.01) and out['atr_pct'] == pytest.approx(0.1)
    sell = bridge.trade_contexts(mt5, [{**spec, 'side': 'SELL'}], (7, 's'))['11']
    assert sell['mae_pct'] == pytest.approx(0.25) and sell['mfe_pct'] == pytest.approx(0.05)


def test_hours_never_reach_the_model_and_old_hour_rules_are_dropped():
    stats = mod.strip_hours(mod.compute_stats([t(1, 5), t(2, -3)]))
    assert stats['hours'] == {} and all('open_hour' not in r for r in stats['recent'])
    rules = ['Ağırlıklı SELL', 'Saat 04:00/16:00 kaçın', 'Gün içi 16.00 sonrası girme']
    assert mod.no_hour_rules(rules) == ['Ağırlıklı SELL']


def test_old_profile_without_version_is_relearned_before_opportunities(tmp_path, monkeypatch):
    from unittest.mock import Mock
    m = mod.MevO(Mock(login_id=1, server='s', tick_clock_offset=0), api_key='k', path=str(tmp_path / 'm.json'))
    m._acct()['profile'] = {'analysis': {}, 'stats': {'symbols': {}}, 'digest': 'x'}      # version missing = 1
    called = []
    monkeypatch.setattr(m, 'learn', lambda: called.append(1) or {'success': False})
    m.opportunities([])
    assert called == [1]
