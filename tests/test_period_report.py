"""Period report (day/week/month/year): broker server calendar, Monday weeks, zero rows for empty periods."""
from datetime import datetime, timezone

import pytest

from app.reports import build_period_report, period_starts, period_window_days, period_start


def ts(text):
    return int(datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp())


def deal(ticket, pos, t, typ, entry, profit=0., commission=0., swap=0., volume=1.):
    return dict(ticket=ticket, position_id=pos, time=ts(t), type=typ, entry=entry, profit=profit,
                commission=commission, swap=swap, fee=0., volume=volume, symbol='XAUUSD')


def activity(deals, end='2026-10-21 10:00', start='2026-01-01 00:00'):
    return dict(account=[1, 'X'], currency='USD', start=ts(start), end=ts(end), deals=deals, open_ids=[])


# position 1: opened Mon 19 Oct, closed Tue 20 Oct +100 (commission -3 on each side)
p1 = [deal(1, 1, '2026-10-19 09:00', 0, 0, commission=-3), deal(2, 1, '2026-10-20 23:59', 1, 1, profit=100, commission=-3)]
# position 2: opened and closed Sun 18 Oct (previous week), -50
p2 = [deal(3, 2, '2026-10-18 10:00', 1, 0), deal(4, 2, '2026-10-18 12:00', 0, 1, profit=-50, swap=-2)]
# position 3: September, +30
p3 = [deal(5, 3, '2026-09-02 10:00', 0, 0), deal(6, 3, '2026-09-02 11:00', 1, 1, profit=30)]
ALL = p1 + p2 + p3
LIFE = {1: p1, 2: p2, 3: p3}


def test_week_starts_monday_and_server_day_boundary():
    assert str(period_start(ts('2026-10-21 10:00'), 'week')) == '2026-10-19'      # Wednesday -> Monday
    assert str(period_start(ts('2026-10-18 23:59'), 'week')) == '2026-10-12'      # Sunday belongs to previous week
    assert str(period_start(ts('2026-10-20 23:59'), 'day')) == '2026-10-20'       # server-time day, not shifted


def test_daily_rows_include_empty_days_with_zero():
    r = build_period_report(activity(ALL), LIFE, 'day', 4)
    assert [x['key'] for x in r['rows']] == ['2026-10-21', '2026-10-20', '2026-10-19', '2026-10-18']
    today, tue, mon, sun = r['rows']
    assert today['net'] == 0 and today['trades'] == 0 and today['profit_factor'] == 0
    assert mon['net'] == -3 and mon['trades'] == 0                 # opening commission is cash on Monday
    assert tue['net'] == 97 and tue['trades'] == 1 and tue['wins'] == 1
    assert tue['gross_profit'] == 94                               # whole-lifetime net of the closed position
    assert sun['net'] == -52 and sun['losses'] == 1 and sun['swap'] == -2
    assert sun['cumulative'] == -52 and today['cumulative'] == 42
    assert r['summary']['current'] == 0 and r['summary']['previous'] == 97


def test_weekly_and_monthly_buckets():
    w = build_period_report(activity(ALL), LIFE, 'week', 3)
    assert [x['key'] for x in w['rows']] == ['2026-10-19', '2026-10-12', '2026-10-05']
    assert w['rows'][0]['net'] == 94 and w['rows'][1]['net'] == -52 and w['rows'][2]['net'] == 0
    assert w['rows'][0]['label'] == '19.10 – 25.10.2026'
    m = build_period_report(activity(ALL), LIFE, 'month', 3)
    assert [x['key'] for x in m['rows']] == ['2026-10-01', '2026-09-01', '2026-08-01']
    assert m['rows'][0]['net'] == 42 and m['rows'][0]['trades'] == 2 and m['rows'][0]['win_rate'] == 50
    assert m['rows'][0]['profit_factor'] == round(94 / 52, 2)
    assert m['rows'][0]['best'] == 94 and m['rows'][0]['worst'] == -52
    assert m['rows'][1]['net'] == 30
    y = build_period_report(activity(ALL), LIFE, 'year', 2)
    assert y['rows'][0]['net'] == 72 and y['rows'][1]['net'] == 0 and y['rows'][1]['label'] == '2025'


def test_window_covers_oldest_period_and_limits():
    now = ts('2026-10-21 10:00')
    assert period_window_days(now, 'week', 2) == 10       # Mon 12 Oct .. Wed 21 Oct
    assert period_window_days(now, 'month', 1) == 21
    assert len(period_starts(now, 'month', 13)) == 13 and str(period_starts(now, 'month', 13)[-1]) == '2025-10-01'
    assert build_period_report(activity(ALL), LIFE, 'year', 99)['limit'] == 5
    with pytest.raises(ValueError):
        build_period_report(activity(ALL), LIFE, 'hour', 3)


def test_client_queries_a_window_that_covers_the_oldest_period(monkeypatch):
    from app import mt5_client as mc
    client = mc.MT5Client.__new__(mc.MT5Client)
    client.tick_clock_offset = 10800
    asked = []
    def inputs(days):
        asked.append(days)
        return activity(ALL), LIFE
    monkeypatch.setattr(client, '_report_inputs', inputs, raising=False)
    monkeypatch.setattr(mc.time, 'time', lambda: ts('2026-10-21 07:00'))   # 10:00 broker time
    report = client.get_period_report('week', 2)
    assert asked == [10] and [r['key'] for r in report['rows']] == ['2026-10-19', '2026-10-12']
    with pytest.raises(ValueError):
        client.get_period_report('quarter', 2)
