"""Buy/sell signal engine for the scanner: HMA(fast) x MA(slow) crossover plus optional filters
and a history test of the same rules.

Indicator formulas follow MT5's built-in indicators exactly, so a value can be checked on the MT5 chart:
- SMA / LWMA: iMA MODE_SMA / MODE_LWMA;
- EMA: iMA MODE_EMA, seeded with the first bar's price (MT5 "Moving Average" source);
- HMA: the MT5 EA (HMA_Crossover_EA.mq5) calculation;
- ATR: simple average of True Range (iATR);
- ADX: iADX, exponential smoothing seeded with 0 on the first bar;
- RSI: iRSI, Wilder smoothing, unrounded;
Recursive indicators (EMA, RSI, ADX) depend on all earlier bars, like MT5 computing them over the
whole chart history; history_bars() asks for enough bars that the seed no longer changes the value.

- volume: tick volume of the candle (MT5 "Volumes", tick).
All series are chronological (oldest first). Candle times are broker times; utc_offset converts
them to UTC for the session filter.
"""
import math

SESSIONS = {'ASIA': (0, 9), 'LONDON': (7, 16), 'NEWYORK': (12, 21)}  # UTC hours, [start, end)
SESSION_LABELS = {'ASIA': 'Asya', 'LONDON': 'Londra', 'NEWYORK': 'New York'}
HTF_AUTO = {1: 15, 5: 60, 15: 60, 30: 240, 60: 240, 240: 1440, 1440: 10080}
TF_CODES = {1: 'M1', 5: 'M5', 15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', 1440: 'D1', 10080: 'W1'}
VOLUME_LOOKBACK = 20

DEFAULTS = dict(hma_period=14, fast_type='HMA', ema_period=34, ma_type='EMA', rsi_period=14, atr_period=14, sl_atr=2.0,
                tp_atr=3.0, confirm_close=True, rsi_filter=False, rsi_buy_max=70.0, rsi_sell_min=30.0,
                htf_filter=False, htf_minutes=0, adx_filter=False, adx_period=14, adx_min=20.0,
                max_spread_points=0, cooldown_bars=0, min_rr=0.0, volume_filter=False, volume_mult=1.0,
                sessions=())


# ---------------------------------------------------------------- moving averages

def _wma_from(values, period, start):
    """Linear weighted MA; values are defined from index `start` on."""
    out = [None] * len(values)
    if period <= 0:
        return out
    den = period * (period + 1) / 2
    for i in range(start + period - 1, len(values)):
        out[i] = sum(values[i - k] * (period - k) for k in range(period)) / den
    return out


def wma_series(closes, period):
    return _wma_from(closes, period, 0)


def hma_series(closes, period):
    if period < 2:
        return [None] * len(closes)
    root = int(math.floor(math.sqrt(period)))
    half, full = _wma_from(closes, period // 2, 0), _wma_from(closes, period, 0)
    diff = [None if full[i] is None else 2.0 * half[i] - full[i] for i in range(len(closes))]
    return _wma_from(diff, root, period - 1)


def sma_series(closes, period):
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        out[i] = sum(closes[i - period + 1:i + 1]) / period
    return out


def ema_series(closes, period):
    """MT5 iMA MODE_EMA: seeded with the first price, then price*k + prev*(1-k) on every bar.

    MT5 starts drawing at bar period-1; earlier bars are None here."""
    out = [None] * len(closes)
    if period <= 0 or len(closes) < period:
        return out
    k = 2.0 / (period + 1.0)
    ema = float(closes[0])
    for i in range(len(closes)):
        if i:
            ema = closes[i] * k + ema * (1.0 - k)
        if i >= period - 1:
            out[i] = ema
    return out


def fast_series(closes, s):
    """Fast line: HMA (the MT5 EA) by default; EMA / SMA / LWMA for the common MA-crossover presets."""
    return ma_series(closes, s.get('fast_type') or 'HMA', s['hma_period'])


def ma_series(closes, ma_type, period):
    if ma_type == 'SMA':
        return sma_series(closes, period)
    if ma_type == 'LWMA':
        return wma_series(closes, period)
    if ma_type == 'HMA':
        return hma_series(closes, period)
    return ema_series(closes, period)


# ---------------------------------------------------------------- oscillators

def true_ranges(rates):
    out = [None]
    for i in range(1, len(rates)):
        h, l, pc = float(rates[i]['high']), float(rates[i]['low']), float(rates[i - 1]['close'])
        out.append(max(h, pc) - min(l, pc))
    return out


def atr_series(rates, period):
    tr = true_ranges(rates)
    out = [None] * len(rates)
    for i in range(period, len(rates)):
        out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def rsi_series(closes, period):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, period + 1)]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, period + 1)]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period

    def value():
        # MT5 iRSI: no rounding (display code rounds).
        return 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    out[period] = value()
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(0.0, diff)) / period
        avg_loss = (avg_loss * (period - 1) + max(0.0, -diff)) / period
        out[i] = value()
    return out


def _mt5_ema(values, period, seed=0.0):
    """MT5 ExponentialMA(position, period, prev_value, price): prev starts at the bar-0 value (0 in iADX)."""
    alpha, prev, out = 2.0 / (period + 1.0), seed, []
    for v in values:
        prev = v * alpha + prev * (1.0 - alpha)
        out.append(prev)
    return out


def adx_series(rates, period):
    """MT5 iADX: +DI/-DI and ADX smoothed with ExponentialMA of `period`; bar 0 is 0."""
    n = len(rates)
    if n < 2:
        return [None] * n
    pdi_raw, ndi_raw = [], []
    for i in range(1, n):
        h, l = float(rates[i]['high']), float(rates[i]['low'])
        ph, pl, pc = float(rates[i - 1]['high']), float(rates[i - 1]['low']), float(rates[i - 1]['close'])
        up, down = max(h - ph, 0.0), max(pl - l, 0.0)
        if up > down:
            down = 0.0
        elif up < down:
            up = 0.0
        else:
            up = down = 0.0
        tr = max(h, pc) - min(l, pc)
        pdi_raw.append(100.0 * up / tr if tr else 0.0)
        ndi_raw.append(100.0 * down / tr if tr else 0.0)
    pdi, ndi = _mt5_ema(pdi_raw, period), _mt5_ema(ndi_raw, period)
    dx = [100.0 * abs(a - b) / (a + b) if a + b else 0.0 for a, b in zip(pdi, ndi)]
    adx = _mt5_ema(dx, period)
    return [0.0] + adx


# ---------------------------------------------------------------- helpers

def htf_minutes_for(tf_minutes, requested):
    if requested and requested > tf_minutes:
        return requested
    return HTF_AUTO.get(tf_minutes)


def cross_at(fast, slow, i):
    if i < 1 or None in (fast[i], slow[i], fast[i - 1], slow[i - 1]):
        return None
    if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
        return 'BUY'
    if fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
        return 'SELL'
    return None


def trend_at(fast, slow, i):
    if i < 0 or fast[i] is None or slow[i] is None:
        return None
    return 'BUY' if fast[i] > slow[i] else 'SELL' if fast[i] < slow[i] else None


class HtfTrend:
    """Higher-timeframe trend at the close of a lower-timeframe candle (closed HTF bars only)."""

    def __init__(self, htf_rates, htf_minutes, s):
        self.rates = htf_rates or []
        self.seconds = int(htf_minutes) * 60
        closes = [float(r['close']) for r in self.rates]
        self.fast = fast_series(closes, s)
        self.slow = ma_series(closes, s['ma_type'], s['ema_period'])

    def at(self, close_time):
        k = None
        for j in range(len(self.rates) - 1, -1, -1):
            if int(self.rates[j]['time']) + self.seconds <= close_time:
                k = j
                break
        return None if k is None else trend_at(self.fast, self.slow, k)


def in_sessions(candle_time, utc_offset, sessions):
    if not sessions:
        return True, None
    hour = int(((int(candle_time) - int(utc_offset)) % 86400) // 3600)
    return any(start <= hour < end for start, end in (SESSIONS[x] for x in sessions if x in SESSIONS)), hour


def _side_tr(side):
    return 'AL' if side == 'BUY' else 'SAT'


class Context:
    """Precomputed series for one symbol so live evaluation and the history test share one code path."""

    def __init__(self, rates, s, tf_minutes, *, htf_rates=None, utc_offset=0, spread_points=None, point=None):
        self.rates, self.s, self.tf = rates, s, tf_minutes
        self.closes = [float(r['close']) for r in rates]
        self.fast = fast_series(self.closes, s)
        self.slow = ma_series(self.closes, s['ma_type'], s['ema_period'])
        self.atr = atr_series(rates, s['atr_period'])
        self.rsi = rsi_series(self.closes, s['rsi_period'])
        self.adx = adx_series(rates, s['adx_period']) if s['adx_filter'] else None
        self.volume = [float(r.get('tick_volume') or 0) for r in rates]
        self.htf_minutes = htf_minutes_for(tf_minutes, s['htf_minutes']) if s['htf_filter'] else None
        self.htf = HtfTrend(htf_rates, self.htf_minutes, s) if self.htf_minutes else None
        self.utc_offset = utc_offset
        self.spread_points, self.point = spread_points, point

    def previous_cross(self, i, limit):
        for j in range(i - 1, max(0, i - limit) - 1, -1):
            if cross_at(self.fast, self.slow, j):
                return j
        return None

    def filters(self, i, side, is_cross, atr, *, live):
        """Blocking reasons for a directional signal on candle i (empty list = passes)."""
        s, out = self.s, []
        rsi = self.rsi[i]
        if s['rsi_filter'] and rsi is not None:
            if side == 'BUY' and rsi > s['rsi_buy_max']:
                out.append(('rsi', f"RSI({s['rsi_period']}) {rsi:.1f} > {s['rsi_buy_max']:g}: AL filtrelendi (aşırı alım)"))
            elif side == 'SELL' and rsi < s['rsi_sell_min']:
                out.append(('rsi', f"RSI({s['rsi_period']}) {rsi:.1f} < {s['rsi_sell_min']:g}: SAT filtrelendi (aşırı satım)"))
        if self.htf_minutes:
            code = TF_CODES.get(self.htf_minutes, f'M{self.htf_minutes}')
            trend = self.htf.at(int(self.rates[i]['time']) + self.tf * 60) if self.htf else None
            if trend is None:
                out.append(('htf', f"Üst zaman dilimi ({code}) trendi doğrulanamadı: filtrelendi"))
            elif trend != side:
                out.append(('htf', f"Üst zaman dilimi ({code}) trendi {_side_tr(trend)}: {_side_tr(side)} filtrelendi"))
        if self.adx is not None:
            adx = self.adx[i]
            if adx is None:
                out.append(('adx', f"ADX({s['adx_period']}) için yeterli mum yok: filtrelendi"))
            elif adx < s['adx_min']:
                out.append(('adx', f"ADX({s['adx_period']}) {adx:.1f} < {s['adx_min']:g}: yatay piyasa, filtrelendi"))
        if live and s['max_spread_points'] and self.spread_points is not None and self.spread_points > s['max_spread_points']:
            out.append(('spread', f"Spread {self.spread_points:g} puan > {s['max_spread_points']:g}: filtrelendi"))
        ok, hour = in_sessions(self.rates[i]['time'], self.utc_offset, s['sessions'])
        if not ok:
            names = '/'.join(SESSION_LABELS[x] for x in s['sessions'] if x in SESSION_LABELS)
            out.append(('session', f"Seans dışı ({hour:02d}:00 UTC; seçili: {names}): filtrelendi"))
        if is_cross and s['cooldown_bars']:
            j = self.previous_cross(i, s['cooldown_bars'])
            if j is not None:
                out.append(('cooldown', f"Önceki kesişim {i - j} mum önce (bekleme {s['cooldown_bars']} mum): filtrelendi"))
        if is_cross and s['volume_filter']:
            window = self.volume[max(0, i - VOLUME_LOOKBACK):i]
            average = sum(window) / len(window) if window else 0.0
            if average > 0 and self.volume[i] < s['volume_mult'] * average:
                out.append(('volume', f"Tick hacmi {self.volume[i]:.0f} < {s['volume_mult']:g} × ortalama {average:.0f}: filtrelendi"))
        if s['min_rr'] and s['sl_atr'] > 0 and s['tp_atr'] > 0 and atr:
            rr = self.reward_risk(atr, side)
            if rr < s['min_rr']:
                out.append(('rr', f"Risk/ödül 1:{rr:.2f} < 1:{s['min_rr']:g} (spread dahil): filtrelendi"))
        return out

    def spread_price(self):
        if self.spread_points and self.point:
            return float(self.spread_points) * float(self.point)
        return 0.0

    def reward_risk(self, atr, side='BUY'):
        """TP distance / SL distance with SL/TP placed from the candle close (Bid), as suggested.

        MT5: a buy opens at Ask and its SL/TP trigger on Bid, so the spread widens the risk and
        shrinks the reward. A sell opens at Bid and its SL/TP trigger on Ask: the spread only makes
        them trigger earlier (modelled in the history test), the distances stay SL/TP × ATR."""
        spread = self.spread_price() if side == 'BUY' else 0.0
        risk, reward = self.s['sl_atr'] * atr + spread, self.s['tp_atr'] * atr - spread
        return max(0.0, reward) / risk if risk > 0 else 0.0


# ---------------------------------------------------------------- history test

def history_stats(ctx, bars=None):
    """Replays every closed-candle crossover in the loaded history with the same filters.

    Entry at the signal candle's close (+ spread), exit at the ATR SL or TP, whichever is touched
    first; a candle touching both counts as a loss. Result in R (1R = the SL distance)."""
    s, rates = ctx.s, ctx.rates
    if not (s['sl_atr'] > 0 and s['tp_atr'] > 0):
        return {'available': False, 'reason': 'Geçmiş testi için SL ve TP (ATR ×) açık olmalı.'}
    last_closed = len(rates) - 2
    start = max(1, last_closed - bars + 1) if bars else 1
    crosses = passed = wins = losses = open_ = 0
    net_r = 0.0
    filter_counts = {}
    trades = []   # every crossover in the window, newest first in the result
    spread = ctx.spread_price()
    for i in range(start, last_closed + 1):
        side = cross_at(ctx.fast, ctx.slow, i)
        atr = ctx.atr[i]
        if not side or not atr:
            continue
        crosses += 1
        blocked = ctx.filters(i, side, True, atr, live=False)
        if blocked:
            for key, _ in blocked:
                filter_counts[key] = filter_counts.get(key, 0) + 1
            trades.append({'time': int(rates[i]['time']), 'side': side, 'entry': ctx.closes[i],
                           'outcome': 'filtered', 'filters': [k for k, _ in blocked]})
            continue
        passed += 1
        close = ctx.closes[i]
        sl = close - s['sl_atr'] * atr if side == 'BUY' else close + s['sl_atr'] * atr
        tp = close + s['tp_atr'] * atr if side == 'BUY' else close - s['tp_atr'] * atr
        win_r = ctx.reward_risk(atr, side)
        outcome, exit_j = None, None
        for j in range(i + 1, last_closed + 1):
            high, low = float(rates[j]['high']), float(rates[j]['low'])
            hit_sl = low <= sl if side == 'BUY' else high + spread >= sl
            hit_tp = high >= tp if side == 'BUY' else low + spread <= tp
            if hit_sl:
                outcome, exit_j = 'loss', j
                break
            if hit_tp:
                outcome, exit_j = 'win', j
                break
        trades.append({'time': int(rates[i]['time']), 'side': side, 'entry': close, 'sl': sl, 'tp': tp,
                       'outcome': outcome or 'open',
                       'r': round(win_r, 2) if outcome == 'win' else -1.0 if outcome == 'loss' else None,
                       'bars': (exit_j - i) if exit_j is not None else None,
                       'exit_time': int(rates[exit_j]['time']) if exit_j is not None else None})
        if outcome == 'win':
            wins += 1
            net_r += win_r
        elif outcome == 'loss':
            losses += 1
            net_r -= 1.0
        else:
            open_ += 1
    closed = wins + losses
    return {
        'available': True, 'bars': last_closed - start + 1,
        'from_time': int(rates[start]['time']) if rates else None,
        'to_time': int(rates[last_closed]['time']) if rates else None,
        'crosses': crosses, 'passed': passed, 'filtered': crosses - passed, 'filter_counts': filter_counts,
        'wins': wins, 'losses': losses, 'open': open_,
        'win_rate_pct': round(100.0 * wins / closed, 1) if closed else None,
        'avg_r': round(net_r / closed, 2) if closed else None, 'net_r': round(net_r, 2),
        'small_sample': closed < 30,
        'trades': trades[::-1][:60],
    }


# ---------------------------------------------------------------- live scan item

def analyze(rates, s, tf_minutes, *, htf_rates=None, utc_offset=0, spread_points=None, point=None,
            stats=False, stats_bars=None):
    s = {**DEFAULTS, **s}
    ctx = Context(rates, s, tf_minutes, htf_rates=htf_rates, utc_offset=utc_offset,
                  spread_points=spread_points, point=point)
    fast_label = f"HMA({s['hma_period']})"
    slow_label = f"{s['ma_type']}({s['ema_period']})"
    last = len(rates) - 1
    idx = last - 1 if s['confirm_close'] else last
    if trend_at(ctx.fast, ctx.slow, idx) is None and (ctx.fast[idx] is None or ctx.slow[idx] is None):
        return None
    h_now, s_now = ctx.fast[idx], ctx.slow[idx]
    if not all(v is not None and math.isfinite(v) and v > 0 for v in (h_now, s_now, ctx.fast[idx - 1], ctx.slow[idx - 1])):
        return None
    cross = cross_at(ctx.fast, ctx.slow, idx)
    bars_ago, cross_side = None, None
    for j in range(idx, max(0, idx - 25), -1):
        found = cross_at(ctx.fast, ctx.slow, j)
        if found:
            bars_ago, cross_side = last - j, found
            break
    if cross:
        signal, side = cross + '_CROSS', cross
    elif h_now > s_now:
        signal, side = 'BUY_TREND', 'BUY'
    elif h_now < s_now:
        signal, side = 'SELL_TREND', 'SELL'
    else:
        signal, side = 'NEUTRAL', 'HOLD'
    closed_idx = last - 1
    rsi = ctx.rsi[closed_idx] if ctx.rsi[closed_idx] is not None else 50.0
    atr = ctx.atr[closed_idx] or 0.0
    price = float(rates[-1]['close'])
    sample = rates[-10:]
    digits = min(8, max(2, max((len(str(r['close']).split('.')[1]) if '.' in str(r['close']) else 2) for r in sample)))
    candle = 'kapanmış mum' if s['confirm_close'] else 'oluşan mum'
    relation = 'üstünde' if h_now > s_now else 'altında' if h_now < s_now else 'eşit'
    reason = f"{fast_label} {h_now:.{digits}f}, {slow_label} {s_now:.{digits}f} {relation} ({candle})"
    if cross:
        reason += f" · {fast_label} {slow_label}'ü {'aşağıdan yukarı' if side == 'BUY' else 'yukarıdan aşağı'} kesti"
    elif bars_ago is not None:
        reason += f" · son kesişim {bars_ago} mum önce ({_side_tr(cross_side)})"
    direction = side  # kept for the reward/risk of a filtered signal
    blocked = ctx.filters(idx, side, bool(cross), atr, live=True) if side in ('BUY', 'SELL') else []
    filtered = '; '.join(text for _, text in blocked) or None
    passed = []
    if side in ('BUY', 'SELL'):
        if ctx.htf_minutes and not any(k == 'htf' for k, _ in blocked):
            passed.append(f"{TF_CODES.get(ctx.htf_minutes, ctx.htf_minutes)} trendi {_side_tr(side)} ✓")
        if ctx.adx is not None and not any(k == 'adx' for k, _ in blocked) and ctx.adx[idx] is not None:
            passed.append(f"ADX {ctx.adx[idx]:.1f} ✓")
    if filtered:
        reason += ' · ' + filtered
        signal, side = 'FILTERED', 'HOLD'
    elif passed:
        reason += ' · ' + ', '.join(passed)
    sl = round(price - s['sl_atr'] * atr if side == 'BUY' else price + s['sl_atr'] * atr, digits) \
        if atr > 0 and s['sl_atr'] > 0 and side in ('BUY', 'SELL') else 0.0
    tp = round(price + s['tp_atr'] * atr if side == 'BUY' else price - s['tp_atr'] * atr, digits) \
        if atr > 0 and s['tp_atr'] > 0 and side in ('BUY', 'SELL') else 0.0
    item = {
        'signal': signal, 'side': side,
        'bars_ago': bars_ago,  # None: no crossover in the last 25 candles 'cross_side': cross_side or side,
        'price': round(price, digits), 'hma': round(h_now, digits), 'ema': round(s_now, digits),
        'rsi': round(rsi, 1) if rsi is not None else None, 'atr': round(atr, digits),
        'adx': round(ctx.adx[idx], 1) if ctx.adx is not None and ctx.adx[idx] is not None else None,
        'spread_points': spread_points,
        'reward_risk': round(ctx.reward_risk(atr, direction), 2) if atr and s['sl_atr'] > 0 and s['tp_atr'] > 0 else None,
        'suggested_sl': max(0.0, sl), 'suggested_tp': max(0.0, tp), 'digits': digits,
        'candle_time': int(rates[idx]['time']), 'reason': reason, 'filtered': filtered,
        'filters': [k for k, _ in blocked],
    }
    if stats:
        item['stats'] = history_stats(ctx, stats_bars)
    return item


def ema_warmup(period):
    """Bars after which an EMA seed is below 1e-10 of its weight (MT5 has the whole chart history)."""
    return int(math.ceil(23.0 * (period + 1) / 2.0)) + period


def wilder_warmup(period):
    return int(math.ceil(23.0 * period)) + period


MAX_HISTORY_BARS = 5000


def history_bars(s):
    """Bars to request so recursive indicators equal MT5's values computed on the full chart."""
    s = {**DEFAULTS, **s}
    need = [required_bars(s), wilder_warmup(s['rsi_period'])]
    if s['ma_type'] == 'EMA':
        need.append(ema_warmup(s['ema_period']))
    if s.get('fast_type') == 'EMA':
        need.append(ema_warmup(s['hma_period']))
    if s['adx_filter']:
        need.append(2 * ema_warmup(s['adx_period']))
    return min(MAX_HISTORY_BARS, max(need))


def required_bars(s):
    s = {**DEFAULTS, **s}
    need = max(s['hma_period'], s['ema_period'], s['rsi_period'] + 1, s['atr_period'] + 1) + 15
    if s['adx_filter']:
        need = max(need, 3 * s['adx_period'] + 5)
    if s['volume_filter']:
        need = max(need, VOLUME_LOOKBACK + 2)
    return need
