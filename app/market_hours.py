"""Next market open, estimated from the broker's own bar history.

The MetaTrader5 Python API has no trade-session schedule (MQL5 SymbolInfoSessionTrade is not
exposed), so the reopening time is taken from earlier closures of the same symbol that started on
the same weekday and time of day (server clock). Holidays can differ; the result says so.
"""
from datetime import datetime, timezone
from statistics import median

BAR = 300           # M5
GAP_MIN = 2 * BAR   # a missing bar or more counts as a closure
TOD_TOLERANCE = 15 * 60


def _weekday_tod(server_epoch):
    d = datetime.fromtimestamp(int(server_epoch), timezone.utc)
    return d.weekday(), d.hour * 3600 + d.minute * 60 + d.second


def closures(times):
    """(start, end) of every gap in M5 bar open times; start = end of the last bar before it."""
    out = []
    for a, b in zip(times, times[1:]):
        if b - a > GAP_MIN:
            out.append((a + BAR, b))
    return out


def next_open(times, server_now):
    times = sorted(int(t) for t in times)
    if not times:
        return {'known': False, 'closed': None, 'reason': 'Mum geçmişi yok.'}
    start = times[-1] + BAR
    if server_now - start < GAP_MIN:
        return {'known': True, 'closed': False}
    weekday, tod = _weekday_tod(start)
    durations = []
    for s, e in closures(times):
        w, t = _weekday_tod(s)
        diff = abs(t - tod)
        if w == weekday and min(diff, 86400 - diff) <= TOD_TOLERANCE:
            durations.append(e - s)
    if not durations:
        return {'known': False, 'closed': True, 'closed_since': start,
                'reason': 'Geçmiş haftalarda bu saatte başlayan bir kapanış bulunamadı.'}
    reopen = start + int(median(durations))
    return {'known': True, 'closed': True, 'closed_since': start, 'next_open': reopen,
            'seconds_until': max(0, int(reopen - server_now)), 'overdue': reopen <= server_now,
            'samples': len(durations), 'basis': 'history'}
