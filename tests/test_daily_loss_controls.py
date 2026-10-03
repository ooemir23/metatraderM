import base64
import threading

import pytest
from fastapi.testclient import TestClient

from app.mt5_client import MT5DataError
from app.order_journal import OrderJournal
from test_trade_safety import client, deal
from app.mt5_bridge import AI_MAGIC, MANUAL_MAGIC


def test_daily_limit_is_persisted_per_broker_account(tmp_path):
    path = tmp_path / 'orders.db'
    journal = OrderJournal(path)
    journal.set_daily_loss_limit(101, 'Demo', 1200)
    restarted = OrderJournal(path)
    assert restarted.daily_loss_limit(101, 'Demo') == 1200
    assert restarted.daily_loss_limit(102, 'Demo') is None
    assert restarted.daily_loss_limit(101, 'Live') is None
    assert restarted.daily_limit_enabled(101, 'Demo')
    journal.set_trading_mode(101, 'Demo', False)
    assert not OrderJournal(path).daily_limit_enabled(101, 'Demo')
    assert OrderJournal(path).daily_limit_enabled(102, 'Demo')
    journal.set_trading_mode(101, 'Demo', True)
    assert OrderJournal(path).daily_limit_enabled(101, 'Demo')


def test_free_trading_bypasses_only_daily_loss_guard(client, tmp_path):
    client.journal = OrderJournal(tmp_path / 'orders.db')
    client.mt5.history_deals_get.return_value = [deal(profit=-600)]
    blocked = client.open_order('EURUSD', 'BUY', .01, request_id='daily-guard-on')
    assert not blocked['success'] and 'Günlük zarar sınırı' in blocked['error']
    client.mt5.order_send.assert_not_called()
    client.journal.set_trading_mode(1, 'test', False)
    assert client.get_risk_status()['daily_limit_enabled'] is False
    allowed = client.open_order('EURUSD', 'BUY', .01, request_id='daily-guard-off')
    assert allowed['success']
    client.mt5.order_send.assert_called_once()
    client.journal.set_trading_mode(1, 'test', True)
    locked = client.open_order('EURUSD', 'BUY', .01, request_id='daily-guard-locked')
    assert locked['reason_code'] == 'trading_halted'
    client.mt5.order_send.assert_called_once()


def test_effective_limit_reaches_manual_order_and_risk_status(client, tmp_path):
    client.journal = OrderJournal(tmp_path / 'orders.db')
    client.journal.set_daily_loss_limit(1, 'test', 1200)
    assert client.get_risk_status()['daily_loss_limit'] == 1200
    sent_limits = []
    original_bridge = client._bridge

    def bridge(operation, *args):
        if operation == 'open_deal':
            sent_limits.append(args[7])
        return original_bridge(operation, *args)

    client._bridge = bridge
    client.open_order('EURUSD', 'BUY', .01, request_id='limit-override-test')
    assert sent_limits == [1200]


def test_ai_limit_uses_lower_of_ai_and_account_limits(client, tmp_path):
    client.journal = OrderJournal(tmp_path / 'orders.db')
    client.journal.set_daily_loss_limit(1, 'test', 500)
    client.ai_daily_loss_limit = 100
    client.mt5.history_deals_get.return_value = [deal(profit=-150)]
    ai = client.open_order('EURUSD', 'BUY', .01, sl_points=200, tp_points=400,
                           magic=AI_MAGIC, request_id='ai-lower-limit-test')
    assert not ai['success'] and ai['daily_loss_limit'] == 100
    assert client.open_order('EURUSD', 'BUY', .01, magic=MANUAL_MAGIC,
                             request_id='manual-account-limit-test')['success']
    client.mt5.order_send.assert_called_once()
    client.ai_daily_loss_limit = 1000
    assert client.effective_daily_loss_limit(1, 'test', AI_MAGIC) == 500


def test_raising_exceeded_limit_requires_explicit_acknowledgement(client, tmp_path):
    client.journal = OrderJournal(tmp_path / 'orders.db')
    original_bridge = client._bridge

    def bridge(operation, *args):
        if operation == 'risk_status':
            return {'daily_loss':600, 'daily_loss_limit':args[2], 'currency':'EUR',
                    'login':1, 'server':'test'}
        return original_bridge(operation, *args)

    client._bridge = bridge
    with pytest.raises(MT5DataError, match='açık risk onayı'):
        client.set_daily_loss_limit(1000)
    assert client.journal.daily_loss_limit(1, 'test') is None
    assert client.set_daily_loss_limit(1000, acknowledge_risk=True)['daily_loss_limit'] == 1000


def test_limit_change_is_admin_only_and_validated(monkeypatch):
    from app import main
    monkeypatch.setenv('DASHBOARD_USER_2', 'trader')
    monkeypatch.setenv('DASHBOARD_PASSWORD_2', 'trader-password')
    monkeypatch.setattr(main.mt5_client, 'set_daily_loss_limit', lambda amount, acknowledge_risk=False: {
        'daily_loss_limit':amount, 'daily_loss':0, 'currency':'USD'})
    admin = base64.b64encode(b'test:test-panel-password').decode()
    trader = base64.b64encode(b'trader:trader-password').decode()
    admin_web = TestClient(main.app, headers={'Authorization':'Basic ' + admin})
    trader_web = TestClient(main.app, headers={'Authorization':'Basic ' + trader})
    payload = {'amount':750, 'acknowledge_risk':False}
    assert trader_web.post('/api/risk/daily-limit', json=payload).status_code == 403
    assert admin_web.post('/api/risk/daily-limit', json={'amount':0}).status_code == 422
    assert admin_web.post('/api/risk/daily-limit', json=payload).json()['daily_loss_limit'] == 750


def test_admin_can_toggle_trading_lock_with_explicit_bypass_ack(monkeypatch):
    from app import main
    monkeypatch.setenv('DASHBOARD_USER_2', 'trader')
    monkeypatch.setenv('DASHBOARD_PASSWORD_2', 'trader-password')
    monkeypatch.setattr(main.mt5_client, 'ensure_connected', lambda: True)
    monkeypatch.setattr(main.mt5_client, '_selected_account', lambda: [1, 'test'])
    monkeypatch.setattr(main.mt5_client, 'get_risk_status', lambda: {'daily_loss':600, 'daily_loss_limit':1})
    monkeypatch.setattr(main.mt5_client, 'automation_stopped', threading.Event())
    monkeypatch.setattr(main.bot, 'stop', lambda: None)
    monkeypatch.setattr(main.ai_advisor, 'update_autopilot', lambda settings: None)
    main.mt5_client.login_id, main.mt5_client.server = 1, 'test'
    admin = base64.b64encode(b'test:test-panel-password').decode()
    trader = base64.b64encode(b'trader:trader-password').decode()
    admin_web = TestClient(main.app, headers={'Authorization':'Basic ' + admin})
    trader_web = TestClient(main.app, headers={'Authorization':'Basic ' + trader})
    assert trader_web.post('/api/trading/lock', json={'locked':False,'acknowledge_unlimited':True}).status_code == 403
    assert admin_web.post('/api/trading/lock', json={'locked':False}).status_code == 409
    main.flatten_active.set()
    try:
        assert admin_web.post('/api/trading/lock', json={'locked':False,'acknowledge_unlimited':True}).status_code == 409
    finally:
        main.flatten_active.clear()
    assert admin_web.post('/api/trading/lock', json={'locked':False,'acknowledge_unlimited':True}).status_code == 200
    assert admin_web.get('/api/trading/status').json()['daily_limit_enabled'] is False
    assert admin_web.post('/api/trading/lock', json={'locked':True}).status_code == 200
    assert admin_web.get('/api/trading/status').json()['new_orders_halted'] is True
    assert admin_web.get('/api/trading/status').json()['daily_limit_enabled'] is True


def test_real_account_lock_change_requires_second_factor(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization':'Basic ' + token})
    assert web.post('/api/trading/lock', json={'locked':False,'acknowledge_unlimited':True}).status_code == 403
