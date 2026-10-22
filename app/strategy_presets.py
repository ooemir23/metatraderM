"""Widely used moving-average crossover setups, tested on the broker's own candles with the bot's rules.

Nothing here claims a strategy is profitable: every preset is a well-known, published MA crossover, and
its result is the history test of the bot's exact rules (closed-candle crossover, order at the next
candle open, Ask/Bid fills, reverse signal closes, the bot's SL/TP) on this symbol and timeframe.
Past results do not guarantee future results."""
from app.backtest import simulate

PRESETS = (
    {'id': 'ema-9-21', 'name': 'EMA 9 × EMA 21', 'fast_type': 'EMA', 'fast': 9, 'slow_type': 'EMA', 'slow': 21,
     'note': 'Gün içi ve kısa vadede en yaygın EMA kesişimi.'},
    {'id': 'ema-8-21', 'name': 'EMA 8 × EMA 21', 'fast_type': 'EMA', 'fast': 8, 'slow_type': 'EMA', 'slow': 21,
     'note': 'Fibonacci sayılarıyla kurulan, kısa vadeli trend takibinde yaygın ayar.'},
    {'id': 'ema-12-26', 'name': 'EMA 12 × EMA 26', 'fast_type': 'EMA', 'fast': 12, 'slow_type': 'EMA', 'slow': 26,
     'note': 'MACD göstergesinin (Gerald Appel) temelindeki iki ortalama.'},
    {'id': 'ema-20-50', 'name': 'EMA 20 × EMA 50', 'fast_type': 'EMA', 'fast': 20, 'slow_type': 'EMA', 'slow': 50,
     'note': 'Orta vadeli trend takibinde yaygın.'},
    {'id': 'sma-10-20', 'name': 'SMA 10 × SMA 20', 'fast_type': 'SMA', 'fast': 10, 'slow_type': 'SMA', 'slow': 20,
     'note': 'Klasik kısa vadeli basit ortalama kesişimi.'},
    {'id': 'sma-50-200', 'name': 'SMA 50 × SMA 200', 'fast_type': 'SMA', 'fast': 50, 'slow_type': 'SMA', 'slow': 200,
     'note': '"Golden cross / death cross": uzun vadeli trend dönüşü; az sinyal verir.'},
    {'id': 'hma-14-ema-34', 'name': 'HMA 14 × EMA 34', 'fast_type': 'HMA', 'fast': 14, 'slow_type': 'EMA', 'slow': 34,
     'note': 'HMA_Crossover_EA varsayılanı (MT5 EA).'},
    {'id': 'hma-9-ema-21', 'name': 'HMA 9 × EMA 21', 'fast_type': 'HMA', 'fast': 9, 'slow_type': 'EMA', 'slow': 21,
     'note': 'Alan Hull ortalamasıyla daha hızlı tepki veren kısa vadeli ayar.'},
)


def run_presets(rates, start, point, spread_points, *, sl_points, tp_points, commission_points=0.0):
    """History test of every preset with the same exits; best total net points first."""
    rows = []
    for p in PRESETS:
        try:
            res = simulate(rates, p['fast'], p['slow_type'], p['slow'], int(sl_points), int(tp_points), point,
                           spread_points, commission_points, start=start, fast_type=p['fast_type'])
        except ValueError as exc:
            rows.append({**p, 'available': False, 'reason': str(exc)})
            continue
        ins, outs = res['in_sample'], res['out_of_sample']
        total = ins['net_points'] + outs['net_points']
        count = res['total_trades']
        wins = sum(part['count'] * (part['win_rate_pct'] or 0) / 100 for part in (ins, outs))
        rows.append({**p, 'available': True, 'trades': count, 'net_points': round(total, 2),
                     'avg_points': round(total / count, 2) if count else None,
                     'win_rate_pct': round(100 * wins / count, 1) if count else None,
                     'max_drawdown_points': max(res['in_sample']['max_drawdown_points'], res['out_of_sample']['max_drawdown_points']),
                     'in_sample': res['in_sample'], 'out_of_sample': res['out_of_sample'],
                     'tested_candles': res['tested_candles'],
                     'small_sample': count < 20})
    rows.sort(key=lambda r: (not r['available'], -(r.get('net_points') or 0)))
    return rows
