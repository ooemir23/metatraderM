"""Bounded, closed-bar HMA/KAMA research. This module cannot place orders."""
from bisect import bisect_right
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Settings:
    hma_period: int = 14
    kama_period: int = 10
    kama_fast: int = 2
    kama_slow: int = 30
    atr_period: int = 14
    er_min: float = .30
    stop_atr: float = 2.0
    target_atr: float = 3.0
    point: float = .01
    spread_points: float = 38.0
    commission_points: float = 0.0
    slippage_points: float = 0.0
    swap_long_points_per_day: float = 0.0
    swap_short_points_per_day: float = 0.0
    historical_spread: bool = False
    min_train_trades: int = 20
    lot_size: float = .01
    initial_capital: float = 10000.0
    contract_size: float | None = None
    currency: str | None = None
    broker_rules: dict | None = None

    def validate(self):
        for key in ('hma_period', 'kama_period', 'kama_fast', 'kama_slow', 'atr_period'):
            if not isinstance(getattr(self, key), int) or not 2 <= getattr(self, key) <= 200:
                raise ValueError('Gösterge periyotları 2–200 olmalı.')
        if self.kama_fast >= self.kama_slow:
            raise ValueError('KAMA hızlı periyodu yavaş periyottan küçük olmalı.')
        if not 0 <= self.er_min <= 1 or not 1 <= self.min_train_trades <= 1000:
            raise ValueError('Geçersiz ER veya işlem sayısı sınırı.')
        for key in ('point', 'stop_atr', 'target_atr', 'spread_points', 'commission_points', 'slippage_points'):
            value = getattr(self, key)
            if not math.isfinite(value) or value < 0:
                raise ValueError('Geçersiz maliyet veya risk ayarı.')
        if self.point <= 0 or self.stop_atr <= 0:
            raise ValueError('Point ve ATR stop çarpanı pozitif olmalı.')
        for value in (self.lot_size, self.initial_capital):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Lot ve başlangıç sermayesi pozitif olmalı.')
        if self.contract_size is not None and (not math.isfinite(self.contract_size) or self.contract_size <= 0 or not self.currency):
            raise ValueError('Para hesabı için sözleşme büyüklüğü ve para birimi gerekli.')
        for value in (self.swap_long_points_per_day, self.swap_short_points_per_day):
            if not math.isfinite(value):
                raise ValueError('Geçersiz swap varsayımı.')
        if self.broker_rules is not None:
            rules = self.broker_rules
            if not self.contract_size or rules.get('mode') not in ('PERCENT', 'MONEY'):
                raise ValueError('Invalid broker margin model.')
            if not math.isclose(rules['volume'], self.lot_size):
                raise ValueError('Margin model lot size mismatch.')
            for key in ('margin_call', 'stop_out', 'tick_size', 'stops_level_points'):
                if not math.isfinite(rules[key]) or rules[key] < 0:
                    raise ValueError('Invalid broker margin settings.')
            if rules['tick_size'] <= 0 or rules['margin_call'] < rules['stop_out']:
                raise ValueError('Invalid broker margin thresholds.')
            for model in rules['models'].values():
                if not all(math.isfinite(model[key]) for key in ('slope', 'intercept')):
                    raise ValueError('Invalid broker margin coefficients.')


def validate_rates(rows, seconds):
    if not 100 <= len(rows) <= 50000:
        raise ValueError('Her zaman dilimi için 100–50000 kapanmış mum gerekli.')
    previous = -1
    for r in rows:
        t = r.get('time')
        if not isinstance(t, int) or t <= previous:
            raise ValueError('Mum zamanları benzersiz ve artan olmalı.')
        if previous >= 0 and t - previous < seconds:
            raise ValueError('Mum aralıkları seçilen zaman diliminden kısa olamaz.')
        previous = t
        o, h, l, c = (float(r[k]) for k in ('open', 'high', 'low', 'close'))
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('Geçersiz OHLC verisi.')
        if 'spread' in r and (not math.isfinite(float(r['spread'])) or r['spread'] < 0):
            raise ValueError('Geçersiz tarihsel spread.')
    return {'count': len(rows), 'first_open': rows[0]['time'],
            'last_close': rows[-1]['time'] + seconds,
            'long_intervals': sum(b['time'] - a['time'] > seconds for a, b in zip(rows, rows[1:])),
            'interval_note': 'Uzun aralıklar hafta sonu/seans kapanışı veya eksik veri olabilir.'}


def wma(values, period):
    out = [None] * len(values)
    divisor = period * (period + 1) / 2
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum((j + 1) * v for j, v in enumerate(window)) / divisor
    return out


def indicators(rows, settings):
    closes = [float(r['close']) for r in rows]
    half, full = wma(closes, settings.hma_period // 2), wma(closes, settings.hma_period)
    hma = wma([2 * a - b if a is not None and b is not None else None
               for a, b in zip(half, full)], math.isqrt(settings.hma_period))
    er, kama, atr = ([None] * len(rows) for _ in range(3))
    fast, slow = 2 / (settings.kama_fast + 1), 2 / (settings.kama_slow + 1)
    n = settings.kama_period
    if len(rows) > n:
        kama[n - 1] = sum(closes[:n]) / n
    for i in range(n, len(rows)):
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
        er[i] = abs(closes[i] - closes[i - n]) / path if path else 0.0
        alpha = (er[i] * (fast - slow) + slow) ** 2
        kama[i] = kama[i - 1] + alpha * (closes[i] - kama[i - 1])
    tr = [max(float(r['high']) - float(r['low']),
              abs(float(r['high']) - closes[i - 1]), abs(float(r['low']) - closes[i - 1]))
          for i, r in enumerate(rows) if i > 0]
    n = settings.atr_period
    if len(tr) >= n:
        atr[n] = sum(tr[:n]) / n
        for i in range(n + 1, len(rows)):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i - 1]) / n
    return {'hma': hma, 'kama': kama, 'er': er, 'atr': atr}


def closed_higher_indices(lower, higher, lower_seconds=3600, higher_seconds=14400):
    """An H4 bar is visible only when its nominal close <= H1 decision time."""
    times = [r['time'] + higher_seconds for r in higher]
    return [bisect_right(times, r['time'] + lower_seconds) - 1 for r in lower]


def stats(trades, marked_drawdown=0, settings=None):
    profits = [t['net_points'] for t in trades]
    gains, losses = sum(p for p in profits if p > 0), -sum(p for p in profits if p < 0)
    equity = peak = drawdown = 0.0
    for p in profits:
        equity += p
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    result = {'count': len(profits),
            'wins': sum(p > 0 for p in profits), 'losses': sum(p < 0 for p in profits),
            'breakeven': sum(p == 0 for p in profits), 'net_points': round(equity, 2),
            'mean_points': round(equity / len(profits), 2) if profits else None,
            'win_rate_pct': round(100 * sum(p > 0 for p in profits) / len(profits), 2) if profits else None,
            'profit_factor': round(gains / losses, 3) if losses else None,
            'max_closed_drawdown_points': round(drawdown, 2),
            'max_drawdown_points': round(max(drawdown, marked_drawdown), 2)}
    if settings and settings.contract_size is not None:
        factor = settings.point * settings.contract_size * settings.lot_size
        result.update(lot_size=settings.lot_size, currency=settings.currency,
                      initial_capital=settings.initial_capital,
                      gross_profit=round(gains * factor, 2), gross_loss=round(losses * factor, 2),
                      net_profit=round(equity * factor, 2),
                      final_capital=round(settings.initial_capital + equity * factor, 2),
                      return_pct=round(equity * factor / settings.initial_capital * 100, 2),
                      max_drawdown_money=round(max(drawdown, marked_drawdown) * factor, 2))
    return result


def run_segment(rows, study, seconds, start, end, settings, wait=0, distance=0,
                filtered=True, higher=None, higher_study=None, higher_indices=None):
    """Start flat in each segment; warmup studies use only earlier price bars."""
    trades, candidate, position = [], None, None
    counts = {'crossovers': 0, 'confirmed': 0, 'expired': 0, 'reversed': 0,
              'filtered_at_deadline': 0, 'rejected_margin': 0, 'rejected_stops': 0}
    equity = peak = drawdown = 0.0
    rules = settings.broker_rules
    factor = settings.point * settings.contract_size * settings.lot_size if settings.contract_size else None
    risk = {'stop_outs': 0, 'margin_calls': 0, 'min_equity': settings.initial_capital,
            'min_margin_level_pct': None, 'max_margin': 0.0}
    hma, kama, er, atr = (study[k] for k in ('hma', 'kama', 'er', 'atr'))

    def spread(i):
        return float(rows[i]['spread']) if settings.historical_spread else settings.spread_points

    def exit_price(bid, side, i):
        return bid - settings.slippage_points * settings.point if side == 1 else bid + (spread(i) + settings.slippage_points) * settings.point

    def swap(at):
        days = max(0, (at // 86400) - (position['entry_time'] // 86400))
        return days * (settings.swap_long_points_per_day if position['side'] == 1 else settings.swap_short_points_per_day)

    def account_mark(price, at):
        value = settings.initial_capital + (equity + position['side'] * (price-position['entry']) / settings.point
                    - settings.commission_points - swap(at)) * factor
        margin = position['margin']
        level = value / margin * 100
        risk['min_equity'] = min(risk['min_equity'], value)
        previous_level = risk['min_margin_level_pct']
        risk['min_margin_level_pct'] = min(previous_level if previous_level is not None else float('inf'), level)
        risk['max_margin'] = max(risk['max_margin'], margin)
        call_metric = level if rules['mode'] == 'PERCENT' else value
        if call_metric <= rules['margin_call'] and not position.get('margin_call_seen'):
            risk['margin_calls'] += 1
            position['margin_call_seen'] = True
        return value, level

    def stop_out_price(at):
        floor = position['margin'] * rules['stop_out'] / 100 if rules['mode'] == 'PERCENT' else rules['stop_out']
        balance = settings.initial_capital + equity * factor
        return position['entry'] + position['side'] * ((floor-balance) / factor
                         + settings.commission_points + swap(at)) * settings.point

    def finish(price, at, reason, index):
        nonlocal position, equity, peak, drawdown
        net = position['side'] * (price - position['entry']) / settings.point - settings.commission_points - swap(at)
        exit_slip = 0 if reason == 'target' else settings.slippage_points
        exit_spread = spread(index) if position['side'] == -1 else 0
        exit_bid = price + position['side'] * exit_slip * settings.point - exit_spread * settings.point
        market = position['side'] * (exit_bid - position['entry_bid']) / settings.point
        costs = {'spread': position['entry_spread'] + exit_spread,
                 'slippage': settings.slippage_points + exit_slip,
                 'commission': settings.commission_points, 'swap': swap(at)}
        margin_data = None
        if rules:
            value, level = account_mark(price, at)
            margin_data = {'used_margin': position['margin'], 'equity_at_exit': value,
                           'margin_level_pct_at_exit': level, 'stop_out_threshold': rules['stop_out'],
                           'stop_out_mode': rules['mode'], 'intrabar_time_approximate': True}
            risk['stop_outs'] += reason == 'stop out'
        trades.append({'side': 'BUY' if position['side'] == 1 else 'SELL',
                       'crossover_time': position['crossover_time'],
                       'signal_time': position['signal_time'], 'entry_time': position['entry_time'],
                       'exit_time': at, 'entry_price': position['entry'], 'exit_price': price,
                       'lot_size': settings.lot_size, 'net_points': round(net, 6), 'reason': reason,
                       'diagnostics': {'market_points': round(market, 6), 'cost_points': costs,
                         'entry_atr': position['entry_atr'], 'entry_er': position['entry_er'],
                         'entry_distance_atr': position['entry_distance_atr'],
                         'stop_price': position['stop'], 'target_price': position['target'],
                         'stop_gap': reason == 'stop' and position['side'] * (position['stop'] - (price + position['side'] * exit_slip * settings.point)) > 1e-8}})
        if margin_data:
            trades[-1]['diagnostics']['margin'] = margin_data
        equity += net
        if settings.contract_size is not None:
            factor = settings.point * settings.contract_size * settings.lot_size
            trades[-1].update(net_profit=round(net * factor, 2), currency=settings.currency,
                              balance_after=round(settings.initial_capital + equity * factor, 2))
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        position = None

    for i in range(1, len(rows) - 1):
        decision = rows[i]['time'] + seconds
        execution = rows[i + 1]['time']
        if execution < start or execution + seconds > end or decision < start:
            continue
        if any(a[i] is None or a[i - 1] is None for a in (hma, kama, er, atr)) or atr[i] <= 0:
            continue
        cross = 1 if hma[i - 1] <= kama[i - 1] and hma[i] > kama[i] else -1 if hma[i - 1] >= kama[i - 1] and hma[i] < kama[i] else 0
        if cross:
            counts['crossovers'] += 1
            if candidate:
                counts['reversed'] += 1
            candidate = {'side': cross, 'index': i}
        confirmed = 0
        crossover_time = None
        if candidate:
            side = candidate['side']
            age = i - candidate['index']
            if age > wait:
                counts['expired'] += 1
                candidate = None
            else:
                valid = side * (hma[i] - kama[i]) >= distance * atr[i] and side * (hma[i] - kama[i]) > 0
                if filtered:
                    valid = valid and er[i] >= settings.er_min and side * (kama[i] - kama[i - 1]) > 0 and side * (hma[i] - hma[i - 1]) > 0
                    if higher_indices is not None:
                        j = higher_indices[i]
                        valid = valid and j >= 1
                        if valid:
                            # Reject stale H4 alignment across a gap; never use a forming H4 bar.
                            valid = (decision - (higher[j]['time'] + 14400) < 14400 and
                                     higher_study['er'][j] is not None and higher_study['kama'][j - 1] is not None and
                                     higher_study['er'][j] >= settings.er_min and
                                     side * (higher_study['kama'][j] - higher_study['kama'][j - 1]) > 0)
                if valid:
                    confirmed = side
                    crossover_time = rows[candidate['index']]['time'] + seconds
                    counts['confirmed'] += 1
                    candidate = None
                elif age == wait:
                    counts['filtered_at_deadline'] += 1
                    candidate = None

        # Raw reverse closes the previous position, even if filters reject a new entry.
        nxt = rows[i + 1]
        # A gap can liquidate an existing position before a new signal is executed.
        if rules and position:
            opening = float(nxt['open']) + (spread(i+1)*settings.point if position['side'] == -1 else 0)
            if position['side'] * (opening-stop_out_price(execution)) <= 0:
                finish(opening-position['side']*settings.slippage_points*settings.point,
                       execution, 'stop out', i+1)
        if position and cross and cross != position['side']:
            finish(exit_price(float(nxt['open']), position['side'], i + 1), execution, 'opposite crossover', i + 1)
        if not position and confirmed:
            entry = float(nxt['open']) + ((spread(i + 1) if confirmed == 1 else 0) + confirmed * settings.slippage_points) * settings.point
            stop = entry - confirmed * settings.stop_atr * atr[i]
            target = entry + confirmed * settings.target_atr * atr[i] if settings.target_atr else None
            margin = None
            if rules:
                tick = rules['tick_size']
                stop = round(stop/tick)*tick
                target = round(target/tick)*tick if target is not None else None
                quote = float(nxt['open']) + (spread(i+1)*settings.point if confirmed == -1 else 0)
                minimum = rules['stops_level_points']*settings.point
                if stop <= 0 or confirmed*(quote-stop) <= 0 or confirmed*(quote-stop) < minimum-1e-8 or (target is not None and (target <= 0 or confirmed*(target-quote) <= 0 or confirmed*(target-quote) < minimum-1e-8)):
                    counts['rejected_stops'] += 1
                    continue
                model = rules['models']['BUY' if confirmed == 1 else 'SELL']
                margin = model['intercept'] + model['slope']*entry
                if not math.isfinite(margin) or margin <= 0:
                    raise ValueError('Invalid simulated margin.')
                available = settings.initial_capital + equity*factor - settings.commission_points*factor
                initial_mark = available + confirmed*(quote-entry)/settings.point*factor
                threshold = margin*rules['stop_out']/100 if rules['mode'] == 'PERCENT' else rules['stop_out']
                if initial_mark < margin or initial_mark <= threshold or available <= 0:
                    counts['rejected_margin'] += 1
                    continue
            position = {'side': confirmed, 'entry': entry, 'entry_time': execution,
                        'margin': margin,
                        'entry_bid': float(nxt['open']), 'entry_spread': spread(i + 1) if confirmed == 1 else 0,
                        'entry_atr': atr[i], 'entry_er': er[i],
                        'entry_distance_atr': abs(hma[i] - kama[i]) / atr[i],
                        'crossover_time': crossover_time,
                        'signal_time': decision, 'stop': stop, 'target': target}
        if not position:
            continue
        side = position['side']
        # MT5 OHLC is treated as bid; short exits pay ask spread.
        quote_offset = spread(i + 1) * settings.point if side == -1 else 0
        low, high, opening = (float(nxt[k]) + quote_offset for k in ('low', 'high', 'open'))
        stop_hit = low <= position['stop'] if side == 1 else high >= position['stop']
        target_hit = position['target'] is not None and (high >= position['target'] if side == 1 else low <= position['target'])
        if rules:
            liquidation = stop_out_price(execution + seconds)
            liquidation_hit = low <= liquidation if side == 1 else high >= liquidation
            if liquidation_hit and (not stop_hit or side*(liquidation-position['stop']) >= 0):
                fill = min(opening, liquidation) if side == 1 else max(opening, liquidation)
                finish(fill-side*settings.slippage_points*settings.point, execution+seconds, 'stop out', i+1)
                continue
            # Record risk only up to the first adverse exit, never after a closed stop.
            adverse = position['stop'] if stop_hit else (low if side == 1 else high)
            adverse = min(opening, adverse) if side == 1 else max(opening, adverse)
            account_mark(adverse, execution+seconds)
        if stop_hit:
            fill = min(opening, position['stop']) if side == 1 else max(opening, position['stop'])
            fill -= side * settings.slippage_points * settings.point
            finish(fill, execution + seconds, 'stop', i + 1)
        else:
            worst = low if side == 1 else high
            worst -= side * settings.slippage_points * settings.point
            marked = equity + side * (worst - position['entry']) / settings.point - settings.commission_points - swap(execution + seconds)
            drawdown = max(drawdown, peak - marked)
            if target_hit:
                finish(position['target'], execution + seconds, 'target', i + 1)
            else:
                marked_close = equity + side * (exit_price(float(nxt['close']), side, i + 1) - position['entry']) / settings.point - settings.commission_points - swap(execution + seconds)
                peak = max(peak, marked_close)
        # Liquidate at the last fully included bar; segments never share positions.
        if position and (i + 2 == len(rows) or rows[i + 2]['time'] >= end):
            finish(exit_price(float(nxt['close']), side, i + 1), execution + seconds, 'segment end', i + 1)
    summary = stats(trades, drawdown, settings)
    if rules:
        summary.update(risk, rejected_margin=counts['rejected_margin'], rejected_stops=counts['rejected_stops'],
                       margin_model_enabled=True)
    return {'stats': summary, 'signals': counts, 'trades': trades}


def compare(h1, h4, settings=None):
    settings = settings or Settings()
    settings.validate()
    quality = {'H1': validate_rates(h1, 3600), 'H4': validate_rates(h4, 14400)}
    if settings.historical_spread and any('spread' not in r for rows in (h1, h4) for r in rows):
        raise ValueError('Tarihsel spread seçildi fakat mum spread verisi eksik.')
    studies = {name: indicators(rows, settings) for name, rows in (('H1', h1), ('H4', h4))}
    warmup = max(settings.hma_period, settings.kama_period, settings.kama_slow, settings.atr_period) * 5
    if min(len(h1), len(h4)) <= warmup + 100:
        raise ValueError('Gösterge ısınması ve üç veri bölümü için yeterli uzun geçmiş yok.')
    start = max(h1[warmup]['time'] + 3600, h4[warmup]['time'] + 14400)
    end = min(h1[-1]['time'] + 3600, h4[-1]['time'] + 14400)
    # Shared split boundaries align to H4 closes so no bar straddles a partition.
    boundaries = [r['time'] + 14400 for r in h4 if start <= r['time'] + 14400 <= end]
    if len(boundaries) < 100:
        raise ValueError('Ortak tarih aralığı yetersiz.')
    split1, split2 = boundaries[int(len(boundaries) * .6)], boundaries[int(len(boundaries) * .8)]
    segments = {'train': (start, split1), 'validation': (split1, split2), 'holdout': (split2, end)}
    alignment = closed_higher_indices(h1, h4)

    def evaluate(config, segment):
        mode = config['mode']
        name = 'H4' if mode == 'H4' else 'H1'
        rows = h4 if name == 'H4' else h1
        return run_segment(rows, studies[name], 14400 if name == 'H4' else 3600,
                           *segments[segment], settings, config['wait'], config['distance_atr'],
                           config['filtered'], h4, studies['H4'], alignment if mode == 'H1_H4' else None)

    results = []
    for mode in ('H1', 'H4', 'H1_H4'):
        configs = [{'mode': mode, 'wait': 0, 'distance_atr': 0, 'filtered': False}]
        configs += [{'mode': mode, 'wait': wait, 'distance_atr': distance, 'filtered': True}
                    for wait in (0, 1, 2, 3) for distance in (0, .05, .10, .20)]
        # H1_H4 unfiltered is the same H1 baseline and is kept explicitly for comparison.
        for config in configs:
            train = evaluate(config, 'train')
            validation = evaluate(config, 'validation')
            results.append({'config': config, 'train': train['stats'],
                            'train_signals': train['signals'],
                            'validation': validation['stats'],
                            'validation_signals': validation['signals']})
    eligible = [r for r in results if r['config']['filtered'] and
                r['train']['count'] >= settings.min_train_trades and r['train']['net_points'] > 0]
    # Selection uses TRAIN only. Neither validation nor holdout can change the choice.
    ranked = sorted(eligible, key=lambda r: (r['train']['net_points'] / max(1, r['train']['max_drawdown_points']),
                                            r['train']['mean_points']), reverse=True)
    winner = ranked[0] if ranked else None
    held = evaluate(winner['config'], 'holdout') if winner else None
    if winner is None:
        assessment = {'status': 'no_candidate', 'message': 'Eğitim koşullarını sağlayan filtreli aday bulunamadı.'}
    elif winner['validation']['net_points'] <= 0 or held['stats']['net_points'] <= 0:
        assessment = {'status': 'failed_validation',
                      'message': 'Eğitimde seçilen aday doğrulama veya ayrılmış testte pozitif net sonuç sağlayamadı. Bu ayar doğrulanmış işlem ayarı değildir.'}
    elif min(winner['validation']['count'], held['stats']['count']) < settings.min_train_trades:
        assessment = {'status': 'insufficient_evidence',
                      'message': 'Doğrulama/ayrılmış test işlem sayısı minimumun altında; pozitif sonuç yeterli kanıt değildir.'}
    else:
        assessment = {'status': 'positive_simulation',
                      'message': 'Doğrulama ve ayrılmış testte pozitif simülasyon sonucu; gerçek tick ve Demo doğrulaması ayrıca gerekli.'}
    baseline = {'mode': winner['config']['mode'] if winner else 'H1', 'wait': 0,
                'distance_atr': 0, 'filtered': False}
    return {'success': True, 'settings': asdict(settings), 'data_quality': quality,
            'segments': {k: {'start': v[0], 'end_exclusive': v[1]} for k, v in segments.items()},
            'comparisons': results, 'selected': winner, 'selected_holdout': held,
            'baseline_holdout': evaluate(baseline, 'holdout'),
            'selected_signals': {segment: evaluate(winner['config'], segment)['signals']
                                 for segment in ('train', 'validation')} if winner else {},
            'selected_trades': {segment: evaluate(winner['config'], segment)['trades']
                                for segment in ('train', 'validation')} if winner else {},
            'selection_rule': 'Eğitimde en az minimum işlem ve pozitif net puan; net puan/azami düşüş. Doğrulama ve holdout seçimde kullanılmaz.',
            'assessment': assessment,
            'status': 'research_candidate' if winner else 'no_eligible_candidate',
            'assumptions': {'units': 'points and account currency when contract_size is supplied; fixed lot, no compounding',
                            'ohlc': 'bid; shorts exit at ask; bar spread is constant within each bar',
                            'fills': 'next bar open; stop first if both touched; target filled at limit',
                            'drawdown': 'bar adverse excursion and close marks; intrabar tick order unknown',
                            'swap': 'user supplied signed COST per calendar-day boundary; no broker triple-swap model',
                            'historical_spread_included': settings.historical_spread,
                            'commission_points': settings.commission_points,
                            'slippage_points_per_fill': settings.slippage_points,
                            'zero_costs_note': 'Sıfır komisyon/swap/kayma bu maliyetlerin bilinmediği veya sıfır varsayıldığı anlamına gelir.',
                            'risk_note': ('Broker teminatı, margin call, stop-out ve minimum stop mesafesi simüle edilir. Güncel broker kuralları geçmişe uygulanır; teminat girişte sabitlenir. Mum içi sıra ve tarihi kural değişiklikleri bilinmez.' if settings.broker_rules else 'Her dönem aynı başlangıç sermayesiyle başlar. Sabit lot; teminat, stop-out ve broker SL/TP sınırları simüle edilmez. Parasal bakiye simülasyondur.'),
                            'live_trading_enabled': False}}
