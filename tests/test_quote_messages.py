import time

import pytest
from types import SimpleNamespace as NS
from app.mt5_bridge import quote_status, price

from test_trade_safety import client


@pytest.mark.parametrize('kind, expected', [
    ('stale', 'Son fiyat 10 saat 41 dakika 47 saniye önce alındı'),
    ('future', 'broker saat farkı'),
    ('invalid', 'Broker geçerli fiyat bildirmedi'),
])
def test_preview_and_open_explain_quote_failure_without_sending(client, monkeypatch, kind, expected):
    now = time.time()
    monkeypatch.setattr('app.mt5_bridge.time.time', lambda: now)
    tick = client.mt5.symbol_info_tick.return_value
    tick.time = now
    if kind == 'stale':
        tick.time -= 38507
    elif kind == 'future':
        tick.time += 10800
    else:
        tick.bid = float('nan')
    preview = client.trade_preview('EURUSD', 'BUY', .01, 200)
    order = client.open_order('EURUSD', 'BUY', .01)
    for result in (preview, order):
        assert not result['success']
        assert expected in result['error']
        assert 'eski veya geçersiz' not in result['error']
        assert 'olabilir' not in result['error']
        assert 'MT5 broker bağlantısı açık.' in result['error']
    client.mt5.order_send.assert_not_called()


@pytest.mark.parametrize('connected', [False, None])
def test_current_cached_price_cannot_mask_disconnected_or_unknown_broker(client, connected):
    client.mt5.terminal_info.return_value = NS(connected=False) if connected is False else None
    result = client.open_order('EURUSD', 'BUY', .01)
    assert not result['success']
    assert ('bağlantısı kesik' if connected is False else 'bağlantısı doğrulanamadı') in result['error']
    client.mt5.order_send.assert_not_called()


def test_price_endpoint_reports_broker_and_quote_independently(client):
    tick = client.mt5.symbol_info_tick.return_value
    tick.time = time.time()-3600
    status = client.get_symbol_price('EURUSD')['quote_status']
    assert status['broker_connected'] is True
    assert status['state'] == 'stale'
    assert status['age_seconds'] >= 3600
    client.mt5.terminal_info.return_value.connected = False
    assert price(client.mt5, 'EURUSD')['quote_status']['broker_connected'] is False


def test_missing_quote_has_explicit_diagnostic(client):
    client.mt5.symbol_info_tick.return_value = None
    result = price(client.mt5, 'EURUSD')
    assert result['time'] == 0
    assert result['quote_status']['state'] == 'missing'
    assert result['quote_status']['age_seconds'] is None
    assert 'MT5 bu sembol için fiyat bildirmedi' in result['quote_status']['message']


def test_same_bid_with_new_tick_is_current(monkeypatch):
    monkeypatch.setattr('app.mt5_bridge.time.time', lambda: 1800000000)
    tick = NS(time=1799999999, bid=1.1, ask=1.1001)
    assert quote_status(tick, connected=True)['state'] == 'fresh'
    tick.time -= 60
    assert quote_status(tick, connected=True)['state'] == 'stale'


def test_price_enables_unselected_symbol_before_reading_tick(client):
    info = client.mt5.symbol_info.return_value
    info.select = False
    tick = client.mt5.symbol_info_tick.return_value
    def select(symbol, enabled):
        info.select = enabled
        return True
    client.mt5.symbol_select.side_effect = select
    client.mt5.symbol_info_tick.side_effect = lambda symbol: tick if info.select else None
    result = price(client.mt5, 'BTCUSD')
    client.mt5.symbol_select.assert_called_once_with('BTCUSD', True)
    assert result['bid'] == tick.bid
    assert result['quote_status']['state'] == 'fresh'
    client.mt5.order_send.assert_not_called()


def test_selected_symbol_does_not_need_resubscription(client):
    client.mt5.symbol_info.return_value.select = True
    assert price(client.mt5, 'EURUSD')['quote_status']['state'] == 'fresh'
    client.mt5.symbol_select.assert_not_called()


def test_failed_symbol_selection_does_not_use_cached_tick(client):
    client.mt5.symbol_info.return_value.select = False
    client.mt5.symbol_select.return_value = False
    result = price(client.mt5, 'BTCUSD')
    assert result['bid'] == result['ask'] == result['time'] == 0
    assert result['quote_status']['state'] == 'missing'
    assert 'MT5 sembolü fiyat takibine alamadı.' in result['quote_status']['message']
    client.mt5.symbol_info_tick.assert_not_called()
