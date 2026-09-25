import base64
import hashlib
import hmac
import struct
import time
from types import SimpleNamespace as NS

from app import security_sessions
from app.backtest import simulate
from app.mt5_bridge import trade_preview, symbol_spec, broker_compatibility, risk_status
from app.order_journal import OrderJournal
from test_trade_safety import client


def test_totp_session_expiry_and_revoke(monkeypatch):
    secret = base64.b32encode(b'01234567890123456789').decode().rstrip('=')
    monkeypatch.setenv('TRADING_TOTP_SECRET', secret)
    now = 1_800_000_000
    counter = now // 30
    mac = hmac.new(b'01234567890123456789', struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = mac[-1] & 15
    code = str((struct.unpack('>I', mac[offset:offset+4])[0] & 0x7fffffff) % 1_000_000).zfill(6)
    assert security_sessions.verify_totp(code, now)
    assert not security_sessions.verify_totp('00000x', now)
    token, _ = security_sessions.unlock('operator')
    assert security_sessions.session_expires(token, 'operator') > time.time()
    assert not security_sessions.session_expires(token, 'other')
    security_sessions.revoke(token)
    assert security_sessions.session_expires(token, 'operator') == 0


def test_broker_trade_preview_uses_profit_and_margin_api(client):
    client.mt5.account_info.return_value = NS(login=1, server='test', trade_mode=0,
        currency='USD', equity=1000, margin_free=800)
    client.mt5.order_calc_profit.return_value = -20.0
    client.mt5.order_calc_margin.return_value = 50.0
    result = trade_preview(client.mt5, 'EURUSD', 'BUY', .01, 200, None, None, 1, 'test')
    assert result['success'] and result['stop_risk'] == 20
    assert result['risk_pct_equity'] == 2
    assert result['margin_required'] == 50
    assert not result['commission_included']
    client.mt5.order_send.assert_not_called()
    assert not trade_preview(client.mt5, 'EURUSD', 'BUY', .01, 200, None, None, 2, 'test')['success']


def test_trade_preview_shows_order_guards_before_submission(client, tmp_path):
    from app.order_journal import OrderJournal
    client.journal = OrderJournal(tmp_path / 'orders.db')
    client.mt5.history_deals_get.return_value = [NS(type=0, profit=-600, commission=0, swap=0, fee=0)]
    assert 'Günlük zarar sınırı' in client.trade_preview('EURUSD', 'BUY', .01, 200)['error']
    client.mt5.history_deals_get.return_value = []
    assert 'üst sınırı' in client.trade_preview('EURUSD', 'BUY', .11, 200)['error']
    assert 'SL broker' in client.trade_preview('EURUSD', 'BUY', .01, 5)['error']
    client.mt5.orders_get.return_value = [NS(volume_current=.5)]
    assert 'Toplam' in client.trade_preview('EURUSD', 'BUY', .01, 200)['error']
    client.mt5.order_send.assert_not_called()


def test_explicit_broker_clock_offset_keeps_future_or_stale_quotes_out(client):
    client.mt5.account_info.return_value = NS(login=1, server='test', trade_mode=0,
        currency='USD', equity=1000, margin_free=800, margin_mode=2)
    client.mt5.order_calc_profit.return_value = -20
    client.mt5.order_calc_margin.return_value = 50
    client.mt5.symbol_info_tick.return_value.time = time.time() + 10800
    assert not trade_preview(client.mt5, 'EURUSD', 'BUY', .01, 200, None, None, 1, 'test')['success']
    client.tick_clock_offset = 10800
    assert client.trade_preview('EURUSD','BUY',.01,200)['success']
    assert client.open_order('EURUSD','BUY',.01)['success']
    client.mt5.symbol_info_tick.return_value.time -= 60
    assert not client.open_order('EURUSD','BUY',.01)['success']


def test_daily_risk_status_uses_verified_account_and_broker_values(client):
    client.mt5.account_info.return_value.currency = 'EUR'
    client.mt5.history_deals_get.return_value = [NS(type=0, profit=-400, commission=-10, swap=0, fee=0)]
    status = risk_status(client.mt5, 1, 'test', 500)
    assert status['daily_loss'] == 410 and status['ratio'] == .82


def test_symbol_capabilities_from_broker(client):
    client.mt5.symbol_info.return_value.order_mode = 7
    client.mt5.symbol_info.return_value.expiration_mode = 1
    result = symbol_spec(client.mt5, 'EURUSD')
    assert result['order_mode'] == 7 and result['volume_step'] == .01
    assert result['account_type'] == 'DEMO'


def test_broker_dry_run_checks_sides_and_order_types_without_sending(client):
    client.mt5.symbol_info.return_value.order_mode = 7
    client.mt5.symbol_info.return_value.expiration_mode = 1
    client.mt5.order_check.return_value = NS(retcode=0, comment='Done')
    result = broker_compatibility(client.mt5, 'EURUSD', 1, 'test')
    assert result['available'] and result['order_sent'] is False
    assert {row['name'] for row in result['checks']} == {
        'BUY_FOK','SELL_FOK','BUY_IOC','SELL_IOC','BUY_LIMIT','SELL_LIMIT','BUY_STOP','SELL_STOP'}
    assert all(row['accepted'] for row in result['checks'])
    assert client.mt5.order_check.call_count == 8
    client.mt5.order_send.assert_not_called()


def test_backtest_uses_closed_bar_next_open_and_never_sends(client):
    rates = []
    for i in range(140):
        price = 1.0 + i*.0001 if i < 70 else 1.014 - i*.0001
        rates.append({'time':i*900, 'open':price, 'high':price+.0005,
                      'low':price-.0005, 'close':price+.0001})
    result = simulate(rates, hma_period=8, ma_type='SMA', ma_period=16,
                      sl_points=100, point=.00001, spread_points=2, commission_points=1)
    assert result['success']
    assert result['in_sample']['count'] + result['out_of_sample']['count'] == len(result['trades'])
    assert all(t['closed_at'] >= t['opened_at'] for t in result['trades'])
    client.mt5.order_send.assert_not_called()


def test_order_events_are_durable_and_replays_do_not_duplicate(tmp_path):
    path = tmp_path / 'orders.sqlite3'
    journal = OrderJournal(path)
    sent = []
    def send():
        sent.append(1)
        return {'success':True, 'ticket':5}
    journal.run('same-id', {'symbol':'EURUSD'}, send)
    OrderJournal(path).run('same-id', {'symbol':'EURUSD'}, send)
    events = OrderJournal(path).events()
    assert len(sent) == 1 and len(events) == 1
    assert events[0]['message'] == 'filled'
