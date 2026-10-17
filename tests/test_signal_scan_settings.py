"""Signal scanner settings: closed-candle confirmation, MA type, RSI filter, ATR SL/TP and reasons."""
import base64
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import main


def rates_with_cross_on(index_from_end, n=140):
    """Falling then rising closes so HMA crosses the slow MA near the end."""
    closes = [100 - i * 0.05 for i in range(n - 30)] + [100 - (n - 30) * 0.05 + i * 0.4 for i in range(30)]
    rows = [{'time': 1000 + i * 60, 'open': c, 'high': c + 0.1, 'low': c - 0.1, 'close': c} for i, c in enumerate(closes)]
    return rows


def scan(monkeypatch, rows, **kw):
    monkeypatch.setattr(main.mt5_client, 'get_rates', Mock(return_value=rows))
    return main._scan_symbols_sync(['EURUSD'], 1, kw.pop('hma_period', 14), kw.pop('ema_period', 34), **kw)


def test_reason_and_labels_follow_settings(monkeypatch):
    item = scan(monkeypatch, rates_with_cross_on(5), ma_type='SMA', ema_period=20)[0]
    assert 'HMA(14)' in item['reason'] and 'SMA(20)' in item['reason']
    assert 'kapanmış mum' in item['reason']
    assert item['side'] in ('BUY', 'SELL')


def test_forming_candle_cannot_create_signal_when_confirm_close(monkeypatch):
    rows = rates_with_cross_on(5)
    # Only the forming (last) candle jumps; a closed-candle scan must ignore it.
    flat = [dict(r, close=100.0 - i * 0.01, high=100.1 - i * 0.01, low=99.9 - i * 0.01) for i, r in enumerate(rows[:-1])]
    spike = dict(rows[-1], close=130.0, high=130.1, low=129.9)
    confirmed = scan(monkeypatch, flat + [spike], confirm_close=True)[0]
    live = scan(monkeypatch, flat + [spike], confirm_close=False)[0]
    assert confirmed['signal'] not in ('BUY_CROSS',)
    assert live['signal'] == 'BUY_CROSS'
    assert confirmed['candle_time'] == flat[-1]['time'] and live['candle_time'] == spike['time']


def test_rsi_filter_blocks_overbought_buy(monkeypatch):
    rows = rates_with_cross_on(5)
    item = scan(monkeypatch, rows, rsi_filter=True, rsi_buy_max=50)[0]
    assert item['signal'] == 'FILTERED' and item['side'] == 'HOLD'
    assert 'filtrelendi' in item['reason'] and item['suggested_sl'] == 0


def test_atr_multipliers_and_off(monkeypatch):
    rows = rates_with_cross_on(5)
    a = scan(monkeypatch, rows, sl_atr=1, tp_atr=0)[0]
    b = scan(monkeypatch, rows, sl_atr=2, tp_atr=3)[0]
    assert a['suggested_tp'] == 0
    assert abs(abs(b['price'] - b['suggested_sl']) - 2 * abs(a['price'] - a['suggested_sl'])) < 1e-6


def test_endpoint_validates_settings(monkeypatch):
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    monkeypatch.setattr(main.mt5_client, 'get_rates', Mock(return_value=rates_with_cross_on(5)))
    ok = web.get('/api/signals/scan', params={'symbols': 'EURUSD', 'timeframe_minutes': 1, 'ma_type': 'LWMA', 'confirm_close': 'false'})
    assert ok.status_code == 200 and ok.json()['settings']['ma_type'] == 'LWMA' and ok.json()['settings']['confirm_close'] is False
    assert web.get('/api/signals/scan', params={'ma_type': 'XYZ'}).status_code == 422
    assert web.get('/api/signals/scan', params={'sl_atr': 99}).status_code == 422
