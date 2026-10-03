import time

import pytest

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
    client.mt5.order_send.assert_not_called()
