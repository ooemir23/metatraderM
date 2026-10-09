"""Observed execution prices; never reconstruct missing submission quotes."""
import math


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def summarize_position(position, records):
    quote = position.get('quote') or {}
    point, bid, ask = quote.get('point'), quote.get('bid'), quote.get('ask')
    current = ((ask - bid) / point if all(positive(v) for v in (point, bid, ask)) and ask >= bid else None)
    result = dict(current_spread_points=round(current, 3) if current is not None else None,
                  opening_spread_points=None, spread_change_points=None, slippage_points=None,
                  opening_bid=None, opening_ask=None, requested_price=None, fill_price=None,
                  state='unrecorded', fills=[])
    fills = position.get('opening_fills') or []
    if not fills:
        return result
    if any(d['entry'] != 0 or d['type'] != position['type_raw'] for d in fills):
        return {**result, 'state': 'changed_direction'}
    observations = []
    for fill in fills:
        record = records.get(fill['order'])
        execution = (record or {}).get('execution') or {}
        order = (record or {}).get('order') or {}
        if execution.get('kind') == 'pending':
            return {**result, 'state': 'pending_quote_missing'}
        if (execution.get('kind') != 'market' or order.get('symbol') != fill['symbol']
                or order.get('magic') != fill['magic'] or order.get('type') != fill['type']):
            return result
        requested = execution.get('requested_price')
        open_bid, open_ask, open_point = (execution.get(k) for k in ('bid', 'ask', 'point'))
        if (not all(positive(v) for v in (requested, open_bid, open_ask, open_point, fill['price'], fill['volume']))
                or open_ask < open_bid or not math.isclose(requested, open_ask if fill['type'] == 0 else open_bid,
                                                        rel_tol=1e-10, abs_tol=1e-10)):
            return result
        direction = 1 if fill['type'] == 0 else -1
        observations.append(dict(order=fill['order'], ticket=fill['ticket'], volume=fill['volume'],
            bid=open_bid, ask=open_ask, requested_price=requested, fill_price=fill['price'],
            point=open_point, spread_points=(open_ask - open_bid) / open_point,
            slippage_points=direction * (fill['price'] - requested) / open_point,
            quote_time=execution.get('quote_time'), deviation_points=execution.get('deviation_points')))
    if not all(math.isclose(d['point'], observations[0]['point'], rel_tol=1e-10) for d in observations):
        return result
    total_volume = sum(d['volume'] for d in observations)
    for source, target in (('bid', 'opening_bid'), ('ask', 'opening_ask'), ('requested_price', 'requested_price'),
                           ('fill_price', 'fill_price'), ('spread_points', 'opening_spread_points'),
                           ('slippage_points', 'slippage_points')):
        value = sum(d[source] * d['volume'] for d in observations) / total_volume
        result[target] = round(value, 3) if source.endswith('_points') else value
    if current is not None and positive(point) and math.isclose(point, observations[0]['point'], rel_tol=1e-10):
        result['spread_change_points'] = round(current - result['opening_spread_points'], 3)
    result.update(state='recorded', fills=observations)
    return result
