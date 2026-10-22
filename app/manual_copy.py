"""Order settings copied from the profitable positions opened by hand in the original MT5 terminal.

User decision (2026-10-23): the HMA bot opens its positions "with the same settings as the profitable manual
MT5 positions" opened since 2026-10-08. This is not an HMA_Crossover_EA input (the EA uses fixed InpLots /
InpStopLoss / InpTakeProfit); it replaces only lot and SL/TP distance, the entry rule stays the EA crossover.

Rules (broker data only, nothing invented):
- trades: closed manual positions of the bot symbol, opened at/after `since` (broker server time), net > 0.
- lot: median total volume of one manual decision (positions of one side opened within 120 s, like
  manual_style.merge_decisions), so 3 × 5 lot opened together counts as one 15 lot decision.
- SL / TP: median distance (points) of the SL / TP the positions were opened with (opening deal sl/tp).
  Positions opened without a TP were closed by hand: their TP is the median exit distance (close − open in the
  trade's direction). Without any SL on record the bot keeps its own SL setting.
"""
import calendar
import math
from datetime import date
from statistics import median

from app.manual_style import merge_decisions

MIN_TRADES = 3


def since_epoch(day):
    """Broker server midnight of a YYYY-MM-DD day as MT5 deal time (server time written as epoch)."""
    y, m, d = (int(x) for x in str(day).split('-'))
    return calendar.timegm(date(y, m, d).timetuple())


def profile(trades, symbol, since, point):
    """Lot / SL / TP of the profitable manual positions, or a reason why none can be used."""
    point = float(point or 0)
    if not math.isfinite(point) or point <= 0:
        return {'available': False, 'reason': f'{symbol} point değeri okunamadı.'}
    start = since_epoch(since)
    rows = [t for t in trades if t.get('symbol') == symbol and int(t.get('open_time', 0)) >= start
            and float(t.get('net', 0)) > 0 and float(t.get('volume', 0)) > 0]
    if len(rows) < MIN_TRADES:
        return {'available': False, 'count': len(rows), 'since': since,
                'reason': f'{since} sonrası {symbol} kârlı manuel pozisyon sayısı {len(rows)} (en az {MIN_TRADES} gerekli).'}
    decisions = merge_decisions(rows)
    lot = median(d['volume'] for d in decisions)

    def dist(t, level):
        return abs(float(t['open_price']) - float(level)) / point

    sls = [dist(t, t['sl']) for t in rows if float(t.get('sl') or 0) > 0]
    tps = [dist(t, t['tp']) for t in rows if float(t.get('tp') or 0) > 0]
    exits = [(float(t['close_price']) - float(t['open_price'])) * (1 if t['side'] == 'BUY' else -1) / point
             for t in rows if t.get('close_price') is not None]
    exits = [x for x in exits if x > 0]
    tp_from_exit = not tps or len(tps) * 2 < len(rows)
    tp = median(exits) if tp_from_exit and exits else (median(tps) if tps else None)
    return {
        'available': True, 'symbol': symbol, 'since': since, 'count': len(rows), 'decisions': len(decisions),
        'lot': round(lot, 8),
        'sl_points': int(math.ceil(median(sls))) if sls else None,
        'tp_points': int(math.ceil(tp)) if tp else None,
        'sl_source': 'manual_sl' if sls else 'bot_setting',
        'tp_source': ('manual_exit' if tp_from_exit else 'manual_tp') if tp else 'bot_setting',
        'with_sl': len(sls), 'with_tp': len(tps),
    }
