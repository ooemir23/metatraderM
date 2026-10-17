import base64
import json
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app.mt5_client import MT5Client, MT5DataError
from app.mt5_bridge import HMA_MAGIC, AI_MAGIC, MANUAL_MAGIC
from app.order_journal import OrderJournal
from app.strategy_bot import StrategyBot


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
    c = MT5Client()
    c.login_id, c.server, c.account_type = 1, 'test', 'DEMO'
    c.is_connected = True
    c.last_ping_time = time.time()
    c.mt5 = Mock()
    c.mt5.terminal_info.return_value = NS(connected=True)
    c.mt5.account_info.return_value = NS(login=1, server='test', trade_mode=0, currency='EUR', margin_mode=2,
        equity=10000., margin_free=8000.)
    c.mt5.positions_get.return_value = []
    c.mt5.orders_get.return_value = []
    c.mt5.history_deals_get.return_value = []
    c.mt5.symbol_info.return_value = NS(point=.00001, digits=5, volume_step=.01, volume_min=.01,
        volume_max=10, trade_stops_level=10, filling_mode=3, trade_exemode=2)
    c.mt5.symbol_info_tick.return_value = NS(ask=1.1, bid=1.0999, time=time.time())
    c.mt5.order_send.return_value = NS(retcode=10009, order=123, price=1.1, volume=.01, comment='done')
    c.mt5.order_check.return_value = NS(retcode=0, comment='checked')
    c.mt5.order_calc_profit.return_value = -2.
    c.mt5.order_calc_margin.return_value = 25.
    return c


def pos(magic=HMA_MAGIC, profit=0, symbol='EURUSD', side=1, ticket=1):
    return NS(ticket=ticket, identifier=ticket, symbol=symbol, type=side, magic=magic, volume=.01,
              price_open=1.1, price_current=1.1, sl=0, tp=0, profit=profit, swap=0, time=time.time())


def deal(profit=0, kind=0, commission=0, swap=0, fee=0):
    return NS(profit=profit, type=kind, commission=commission, swap=swap, fee=fee)


@pytest.mark.parametrize('source', ['positions_get', 'orders_get', 'history_deals_get', 'account_info'])
def test_missing_risk_data_blocks_orders(client, source):
    getattr(client.mt5, source).return_value = None
    assert not client.open_order('EURUSD', 'BUY', .01)['success']
    client.mt5.order_send.assert_not_called()


def test_account_switch_blocks_open_close_and_cancel(client):
    client.mt5.account_info.return_value.login = 42
    assert not client.open_order('EURUSD', 'BUY', .01)['success']
    assert not client.close_position(1)['success']
    assert not client.close_by_filter('all')['success']
    assert not client.cancel_pending(5, 'account-switch-cancel')['success']
    client.mt5.order_send.assert_not_called()


def test_missing_selected_account_blocks_trading(client):
    client.login_id = 0
    result = client.open_order('EURUSD', 'BUY', .01)
    assert not result['success'] and not result.get('uncertain')
    client.mt5.order_send.assert_not_called()


def test_safety_lock_blocks_buy_without_claiming_close_all(client, tmp_path):
    client.journal = OrderJournal(tmp_path / 'halted.db')
    client.journal.set_trading_halted(True)
    result = client.open_order('EURUSD', 'BUY', .01)
    assert result['reason_code'] == 'trading_halted'
    assert 'güvenlik kilidi' in result['error']
    assert 'Tümünü kapat' not in result['error']
    client.mt5.order_send.assert_not_called()


def test_account_type_mismatch_blocks_orders(client):
    client.mt5.account_info.return_value.trade_mode = 2
    result = client.open_order('EURUSD', 'BUY', .01)
    assert not result['success'] and 'türü' in result['error']
    client.mt5.order_send.assert_not_called()


def test_login_rejects_broker_type_mismatch(client):
    client.mt5.initialize.return_value = True
    client.mt5.account_info.return_value = NS(login=3, server='broker-live', trade_mode=2)
    result = client.login(3, 'password', 'broker-live', 'DEMO')
    assert not result['success'] and 'Gerçek' in result['error']
    assert (client.login_id, client.server, client.account_type) == (1, 'test', 'DEMO')


def test_real_login_requires_real_broker_mode(client, monkeypatch):
    client.mt5.initialize.return_value = True
    client.mt5.account_info.return_value = NS(login=3, server='broker-live', trade_mode=2)
    monkeypatch.setattr(client, 'save_credentials', lambda: None)
    result = client.login(3, 'password', 'broker-live', 'REAL')
    assert result['success'] and result['account_type'] == 'REAL'
    assert client._selected_account() == [3, 'broker-live']


def test_login_does_not_save_unverified_account(client):
    client.mt5.initialize.return_value = True
    result = client.login(3, 'password', 'new-broker', 'REAL')
    assert not result['success']
    assert (client.login_id, client.server) == (1, 'test')


def test_open_exposure_limits_count_pending_orders(client):
    client.mt5.orders_get.return_value = [NS(volume_current=.45)]
    result = client.open_order('EURUSD', 'BUY', .06)
    assert not result['success'] and 'Toplam' in result['error']
    client.mt5.order_send.assert_not_called()


def test_per_order_and_position_count_limits(client):
    assert 'üst sınırı' in client.open_order('EURUSD', 'BUY', .11)['error']
    client.mt5.positions_get.return_value = [pos(ticket=i) for i in range(10)]
    assert 'Toplam' in client.open_order('EURUSD', 'BUY', .01)['error']
    client.mt5.order_send.assert_not_called()


def test_flatten_halt_persists_across_client_restart(client, monkeypatch):
    client.journal.set_trading_halted(True)
    assert not client.open_order('EURUSD', 'BUY', .01)['success']
    other = MT5Client()
    assert other.journal.trading_halted()
    client.mt5.order_send.assert_not_called()


def test_queued_manual_open_is_blocked_by_flatten_halt(client):
    responses = []
    with client._lock:
        worker = threading.Thread(target=lambda: responses.append(client.open_order('EURUSD', 'BUY', .01)))
        worker.start()
        client.journal.set_trading_halted(True)
    worker.join(2)
    assert responses and not responses[0]['success']
    client.mt5.order_send.assert_not_called()


def test_position_error_is_not_empty_account(client):
    client.mt5.positions_get.return_value = None
    with pytest.raises(MT5DataError):
        client.get_positions(fresh=True)
    assert not StrategyBot(client).close_positions_by_type('SELL')


def test_bot_closes_only_its_own_positions(client):
    client.mt5.positions_get.return_value = [pos(HMA_MAGIC), pos(MANUAL_MAGIC, ticket=2), pos(AI_MAGIC, ticket=3)]
    client.close_position = Mock(return_value={'success': True})
    assert StrategyBot(client).close_positions_by_type('SELL')
    client.close_position.assert_called_once_with(1, expected_magic=HMA_MAGIC)


def test_filtered_close_uses_profit_plus_swap(client):
    losing = pos(profit=5, ticket=11)
    losing.swap = -8
    winning = pos(profit=-2, ticket=12)
    winning.swap = 4
    client.mt5.positions_get.return_value = [losing, winning]
    client.mt5.history_deals_get.side_effect = lambda *, position: [deal()]
    client.close_position = Mock(return_value={'success': True})
    assert client.close_by_filter('profit')['total_matched'] == 1
    client.close_position.assert_called_once()
    assert client.close_position.call_args.args[0] == 12
    client.close_position.reset_mock()
    assert client.close_by_filter('loss')['total_matched'] == 1
    assert client.close_position.call_args.args[0] == 11


def test_filtered_close_includes_position_history_costs_and_fails_closed(client):
    a = pos(profit=5, ticket=11)
    b = pos(profit=2, ticket=12)
    client.mt5.positions_get.return_value = [a, b]
    client.mt5.history_deals_get.side_effect = lambda *, position: [deal(profit=20, commission=-7)] if position == 11 else [deal(commission=-1)]
    client.close_position = Mock(return_value={'success': True})
    assert client.close_by_filter('profit')['total_matched'] == 1
    assert client.close_position.call_args.args[0] == 12
    client.close_position.reset_mock()
    client.mt5.history_deals_get.side_effect = lambda *, position: None if position == 12 else [deal()]
    assert not client.close_by_filter('loss')['success']
    client.close_position.assert_not_called()


@pytest.mark.parametrize('magic', [MANUAL_MAGIC, HMA_MAGIC, AI_MAGIC])
def test_loss_limit_includes_opening_costs_and_floating_loss(client, magic):
    client.mt5.history_deals_get.return_value = [deal(-400, commission=-30, fee=-20), deal(100000, kind=2)]
    client.mt5.positions_get.return_value = [pos(profit=-50, symbol='XAUUSD')]
    response = client.open_order('EURUSD', 'BUY', .01, magic=magic)
    assert not response['success'] and response['daily_loss'] == 500
    assert response['currency'] == 'EUR'
    client.mt5.order_send.assert_not_called()


def test_open_profit_cannot_hide_realized_limit(client):
    client.mt5.history_deals_get.return_value = [deal(-600)]
    client.mt5.positions_get.return_value = [pos(profit=10000)]
    assert not client.open_order('EURUSD', 'BUY', .01)['success']
    client.mt5.order_send.assert_not_called()


def test_utc_day_used_without_fallback_history(client):
    assert client.open_order('EURUSD', 'BUY', .01)['success']
    start, end = client.mt5.history_deals_get.call_args.args
    assert start.hour == start.minute == start.second == 0
    assert start.utcoffset().total_seconds() == 0 and end >= start
    client.mt5.history_deals_get.assert_called_once()


def test_broker_clock_offset_applies_to_daily_loss_gate(client):
    from datetime import datetime, timezone
    client.tick_clock_offset = 10800
    broker_now = datetime.now(timezone.utc).timestamp() + 10800
    def broker_deals(start, end):
        assert abs(end.timestamp() - broker_now) < 5
        assert start.hour == 3 and start.minute == 0
        return [deal(-500)]
    client.mt5.history_deals_get.side_effect = broker_deals
    result = client.open_order('EURUSD', 'BUY', .01)
    assert not result['success'] and result['daily_loss'] == 500
    client.mt5.order_send.assert_not_called()


def test_manual_close_remains_available_above_loss_limit(client):
    client.mt5.history_deals_get.return_value = [deal(-5000)]
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    assert client.close_position(1)['success']
    client.mt5.history_deals_get.assert_not_called()


def test_cache_is_invalidated_even_for_lost_order_response(client):
    client.get_positions()
    client.mt5.order_send.side_effect = TimeoutError()
    assert client.open_order('EURUSD', 'BUY', .01)['uncertain']
    client.mt5.positions_get.return_value = [pos()]
    assert len(client.get_positions()) == 1


@pytest.mark.parametrize('magic', [HMA_MAGIC, AI_MAGIC])
def test_netting_does_not_mix_owners(client, magic):
    client.mt5.account_info.return_value.margin_mode = 0
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    assert not client.open_order('EURUSD', 'BUY', .01, magic=magic)['success']
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('change', ['invalid_tick', 'lot_step', 'stop_distance'])
def test_invalid_market_inputs_are_rejected_before_send(client, change):
    args = {}
    volume = .01
    if change == 'invalid_tick': client.mt5.symbol_info_tick.return_value.bid = 0
    if change == 'lot_step': volume = .015
    if change == 'stop_distance': args['sl_points'] = 5
    assert not client.open_order('EURUSD', 'BUY', volume, **args)['success']
    client.mt5.order_send.assert_not_called()


def test_accepted_request_replay_survives_journal_restart(client):
    first = client.open_order('EURUSD', 'BUY', .01, request_id='same-intent')
    client.journal = OrderJournal()
    second = client.open_order('EURUSD', 'BUY', .01, request_id='same-intent')
    assert first['success'] and second['replayed']
    client.mt5.order_send.assert_called_once()
    assert client.open_order('EURUSD', 'SELL', .01, request_id='same-intent')['conflict']
    client.mt5.order_send.assert_called_once()


def test_lost_response_is_never_resent(client):
    client.mt5.order_send.side_effect = TimeoutError()
    assert client.open_order('EURUSD', 'BUY', .01, request_id='lost-intent')['uncertain']
    assert client.open_order('EURUSD', 'BUY', .01, request_id='lost-intent')['uncertain']
    client.mt5.order_send.assert_called_once()


def test_concurrent_journals_reserve_before_sending(tmp_path):
    path = tmp_path / 'journal.db'
    a, b = OrderJournal(path), OrderJournal(path)
    entered, release = threading.Event(), threading.Event()
    def send():
        entered.set()
        assert release.wait(2)
        return {'success': True}
    worker = threading.Thread(target=lambda: a.run('one', {}, send))
    worker.start()
    try:
        assert entered.wait(1)
        duplicate = b.run('one', {}, lambda: pytest.fail('duplicate send'))
        assert duplicate['uncertain'] and duplicate['replayed']
    finally:
        release.set()
        worker.join(2)
    assert b.run('one', {}, lambda: pytest.fail('duplicate send'))['success']


def test_crash_pending_record_is_never_resent(tmp_path):
    journal = OrderJournal(tmp_path / 'journal.db')
    with pytest.raises(KeyboardInterrupt):
        journal.run('interrupted', {}, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    restarted = OrderJournal(tmp_path / 'journal.db')
    assert restarted.run('interrupted', {}, lambda: pytest.fail('duplicate send'))['uncertain']


def test_waiting_auto_order_observes_stop(client):
    result = []
    with client._lock:
        worker = threading.Thread(target=lambda: result.append(client.open_order('EURUSD', 'BUY', .01, magic=HMA_MAGIC)))
        worker.start()
        client.automation_stopped.set()
    worker.join(2)
    assert result and not result[0]['success']
    client.mt5.order_send.assert_not_called()


def test_large_period_requests_sufficient_history_and_never_telegram(client):
    bot = StrategyBot(client)
    bot.second_ma_period = 200
    client.get_rates = Mock(return_value=[])
    bot._check_strategy()
    assert client.get_rates.call_args.kwargs['count'] >= 215
    assert not hasattr(bot, 'send_telegram')


def authenticated_web():
    from app.main import app
    token = base64.b64encode(b'test:test-panel-password').decode()
    return TestClient(app, headers={'Authorization': 'Basic ' + token})


def test_dashboard_and_api_require_auth_and_debug_is_disabled(monkeypatch):
    from app.main import app
    web = TestClient(app)
    for path in ['/', '/api/account', '/api/positions', '/api/debug/inspect']:
        assert web.get(path).status_code == 401
    assert web.post('/api/order/open', json={}).status_code == 401
    assert authenticated_web().get('/').status_code == 200
    assert authenticated_web().get('/api/debug/inspect').status_code == 404
    monkeypatch.delenv('DASHBOARD_PASSWORD')
    assert authenticated_web().get('/').status_code == 503


def test_cross_site_mutations_rejected():
    response = authenticated_web().post('/api/order/close-all', json={'request_id':'valid-request-id-123'},
                                        headers={'Origin': 'https://attacker.example'})
    assert response.status_code == 403


def test_account_login_requires_explicit_demo_or_real_choice():
    base = {'login': 3, 'password': 'example', 'server': 'broker-server'}
    assert authenticated_web().post('/api/account/login', json=base).status_code == 422
    assert authenticated_web().post('/api/account/login', json={**base, 'account_type':'CONTEST'}).status_code == 422


def test_real_full_auto_requires_explicit_confirmation(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'login_id', 3)
    monkeypatch.setattr(main.mt5_client, 'server', 'broker-live')
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    monkeypatch.setattr(main.mt5_client, 'ensure_connected', lambda: True)
    monkeypatch.setattr(main.mt5_client, '_selected_account', lambda: [3, 'broker-live'])
    update = Mock(return_value={'success': True})
    monkeypatch.setattr(main.ai_advisor, 'update_autopilot', update)
    payload = {'enabled': True, 'mode': 'FULL_AUTO'}
    assert authenticated_web().post('/api/ai/autopilot', json=payload).status_code == 403
    monkeypatch.setattr(main.security_sessions, 'verify_totp', lambda code, username=None: True)
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, base_url='https://testserver', headers={'Authorization': 'Basic ' + token})
    assert web.post('/api/security/unlock', json={'code':'123456'}).status_code == 200
    assert web.post('/api/ai/autopilot', json=payload).status_code == 409
    update.assert_not_called()
    response = web.post('/api/ai/autopilot', json={**payload, 'confirm_real_full_auto': True})
    assert response.status_code == 200
    update.assert_called_once_with(payload)


def test_real_open_requires_second_factor_but_emergency_close_stays_available(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    open_order = Mock(return_value={'success':True, 'ticket':5})
    monkeypatch.setattr(main.mt5_client, 'open_order', open_order)
    payload = {'request_id':'real-order-test-12345', 'symbol':'EURUSD', 'order_type':'BUY', 'volume':.01}
    assert authenticated_web().post('/api/order/open', json=payload).status_code == 403
    open_order.assert_not_called()
    monkeypatch.setattr(main.mt5_client, 'close_position', Mock(return_value={'success':True}))
    assert authenticated_web().post('/api/order/close', json={'request_id':'real-close-test-12345','ticket':5}).status_code == 200


def test_api_position_failure_returns_error_not_empty_list(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'get_positions', Mock(side_effect=MT5DataError('no data')))
    response = authenticated_web().get('/api/positions')
    assert response.status_code == 503


def test_close_all_stops_automation_before_close(monkeypatch):
    from app import main
    monkeypatch.setattr(main.bot, 'stop', Mock())
    monkeypatch.setattr(main.ai_advisor, 'update_autopilot', Mock())
    def close(kind):
        assert main.mt5_client.automation_stopped.is_set()
        main.bot.stop.assert_called_once()
        main.ai_advisor.update_autopilot.assert_called_once_with({'enabled':False})
        return {'success': True, 'closed_count': 1, 'total_matched': 1, 'errors': []}
    monkeypatch.setattr(main.mt5_client, 'close_by_filter', close)
    monkeypatch.setattr(main.mt5_client, 'get_pending_orders', lambda: [])
    monkeypatch.setattr(main.mt5_client, 'get_positions', lambda fresh=False: [])
    response = authenticated_web().post('/api/order/close-all', json={'request_id':'close-all-test-123'})
    assert response.json()['success']
    assert main.mt5_client.journal.trading_halted()
    assert authenticated_web().get('/api/trading/status').json()['new_orders_halted']
    main.mt5_client.automation_stopped.clear()


def test_resume_requires_verified_account_and_clears_halt(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'login_id', 1)
    monkeypatch.setattr(main.mt5_client, 'server', 'test')
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    main.mt5_client.journal.set_trading_halted(True)
    monkeypatch.setattr(main.mt5_client, 'ensure_connected', lambda: True)
    monkeypatch.setattr(main.mt5_client, '_selected_account', Mock(side_effect=MT5DataError('Yanlış hesap')))
    assert authenticated_web().post('/api/trading/resume', json={}).status_code == 409
    assert main.mt5_client.journal.trading_halted()
    monkeypatch.setattr(main.mt5_client, '_selected_account', lambda: [1, 'test'])
    monkeypatch.setattr(main.mt5_client, 'get_risk_status', lambda: {'daily_loss':600, 'daily_loss_limit':500, 'daily_limit_enabled':True})
    assert authenticated_web().post('/api/trading/resume', json={}).status_code == 409
    assert main.mt5_client.journal.trading_halted()
    monkeypatch.setattr(main.mt5_client, 'get_risk_status', lambda: {'daily_loss':100, 'daily_loss_limit':500, 'daily_limit_enabled':True})
    assert authenticated_web().post('/api/trading/resume', json={}).json()['new_orders_halted'] is False
    assert not main.mt5_client.journal.trading_halted()


def test_flatten_reports_remaining_broker_exposure(monkeypatch):
    from app import main
    calls = iter([[], [{'ticket': 8}]])
    monkeypatch.setattr(main.mt5_client, 'get_pending_orders', lambda: next(calls))
    monkeypatch.setattr(main.mt5_client, 'get_positions', lambda fresh=False: [])
    monkeypatch.setattr(main.mt5_client, 'close_by_filter', lambda kind: {'success': True, 'closed_count': 0, 'errors': []})
    result = main.flatten_account('verify-remaining')
    assert not result['success'] and '1 bekleyen emir' in result['errors'][0]


def test_ai_uses_saved_recommendation_and_preserves_execution_state(monkeypatch, tmp_path):
    from app import ai_advisor
    monkeypatch.setattr(ai_advisor, 'MEMORY_PATHS', [str(tmp_path / 'ai.json')])
    mt5 = Mock()
    a = ai_advisor.DeepSeekAdvisor(mt5)
    rec = {'id':'trusted-rec', 'action':'BUY','symbol':'EURUSD','expires_at':time.time()+900,
           'sl_points':200,'tp_points':400,'suggested_lot':.01}
    a.cost_state['advice_cache']['key'] = {'recommendation':rec, 'expires_at':rec['expires_at']}
    mt5.open_order.return_value = {'success':True,'partial':True,'volume':.005,'retcode':10010}
    response = a.execute_recommendation({**rec,'action':'SELL','symbol':'XAUUSD','sl_points':0})
    assert response['partial'] and response['volume'] == .005
    assert mt5.open_order.call_args.args == ('EURUSD','BUY',.01)
    assert mt5.open_order.call_args.kwargs['sl_points'] == 200
    assert mt5.open_order.call_args.kwargs['request_id'] == 'ai-trusted-rec'
    assert not a.execute_recommendation({})['success']
    assert not a.execute_recommendation({**rec,'id':'forged'})['success']


def test_old_autopilot_analysis_cannot_trade_after_settings_change(monkeypatch, tmp_path):
    from app import ai_advisor
    monkeypatch.setattr(ai_advisor, 'MEMORY_PATHS', [str(tmp_path / 'ai.json')])
    mt5 = Mock()
    a = ai_advisor.DeepSeekAdvisor(mt5)
    a.update_autopilot({'enabled':True,'mode':'FULL_AUTO'})
    old_event, old_generation = a._auto_stop, a.autopilot_generation
    a.update_autopilot({'max_lot':.02})
    assert old_event.is_set() and a._auto_stop is not old_event
    rec = {'id':'trusted-rec','action':'BUY','symbol':'EURUSD','expires_at':time.time()+900}
    a.cost_state['advice_cache']['key'] = {'recommendation':rec}
    assert not a.execute_recommendation(rec, automatic=True, generation=old_generation)['success']
    mt5.open_order.assert_not_called()


def test_waiting_order_precedes_dashboard_reads():
    from app.trading_lock import TradingLock
    lock = TradingLock()
    sequence = []
    def read():
        with lock:
            sequence.append('read')
    def order():
        with lock.order():
            # Reentrant health checks must not deadlock behind our own priority.
            with lock:
                sequence.append('order')
    with lock:
        readers = [threading.Thread(target=read) for _ in range(3)]
        for t in readers: t.start()
        trader = threading.Thread(target=order)
        trader.start()
        deadline = time.monotonic() + 1
        while not lock._orders_waiting and time.monotonic() < deadline:
            time.sleep(.001)
        assert lock._orders_waiting == 1
    trader.join(2)
    for t in readers: t.join(2)
    assert sequence == ['order', 'read', 'read', 'read']


def test_priority_lock_timeout_does_not_block_future_reads():
    from app.trading_lock import TradingLock
    lock = TradingLock()
    results = []
    with lock:
        t = threading.Thread(target=lambda: results.append(lock.acquire(timeout=.01, order=True)))
        t.start()
        t.join(1)
    assert results == [False] and lock._orders_waiting == 0
    assert lock.acquire(blocking=False)
    lock.release()


def test_strategy_close_rechecks_magic_and_netting(client):
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    assert not client.close_position(1, expected_magic=HMA_MAGIC)['success']
    client.mt5.positions_get.return_value = [pos(HMA_MAGIC)]
    client.mt5.account_info.return_value.margin_mode = 0
    assert not client.close_position(1, expected_magic=HMA_MAGIC)['success']
    client.mt5.order_send.assert_not_called()


def test_history_empty_range_does_not_fetch_all_time(client):
    assert client.get_history(days=7) == []
    client.mt5.history_deals_get.assert_called_once()
    start, end = client.mt5.history_deals_get.call_args.args
    assert 6.99 < (end-start).total_seconds()/86400 < 7.01


def test_remote_bridge_serializes_full_result_once(client, monkeypatch):
    import sys
    from types import ModuleType
    from rpyc.utils.server import ThreadedServer
    from app.mt5_client import rpyc
    fake = ModuleType('MetaTrader5')
    for name in ('terminal_info','account_info','positions_get','orders_get','history_deals_get','symbol_select','symbol_info','symbol_info_tick','order_check','order_send'):
        setattr(fake, name, getattr(client.mt5, name))
    monkeypatch.setitem(sys.modules, 'MetaTrader5', fake)
    disconnected = threading.Event()
    class Service(rpyc.SlaveService):
        def on_disconnect(self, conn):
            try:
                super().on_disconnect(conn)
            finally:
                disconnected.set()
    server = ThreadedServer(Service, hostname='127.0.0.1', port=0, auto_register=False)
    server.listener.listen(5)
    worker = threading.Thread(target=server.start, daemon=True)
    worker.start()
    client.conn = rpyc.classic.connect('127.0.0.1', server.port)
    try:
        response = client.open_order('EURUSD', 'BUY', .01, request_id='remote-bridge-test')
        assert response['success'], response
        assert type(response['ticket']) is int
        assert client.get_positions(fresh=True) == []
        fake.order_send.assert_called_once()
    finally:
        client._reset_connection()
        assert disconnected.wait(2)
        server.close()
        worker.join(2)


def test_unresolved_barrier_is_account_symbol_and_strategy_scoped(tmp_path):
    journal=OrderJournal(tmp_path/'journal.sqlite3')
    metadata={'account':[1,'demo'],'order':{'symbol':'XAUUSD','magic':HMA_MAGIC}}
    journal.run('pending-hma',{'symbol':'XAUUSD'},lambda:{'success':False,'uncertain':True},metadata=metadata)
    assert journal.has_unresolved_open([1,'demo'],'XAUUSD',HMA_MAGIC)
    assert not journal.has_unresolved_open([2,'demo'],'XAUUSD',HMA_MAGIC)
    assert not journal.has_unresolved_open([1,'demo'],'EURUSD',HMA_MAGIC)
    assert not journal.has_unresolved_open([1,'demo'],'XAUUSD',MANUAL_MAGIC)
    journal.resolve('pending-hma',{'success':False,'uncertain':False,'pending':False})
    assert not journal.has_unresolved_open([1,'demo'],'XAUUSD',HMA_MAGIC)

# MT5 ticket compatibility: explicit prices must survive preview, journal and send.
@pytest.mark.parametrize('side,sl,tp,expected_type', [
    ('BUY', 1.099, 1.102, 0), ('SELL', 1.101, 1.098, 1),
])
def test_manual_price_ticket_matches_preview_and_actual_request(client, side, sl, tp, expected_type):
    preview = client.trade_preview('EURUSD', side, .01, sl_price=sl, tp_price=tp, deviation_points=37)
    assert preview['success']
    assert preview['entry_price'] == (1.1 if side == 'BUY' else 1.0999)
    assert (preview['stop_price'], preview['take_profit_price']) == (sl, tp)
    result = client.open_order('EURUSD', side, .01, sl_price=sl, tp_price=tp, deviation_points=37)
    assert result['success']
    request = client.mt5.order_send.call_args.args[0]
    assert (request['type'], request['sl'], request['tp'], request['deviation']) == (expected_type, sl, tp, 37)
    assert result['execution']['deviation_points'] == 37


def test_manual_zero_price_levels_do_not_create_default_protection(client):
    result = client.open_order('EURUSD', 'BUY', .01, sl_price=0, tp_price=0)
    assert result['success']
    request = client.mt5.order_send.call_args.args[0]
    assert request['sl'] == request['tp'] == 0


@pytest.mark.parametrize('kwargs', [
    {'sl_price':1.099, 'sl_points':200},
    {'sl_price':1.09995},  # Below Ask, but above the executable Bid.
    {'sl_price':float('nan')},
    {'tp_price':-1},
    {'deviation_points':-1},
])
def test_invalid_price_ticket_never_sends(client, kwargs):
    assert not client.open_order('EURUSD', 'BUY', .01, **kwargs)['success']
    client.mt5.order_send.assert_not_called()


def test_price_ticket_tp_uses_bid_not_opening_ask(client):
    # A BUY TP between Bid and Ask is legal when it satisfies the stop distance.
    client.mt5.symbol_info.return_value.trade_stops_level = 1
    assert client.open_order('EURUSD', 'BUY', .01, sl_price=0, tp_price=1.09995)['success']


def test_pending_ticket_uses_explicit_prices_and_return_policy(client):
    info = client.mt5.symbol_info.return_value
    info.order_mode, info.expiration_mode = 127, 1
    result = client.open_order('EURUSD', 'BUY', .01, pending_type='BUY_LIMIT', entry_price=1.09,
                               sl_price=1.089, tp_price=1.092)
    assert result['success']
    request = client.mt5.order_send.call_args.args[0]
    assert (request['action'], request['type'], request['price'], request['type_filling']) == (5,2,1.09,2)
    assert (request['sl'], request['tp']) == (1.089,1.092)


def test_changed_price_or_deviation_cannot_reuse_existing_request_id(client):
    rid = 'mt5-price-ticket-id'
    assert client.open_order('EURUSD','BUY',.01,sl_price=1.099,tp_price=1.102,request_id=rid)['success']
    for change in ({'sl_price':1.098}, {'deviation_points':50}):
        kwargs = {'sl_price':1.099,'tp_price':1.102,**change}
        result = client.open_order('EURUSD','BUY',.01,request_id=rid,**kwargs)
        assert result.get('conflict')
    assert client.mt5.order_send.call_count == 1


def test_bitcoin_spread_does_not_block_unprotected_demo_ticket(client):
    info = client.mt5.symbol_info.return_value
    info.point, info.digits, info.trade_stops_level = .01, 2, 0
    client.mt5.symbol_info_tick.return_value = NS(ask=82476.5, bid=82462.5, time=time.time())
    preview = client.trade_preview('BTCUSD','BUY',.01,sl_price=0,tp_price=0)
    assert preview['success'] and preview['spread_points'] == 1400
    assert client.open_order('BTCUSD','BUY',.01,sl_price=0,tp_price=0)['success']
    assert client.mt5.order_send.call_args.args[0]['sl'] == 0


def test_explicit_price_not_silently_rounded_to_broker_tick_size(client):
    client.mt5.symbol_info.return_value.trade_tick_size = .0001
    result = client.open_order('EURUSD','BUY',.01,sl_price=1.09895,tp_price=1.102)
    assert not result['success'] and 'fiyat adımına' in result['error']
    client.mt5.order_send.assert_not_called()


def test_point_ticket_replays_pre_upgrade_journal_without_resending(client):
    rid = 'pre-upgrade-point-ticket'
    old_payload = dict(symbol='EURUSD',order_type='BUY',volume=.01,sl_points=200,tp_points=400,
                       comment='HMA Web App',magic=MANUAL_MAGIC,pending_type=None,entry_price=None)
    client.journal.run(rid, old_payload, lambda: {'success':True,'ticket':321},metadata={'account':[1,'test']})
    result = client.open_order('EURUSD','BUY',.01,200,400,request_id=rid)
    assert result['success'] and result['replayed'] and result['ticket'] == 321
    client.mt5.order_send.assert_not_called()


def test_http_price_ticket_fields_reach_preview_and_pending_bridge(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'mt5_client', client)
    info = client.mt5.symbol_info.return_value
    info.order_mode, info.expiration_mode = 127,1
    payload={'symbol':'EURUSD','order_type':'BUY','volume':.01,'sl_price':1.089,'tp_price':1.092,
             'pending_type':'BUY_LIMIT','entry_price':1.09,'deviation_points':35}
    preview = authenticated_web().post('/api/trade/preview',json=payload)
    assert preview.status_code == 200
    assert preview.json()['stop_price'] == payload['sl_price']
    result = authenticated_web().post('/api/order/pending',json={**payload,'request_id':'http-mt5-price-test'})
    assert result.status_code == 200
    request = client.mt5.order_send.call_args.args[0]
    assert request['sl'] == payload['sl_price'] and request['tp'] == payload['tp_price']
    assert request['deviation'] == 35


@pytest.mark.parametrize('side,kind,entry,limit,expected', [
    ('BUY','BUY_LIMIT',1.09,None,2),('SELL','SELL_LIMIT',1.11,None,3),
    ('BUY','BUY_STOP',1.11,None,4),('SELL','SELL_STOP',1.09,None,5),
    ('BUY','BUY_STOP_LIMIT',1.11,1.105,6),('SELL','SELL_STOP_LIMIT',1.09,1.095,7),
])
@pytest.mark.parametrize('time_type,code', [('GTC',0),('DAY',1),('SPECIFIED',2),('SPECIFIED_DAY',3)])
def test_mt5_all_pending_types_and_expiration_modes(client,side,kind,entry,limit,expected,time_type,code):
    info=client.mt5.symbol_info.return_value
    info.order_mode,info.expiration_mode=127,15
    expiry=int(time.time())+86400 if code>=2 else 0
    kwargs=dict(pending_type=kind,entry_price=entry,stoplimit_price=limit,time_type=time_type,expiration=expiry,
                sl_price=0,tp_price=0,manual_mode='MT5')
    preview=client.trade_preview('EURUSD',side,.01,**kwargs)
    assert preview['success'] and preview['stoplimit_price']==(limit or 0)
    result=client.open_order('EURUSD',side,.01,**kwargs)
    assert result['success']
    req=client.mt5.order_send.call_args.args[0]
    assert (req['action'],req['type'],req['type_time'],req['expiration'],req['type_filling'])==(5,expected,code,expiry,2)
    assert req['stoplimit']==(limit or 0) and req['price']==entry


@pytest.mark.parametrize('kwargs', [
    {'pending_type':'BUY_STOP_LIMIT','entry_price':1.11,'stoplimit_price':1.12},
    {'pending_type':'BUY_STOP_LIMIT','entry_price':1.11},
    {'pending_type':'SELL_STOP_LIMIT','entry_price':1.09,'stoplimit_price':1.08},
    {'pending_type':'BUY_LIMIT','entry_price':1.09,'stoplimit_price':1.08},
    {'pending_type':'BUY_LIMIT','entry_price':1.09,'time_type':'SPECIFIED','expiration':1},
    {'pending_type':'BUY_LIMIT','entry_price':1.09,'expiration':int(time.time())+86400},
])
def test_invalid_mt5_pending_fields_never_send(client,kwargs):
    client.mt5.symbol_info.return_value.order_mode=127
    client.mt5.symbol_info.return_value.expiration_mode=15
    side=kwargs['pending_type'].split('_')[0]
    assert not client.open_order('EURUSD',side,.01,manual_mode='MT5',**kwargs)['success']
    client.mt5.order_send.assert_not_called()


def test_mt5_manual_mode_uses_broker_limits_not_panel_caps_or_history(client):
    client.mt5.history_deals_get.return_value=None
    result=client.open_order('EURUSD','BUY',2,manual_mode='MT5',sl_price=0,tp_price=0)
    assert result['success'] and not result['stop_required']
    req=client.mt5.order_send.call_args.args[0]
    assert req['volume']==2 and req['sl']==req['tp']==0
    assert 'price' not in req
    client.mt5.history_deals_get.assert_not_called()
    client.mt5.order_check.assert_called_once()


def test_mt5_manual_real_account_accepts_optional_stop_but_ai_does_not(client):
    client.account_type='REAL';client.mt5.account_info.return_value.trade_mode=2
    assert client.open_order('EURUSD','BUY',.01,manual_mode='MT5',sl_price=0,tp_price=0)['success']
    assert not client.open_order('EURUSD','BUY',.01,manual_mode='MT5',magic=AI_MAGIC)['success']
    assert client.mt5.order_send.call_count==1


def test_mt5_manual_netting_allows_opposite_native_position(client):
    client.mt5.account_info.return_value.margin_mode=0
    client.mt5.positions_get.return_value=[pos(magic=0,side=1)]
    assert client.open_order('EURUSD','BUY',.01,manual_mode='MT5')['success']
    req=client.mt5.order_send.call_args.args[0]
    assert req['type']==0 and req['volume']==.01


@pytest.mark.parametrize('policy,code',[('FOK',0),('IOC',1)])
def test_explicit_mt5_fill_policy_is_sent_once(client,policy,code):
    assert client.open_order('EURUSD','BUY',.01,manual_mode='MT5',filling_policy=policy)['success']
    assert client.mt5.order_send.call_args.args[0]['type_filling']==code
    assert client.mt5.order_send.call_count==1


def test_unsupported_return_market_policy_and_expiry_never_send(client):
    assert not client.open_order('EURUSD','BUY',.01,manual_mode='MT5',filling_policy='RETURN')['success']
    client.mt5.symbol_info.return_value.order_mode=127
    client.mt5.symbol_info.return_value.expiration_mode=1
    assert not client.open_order('EURUSD','BUY',.01,manual_mode='MT5',pending_type='BUY_STOP_LIMIT',entry_price=1.11,
                                 stoplimit_price=1.105,time_type='DAY')['success']
    client.mt5.order_send.assert_not_called()


def pending_row(**updates):
    return NS(ticket=432,symbol='EURUSD',type=2,magic=MANUAL_MAGIC,volume_current=.01,price_open=1.09,
              sl=1.089,tp=1.092,price_stoplimit=0,type_time=0,time_expiration=0,**updates)


def test_pending_modification_matches_mt5_and_replays_without_sending_twice(client):
    from app.mt5_bridge import _pending_state
    order=pending_row();client.mt5.orders_get.return_value=[order]
    client.mt5.symbol_info.return_value.order_mode=127
    client.mt5.symbol_info.return_value.expiration_mode=15
    values=dict(entry_price=1.08,sl_price=0,tp_price=0,time_type='DAY',expiration=0,manual_mode='MT5',expected_state=list(_pending_state(order)))
    result=client.modify_pending(432,values,'pending-modify-mt5-test')
    assert result['success']
    req=client.mt5.order_send.call_args.args[0]
    assert (req['action'],req['order'],req['price'],req['type_time'])==(7,432,1.08,1)
    assert req['sl']==req['tp']==0
    assert client.modify_pending(432,values,'pending-modify-mt5-test')['replayed']
    assert client.mt5.order_send.call_count==1


def test_pending_order_changed_during_preflight_is_not_modified(client):
    from app.mt5_bridge import _pending_state
    order=pending_row();client.mt5.orders_get.return_value=[order]
    client.mt5.symbol_info.return_value.order_mode=127
    client.mt5.symbol_info.return_value.expiration_mode=1
    values=dict(entry_price=1.08,manual_mode='MT5',expected_state=list(_pending_state(order)))
    def changed(req):
        order.price_open=1.085
        return NS(retcode=0,comment='ok')
    client.mt5.order_check.side_effect=changed
    result=client.modify_pending(432,values,'pending-change-mt5-test')
    assert not result['success'] and not result.get('uncertain')
    client.mt5.order_send.assert_not_called()


def test_mt5_manual_position_stop_removal_is_allowed_on_real(client):
    client.account_type='REAL';client.mt5.account_info.return_value.trade_mode=2
    p=pos(magic=MANUAL_MAGIC,side=0);p.sl=1.099
    client.mt5.positions_get.return_value=[p]
    result=client.manage_position(1,'stops',{'sl':0,'tp':0,'manual_mode':'MT5'},'mt5-manual-remove-stop')
    assert result['success'] and client.mt5.order_send.call_args.args[0]['sl']==0


def test_market_endpoint_rejects_pending_fields_instead_of_sending_market_order(client,monkeypatch):
    from app import main
    monkeypatch.setattr(main,'mt5_client',client)
    payload={'symbol':'EURUSD','order_type':'BUY','volume':.01,'request_id':'mistyped-endpoint-pending',
             'pending_type':'BUY_LIMIT','entry_price':1.09,'manual_mode':'MT5'}
    response=authenticated_web().post('/api/order/open',json=payload)
    assert response.status_code==422
    client.mt5.order_send.assert_not_called()


def test_mt5_manual_mode_still_respects_broker_rejection_and_emergency_lock(client):
    client.mt5.order_check.return_value=NS(retcode=10019,comment='No money')
    result=client.open_order('EURUSD','BUY',2,manual_mode='MT5')
    assert not result['success'] and result['retcode']==10019
    client.journal.set_trading_halted(True)
    result=client.open_order('EURUSD','BUY',2,manual_mode='MT5')
    assert result['reason_code']=='trading_halted'
    client.mt5.order_send.assert_not_called()


def test_pending_modify_http_preserves_snapshot_and_price_fields(client,monkeypatch):
    from app import main
    from app.mt5_bridge import _pending_state
    monkeypatch.setattr(main,'mt5_client',client)
    order=pending_row();client.mt5.orders_get.return_value=[order]
    client.mt5.symbol_info.return_value.order_mode=127
    client.mt5.symbol_info.return_value.expiration_mode=1
    payload=dict(ticket=432,request_id='pending-modify-http-test',entry_price=1.08,sl_price=0,tp_price=0,
                 manual_mode='MT5',expected_state=list(_pending_state(order)))
    response=authenticated_web().post('/api/order/modify',json=payload)
    assert response.status_code==200
    req=client.mt5.order_send.call_args.args[0]
    assert req['action']==7 and req['price']==1.08


def test_pending_modify_api_rejects_nonfinite_expected_price_before_broker(client,monkeypatch):
    from app import main
    from app.mt5_bridge import _pending_state
    monkeypatch.setattr(main,'mt5_client',client)
    state=list(_pending_state(pending_row()));state[5]='NaN'
    response=authenticated_web().post('/api/order/modify',json={'ticket':432,'request_id':'pending-bad-state-test','entry_price':1.08,'expected_state':state})
    assert response.status_code==422
    client.mt5.order_send.assert_not_called()


def test_close_on_a_quiet_symbol_is_sent_like_mt5(client):
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    client.mt5.symbol_info_tick.return_value.time = time.time() - 45
    assert client.close_position(1)['success']
    client.mt5.order_send.assert_called_once()


def test_close_requote_is_resent_at_the_new_price_like_mt5(client):
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    client.mt5.symbol_info.return_value.trade_exemode = 1
    replies = iter([NS(retcode=10004, order=0, price=0, volume=0, comment='Requote'),
                    NS(retcode=10009, order=124, price=1.1005, volume=.01, comment='done')])

    def send(request):
        client.mt5.symbol_info_tick.return_value = NS(ask=1.1005, bid=1.1004, time=time.time())  # market moved
        return next(replies)
    client.mt5.order_send.side_effect = send
    result = client.close_position(1)
    assert result['success'] and client.mt5.order_send.call_count == 2
    assert [c[0][0]['price'] for c in client.mt5.order_send.call_args_list] == [1.1, 1.1005]


def test_close_requote_retries_are_bounded(client):
    client.mt5.positions_get.return_value = [pos(MANUAL_MAGIC)]
    client.mt5.symbol_info.return_value.trade_exemode = 1
    client.mt5.order_send.return_value = NS(retcode=10004, order=0, price=0, volume=0, comment='Requote')
    assert not client.close_position(1)['success']
    assert client.mt5.order_send.call_count == 3
