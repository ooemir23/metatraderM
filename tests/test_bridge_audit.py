"""Broker execution regressions: every MT5 operation here uses an in-memory fake."""
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app import mt5_bridge as bridge


@pytest.fixture
def broker():
    mt5 = Mock()
    mt5.account_info.return_value = NS(login=7, server='demo', margin_mode=2, trade_mode=0,
                                       equity=1000., margin_free=900., currency='USD',
                                       trade_allowed=True, trade_expert=True)
    mt5.terminal_info.return_value = NS(connected=True, trade_allowed=True, tradeapi_disabled=False)
    mt5.symbol_select.return_value = True
    mt5.symbol_info.return_value = NS(point=.00001, digits=5, trade_tick_size=.00001,
        volume_min=.01, volume_max=100., volume_step=.01, volume_limit=0.,
        trade_stops_level=10, trade_freeze_level=0, filling_mode=3, trade_exemode=2,
        order_mode=127, expiration_mode=15, trade_mode=4)
    mt5.symbol_info_tick.return_value = NS(ask=1.1, bid=1.0999, time=time.time())
    mt5.positions_get.return_value = []
    mt5.orders_get.return_value = []
    mt5.history_deals_get.return_value = []
    mt5.order_check.return_value = NS(retcode=0, comment='checked')
    mt5.order_send.return_value = NS(retcode=10009, order=12, volume=.01, price=1.1, comment='done')
    mt5.order_calc_profit.return_value = -2.
    mt5.order_calc_margin.return_value = 10.
    return mt5


def open_order(broker, **changes):
    values = dict(symbol='EURUSD', order_type='BUY', volume=.01, sl_points=200, tp_points=400,
                  comment='audit', magic=bridge.MANUAL_MAGIC, daily_loss_limit=500,
                  expected_login=7, expected_server='demo')
    values.update(changes)
    return bridge.open_deal(broker, **values)


def position(volume=.03):
    return NS(ticket=22, identifier=22, symbol='EURUSD', type=0, magic=bridge.MANUAL_MAGIC,
              volume=volume, sl=1.09, tp=1.12, profit=0., swap=0.)


def invoke(broker, operation):
    if operation == 'market':
        return open_order(broker)
    if operation == 'pending':
        return open_order(broker, pending_type='BUY_LIMIT', entry_price=1.08)
    if operation == 'cancel':
        broker.orders_get.return_value = [NS(ticket=31)]
        return bridge.cancel_pending(broker, 31, 7, 'demo')
    broker.positions_get.return_value = [position()]
    if operation == 'close':
        return bridge.close_deal(broker, 22, expected_login=7, expected_server='demo')
    return bridge.manage_position(broker, 22, 'partial' if operation == 'partial' else 'stops',
                                  {'volume': .01} if operation == 'partial' else {'sl': 1.08}, 7, 'demo')


@pytest.mark.parametrize('operation', ['market', 'pending', 'partial', 'stops', 'cancel', 'close'])
def test_each_actual_request_must_pass_broker_preflight(broker, operation):
    broker.order_check.return_value = NS(retcode=10019, comment='insufficient margin')
    result = invoke(broker, operation)
    assert not result['success'] and result['preflight'] and not result['uncertain']
    assert result['retcode'] == 10019
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('checked', [None, NS(retcode='0'), NS(retcode=False), NS(retcode=Mock())])
def test_missing_or_malformed_check_never_sends(broker, checked):
    broker.order_check.return_value = checked
    result = open_order(broker)
    assert not result['success'] and not result['uncertain']
    broker.order_send.assert_not_called()


def test_preflight_transport_failure_is_certainly_unsent(broker):
    broker.order_check.side_effect = TimeoutError('check transport')
    result = open_order(broker)
    assert not result['success'] and not result['uncertain']
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('operation', ['market', 'pending', 'partial', 'stops', 'cancel', 'close'])
def test_account_switch_during_check_blocks_every_mutation(broker, operation):
    def switch(_request):
        broker.account_info.return_value = NS(login=8, server='other')
        return NS(retcode=0)
    broker.order_check.side_effect = switch
    result = invoke(broker, operation)
    assert not result['success'] and not result.get('uncertain')
    broker.order_send.assert_not_called()


def test_identity_change_while_collecting_risk_is_not_an_account_snapshot(broker):
    def switch(*_args):
        broker.account_info.return_value = NS(login=8, server='other')
        return []
    broker.history_deals_get.side_effect = switch
    assert not open_order(broker)['success']
    broker.order_check.assert_not_called()
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('change', [dict(order_type='TYPO'), dict(sl_points=-1),
    dict(sl_points=float('nan')), dict(tp_points=float('inf'))])
def test_invalid_direct_bridge_arguments_cannot_become_orders(broker, change):
    assert not open_order(broker, **change)['success']
    broker.order_send.assert_not_called()


def test_stop_prices_require_tick_grid_in_both_preview_and_send(broker):
    broker.symbol_info.return_value.trade_tick_size = .00005
    result = open_order(broker, sl_points=201)
    assert not result['success'] and 'fiyat adımı' in result['error']
    preview = bridge.trade_preview(broker, 'EURUSD', 'BUY', .01, 201, None, None, 7, 'demo')
    assert not preview['success']
    broker.order_send.assert_not_called()


def test_exact_minimum_stop_distance_uses_floating_point_tolerance(broker):
    assert open_order(broker, sl_points=20)['success']
    broker.order_send.assert_called_once()


def test_zero_stop_level_does_not_allow_stop_at_exit_quote(broker):
    broker.symbol_info.return_value.trade_stops_level = 0
    assert not open_order(broker, sl_points=10)['success']
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('execution,mask,expected', [(0,0,[0,1,2]), (1,0,[0,1,2]),
    (2,0,[]), (2,1,[0]), (2,2,[1]), (2,3,[0,1]), (3,0,[2]), (3,3,[0,1,2])])
def test_filling_policy_matches_metaquotes_execution_matrix(broker, execution, mask, expected):
    info = broker.symbol_info.return_value
    info.trade_exemode, info.filling_mode = execution, mask
    assert bridge._fillings(info) == expected


def test_invalid_filling_in_preflight_selects_other_policy_without_send_retry(broker):
    broker.order_check.side_effect = [NS(retcode=10030, comment='invalid fill'), NS(retcode=0)]
    assert open_order(broker)['success']
    assert broker.order_check.call_count == 2
    broker.order_send.assert_called_once()
    assert broker.order_send.call_args.args[0]['type_filling'] == 1


@pytest.mark.parametrize('code', [10008, 10010, 10012, 10031])
def test_accepted_partial_and_unknown_send_results_never_retrigger(broker, code):
    broker.order_send.return_value.retcode = code
    result = open_order(broker)
    assert result['success'] == (code in (10008, 10010))
    assert result['uncertain'] == (code in (10012, 10031))
    broker.order_send.assert_called_once()


@pytest.mark.parametrize('operation', ['partial', 'stops', 'close'])
def test_position_change_during_preflight_never_uses_stale_volume_or_stops(broker, operation):
    def changed(_request):
        broker.positions_get.return_value = [position(.02)]
        return NS(retcode=0)
    broker.order_check.side_effect = changed
    assert not invoke(broker, operation)['success']
    broker.order_send.assert_not_called()


def test_pending_order_filled_during_cancel_check_is_not_removed(broker):
    def filled(_request):
        broker.orders_get.return_value = []
        return NS(retcode=0)
    broker.order_check.side_effect = filled
    assert not invoke(broker, 'cancel')['success']
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('code', [10008, 10010])
def test_full_close_requires_completed_execution(broker, code):
    broker.order_send.return_value.retcode = code
    result = invoke(broker, 'close')
    assert not result['success'] and (result['partial'] or result['pending'])
    broker.order_send.assert_called_once()


def test_full_close_sends_fresh_volume_and_preserves_position_owner(broker):
    assert invoke(broker, 'close')['success']
    request = broker.order_send.call_args.args[0]
    assert request['volume'] == .03 and request['magic'] == bridge.MANUAL_MAGIC
    assert request['position'] == 22 and request['type'] == 1


@pytest.mark.parametrize('field', ['trade_allowed', 'trade_expert'])
def test_broker_permissions_are_checked_before_send(broker, field):
    setattr(broker.account_info.return_value, field, False)
    assert not open_order(broker)['success']
    broker.order_send.assert_not_called()


def test_symbol_direction_permission_and_broker_directional_volume_are_enforced(broker):
    info = broker.symbol_info.return_value
    info.trade_mode = 2
    assert not open_order(broker)['success']
    info.trade_mode = 4
    info.volume_limit = .05
    broker.orders_get.return_value = [NS(symbol='EURUSD', type=2, volume_current=.05)]
    assert not open_order(broker)['success']
    broker.order_send.assert_not_called()


def test_negative_exposure_cannot_cancel_positive_lots_in_limit(broker):
    broker.orders_get.return_value = [NS(volume_current=-.1), NS(volume_current=.15)]
    assert not open_order(broker)['success']
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('limit', [float('nan'), float('inf'), 1.5, True, 0])
def test_invalid_order_count_limit_cannot_disable_risk_guard(broker, limit):
    assert not open_order(broker, max_open_orders=limit)['success']
    broker.order_send.assert_not_called()


def test_successful_preflight_evidence_is_retained_with_execution_result(broker):
    result = open_order(broker)
    assert result['preflight_retcode'] == 0 and result['preflight_comment'] == 'checked'


@pytest.mark.parametrize('magic', [bridge.MANUAL_MAGIC, bridge.HMA_MAGIC, bridge.AI_MAGIC])
@pytest.mark.parametrize('pending', [False, True])
def test_real_account_requires_stop_for_every_new_order_source(broker, magic, pending):
    broker.account_info.return_value.trade_mode = 2
    result = open_order(broker, magic=magic, sl_points=0,
                        **({'pending_type': 'BUY_LIMIT', 'entry_price': 1.08} if pending else {}))
    assert not result['success'] and result['reason_code'] == 'real_stop_required'
    assert result['stop_required']
    assert result['trade_risk_limit'] == (10. if magic == bridge.AI_MAGIC else 20.)
    broker.order_check.assert_not_called()
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('magic', [bridge.MANUAL_MAGIC, bridge.HMA_MAGIC])
def test_demo_manual_and_hma_keep_explicit_unprotected_stop_choice(broker, magic):
    broker.order_calc_profit.return_value = None
    result = open_order(broker, magic=magic, sl_points=0, tp_points=0)
    assert result['success'] and not result['stop_required']
    assert result['stop_risk'] is None
    broker.order_calc_profit.assert_not_called()


@pytest.mark.parametrize('magic', [bridge.MANUAL_MAGIC, bridge.HMA_MAGIC, bridge.AI_MAGIC])
@pytest.mark.parametrize('pending', [False, True])
def test_broker_money_risk_blocks_small_lot_above_equity_cap(broker, magic, pending):
    broker.order_calc_profit.return_value = -20.01
    result = open_order(broker, magic=magic,
                        **({'pending_type': 'BUY_LIMIT', 'entry_price': 1.08} if pending else {}))
    assert not result['success'] and result['reason_code'] == 'trade_risk_exceeded'
    assert result['trade_risk_amount'] == 20.01 and result['stop_risk'] == 20.01
    assert result['trade_risk_limit'] == (10. if magic == bridge.AI_MAGIC else 20.)
    broker.order_check.assert_not_called()
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('outcome', [None, float('nan'), float('inf'), 0., 2.])
@pytest.mark.parametrize('magic', [bridge.MANUAL_MAGIC, bridge.HMA_MAGIC, bridge.AI_MAGIC])
def test_every_protected_order_needs_broker_verified_loss(broker, outcome, magic):
    broker.order_calc_profit.return_value = outcome
    result = open_order(broker, magic=magic)
    assert not result['success'] and result['reason_code'] == 'trade_risk_unverified'
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('equity', [None, 0., -1., float('nan'), float('inf')])
def test_non_positive_or_unknown_equity_cannot_support_protected_trade(broker, equity):
    broker.account_info.return_value.equity = equity
    result = open_order(broker)
    assert not result['success'] and result['reason_code'] == 'trade_risk_unverified'
    broker.order_send.assert_not_called()


def test_exact_money_risk_limit_is_allowed_and_ai_keeps_lower_limit(broker):
    broker.order_calc_profit.return_value = -20.
    result = open_order(broker)
    assert result['success'] and result['trade_risk_limit'] == result['trade_risk_amount'] == 20.
    assert result['risk_pct_equity'] == 2.
    broker.order_send.reset_mock()
    result = open_order(broker, magic=bridge.AI_MAGIC)
    assert not result['success'] and result['trade_risk_limit'] == 10.
    broker.order_send.assert_not_called()


def test_common_cap_also_limits_ai_when_configured_below_ai_cap(broker):
    broker.order_calc_profit.return_value = -7.
    result = open_order(broker, magic=bridge.AI_MAGIC, max_trade_risk_pct=.5)
    assert not result['success'] and result['trade_risk_limit'] == 5.
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('maximum', [0., -1., float('nan'), float('inf')])
def test_invalid_money_risk_configuration_cannot_disable_guard(broker, maximum):
    result = open_order(broker, max_trade_risk_pct=maximum)
    assert not result['success'] and result['reason_code'] == 'trade_risk_configuration_invalid'
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('real,sl,outcome,reason', [(True,0,-2.,'real_stop_required'),
    (False,200,None,'trade_risk_unverified'), (False,200,-25.,'trade_risk_exceeded')])
def test_read_only_preview_applies_same_money_and_real_stop_guard(broker, real, sl, outcome, reason):
    broker.account_info.return_value.trade_mode = 2 if real else 0
    broker.order_calc_profit.return_value = outcome
    result = bridge.trade_preview(broker, 'EURUSD', 'BUY', .01, sl, None, None, 7, 'demo')
    assert not result['success'] and result['reason_code'] == reason
    assert result['trade_risk_limit'] == 20.
    broker.order_check.assert_not_called()
    broker.order_send.assert_not_called()


def test_real_position_cannot_remove_stop_or_keep_missing_stop_for_tp_only_edit(broker):
    broker.account_info.return_value.trade_mode = 2
    p = position()
    broker.positions_get.return_value = [p]
    removed = bridge.manage_position(broker, 22, 'stops', {'sl': 0}, 7, 'demo')
    assert removed['reason_code'] == 'real_stop_required'
    p.sl = 0
    tp_only = bridge.manage_position(broker, 22, 'stops', {'tp': 1.13}, 7, 'demo')
    assert tp_only['reason_code'] == 'real_stop_required'
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('side,new_sl', [(0,1.08), (1,1.13)])
def test_widened_stop_uses_current_exit_quote_and_money_cap(broker, side, new_sl):
    p = position()
    p.type, p.sl, p.tp = side, 1.09 if side == 0 else 1.12, 1.12 if side == 0 else 1.08
    broker.positions_get.return_value = [p]
    broker.order_calc_profit.return_value = -30.
    result = bridge.manage_position(broker, 22, 'stops', {'sl': new_sl}, 7, 'demo')
    assert not result['success'] and result['reason_code'] == 'trade_risk_exceeded'
    assert broker.order_calc_profit.call_args.args == (side, 'EURUSD', .03,
        1.0999 if side == 0 else 1.1, new_sl)
    broker.order_send.assert_not_called()


@pytest.mark.parametrize('values', [{'sl':1.095}, {'tp':1.13}])
def test_tightened_stop_and_tp_only_edit_can_reduce_preexisting_large_risk(broker, values):
    broker.account_info.return_value.trade_mode = 2
    broker.positions_get.return_value = [position()]
    broker.order_calc_profit.return_value = -1000.
    result = bridge.manage_position(broker, 22, 'stops', values, 7, 'demo')
    assert result['success']
    broker.order_calc_profit.assert_not_called()
    broker.order_send.assert_called_once()


def test_demo_stop_removal_remains_explicitly_available(broker):
    broker.positions_get.return_value = [position()]
    broker.order_calc_profit.return_value = None
    assert bridge.manage_position(broker, 22, 'stops', {'sl':0}, 7, 'demo')['success']
    broker.order_calc_profit.assert_not_called()


def test_widening_ai_position_stop_keeps_stricter_ai_money_limit(broker):
    p = position()
    p.magic = bridge.AI_MAGIC
    broker.positions_get.return_value = [p]
    broker.order_calc_profit.return_value = -15.
    result = bridge.manage_position(broker, 22, 'stops', {'sl':1.08}, 7, 'demo')
    assert not result['success'] and result['reason_code'] == 'trade_risk_exceeded'
    assert result['trade_risk_limit'] == 10. and result['effective_trade_risk_pct'] == 1.
    broker.order_send.assert_not_called()
