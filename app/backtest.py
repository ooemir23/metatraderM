"""Closed-candle HMA research simulator. Never places an order."""
import math
from app.strategy_bot import StrategyBot


def simulate(rates, hma_period=14, ma_type='EMA', ma_period=34, sl_points=200,
             tp_points=0, point=.00001, spread_points=0, commission_points=0):
    if len(rates) < max(hma_period, ma_period) + 20:
        raise ValueError('Yeterli kapanmış mum yok.')
    if not 2 <= hma_period <= 100 or not 2 <= ma_period <= 100 or ma_type not in ('EMA','SMA','LWMA','HMA'):
        raise ValueError('Geçersiz strateji parametresi.')
    if not all(math.isfinite(x) and x >= 0 for x in (point, spread_points, commission_points)) or point <= 0:
        raise ValueError('Geçersiz fiyat veya maliyet.')
    if not all(isinstance(x, int) and 0 <= x <= 10000 for x in (sl_points, tp_points)):
        raise ValueError('Geçersiz SL/TP mesafesi.')
    closes = [float(r['close']) for r in rates]
    for r in rates:
        if not all(math.isfinite(float(r[k])) for k in ('open','high','low','close')) or float(r['low']) > float(r['high']):
            raise ValueError('Geçersiz mum verisi.')
    trades = []
    position = None
    min_index = max(hma_period, ma_period) + 12
    half_spread = spread_points * point / 2
    for i in range(min_index, len(rates)-1):
        # Signal uses candle i only after it has closed. Execution occurs at i+1 open.
        reverse = list(reversed(closes[:i+1]))
        hma_now = StrategyBot.calc_hma(reverse, hma_period)
        hma_prev = StrategyBot.calc_hma(reverse, hma_period, 1)
        ma_now = StrategyBot.calc_second_ma(reverse, ma_type, ma_period)
        ma_prev = StrategyBot.calc_second_ma(reverse, ma_type, ma_period, 1)
        if not all((hma_now, hma_prev, ma_now, ma_prev)):
            continue
        side = 'BUY' if hma_prev <= ma_prev and hma_now > ma_now else 'SELL' if hma_prev >= ma_prev and hma_now < ma_now else None
        candle = rates[i+1]
        open_price = float(candle['open'])
        if position and side and side != position['side']:
            exit_price = open_price - half_spread if position['side']=='BUY' else open_price + half_spread
            _finish(trades, position, exit_price, point, commission_points, i+1, 'opposite signal')
            position = None
        if not position and side:
            entry = open_price + half_spread if side=='BUY' else open_price - half_spread
            position = {'side':side, 'entry':entry, 'opened_at':i+1,
                        'sl':entry + (-1 if side=='BUY' else 1)*sl_points*point if sl_points else None,
                        'tp':entry + (1 if side=='BUY' else -1)*tp_points*point if tp_points else None}
        if not position:
            continue
        # A bar touching both limits is counted as a stop fill, the conservative outcome.
        buy = position['side']=='BUY'
        low, high = float(candle['low']), float(candle['high'])
        stop_hit = position['sl'] is not None and (low <= position['sl'] if buy else high >= position['sl'])
        target_hit = position['tp'] is not None and (high >= position['tp'] if buy else low <= position['tp'])
        if stop_hit or target_hit:
            if stop_hit:
                gap_exit = open_price - half_spread if buy else open_price + half_spread
                exit_price = min(position['sl'], gap_exit) if buy else max(position['sl'], gap_exit)
            else:
                exit_price = position['tp']
            _finish(trades, position, exit_price, point, commission_points, i+1, 'stop' if stop_hit else 'target')
            position = None
    if position:
        final_close = float(rates[-1]['close'])
        exit_price = final_close - half_spread if position['side']=='BUY' else final_close + half_spread
        _finish(trades, position, exit_price, point, commission_points, len(rates)-1, 'test end')
    split = int(len(rates)*.7)
    return {'success':True, 'symbol_points':True, 'trades':trades[-100:],
            'total_trades':len(trades), 'trades_sample_limit':100,
            'in_sample':_stats([t for t in trades if t['opened_at'] < split]),
            'out_of_sample':_stats([t for t in trades if t['opened_at'] >= split]),
            'assumptions':{'spread_points':spread_points, 'commission_points_round_turn':commission_points,
                           'swap_included':False, 'slippage_included':False,
                           'historical_spread_included':False, 'fills':'next candle open; same-bar stop before target'}}


def _finish(trades, position, price, point, commission, index, reason):
    direction = 1 if position['side']=='BUY' else -1
    net = direction*(price-position['entry'])/point - commission
    trades.append({'side':position['side'], 'opened_at':position['opened_at'],
                   'closed_at':index, 'net_points':round(net, 2), 'reason':reason})


def _stats(rows):
    profits = [r['net_points'] for r in rows]
    peak = equity = max_drawdown = 0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak-equity)
    return {'count':len(rows), 'net_points':round(sum(profits), 2),
            'win_rate_pct':round(100*sum(p>0 for p in profits)/len(profits), 1) if profits else None,
            'max_drawdown_points':round(max_drawdown, 2)}
