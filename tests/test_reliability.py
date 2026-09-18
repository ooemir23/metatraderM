import asyncio
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app import mt5_client as module
from app.mt5_client import MT5Client, NATIVE_CLOSE_SCRIPT
from app.strategy_bot import StrategyBot


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
    c = MT5Client()
    c.mt5 = Mock()
    c.mt5.terminal_info.return_value = NS(connected=True)
    c.mt5.symbol_info.return_value = NS(point=.00001, digits=5)
    c.mt5.symbol_info_tick.return_value = NS(ask=1.1, bid=1.09)
    c.is_connected = True
    c.last_ping_time = time.time()
    return c


def result(code):
    return NS(retcode=code, order=123, price=1.1, volume=.01, comment='broker response')


@pytest.mark.parametrize('code', [10008, 10009, 10010, 10012, 10027])
def test_open_never_retries_accepted_partial_or_ambiguous_result(client, code):
    client.mt5.order_send.return_value = result(code)
    res = client.open_order('EURUSD', 'BUY', .01)
    assert res['success'] == (code in (10008, 10009, 10010))
    assert client.mt5.order_send.call_count == 1


def test_only_invalid_filling_is_retried(client):
    client.mt5.order_send.side_effect = [result(10030), result(10009)]
    assert client.open_order('EURUSD', 'BUY', .01)['success']
    assert client.mt5.order_send.call_count == 2


def test_unknown_result_is_not_resent_through_other_transport(client):
    client.conn = Mock()
    client.conn.eval.side_effect = TimeoutError('response lost after broker accepted')
    res = client.open_order('EURUSD', 'BUY', .01)
    assert res['uncertain']
    client.mt5.order_send.assert_not_called()
    assert client.conn.eval.call_count == 1


@pytest.mark.parametrize('operation', ['close_position', 'close_by_filter'])
def test_unknown_close_does_not_fall_back(client, operation):
    client.conn = Mock()
    client.conn.eval.side_effect = TimeoutError()
    args = (123,) if operation == 'close_position' else ('all',)
    assert getattr(client, operation)(*args)['uncertain']
    client.mt5.order_send.assert_not_called()
    client.mt5.positions_get.assert_not_called()


def test_failed_health_check_clears_stale_connection(client):
    old = Mock()
    client.conn = old
    client.mt5.terminal_info.return_value = None
    client.last_ping_time = 0
    client.last_connect_attempt = time.time()  # exercise retry cooldown
    assert client.ensure_connected() is False
    assert client.mt5 is None and client.conn is None
    assert not client.is_connected
    old.close.assert_called_once()


def test_failed_initialize_closes_rpc_connection(client, monkeypatch):
    client.is_connected = False
    remote = Mock()
    remote.modules.MetaTrader5.initialize.return_value = False
    monkeypatch.setattr(module.rpyc.SocketStream, 'connect', Mock())
    monkeypatch.setattr(module.rpyc.utils.factory, 'connect_stream', Mock(return_value=remote))
    assert not client.connect()
    remote.close.assert_called_once()
    assert not client.is_connected


def test_connection_attempts_are_serialized(client, monkeypatch):
    client.is_connected = False
    remote = Mock()
    monkeypatch.setattr(module.rpyc.SocketStream, 'connect', Mock())
    factory = Mock(return_value=remote)
    monkeypatch.setattr(module.rpyc.utils.factory, 'connect_stream', factory)
    threads = [threading.Thread(target=client.connect) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert factory.call_count == 1
    assert client.is_connected


def test_hourly_timeframe_uses_mt5_constant(client):
    client.mt5.TIMEFRAME_H1 = 16385
    client.mt5.copy_rates_from_pos.return_value = [(100, 1., 2., .5, 1.5, 25)]
    assert client.get_rates('EURUSD', 60)[0]['close'] == 1.5
    client.mt5.copy_rates_from_pos.assert_called_once_with('EURUSD', 16385, 0, 100)


@pytest.mark.parametrize('direction,volume', [('INVALID', .01), ('BUY', -1), ('BUY', float('nan'))])
def test_invalid_order_is_rejected_before_broker(client, direction, volume):
    assert not client.open_order('EURUSD', direction, volume)['success']
    client.mt5.order_send.assert_not_called()


def test_native_close_partial_not_reported_closed_or_retried(monkeypatch):
    import sys
    mt5 = Mock()
    mt5.TRADE_ACTION_DEAL = 1
    mt5.symbol_info_tick.return_value = NS(ask=1.1, bid=1.09)
    mt5.order_send.return_value = result(10010)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', mt5)
    ns = {}
    exec(NATIVE_CLOSE_SCRIPT, ns)
    response = ns['_execute_close_deal'](NS(ticket=12, symbol='EURUSD', type=0, volume=.02))
    assert response['partial'] and not response['success']
    mt5.order_send.assert_called_once()


def test_close_failure_prevents_opposite_order(client):
    bot = StrategyBot(client)
    bot.send_telegram = Mock()
    client.ensure_connected = Mock(return_value=True)
    client.get_rates = Mock(return_value=[{'time': i, 'close': 1.} for i in range(100)])
    bot.calc_hma = Mock(side_effect=[2., 0.])
    bot.calc_second_ma = Mock(return_value=1.)
    client.get_positions = Mock(return_value=[{'ticket': 1, 'symbol': 'EURUSD', 'type': 'SELL'}])
    client.close_position = Mock(return_value={'success': False})
    client.open_order = Mock()
    bot._check_strategy()
    client.close_position.assert_called_once()
    client.open_order.assert_not_called()


def test_bot_worker_does_not_block_event_loop_and_stop_prevents_order(client):
    async def scenario():
        bot = StrategyBot(client)
        entered, release = threading.Event(), threading.Event()
        client.ensure_connected = Mock(return_value=True)
        def rates(*args, **kwargs):
            entered.set()
            assert release.wait(2)
            return [{'time': i, 'close': 1.} for i in range(100)]
        client.get_rates = rates
        bot.calc_hma = Mock(side_effect=[2., 0.])
        bot.calc_second_ma = Mock(return_value=1.)
        bot.send_telegram = Mock()
        client.open_order = Mock()
        task = asyncio.create_task(bot.check_strategy())
        assert await asyncio.to_thread(entered.wait, 1)
        bot.stop()
        release.set()
        await asyncio.wait_for(task, 2)
        client.open_order.assert_not_called()
    asyncio.run(scenario())


def test_web_starts_while_mt5_connection_is_blocked(monkeypatch):
    from app import main
    entered, release = threading.Event(), threading.Event()
    def connect():
        entered.set()
        release.wait(2)
        return False
    monkeypatch.setattr(main, 'ensure_optimized_server_py', lambda: None)
    monkeypatch.setattr(main.mt5_client, 'connect', connect)
    main.mt5_client.is_connected = False
    try:
        with TestClient(main.app) as web:
            assert entered.wait(1)
            assert web.get('/').status_code == 200
            release.set()
    finally:
        release.set()


def test_invalid_request_returns_validation_error():
    from app.main import app
    web = TestClient(app)  # no lifespan / remote connection
    assert web.post('/api/order/open', json={'symbol':'EURUSD', 'order_type':'TYPO', 'volume':.01}).status_code == 422
    assert web.post('/api/order/open', json={'symbol':'EURUSD', 'order_type':'BUY', 'volume':-1}).status_code == 422


def test_real_rpyc_connection_exposes_remote_modules(client, monkeypatch):
    """Exercise the actual service handshake, not just mocked RPC objects."""
    import sys
    from types import ModuleType
    from rpyc.utils.server import ThreadedServer
    remote_mt5 = ModuleType('MetaTrader5')
    remote_mt5.initialize = lambda **kwargs: True
    remote_mt5.terminal_info = lambda: NS(connected=True)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', remote_mt5)
    server = ThreadedServer(module.rpyc.SlaveService, hostname='127.0.0.1', port=0,
                            auto_register=False)
    server.listener.listen(5)
    worker = threading.Thread(target=server.start, daemon=True)
    worker.start()
    client.is_connected = False
    client.host, client.port = '127.0.0.1', server.port
    try:
        assert client.connect(), client.last_error_msg
        assert client.mt5.terminal_info().connected
    finally:
        client._reset_connection()
        server.close()
        worker.join(timeout=2)
