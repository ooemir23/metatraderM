import base64

import pytest
from fastapi.testclient import TestClient

from app.mt5_client import MT5DataError
from app.order_journal import OrderJournal
from test_trade_safety import client


def test_daily_limit_is_persisted_per_broker_account(tmp_path):
    path = tmp_path / 'orders.db'
    journal = OrderJournal(path)
    journal.set_daily_loss_limit(101, 'Demo', 1200)
    restarted = OrderJournal(path)
    assert restarted.daily_loss_limit(101, 'Demo') == 1200
    assert restarted.daily_loss_limit(102, 'Demo') is None
    assert restarted.daily_loss_limit(101, 'Live') is None


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
