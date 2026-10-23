"""Regression cases for automation races and broker-result ambiguity; no live services."""
import json
import threading
import time
from unittest.mock import Mock

import pytest

from app.mt5_bridge import HMA_MAGIC
from app.order_journal import OrderJournal
from app.strategy_bot import StrategyBot








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
