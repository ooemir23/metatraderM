"""The bot trades the selected symbol, also while running.

MT5: changing the chart symbol of an attached EA runs OnDeinit(REASON_CHARTCHANGE) and OnInit on the new
symbol. Positions on the old symbol stay open, and a crossover on a candle that closed before the switch is
not traded (OnInit sets m_lastBarTime to the current bar)."""
import base64
import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.strategy_bot import StrategyBot


def running_bot():
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    bot = StrategyBot(client)
    bot.is_running = True
    bot.last_candle_times = {'EURUSD': 1_700_000_000}
    bot.last_candle_time = 1_700_000_000
    bot.symbol_status = {'EURUSD': {'state': 'WAITING_CROSS'}}
    return bot, client


def test_running_bot_switches_and_reinitialises_like_mt5_chart_change():
    bot, client = running_bot()
    assert bot.switch_symbol_live('XAUUSD') == ['XAUUSD']
    assert bot.symbol == 'XAUUSD' and bot.symbols == ['XAUUSD']
    assert bot.is_running
    # OnInit: no candle marker, so the first check only records the current closed candle (no trade on it).
    assert bot.last_candle_times == {} and bot.last_candle_time == 0
    assert bot.symbol_status == {}
    # Positions on the old symbol are not closed.
    client.close_position.assert_not_called()
    client.open_order.assert_not_called()


def test_first_check_after_switch_does_not_trade_an_already_closed_crossover():
    bot, client = running_bot()
    bot.switch_symbol_live('XAUUSD')
    closes = [100 + i * 0.1 for i in range(200)]
    rates = [{'time': 1_700_000_000 + i * 900, 'close': c} for i, c in enumerate(closes)]
    client.get_rates.return_value = rates
    bot._check_strategy_for_symbol('XAUUSD')
    assert bot.last_candle_times['XAUUSD'] == rates[-2]['time']
    client.open_order.assert_not_called()
    client.close_position.assert_not_called()


def test_switch_waits_for_a_check_in_progress():
    bot, _ = running_bot()
    bot._check_lock.acquire()
    done = []
    worker = threading.Thread(target=lambda: done.append(bot.switch_symbol_live('XAUUSD', timeout=5)))
    worker.start()
    time.sleep(0.2)
    assert not done and bot.symbol == 'EURUSD'   # never mid-order
    bot._check_lock.release()
    worker.join(5)
    assert done == [['XAUUSD']]


def test_switch_refused_when_check_stays_busy_or_bot_stopped():
    bot, _ = running_bot()
    bot._check_lock.acquire()
    with pytest.raises(RuntimeError):
        bot.switch_symbol_live('XAUUSD', timeout=0.05)
    bot._check_lock.release()
    bot.is_running = False
    with pytest.raises(RuntimeError):
        bot.switch_symbol_live('XAUUSD')
    assert bot.symbol == 'EURUSD'


def web():
    from app.main import app
    token = base64.b64encode(b'test:test-panel-password').decode()
    return TestClient(app, headers={'Authorization': 'Basic ' + token})


@pytest.fixture
def live(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    monkeypatch.setattr(main.bot, 'is_running', True)
    monkeypatch.setattr(main.bot, 'symbol', 'EURUSD')
    monkeypatch.setattr(main.bot, 'symbols', ['EURUSD'])
    monkeypatch.setattr(main.bot, 'persist_config', lambda data: None)
    monkeypatch.setattr(main, 'require_real_unlock', lambda request: [7, 'demo', 'DEMO'])
    return main


def test_api_switches_running_bot_after_readiness_check(live):
    ready = Mock(return_value={'success': True, 'account': [7, 'demo', 'DEMO'], 'warnings': []})
    live.mt5_client.bot_readiness = ready
    try:
        response = web().post('/api/bot/add-symbol', json={'symbol': 'XAUUSD'})
        assert response.status_code == 200, response.text
        assert response.json()['symbol'] == 'XAUUSD' and response.json()['is_running'] is True
        # The new symbol passes the same checks as a bot start.
        config = ready.call_args.args[0]
        assert config['symbol'] == 'XAUUSD' and config['symbols'] == ['XAUUSD']
        assert live.bot.symbol == 'XAUUSD'
    finally:
        del live.mt5_client.bot_readiness


def test_api_keeps_old_symbol_when_new_symbol_is_not_ready(live):
    live.mt5_client.bot_readiness = Mock(return_value={'success': False, 'error': 'Yeterli mum yok.'})
    try:
        response = web().post('/api/bot/add-symbol', json={'symbol': 'XAUUSD'})
        assert response.status_code == 409
        assert 'EURUSD' in response.json()['detail']
        assert live.bot.symbol == 'EURUSD' and live.bot.is_running
    finally:
        del live.mt5_client.bot_readiness


def test_api_running_switch_on_real_account_needs_second_factor(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    monkeypatch.setattr(main.bot, 'is_running', True)
    monkeypatch.setattr(main.bot, 'symbol', 'EURUSD')
    monkeypatch.setattr(main.bot, 'symbols', ['EURUSD'])
    switch = Mock()
    monkeypatch.setattr(main.bot, 'switch_symbol_live', switch)
    response = web().post('/api/bot/add-symbol', json={'symbol': 'XAUUSD'})
    assert response.status_code == 403
    switch.assert_not_called()
