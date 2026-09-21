import asyncio
from contextlib import suppress
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from app import mt5_bridge as bridge
from app.reports import build_report
from app.live_feed import LiveFeed
from test_trade_safety import client, pos, deal, authenticated_web


@pytest.fixture
def management(client):
    p = pos(side=0)
    p.volume = .03
    p.tp = 1.12
    client.mt5.positions_get.return_value = [p]
    info = client.mt5.symbol_info.return_value
    info.trade_tick_size = .00001
    info.trade_freeze_level = 0
    info.order_mode = 127
    info.expiration_mode = 15
    client.mt5.orders_get.return_value = []
    return client


def test_partial_close_preserves_remaining_volume_and_is_idempotent(management):
    c = management
    r = c.manage_position(1, 'partial', {'volume': .01}, 'partial-test')
    assert r['success'] and r['position'] == 1
    req = c.mt5.order_send.call_args.args[0]
    assert req['position'] == 1 and req['volume'] == .01 and req['type'] == 1
    assert c.manage_position(1, 'partial', {'volume': .01}, 'partial-test')['replayed']
    c.mt5.order_send.assert_called_once()


@pytest.mark.parametrize('volume', [0, -.01, .015, .03, .04, float('nan')])
def test_partial_close_rejects_invalid_or_full_volume(management, volume):
    result = bridge.manage_position(management.mt5, 1, 'partial', {'volume':volume}, 0, '')
    assert not result['success']
    management.mt5.order_send.assert_not_called()


def test_sl_edit_preserves_tp_and_checks_last_displayed_values(management):
    c = management
    result = c.manage_position(1,'stops',{'sl':1.09,'expected_sl':0,'expected_tp':1.12},'stops-1')
    assert result['success']
    assert c.mt5.order_send.call_args.args[0] == dict(action=6,position=1,symbol='EURUSD',sl=1.09,tp=1.12)
    c.mt5.order_send.reset_mock()
    result = c.manage_position(1,'stops',{'sl':1.09,'expected_tp':1.13},'stops-2')
    assert not result['success']
    c.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('values', [{'sl':1.11}, {'tp':1.08}, {'sl':1.090001}, {'sl':-1}])
def test_stop_direction_and_price_grid_rejected(management, values):
    result = management.manage_position(1,'stops',values,'bad-stops')
    assert not result['success']
    management.mt5.order_send.assert_not_called()


def test_modify_no_change_is_success_but_ambiguous_is_not_resent(management):
    c = management
    c.mt5.order_send.return_value.retcode = 10025
    assert c.manage_position(1,'stops',{'sl':0},'no-change')['success']
    c.mt5.order_send.side_effect = TimeoutError()
    first = c.manage_position(1,'partial',{'volume':.01},'ambiguous-partial')
    second = c.manage_position(1,'partial',{'volume':.01},'ambiguous-partial')
    assert first['uncertain'] and second['uncertain'] and second['replayed']
    assert c.mt5.order_send.call_count == 2


@pytest.mark.parametrize('kind,price,side,type_code', [('BUY_LIMIT',1.08,'BUY',2),('SELL_LIMIT',1.12,'SELL',3),('BUY_STOP',1.12,'BUY',4),('SELL_STOP',1.08,'SELL',5)])
def test_pending_order_uses_correct_type_and_return_filling(management,kind,price,side,type_code):
    c = management
    c.mt5.positions_get.return_value = []
    result = c.open_order('EURUSD',side,.01,pending_type=kind,entry_price=price,sl_points=200,tp_points=400)
    assert result['success'] and result['pending_order']
    req = c.mt5.order_send.call_args.args[0]
    assert req['action'] == 5 and req['type'] == type_code and req['type_filling'] == 2 and req['price'] == price


@pytest.mark.parametrize('kind,price', [('BUY_LIMIT',1.2),('BUY_STOP',1.0)])
def test_pending_wrong_price_side_never_reaches_broker(management,kind,price):
    c = management
    c.mt5.positions_get.return_value=[]
    assert not c.open_order('EURUSD','BUY',.01,pending_type=kind,entry_price=price)['success']
    c.mt5.order_send.assert_not_called()


def test_daily_limit_also_blocks_pending_order(management):
    c=management
    c.mt5.history_deals_get.return_value=[deal(-1000)]
    assert not c.open_order('EURUSD','BUY',.01,pending_type='BUY_LIMIT',entry_price=1.08)['success']
    c.mt5.order_send.assert_not_called()


def test_cancel_checks_pending_state_and_can_run_above_loss_limit(management):
    c=management
    assert not c.cancel_pending(5,'absent-order')['success']
    c.mt5.order_send.assert_not_called()
    c.mt5.orders_get.return_value=[NS(ticket=5)]
    c.mt5.history_deals_get.return_value=[deal(-1000)]
    assert c.cancel_pending(5,'cancel-order')['success']
    assert c.mt5.order_send.call_args.args[0] == {'action':8,'order':5}
    c.mt5.history_deals_get.assert_not_called()


def row(ticket, entry, volume, side, timestamp, profit=0, commission=0, fee=0, position_id=7):
    return dict(ticket=ticket,position_id=position_id,symbol='EURUSD',type=side,entry=entry,
                time=timestamp,volume=volume,profit=profit,commission=commission,swap=0,fee=fee)


def test_reports_count_partial_exits_as_one_position_and_include_opening_fees():
    opened=row(1,0,.03,0,10,commission=-5)
    exit1=row(2,1,.01,1,30,profit=2,commission=-1)
    exit2=row(3,1,.02,1,40,profit=2,fee=-1)
    a=dict(deals=[exit1,exit2],open_ids=[],start=20,end=50,currency='EUR')
    r=build_report(a,{7:[opened,exit1,exit2]})
    assert r['summary']['total_profit'] == 2  # period cash activity
    assert r['summary']['cohort_net_profit'] == -3  # whole lifetime includes opening cost before period
    assert r['summary']['total_trades'] == 1 and r['summary']['losing_trades'] == 1
    assert r['summary']['win_rate'] == 0
    assert r['daily'][0]['trades_count'] == 1
    assert r['currency'] == 'EUR'


def test_open_position_partial_profit_is_cash_but_not_a_closed_trade():
    opened=row(1,0,.03,0,10)
    exited=row(2,1,.01,1,30,profit=10)
    r=build_report(dict(deals=[exited],open_ids=[7],start=20,end=50,currency='USD'),{7:[opened,exited]})
    assert r['summary']['total_profit'] == 10 and r['summary']['total_trades'] == 0


def test_incomplete_history_never_fabricates_win_statistics():
    exited=row(2,1,.01,1,30,profit=10)
    r=build_report(dict(deals=[exited],open_ids=[],start=20,end=50,currency='USD'),{7:[exited]})
    assert r['summary']['total_trades'] == 0 and r['summary']['incomplete_positions'] == 1


def test_profit_factor_infinity_is_json_safe():
    opened=row(1,0,.01,0,10)
    exited=row(2,1,.01,1,30,profit=10)
    r=build_report(dict(deals=[exited],open_ids=[],start=20,end=50,currency='USD'),{7:[opened,exited]})
    assert r['summary']['profit_factor'] is None


def test_report_fetches_full_lifetime_separately_from_selected_period(client):
    opened=row(1,0,.01,0,10,commission=-5)
    exited=row(2,1,.01,1,30,profit=10)
    activity=dict(deals=[exited],open_ids=[],start=20,end=50,currency='USD',account=[1,'test'])
    client._bridge=Mock(side_effect=[activity,[opened,exited],activity])
    first=client.get_reports(7)
    second=client.get_reports(7)
    assert first['summary']['cohort_net_profit'] == 5 and second == first
    assert client._bridge.call_count == 3  # second request reuses lifetime, still reads fresh activity


def test_live_feed_shares_one_snapshot_and_discards_backlog():
    async def scenario():
        c=Mock()
        c.get_live_snapshot.return_value={'sampled_at':1}
        feed=LiveFeed(c)
        a,b=feed.subscribe('EURUSD'),feed.subscribe('EURUSD')
        worker=asyncio.create_task(feed.run())
        try:
            assert await asyncio.wait_for(a.get(),1) == {'sampled_at':1}
            assert await asyncio.wait_for(b.get(),1) == {'sampled_at':1}
            c.get_live_snapshot.assert_called_once_with(['EURUSD'])
            feed.publish({'sampled_at':2});feed.publish({'sampled_at':3})
            assert a.qsize() == 1 and (await a.get())['sampled_at'] == 3
        finally:
            feed.unsubscribe(a);feed.unsubscribe(b)
            worker.cancel()
            with suppress(asyncio.CancelledError): await worker
    asyncio.run(scenario())


def test_sse_generator_removes_listener_on_disconnect():
    from app import main
    async def scenario():
        response=await main.live('EURUSD')
        stream=response.body_iterator
        assert 'connected' in await anext(stream)
        main.live_feed.publish({'sampled_at':1})
        assert 'data:' in await anext(stream)
        await stream.aclose()
        assert not main.live_feed.listeners
    asyncio.run(scenario())


def test_new_mutations_require_valid_fields_and_preserve_uncertainty(monkeypatch):
    from app import main
    web=authenticated_web()
    assert web.post('/api/position/partial-close',json={'request_id':'valid-request-123','ticket':1,'volume':0}).status_code == 422
    assert web.post('/api/position/stops',json={'request_id':'valid-request-123','ticket':1}).status_code == 422
    monkeypatch.setattr(main.mt5_client,'manage_position',Mock(return_value={'success':False,'uncertain':True,'error':'unknown'}))
    response=web.post('/api/position/partial-close',json={'request_id':'valid-request-123','ticket':1,'volume':.01})
    assert response.status_code == 400 and response.json()['uncertain']


def test_flatten_cancels_pending_before_closing_positions(monkeypatch):
    from app import main
    calls=[]
    monkeypatch.setattr(main.mt5_client,'get_pending_orders',lambda:[{'ticket':5}])
    monkeypatch.setattr(main.mt5_client,'cancel_pending',lambda *a:calls.append('cancel') or {'success':True})
    monkeypatch.setattr(main.mt5_client,'close_by_filter',lambda *a:calls.append('close') or {'success':True,'closed_count':1})
    result=main.flatten_account('test-id')
    assert calls == ['cancel','close'] and result['cancelled_count'] == 1 and result['success']
