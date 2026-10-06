from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.mt5_bridge import positions


def broker():
    mt5 = Mock()
    mt5.symbol_info.return_value = NS(digits=5)
    mt5.positions_get.return_value = [NS(
        ticket=22, identifier=11, symbol='EURUSD', type=0, magic=0,
        volume=.1, price_open=1.1, price_current=1.2, sl=0, tp=0,
        profit=10, swap=-1, time=1700000000,
    )]
    return mt5


def test_position_commission_includes_opening_and_partial_closing_charges():
    mt5 = broker()
    mt5.history_deals_get.return_value = [
        NS(commission=-2.50, fee=-.3, swap=-1),
        NS(commission=-1.25, fee=0, swap=0),
    ]
    row = positions(mt5, include_commission=True)[0]
    assert row['commission'] == -3.75
    assert row['profit'] == 10
    mt5.history_deals_get.assert_called_once_with(position=11)


@pytest.mark.parametrize('history', [None, [], TimeoutError()])
def test_unavailable_commission_does_not_hide_open_position(history):
    mt5 = broker()
    if isinstance(history, Exception):
        mt5.history_deals_get.side_effect = history
    else:
        mt5.history_deals_get.return_value = history
    row = positions(mt5, include_commission=True)[0]
    assert row['ticket'] == 22
    assert row['commission'] is None


def test_commission_free_deal_is_reported_as_zero():
    mt5 = broker()
    mt5.history_deals_get.return_value = [NS(commission=0)]
    assert positions(mt5, include_commission=True)[0]['commission'] == 0


def test_order_position_reads_do_not_request_commission_history():
    mt5 = broker()
    assert positions(mt5)[0]['ticket'] == 22
    mt5.history_deals_get.assert_not_called()


@pytest.mark.parametrize('digits', [2, 3, 5])
def test_positions_report_broker_price_precision(digits):
    mt5 = broker()
    mt5.symbol_info.return_value = NS(digits=digits)
    assert positions(mt5)[0]['digits'] == digits
    mt5.symbol_info.assert_called_once_with('EURUSD')


def test_positions_keep_visible_when_symbol_info_is_unavailable():
    mt5 = broker()
    mt5.symbol_info.return_value = None
    row = positions(mt5)[0]
    assert row['ticket'] == 22
    assert row['digits'] == 5
