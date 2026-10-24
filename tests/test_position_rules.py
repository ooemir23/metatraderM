from types import SimpleNamespace as NS

import pytest

from app import mt5_bridge as bridge
from app.position_rules import PositionRules
from test_trade_safety import client, pos


def rules_for(client, profit=0.0, swap=0.0, margin=1000.0):
    p = pos(magic=0, side=0, ticket=10)
    p.profit, p.swap = profit, swap
    client.mt5.positions_get.side_effect = lambda **kw: [p]
    client.mt5.order_calc_margin.return_value = margin
    return PositionRules(client), p


def test_bridge_state_has_net_result_and_margin(client):
    _, p = rules_for(client, profit=40, swap=-5, margin=800)
    state = bridge.position_rule_state(client.mt5, 10, 1, 'test')
    assert state['profit'] == 35 and state['margin'] == 800 and not state['closed']
    client.mt5.order_calc_margin.assert_called_with(int(p.type), str(p.symbol), float(p.volume), float(p.price_open))


@pytest.mark.parametrize('kind,profit,loss,result,expected', [
    ('percent', 10, 0, 100, 'profit'), ('percent', 10, 0, 99.9, None),
    ('amount', 1000, 0, 1000, 'profit'), ('amount', 0, 100, -100, 'loss'),
    ('percent', 0, 10, -100.1, 'loss'), ('percent', 0, 10, -99, None), ('amount', 5, 5, 0, None)])
def test_hit_thresholds(kind, profit, loss, result, expected):
    rule = dict(profit_kind=kind, profit=profit or None, loss_kind=kind, loss=loss or None)
    assert PositionRules.hit(rule, dict(profit=result, margin=1000)) == expected


def test_rejects_bad_values_and_percent_without_margin(client):
    r, _ = rules_for(client, margin=0)
    with pytest.raises(ValueError):
        r.configure(10, 'percent', -1, 'amount', 0)
    with pytest.raises(ValueError):
        r.configure(10, 'percent', 10, 'amount', 0)
    assert r.configure(10, 'amount', 50, 'amount', 0)['enabled']
    assert r.configure(10, 'amount', 0, 'amount', 0)['enabled'] is False and not r.rules


def test_step_closes_when_target_reached_and_drops_rule(client):
    r, p = rules_for(client, profit=10, margin=1000)
    r.configure(10, 'percent', 10, 'amount', 0)
    r.step()
    assert 10 in r.rules and not client.mt5.order_send.called
    p.profit = 100
    client.close_position = lambda *a, **k: {'success': True}
    r.step()
    assert 10 not in r.rules


def test_step_keeps_rule_when_close_rejected(client):
    r, p = rules_for(client, profit=0, margin=1000)
    r.configure(10, 'amount', 0, 'amount', 20)
    p.profit = -25
    client.close_position = lambda *a, **k: {'success': False, 'error': 'x'}
    r.step()
    assert r.rules[10]['status'] == 'rejected'
