import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.mt5_bridge import MANUAL_MAGIC
from app.strategy_bot import StrategyBot
from test_trade_safety import client, pos


def test_queued_order_keeps_its_authorized_account(client):
    responses = []
    with client._lock:
        worker = threading.Thread(target=lambda: responses.append(client.open_order(
            'EURUSD', 'BUY', .01, expected_account=[1, 'test', 'DEMO'])))
        worker.start()
        client.login_id, client.account_type = 2, 'REAL'
        client.mt5.account_info.return_value.login = 2
        client.mt5.account_info.return_value.trade_mode = 2
    worker.join(2)
    assert not worker.is_alive()
    assert not responses[0]['success'] and not responses[0].get('uncertain')
    client.mt5.order_send.assert_not_called()


def test_open_id_is_not_replayed_as_success_on_a_different_account(client):
    assert client.open_order('EURUSD', 'BUY', .01, request_id='account-bound-intent')['success']
    client.login_id = 2
    client.mt5.account_info.return_value.login = 2
    changed = client.open_order('EURUSD', 'BUY', .01, request_id='account-bound-intent')
    assert changed['conflict'] and not changed['success']
    assert client.mt5.order_send.call_count == 1


@pytest.mark.parametrize('action', ['partial', 'cancel'])
def test_position_actions_reject_a_stale_expected_account(client, action):
    if action == 'partial':
        result = client.manage_position(1, action, {'volume': .01}, 'bound-partial',
            expected_account=[2, 'test', 'DEMO'])
    else:
        result = client.cancel_pending(1, 'bound-cancel', expected_account=[2, 'test', 'DEMO'])
    assert not result['success'] and not result.get('uncertain')
    client.mt5.order_send.assert_not_called()


def test_full_close_uses_fresh_volume_and_preserves_owner(client):
    current = pos(magic=MANUAL_MAGIC)
    client.mt5.positions_get.return_value = [current]
    result = client.close_position(1, pos_data={'symbol': 'OTHER', 'volume': .03, 'type': 'BUY'})
    assert result['success']
    sent = client.mt5.order_send.call_args.args[0]
    assert (sent['symbol'], sent['volume'], sent['magic']) == ('EURUSD', .01, MANUAL_MAGIC)
    client.mt5.order_check.assert_called_once()


@pytest.mark.parametrize('retcode, flag', [(10008, 'pending'), (10010, 'partial'), (10012, 'uncertain')])
def test_full_close_does_not_claim_completion_for_incomplete_results(client, retcode, flag):
    client.mt5.positions_get.return_value = [pos(magic=MANUAL_MAGIC)]
    client.mt5.order_send.return_value.retcode = retcode
    result = client.close_position(1)
    assert result[flag] and not result['success']
    client.mt5.order_send.assert_called_once()


def test_failed_login_cannot_leave_previous_account_in_cache(client):
    client._account_cache = {'login': 1, 'account_mismatch': False}
    client._positions_cache = [{'ticket': 1}]
    client._price_cache['EURUSD'] = {'bid': 1}
    client.mt5.initialize.return_value = True
    client.mt5.account_info.return_value.login = 2
    result = client.login(3, 'unused', 'other-server', 'DEMO')
    assert not result['success']
    assert client._account_cache is None and client._positions_cache is None
    assert client._price_cache == {}


@pytest.fixture
def ready_client(client, monkeypatch):
    account = client.mt5.account_info.return_value
    account.equity, account.margin_free = 1000., 800.
    client.mt5.order_calc_profit.return_value = -2.
    client.mt5.order_calc_margin.return_value = 25.
    rows = [{'time': i * 900, 'open': 1.1, 'high': 1.101, 'low': 1.099,
             'close': 1.1, 'tick_volume': 10} for i in range(200)]
    monkeypatch.setattr(client, 'get_rates', Mock(return_value=rows))
    return client


def test_bot_readiness_is_read_only_and_checks_margin(ready_client):
    config = StrategyBot(ready_client).config_snapshot()
    result = ready_client.bot_readiness(config)
    assert result['success'] and result['account'] == [1, 'test', 'DEMO']
    ready_client.mt5.order_send.assert_not_called()
    ready_client.mt5.order_calc_margin.return_value = 900.
    result = ready_client.bot_readiness(config)
    assert not result['success'] and 'teminat' in result['error']


@pytest.mark.parametrize('failure', ['netting', 'stale', 'history', 'risk', 'permission'])
def test_bot_readiness_rejects_an_unusable_trading_state(ready_client, failure):
    if failure == 'netting':
        ready_client.mt5.account_info.return_value.margin_mode = 0
    elif failure == 'stale':
        ready_client.mt5.symbol_info_tick.return_value.time = time.time() - 60
    elif failure == 'history':
        ready_client.get_rates.return_value = []
    elif failure == 'risk':
        ready_client.mt5.history_deals_get.return_value = [NS(type=0, profit=-500, commission=0, swap=0, fee=0)]
    else:
        ready_client.mt5.terminal_info.return_value.trade_allowed = False
    assert not ready_client.bot_readiness(StrategyBot(ready_client).config_snapshot())['success']
    ready_client.mt5.order_send.assert_not_called()


def test_api_rejects_ui_intent_for_another_account(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    web = TestClient(main.app)
    response = web.post('/api/order/open', auth=('test', 'test-panel-password'), json={
        'request_id': 'stale-account-order', 'symbol': 'EURUSD', 'order_type': 'BUY', 'volume': .01,
        'expected_account': [2, 'test', 'DEMO']})
    assert response.status_code == 409
    client.mt5.order_send.assert_not_called()


def test_bot_toggle_does_not_start_when_preflight_fails(client, monkeypatch):
    strategy = StrategyBot(client)
    monkeypatch.setattr(main, 'mt5_client', client)
    monkeypatch.setattr(main, 'bot', strategy)
    monkeypatch.setattr(client, 'bot_readiness', Mock(return_value={'success': False, 'error': 'Yetersiz teminat'}))
    response = TestClient(main.app).post('/api/bot/toggle', auth=('test', 'test-panel-password'))
    assert response.status_code == 409 and 'teminat' in response.json()['detail']
    assert not strategy.is_running and strategy.task is None


def test_risk_preview_is_bound_to_the_displayed_account(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    response = TestClient(main.app).post('/api/trade/preview', auth=('test', 'test-panel-password'), json={
        'symbol': 'EURUSD', 'order_type': 'BUY', 'volume': .01, 'sl_points': 200,
        'expected_account': [2, 'test', 'DEMO']})
    assert response.status_code == 409
    client.mt5.order_calc_profit.assert_not_called()


@pytest.mark.parametrize('value', ['nan', 'inf', '0', '-1', '101'])
def test_invalid_money_risk_configuration_fails_closed(client, monkeypatch, value):
    monkeypatch.setenv('MAX_TRADE_RISK_PCT', value)
    with pytest.raises(ValueError, match='MAX_TRADE_RISK_PCT'):
        type(client)()


def test_same_host_with_different_origin_scheme_cannot_send_an_order(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    response = TestClient(main.app, base_url='https://testserver').post('/api/order/open',
        auth=('test', 'test-panel-password'), headers={'Origin': 'http://testserver'}, json={
            'request_id': 'cross-scheme-order', 'symbol': 'EURUSD', 'order_type': 'BUY', 'volume': .01})
    assert response.status_code == 403
    client.mt5.order_send.assert_not_called()


def test_queued_automation_close_observes_the_stop_request(client):
    stop = threading.Event()
    results = []
    with client._lock:
        worker = threading.Thread(target=lambda: results.append(client.close_position(1,
            expected_magic=123461, stop_event=stop)))
        worker.start()
        stop.set()
    worker.join(2)
    assert not worker.is_alive() and not results[0]['success']
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('endpoint,payload', [
    ('/api/risk/daily-limit', {'amount': 100}),
    ('/api/trading/lock', {'locked': False, 'acknowledge_unlimited': True}),
    ('/api/trading/resume', {}),
])
def test_risk_controls_cannot_move_from_authorized_demo_to_real(client, monkeypatch, endpoint, payload):
    monkeypatch.setattr(main, 'mt5_client', client)
    client.login_id, client.account_type = 2, 'REAL'
    client.mt5.account_info.return_value.login = 2
    client.mt5.account_info.return_value.trade_mode = 2
    client.journal.set_trading_halted(True)
    change_limit = Mock()
    change_mode = Mock()
    change_halt = Mock()
    monkeypatch.setattr(client.journal, 'set_daily_loss_limit', change_limit)
    monkeypatch.setattr(client.journal, 'set_trading_mode', change_mode)
    monkeypatch.setattr(client.journal, 'set_trading_halted', change_halt)
    # The dependency completed on DEMO before the request acquired the order lock.
    main.app.dependency_overrides[main.require_real_unlock] = lambda: [1, 'test', 'DEMO']
    try:
        response = TestClient(main.app).post(endpoint, auth=('test', 'test-panel-password'), json=payload)
    finally:
        main.app.dependency_overrides.pop(main.require_real_unlock)
    assert response.status_code == 409
    change_limit.assert_not_called()
    change_mode.assert_not_called()
    change_halt.assert_not_called()
    assert client.journal.trading_halted()


@pytest.mark.parametrize('endpoint,payload', [
    ('/api/risk/daily-limit', {'amount': 100}),
    ('/api/trading/lock', {'locked': False, 'acknowledge_unlimited': True}),
    ('/api/ai/autopilot', {'enabled': True, 'mode': 'FULL_AUTO'}),
    ('/api/ai/autopilot', {'enabled': False}),
])
def test_settings_reject_the_account_of_an_old_confirmation(client, monkeypatch, endpoint, payload):
    monkeypatch.setattr(main, 'mt5_client', client)
    update = Mock()
    monkeypatch.setattr(main, 'ai_advisor', NS(autopilot={'enabled': False, 'mode': 'ADVISORY'}, update_autopilot=update))
    response = TestClient(main.app).post(endpoint, auth=('test', 'test-panel-password'),
        json={**payload, 'expected_account': [2, 'test', 'DEMO']})
    assert response.status_code == 409
    update.assert_not_called()
    assert client.journal.daily_loss_limit(1, 'test') is None


def test_autopilot_reconnect_cannot_publish_for_a_different_account(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    update = Mock()
    monkeypatch.setattr(main, 'ai_advisor', NS(autopilot={'enabled': False, 'mode': 'ADVISORY'}, update_autopilot=update))

    def reconnect():
        client.login_id, client.account_type = 2, 'REAL'
        client.mt5.account_info.return_value.login = 2
        client.mt5.account_info.return_value.trade_mode = 2
        return True

    monkeypatch.setattr(client, 'ensure_connected', reconnect)
    response = TestClient(main.app).post('/api/ai/autopilot', auth=('test', 'test-panel-password'),
        json={'enabled': True, 'mode': 'FULL_AUTO', 'confirm_real_full_auto': True})
    assert response.status_code == 409
    update.assert_not_called()


def test_autopilot_authorization_and_publication_serialize_account_changes(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    competing_lock = []

    def publish(settings):
        def switch_account():
            acquired = client._lock.acquire(blocking=False)
            competing_lock.append(acquired)
            if acquired:
                client._lock.release()
        worker = threading.Thread(target=switch_account)
        worker.start()
        worker.join(1)
        assert not worker.is_alive()
        return {'success': True}

    monkeypatch.setattr(main, 'ai_advisor', NS(autopilot={'enabled': False, 'mode': 'ADVISORY'}, update_autopilot=publish))
    response = TestClient(main.app).post('/api/ai/autopilot', auth=('test', 'test-panel-password'),
        json={'enabled': True, 'mode': 'FULL_AUTO', 'expected_account': [1, 'test', 'DEMO']})
    assert response.status_code == 200
    assert competing_lock == [False]


def test_partial_autopilot_settings_cannot_reenable_work_during_a_halt(client, monkeypatch):
    monkeypatch.setattr(main, 'mt5_client', client)
    client.journal.set_trading_halted(True)
    update = Mock()
    monkeypatch.setattr(main, 'ai_advisor', NS(autopilot={'enabled': True, 'mode': 'ADVISORY'}, update_autopilot=update))
    response = TestClient(main.app).post('/api/ai/autopilot', auth=('test', 'test-panel-password'),
        json={'min_confidence': 90})
    assert response.status_code == 409
    update.assert_not_called()
