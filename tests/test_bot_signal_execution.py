"""Manually confirmed bot signals keep the panel risk path, account binding and idempotency."""
import base64
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot

ACCOUNT = [7, 'demo', 'DEMO']


def make_bot():
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    client.has_unresolved_bot_order.return_value = False
    client.get_symbol_spec.return_value = {'point': .00001}
    client.open_order.return_value = {'success': True, 'ticket': 42, 'price': 1.1}
    return StrategyBot(client), client


def test_signal_points_are_not_treated_as_prices_and_use_panel_risk_mode():
    bot, client = make_bot()
    bot.execute_signal('eurusd', 'BUY', signal_id='EURUSD-BUY-1', expected_account=ACCOUNT,
                       price=1.1, sl_points=200, tp_points=400)
    args, kwargs = client.open_order.call_args
    assert args[:3] == ('EURUSD', 'BUY', bot.lot_size)
    assert (kwargs['sl_points'], kwargs['tp_points']) == (200, 400)
    assert kwargs['magic'] == HMA_MAGIC and kwargs['expected_account'] == ACCOUNT
    assert 'manual_mode' not in kwargs  # defaults to PANEL: daily loss, lot and stop-risk limits apply


def test_signal_prices_are_converted_and_wrong_side_stops_rejected():
    bot, client = make_bot()
    bot.execute_signal('EURUSD', 'SELL', signal_id='scan-1', expected_account=ACCOUNT,
                       price=1.1, sl_price=1.102, tp_price=1.097)
    assert client.open_order.call_args.kwargs['sl_points'] == 200
    assert client.open_order.call_args.kwargs['tp_points'] == 300
    with pytest.raises(ValueError):
        bot.execute_signal('EURUSD', 'SELL', signal_id='scan-2', expected_account=ACCOUNT,
                           price=1.1, sl_price=1.098)


def test_same_signal_reuses_request_id_and_account_changes_it():
    bot, client = make_bot()
    for _ in range(2):
        bot.execute_signal('EURUSD', 'BUY', signal_id='EURUSD-BUY-1', expected_account=ACCOUNT)
    first, second = (c.kwargs['request_id'] for c in client.open_order.call_args_list)
    assert first == second
    bot.execute_signal('EURUSD', 'BUY', signal_id='EURUSD-BUY-1', expected_account=[8, 'demo', 'DEMO'])
    assert client.open_order.call_args.kwargs['request_id'] != first


def test_signal_requires_stop_when_bot_stop_loss_is_enabled():
    bot, client = make_bot()
    with pytest.raises(ValueError):
        bot.execute_signal('EURUSD', 'BUY', signal_id='x', expected_account=ACCOUNT, sl_points=0)
    client.open_order.assert_not_called()


def test_symbol_list_cannot_change_while_bot_runs():
    bot, _ = make_bot()
    bot.is_running = True
    with pytest.raises(RuntimeError):
        bot.add_symbol('GBPUSD')
    with pytest.raises(RuntimeError):
        bot.remove_symbol('EURUSD')
    assert bot.symbols == ['EURUSD']


def web():
    from app.main import app
    token = base64.b64encode(b'test:test-panel-password').decode()
    return TestClient(app, headers={'Authorization': 'Basic ' + token})


def test_real_account_signal_execution_requires_second_factor(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    execute = Mock(return_value={'success': True, 'ticket': 5})
    monkeypatch.setattr(main.bot, 'execute_signal', execute)
    response = web().post('/api/bot/execute-signal', json={'symbol': 'EURUSD', 'side': 'BUY', 'signal_id': 'EURUSD-BUY-1'})
    assert response.status_code == 403
    execute.assert_not_called()


def test_signal_execution_rejects_unknown_fields_and_bad_rules(monkeypatch):
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    execute = Mock(return_value={'success': True, 'ticket': 5})
    monkeypatch.setattr(main.bot, 'execute_signal', execute)
    client = web()
    assert client.post('/api/bot/execute-signal', json={'symbol': 'EURUSD', 'side': 'BUY', 'signal_id': 's',
                                                         'lot_size': 50}).status_code == 422
    assert client.post('/api/bot/execute-signal', json={'symbol': 'EURUSD', 'side': 'BUY'}).status_code == 422
    execute.assert_not_called()
    assert client.post('/api/bot/signal-rules', json={'rsi_buy_max': 'x'}).status_code == 422
    assert client.post('/api/bot/signal-rules', json={'execution_mode': 'YOLO'}).status_code == 422
