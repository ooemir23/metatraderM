import time
from types import SimpleNamespace as NS

import pytest

from app import mt5_bridge as bridge
from app.order_journal import OrderJournal
from app.trailing_stops import TrailingStops
from test_trade_safety import client, pos


def pair(client, first=.03, second=.01):
    a, b = pos(magic=0, side=0, ticket=10), pos(magic=0, side=1, ticket=20)
    a.volume, b.volume = first, second
    client.mt5.positions_get.side_effect = lambda **kw: [p for p in (a,b) if not kw or p.ticket == kw['ticket']]
    client.mt5.symbol_info.return_value.order_mode = 127
    return a, b


def test_close_by_is_one_native_request_and_preserves_volume_difference(client):
    pair(client)
    result = bridge.close_opposite(client.mt5,10,20,1,'test')
    assert result['success'] and result['requested_volume'] == .01
    assert result['remaining_volume'] == pytest.approx(.02)
    request = client.mt5.order_send.call_args.args[0]
    assert request == dict(action=10,position=10,position_by=20,symbol='EURUSD',comment='Panel Close By')
    assert client.mt5.order_send.call_count == 1


@pytest.mark.parametrize('reason',['same_ticket','same_side','different_symbol','netting','fifo','unsupported','missing','check_rejected'])
def test_close_by_never_emulates_two_closes_on_invalid_pair(client,reason):
    a,b=pair(client)
    opposite=20
    if reason=='same_ticket':opposite=10
    elif reason=='same_side':b.type=0
    elif reason=='different_symbol':b.symbol='BTCUSD'
    elif reason=='netting':client.mt5.account_info.return_value.margin_mode=0
    elif reason=='fifo':client.mt5.account_info.return_value.fifo_close=True
    elif reason=='unsupported':client.mt5.symbol_info.return_value.order_mode=63
    elif reason=='missing':opposite=30
    elif reason=='check_rejected':client.mt5.order_check.return_value=NS(retcode=10035,comment='Invalid order')
    assert not bridge.close_opposite(client.mt5,10,opposite,1,'test')['success']
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('change',['position','account'])
def test_close_by_rechecks_pair_and_account_after_preflight(client,change):
    a,b=pair(client)
    def checked(req):
        if change=='position':b.volume=.02
        else:client.mt5.account_info.return_value.login=2
        return NS(retcode=0,comment='checked')
    client.mt5.order_check.side_effect=checked
    assert not bridge.close_opposite(client.mt5,10,20,1,'test')['success']
    client.mt5.order_send.assert_not_called()


def test_close_by_lost_result_is_durable_and_never_retried(client,tmp_path):
    pair(client)
    client.journal=OrderJournal(tmp_path/'journal.db')
    client.mt5.order_send.return_value=None
    assert client.close_opposite(10,20,'opposite-request-123')['uncertain']
    client.mt5.order_send.return_value=NS(retcode=10009,order=1,volume=.01,price=1.,comment='done')
    assert client.close_opposite(10,20,'opposite-request-123')['uncertain']
    assert client.mt5.order_send.call_count==1
    assert client.close_opposite(10,30,'opposite-request-123')['conflict']


@pytest.mark.parametrize('execution,has_price',[(0,True),(1,True),(2,False),(3,True)])
def test_close_respects_execution_and_explicit_settings(client,execution,has_price):
    a,_=pair(client)
    client.mt5.symbol_info.return_value.trade_exemode=execution
    result=client.close_position(a.ticket,deviation_points=7,filling_policy='FOK')
    assert result['success']
    req=client.mt5.order_send.call_args.args[0]
    assert ('price' in req)==has_price and req['deviation']==7 and req['type_filling']==0


def test_partial_close_preserves_stops_and_user_execution_policy(client):
    a,_=pair(client)
    a.sl,a.tp=1.08,1.12
    result=bridge.manage_position(client.mt5,10,'partial',{'volume':.01,'deviation_points':9,'filling_policy':'IOC'},1,'test')
    assert result['success']
    req=client.mt5.order_send.call_args.args[0]
    assert req['deviation']==9 and req['type_filling']==1
    assert 'price' not in req and 'sl' not in req and 'tp' not in req


@pytest.mark.parametrize('side,quote,ready',[(0,1.1009,False),(0,1.101,True),(1,1.0991,False),(1,1.099,True)])
def test_trailing_activates_at_profit_distance_using_liquidation_quote(client,side,quote,ready):
    p=pos(magic=0,side=side,ticket=10)
    client.mt5.positions_get.return_value=[p]
    client.mt5.symbol_info_tick.return_value.bid=quote if side==0 else quote-.0001
    client.mt5.symbol_info_tick.return_value.ask=quote if side==1 else quote+.0001
    result=bridge.trailing_candidate(client.mt5,10,100,1,'test')
    assert result['ready']==ready
    if ready:assert result['sl']==pytest.approx(p.price_open)
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('side,existing',[(0,1.102),(1,1.098)])
def test_trailing_never_widens_existing_stop(client,side,existing):
    p=pos(magic=0,side=side,ticket=10);p.sl=existing
    client.mt5.positions_get.return_value=[p]
    client.mt5.symbol_info_tick.return_value.bid=1.102 if side==0 else 1.0979
    client.mt5.symbol_info_tick.return_value.ask=1.1021 if side==0 else 1.098
    assert not bridge.trailing_candidate(client.mt5,10,100,1,'test')['ready']


def trailing_setup(client,tmp_path):
    p=pos(magic=0,side=0,ticket=10)
    client.mt5.positions_get.return_value=[p]
    client.mt5.symbol_info_tick.return_value.bid=1.102
    client.mt5.symbol_info_tick.return_value.ask=1.1021
    client.journal=OrderJournal(tmp_path/'journal.db')
    manager=TrailingStops(client)
    assert manager.configure(10,100,[1,'test','DEMO'])['enabled']
    client.mt5.order_send.assert_not_called()
    return manager,p


def test_trailing_configuration_sends_nothing_and_steps_are_rate_limited(client,tmp_path):
    manager,p=trailing_setup(client,tmp_path)
    manager.step();manager.step()
    assert client.mt5.order_send.call_count==1
    req=client.mt5.order_send.call_args.args[0]
    assert req['action']==6 and req['sl']==pytest.approx(1.101) and req['tp']==0
    assert manager.status()[0]['status']=='armed'
    manager.configure(10,0,[1,'test','DEMO']);manager.step()
    assert client.mt5.order_send.call_count==1


def test_trailing_uncertain_send_pauses_until_explicit_rearm(client,tmp_path):
    manager,p=trailing_setup(client,tmp_path)
    client.mt5.order_send.return_value=None
    manager.step();manager.configs[10]['last_attempt']=0;manager.step()
    assert manager.status()[0]['status']=='uncertain'
    assert client.mt5.order_send.call_count==1


def test_trailing_changes_account_or_position_identity_never_modify_new_position(client,tmp_path):
    manager,p=trailing_setup(client,tmp_path)
    p.identifier=11
    manager.step()
    assert not manager.configs
    client.mt5.order_send.assert_not_called()
    manager.configure(10,100,[1,'test','DEMO'])
    client.login_id=2;client.mt5.account_info.return_value.login=2
    manager.step()
    assert not manager.configs
    client.mt5.order_send.assert_not_called()


def test_trailing_shutdown_clears_config_and_restart_requires_rearm(client,tmp_path):
    manager,_=trailing_setup(client,tmp_path)
    manager.stop();manager.step()
    assert not manager.configs and not TrailingStops(client).configs
    client.mt5.order_send.assert_not_called()


def test_trailing_stale_quote_cannot_update_stop(client,tmp_path):
    manager,_=trailing_setup(client,tmp_path)
    client.mt5.symbol_info_tick.return_value.time=time.time()-60
    manager.step()
    assert manager.configs[10]['status']=='paused'
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('side,kind,price',[('BUY','BUY_LIMIT',1.101),('SELL','SELL_LIMIT',1.099)])
def test_exchange_limit_can_cross_current_market_while_forex_cannot(client,side,kind,price):
    info=client.mt5.symbol_info.return_value
    info.order_mode=127;info.expiration_mode=15;info.trade_exemode=3
    preview=client.trade_preview('EURUSD',side,.01,pending_type=kind,entry_price=price,manual_mode='MT5')
    assert preview['success']
    assert client.open_order('EURUSD',side,.01,pending_type=kind,entry_price=price,manual_mode='MT5')['success']
    info.trade_exemode=2
    assert not client.trade_preview('EURUSD',side,.01,pending_type=kind,entry_price=price,manual_mode='MT5')['success']


def test_boc_requires_supported_limit_order_and_is_sent_explicitly(client):
    info=client.mt5.symbol_info.return_value
    info.order_mode=127;info.expiration_mode=15;info.filling_mode=7
    kwargs=dict(pending_type='BUY_LIMIT',entry_price=1.099,manual_mode='MT5',filling_policy='BOC')
    assert client.trade_preview('EURUSD','BUY',.01,**kwargs)['success']
    assert client.open_order('EURUSD','BUY',.01,**kwargs)['success']
    assert client.mt5.order_send.call_args.args[0]['type_filling']==3
    client.mt5.order_send.reset_mock()
    info.filling_mode=3
    assert not client.open_order('EURUSD','BUY',.01,**kwargs)['success']
    info.filling_mode=7;kwargs.update(pending_type='BUY_STOP',entry_price=1.101)
    assert not client.open_order('EURUSD','BUY',.01,**kwargs)['success']
    assert not client.open_order('EURUSD','BUY',.01,manual_mode='MT5',filling_policy='BOC')['success']
    client.mt5.order_send.assert_not_called()


def test_requote_exposes_broker_offer_without_retry(client):
    client.mt5.order_send.return_value=NS(retcode=10004,order=0,volume=0,price=0,
                                          comment='Requote',bid=1.1001,ask=1.1002)
    result=client.open_order('EURUSD','BUY',.01,manual_mode='MT5')
    assert not result['success'] and result['requote']
    assert result['broker_quote']=={'bid':1.1001,'ask':1.1002}
    assert client.mt5.order_send.call_count==1


def test_depth_is_real_broker_data_and_subscription_always_released(client):
    client.mt5.market_book_get.return_value=(NS(type=1,price=1.1001,volume=4,volume_dbl=4.5),NS(type=2,price=1.0999,volume=2))
    result=bridge.market_depth(client.mt5,'EURUSD',1,'test')
    assert result['available'] and result['levels'][0]['volume']==4.5
    assert result['levels'][1]['side']=='BUY'
    client.mt5.market_book_release.assert_called_once_with('EURUSD')
    client.mt5.market_book_release.reset_mock()
    client.mt5.market_book_get.side_effect=RuntimeError('Disconnected')
    with pytest.raises(RuntimeError):bridge.market_depth(client.mt5,'EURUSD',1,'test')
    client.mt5.market_book_release.assert_called_once_with('EURUSD')
    client.mt5.order_send.assert_not_called()


def test_no_depth_is_reported_without_making_up_a_book(client):
    client.mt5.market_book_add.return_value=False
    result=bridge.market_depth(client.mt5,'EURUSD',1,'test')
    assert not result['available'] and result['levels']==[]
    client.mt5.market_book_get.assert_not_called()
    client.mt5.market_book_release.assert_not_called()
    client.mt5.order_send.assert_not_called()


def test_http_close_by_and_trailing_are_account_bound_and_replay_safe(client,monkeypatch,tmp_path):
    from app import main
    from test_trade_safety import authenticated_web
    pair(client)
    client.journal=OrderJournal(tmp_path/'journal.db')
    monkeypatch.setattr(main,'mt5_client',client)
    manager=TrailingStops(client);monkeypatch.setattr(main,'trailing_stops',manager)
    web=authenticated_web()
    scope=[1,'test','DEMO']
    caps=web.get('/api/position/10/capabilities')
    assert caps.status_code==200 and caps.json()['opposites'][0]['ticket']==20
    payload={'ticket':10,'distance_points':100,'request_id':'trailing-http-test-123','expected_account':scope}
    assert web.post('/api/position/trailing',json=payload).status_code==200
    client.mt5.order_send.assert_not_called()
    assert web.get('/api/position/trailing').json()[0]['points']==100
    manager.stop();monkeypatch.setattr(main,'trailing_stops',TrailingStops(client))
    reply=web.post('/api/position/trailing',json=payload)
    assert reply.status_code==400 and reply.json()['enabled'] is False
    request={'ticket':10,'position_by':20,'request_id':'close-by-http-test-123','expected_account':scope}
    assert web.post('/api/position/close-by',json=request).status_code==200
    assert web.post('/api/position/close-by',json=request).json()['replayed']
    assert client.mt5.order_send.call_count==1
    request['expected_account']=[2,'test','DEMO']
    assert web.post('/api/position/close-by',json=request).status_code==409
    request.pop('expected_account');request['position_by']=0
    assert web.post('/api/position/close-by',json=request).status_code==422


def test_viewer_cannot_enable_trailing_or_close_by(client,monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main,'mt5_client',client)
    monkeypatch.setenv('DASHBOARD_USER_2','viewer');monkeypatch.setenv('DASHBOARD_PASSWORD_2','read-only')
    monkeypatch.setenv('DASHBOARD_ROLE_2','VIEWER')
    auth=base64.b64encode(b'viewer:read-only').decode()
    web=TestClient(main.app,headers={'Authorization':'Basic '+auth})
    for route,extra in [('close-by',{'position_by':20}),('trailing',{'distance_points':100})]:
        response=web.post('/api/position/'+route,json={'ticket':10,'request_id':'viewer-request-123',**extra})
        assert response.status_code==403
    client.mt5.order_send.assert_not_called()


def test_native_netting_reduce_inherits_stops_without_treating_them_as_new_short(client):
    p=pos(magic=0,side=0,ticket=10);p.volume=.03;p.sl=1.08;p.tp=1.12
    client.mt5.positions_get.return_value=[p]
    client.mt5.account_info.return_value.margin_mode=0
    client.mt5.symbol_info.return_value.trade_mode=3  # Close-only symbol still permits reduction.
    kwargs={'manual_mode':'MT5','sl_price':p.sl,'tp_price':p.tp}
    preview=client.trade_preview('EURUSD','SELL',.01,**kwargs)
    assert preview['success'] and preview['reducing_position']
    assert client.open_order('EURUSD','SELL',.01,**kwargs)['success']
    assert client.mt5.order_send.call_count==1
    client.mt5.order_send.reset_mock()
    assert not client.open_order('EURUSD','SELL',.04,**kwargs)['success']
    client.mt5.order_send.assert_not_called()


def test_real_trailing_enable_requires_second_verification(client,monkeypatch):
    from app import main
    from test_trade_safety import authenticated_web
    monkeypatch.setattr(main,'mt5_client',client)
    client.account_type='REAL';client.mt5.account_info.return_value.trade_mode=2
    response=authenticated_web().post('/api/position/trailing',json={'ticket':10,'distance_points':100,'request_id':'real-trailing-test-123'})
    assert response.status_code==403
    client.mt5.order_send.assert_not_called()
