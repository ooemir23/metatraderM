"""Closed-candle HMA research simulator. Never places an order."""
import math
from app import signal_engine as se


def simulate(rates, hma_period=14, ma_type='EMA', ma_period=34, sl_points=200,
             tp_points=0, point=.00001, spread_points=0, commission_points=0, *, start=0,
             sl_atr=0.0, tp_atr=0.0, atr_period=14, fast_type='HMA'):
    """Bot rules on closed candles: crossover on candle i, order at candle i+1 open.

    MT5 candles are Bid prices. A buy opens at Ask (Bid + spread) and its SL/TP trigger on Bid; a
    sell opens at Bid and its SL/TP trigger on Ask (Bid + spread). SL/TP are placed from the fill
    price like the bot's orders: fixed points, or ATR(atr_period) x multiplier of the signal candle
    (the bot's ATR risk mode). Candles before `start` only warm up the indicators (MT5 computes
    EMA/ATR on the whole chart)."""
    if len(rates) - start < max(hma_period, ma_period) + 20:
        raise ValueError('Yeterli kapanmış mum yok.')
    if not 2 <= hma_period <= 200 or not 2 <= ma_period <= 200 or ma_type not in ('EMA','SMA','LWMA','HMA') \
            or fast_type not in ('EMA','SMA','LWMA','HMA'):
        raise ValueError('Geçersiz strateji parametresi.')
    if not all(math.isfinite(x) and x >= 0 for x in (point, spread_points, commission_points, sl_atr, tp_atr)) or point <= 0:
        raise ValueError('Geçersiz fiyat veya maliyet.')
    if not all(isinstance(x, int) and 0 <= x <= 10000 for x in (sl_points, tp_points)):
        raise ValueError('Geçersiz SL/TP mesafesi.')
    for r in rates:
        if not all(math.isfinite(float(r[k])) for k in ('open','high','low','close')) or float(r['low']) > float(r['high']):
            raise ValueError('Geçersiz mum verisi.')
    closes = [float(r['close']) for r in rates]
    fast = se.ma_series(closes, fast_type, hma_period)   # HMA = the MT5 EA; EMA/SMA/LWMA for presets
    slow = se.ma_series(closes, ma_type, ma_period)
    atr = se.atr_series(rates, atr_period) if (sl_atr or tp_atr) else None
    spread = spread_points * point
    trades = []
    position = None
    min_index = max(int(start), max(hma_period, ma_period) + 12)

    def distance(points, mult, i):
        if mult:
            a = atr[i]
            return math.ceil(a * mult / point) * point if a else None
        return points * point if points else None

    for i in range(min_index, len(rates)-1):
        side = se.cross_at(fast, slow, i)   # candle i is closed; execution at i+1 open
        candle = rates[i+1]
        bid_open = float(candle['open'])
        if position and side and side != position['side']:
            exit_price = bid_open if position['side'] == 'BUY' else bid_open + spread
            _finish(trades, position, exit_price, point, commission_points, i+1, 'opposite signal')
            position = None
        if not position and side:
            entry = bid_open + spread if side == 'BUY' else bid_open
            sl_d, tp_d = distance(sl_points, sl_atr, i), distance(tp_points, tp_atr, i)
            if (sl_atr and sl_d is None) or (tp_atr and tp_d is None):
                continue   # ATR not available yet: the bot would not send either
            g = 1 if side == 'BUY' else -1
            position = {'side': side, 'entry': entry, 'opened_at': i+1,
                        'sl': entry - g * sl_d if sl_d else None,
                        'tp': entry + g * tp_d if tp_d else None}
        if not position:
            continue
        # A bar touching both limits is counted as a stop fill, the conservative outcome.
        buy = position['side'] == 'BUY'
        low, high = float(candle['low']), float(candle['high'])
        if buy:      # buy SL/TP trigger on Bid
            stop_hit = position['sl'] is not None and low <= position['sl']
            target_hit = position['tp'] is not None and high >= position['tp']
        else:        # sell SL/TP trigger on Ask = Bid + spread
            stop_hit = position['sl'] is not None and high + spread >= position['sl']
            target_hit = position['tp'] is not None and low + spread <= position['tp']
        if stop_hit or target_hit:
            if stop_hit:   # a gap through the stop fills at the open
                exit_price = min(position['sl'], bid_open) if buy else max(position['sl'], bid_open + spread)
            else:
                exit_price = position['tp']
            _finish(trades, position, exit_price, point, commission_points, i+1, 'stop' if stop_hit else 'target')
            position = None
    if position:
        final_close = float(rates[-1]['close'])
        exit_price = final_close if position['side'] == 'BUY' else final_close + spread
        _finish(trades, position, exit_price, point, commission_points, len(rates)-1, 'test end')
    split = min_index + int((len(rates) - min_index) * .7)
    return {'success':True, 'symbol_points':True, 'trades':trades[-100:],
            'total_trades':len(trades), 'trades_sample_limit':100, 'tested_candles': len(rates) - min_index,
            'in_sample':_stats([t for t in trades if t['opened_at'] < split]),
            'out_of_sample':_stats([t for t in trades if t['opened_at'] >= split]),
            'assumptions':{'spread_points':spread_points, 'commission_points_round_turn':commission_points,
                           'swap_included':False, 'slippage_included':False,
                           'historical_spread_included':False, 'prices':'MT5 Bid candles; buy at Ask, sell SL/TP on Ask',
                           'stops': 'ATR x multiplier' if (sl_atr or tp_atr) else 'points',
                           'fills':'next candle open; same-bar stop before target'}}


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
