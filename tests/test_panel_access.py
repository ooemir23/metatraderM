import base64
import json
import pytest
from fastapi.testclient import TestClient

from app import security_sessions
from test_trade_safety import client


def test_second_panel_user_has_own_login_and_totp(client, monkeypatch):
    monkeypatch.setenv('DASHBOARD_USER_2', 'colleague')
    monkeypatch.setenv('DASHBOARD_PASSWORD_2', 'separate-password')
    from app.main import app
    first = base64.b64encode(b'test:test-panel-password').decode()
    second = base64.b64encode(b'colleague:separate-password').decode()
    wrong = base64.b64encode(b'colleague:test-panel-password').decode()
    assert TestClient(app, headers={'Authorization': 'Basic ' + first}).get('/api/security/status').status_code == 200
    assert TestClient(app, headers={'Authorization': 'Basic ' + second}).get('/api/security/status').status_code == 200
    assert TestClient(app, headers={'Authorization': 'Basic ' + wrong}).get('/api/security/status').status_code == 401
    security_sessions.initialize()
    assert security_sessions._secret('test') != security_sessions._secret('colleague')


def test_operator_roles_are_enforced_on_server(client, monkeypatch):
    monkeypatch.setenv('DASHBOARD_USER_2', 'colleague')
    monkeypatch.setenv('DASHBOARD_PASSWORD_2', 'separate-password')
    from app.main import app
    token = base64.b64encode(b'colleague:separate-password').decode()
    web = TestClient(app, headers={'Authorization': 'Basic ' + token})
    assert web.get('/api/auth/me').json() == {'username': 'colleague', 'role': 'TRADER'}
    assert web.post('/api/trading/resume', json={}).status_code == 403
    assert web.post('/api/ai/autopilot', json={'enabled': False}).status_code == 403
    assert web.post('/api/order/open', json={}).status_code == 422
    monkeypatch.setenv('DASHBOARD_ROLE_2', 'VIEWER')
    assert web.get('/api/auth/me').json()['role'] == 'VIEWER'
    assert web.post('/api/order/open', json={}).status_code == 403
    assert web.post('/api/backtest', json={}).status_code == 422


