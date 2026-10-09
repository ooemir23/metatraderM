import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.mt5_client import MT5Client, NATIVE_HISTORY_SCRIPT
from app.mt5_bridge import position_details
from app.mt5_client import MT5DataError
from test_trade_safety import client, authenticated_web


@pytest.mark.parametrize('transport', ['direct', 'native'])
@pytest.mark.parametrize('deal_type', [0, 1])
@pytest.mark.parametrize('entry', [0, 1, 2, 3])
def test_history_distinguishes_position_direction_from_deal_direction(
        monkeypatch, transport, deal_type, entry):
    mt5 = Mock()
    mt5.history_deals_get.return_value = [NS(
        ticket=292611642, order=99, position_id=348837969,
        symbol='XAUUSD', type=deal_type, entry=entry, magic=123462,
        volume=5, price=4171.2, profit=3585 if entry else 0,
        commission=-15, swap=0, fee=0, time=1791550590,
    )]
    if transport == 'native':
        monkeypatch.setitem(sys.modules, 'MetaTrader5', mt5)
        namespace = {}
        exec(NATIVE_HISTORY_SCRIPT, namespace)
        rows = namespace['hma_native_get_history'](include_ai_entries=True)
    else:
        monkeypatch.setattr(MT5Client, 'load_credentials', lambda self: None)
        client = MT5Client()
        client.mt5 = mt5
        monkeypatch.setattr(client, 'ensure_connected', lambda: True)
        rows = client.get_history(include_ai_entries=True)

    row, = rows
    assert row['type'] == ('BUY' if deal_type == 0 else 'SELL')
    position_type = deal_type if entry == 0 else 1 - deal_type
    assert row['position_type'] == ('BUY' if position_type == 0 else 'SELL')
    assert row['entry'] == entry
    assert row['position_id'] == 348837969
    assert row['price'] == 4171.2
    assert row['commission'] == -15
    assert row['profit'] == (3585 if entry else 0)
    mt5.order_send.assert_not_called()


def detail_deal(ticket, entry, side, volume, time, profit=0, position_id=11):
    return NS(ticket=ticket, position_id=position_id, order=ticket + 100,
              symbol='XAUUSD', type=side, entry=entry, time=time,
              time_msc=time * 1000, volume=volume, price=4178.37 if entry == 0 else 4171.2,
              profit=profit, commission=-15, swap=-2 if entry else 0,
              fee=-1, comment='broker fill')


def test_details_read_entire_lifetime_with_partial_closes_and_precision(client):
    c = client
    c.mt5.symbol_info.return_value.digits = 2
    c.mt5.history_deals_get.return_value = [
        detail_deal(3, 1, 0, 3, 1791550590, 2151),
        detail_deal(1, 0, 1, 5, 1780000000),  # opening is outside the list's 30-day range
        detail_deal(2, 1, 0, 2, 1791500000, 1434),
    ]
    result = c.get_position_details(11)
    assert result['digits'] == 2 and result['currency'] == 'EUR'
    assert [d['ticket'] for d in result['deals']] == [1, 2, 3]
    assert result['deals'][0]['price'] == 4178.37
    assert result['deals'][0]['type'] == 1
    assert result['deals'][-1]['type'] == 0
    assert result['deals'][-1]['commission'] == -15
    assert result['deals'][-1]['comment'] == 'broker fill'
    c.mt5.history_deals_get.assert_called_once_with(position=11)
    c.mt5.order_send.assert_not_called()


def test_details_block_account_mismatch_before_history_read(client):
    client.mt5.account_info.return_value.login = 42
    with pytest.raises(MT5DataError):
        client.get_position_details(11)
    client.mt5.history_deals_get.assert_not_called()


def test_details_block_account_change_during_read(client):
    old = client.mt5.account_info.return_value
    client.mt5.account_info.side_effect = [old, NS(login=42, server='test')]
    with pytest.raises(RuntimeError):
        position_details(client.mt5, 11, [1, 'test'])


@pytest.mark.parametrize('rows', [None, [detail_deal(1, 0, 1, 5, 1780000000, position_id=22)]])
def test_details_reject_unavailable_or_mismatched_history(client, rows):
    client.mt5.history_deals_get.return_value = rows
    with pytest.raises(MT5DataError):
        client.get_position_details(11)


def test_details_endpoint_validates_ids_and_missing_positions(monkeypatch):
    from app import main
    web = authenticated_web()
    method = Mock(return_value={'position_id': 11, 'deals': []})
    monkeypatch.setattr(main.mt5_client, 'get_position_details', method)
    assert web.get('/api/history/position/0').status_code == 422
    assert web.get('/api/history/position/invalid').status_code == 422
    method.assert_not_called()
    assert web.get('/api/history/position/11').status_code == 404
    method.return_value = {'position_id': 11, 'deals': [{'ticket': 1, 'price': 4178.37}]}
    response = web.get('/api/history/position/11')
    assert response.status_code == 200
    assert response.json()['deals'][0]['price'] == 4178.37
