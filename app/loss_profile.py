"""Loss profile: which kinds of closed trades lose, and an advisory check for a new order (read-only, no MT5 rule).

Closed manual + bot positions (broker history rows from `manual_lifetimes` / `bot_lifetimes`) are grouped by symbol,
direction, lot size (vs. the symbol's median lot), stop distance (% of entry) and holding time. A group is "risky"
only with enough trades (MIN_TRADES), a low win rate and a net loss. Nothing here blocks or changes an order.
"""
import statistics

MIN_TRADES = 5
MAX_WIN_RATE = 35.0
CLOSE_REASONS = {4: 'SL', 5: 'TP', 6: 'Stop out'}


def sl_bucket(row):
    sl, price = float(row.get('sl') or 0), float(row.get('open_price') or 0)
    if sl <= 0 or price <= 0:
        return 'SL yok'
    return sl_bucket_pct(abs(price - sl) / price * 100)


def sl_bucket_pct(pct):
    if pct is None:
        return 'SL yok'
    return 'SL çok dar (<%0,1)' if pct < 0.1 else 'SL dar (%0,1–0,3)' if pct < 0.3 else \
        'SL orta (%0,3–1)' if pct < 1 else 'SL geniş (>%1)'


def lot_bucket_ratio(ratio):
    return 'Lot normal (≤1,5×)' if ratio <= 1.5 else 'Lot büyük (1,5–3×)' if ratio <= 3 else 'Lot çok büyük (>3×)'


def hold_bucket(row):
    minutes = (int(row['close_time']) - int(row['open_time'])) / 60
    return 'Tutma <5 dk' if minutes < 5 else 'Tutma 5–60 dk' if minutes < 60 else \
        'Tutma 1–8 sa' if minutes < 480 else 'Tutma >8 sa'


def _medians(rows):
    by = {}
    for r in rows:
        by.setdefault(r['symbol'], []).append(float(r['volume']))
    return {k: statistics.median(v) for k, v in by.items()}


def _dims(row, medians):
    median = medians.get(row['symbol']) or float(row['volume'])
    return [('Sembol', row['symbol']), ('Yön', f"{row['symbol']} {'AL' if row['side'] == 'BUY' else 'SAT'}"),
            ('Lot', lot_bucket_ratio(float(row['volume']) / median if median else 1)),
            ('Stop', sl_bucket(row)), ('Süre', hold_bucket(row))]


def build(rows):
    rows = [r for r in rows if r.get('net') is not None]
    medians = _medians(rows)
    groups = {}
    for r in rows:
        for dim, key in _dims(r, medians):
            g = groups.setdefault((dim, key), {'dim': dim, 'key': key, 'count': 0, 'wins': 0, 'net': 0.0})
            g['count'] += 1
            g['wins'] += r['net'] > 0
            g['net'] = round(g['net'] + r['net'], 2)
    out = []
    for g in groups.values():
        g['win_rate_pct'] = round(100 * g['wins'] / g['count'], 1)
        g['risky'] = g['count'] >= MIN_TRADES and g['win_rate_pct'] <= MAX_WIN_RATE and g['net'] < 0
        out.append(g)
    out.sort(key=lambda g: (not g['risky'], g['net']))
    stops = {}
    for r in rows:
        if r['net'] < 0:
            reason = CLOSE_REASONS.get(r.get('close_reason'), 'Elle/diğer')
            stops[reason] = stops.get(reason, 0) + 1
    total = len(rows)
    return {'trades': total, 'net': round(sum(r['net'] for r in rows), 2),
            'win_rate_pct': round(100 * sum(r['net'] > 0 for r in rows) / total, 1) if total else None,
            'loss_reasons': stops, 'groups': out, 'medians': medians, 'min_trades': MIN_TRADES,
            'early_sample': total < 30}


def check(profile, symbol, side, volume, entry_price=None, sl_price=None):
    """Risky groups that match a new order. Advisory text only."""
    median = (profile.get('medians') or {}).get(symbol)
    keys = {('Sembol', symbol), ('Yön', f"{symbol} {'AL' if side == 'BUY' else 'SAT'}")}
    if median:
        keys.add(('Lot', lot_bucket_ratio(float(volume) / median)))
    pct = None
    if sl_price and entry_price and float(sl_price) > 0 and float(entry_price) > 0:
        pct = abs(float(entry_price) - float(sl_price)) / float(entry_price) * 100
    keys.add(('Stop', sl_bucket_pct(pct)))
    return [g for g in profile.get('groups', []) if g['risky'] and (g['dim'], g['key']) in keys]
