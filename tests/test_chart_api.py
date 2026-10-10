import base64
from unittest.mock import Mock
from fastapi.testclient import TestClient


def web():
    from app import main
    token = base64.b64encode(b'test:test-panel-password').decode()
    return main, TestClient(main.app, headers={'Authorization': 'Basic ' + token})


def test_chart_history_validates_input_and_returns_broker_data(monkeypatch):
    main, client = web()
    rows = [{'time': 1791590400, 'open': 82000, 'high': 82100,
             'low': 81900, 'close': 82050, 'tick_volume': 25}]
    get_rates = Mock(return_value=rows)
    monkeypatch.setattr(main.mt5_client, 'get_rates', get_rates)
    for params in ({'symbol':'BTCUSD', 'timeframe_minutes':7}, {'count':1001}, {'symbol':'BTCUSD/../../'}):
        assert client.get('/api/chart/candles', params=params).status_code == 422
    get_rates.assert_not_called()
    response = client.get('/api/chart/candles', params={'symbol':'btcusd', 'timeframe_minutes':60, 'count':3})
    assert response.status_code == 200
    assert response.json()['rates'] == rows
    assert response.json()['time_basis'] == 'broker'
    assert response.json()['symbol'] == 'btcusd'
    get_rates.assert_called_once_with('btcusd', 60, 3)


def test_chart_unavailable_does_not_supply_fake_prices(monkeypatch):
    main, client = web()
    monkeypatch.setattr(main.mt5_client, 'get_rates', lambda *args: None)
    response = client.get('/api/chart/candles')
    assert response.status_code == 503
    assert 'MT5 mum verisi' in response.json()['detail']
