from app import health
from test_trade_safety import client  # noqa: F401  (fixture)


def base(**kw):
    ctx = {'connected': True, 'account_type': 'DEMO', 'now': 10_000, 'trading_halted': False,
           'quote': {'symbol': 'XAUUSD', 'state': 'fresh', 'age_seconds': 1},
           'limits': {'max_total_lots': 31, 'max_order_lots': 10, 'max_open_orders': 10},
           'positions': [], 'bot': {'is_running': True, 'symbol': 'XAUUSD', 'lot_size': 5, 'use_stop_loss': True, 'symbol_status': {}}}
    ctx.update(kw)
    return ctx


def ids(ctx):
    return {f['id'] for f in health.evaluate(ctx)}


def test_healthy_system_has_no_findings():
    assert health.evaluate(base()) == [] and health.overall([]) == 'ok'


def test_connection_halt_and_quote_findings():
    assert 'mt5_down' in ids(base(connected=False))
    assert 'trading_halted' in ids(base(trading_halted=True))
    assert 'quote_bad' in ids(base(quote={'symbol': 'X', 'state': 'missing', 'age_seconds': None}))
    assert 'quote_stale' in ids(base(quote={'symbol': 'X', 'state': 'stale', 'age_seconds': 3600}))
    assert 'quote_stale' not in ids(base(quote={'symbol': 'X', 'state': 'stale', 'age_seconds': 60}))


def test_limits_and_lot_cap():
    positions = [{'volume': 10}, {'volume': 10}, {'volume': 10}]
    assert 'lots_near_limit' in ids(base(positions=positions))
    assert 'count_at_limit' in ids(base(positions=[{'volume': .1}] * 10))
    bot = {'is_running': True, 'symbol': 'XAUUSD', 'lot_size': 20, 'use_stop_loss': True, 'symbol_status': {}}
    assert 'lot_over_cap' in ids(base(bot=bot))
    assert 'lot_over_cap' not in ids(base(bot={**bot, 'auto_lot_limits_demo': True, 'copy_manual_trades': True}))


def test_bot_findings():
    status = {'XAUUSD': {'state': 'SKIPPED', 'detail': 'aynı yönde pozisyon açık (#9)', 'updated': 9_990}}
    found = health.evaluate(base(bot={'is_running': True, 'symbol': 'XAUUSD', 'lot_size': 5, 'use_stop_loss': True, 'symbol_status': status}))
    assert any(f['id'] == 'skipped:XAUUSD' and '#9' in f['detail'] for f in found)
    stalled = {'XAUUSD': {'state': 'WAITING_SIGNAL', 'detail': '', 'updated': 10_000 - 3600}}
    assert 'bot_stalled' in ids(base(bot={'is_running': True, 'symbol': 'XAUUSD', 'symbol_status': stalled}))
    # heartbeat present: an old status text alone is not a stall; a stopped heartbeat is
    assert 'bot_stalled' not in ids(base(bot={'is_running': True, 'symbol': 'XAUUSD', 'symbol_status': stalled, 'loop_at': 9_999}))
    assert 'bot_stalled' in ids(base(bot={'is_running': True, 'symbol': 'XAUUSD', 'symbol_status': stalled, 'loop_at': 10_000 - 600}))
    assert 'bot_error' in ids(base(bot={'is_running': False, 'last_error': 'boom'}))
    assert 'order_unresolved' in ids(base(bot={'is_running': True, 'runtime_state': 'ORDER_UNRESOLVED', 'symbol_status': {}}))
    assert 'session_target' in ids(base(bot={'is_running': False, 'runtime_state': 'PROFIT_TARGET', 'symbol_status': {}}))


def test_real_account_without_stop_loss_is_an_error():
    found = health.evaluate(base(account_type='REAL', bot={'is_running': False, 'use_stop_loss': False}))
    assert any(f['id'] == 'real_no_sl' and f['level'] == 'error' for f in found) and health.overall(found) == 'error'


def test_api_health_returns_findings_without_changing_anything(client, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.strategy_bot import StrategyBot
    monkeypatch.setattr(main, 'mt5_client', client)
    monkeypatch.setattr(main, 'bot', StrategyBot(client))
    response = TestClient(main.app).get('/api/health', auth=('test', 'test-panel-password'))
    assert response.status_code == 200 and response.json()['level'] in ('ok', 'warn', 'error')
    assert isinstance(response.json()['findings'], list)
    client.mt5.order_send.assert_not_called()
