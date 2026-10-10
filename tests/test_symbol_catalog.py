"""Terminal catalog and exact broker-name integration, using only fake MT5 data."""
import asyncio
import base64
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import mt5_bridge as bridge
from app.mt5_client import MT5DataError
from test_trade_safety import client


def symbol(name, *, path='', description='', trade_mode=4, visible=False, **fields):
    return NS(name=name, path=path, description=description, trade_mode=trade_mode,
              digits=5, visible=visible, **fields)


@pytest.fixture
def catalog_client(client):
    client.mt5.symbols_get.return_value = (
        symbol('EURUSD.a', path='Forex\\Majors', description='Euro vs US Dollar', visible=True),
        symbol('BTCUSDm', path='Cryptocurrencies', description='Bitcoin', digits_unused=2),
        symbol('XAUUSD', path='Metals', description='Gold'),
        symbol('US500', path='Indices\\Cash'),
        symbol('GS.N', path='US Stocks', description='Goldman Sachs'),
        symbol('WTI', path='Commodities\\Energy'),
        symbol('CustomProduct', path='Custom', description='Broker product'),
    )
    return client


def assert_catalog_is_read_only(mt5):
    for name in ('symbol_info', 'symbol_select', 'symbol_info_tick', 'order_check', 'order_send'):
        getattr(mt5, name).assert_not_called()


def test_catalog_contract_preserves_broker_metadata_and_infers_categories(catalog_client):
    result = catalog_client.get_symbols()
    assert result['account'] == [1, 'test', 'DEMO'] and result['total'] == 7
    rows = {row['name']: row for row in result['symbols']}
    assert {name: row['category'] for name, row in rows.items()} == {
        'EURUSD.a':'forex', 'BTCUSDm':'crypto', 'XAUUSD':'metals', 'US500':'indices',
        'GS.N':'stocks', 'WTI':'commodities', 'CustomProduct':'other'}
    assert rows['EURUSD.a'] == dict(name='EURUSD.a', path='Forex\\Majors',
        description='Euro vs US Dollar', category='forex', trade_mode=4,
        digits=5, visible=True, selectable=True)
    assert all(row['selectable'] for row in rows.values())
    assert_catalog_is_read_only(catalog_client.mt5)


@pytest.mark.parametrize('mode,selectable', [(0,False), (1,True), (2,True), (3,False), (4,True), (99,False)])
def test_only_open_trade_modes_are_selectable(catalog_client, mode, selectable):
    catalog_client.mt5.symbols_get.return_value = [symbol('BTCUSDm', trade_mode=mode)]
    row = catalog_client.get_symbols()['symbols'][0]
    assert row['trade_mode'] == mode and row['selectable'] is selectable


@pytest.mark.parametrize('name', ['Name with space', '<img-src=x>', 'A'*33, '', 'EURUSD/other'])
def test_unsupported_api_names_stay_plain_metadata_and_cannot_be_selected(catalog_client, name):
    catalog_client.mt5.symbols_get.return_value = [symbol(name, description='<script>alert(1)</script>')]
    row = catalog_client.get_symbols()['symbols'][0]
    assert row['name'] == name and row['description'] == '<script>alert(1)</script>'
    assert row['selectable'] is False
    assert_catalog_is_read_only(catalog_client.mt5)


def test_metadata_hint_without_broker_row_does_not_invent_symbols(catalog_client):
    catalog_client.mt5.symbols_get.return_value = [symbol('EURUSD.a', path='Forex')]
    assert [row['name'] for row in catalog_client.get_symbols()['symbols']] == ['EURUSD.a']


def test_catalog_cache_is_account_bound_expires_and_cannot_be_mutated_by_caller(catalog_client):
    first = catalog_client.get_symbols()
    first['symbols'][0]['name'] = 'tampered'
    second = catalog_client.get_symbols()
    assert all(row['name'] != 'tampered' for row in second['symbols'])
    catalog_client.mt5.symbols_get.assert_called_once_with()
    catalog_client._symbol_catalog_cache_time -= 61
    catalog_client.get_symbols()
    assert catalog_client.mt5.symbols_get.call_count == 2
    catalog_client.login_id = 2
    catalog_client.server = 'second'
    catalog_client.mt5.account_info.return_value.login = 2
    catalog_client.mt5.account_info.return_value.server = 'second'
    catalog_client.mt5.symbols_get.return_value = [symbol('BTCUSDz', path='Crypto')]
    switched = catalog_client.get_symbols()
    assert switched['account'] == [2, 'second', 'DEMO']
    assert [row['name'] for row in switched['symbols']] == ['BTCUSDz']
    assert catalog_client.mt5.symbols_get.call_count == 3


def test_external_account_switch_cannot_expose_previous_cached_catalog(catalog_client):
    catalog_client.get_symbols()
    catalog_client.mt5.account_info.return_value.login = 99
    with pytest.raises(MT5DataError):
        catalog_client.get_symbols()
    catalog_client.mt5.symbols_get.assert_called_once()


def test_account_switch_during_symbols_read_discards_result(catalog_client):
    def switched():
        catalog_client.mt5.account_info.return_value.login = 99
        return [symbol('WrongAccount')]
    catalog_client.mt5.symbols_get.side_effect = switched
    with pytest.raises(MT5DataError):
        catalog_client.get_symbols()
    assert catalog_client._symbol_catalog_cache is None


def test_account_type_change_during_catalog_read_is_detected(catalog_client):
    def switched():
        catalog_client.mt5.account_info.return_value.trade_mode = 2
        return [symbol('WrongAccount')]
    catalog_client.mt5.symbols_get.side_effect = switched
    with pytest.raises(MT5DataError):
        catalog_client.get_symbols()


def test_missing_catalog_fails_without_fabricated_fallback(catalog_client):
    catalog_client.mt5.symbols_get.return_value = None
    with pytest.raises(MT5DataError, match='kataloğu'):
        catalog_client.get_symbols()
    assert catalog_client._symbol_catalog_cache is None


def test_forced_catalog_refresh_updates_same_account_without_subscribing_quotes(catalog_client, monkeypatch):
    _, browser = web(monkeypatch, catalog_client)
    assert browser.get('/api/symbols').json()['total'] == 7
    catalog_client.mt5.symbols_get.return_value = [symbol('BTCUSDnew', path='Crypto')]
    assert browser.get('/api/symbols').json()['total'] == 7
    updated = browser.get('/api/symbols', params={'refresh': 'true'}).json()
    assert updated['total'] == 1 and updated['symbols'][0]['name'] == 'BTCUSDnew'
    assert catalog_client.mt5.symbols_get.call_count == 2
    assert_catalog_is_read_only(catalog_client.mt5)


def test_rpc_catalog_crosses_bridge_as_json_with_exact_names(catalog_client, monkeypatch):
    from app import mt5_client as module
    from rpyc.utils.server import ThreadedServer
    remote = ModuleType('MetaTrader5')
    remote.initialize = lambda **_kwargs: True
    remote.terminal_info = lambda: NS(connected=True)
    remote.account_info = lambda: NS(login=1, server='test', trade_mode=0)
    remote.symbols_get = Mock(return_value=[symbol('BTCUSDm', path='Crypto')])
    monkeypatch.setitem(sys.modules, 'MetaTrader5', remote)
    server = ThreadedServer(module.rpyc.SlaveService, hostname='127.0.0.1', port=0, auto_register=False)
    server.listener.listen(5)
    worker = threading.Thread(target=server.start, daemon=True)
    worker.start()
    catalog_client.is_connected = False
    catalog_client.host, catalog_client.port = '127.0.0.1', server.port
    try:
        assert catalog_client.connect()
        result = catalog_client.get_symbols()
        assert result['account'] == [1, 'test', 'DEMO']
        assert result['symbols'][0]['name'] == 'BTCUSDm' and result['total'] == 1
        remote.symbols_get.assert_called_once_with()
        assert catalog_client._bridge_ready
    finally:
        catalog_client._reset_connection()
        server.close()
        worker.join(timeout=2)


def web(monkeypatch, catalog_client):
    from app import main
    monkeypatch.setattr(main, 'mt5_client', catalog_client)
    token = base64.b64encode(b'test:test-panel-password').decode()
    return main, TestClient(main.app, headers={'Authorization':'Basic ' + token})


def test_catalog_api_is_authenticated_read_only_and_account_scoped(monkeypatch, catalog_client):
    main, browser = web(monkeypatch, catalog_client)
    assert TestClient(main.app).get('/api/symbols').status_code == 401
    response = browser.get('/api/symbols')
    assert response.status_code == 200
    assert response.json()['account'] == [1, 'test', 'DEMO']
    assert response.json()['total'] == 7
    assert_catalog_is_read_only(catalog_client.mt5)


@pytest.mark.parametrize('name', ['EURUSD.a', 'BTCUSDm'])
def test_manual_rates_price_preview_and_order_keep_exact_symbol(catalog_client, name):
    mt5 = catalog_client.mt5
    mt5.TIMEFRAME_M15 = 15
    mt5.copy_rates_from_pos.return_value = [(100, 1., 2., .5, 1.5, 25)]
    assert catalog_client.get_rates(name)[0]['close'] == 1.5
    mt5.copy_rates_from_pos.assert_called_with(name, 15, 0, 100)
    assert catalog_client.get_symbol_price(name)['symbol'] == name
    mt5.symbol_info_tick.assert_called_with(name)
    assert catalog_client.trade_preview(name, 'BUY', .01, 200)['success']
    mt5.order_calc_profit.assert_called_with(0, name, .01, 1.1, 1.098)
    result = catalog_client.open_order(name, 'BUY', .01, 200, request_id='exact-name-' + name)
    assert result['success']
    assert mt5.order_send.call_args.args[0]['symbol'] == name
    row = catalog_client.journal.opening_executions([1,'test'], [123])[123]
    assert row['order']['symbol'] == name


def test_chart_price_open_and_pending_api_preserve_lowercase_suffix(monkeypatch, catalog_client):
    main, browser = web(monkeypatch, catalog_client)
    rows = [{'time':100, 'open':1., 'high':2., 'low':.5, 'close':1.5, 'tick_volume':25}]
    rates = Mock(return_value=rows)
    monkeypatch.setattr(catalog_client, 'get_rates', rates)
    response = browser.get('/api/chart/candles', params={'symbol':'EURUSD.a', 'count':3})
    assert response.status_code == 200 and response.json()['symbol'] == 'EURUSD.a'
    rates.assert_called_once_with('EURUSD.a', 15, 3)
    assert browser.get('/api/price/BTCUSDm').json()['symbol'] == 'BTCUSDm'
    opened = browser.post('/api/order/open', json={'request_id':'catalog-open-request-1234',
        'symbol':'EURUSD.a','order_type':'BUY','volume':.01,'sl_points':200})
    assert opened.status_code == 200
    assert catalog_client.mt5.order_send.call_args.args[0]['symbol'] == 'EURUSD.a'
    catalog_client.mt5.symbol_info.return_value.order_mode = 127
    catalog_client.mt5.symbol_info.return_value.expiration_mode = 15
    pending = browser.post('/api/order/pending', json={'request_id':'catalog-pending-request-1234',
        'symbol':'BTCUSDm','order_type':'BUY','volume':.01,'sl_points':200,
        'pending_type':'BUY_LIMIT','entry_price':1.08})
    assert pending.status_code == 200
    assert catalog_client.mt5.order_send.call_args.args[0]['symbol'] == 'BTCUSDm'


def test_sse_subscription_and_snapshot_keep_exact_symbol(monkeypatch, catalog_client):
    from app import main
    from app.live_feed import LiveFeed
    feed = LiveFeed(catalog_client)
    monkeypatch.setattr(main, 'live_feed', feed)
    async def scenario():
        response = await main.live('BTCUSDm')
        assert list(feed.listeners.values()) == ['BTCUSDm']
        stream = response.body_iterator
        assert 'connected' in await anext(stream)
        await stream.aclose()
        assert not feed.listeners
    asyncio.run(scenario())
    bridge_call = Mock(return_value={'account':dict(login=1,server='test',trade_mode=0), 'positions':[]})
    monkeypatch.setattr(catalog_client, '_bridge', bridge_call)
    catalog_client.get_live_snapshot(['BTCUSDm','EURUSD.a'])
    bridge_call.assert_called_once_with('live_snapshot', ['BTCUSDm','EURUSD.a'], 0)
