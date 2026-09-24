"""Observed broker outcomes for AI-tagged positions; no profitability projection."""
import math
from app.mt5_bridge import AI_MAGIC


def summarize(deals, records, open_position_ids=()):
    open_position_ids = set(open_position_ids)
    ai_ids = {d.get('position_id') for d in deals if d.get('magic') == AI_MAGIC and d.get('position_id')}
    groups = {}
    for deal in deals:
        position_id = deal.get('position_id')
        if position_id in ai_ids:
            groups.setdefault(position_id, []).append(deal)
    by_order = {str(row.get('order_ticket')): row for row in records if row.get('order_ticket')}
    observations = []
    for position_id, rows in groups.items():
        if position_id in open_position_ids or not any(d.get('entry') in (1, 2, 3) for d in rows):
            continue
        net = sum(sum(float(d.get(key, 0) or 0) for key in ('profit', 'commission', 'swap', 'fee')) for d in rows)
        costs = sum(sum(float(d.get(key, 0) or 0) for key in ('commission', 'swap', 'fee')) for d in rows)
        matching = next((by_order[str(d.get('order'))] for d in rows if str(d.get('order')) in by_order), None)
        close_time = max(int(d.get('timestamp') or 0) for d in rows if d.get('entry') in (1, 2, 3))
        symbol = next((d.get('symbol') for d in rows if d.get('symbol')), '')
        confidence = matching.get('confidence') if matching else None
        slippage = None
        if matching:
            try:
                expected = float(matching['expected_price'])
                fill = float(matching['fill_price'])
                if expected > 0 and math.isfinite(expected) and math.isfinite(fill):
                    direction = 1 if matching['action'] == 'BUY' else -1
                    slippage = round(100 * direction * (fill - expected) / expected, 5)
            except (KeyError, TypeError, ValueError):
                pass
        observations.append({'position_id': position_id, 'symbol': symbol, 'net': round(net, 2),
                             'costs': round(costs, 2), 'confidence': confidence,
                             'adverse_slippage_pct': slippage, 'closed_at': close_time})
    observations.sort(key=lambda row: (row['closed_at'], row['position_id']))
    wins = [row for row in observations if row['net'] > 0]
    losses = [row for row in observations if row['net'] < 0]
    equity = peak = drawdown = 0.0
    for row in observations:
        equity += row['net']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    buckets = {}
    for label, lower, upper in (('0-59', 0, 60), ('60-79', 60, 80), ('80-100', 80, 101)):
        cohort = [row for row in observations if isinstance(row['confidence'], (int, float))
                  and lower <= row['confidence'] < upper]
        buckets[label] = {'count': len(cohort), 'wins': sum(row['net'] > 0 for row in cohort),
                          'net': round(sum(row['net'] for row in cohort), 2),
                          'win_rate_pct': round(100 * sum(row['net'] > 0 for row in cohort) / len(cohort), 1) if cohort else None}
    slippage = [row['adverse_slippage_pct'] for row in observations if row['adverse_slippage_pct'] is not None]
    symbols = {}
    for row in observations:
        entry = symbols.setdefault(row['symbol'], {'count': 0, 'net': 0.0})
        entry['count'] += 1
        entry['net'] = round(entry['net'] + row['net'], 2)
    gain = sum(row['net'] for row in wins)
    loss = -sum(row['net'] for row in losses)
    return {'closed_positions': len(observations), 'wins': len(wins), 'losses': len(losses),
            'net': round(sum(row['net'] for row in observations), 2),
            'costs': round(sum(row['costs'] for row in observations), 2),
            'max_drawdown': round(drawdown, 2),
            'profit_factor': round(gain / loss, 2) if loss else None,
            'matched_recommendations': sum(row['confidence'] is not None for row in observations),
            'confidence_buckets': buckets, 'by_symbol': symbols,
            'mean_adverse_slippage_pct': round(sum(slippage) / len(slippage), 5) if slippage else None,
            'observations': observations[-100:], 'early_sample': len(observations) < 30}
