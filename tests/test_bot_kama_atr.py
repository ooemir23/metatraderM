from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app.strategy_bot import StrategyBot
from app.mt5_bridge import HMA_MAGIC


def bars(count=250):
    return [dict(time=1700000000+i*3600, open=100, high=102, low=98, close=100) for i in range(count)]


def client(rows):
    c = Mock(login_id=123, server='demo')
    c.ensure_connected.return_value = True
    c.get_rates.return_value = rows
    c.get_positions.return_value = []
    c.get_pending_orders.return_value = []
    c.has_unresolved_bot_order.return_value = False
    c.get_symbol_spec.return_value = {'point': .01}
    c.open_order.return_value = {'success': True, 'ticket': 12, 'price': 100}
    c.close_position.return_value = {'success': True}
    return c


def install_study(monkeypatch, points):
    # Chronological indicator fixture indexed by closed-bar time.
    def study(rows, settings):
        values = [points.get(r['time'], (99, 100)) for r in rows]
        return {'hma': [v[0] for v in values], 'kama': [v[1] for v in values],
                'er': [.5] * len(rows), 'atr': [2.25] * len(rows)}
    monkeypatch.setattr('app.strategy_bot.indicators', study)


def configured(c, **fields):
    b = StrategyBot(c)
    b.update_config(dict(second_ma_type='KAMA', second_ma_period=10, use_atr_filter=True,
        min_distance_atr=.1, confirmation_bars=2, risk_mode='ATR', use_take_profit=True, **fields))
    return b


def test_confirmation_wait_then_atr_order_and_no_duplicate(monkeypatch):
    rows = bars()
    t = rows[-2]['time']
    install_study(monkeypatch, {t-3600: (99, 99.9), t: (100.1, 100), t+3600: (100.4, 100.1)})
    c = client(rows); b = configured(c)
    b._check_strategy()
    assert b.get_status()['pending_confirmation']
    c.open_order.assert_not_called()
    c.get_rates.return_value = bars(251)
    b._check_strategy()
    assert not b.get_status()['pending_confirmation']
    c.open_order.assert_called_once()
    args = c.open_order.call_args
    assert args.args[1] == 'BUY'
    assert args.kwargs['sl_points'] == 450
    assert args.kwargs['tp_points'] == 675
    assert args.kwargs['magic'] == HMA_MAGIC
    b._check_strategy()
    c.open_order.assert_called_once()


def test_missed_bars_expire_candidate_even_when_latest_passes(monkeypatch):
    rows = bars(); t = rows[-2]['time']
    install_study(monkeypatch, {t-3600: (99, 99.9), t: (100.1, 100),
        t+3600: (100.2, 100.1), t+7200: (100.6, 100.2)})
    c = client(rows); b = configured(c)
    b.confirmation_bars = 1
    b._check_strategy()
    c.get_rates.return_value = bars(252)
    b._check_strategy()
    assert not b.get_status()['pending_confirmation']
    c.open_order.assert_not_called()


def test_reverse_closes_only_owned_positions_even_without_entry_confirmation(monkeypatch):
    rows=bars();t=rows[-2]['time']
    install_study(monkeypatch, {t-3600:(99,99.9),t:(100.1,100)})
    c=client(rows); b=configured(c);b.confirmation_bars=0
    c.get_positions.return_value=[{'ticket':1,'symbol':'EURUSD','type':'SELL','magic':HMA_MAGIC},
        {'ticket':2,'symbol':'EURUSD','type':'SELL','magic':77}]
    b._check_strategy()
    c.close_position.assert_called_once_with(1,expected_magic=HMA_MAGIC)
    c.open_order.assert_not_called()


def test_forming_candle_cannot_change_kama_atr_or_er():
    rows=bars(); c=client(rows); b=configured(c);b._check_strategy()
    original=(b.current_hma,b.current_ma2,b.current_atr,b.current_er)
    changed=[dict(r) for r in rows]
    changed[-1].update(open=2000,close=3000,high=4000,low=1000)
    other=configured(client(changed));other._check_strategy()
    assert original == (other.current_hma,other.current_ma2,other.current_atr,other.current_er)


def test_atr_point_failure_blocks_order_and_stop_clears_candidate(monkeypatch):
    rows=bars();t=rows[-2]['time']
    install_study(monkeypatch,{t-3600:(99,99.9),t:(101,100)})
    c=client(rows);b=configured(c)
    c.get_symbol_spec.return_value={'point':0}
    b._check_strategy();c.open_order.assert_not_called()
    assert any('ATR stop/hedef' in l['message'] for l in b.logs)
    b._candidate={'side':1,'time':t};b.stop()
    assert b._candidate is None


def test_api_kama_settings_validate_without_starting_or_mutating_on_error(monkeypatch):
    import app.main as main
    b=StrategyBot(client(bars()));monkeypatch.setattr(main,'bot',b)
    web=TestClient(main.app);auth=('test','test-panel-password')
    config={'second_ma_type':'KAMA','second_ma_period':10,'timeframe_minutes':60,
        'kama_fast':2,'kama_slow':30,'atr_period':14,'use_atr_filter':True,'er_min':.3,
        'confirmation_bars':2,'min_distance_atr':.1,'risk_mode':'ATR','atr_stop_multiplier':2,
        'atr_target_multiplier':3,'use_take_profit':True}
    response=web.post('/api/bot/config',auth=auth,json=config)
    assert response.status_code==200,response.text
    assert all(response.json()[k]==v for k,v in config.items())
    assert response.json()['is_running'] is False
    for bad in ({'kama_fast':30},{'confirmation_bars':4},{'er_min':1.1},{'risk_mode':'INVALID'}, {'atr_stop_multiplier':0}):
        previous=b.get_status()
        assert web.post('/api/bot/config',auth=auth,json=bad).status_code==422
        assert b.get_status()==previous
    b.is_running=True
    assert web.post('/api/bot/config',auth=auth,json=config).status_code==409


def test_h4_filter_uses_closed_bars_and_rejects_stale_alignment(monkeypatch):
    rows=bars();t=rows[-2]['time'];decision=t+3600
    higher=[dict(rows[0],time=decision-28800),dict(rows[0],time=decision-14400),dict(rows[0],time=decision,close=9999)]
    def study(data, settings):
        if len(data)==2:
            assert all(r['time'] < decision for r in data)
            return {'hma':[100,101],'kama':[99,100],'atr':[2.25,2.25],'er':[.5,.5]}
        values=[(99,99.9) if r['time'] < t else (101,100) for r in data]
        return {'hma':[v[0] for v in values],'kama':[v[1] for v in values],'atr':[2.25]*len(data),'er':[.5]*len(data)}
    monkeypatch.setattr('app.strategy_bot.indicators',study)
    c=client(rows);c.get_rates.side_effect=lambda symbol,timeframe,count: higher if timeframe==240 else rows
    b=configured(c);b.timeframe_minutes=60;b.use_h4_filter=True;b.confirmation_bars=0
    b._check_strategy();c.open_order.assert_called_once()
    higher[:]=[dict(r,time=r['time']-14400) for r in higher[:-1]]
    other=configured(c);other.timeframe_minutes=60;other.use_h4_filter=True;other.confirmation_bars=0;c.open_order.reset_mock()
    other._check_strategy();c.open_order.assert_not_called()


def test_single_position_and_unsettled_order_block_new_entries(monkeypatch):
    rows=bars();t=rows[-2]['time'];install_study(monkeypatch,{t-3600:(99,99.9),t:(101,100)})
    c=client(rows);b=configured(c)
    c.get_positions.return_value=[{'symbol':'EURUSD','magic':HMA_MAGIC,'type':'BUY','ticket':1}]
    b._check_strategy();c.open_order.assert_not_called()
    c.get_positions.return_value=[];c.has_unresolved_bot_order.return_value=True
    other=configured(c);other._check_strategy();c.open_order.assert_not_called();assert other._stop_event.is_set()
    c.has_unresolved_bot_order.return_value=False;c.open_order.return_value={'success':False,'uncertain':True}
    other=configured(c);other._check_strategy();assert other._stop_event.is_set()


def test_startup_seeds_bar_instead_of_trading_old_signal(monkeypatch):
    rows=bars();t=rows[-2]['time'];install_study(monkeypatch,{t-3600:(99,99.9),t:(101,100)})
    c=client(rows);b=configured(c);b.is_running=True
    b._check_strategy();assert b.last_candle_time==t;c.open_order.assert_not_called()


def test_saved_settings_restore_stopped_and_failed_write_preserves_settings(monkeypatch,tmp_path):
    import json
    import app.main as main
    path=tmp_path/'bot_settings.json';b=StrategyBot(client(bars()),config_path=path)
    config={'symbol':'XAUUSD','timeframe_minutes':60,'second_ma_type':'KAMA','use_atr_filter':True,'use_h4_filter':True,'lot_size':.001}
    b.update_config(config);assert json.loads(path.read_text())['lot_size']==.001
    restarted=StrategyBot(client(bars()),config_path=path);monkeypatch.setattr(main,'bot',restarted)
    main.restore_bot_settings();assert restarted.use_h4_filter and restarted.symbol=='XAUUSD' and not restarted.is_running
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(restarted,'persist_config',fail)
    previous=restarted.config_snapshot();web=TestClient(main.app)
    assert web.post('/api/bot/config',auth=('test','test-panel-password'),json={'lot_size':.1}).status_code==503
    assert restarted.config_snapshot()==previous
    assert web.post('/api/bot/config',auth=('test','test-panel-password'),json={'timeframe_minutes':240}).status_code==422
