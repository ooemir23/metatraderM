"""Learn from positions opened by hand in the original MT5 terminal and signal similar setups.

What it does
- Every closed manual position is read; positions opened within `merge_seconds` of each other on
  the same symbol and side are one decision (several tickets sent at once are one choice).
- For every decision it reads the market on the last *closed* candle before the entry with the
  same MT5 formulas as the signal scanner (signal_engine: HMA, iMA EMA, iRSI, iATR, iADX) plus
  context a trader looks at without saying so: previous day high/low, day open, round prices,
  H1/H4/D1 trends, session, candle wick/body, volatility regime and the recent swing high/low.
- Findings: which of those differ between your winning and losing entries (permutation test),
  and what your entries have in common compared with ordinary candles (your habits).
- Model: nearest-neighbour vote on the features that best separate your wins from your losses
  (chosen only from trades already closed, so the test never sees the future).
- Checks: leave-one-out on your own trades (does it predict which of your entries won?) and a
  walk-forward replay over the loaded candles compared with entering on every candle.

Only closed candles are used; nothing here can place an order.
"""
import math

import numpy as np

from app import signal_engine as se

DEFAULTS = dict(hma_period=14, ema_period=34, ma_type='EMA', rsi_period=14, atr_period=14, adx_period=14,
                merge_seconds=120, k=7, top_features=6, min_score=0.4, horizon=8, min_decisions=12,
                min_signals=30, min_edge=0.05, min_z=2.33, permutations=2000)
DAY = 86400
SESSIONS_UTC = {'asia': (0, 7), 'london': (7, 13), 'newyork': (13, 21)}
LOOKBACK_RANGE = 20
SWING_BARS = 50
MOMENTUM_BARS = 4
VOL_BARS = 100

# name: (label, kind, unit). kind "flag" = share of entries where the value is > 0.
FEATURES = {
    'trend': ('Grafik trendi (HMA14 / EMA34) işlem yönünde', 'flag', ''),
    'slope': ('HMA eğimi işlem yönünde', 'flag', ''),
    'htf': ('Bir üst zaman dilimi trendi işlem yönünde', 'flag', ''),
    'h4': ('H4 trendi işlem yönünde', 'flag', ''),
    'd1': ('Günlük (D1) trend işlem yönünde', 'flag', ''),
    'body': ('Son mum işlem yönünde kapanmış', 'flag', ''),
    'asia': ('Asya seansında giriş', 'flag', ''),
    'london': ('Londra seansında giriş', 'flag', ''),
    'newyork': ('New York seansında giriş', 'flag', ''),
    'rsi': ('Yöne göre RSI (alışta RSI, satışta 100−RSI)', 'value', ''),
    'adx': ('ADX (trend gücü)', 'value', ''),
    'dist': ("Fiyatın EMA34'ten işlem yönündeki uzaklığı", 'value', 'ATR'),
    'mom': ('Son 4 mumda işlem yönündeki hareket', 'value', 'ATR'),
    'range': ('Son 20 mum aralığında yön tarafındaki yer (1 = en uç)', 'value', ''),
    'swing': ('Son 50 mumun karşı ucuna uzaklık (gidilebilecek yer)', 'value', 'ATR'),
    'pdh': ('Önceki gün tepesine göre konum (+ = yön tarafında)', 'value', 'ATR'),
    'pdl': ('Önceki gün dibine göre konum (+ = yön tarafında)', 'value', 'ATR'),
    'dopen': ('Gün açılışına göre konum (+ = yön tarafında)', 'value', 'ATR'),
    'level': ('En yakın önemli seviyeye uzaklık (önceki gün tepe/dip, gün açılışı)', 'value', 'ATR'),
    'round': ('En yakın yuvarlak fiyata uzaklık', 'value', 'ATR'),
    'wick': ('Son mumda ters yönden gelen fitil (red) oranı', 'value', ''),
    'vol': ('Oynaklık (ATR / son 100 mum ortalaması)', 'value', ''),
    'hour_sin': ('Saat (sin)', 'hidden', ''),
    'hour_cos': ('Saat (cos)', 'hidden', ''),
}
NAMES = tuple(FEATURES)


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
            last['close_price'] = t.get('close_price', last.get('close_price'))
            last['positions'].append(t['position_id'])
            continue
        out.append(dict(symbol=t['symbol'], side=t['side'], open_time=t['open_time'], open_price=t['open_price'],
                        close_time=t['close_time'], close_price=t.get('close_price'), volume=t['volume'],
                        net=t['net'], positions=[t['position_id']]))
    for d in out:
        d['win'] = bool(d['net'] > 0)
    return out


def _arr(values):
    return np.array([np.nan if v is None else float(v) for v in values])


def round_step(price):
    """Round-number grid for a price: 4150 -> 10, 1.32 -> 0.01, 81000 -> 100."""
    return 10.0 ** (math.floor(math.log10(abs(price))) - 2) if price else 1.0


class _Trend:
    """HMA/MA trend of a higher timeframe at a lower-timeframe close time (closed bars only)."""

    def __init__(self, rates, minutes, s):
        self.ok = bool(rates) and bool(minutes)
        if not self.ok:
            return
        closes = [float(r['close']) for r in rates]
        self.fast, self.slow = se.hma_series(closes, s['hma_period']), se.ma_series(closes, s['ma_type'], s['ema_period'])
        self.close_times = np.array([int(r['time']) for r in rates], dtype=np.int64) + int(minutes) * 60

    def at(self, close_time):
        if not self.ok:
            return 0.0
        k = int(np.searchsorted(self.close_times, close_time, side='right')) - 1
        t = se.trend_at(self.fast, self.slow, k) if k >= 0 else None
        return 1.0 if t == 'BUY' else -1.0 if t == 'SELL' else 0.0


class Market:
    """Indicator series for one symbol/timeframe (chronological, broker times)."""

    def __init__(self, rates, tf_minutes, s, htf_rates=None, htf_minutes=None, utc_offset=0,
                 h4_rates=None, d1_rates=None):
        self.rates, self.tf, self.s = rates, int(tf_minutes), s
        self.times = np.array([int(r['time']) for r in rates], dtype=np.int64)
        self.open = np.array([float(r['open']) for r in rates])
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
        self.htf = _Trend(htf_rates, htf_minutes, s)
        self.h4 = _Trend(h4_rates, 240, s)
        self.d1 = _Trend(d1_rates, 1440, s)
        self._days()
        self.warmup = max(se.history_bars(s), SWING_BARS, VOL_BARS) + 1

    def _days(self):
        """Broker server days (MT5 day = server 00:00): previous day high/low and today's open."""
        day = self.times // DAY
        n = len(day)
        self.pdh, self.pdl, self.dopen = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
        start, prev_hi, prev_lo = 0, np.nan, np.nan
        for i in range(1, n + 1):
            if i == n or day[i] != day[start]:
                self.pdh[start:i], self.pdl[start:i], self.dopen[start:i] = prev_hi, prev_lo, self.open[start]
                prev_hi, prev_lo = self.high[start:i].max(), self.low[start:i].min()
                start = i
        # The first loaded day has no complete previous day.
        first = day[0]
        self.pdh[day == first] = np.nan
        self.pdl[day == first] = np.nan

    def last_closed_before(self, t):
        """Index of the last candle whose close time (open + tf) is at or before t."""
        i = int(np.searchsorted(self.times + self.tf * 60, int(t), side='right')) - 1
        return i if i >= 0 else None

    def usable(self, i):
        return (i is not None and self.warmup <= i < len(self.rates) and
                all(np.isfinite(x[i]) for x in (self.fast, self.slow, self.atr, self.rsi, self.adx, self.pdh)) and
                np.isfinite(self.fast[i - 1]) and self.atr[i] > 0)

    def hour_utc(self, i):
        return int(((int(self.times[i]) - self.utc_offset) % DAY) // 3600)

    def features(self, i, side):
        """Side-aligned description of candle i (positive = in favour of `side`)."""
        g = 1.0 if side == 'BUY' else -1.0
        a, c = self.atr[i], self.close[i]
        lo20, hi20 = self.low[i - LOOKBACK_RANGE + 1:i + 1].min(), self.high[i - LOOKBACK_RANGE + 1:i + 1].max()
        pos = (c - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
        lo50, hi50 = self.low[i - SWING_BARS + 1:i + 1].min(), self.high[i - SWING_BARS + 1:i + 1].max()
        room = (hi50 - c) if g > 0 else (c - lo50)
        rng = self.high[i] - self.low[i]
        wick = ((min(self.open[i], c) - self.low[i]) if g > 0 else (self.high[i] - max(self.open[i], c)))
        step = round_step(c)
        to_round = abs(c - round(c / step) * step)
        levels = [self.pdh[i], self.pdl[i], self.dopen[i]]
        close_time = int(self.times[i]) + self.tf * 60
        hour = ((int(self.times[i]) - self.utc_offset) % DAY) / 3600.0
        h = int(hour)
        clip = lambda x, lim=6.0: float(np.clip(x, -lim, lim))
        return dict(
            trend=float(np.sign(self.fast[i] - self.slow[i]) * g),
            slope=float(np.sign(self.fast[i] - self.fast[i - 1]) * g),
            htf=self.htf.at(close_time) * g, h4=self.h4.at(close_time) * g, d1=self.d1.at(close_time) * g,
            body=float(np.sign(c - self.open[i]) * g),
            asia=float(SESSIONS_UTC['asia'][0] <= h < SESSIONS_UTC['asia'][1]),
            london=float(SESSIONS_UTC['london'][0] <= h < SESSIONS_UTC['london'][1]),
            newyork=float(SESSIONS_UTC['newyork'][0] <= h < SESSIONS_UTC['newyork'][1]),
            rsi=float(50 + (self.rsi[i] - 50) * g),
            adx=float(self.adx[i]),
            dist=clip((c - self.slow[i]) / a * g),
            mom=clip((c - self.close[i - MOMENTUM_BARS]) / a * g),
            range=float(pos if g > 0 else 1.0 - pos),
            swing=clip(room / a, 10),
            pdh=clip((c - self.pdh[i]) / a * g, 10), pdl=clip((c - self.pdl[i]) / a * g, 10),
            dopen=clip((c - self.dopen[i]) / a * g, 10),
            level=clip(min(abs(c - x) for x in levels) / a, 10),
            round=clip(to_round / a, 10),
            wick=float(wick / rng) if rng > 0 else 0.0,
            vol=float(a / np.nanmean(self.atr[i - VOL_BARS + 1:i + 1])),
            hour_sin=math.sin(2 * math.pi * hour / 24), hour_cos=math.cos(2 * math.pi * hour / 24))

    def forward(self, i, side, horizon, spread):
        """Move after entering at candle i's close (Bid; a buy pays the spread), in ATR units."""
        j = i + horizon
        if j >= len(self.rates) - 1:  # the last row is the forming candle
            return None
        g = 1.0 if side == 'BUY' else -1.0
        entry = self.close[i] + (spread if g > 0 else 0.0)
        exit_ = self.close[j] + (0.0 if g > 0 else spread)
        seg_hi, seg_lo = self.high[i + 1:j + 1].max(), self.low[i + 1:j + 1].min()
        mfe = (seg_hi - entry) if g > 0 else (entry - (seg_lo + spread))
        mae = (entry - seg_lo) if g > 0 else ((seg_hi + spread) - entry)
        a = self.atr[i]
        return dict(move=float((exit_ - entry) * g / a), mfe=float(mfe / a), mae=float(mae / a))


def _vector(f):
    return np.array([f[k] for k in NAMES])


def _separation(X, wins):
    """|standardised mean difference| of each feature between wins and losses."""
    w, l = X[wins], X[~wins]
    if len(w) < 3 or len(l) < 3:
        return np.zeros(X.shape[1])
    pooled = np.sqrt((w.var(0) + l.var(0)) / 2) + 1e-9
    return np.abs(w.mean(0) - l.mean(0)) / pooled


class Model:
    """Nearest-neighbour vote on the features that best separate past wins from losses."""

    def __init__(self, X, wins, k, top):
        self.X, self.wins, self.k = X, np.asarray(wins, dtype=bool), int(k)
        n = len(X)
        self.ready = n >= 3
        sep = _separation(X, self.wins)
        sep[[NAMES.index('hour_sin'), NAMES.index('hour_cos')]] *= 0.5  # time of day counts as one feature
        self.cols = np.argsort(-sep)[:max(1, int(top))] if sep.any() else np.arange(X.shape[1])
        self.weights = np.ones(len(self.cols))
        if not self.ready:
            self.radius = 0.0
            return
        Z = X[:, self.cols]
        kk = min(self.k, n - 1)
        d = np.sqrt(((Z[:, None, :] - Z[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(d, np.inf)
        own = np.sort(d, axis=1)[:, :kk].mean(1)
        # "Looks like your entries": as close to them as they usually are to each other.
        self.radius = float(np.percentile(own, 75))

    def vote(self, v):
        if not self.ready:
            return None
        d = np.sqrt(((self.X[:, self.cols] - v[self.cols]) ** 2).sum(1))
        kk = min(self.k, len(d))
        idx = np.argsort(d)[:kk]
        w = 1.0 / (1.0 + d[idx])
        score = float((w * np.where(self.wins[idx], 1.0, -1.0)).sum() / w.sum())
        return dict(score=score, distance=float(d[idx].mean()), similar=bool(d[idx].mean() <= self.radius),
                    neighbours=[int(x) for x in idx], win_share=float(self.wins[idx].mean()))

    def used(self):
        return [NAMES[c] for c in self.cols]


def _summary(a):
    a = [x for x in a if x is not None]
    if not a:
        return dict(count=0, hit_rate=None, avg_move=None, mfe_gt_mae=None)
    return dict(count=len(a), hit_rate=round(sum(x['move'] > 0 for x in a) / len(a), 3),
                avg_move=round(sum(x['move'] for x in a) / len(a), 3),
                mfe_gt_mae=round(sum(x['mfe'] > x['mae'] for x in a) / len(a), 3))


def _stat(values, kind):
    if not len(values):
        return None
    return round(float((values > 0).mean() if kind == 'flag' else np.median(values)), 3)


def _perm_p(a, b, rng, n):
    """Two-sided permutation p-value for the difference in means of a and b."""
    if len(a) < 3 or len(b) < 3:
        return None
    pool = np.concatenate([a, b])
    obs = abs(a.mean() - b.mean())
    if pool.std() < 1e-12:
        return 1.0
    idx = np.array([rng.permutation(len(pool)) for _ in range(n)])
    pa = pool[idx[:, :len(a)]].mean(1)
    pb = pool[idx[:, len(a):]].mean(1)
    return float(((np.abs(pa - pb) >= obs - 1e-12).sum() + 1) / (n + 1))


def _fmt(v, kind, unit):
    if v is None:
        return '—'
    if kind == 'flag':
        return f'%{round(100 * v)}'
    return f'{v:.2f}{(" " + unit) if unit else ""}'


def findings(decisions, raw_baseline, permutations=2000, seed=7):
    """What separates your wins from your losses, and what your entries share vs ordinary candles."""
    rng = np.random.default_rng(seed)
    F = np.array([[d['features'][k] for k in NAMES] for d in decisions])
    wins = np.array([d['win'] for d in decisions], dtype=bool)
    B = np.array(raw_baseline) if len(raw_baseline) else np.empty((0, len(NAMES)))
    win_loss, habits = [], []
    for j, name in enumerate(NAMES):
        label, kind, unit = FEATURES[name]
        if kind == 'hidden':
            continue
        col = F[:, j]
        w, l = col[wins], col[~wins]
        p = _perm_p(w, l, rng, permutations)
        sd = col.std() + 1e-9
        effect = float(abs(w.mean() - l.mean()) / sd) if len(w) and len(l) else 0.0
        row = dict(feature=name, label=label, kind=kind, wins=_stat(w, kind), losses=_stat(l, kind),
                   p=None if p is None else round(p, 3), effect=round(effect, 2))
        if p is not None:
            better = 'kazananlarda' if w.mean() > l.mean() else 'kaybedenlerde'
            row['text'] = (f"{label}: kazananlarda {_fmt(row['wins'], kind, unit)}, "
                           f"kaybedenlerde {_fmt(row['losses'], kind, unit)} ({better} daha yüksek)")
        win_loss.append(row)
        if len(B):
            base = B[:, j]
            p2 = _perm_p(col, rng.choice(base, size=min(len(base), 400), replace=False), rng, permutations // 4)
            hb = dict(feature=name, label=label, kind=kind, yours=_stat(col, kind), usual=_stat(base, kind),
                      p=None if p2 is None else round(p2, 3),
                      effect=round(float(abs(col.mean() - base.mean()) / (base.std() + 1e-9)), 2))
            hb['text'] = (f"{label}: senin girişlerinde {_fmt(hb['yours'], kind, unit)}, "
                          f"sıradan mumlarda {_fmt(hb['usual'], kind, unit)}")
            habits.append(hb)
    win_loss.sort(key=lambda r: (r['p'] if r['p'] is not None else 2, -r['effect']))
    habits.sort(key=lambda r: (r['p'] if r['p'] is not None else 2, -r['effect']))
    for r in win_loss + habits:
        p = r['p']
        r['strength'] = ('belirgin' if p is not None and p < 0.01 else 'olası' if p is not None and p < 0.05
                         else 'zayıf' if p is not None and p < 0.15 else 'yok')
    tested = sum(1 for r in win_loss if r['p'] is not None)
    return dict(win_loss=win_loss, habits=habits, tested=tested,
                expected_false=round(0.05 * tested, 1))


def leave_one_out(X, wins, k, top):
    """Predict each of your decisions from all the others: does the pattern generalise?"""
    wins = np.asarray(wins, dtype=bool)
    n, hits, rows = len(X), 0, []
    for j in range(n):
        mask = np.arange(n) != j
        model = Model(X[mask], wins[mask], k, top)
        v = model.vote(X[j])
        pred = v['score'] > 0
        hits += int(pred == wins[j])
        rows.append(dict(predicted_win=bool(pred), score=round(v['score'], 3)))
    base = max(wins.mean(), 1 - wins.mean()) if n else None
    acc = hits / n if n else None
    z = None
    if n and base is not None and 0 < base < 1:
        z = round((acc - base) / math.sqrt(base * (1 - base) / n), 2)
    return dict(count=n, accuracy=None if acc is None else round(acc, 3),
                majority=None if base is None else round(float(base), 3), z=z, rows=rows)


def analyze(trades, rates, tf_minutes, *, symbol, settings=None, htf_rates=None, utc_offset=0,
            spread=0.0, now=None, h4_rates=None, d1_rates=None):
    """Learn from `trades` (manual_lifetimes rows) on `rates` and return findings, tests and live signal."""
    s = {**DEFAULTS, **(settings or {})}
    htf_minutes = se.HTF_AUTO.get(int(tf_minutes))
    m = Market(rates, tf_minutes, s, htf_rates=htf_rates, htf_minutes=htf_minutes, utc_offset=utc_offset,
               h4_rates=h4_rates, d1_rates=d1_rates)
    decisions_all = merge_decisions([t for t in trades if t['symbol'] == symbol], s['merge_seconds'])
    decisions, skipped = [], 0
    for d in decisions_all:
        i = m.last_closed_before(d['open_time'])
        if not m.usable(i):
            skipped += 1
            continue
        decisions.append({**d, 'bar_index': i, 'candle_time': int(m.times[i]), 'features': m.features(i, d['side']),
                          'hour_utc': m.hour_utc(i), 'after': m.forward(i, d['side'], s['horizon'], spread)})
    result = dict(symbol=symbol, timeframe_minutes=int(tf_minutes), htf_minutes=htf_minutes, settings=s,
                  positions=sum(len(d['positions']) for d in decisions_all), decisions_total=len(decisions_all),
                  decisions=len(decisions), skipped_before_history=skipped,
                  wins=int(sum(bool(d['win']) for d in decisions)),
                  net=round(float(sum(d['net'] for d in decisions)), 2),
                  first_candle=int(m.times[min(m.warmup, len(m.times) - 1)]) if len(m.times) else None)
    result['trades'] = [dict(open_time=d['open_time'], side=d['side'], positions=len(d['positions']),
                             volume=round(d['volume'], 2), open_price=round(d['open_price'], 5),
                             close_time=d['close_time'], net=d['net'], win=d['win'], hour_utc=d['hour_utc'],
                             features={k: round(v, 3) for k, v in d['features'].items() if FEATURES[k][1] != 'hidden'})
                        for d in decisions]
    if len(decisions) < s['min_decisions']:
        result.update(status='NOT_ENOUGH', verdict=None, signal=None, history_test=None, findings=None,
                      cross_check=None,
                      message=f"{symbol} için öğrenecek yeterli manuel karar yok "
                              f"({len(decisions)}/{s['min_decisions']}).")
        return result

    start = m.warmup
    usable = [i for i in range(start, len(m.rates) - 1) if m.usable(i)]
    raw = {(i, side): _vector(m.features(i, side)) for i in usable for side in ('BUY', 'SELL')}
    allm = np.array(list(raw.values()))
    mean, std = allm.mean(0), allm.std(0)
    std[std < 1e-9] = 1.0
    scale = lambda v: (v - mean) / std

    X = np.array([scale(_vector(d['features'])) for d in decisions])
    wins = np.array([d['win'] for d in decisions], dtype=bool)
    # Habits are compared with the same side the trader took, on ordinary candles.
    sides = [d['side'] for d in decisions]
    buy_share = sides.count('BUY') / len(sides)
    pick = np.random.default_rng(1).random(len(usable)) < buy_share
    base_rows = [raw[(i, 'BUY' if b else 'SELL')] for i, b in zip(usable, pick)]
    result['findings'] = findings(decisions, base_rows, s['permutations'])
    result['cross_check'] = leave_one_out(X, wins, s['k'], s['top_features'])
    result['entry_quality'] = _summary([d['after'] for d in decisions])

    # Walk-forward replay: each candle only uses decisions already closed at its close time.
    first_close = min(d['close_time'] for d in decisions)
    order = sorted(d['close_time'] for d in decisions)
    test_start = order[s['min_decisions'] - 1]
    signals, baseline, prev, cache = [], [], None, {}
    close_times = np.array([d['close_time'] for d in decisions])
    for i in usable:
        close_time = int(m.times[i]) + m.tf * 60
        if close_time < first_close:
            continue
        for side in ('BUY', 'SELL'):
            baseline.append(m.forward(i, side, s['horizon'], spread))
        known = tuple(np.nonzero(close_times <= close_time)[0])
        if len(known) < s['min_decisions']:
            prev = None
            continue
        model = cache.get(known)
        if model is None:
            model = cache[known] = Model(X[list(known)], wins[list(known)], s['k'], s['top_features'])
        best = None
        for side in ('BUY', 'SELL'):
            v = model.vote(scale(raw[(i, side)]))
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
    tf_bars = (int(m.times[usable[-1]]) - test_start) // (m.tf * 60) if usable and test_start else 0
    if sig_stats['count'] < s['min_signals']:
        reason = (f"Geçmiş test {s['min_decisions']}. işlemin kapandığı andan itibaren ~{max(tf_bars, 0)} mumluk "
                  f"bir dönemi kapsıyor; bu sürede {sig_stats['count']} bağımsız sinyal çıktı "
                  f"(karar için en az {s['min_signals']} gerekli). Daha kısa periyot (M5/M15) daha çok sinyal test eder.")
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
                                  test_start=int(test_start), test_bars=int(max(tf_bars, 0)), recent=signals[-20:])
    result['verdict'] = dict(reliable=reliable, reason=reason)

    # Live: last closed candle, model trained on every decision closed so far.
    live_i = len(m.rates) - 2
    known = [j for j, d in enumerate(decisions) if d['close_time'] <= (now or int(m.times[-1]) + m.tf * 60)]
    signal = None
    if m.usable(live_i) and len(known) >= s['min_decisions']:
        model = Model(X[known], wins[known], s['k'], s['top_features'])
        votes = {side: model.vote(scale(_vector(m.features(live_i, side)))) for side in ('BUY', 'SELL')}
        side, v = max(votes.items(), key=lambda kv: kv[1]['score'])
        active = v['similar'] and v['score'] >= s['min_score']
        near = [decisions[known[j]] for j in v['neighbours']]
        signal = dict(side=side if active else 'HOLD', candidate_side=side, score=round(v['score'], 3),
                      similar=v['similar'], win_share=round(v['win_share'], 3),
                      candle_time=int(m.times[live_i]), price=float(m.close[live_i]),
                      atr=float(m.atr[live_i]), experimental=not reliable,
                      used_features=[dict(feature=f, label=FEATURES[f][0]) for f in model.used()],
                      neighbours=[dict(open_time=d['open_time'], side=d['side'], net=d['net'], win=d['win'])
                                  for d in near])
    result['signal'] = signal
    result['status'] = 'OK'
    result['message'] = reason
    return result
