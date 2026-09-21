import pytest

@pytest.fixture(autouse=True)
def isolated_state_and_panel_auth(monkeypatch, tmp_path):
    monkeypatch.setenv('TRADING_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('DASHBOARD_USER', 'test')
    monkeypatch.setenv('DASHBOARD_PASSWORD', 'test-panel-password')
    monkeypatch.setenv('MT5_LOGIN', '0')
    monkeypatch.setenv('DAILY_LOSS_LIMIT', '500')
