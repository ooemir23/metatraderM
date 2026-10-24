"""Observed results of HMA bot positions (automatic + manually confirmed signals).

Only closed broker positions are counted; numbers are history, not a forecast.
R multiple = net result / risk at entry, available when the entry risk was recorded
(risk-percentage lot sizing stores it in the bot's signal history).
"""
import math
from datetime import datetime, timedelta, timezone

from app.mt5_bridge import HMA_MAGIC

COST_KEYS = ('commission', 'swap', 'fee')


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def closed_positions(deals, history=(), open_position_ids=(), clock_offset=0):
    """Group deals by position; keep closed HMA positions, oldest first.

    MT5 deal times are broker server time; `clock_offset` (server - UTC, seconds) turns them into
    UTC epoch seconds, the clock of the bot's own records (signal history, start time)."""
    open_ids = {int(x) for x in open_position_ids}
    offset = int(clock_offset or 0)
    by_ticket = {}
    for entry in history or ():
        ticket = entry.get('ticket')
        if entry.get('success') and ticket is not None:
            try:
                by_ticket[int(ticket)] = entry
            except (TypeError, ValueError):
                continue
    groups = {}
    for deal in deals or ():
        position_id = deal.get('position_id')
        if position_id is None:
            continue
        groups.setdefault(int(position_id), []).append(deal)
    rows = []
    for position_id, deals_ in groups.items():
        sent = by_ticket.get(position_id)
        if not (sent or any(int(d.get('magic') or 0) == HMA_MAGIC for d in deals_)):
            continue
        exits = [d for d in deals_ if d.get('entry') in (1, 2, 3)]
        if position_id in open_ids or not exits:
            continue
        net = sum(_num(d.get('profit')) + sum(_num(d.get(k)) for k in COST_KEYS) for d in deals_)
        costs = sum(sum(_num(d.get(k)) for k in COST_KEYS) for d in deals_)
        closed_at = max(int(d.get('timestamp') or 0) for d in exits) - offset
        entries = [d for d in deals_ if d.get('entry') == 0]
        opened_at = int(sent['time']) if sent and sent.get('time') else (
            min(int(d.get('timestamp') or 0) for d in entries) - offset if entries else None)
        side = (sent or {}).get('side') or next((d.get('position_type') for d in exits if d.get('position_type') in ('BUY', 'SELL')), None)
        risk = _num((sent or {}).get('risk_money'))
        rows.append({
            'position_id': position_id,
            'symbol': (sent or {}).get('symbol') or next((d.get('symbol') for d in deals_ if d.get('symbol')), ''),
            'side': side, 'source': (sent or {}).get('source') or 'AUTO',
            'net': round(net, 2), 'costs': round(costs, 2),
            'opened_at': opened_at, 'closed_at': closed_at,
            'r': round(net / risk, 2) if risk > 0 else None,
        })
    rows.sort(key=lambda r: (r['closed_at'], r['position_id']))
    return rows


def loss_streak(rows, since=0):
    """Consecutive losing positions at the end of the list, closed at/after `since`."""
    streak = 0
    for row in reversed([r for r in rows if r['closed_at'] >= since]):
        if row['net'] < 0:
            streak += 1
        else:
            break
    return streak


def _group(rows, key):
    out = {}
    for row in rows:
        bucket = out.setdefault(key(row), {'count': 0, 'wins': 0, 'net': 0.0})
        bucket['count'] += 1
        bucket['wins'] += row['net'] > 0
        bucket['net'] = round(bucket['net'] + row['net'], 2)
    for bucket in out.values():
        bucket['win_rate_pct'] = round(100 * bucket['wins'] / bucket['count'], 1)
    return out


def summarize(rows, utc_offset_min=180):
    wins = [r for r in rows if r['net'] > 0]
    losses = [r for r in rows if r['net'] < 0]
    gain, loss = sum(r['net'] for r in wins), -sum(r['net'] for r in losses)
    equity = peak = drawdown = 0.0
    for row in rows:
        equity += row['net']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    r_rows = [r['r'] for r in rows if r['r'] is not None]
    tz = timezone(timedelta(minutes=utc_offset_min))

    def hour(row):
        stamp = row['opened_at'] or row['closed_at']
        return f"{datetime.fromtimestamp(stamp, tz).hour:02d}:00" if stamp else '—'

    count = len(rows)
    return {
        'closed_positions': count, 'wins': len(wins), 'losses': len(losses),
        'win_rate_pct': round(100 * len(wins) / count, 1) if count else None,
        'net': round(sum(r['net'] for r in rows), 2), 'costs': round(sum(r['costs'] for r in rows), 2),
        'avg_win': round(gain / len(wins), 2) if wins else None,
        'avg_loss': round(-loss / len(losses), 2) if losses else None,
        'profit_factor': round(gain / loss, 2) if loss else None,
        'expectancy': round(sum(r['net'] for r in rows) / count, 2) if count else None,
        'avg_r': round(sum(r_rows) / len(r_rows), 2) if r_rows else None, 'r_samples': len(r_rows),
        'max_drawdown': round(drawdown, 2),
        'current_loss_streak': loss_streak(rows),
        'by_symbol': _group(rows, lambda r: r['symbol'] or '—'),
        'by_hour': dict(sorted(_group(rows, hour).items())),
        'by_source': _group(rows, lambda r: r['source']),
        'by_side': _group(rows, lambda r: {'BUY': 'AL', 'SELL': 'SAT'}.get(r['side'], '—')),
        'recent': rows[-15:][::-1], 'early_sample': count < 30, 'utc_offset_min': utc_offset_min,
    }
