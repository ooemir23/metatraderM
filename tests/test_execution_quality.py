from types import SimpleNamespace as NS

import pytest

from app.execution_quality import summarize_position
from app.order_journal import OrderJournal
from app.mt5_bridge import MANUAL_MAGIC
from test_trade_safety import client, pos


def sample(side=0, fill_price=1.10003):
    position = dict(type_raw=side, quote=dict(bid=1.10000, ask=1.10015, point=.00001),
                    opening_fills=[dict(ticket=11, order=123, symbol='EURUSD', magic=MANUAL_MAGIC,
                                       type=side, entry=0, volume=.1, price=fill_price)])
    record = dict(order=dict(symbol='EURUSD', magic=MANUAL_MAGIC, type=side), execution=dict(
        kind='market', bid=1.0999, ask=1.1, point=.00001, requested_price=1.1 if side == 0 else 1.0999,
        quote_time=1700000000, deviation_points=20))
    return position, {123:record}


@pytest.mark.parametrize('side,fill,expected', [(0,1.10003,3), (0,1.09998,-2), (0,1.1,0),
                                             (1,1.09987,3), (1,1.09992,-2), (1,1.0999,0)])
def test_slippage_direction_and_spread_are_independent(side, fill, expected):
    position, records = sample(side, fill)
    result = summarize_position(position, records)
    assert result['state'] == 'recorded'
    assert result['slippage_points'] == expected
    assert result['opening_spread_points'] == 10
    assert result['current_spread_points'] == 15
    assert result['spread_change_points'] == 5


def test_multiple_fills_are_volume_weighted():
    position, records = sample()
    position['opening_fills'][0]['volume'] = .01
    position['opening_fills'].append({**position['opening_fills'][0], 'ticket':12, 'volume':.03, 'price':1.10007})
    result = summarize_position(position, records)
    assert result['fill_price'] == pytest.approx(1.10006)
    assert result['slippage_points'] == 6


@pytest.mark.parametrize('change', ['missing','pending','wrong_symbol','wrong_magic','wrong_side','bad_quote','reverse'])
def test_missing_or_incompatible_evidence_never_becomes_zero_slippage(change):
    position, records = sample()
    if change == 'missing': records = {}
    elif change == 'pending': records[123]['execution']['kind'] = 'pending'
    elif change == 'wrong_symbol': records[123]['order']['symbol'] = 'GBPUSD'
    elif change == 'wrong_magic': records[123]['order']['magic'] = 1
    elif change == 'wrong_side': records[123]['order']['type'] = 1
    elif change == 'bad_quote': records[123]['execution']['bid'] = float('nan')
    else: position['opening_fills'][0]['entry'] = 2
    result = summarize_position(position, records)
    assert result['slippage_points'] is None
    assert result['opening_spread_points'] is None
    assert result['current_spread_points'] == 15


def test_missing_current_quote_does_not_erase_recorded_opening_execution():
    position, records = sample()
    position['quote'] = None
    result = summarize_position(position, records)
    assert result['current_spread_points'] is None
    assert result['slippage_points'] == 3


def test_one_unrecorded_fill_prevents_misleading_complete_average():
    position, records = sample()
    position['opening_fills'].append({**position['opening_fills'][0], 'order':999})
    assert summarize_position(position, records)['opening_spread_points'] is None


def test_submission_quote_survives_journal_restart_and_replay(client, tmp_path):
    client.journal = OrderJournal(tmp_path/'orders.db')
    client.mt5.order_send.return_value.price = 1.10003
    result = client.open_order('EURUSD','BUY',.01,request_id='execution-quote-test')
    assert result['execution']['requested_price'] == 1.1
    assert result['execution']['bid'] == 1.0999
    assert result['execution']['ask'] == 1.1
    assert result['execution']['kind'] == 'market'
    client.mt5.symbol_info_tick.return_value.ask = 1.11
    replay = client.open_order('EURUSD','BUY',.01,request_id='execution-quote-test')
    assert replay['replayed'] and replay['execution'] == result['execution']
    client.mt5.order_send.assert_called_once()
    restarted = OrderJournal(tmp_path/'orders.db')
    assert restarted.opening_executions([1,'test'],[123])[123]['execution'] == result['execution']
    assert restarted.opening_executions([2,'test'],[123]) == {}
    assert restarted.opening_executions([1,'another-server'],[123]) == {}


def test_reconciliation_keeps_the_original_quote(client, tmp_path):
    client.journal = OrderJournal(tmp_path/'orders.db')
    client.mt5.order_send.return_value.retcode = 10008
    result = client.open_order('EURUSD','BUY',.01,request_id='execution-pending-test')
    client.journal.resolve('execution-pending-test',dict(success=True,pending=False,price=1.10002,ticket=123))
    assert client.journal.opening_executions([1,'test'],[123])[123]['execution'] == result['execution']


def test_quote_is_returned_when_broker_response_is_uncertain(client):
    client.mt5.order_send.return_value = None
    result = client.open_order('EURUSD','SELL',.01)
    assert result['uncertain']
    assert result['execution']['requested_price'] == 1.0999
    client.mt5.order_send.assert_called_once()


def test_position_api_uses_actual_fills_instead_of_order_result_price(client):
    result = client.open_order('EURUSD','BUY',.01)
    p = pos(magic=MANUAL_MAGIC, side=0)
    client.mt5.positions_get.return_value = [p]
    client.mt5.history_deals_get.return_value = [NS(ticket=9,order=result['ticket'],symbol='EURUSD',
        magic=MANUAL_MAGIC,type=0,entry=0,volume=.01,price=1.10007,commission=-.03)]
    row = client.get_positions(fresh=True,include_commission=True)[0]
    assert row['execution_quality']['slippage_points'] == 7
    assert row['execution_quality']['fill_price'] == 1.10007
    assert row['commission'] == -.03


def test_journal_lookup_failure_keeps_position_and_current_spread(client, monkeypatch):
    client.mt5.positions_get.return_value = [pos()]
    monkeypatch.setattr(client.journal,'opening_executions',lambda *args: (_ for _ in ()).throw(OSError()))
    row = client.get_positions(include_commission=True)[0]
    assert row['ticket'] == 1
    assert row['execution_quality']['slippage_points'] is None
    assert row['execution_quality']['current_spread_points'] == 10
    client.mt5.order_send.assert_not_called()


def test_live_feed_contains_the_same_execution_measurements(client):
    client.open_order('EURUSD','SELL',.01)
    client.mt5.positions_get.return_value = [pos(magic=MANUAL_MAGIC,side=1)]
    client.mt5.history_deals_get.return_value = [NS(ticket=9,order=123,symbol='EURUSD',
        magic=MANUAL_MAGIC,type=1,entry=0,volume=.01,price=1.09987,commission=-.03)]
    account=client.mt5.account_info.return_value
    for field in ('balance','equity','profit','margin','margin_free','margin_level','leverage'):
        setattr(account,field,1000)
    row=client.get_live_snapshot(['EURUSD'])['positions'][0]
    assert row['execution_quality']['slippage_points'] == 3
    assert row['execution_quality']['current_spread_points'] == 10
    # Quote is read once per symbol and shared by positions and the chart snapshot.
    assert client.mt5.symbol_info_tick.call_count == 3  # request, post-check quote verification, shared snapshot


def test_pending_placement_quote_is_not_presented_as_trigger_spread(client):
    info=client.mt5.symbol_info.return_value
    info.order_mode,info.expiration_mode=127,15
    result=client.open_order('EURUSD','BUY',.01,pending_type='BUY_LIMIT',entry_price=1.09)
    assert result['execution']['kind'] == 'pending'
    assert result['execution']['requested_price'] == 1.09
    client.mt5.positions_get.return_value=[pos(magic=MANUAL_MAGIC,side=0)]
    client.mt5.history_deals_get.return_value=[NS(ticket=9,order=123,symbol='EURUSD',
        magic=MANUAL_MAGIC,type=0,entry=0,volume=.01,price=1.09,commission=-.03)]
    row=client.get_positions(include_commission=True)[0]
    assert row['execution_quality']['state'] == 'pending_quote_missing'
    assert row['execution_quality']['opening_spread_points'] is None


def test_account_change_during_positions_read_rejects_mixed_data(client):
    account=client.mt5.account_info.return_value
    client.mt5.account_info.side_effect=[account,NS(login=42,server='test')]
    from app.mt5_client import MT5DataError
    with pytest.raises(MT5DataError):
        client.get_positions(include_commission=True)


def test_closed_position_details_reuse_persisted_submission_quote(client, monkeypatch):
    from test_history_direction import detail_deal
    opening = detail_deal(1, 0, 1, 5, 1780000000)
    opening.magic = MANUAL_MAGIC
    client.mt5.history_deals_get.return_value = [opening, detail_deal(2, 1, 0, 5, 1791550590)]
    from unittest.mock import Mock
    records = Mock(return_value={101: dict(order=dict(symbol='XAUUSD', magic=MANUAL_MAGIC, type=1),
        execution=dict(kind='market', bid=4178.40, ask=4178.60, point=.01, requested_price=4178.40))})
    monkeypatch.setattr(client.journal, 'opening_executions', records)
    quality = client.get_position_details(11)['execution_quality']
    records.assert_called_once_with([1, 'test'], [101])
    assert quality['state'] == 'recorded'
    assert quality['opening_spread_points'] == 20
    assert quality['slippage_points'] == 3
    assert quality['fill_price'] == 4178.37
    assert quality['current_spread_points'] is None
    client.mt5.order_send.assert_not_called()


def test_closed_position_details_keep_missing_quotes_unknown(client):
    from test_history_direction import detail_deal
    client.mt5.history_deals_get.return_value = [detail_deal(1, 0, 1, 5, 1780000000)]
    quality = client.get_position_details(11)['execution_quality']
    assert quality['state'] == 'unrecorded'
    assert quality['opening_spread_points'] is None
    assert quality['slippage_points'] is None
