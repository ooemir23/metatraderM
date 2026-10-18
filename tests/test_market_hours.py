from datetime import datetime, timezone

from app import market_hours as mh


def ts(y, m, d, H=0, M=0):
    return int(datetime(y, m, d, H, M, tzinfo=timezone.utc).timestamp())


def forex_week_bars(weeks):
    """M5 bars Monday 00:00 – Friday 23:55 (server clock) for the given Mondays."""
    out = []
    for monday in weeks:
        out += list(range(monday, monday + 5 * 86400, 300))
    return out


MONDAYS = [ts(2026, 9, 28), ts(2026, 10, 5), ts(2026, 10, 12)]


def test_weekend_closure_reopens_monday_midnight():
    bars = forex_week_bars(MONDAYS)
    now = ts(2026, 10, 18, 16, 36)                    # Sunday 16:36 server time
    r = mh.next_open(bars, now)
    assert r['closed'] and r['known'] and r['samples'] == 2
    assert r['closed_since'] == ts(2026, 10, 17)       # Friday 23:55 bar ended at Saturday 00:00
    assert r['next_open'] == ts(2026, 10, 19)          # Monday 00:00
    assert r['seconds_until'] == 7 * 3600 + 24 * 60 and not r['overdue']


def test_open_market_and_daily_break():
    bars = forex_week_bars(MONDAYS)
    assert mh.next_open(bars, bars[-1] + 400)['closed'] is False
    # Gold-like daily break 23:55–01:00 on Tue/Wed of earlier weeks, now inside Wednesday's break.
    day_bars = [t for t in forex_week_bars(MONDAYS[:2])
                if not (datetime.fromtimestamp(t, timezone.utc).weekday() in (1, 2)
                        and (datetime.fromtimestamp(t, timezone.utc).hour == 0))]
    wed = ts(2026, 10, 14)
    current = [t for t in range(ts(2026, 10, 12), wed, 300)
               if not (datetime.fromtimestamp(t, timezone.utc).weekday() == 1 and datetime.fromtimestamp(t, timezone.utc).hour == 0)]
    r = mh.next_open(day_bars + current, wed + 20 * 60)
    assert r['closed'] and r['next_open'] == wed + 3600


def test_unknown_pattern_and_overdue():
    bars = forex_week_bars(MONDAYS[-1:])
    r = mh.next_open(bars, ts(2026, 10, 18, 12))
    assert r['closed'] and not r['known']
    late = mh.next_open(forex_week_bars(MONDAYS), ts(2026, 10, 19, 2))   # Monday 02:00, still no bars
    assert late['overdue'] and late['seconds_until'] == 0
