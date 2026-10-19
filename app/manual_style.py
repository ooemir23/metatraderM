"""Learn from positions opened by hand in the original MT5 terminal and signal similar setups.

What it does
- Positions opened within `merge_seconds` of each other on the same symbol and side are one
  decision (several tickets sent at once are one choice, not many).
- For every decision it reads the market on the last *closed* candle before the entry, with the
  same MT5 formulas as the signal scanner (signal_engine: HMA, iMA EMA, iRSI, iATR, iADX).
- A candle (and side) is compared with past decisions by a nearest-neighbour vote: it "looks like
  your entries" when it is as close to them as they are to each other, and it is a signal when
  those nearest entries were mostly winners.
- The same rule is replayed walk-forward over the loaded history (each candle only knows the
  decisions already closed at that time), and the signals' forward move is compared with entering
  on every candle. If the signals are not better than that baseline, the verdict says so.

Only closed candles are used; nothing here can place an order.
"""
import math

import numpy as np

from app import signal_engine as se

DEFAULTS = dict(hma_period=14, ema_period=34, ma_type='EMA', rsi_period=14, atr_period=14, adx_period=14,
                merge_seconds=120, k=7, min_score=0.4, horizon=8, min_decisions=12, min_signals=30,
                min_edge=0.05, min_z=2.33)
FEATURES = ('trend', 'slope', 'htf', 'rsi', 'adx', 'dist', 'mom', 'range', 'hour_sin', 'hour_cos')
FEATURE_LABELS = {
    'trend': 'HMA/EMA trend yönü', 'slope': 'HMA eğimi', 'htf': 'Üst zaman dilimi trendi',
    'rsi': 'RSI', 'adx': 'ADX', 'dist': "Fiyatın EMA'ya uzaklığı (ATR)",
    'mom': 'Son 4 mum hareketi (ATR)', 'range': 'Son 20 mum aralığındaki yer'}
LOOKBACK_RANGE = 20
MOMENTUM_BARS = 4


def merge_decisions(trades, merge_seconds=120):
    """Group positions of one symbol+side opened within merge_seconds of the first one."""
    out = []
    for t in sorted(trades, key=lambda r: (r['open_time'], r['position_id'])):
        last = next((d for d in reversed(out) if d['symbol'] == t['symbol'] and d['side'] == t['side']), None)
        if last and t['open_time'] - last['open_time'] <= merge_seconds:
            vol = last['volume'] + t['volume']
            last['open_price'] = (last['open_price'] * last['volume'] + t['open_price'] * t['volume']) / vol
            last['volume'] = vol
            last['net'] = round(last['net'] + t['net'], 2)
            last['close_time'] = max(last['close_time'], t['close_time'])
            last['positions'].append(t['position_id'])
            continue
        out.append(dict(symbol=t['symbol'], side=t['side'], open_time=t['open_time'], open_price=t['open_price'],
                        close_time=t['close_time'], volume=t['volume'], net=t['net'],
                        positions=[t['position_id']]))
    for d in out:
        d['win'] = bool(d['net'] > 0)
    return out


class Market:
    """Indicator series for one symbol/timeframe (chronological, broker times)."""

    def __init__(self, rates, tf_minutes, s, htf_rates=None, htf_minutes=None, utc_offset=0):
        self.rates, self.tf, self.s = rates, int(tf_minutes), s
        self.times = np.array([int(r['time']) for r in rates], dtype=np.int64)
        self.close = np.array([float(r['close']) for r in rates])
        self.high = np.array([float(r['high']) for r in rates])
        self.low = np.array([float(r['low']) for r in rates])
        closes = list(self.close)
        self.fast = _arr(se.hma_series(closes, s['hma_period']))
        self.slow = _arr(se.ma_series(closes, s['ma_type'], s['ema_period']))
        self.atr = _arr(se.atr_series(rates, s['atr_period']))
        self.rsi = _arr(se.rsi_series(closes, s['rsi_period']))
        self.adx = _arr(se.adx_series(rates, s['adx_period']))
        self.utc_offset = int(utc_offset)
        self.htf = None
        if htf_rates and htf_minutes:
            self.htf = se.HtfTrend(htf_rates, htf_minutes, s)
            self._htf_times = [int(r['time']) for r in htf_rates]
            self._htf_seconds = int(htf_minutes) * 60
        self.warmup = max(se.history_bars(s), LOOKBACK_RANGE + MOMENTUM_BARS)

    def last_closed_before(self, t):
        """Index of the last candle whose close time (open + tf) is at or before t."""
        i = int(np.searchsorted(self.times + self.tf * 60, int(t), side='right')) - 1
        return i if i >= 0 else None

    def htf_trend(self, i):
        if self.htf is None:
            return 0.0
        close_time = int(self.times[i]) + self.tf * 60
        k = int(np.searchsorted(np.array(self._htf_times) + self._htf_seconds, close_time, side='right')) - 1
        if k < 0:
            return 0.0
        t = se.trend_at(self.htf.fast, self.htf.slow, k)
        return 1.0 if t == 'BUY' else -1.0 if t == 'SELL' else 0.0

    def usable(self, i):
        return (i is not None and self.warmup <= i < len(self.rates) and
                all(np.isfinite(x[i]) for x in (self.fast, self.slow, self.atr, self.rsi, self.adx)) and
                np.isfinite(self.fast[i - 1]) and self.atr[i] > 0)

    def features(self, i, side):
        """Side-aligned description of candle i (positive = in favour of `side`)."""
        sgn = 1.0 if side == 'BUY' else -1.0
        a = self.atr[i]
        lo = self.low[i - LOOKBACK_RANGE + 1:i + 1].min()
        hi = self.high[i - LOOKBACK_RANGE + 1:i + 1].max()
        pos = (self.close[i] - lo) / (hi - lo) if hi > lo else 0.5
        hour = ((int(self.times[i]) - self.utc_offset) % 86400) / 3600.0
        return dict(
            trend=float(np.sign(self.fast[i] - self.slow[i]) * sgn),
            slope=float(np.sign(self.fast[i] - self.fast[i - 1]) * sgn),
            htf=self.htf_trend(i) * sgn,
            rsi=float((self.rsi[i] - 50.0) / 50.0 * sgn),
            adx=float(self.adx[i] / 50.0),
            dist=float(np.clip((self.close[i] - self.slow[i]) / a * sgn, -4, 4)),
            mom=float(np.clip((self.close[i] - self.close[i - MOMENTUM_BARS]) / a * sgn, -4, 4)),
            range=float(pos if sgn > 0 else 1.0 - pos),
            hour_sin=math.sin(2 * math.pi * hour / 24), hour_cos=math.cos(2 * math.pi * hour / 24))

    def forward(self, i, side, horizon, spread):
        """Move after entering at candle i's close (Bid; a buy pays the spread), in ATR units."""
        j = i + horizon
        if j >= len(self.rates) - 1:  # the last row is the forming candle
            return None
        sgn = 1.0 if side == 'BUY' else -1.0
        entry = self.close[i] + (spread if sgn > 0 else 0.0)
        exit_ = self.close[j] + (0.0 if sgn > 0 else spread)
        seg_hi, seg_lo = self.high[i + 1:j + 1].max(), self.low[i + 1:j + 1].min()
        mfe = (seg_hi - entry) if sgn > 0 else (entry - (seg_lo + spread))
        mae = (entry - seg_lo) if sgn > 0 else ((seg_hi + spread) - entry)
        a = self.atr[i]
        return dict(move=float((exit_ - entry) * sgn / a), mfe=float(mfe / a), mae=float(mae / a))


def _arr(values):
    return np.array([np.nan if v is None else float(v) for v in values])


def _vector(f):
    return np.array([f[k] for k in FEATURES])


class Model:
    """Nearest-neighbour vote over past decisions (feature vectors already scaled)."""

    def __init__(self, X, wins, k):
        self.X, self.wins, self.k = X, np.asarray(wins, dtype=bool), int(k)
        n = len(X)
        self.ready = n >= 3
        if not self.ready:
            self.radius = 0.0
            return
        kk = min(self.k, n - 1)
        d = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(d, np.inf)
        own = np.sort(d, axis=1)[:, :kk].mean(1)
        # "Looks like your entries": as close to them as they usually are to each other.
        self.radius = float(np.percentile(own, 75))

    def vote(self, v):
        if not self.ready:
            return None
        d = np.sqrt(((self.X - v) ** 2).sum(1))
        kk = min(self.k, len(d))
        idx = np.argsort(d)[:kk]
        w = 1.0 / (1.0 + d[idx])
        score = float((w * np.where(self.wins[idx], 1.0, -1.0)).sum() / w.sum())
        return dict(score=score, distance=float(d[idx].mean()), similar=bool(d[idx].mean() <= self.radius),
                    neighbours=[int(x) for x in idx], win_share=float(self.wins[idx].mean()))


def _scaler(market, start):
    rows = [_vector(market.features(i, side)) for i in range(start, len(market.rates) - 1)
            if market.usable(i) for side in ('BUY', 'SELL')]
    if not rows:
        return None
    m = np.array(rows)
    mean, std = m.mean(0), m.std(0)
    std[std < 1e-9] = 1.0
    return mean, std


def _summary(a):
    a = [x for x in a if x is not None]
    if not a:
        return dict(count=0, hit_rate=None, avg_move=None, mfe_gt_mae=None)
    return dict(count=len(a), hit_rate=round(sum(x['move'] > 0 for x in a) / len(a), 3),
                avg_move=round(sum(x['move'] for x in a) / len(a), 3),
                mfe_gt_mae=round(sum(x['mfe'] > x['mae'] for x in a) / len(a), 3))


def _profile(decisions):
    """Plain-language description of what the winning and losing entries had in common."""
    def med(rows, key):
        vals = [r['features'][key] for r in rows]
        return round(float(np.median(vals)), 3) if vals else None

    def share(rows, key):
        vals = [r['features'][key] for r in rows]
        return round(sum(v > 0 for v in vals) / len(vals), 3) if vals else None

    wins = [d for d in decisions if d['win']]
    losses = [d for d in decisions if not d['win']]
    out = []
    for key in ('trend', 'slope', 'htf'):
        out.append(dict(feature=key, label=FEATURE_LABELS[key], kind='share',
                        wins=share(wins, key), losses=share(losses, key)))
    for key in ('rsi', 'adx', 'dist', 'mom', 'range'):
        w, l = med(wins, key), med(losses, key)
        if key == 'rsi':
            w = None if w is None else round(50 + 50 * w, 1)
            l = None if l is None else round(50 + 50 * l, 1)
        if key == 'adx':
            w = None if w is None else round(50 * w, 1)
            l = None if l is None else round(50 * l, 1)
        out.append(dict(feature=key, label=FEATURE_LABELS[key], kind='median', wins=w, losses=l))
    hours = {}
    for d in decisions:
        h = d['hour_utc']
        g = hours.setdefault(h, [0, 0])
        g[0] += 1
        g[1] += int(d['win'])
    return dict(features=out, hours_utc={str(h): dict(count=v[0], wins=v[1]) for h, v in sorted(hours.items())})


def analyze(trades, rates, tf_minutes, *, symbol, settings=None, htf_rates=None, utc_offset=0,
            spread=0.0, now=None):
    """Learn from `trades` (manual_lifetimes rows) on `rates` and return profile, test and live signal."""
    s = {**DEFAULTS, **(settings or {})}
    htf_minutes = se.HTF_AUTO.get(int(tf_minutes))
    m = Market(rates, tf_minutes, s, htf_rates=htf_rates, htf_minutes=htf_minutes, utc_offset=utc_offset)
    decisions_all = merge_decisions([t for t in trades if t['symbol'] == symbol], s['merge_seconds'])
    decisions, skipped = [], 0
    for d in decisions_all:
        i = m.last_closed_before(d['open_time'])
        if not m.usable(i):
            skipped += 1
            continue
        f = m.features(i, d['side'])
        hour = int(((int(m.times[i]) - m.utc_offset) % 86400) // 3600)
        decisions.append({**d, 'bar_index': i, 'candle_time': int(m.times[i]), 'features': f, 'hour_utc': hour,
                          'after': m.forward(i, d['side'], s['horizon'], spread)})
    result = dict(symbol=symbol, timeframe_minutes=int(tf_minutes), htf_minutes=htf_minutes, settings=s,
                  positions=sum(len(d['positions']) for d in decisions_all), decisions=len(decisions),
                  skipped_before_history=skipped, wins=int(sum(bool(d['win']) for d in decisions)),
                  net=round(float(sum(d['net'] for d in decisions)), 2))
    if len(decisions) < s['min_decisions']:
        result.update(status='NOT_ENOUGH', verdict=None, signal=None, history_test=None, profile=None,
                      message=f"{symbol} için öğrenecek yeterli manuel karar yok "
                              f"({len(decisions)}/{s['min_decisions']}).")
        return result
    start = m.warmup
    scale = _scaler(m, start)
    mean, std = scale

    def vec(i, side):
        return (_vector(m.features(i, side)) - mean) / std

    X = np.array([(_vector(d['features']) - mean) / std for d in decisions])
    wins = [d['win'] for d in decisions]
    result['profile'] = _profile(decisions)
    result['entry_quality'] = _summary([d['after'] for d in decisions])

    # Walk-forward replay: each candle only uses decisions already closed at its close time.
    first_close = min(d['close_time'] for d in decisions)
    signals, baseline, prev = [], [], None
    cache = {}
    for i in range(start, len(m.rates) - 1):
        if not m.usable(i):
            continue
        close_time = int(m.times[i]) + m.tf * 60
        if close_time < first_close:
            continue
        for side in ('BUY', 'SELL'):
            baseline.append(m.forward(i, side, s['horizon'], spread))
        known = tuple(j for j, d in enumerate(decisions) if d['close_time'] <= close_time)
        if len(known) < s['min_decisions']:
            prev = None
            continue
        model = cache.get(known)
        if model is None:
            model = cache[known] = Model(X[list(known)], [wins[j] for j in known], s['k'])
        best = None
        for side in ('BUY', 'SELL'):
            v = model.vote(vec(i, side))
            if v and v['similar'] and v['score'] >= s['min_score'] and (best is None or v['score'] > best[1]['score']):
                best = (side, v)
        side = best[0] if best else None
        if side and side != prev:  # one signal per setup, like a crossover arrow
            signals.append(dict(candle_time=int(m.times[i]), side=side, score=round(best[1]['score'], 3),
                                price=float(m.close[i]), after=m.forward(i, side, s['horizon'], spread)))
        prev = side
    # Signals closer than `horizon` candles share the same move; count them once.
    independent, last_i = [], None
    for x in signals:
        i = int(np.searchsorted(m.times, x['candle_time']))
        if last_i is None or i - last_i >= s['horizon']:
            independent.append(x)
            last_i = i
    sig_stats, base_stats = _summary([x['after'] for x in independent]), _summary(baseline)
    sig_stats['all_signals'] = len(signals)
    edge = z = None
    if sig_stats['count'] and base_stats['count']:
        edge = round(sig_stats['hit_rate'] - base_stats['hit_rate'], 3)
        p0 = min(max(base_stats['hit_rate'], 1e-6), 1 - 1e-6)
        z = round((sig_stats['hit_rate'] - p0) / math.sqrt(p0 * (1 - p0) / sig_stats['count']), 2)
    reliable = bool(sig_stats['count'] >= s['min_signals'] and edge is not None and edge >= s['min_edge']
                    and z >= s['min_z'] and sig_stats['avg_move'] > base_stats['avg_move'])
    if sig_stats['count'] < s['min_signals']:
        reason = f"Geçmiş testte yalnız {sig_stats['count']} bağımsız sinyal çıktı (en az {s['min_signals']} gerekli)."
    elif not reliable and edge is not None and edge > 0:
        reason = ("Geçmiş testte sinyaller her mumda girmekten biraz iyi ama fark şans olabilir "
                  f"(isabet %{100 * sig_stats['hit_rate']:.0f}, her mum %{100 * base_stats['hit_rate']:.0f}).")
    elif not reliable:
        reason = ("Geçmiş testte sinyaller her mumda girmekten daha iyi değil "
                  f"(isabet %{100 * sig_stats['hit_rate']:.0f}, her mum %{100 * base_stats['hit_rate']:.0f}).")
    else:
        reason = ("Geçmiş testte sinyaller her mumda girmekten daha iyi "
                  f"(isabet %{100 * sig_stats['hit_rate']:.0f}, her mum %{100 * base_stats['hit_rate']:.0f}).")
    result['history_test'] = dict(horizon_bars=s['horizon'], signals=sig_stats, baseline=base_stats, edge=edge, z=z,
                                  recent=signals[-20:])
    result['verdict'] = dict(reliable=reliable, reason=reason)

    # Live: last closed candle, model trained on every decision closed so far.
    live_i = len(m.rates) - 2
    known = [j for j, d in enumerate(decisions) if d['close_time'] <= (now or int(m.times[-1]) + m.tf * 60)]
    signal = None
    if m.usable(live_i) and len(known) >= s['min_decisions']:
        model = Model(X[known], [wins[j] for j in known], s['k'])
        votes = {side: model.vote(vec(live_i, side)) for side in ('BUY', 'SELL')}
        side, v = max(votes.items(), key=lambda kv: kv[1]['score'])
        active = v['similar'] and v['score'] >= s['min_score']
        near = [decisions[known[j]] for j in v['neighbours']]
        signal = dict(side=side if active else 'HOLD', candidate_side=side, score=round(v['score'], 3),
                      similar=v['similar'], win_share=round(v['win_share'], 3),
                      candle_time=int(m.times[live_i]), price=float(m.close[live_i]),
                      atr=float(m.atr[live_i]), experimental=not reliable,
                      neighbours=[dict(open_time=d['open_time'], side=d['side'], net=d['net'], win=d['win'])
                                  for d in near])
    result['signal'] = signal
    result['status'] = 'OK'
    result['message'] = reason
    return result
