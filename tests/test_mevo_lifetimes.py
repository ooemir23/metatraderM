"""mevo_lifetimes: MevO sayfasından ("MevO" yorumu) ve bot MevO girişinden açılan kapalı pozisyonları seçer."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from app import mt5_bridge


def deal(ticket, pos, entry, typ, comment='', magic=0, t=1000, profit=0.0, reason=0):
    return NS(ticket=ticket, position_id=pos, entry=entry, type=typ, symbol='XAUUSD', volume=1.0, price=2000.0, time=t,
              time_msc=t * 1000, profit=profit, commission=0.0, swap=0.0, fee=0.0, comment=comment, magic=magic,
              sl=1990.0, tp=2020.0, reason=reason)


def fake(deals):
    mt5 = Mock()
    mt5.account_info.return_value = NS(login=1, server='s')
    mt5.history_deals_get.return_value = deals
    mt5.positions_get.return_value = []
    return mt5


def test_selects_mevo_comment_and_bot_ids_only():
    deals = [deal(1, 10, 0, 0, comment='MevO'), deal(2, 10, 1, 1, t=1100, profit=5, reason=5),
             deal(3, 11, 0, 0, comment='HMA Bot M5 BUY', magic=123461), deal(4, 11, 1, 1, t=1100, profit=-2, reason=4),
             deal(5, 12, 0, 0, comment='HMA Bot M5 BUY', magic=123461), deal(6, 12, 1, 1, t=1100, profit=1),
             deal(7, 13, 0, 0), deal(8, 13, 1, 1, t=1100, profit=9)]
    rows = mt5_bridge.mevo_lifetimes(fake(deals), 90, 0, (1, 's'), [11])
    assert sorted(r['position_id'] for r in rows) == [10, 11]
    assert {r['position_id']: r['close_reason'] for r in rows} == {10: 5, 11: 4}
