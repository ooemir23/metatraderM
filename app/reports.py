"""Cash activity by MT5 server day (deal times are broker time); win statistics by fully closed position lifetime."""
from datetime import datetime, timedelta, timezone
import math


def net(row):
    return sum(float(row.get(k, 0)) for k in ('profit', 'commission', 'swap', 'fee'))


def day(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')


def closed_positions(activity, lifetimes):
    """Positions fully closed inside the window, with the net of their whole lifetime (MT5 report rule)."""
    closed, incomplete = [], []
    for position_id, history in lifetimes.items():
        trades = sorted((r for r in history if r['type'] in (0, 1)), key=lambda r: (r['time'], r['ticket']))
        balance = sum(r['volume'] * (1 if r['type'] == 0 else -1) for r in trades)
        if not trades or trades[0]['entry'] != 0 or not math.isclose(balance, 0, abs_tol=1e-7):
            incomplete.append(position_id)
            continue
        final = trades[-1]
        if position_id in activity['open_ids'] or not activity['start'] <= final['time'] <= activity['end']:
            continue
        closed.append(dict(position_id=position_id, symbol=final['symbol'], date=day(final['time']), time=final['time'],
                           net=round(net_sum(history), 2), volume=sum(r['volume'] for r in trades if r['entry'] in (1, 3))))
    return closed, incomplete


PERIODS = {'day': 90, 'week': 52, 'month': 24, 'year': 5}   # period -> largest allowed limit


def period_start(timestamp, period):
    """Start of the broker-server calendar period holding `timestamp` (deal times are server time).
    Weeks start on Monday."""
    d = datetime.fromtimestamp(int(timestamp), timezone.utc).date()
    if period == 'week':
        d -= timedelta(days=d.weekday())
    elif period == 'month':
        d = d.replace(day=1)
    elif period == 'year':
        d = d.replace(month=1, day=1)
    return d


def previous_start(start, period):
    if period == 'day':
        return start - timedelta(days=1)
    if period == 'week':
        return start - timedelta(days=7)
    if period == 'month':
        return (start - timedelta(days=1)).replace(day=1)
    return start.replace(year=start.year - 1)


def period_starts(now, period, limit):
    """The last `limit` calendar periods, newest first."""
    starts = [period_start(now, period)]
    while len(starts) < limit:
        starts.append(previous_start(starts[-1], period))
    return starts


def period_window_days(now, period, limit):
    """Whole days the activity query must cover so the oldest period is complete."""
    oldest = period_starts(now, period, limit)[-1]
    today = datetime.fromtimestamp(int(now), timezone.utc).date()
    return (today - oldest).days + 1


def period_label(start, period):
    if period == 'day':
        return start.strftime('%d.%m.%Y')
    if period == 'week':
        return f"{start:%d.%m} – {start + timedelta(days=6):%d.%m.%Y}"
    if period == 'month':
        return start.strftime('%m.%Y')
    return str(start.year)


def build_period_report(activity, lifetimes, period, limit):
    """Day / week / month / year table: cash net by server day, win statistics by fully closed
    position. A period without trades is listed with zeros."""
    if period not in PERIODS:
        raise ValueError('period day, week, month veya year olmalı.')
    limit = max(1, min(int(limit), PERIODS[period]))
    starts = period_starts(activity['end'], period, limit)
    groups = {s: dict(trades=0, wins=0, losses=0, gross_profit=0., gross_loss=0., commission=0., swap=0., fee=0.,
                      net=0., best=None, worst=None) for s in starts}
    for r in activity['deals']:
        g = groups.get(period_start(r['time'], period))
        if g is None:
            continue
        g['net'] += net(r)
        for k in ('commission', 'swap', 'fee'):
            g[k] += float(r.get(k, 0))
    closed, incomplete = closed_positions(activity, lifetimes)
    for p in closed:
        g = groups.get(period_start(p['time'], period))
        if g is None:
            continue
        g['trades'] += 1
        g['wins'] += int(p['net'] > 0)
        g['losses'] += int(p['net'] < 0)
        g['gross_profit'] += max(0, p['net'])
        g['gross_loss'] += min(0, p['net'])
        g['best'] = p['net'] if g['best'] is None else max(g['best'], p['net'])
        g['worst'] = p['net'] if g['worst'] is None else min(g['worst'], p['net'])
    rows, cumulative = [], 0.
    for start in reversed(starts):          # cumulative runs oldest -> newest
        g = groups[start]
        cumulative += g['net']
        rows.append(dict(key=start.isoformat(), label=period_label(start, period), start=start.isoformat(),
            trades=g['trades'], wins=g['wins'], losses=g['losses'],
            win_rate=round(100 * g['wins'] / g['trades'], 1) if g['trades'] else 0,
            gross_profit=round(g['gross_profit'], 2), gross_loss=round(g['gross_loss'], 2),
            commission=round(g['commission'], 2), swap=round(g['swap'], 2), fee=round(g['fee'], 2),
            net=round(g['net'], 2),
            profit_factor=round(g['gross_profit'] / abs(g['gross_loss']), 2) if g['gross_loss'] else (None if g['gross_profit'] else 0),
            best=round(g['best'], 2) if g['best'] is not None else None,
            worst=round(g['worst'], 2) if g['worst'] is not None else None,
            cumulative=round(cumulative, 2)))
    rows.reverse()                          # newest first, like the daily table
    nets = [r['net'] for r in rows]
    active = [r for r in rows if r['trades'] or r['net']]
    summary = dict(current=rows[0]['net'], previous=rows[1]['net'] if len(rows) > 1 else 0,
                   average=round(sum(nets) / len(nets), 2) if nets else 0,
                   profitable_periods=sum(n > 0 for n in nets), periods=len(rows),
                   profitable_ratio=round(100 * sum(n > 0 for n in nets) / len(active), 1) if active else 0,
                   total=round(sum(nets), 2))
    return dict(period=period, limit=limit, currency=activity['currency'], rows=rows, summary=summary,
                incomplete_position_ids=incomplete,
                basis='Gün = broker sunucu günü, hafta pazartesi başlar. Net, dönemdeki hesap hareketlerinin toplamıdır; '
                      'işlem, kazanma ve brüt kâr/zarar dönemde tamamen kapanan pozisyonların tüm yaşamına göre hesaplanır.')


def build_report(activity, lifetimes):
    rows = activity['deals']
    today = day(activity['end'])
    daily, symbols = {}, {}
    closed, incomplete = closed_positions(activity, lifetimes)
    def group(mapping, key):
        return mapping.setdefault(key, dict(profit=0., swap=0., commission=0., fee=0., volume=0.,
            trades_count=0, winning_trades=0, losing_trades=0, gross_profit=0., gross_loss=0.))
    for r in rows:
        for g in (group(daily, day(r['time'])), group(symbols, r['symbol'] or 'Hesap giderleri')):
            g['profit'] += net(r)
            for k in ('swap','commission','fee'):
                g[k] += r[k]
            if r['type'] in (0,1) and r['entry'] in (1,3):
                g['volume'] += r['volume']
    for p in closed:
        for g in (group(daily,p['date']), group(symbols,p['symbol'])):
            g['trades_count'] += 1
            g['winning_trades'] += int(p['net'] > 0)
            g['losing_trades'] += int(p['net'] < 0)
            g['gross_profit'] += max(0, p['net'])
            g['gross_loss'] += min(0, p['net'])
    wins, losses = [p['net'] for p in closed if p['net'] > 0], [p['net'] for p in closed if p['net'] < 0]
    gains, loss = sum(wins), sum(losses)
    def finish(g):
        return {**{k:round(v, 8 if k == "volume" else 2) if isinstance(v,float) else v for k,v in g.items()},
                'win_rate':round(100*g['winning_trades']/g['trades_count'],1) if g['trades_count'] else 0}
    summary = dict(total_trades=len(closed), winning_trades=len(wins), losing_trades=len(losses),
        win_rate=round(100*len(wins)/len(closed),1) if closed else 0,
        total_profit=round(net_sum(rows),2), gross_profit=round(gains,2), gross_loss=round(loss,2),
        cohort_net_profit=round(sum(p['net'] for p in closed),2),
        profit_factor=round(gains/abs(loss),2) if loss else (None if gains else 0),
        today_profit=round(net_sum([r for r in rows if day(r['time']) == today]),2),
        today_trades=sum(p['date'] == today for p in closed),
        avg_profit=round(gains/len(wins),2) if wins else 0,
        avg_loss=round(loss/len(losses),2) if losses else 0,
        best_trade=max((p['net'] for p in closed),default=0), worst_trade=min((p['net'] for p in closed),default=0),
        total_volume=round(sum(r['volume'] for r in rows if r['type'] in (0,1) and r['entry'] in (1,3)),4),
        total_swap=round(sum(r['swap'] for r in rows),2), total_commission=round(sum(r['commission'] for r in rows),2),
        total_fee=round(sum(r['fee'] for r in rows),2),
        unallocated_net=round(net_sum([r for r in rows if not r['position_id']]),2),
        incomplete_positions=len(incomplete))
    return dict(summary=summary, currency=activity['currency'], timezone='UTC',
        basis='Tutarlar seçilen dönemdeki net hesap hareketleridir. Kazanma oranı ve kâr faktörü, dönemde tamamen kapanan pozisyonların açılış giderleri dahil tüm yaşamına göre hesaplanır. Kısmi kapanışlar ayrı pozisyon sayılmaz. Pozisyona bağlanamayan giderler yalnız dönem toplamına dahildir.',
        daily=[dict(date=k,is_today=k==today,**finish(v)) for k,v in sorted(daily.items(),reverse=True)],
        by_symbol=[dict(symbol=k,**finish(v)) for k,v in sorted(symbols.items(),key=lambda x:x[1]['profit'],reverse=True)],
        incomplete_position_ids=incomplete)


def net_sum(rows):
    return sum(net(r) for r in rows)
