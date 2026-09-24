import base64
from fastapi.testclient import TestClient

from app import ai_advisor, security_sessions
from app.mt5_bridge import AI_MAGIC
from app.ai_performance import summarize
from test_trade_safety import client
from test_ai_costs import setup


def test_ai_order_requires_broker_verified_stop_and_risk(client):
    client.mt5.account_info.return_value.equity = 1000
    client.mt5.order_calc_profit.return_value = -5
    assert not client.open_order('EURUSD', 'BUY', .01, 0, 200, magic=AI_MAGIC,
                                 request_id='ai-missing-stop')['success']
    assert not client.open_order('EURUSD', 'BUY', .01, 200, 0, magic=AI_MAGIC,
                                 request_id='ai-missing-target')['success']
    client.mt5.order_calc_profit.return_value = -20
    assert not client.open_order('EURUSD', 'BUY', .01, 200, 400, magic=AI_MAGIC,
                                 request_id='ai-risk-over-limit')['success']
    client.mt5.order_calc_profit.return_value = None
    assert not client.open_order('EURUSD', 'BUY', .01, 200, 400, magic=AI_MAGIC,
                                 request_id='ai-risk-unknown')['success']
    client.mt5.order_send.assert_not_called()
    client.mt5.order_calc_profit.return_value = -5
    assert client.open_order('EURUSD', 'BUY', .01, 200, 400, magic=AI_MAGIC,
                             request_id='ai-risk-valid')['success']


def test_ai_profile_and_recommendations_stay_with_broker_account(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_advisor, 'MEMORY_PATHS', [str(tmp_path / 'ai.json')])
    advisor = ai_advisor.DeepSeekAdvisor(client, api_key='test')
    advisor.memory['persona']['style'] = 'first'
    advisor.cost_state['advice_cache']['first'] = {'recommendation': {'id': 'first'}}
    advisor.save_memory()
    client.login_id = 2
    advisor.sync_account_scope()
    assert advisor.memory['persona']['style'] != 'first'
    assert not advisor.cost_state['advice_cache']
    advisor.memory['persona']['style'] = 'second'
    client.login_id = 1
    advisor.sync_account_scope()
    assert advisor.memory['persona']['style'] == 'first'
    assert 'first' in advisor.cost_state['advice_cache']
    assert ai_advisor.DeepSeekAdvisor(client, api_key='test').memory['persona']['style'] == 'first'


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


def test_usage_breakdown_and_autopilot_budget(setup):
    advisor, post, response, clock = setup
    from test_ai_costs import advise
    assert advise(advisor)['success']
    usage = advisor.get_status()['usage_by_operation']['advice:manual:EURUSD:M15']
    assert usage['calls'] == 1 and usage['total_tokens'] == 130
    advisor.daily_call_limit = 1
    assert not advisor.autopilot_budget_available()


def test_ai_outcomes_use_only_closed_ai_positions(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, '_selected_account', lambda: (1, 'test'))
    monkeypatch.setattr(main.mt5_client, 'get_account_info', lambda: {'currency': 'USD'})
    monkeypatch.setattr(main.mt5_client, 'get_positions', lambda fresh=False: [])
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    monkeypatch.setattr(main.mt5_client, 'get_history', lambda days: [
        {'magic': AI_MAGIC, 'position_id': 7, 'entry': 0, 'profit': 0, 'commission': -1},
        {'magic': 0, 'position_id': 7, 'entry': 1, 'profit': 6, 'commission': -1},
        {'magic': AI_MAGIC, 'position_id': 8, 'entry': 0, 'profit': 0},
        {'magic': 123460, 'position_id': 9, 'entry': 1, 'profit': 100},
    ])
    token = base64.b64encode(b'test:test-panel-password').decode()
    response = TestClient(main.app, headers={'Authorization': 'Basic ' + token}).get('/api/ai/performance')
    assert response.status_code == 200
    assert response.json()['closed_positions'] == 1
    assert response.json()['net'] == 4


def test_ai_forward_outcomes_include_costs_slippage_and_confidence():
    deals = [
        {'magic': AI_MAGIC, 'position_id': 1, 'order': 10, 'entry': 0, 'symbol': 'EURUSD', 'commission': -1},
        {'magic': 0, 'position_id': 1, 'order': 11, 'entry': 1, 'symbol': 'EURUSD', 'profit': 9,
         'commission': -1, 'timestamp': 100},
        {'magic': AI_MAGIC, 'position_id': 2, 'order': 20, 'entry': 0, 'symbol': 'GBPUSD', 'commission': -1},
        {'magic': AI_MAGIC, 'position_id': 2, 'order': 21, 'entry': 1, 'symbol': 'GBPUSD', 'profit': -4,
         'commission': -1, 'timestamp': 200},
        {'magic': AI_MAGIC, 'position_id': 3, 'order': 30, 'entry': 0, 'symbol': 'EURUSD'},
    ]
    records = [{'order_ticket': 10, 'confidence': 85, 'expected_price': 1.0, 'fill_price': 1.001,
                'action': 'BUY'},
               {'order_ticket': 20, 'confidence': 65, 'expected_price': 1.0, 'fill_price': 0.999,
                'action': 'BUY'}]
    result = summarize(deals, records, open_position_ids=[3])
    assert result['closed_positions'] == 2
    assert result['net'] == 1 and result['costs'] == -4
    assert result['max_drawdown'] == 6
    assert result['confidence_buckets']['80-100']['win_rate_pct'] == 100
    assert result['confidence_buckets']['60-79']['win_rate_pct'] == 0
    assert result['mean_adverse_slippage_pct'] == 0
