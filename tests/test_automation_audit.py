"""Regression cases for automation races and broker-result ambiguity; no live services."""
import json
import threading
import time
from unittest.mock import Mock

import pytest

from app import ai_advisor
from app.mt5_bridge import HMA_MAGIC
from app.order_journal import OrderJournal
from app.strategy_bot import StrategyBot


@pytest.fixture
def advisor(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_advisor, 'MEMORY_PATHS', [str(tmp_path / 'ai.json')])
    monkeypatch.setattr(ai_advisor.requests, 'post', Mock(side_effect=AssertionError('Live provider forbidden')))
    mt5 = Mock(login_id=7, server='demo', account_type='DEMO')
    mt5.get_symbol_price.return_value = {'ask': 1.1, 'bid': 1.0999}
    mt5.open_order.return_value = {'success': True, 'ticket': 42, 'price': 1.1}
    return ai_advisor.DeepSeekAdvisor(mt5, api_key='stub')


def install_recommendation(advisor, **fields):
    rec = {'id': 'trusted', 'symbol': 'EURUSD', 'action': 'BUY', 'confidence': 90,
           'sl_points': 200, 'tp_points': 400, 'suggested_lot': .01,
           'expires_at': time.time() + 900, 'account': [7, 'demo', 'DEMO'], **fields}
    advisor.cost_state['advice_cache']['trusted'] = {'recommendation': rec, 'expires_at': rec['expires_at']}
    advisor.memory['latest_recommendation'] = rec
    return rec


def advise(advisor, **fields):
    return advisor.get_market_advice('EURUSD', rates=[{'time': 100, 'close': 1}, {'time': 1000, 'close': 1}],
                                     tick={'bid': 1, 'ask': 1.1}, **fields)


def bot_client():
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    client.ensure_connected.return_value = True
    client.get_rates.return_value = [{'time': i * 900, 'close': 1.0} for i in range(100)]
    client.get_positions.return_value = []
    client.get_pending_orders.return_value = []
    client.has_unresolved_bot_order.return_value = False
    client.open_order.return_value = {'success': True, 'ticket': 42}
    bot = StrategyBot(client)
    bot.calc_hma = Mock(side_effect=[2., 0.])
    bot.calc_second_ma = Mock(return_value=1.)
    return bot, client


def test_restart_restores_preferences_without_reenabling_autopilot(advisor):
    assert advisor.update_autopilot({'enabled': True, 'mode': 'FULL_AUTO', 'max_lot': .02})['success']
    restarted = ai_advisor.DeepSeekAdvisor(advisor.mt5, api_key='stub')
    assert not restarted.autopilot['enabled']
    assert restarted.autopilot['mode'] == 'FULL_AUTO'
    assert restarted.autopilot['max_lot'] == .02
    assert restarted._auto_stop.is_set()


@pytest.mark.parametrize('operation', ['advice', 'learn'])
def test_analysis_response_cannot_cross_broker_accounts(advisor, monkeypatch, operation):
    def response(*args):
        advisor.mt5.login_id = 8
        return {'action': 'HOLD', 'confidence': 30} if operation == 'advice' else {'persona': {'style': 'other'}, 'learned_rules': []}
    monkeypatch.setattr(advisor, '_request_json', response)
    result = advise(advisor) if operation == 'advice' else advisor.analyze_user_trades([{'ticket': 1, 'profit': 2}])
    assert not result['success'] and 'hesabı değişti' in result['error']
    assert not advisor.cost_state['advice_cache']
    assert advisor.memory['last_analyzed'] is None
    assert advisor.memory['latest_recommendation'] is None


@pytest.mark.parametrize('operation', ['advice', 'learn'])
def test_data_snapshot_account_is_checked_before_paid_analysis(advisor, monkeypatch, operation):
    request = Mock()
    monkeypatch.setattr(advisor, '_request_json', request)
    kwargs = {'expected_account': [8, 'demo', 'DEMO']}
    result = advise(advisor, **kwargs) if operation == 'advice' else advisor.analyze_user_trades([{'ticket': 1}], **kwargs)
    assert not result['success']
    request.assert_not_called()


def test_automatic_execution_rechecks_confidence(advisor):
    rec = install_recommendation(advisor, confidence=50)
    assert advisor.update_autopilot({'enabled': True, 'mode': 'FULL_AUTO'})['success']
    result = advisor.execute_recommendation(rec, automatic=True, generation=advisor.autopilot_generation)
    assert not result['success'] and 'güven' in result['error']
    advisor.mt5.open_order.assert_not_called()


@pytest.mark.parametrize('lot', [0, -1, float('nan'), float('inf'), True])
def test_invalid_explicit_lot_is_rejected_instead_of_replaced(advisor, lot):
    rec = install_recommendation(advisor)
    assert not advisor.execute_recommendation({**rec, 'suggested_lot': lot})['success']
    advisor.mt5.open_order.assert_not_called()


@pytest.mark.parametrize('flag', ['pending', 'partial', 'uncertain'])
def test_unsettled_ai_execution_stops_further_automatic_orders(advisor, flag):
    rec = install_recommendation(advisor)
    advisor.update_autopilot({'enabled': True, 'mode': 'FULL_AUTO'})
    generation, event = advisor.autopilot_generation, advisor._auto_stop
    advisor.mt5.open_order.return_value = {'success': True, flag: True, 'ticket': 42}
    result = advisor.execute_recommendation(rec, automatic=True, generation=generation)
    assert result[flag] and not advisor.autopilot['enabled'] and event.is_set()
    assert not advisor.execute_recommendation(rec, automatic=True, generation=generation)['success']
    advisor.mt5.open_order.assert_called_once()


def test_ai_order_forwards_authorized_account_and_rejects_mismatch(advisor):
    rec = install_recommendation(advisor)
    assert not advisor.execute_recommendation(rec, expected_account=[8, 'demo', 'DEMO'])['success']
    advisor.mt5.open_order.assert_not_called()
    assert advisor.execute_recommendation(rec, expected_account=[7, 'demo', 'DEMO'])['success']
    assert advisor.mt5.open_order.call_args.kwargs['expected_account'] == [7, 'demo', 'DEMO']


def test_fill_record_remains_with_original_account_when_selection_changes(advisor):
    rec = install_recommendation(advisor)
    original_scope = advisor.account_scope
    def send(*args, **kwargs):
        advisor.mt5.login_id = 8
        return {'success': True, 'ticket': 42, 'price': 1.1}
    advisor.mt5.open_order.side_effect = send
    assert advisor.execute_recommendation(rec)['success']
    assert not advisor.execution_records
    assert advisor.account_states[original_scope]['execution_records'][0]['order_ticket'] == 42
    advisor.mt5.login_id = 7
    advisor.sync_account_scope()
    assert advisor.execution_records[0]['order_ticket'] == 42


@pytest.mark.parametrize('config', [{'max_lot': float('nan')}, {'daily_loss_limit': -1}, {'mode': 'WRONG'},
                                  {'enabled': 'false'}, {'min_confidence': 101}, {'allowed_symbols': ['']}])
def test_autopilot_configuration_is_atomic_on_validation_error(advisor, config):
    before = dict(advisor.autopilot)
    generation, event = advisor.autopilot_generation, advisor._auto_stop
    assert not advisor.update_autopilot(config)['success']
    assert advisor.autopilot == before and advisor.autopilot_generation == generation
    assert advisor._auto_stop is event


def test_unpersisted_recommendation_is_not_executable(advisor, monkeypatch):
    monkeypatch.setattr(advisor, '_request_json', lambda *args: {'action': 'BUY', 'confidence': 90, 'sl_points': 200, 'tp_points': 400, 'suggested_lot': .01})
    monkeypatch.setattr(advisor, 'save_memory', lambda: False)
    assert not advise(advisor)['success']
    assert not advisor.cost_state['advice_cache'] and advisor.memory['latest_recommendation'] is None
    assert not advisor.execute_recommendation()['success']
    advisor.mt5.open_order.assert_not_called()


def test_failed_autopilot_persistence_preserves_risk_settings_and_stops(advisor, monkeypatch):
    advisor.update_autopilot({'enabled': True, 'mode': 'FULL_AUTO'})
    before, old_event = dict(advisor.autopilot), advisor._auto_stop
    monkeypatch.setattr(advisor, 'save_memory', lambda: False)
    assert not advisor.update_autopilot({'max_lot': .04, 'daily_loss_limit': 20})['success']
    assert advisor.autopilot == {**before, 'enabled': False}
    assert advisor.mt5.ai_daily_loss_limit == before['daily_loss_limit']
    assert old_event.is_set()


def test_failed_learning_persistence_cannot_be_returned_as_cached_success(advisor, monkeypatch):
    request = Mock(return_value={'persona': {'style': 'new'}, 'learned_rules': []})
    monkeypatch.setattr(advisor, '_request_json', request)
    monkeypatch.setattr(advisor, 'save_memory', lambda: False)
    trades = [{'ticket': 1, 'profit': 2}]
    assert not advisor.analyze_user_trades(trades)['success']
    assert not advisor.analyze_user_trades(trades)['success']
    assert advisor.memory['last_analyzed'] is None and advisor.cost_state['learn_digest'] is None


@pytest.mark.parametrize('flag', ['partial', 'pending', 'uncertain'])
def test_unsettled_reverse_close_blocks_new_bot_entry_even_if_success(flag):
    bot, client = bot_client()
    client.get_positions.return_value = [{'ticket': 1, 'symbol': 'EURUSD', 'type': 'SELL', 'magic': HMA_MAGIC}]
    client.close_position.return_value = {'success': True, flag: True}
    bot._check_strategy()
    client.open_order.assert_not_called()
    assert bot._stop_event.is_set() and bot.runtime_state == 'ORDER_UNRESOLVED'
    assert client.close_position.call_args.kwargs['expected_account'] == [7, 'demo', 'DEMO']
    assert client.close_position.call_args.kwargs['stop_event'] is bot._stop_event


def test_failed_bot_settings_do_not_replace_disk_or_in_memory_snapshot(tmp_path):
    bot, _ = bot_client()
    bot.config_path = tmp_path / 'bot.json'
    bot.update_config({'symbol': 'GBPUSD'})
    before = bot.config_snapshot()
    original = bot.config_path.read_text()
    with pytest.raises(ValueError):
        bot.update_config({'symbol': 'XAUUSD', 'lot_size': float('nan')})
    assert bot.config_snapshot() == before and bot.config_path.read_text() == original
    assert json.loads(original)['symbol'] == 'GBPUSD'
    with pytest.raises(ValueError, match='tek pozisyon'):
        bot.update_config({'single_position': False})


def test_bot_configuration_is_blocked_until_inflight_cycle_finishes():
    bot, client = bot_client()
    entered, release = threading.Event(), threading.Event()
    original = client.get_rates.return_value
    def rates(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original
    client.get_rates.side_effect = rates
    worker = threading.Thread(target=bot._check_strategy)
    worker.start()
    try:
        assert entered.wait(1)
        before = bot.config_snapshot()
        with pytest.raises(ValueError):
            bot.update_config({'symbol': 'GBPUSD'})
        assert bot.config_snapshot() == before
        bot._check_strategy()  # concurrent callers cannot evaluate the same candle twice
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    client.open_order.assert_called_once()


def test_bot_start_refuses_settings_changed_during_readiness():
    bot, client = bot_client()
    checked = bot.config_snapshot()
    bot.update_config({'lot_size': .02})
    with pytest.raises(RuntimeError, match='ayarları değişti'):
        bot.start(expected_config=checked)
    assert not bot.is_running
    client.automation_stopped.clear.assert_not_called()


@pytest.mark.parametrize('period,shift', [(14, 1), (400, 2)])
def test_hma_exact_history_length_avoids_zero_filled_weights(period, shift):
    required = period + shift + int(period ** .5) - 1
    assert StrategyBot.calc_hma([1.] * required, period, shift) == pytest.approx(1.)
    assert StrategyBot.calc_hma([1.] * (required - 1), period, shift) == 0


@pytest.mark.parametrize('invalid', ['nan', 'unsorted'])
def test_invalid_candle_data_cannot_produce_a_bot_order(invalid):
    bot, client = bot_client()
    if invalid == 'nan':
        client.get_rates.return_value[-2]['close'] = float('nan')
    else:
        client.get_rates.return_value[-2]['time'] = client.get_rates.return_value[-3]['time']
    bot._check_strategy()
    client.open_order.assert_not_called()
    assert bot.runtime_state == 'WAITING_DATA'


def test_older_candle_cannot_replay_a_previous_signal():
    bot, client = bot_client()
    bot.last_candle_time = client.get_rates.return_value[-2]['time'] + 900
    bot._check_strategy()
    client.open_order.assert_not_called()
    bot.calc_hma.assert_not_called()


def test_journal_request_id_is_bound_to_submission_account(tmp_path):
    journal = OrderJournal(tmp_path / 'journal.db')
    payload = {'symbol': 'EURUSD', 'volume': .01}
    journal.run('same-id', payload, lambda: {'success': True, 'ticket': 42}, metadata={'account': [7, 'demo']})
    send = Mock()
    result = journal.run('same-id', payload, send, metadata={'account': [8, 'demo']})
    assert result['conflict'] and not result['success']
    send.assert_not_called()


def test_journal_late_ambiguous_response_cannot_overwrite_reconciled_fill(tmp_path):
    journal = OrderJournal(tmp_path / 'journal.db')
    def submit():
        journal.resolve('slow-order', {'success': True, 'uncertain': False, 'pending': False, 'ticket': 42})
        return {'success': False, 'uncertain': True}
    result = journal.run('slow-order', {}, submit)
    assert result['success'] and result['ticket'] == 42 and result['reconciled']
    assert not journal.status('slow-order')['uncertain']
    assert journal.run('slow-order', {}, lambda: pytest.fail('duplicate submit'))['ticket'] == 42
