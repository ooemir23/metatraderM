"""Manually confirmed bot signals keep the panel risk path, account binding and idempotency."""
import base64
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.mt5_bridge import HMA_MAGIC, MANUAL_MAGIC
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
    bot.execute_signal('EURUSD', 'BUY', signal_id='EURUSD-BUY-1', expected_account=ACCOUNT,
                       price=1.1, sl_points=200, tp_points=400)
    args, kwargs = client.open_order.call_args
    assert args[:3] == ('EURUSD', 'BUY', bot.lot_size)
    assert (kwargs['sl_points'], kwargs['tp_points']) == (200, 400)
    # "Şimdi AL/SAT" is a manual one-click order: like a hand-opened MT5 trade the EA does not manage it.
    assert kwargs['magic'] == MANUAL_MAGIC and kwargs['expected_account'] == ACCOUNT
    assert 'deviation_points' not in kwargs   # manual ticket deviation
    assert kwargs['manual_mode'] == 'MT5'  # manually confirmed: broker rules, like the MT5 order ticket


def test_approved_bot_signal_is_the_bots_own_trade_with_ea_deviation():
    bot, client = make_bot()
    client.get_positions.return_value = []
    bot.execute_signal('EURUSD', 'BUY', signal_id='bot-EURUSD-15-BUY-1', expected_account=ACCOUNT,
                       price=1.1, sl_points=200, tp_points=400)
    kwargs = client.open_order.call_args.kwargs
    assert kwargs['magic'] == HMA_MAGIC and kwargs['deviation_points'] == 10   # EA InpDeviation


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


def test_broker_symbol_case_is_preserved():
    bot, client = make_bot()
    bot.add_symbol('BTCUSDm')
    assert bot.symbol == 'BTCUSDm' and bot.symbols == ['BTCUSDm']  # one symbol, like an MT5 EA
    bot.update_config({'symbol': 'EURUSD.a', 'symbols': ['EURUSD.a', 'BTCUSDm']}, persist=False)
    assert bot.symbol == 'EURUSD.a' and bot.symbols == ['EURUSD.a', 'BTCUSDm']
    bot.execute_signal('BTCUSDm', 'BUY', signal_id='s1', expected_account=ACCOUNT, sl_points=200)
    assert client.open_order.call_args.args[0] == 'BTCUSDm'


def protection_bot(position, **spec):
    bot, client = make_bot()
    client.get_symbol_spec.return_value = {'point': .00001, 'digits': 5, **spec}
    client.get_positions.return_value = [position]
    client.manage_position.return_value = {'success': True}
    return bot, client


def test_breakeven_uses_bridge_position_fields():
    pos = {'ticket': 9, 'symbol': 'EURUSD', 'magic': HMA_MAGIC, 'type': 'BUY',
           'price_open': 1.10000, 'price_current': 1.10250, 'sl': 1.09800, 'tp': 0.0}
    bot, client = protection_bot(pos)
    bot.use_breakeven = True
    bot._apply_position_protection(expected_account=ACCOUNT)
    args, kwargs = client.manage_position.call_args
    assert args[0] == 9 and args[1] == 'stops'
    assert args[2]['sl'] == 1.10002  # entry + 2 points once profit >= 1R (200 points)
    assert kwargs['expected_account'] == ACCOUNT


def test_breakeven_waits_for_one_r_and_trailing_is_idempotent():
    pos = {'ticket': 9, 'symbol': 'EURUSD', 'magic': HMA_MAGIC, 'type': 'SELL',
           'price_open': 1.10000, 'price_current': 1.09900, 'sl': 1.10200, 'tp': 0.0}
    bot, client = protection_bot(pos)
    bot.use_breakeven = True
    bot._apply_position_protection()
    client.manage_position.assert_not_called()  # 100 points profit < 200 points risk
    bot.use_breakeven, bot.trailing_stop_points = False, 50
    bot._apply_position_protection()
    bot._apply_position_protection()
    first, second = client.manage_position.call_args_list
    assert first.args[2]['sl'] == 1.0995 and first.args[3] == second.args[3]


def test_protection_ignores_foreign_positions_and_logs_failures():
    pos = {'ticket': 9, 'symbol': 'EURUSD', 'magic': 1, 'type': 'BUY',
           'price_open': 1.1, 'price_current': 1.2, 'sl': 0, 'tp': 0}
    bot, client = protection_bot(pos)
    bot.trailing_stop_points = 50
    bot._apply_position_protection()
    client.manage_position.assert_not_called()
    client.get_positions.side_effect = RuntimeError('offline')
    bot._apply_position_protection()
    assert any('okunamadı' in entry['message'] for entry in bot.logs)


# --- Exposure caps: manual trades must not block automation; confirmed signals follow MT5 rules ---
import time
from types import SimpleNamespace as NS
from app.mt5_client import MT5Client
from app.mt5_bridge import MANUAL_MAGIC


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
    c = MT5Client()
    c.login_id, c.server, c.account_type = 1, 'test', 'DEMO'
    c.is_connected = True
    c.last_ping_time = time.time()
    c.mt5 = Mock()
    c.mt5.terminal_info.return_value = NS(connected=True)
    c.mt5.account_info.return_value = NS(login=1, server='test', trade_mode=0, currency='EUR', margin_mode=2,
        equity=10000., margin_free=8000.)
    # Six manual 5-lot positions: far above the 0.50 total-lot / 10 order panel caps once automation adds.
    c.mt5.positions_get.return_value = [NS(ticket=i, identifier=i, symbol='XAUUSD', type=1, volume=5., magic=MANUAL_MAGIC,
        price_open=4156., price_current=4156., sl=0., tp=0., profit=0., swap=0., time=1, time_msc=1000, comment='')
        for i in range(10, 20)]
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


def test_bot_caps_count_the_whole_account_like_ea_canopentrade(broker):
    # HMA_Crossover_EA CanOpenTrade: committed lots and open count include every position/order.
    result = broker.open_order('EURUSD', 'BUY', .01, sl_points=200, magic=HMA_MAGIC, request_id='bot-cap-scope-1')
    assert not result['success'] and 'Toplam' in result['error']


def test_manual_panel_caps_still_count_whole_account(broker):
    result = broker.open_order('EURUSD', 'BUY', .01, sl_points=200, request_id='manual-panel-cap-1', manual_mode='PANEL')
    assert not result['success'] and 'Toplam' in result['error']


def test_confirmed_signal_uses_mt5_rules_without_panel_caps(broker):
    result = broker.open_order('EURUSD', 'BUY', .5, sl_points=200, magic=HMA_MAGIC, request_id='sig-mt5-rules-1', manual_mode='MT5')
    assert 'üst sınır' not in str(result.get('error')) and 'Toplam' not in str(result.get('error'))


def test_bot_own_positions_still_capped(broker):
    own = [NS(ticket=i, identifier=i, symbol=f'S{i}', type=0, volume=.05, magic=HMA_MAGIC, price_open=1., price_current=1.,
              sl=0., tp=0., profit=0., swap=0., time=1, time_msc=1000, comment='') for i in range(30, 40)]
    broker.mt5.positions_get.return_value = broker.mt5.positions_get.return_value + own
    result = broker.open_order('EURUSD', 'BUY', .01, sl_points=200, magic=HMA_MAGIC, request_id='bot-cap-own-1')
    assert not result['success'] and 'Toplam' in result['error']


# --- History of signals sent to the bot ---
def test_sent_signals_are_recorded_persisted_and_cleared(tmp_path):
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    client.has_unresolved_bot_order.return_value = False
    client.get_symbol_spec.return_value = {'point': .00001}
    client.open_order.side_effect = [{'success': True, 'ticket': 42, 'price': 1.1},
                                     {'success': False, 'error': 'Piyasa kapalı'}]
    bot = StrategyBot(client, config_path=tmp_path / 'bot_settings.json')
    bot.execute_signal('EURUSD', 'BUY', signal_id='s1', expected_account=ACCOUNT, sl_points=200)
    bot.execute_signal('XAUUSD', 'SELL', signal_id='s2', expected_account=ACCOUNT, sl_points=300)
    newest, oldest = bot.get_status()['signal_history'][:2]
    assert (newest['symbol'], newest['success'], newest['error']) == ('XAUUSD', False, 'Piyasa kapalı')
    assert (oldest['ticket'], oldest['source'], oldest['sl_points']) == (42, 'MANUAL', 200)
    reloaded = StrategyBot(client, config_path=tmp_path / 'bot_settings.json')
    assert [r['symbol'] for r in reloaded.signal_history] == ['XAUUSD', 'EURUSD'], 'survives restart'
    reloaded.clear_history()
    assert StrategyBot(client, config_path=tmp_path / 'bot_settings.json').signal_history == []


def test_history_is_capped_and_survives_corrupt_file(tmp_path):
    (tmp_path / 'bot_signal_history.json').write_text('{not json')
    bot = StrategyBot(Mock(), config_path=tmp_path / 'bot_settings.json')
    assert bot.signal_history == []
    for i in range(StrategyBot.HISTORY_LIMIT + 5):
        bot.record_signal(source='AUTO', symbol=f'S{i}', side='BUY', lot=.01, result={'success': True, 'ticket': i})
    assert len(bot.signal_history) == StrategyBot.HISTORY_LIMIT and bot.signal_history[0]['symbol'] == f'S{StrategyBot.HISTORY_LIMIT + 4}'
    assert len(bot.get_status()['signal_history']) == 50


def test_single_history_row_can_be_deleted():
    bot, client = make_bot()
    bot.record_signal(source='MANUAL', symbol='EURUSD', side='BUY', lot=1, result={'success': False, 'error': 'x'})
    bot.record_signal(source='AUTO', symbol='EURUSD', side='SELL', lot=1, result={'success': True, 'ticket': 7})
    first, second = bot.signal_history
    assert first['id'] and first['id'] != second['id']
    assert bot.delete_history_entry(second['id']) is True
    assert [r['id'] for r in bot.signal_history] == [first['id']]
    assert bot.delete_history_entry('missing') is False


def test_auto_history_records_entry_mode_and_strategy(tmp_path):
    bot = StrategyBot(Mock(), config_path=tmp_path / 'bot_settings.json')
    bot.entry_mode = 'MEVO'
    bot.record_signal(source='AUTO', symbol='EURUSD', side='BUY', lot=.01, result={'success': True, 'ticket': 7})
    bot.record_signal(source='MANUAL', symbol='EURUSD', side='BUY', lot=.01, result={'success': True, 'ticket': 8})
    manual, auto = bot.signal_history
    assert auto['entry_mode'] == 'MEVO' and auto['strategy'].startswith(bot.fast_ma_type)
    assert manual['entry_mode'] is None and manual['strategy'] is None
