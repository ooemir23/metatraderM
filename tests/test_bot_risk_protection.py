"""Risk-% lot sizing, protection rules (hours, blocked windows, loss streak) and bot performance."""
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app import bot_performance, mt5_bridge
from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot

ACCOUNT = [7, 'demo', 'DEMO']


# ---------------- risk lot (bridge) ----------------
def broker(equity=10000., loss_per_lot=200., step=.01, minimum=.01, maximum=50.):
    mt5 = Mock()
    mt5.account_info.return_value = NS(login=7, server='demo', equity=equity, currency='USD', trade_mode=0)
    mt5.symbol_info.return_value = NS(point=.00001, digits=5, volume_step=step, volume_min=minimum, volume_max=maximum,
                                      trade_stops_level=0, filling_mode=3, trade_exemode=2, trade_tick_size=.00001)
    mt5.symbol_info_tick.return_value = NS(ask=1.1, bid=1.0999, time=time.time())
    mt5.order_calc_profit.return_value = -loss_per_lot
    mt5.terminal_info.return_value = NS(connected=True)
    return mt5


def test_risk_lot_uses_broker_loss_and_rounds_down():
    # 1% of 10 000 = 100; 200 per lot at the stop -> 0.5 lot
    result = mt5_bridge.risk_lot(broker(), 'EURUSD', 'BUY', 200, 1.0, 7, 'demo')
    assert result['success'] and result['lot'] == .5 and result['risk_money'] == 100.
    # 0.7% -> 70 / 200 = 0.35 ; 0.33% -> 0.165 -> rounds DOWN to 0.16
    assert mt5_bridge.risk_lot(broker(), 'EURUSD', 'SELL', 200, .33, 7, 'demo')['lot'] == .16


def test_risk_lot_never_rounds_up_to_minimum():
    result = mt5_bridge.risk_lot(broker(equity=100., loss_per_lot=500.), 'XAUUSD', 'BUY', 300, .5, 7, 'demo')
    assert not result['success'] and 'en küçük lot' in result['error']


def test_risk_lot_requires_stop_and_matching_account():
    assert not mt5_bridge.risk_lot(broker(), 'EURUSD', 'BUY', 0, 1, 7, 'demo')['success']
    assert not mt5_bridge.risk_lot(broker(), 'EURUSD', 'BUY', 200, 1, 8, 'demo')['success']


def test_risk_lot_clamped_to_broker_maximum():
    assert mt5_bridge.risk_lot(broker(equity=1e7, maximum=5.), 'EURUSD', 'BUY', 200, 1, 7, 'demo')['lot'] == 5.


# ---------------- bot sizing ----------------
def bot_with_client():
    client = Mock(login_id=7, server='demo', account_type='DEMO', max_order_lots=.1)
    client.has_unresolved_bot_order.return_value = False
    client.get_symbol_spec.return_value = {'point': .00001}
    client.open_order.return_value = {'success': True, 'ticket': 42, 'price': 1.1}
    return StrategyBot(client), client


def test_order_lot_fixed_and_risk_with_cap():
    bot, client = bot_with_client()
    assert bot.order_lot('EURUSD', 'BUY', 200) == (.01, None, None)
    bot.lot_mode, bot.risk_pct = 'RISK', 1.0
    client.risk_lot.return_value = {'success': True, 'lot': .5, 'risk_money': 100.}
    assert bot.order_lot('EURUSD', 'BUY', 200, cap=.1) == (.1, 20., None), 'automation cap lowers lot and risk'
    client.risk_lot.return_value = {'success': False, 'error': 'yetersiz'}
    assert bot.order_lot('EURUSD', 'BUY', 200)[2] == 'yetersiz'


def test_confirmed_signal_uses_risk_lot_and_records_risk():
    bot, client = bot_with_client()
    bot.lot_mode, bot.risk_pct = 'RISK', .5
    client.risk_lot.return_value = {'success': True, 'lot': .3, 'risk_money': 50.}
    bot.execute_signal('EURUSD', 'BUY', signal_id='r1', expected_account=ACCOUNT, sl_points=250)
    assert client.open_order.call_args.args[2] == .3
    assert client.risk_lot.call_args.args[:4] == ('EURUSD', 'BUY', 250, .5)
    assert bot.signal_history[0]['risk_money'] == 50.


def test_confirmed_signal_rejected_when_risk_lot_impossible():
    bot, client = bot_with_client()
    bot.lot_mode = 'RISK'
    client.risk_lot.return_value = {'success': False, 'error': 'en küçük lotu karşılamıyor'}
    with pytest.raises(ValueError):
        bot.execute_signal('EURUSD', 'BUY', signal_id='r2', expected_account=ACCOUNT, sl_points=250)
    client.open_order.assert_not_called()
    assert bot.signal_history[0]['success'] is False


# ---------------- config validation ----------------
def test_protection_settings_validated_and_persisted(tmp_path):
    bot = StrategyBot(Mock(), config_path=tmp_path / 'bot.json')
    bot.update_config({'lot_mode': 'RISK', 'risk_pct': .75, 'max_consecutive_losses': 3, 'trade_hours_enabled': True,
                       'trade_start': '09:00', 'trade_end': '18:30', 'trade_days': [4, 0, 1, 1],
                       'blocked_windows': ['15:25-15:45', '2026-11-06 15:20-15:50']})
    assert bot.trade_days == [0, 1, 4] and bot.config_snapshot()['risk_pct'] == .75
    for bad in ({'risk_pct': 9}, {'lot_mode': 'X'}, {'trade_start': '25:00'}, {'trade_days': []},
                {'blocked_windows': ['yarın öğlen']}, {'max_consecutive_losses': 50},
                {'lot_mode': 'RISK', 'use_stop_loss': False}):
        with pytest.raises(ValueError):
            bot.update_config(bad)
    assert bot.risk_pct == .75, 'invalid update leaves settings unchanged'


# ---------------- trading window ----------------
def test_trading_hours_days_and_blocked_windows():
    bot = StrategyBot(Mock())
    friday_10 = datetime(2026, 11, 6, 7, 0, tzinfo=timezone.utc)   # 10:00 Istanbul, Friday
    assert bot.trading_window_issue(friday_10) is None, 'defaults never block'
    bot.trade_hours_enabled, bot.trade_start, bot.trade_end = True, '09:00', '18:00'
    assert bot.trading_window_issue(friday_10) is None
    assert 'saatleri dışında' in bot.trading_window_issue(datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc))  # 19:00
    bot.trade_start, bot.trade_end = '22:00', '02:00'  # overnight session
    assert bot.trading_window_issue(datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc)) is None  # 23:30
    bot.trade_hours_enabled = False
    bot.trade_days = [0, 1, 2, 3]
    assert 'Cuma' in bot.trading_window_issue(friday_10)
    bot.trade_days = list(range(7))
    bot.blocked_windows = ['2026-11-06 09:50-10:10']
    assert 'Engelli' in bot.trading_window_issue(friday_10)
    assert bot.trading_window_issue(datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)) is None, 'dated window only that day'
    bot.blocked_windows = ['09:55-10:05']
    assert 'Engelli' in bot.trading_window_issue(datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)), 'daily window'


def test_automatic_entry_skipped_outside_hours():
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    client.ensure_connected.return_value = True
    client.get_rates.return_value = [{'time': i * 900, 'close': 1.0} for i in range(100)]
    client.get_positions.return_value = []
    client.get_pending_orders.return_value = []
    client.has_unresolved_bot_order.return_value = False
    bot = StrategyBot(client)
    bot.calc_hma = Mock(side_effect=[2., 0.])
    bot.calc_second_ma = Mock(return_value=1.)
    bot.last_candle_time = 1
    bot.trade_days = [((datetime.now(timezone.utc).weekday()) + 1) % 7]  # not today
    bot._check_strategy()
    client.open_order.assert_not_called()
    assert any('işlem günü değil' in entry['message'] for entry in bot.logs)


# ---------------- performance + loss streak ----------------
def deal(pid, entry, profit, ts, magic=HMA_MAGIC, symbol='EURUSD', ptype='BUY'):
    return {'position_id': pid, 'entry': entry, 'profit': profit, 'commission': -.5, 'swap': 0, 'fee': 0,
            'timestamp': ts, 'magic': magic, 'symbol': symbol, 'position_type': ptype}


def sample_deals():
    return [deal(1, 0, 0, 100), deal(1, 1, 40, 200),
            deal(2, 0, 0, 300, symbol='XAUUSD'), deal(2, 1, -20, 400, symbol='XAUUSD'),
            deal(3, 0, 0, 500), deal(3, 1, -10, 600),
            deal(4, 0, 0, 700, magic=0), deal(4, 1, 99, 800, magic=0),   # manual, not the bot
            deal(5, 0, 0, 900)]                                          # still open


def test_closed_positions_filter_and_r_multiple():
    history = [{'ticket': 3, 'success': True, 'risk_money': 21.0, 'source': 'MANUAL', 'symbol': 'EURUSD', 'side': 'BUY', 'time': 500}]
    rows = bot_performance.closed_positions(sample_deals(), history, open_position_ids=[5])
    assert [r['position_id'] for r in rows] == [1, 2, 3]
    assert rows[0]['net'] == 39.0 and rows[2]['r'] == round(-11 / 21, 2) and rows[2]['source'] == 'MANUAL'


def test_summary_metrics():
    rows = bot_performance.closed_positions(sample_deals(), (), open_position_ids=[5])
    s = bot_performance.summarize(rows, 180)
    assert (s['closed_positions'], s['wins'], s['losses']) == (3, 1, 2)
    assert s['net'] == 39 - 21 - 11 and s['profit_factor'] == round(39 / 32, 2)
    assert s['max_drawdown'] == 32 and s['current_loss_streak'] == 2
    assert s['by_symbol']['XAUUSD']['count'] == 1 and s['early_sample'] is True


def test_loss_streak_stops_bot():
    client = Mock()
    client.get_history.return_value = sample_deals()
    client.get_positions.return_value = [{'ticket': 5, 'identifier': 5}]
    bot = StrategyBot(client)
    bot.max_consecutive_losses, bot.is_running, bot.started_at = 2, True, 150
    assert bot.check_loss_streak(force=True) == 2
    assert not bot.is_running and bot.runtime_state == 'LOSS_STREAK'


def test_loss_streak_counts_only_since_start():
    client = Mock()
    client.get_history.return_value = sample_deals()
    client.get_positions.return_value = []
    bot = StrategyBot(client)
    bot.max_consecutive_losses, bot.is_running, bot.started_at = 2, True, 550   # only position 3 after start
    assert bot.check_loss_streak(force=True) == 1 and bot.is_running


def test_performance_endpoint(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.mt5_client, 'get_history', Mock(return_value=sample_deals()))
    monkeypatch.setattr(main.mt5_client, 'get_positions', Mock(return_value=[{'ticket': 5, 'identifier': 5}]))
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    data = web.get('/api/bot/performance', params={'days': 30}).json()
    assert data['closed_positions'] == 3 and data['days'] == 30 and 'by_hour' in data
    monkeypatch.setattr(main.mt5_client, 'get_history', Mock(side_effect=RuntimeError('offline')))
    assert web.get('/api/bot/performance').status_code == 503
    assert web.get('/api/bot/performance', params={'days': 999}).status_code == 422


def test_bot_config_api_accepts_protection_fields(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.bot, 'persist_config', lambda data: None)
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    ok = web.post('/api/bot/config', json={'max_consecutive_losses': 3, 'trade_hours_enabled': True, 'trade_start': '09:00',
                                           'trade_end': '18:00', 'trade_days': [0, 1, 2, 3, 4], 'blocked_windows': ['15:25-15:45']})
    assert ok.status_code == 200 and ok.json()['max_consecutive_losses'] == 3 and ok.json()['trade_days'] == [0, 1, 2, 3, 4]
    assert web.post('/api/bot/config', json={'trade_start': '9am'}).status_code == 422
    assert web.post('/api/bot/config', json={'risk_pct': 20}).status_code == 422
    web.post('/api/bot/config', json={'max_consecutive_losses': 0, 'trade_hours_enabled': False, 'trade_days': [0, 1, 2, 3, 4, 5, 6], 'blocked_windows': []})


# ---------------- per-symbol "why no trade" status ----------------
def status_bot(cross):
    client = Mock(login_id=7, server='demo', account_type='DEMO')
    client.ensure_connected.return_value = True
    client.get_rates.return_value = [{'time': i * 900, 'close': 1.0} for i in range(100)]
    client.get_pending_orders.return_value = []
    client.has_unresolved_bot_order.return_value = False
    bot = StrategyBot(client)
    bot.calc_hma = Mock(side_effect=lambda closes, period, shift=0: 2. if (cross and shift == 1) else 0.5)
    bot.calc_second_ma = Mock(return_value=1.)
    bot.last_candle_time = 1
    return bot, client


def test_status_explains_waiting_for_next_crossover():
    bot, client = status_bot(cross=False)
    bot._check_strategy()
    row = bot.get_status()['symbol_status']['EURUSD']
    assert row['state'] == 'WAITING_CROSS' and 'AL (yukarı)' in row['detail']
    client.open_order.assert_not_called()


def test_status_explains_existing_bot_position():
    bot, client = status_bot(cross=True)
    client.get_positions.return_value = [{'symbol': 'EURUSD', 'magic': HMA_MAGIC, 'type': 'BUY', 'ticket': 9}]
    bot.close_opposite = False
    bot._check_strategy()
    row = bot.get_status()['symbol_status']['EURUSD']
    assert row['state'] == 'SKIPPED' and 'aynı yönde bir bot pozisyonu' in row['detail']
    client.open_order.assert_not_called()


def test_status_reports_opened_and_rejected_orders():
    bot, client = status_bot(cross=True)
    client.get_positions.return_value = []
    client.open_order.return_value = {'success': False, 'error': 'Piyasa kapalı'}
    bot._check_strategy()
    row = bot.get_status()['symbol_status']['EURUSD']
    assert row['state'] == 'REJECTED' and 'Piyasa kapalı' in row['detail']


def test_bot_start_refuses_lot_above_automation_cap_instead_of_shrinking(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.bot, 'persist_config', lambda data: None)
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    readiness = Mock(return_value={'success': True, 'account': [main.mt5_client.login_id, main.mt5_client.server, 'DEMO']})
    monkeypatch.setattr(main.mt5_client, 'bot_readiness', readiness)
    monkeypatch.setattr(main.mt5_client, 'max_order_lots', .1)
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    assert web.post('/api/bot/config', json={'lot_size': 1.0, 'lot_mode': 'FIXED'}).status_code == 200
    res = web.post('/api/bot/toggle')
    assert res.status_code == 409 and 'Lot ve risk' in res.json()['detail']
    assert main.bot.lot_size == 1.0 and not main.bot.is_running, 'lot is never silently reduced'
    readiness.assert_not_called()
    assert main.bot.get_status()['max_order_lots'] == .1
    web.post('/api/bot/config', json={'lot_size': .01})


def test_lot_limits_can_be_changed_from_the_panel_and_persist(monkeypatch, tmp_path):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    from app.mt5_client import MT5Client
    monkeypatch.setattr(main.mt5_client, 'account_type', 'DEMO')
    original = main.mt5_client.lot_limits()
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    try:
        res = web.post('/api/risk/lot-limits', json={'max_order_lots': 1.0, 'max_total_lots': 3.0})
        assert res.status_code == 200 and res.json()['max_order_lots'] == 1.0
        assert main.mt5_client.max_order_lots == 1.0 and main.bot.get_status()['max_total_lots'] == 3.0
        assert web.get('/api/risk/lot-limits').json()['max_open_orders'] == original['max_open_orders']
        # Persisted: a fresh client (restart) loads the panel values instead of the env defaults.
        monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
        assert MT5Client().max_order_lots == 1.0
        # Validation: total below per-order, out of range, unknown field, nothing to change.
        assert web.post('/api/risk/lot-limits', json={'max_total_lots': .5}).status_code == 422
        assert web.post('/api/risk/lot-limits', json={'max_order_lots': 500}).status_code == 422
        assert web.post('/api/risk/lot-limits', json={'max_lots': 1}).status_code == 422
        assert web.post('/api/risk/lot-limits', json={}).status_code == 422
        assert main.mt5_client.max_total_lots == 3.0, 'rejected change leaves limits unchanged'
    finally:
        main.mt5_client.set_lot_limits(original)


def test_lot_limits_need_second_factor_on_real_account(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.mt5_client, 'account_type', 'REAL')
    before = main.mt5_client.max_order_lots
    token = base64.b64encode(b'test:test-panel-password').decode()
    web = TestClient(main.app, headers={'Authorization': 'Basic ' + token})
    assert web.post('/api/risk/lot-limits', json={'max_order_lots': 2.0, 'max_total_lots': 5.0}).status_code == 403
    assert main.mt5_client.max_order_lots == before
