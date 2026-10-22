"""Globe button: widely used MA crossovers tested with the bot's rules on broker candles (2026-10-23).
The fast line can be EMA / SMA / LWMA (user approved); bot, scanner and backtest compute it the same way."""
import base64
import random
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import signal_engine as se, strategy_presets as sp
from app.backtest import simulate
from app.strategy_bot import StrategyBot
from tests.test_bot_auto_trade_e2e import FakeMT5, live_bot, market


def test_fast_line_is_the_same_in_bot_scanner_and_backtest():
    rnd = random.Random(3)
    closes = [2000.0]
    for _ in range(1500):
        closes.append(closes[-1] + rnd.gauss(0, 1))
    rev = list(reversed(closes))
    for fast_type, period in (('EMA', 9), ('SMA', 50), ('LWMA', 12), ('HMA', 14)):
        bot = StrategyBot(Mock())
        bot.fast_ma_type, bot.hma_period = fast_type, period
        series = se.fast_series(closes, {'fast_type': fast_type, 'hma_period': period})
        for shift in range(1, 30):
            assert abs(series[len(closes) - 1 - shift] - bot.calc_fast(rev, shift=shift)) < 1e-9, fast_type


def test_bot_trades_ema_crossovers_like_the_scanner():
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    bot.fast_ma_type, bot.hma_period, bot.second_ma_type, bot.second_ma_period = 'EMA', 9, 'EMA', 21
    for now in range(450, len(rows)):
        client.now = now
        bot._check_strategy_once()
    closes = [r['close'] for r in rows]
    fast, slow = se.ema_series(closes, 9), se.ema_series(closes, 21)
    expected = [(i, se.cross_at(fast, slow, i)) for i in range(450, len(rows) - 1) if se.cross_at(fast, slow, i)]
    assert [(o['candle'] - 1, o['side']) for o in client.opened] == expected and expected


def test_presets_are_ranked_by_history_test_of_bot_rules():
    rows = market(n=1200)
    rows_start = 600
    out = sp.run_presets(rows, rows_start, 0.01, 30, sl_points=0, tp_points=0)
    assert {r['id'] for r in out} == {p['id'] for p in sp.PRESETS}
    nets = [r['net_points'] for r in out if r['available']]
    assert nets == sorted(nets, reverse=True)
    ema = next(r for r in out if r['id'] == 'ema-9-21')
    direct = simulate(rows, 9, 'EMA', 21, 0, 0, 0.01, 30, 0, start=rows_start, fast_type='EMA')
    assert ema['trades'] == direct['total_trades']
    assert ema['net_points'] == round(direct['in_sample']['net_points'] + direct['out_of_sample']['net_points'], 2)


def test_presets_endpoint_uses_broker_candles_and_bot_exits(monkeypatch):
    from app import main
    rows = market(n=3000)
    monkeypatch.setattr(main.mt5_client, 'get_rates', lambda s, tf, n: rows[-n:])
    monkeypatch.setattr(main.mt5_client, 'get_symbol_spec', lambda s: {'point': 0.01})
    monkeypatch.setattr(main.mt5_client, 'get_symbol_price', lambda s: {'spread': 25, 'bid': 4100})
    monkeypatch.setattr(main.bot, 'copy_manual_trades', False)
    monkeypatch.setattr(main.bot, 'use_stop_loss', True)
    monkeypatch.setattr(main.bot, 'sl_points', 300)
    monkeypatch.setattr(main.bot, 'use_take_profit', False)
    main._PRESET_CACHE.clear()
    token = base64.b64encode(b'test:test-panel-password').decode()
    res = TestClient(main.app, headers={'Authorization': 'Basic ' + token}).get(
        '/api/strategy-presets', params={'symbol': 'XAUUSD', 'timeframe_minutes': 60, 'candles': 1000})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body['candles'] == 1000 and body['sl_points'] == 300 and body['tp_points'] == 0
    assert body['exit_source'] == 'bot' and body['spread_points'] == 25
    assert len(body['presets']) == len(sp.PRESETS) and 'garanti' in body['disclaimer']


def test_bot_accepts_fast_type_and_rejects_unknown():
    bot = StrategyBot(Mock())
    bot.update_config({'fast_ma_type': 'EMA', 'hma_period': 9}, persist=False)
    assert bot.fast_ma_type == 'EMA'
    try:
        bot.update_config({'fast_ma_type': 'KAMA'}, persist=False)
        assert False
    except ValueError:
        assert bot.fast_ma_type == 'EMA'
