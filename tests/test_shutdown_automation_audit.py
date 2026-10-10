"""Shutdown must stop queued automation; every broker/provider is mocked."""
import asyncio
import threading
import time
from unittest.mock import Mock

import pytest

from app import ai_advisor
from test_trade_safety import client


@pytest.mark.parametrize('persistence_ok', [True, False])
def test_shutdown_blocks_ai_worker_already_waiting_for_order_lock(client, monkeypatch, tmp_path, persistence_ok):
    monkeypatch.setattr(ai_advisor, 'MEMORY_PATHS', [str(tmp_path / 'ai.json')])
    provider = Mock(side_effect=AssertionError('Live provider forbidden'))
    monkeypatch.setattr(ai_advisor.requests, 'post', provider)
    from app import main

    advisor = ai_advisor.DeepSeekAdvisor(client, api_key='stub')
    assert advisor.update_autopilot({'enabled': True, 'mode': 'FULL_AUTO'})['success']
    generation, stop_event = advisor.autopilot_generation, advisor._auto_stop
    recommendation = {'id': 'shutdown-queued-ai', 'symbol': 'EURUSD', 'action': 'BUY',
        'confidence': 90, 'suggested_lot': .01, 'sl_points': 200, 'tp_points': 400,
        'expires_at': time.time() + 900, 'account': [1, 'test', 'DEMO']}
    advisor.cost_state['advice_cache']['trusted'] = {'recommendation': recommendation,
        'expires_at': recommendation['expires_at']}
    monkeypatch.setattr(client, 'get_symbol_price', Mock(return_value={'ask': 1.1, 'bid': 1.0999}))
    entered, finished = threading.Event(), threading.Event()
    outcomes = []
    original_open = client.open_order

    def queued_open(*args, **kwargs):
        entered.set()
        try:
            result = original_open(*args, **kwargs)
            outcomes.append(result)
            return result
        finally:
            finished.set()

    async def automatic_loop():
        try:
            await asyncio.to_thread(advisor.execute_recommendation, recommendation,
                automatic=True, generation=generation)
        except asyncio.CancelledError:
            # Stop guards must precede cancellation, while the worker still runs.
            assert client.automation_stopped.is_set()
            assert stop_event.is_set()
            raise

    async def idle_loop():
        await asyncio.Future()

    monkeypatch.setattr(client, 'open_order', queued_open)
    monkeypatch.setattr(main, 'mt5_client', client)
    monkeypatch.setattr(main, 'ai_advisor', advisor)
    monkeypatch.setattr(main, 'bot', Mock())
    monkeypatch.setattr(main.security_sessions, 'initialize', Mock())
    monkeypatch.setattr(main, 'ensure_optimized_server_py', Mock())
    monkeypatch.setattr(main, 'auto_reconnect_loop', automatic_loop)
    monkeypatch.setattr(main, 'live_feed', Mock(run=idle_loop))
    monkeypatch.setattr(main, 'maintenance_loop', idle_loop)

    async def scenario():
        # The worker passes AI validation, then queues on the real order lock.
        with client._lock:
            async with main.lifespan(main.app):
                assert await asyncio.to_thread(entered.wait, 2), 'AI worker never reached the order queue'
                assert not finished.is_set()
                if not persistence_ok:
                    monkeypatch.setattr(advisor, 'save_memory', Mock(return_value=False))
            assert not advisor.autopilot['enabled']
            assert advisor.autopilot_generation > generation
            assert not finished.is_set(), 'Cancelling the task must not be mistaken for stopping its thread'
        assert await asyncio.to_thread(finished.wait, 2), 'Queued worker did not finish after the lock was released'

    asyncio.run(scenario())
    assert len(outcomes) == 1 and not outcomes[0]['success']
    assert not outcomes[0].get('uncertain')
    client.mt5.order_send.assert_not_called()
    provider.assert_not_called()
