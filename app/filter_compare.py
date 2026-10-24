"""History comparison of the bot's crossover with and without entry filters (read-only; changes no bot rule).

The same `backtest.simulate` runs four times on the same candles: plain crossover (the bot / HMA_Crossover_EA),
+ higher-timeframe trend, + RSI extreme guard (no BUY above rsi_buy_max / SELL below rsi_sell_min),
+ both. The filters are the scanner's own (`signal_engine.HtfTrend`, `rsi_series`, `DEFAULTS`).
"""
from app import signal_engine as se
from app.backtest import simulate

VARIANTS = (('plain', 'Saf kesişim (bot)', False, False), ('htf', '+ Üst zaman dilimi trendi', True, False),
            ('rsi', '+ RSI aşırı bölge koruması', False, True), ('both', '+ İkisi birden', True, True))


def compare(rates, htf_rates, htf_minutes, tf_minutes, *, hma_period, ma_type, ma_period, sl_points, tp_points,
            point, spread_points, commission_points, start, sl_atr=0.0, tp_atr=0.0, atr_period=14):
    s = dict(se.DEFAULTS, hma_period=hma_period, ema_period=ma_period, ma_type=ma_type)
    closes = [float(r['close']) for r in rates]
    rsi = se.rsi_series(closes, s['rsi_period'])
    htf = se.HtfTrend(htf_rates, htf_minutes, s) if htf_rates else None
    seconds = tf_minutes * 60

    def make(use_htf, use_rsi):
        if not (use_htf or use_rsi):
            return None

        def allow(i, side):
            if use_rsi:
                value = rsi[i]
                if value is None or (side == 'BUY' and value > s['rsi_buy_max']) or (side == 'SELL' and value < s['rsi_sell_min']):
                    return False
            if use_htf:
                trend = htf.at(int(rates[i]['time']) + seconds) if htf else None
                if trend != side:
                    return False
            return True
        return allow

    rows = []
    for key, label, use_htf, use_rsi in VARIANTS:
        if use_htf and not htf:
            continue
        result = simulate(rates, hma_period, ma_type, ma_period, sl_points, tp_points, point, spread_points,
                          commission_points, start=start, sl_atr=sl_atr, tp_atr=tp_atr, atr_period=atr_period,
                          entry_filter=make(use_htf, use_rsi))
        rows.append({'key': key, 'label': label, **result['all'], 'out_of_sample': result['out_of_sample']})
    return rows
