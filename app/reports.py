"""Cash activity by UTC day; win statistics by fully closed position lifetime."""
from datetime import datetime, timezone
import math


def net(row):
    return sum(float(row.get(k, 0)) for k in ('profit', 'commission', 'swap', 'fee'))


def day(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')


def build_report(activity, lifetimes):
    rows = activity['deals']
    today = day(activity['end'])
    daily, symbols = {}, {}
    closed, incomplete = [], []
    for position_id, history in lifetimes.items():
        trades = sorted((r for r in history if r['type'] in (0, 1)), key=lambda r:(r['time'],r['ticket']))
        balance = sum(r['volume'] * (1 if r['type'] == 0 else -1) for r in trades)
        if not trades or trades[0]['entry'] != 0 or not math.isclose(balance, 0, abs_tol=1e-7):
            incomplete.append(position_id)
            continue
        final = trades[-1]
        if position_id in activity['open_ids'] or not activity['start'] <= final['time'] <= activity['end']:
            continue
        closed.append(dict(position_id=position_id, symbol=final['symbol'], date=day(final['time']),
                           net=round(net_sum(history), 2), volume=sum(r['volume'] for r in trades if r['entry'] in (1,3))))
    def group(mapping, key):
        return mapping.setdefault(key, dict(profit=0., swap=0., commission=0., fee=0., volume=0.,
            trades_count=0, winning_trades=0, losing_trades=0, gross_profit=0., gross_loss=0.))
    for r in rows:
        for g in (group(daily, day(r['time'])), group(symbols, r['symbol'] or 'Hesap giderleri')):
            g['profit'] += net(r)
            for k in ('swap','commission','fee'):
                g[k] += r[k]
            if r['type'] in (0,1) and r['entry'] in (1,3):
                g['volume'] += r['volume']
    for p in closed:
        for g in (group(daily,p['date']), group(symbols,p['symbol'])):
            g['trades_count'] += 1
            g['winning_trades'] += int(p['net'] > 0)
            g['losing_trades'] += int(p['net'] < 0)
            g['gross_profit'] += max(0, p['net'])
            g['gross_loss'] += min(0, p['net'])
    wins, losses = [p['net'] for p in closed if p['net'] > 0], [p['net'] for p in closed if p['net'] < 0]
    gains, loss = sum(wins), sum(losses)
    def finish(g):
        return {**{k:round(v, 8 if k == "volume" else 2) if isinstance(v,float) else v for k,v in g.items()},
                'win_rate':round(100*g['winning_trades']/g['trades_count'],1) if g['trades_count'] else 0}
    summary = dict(total_trades=len(closed), winning_trades=len(wins), losing_trades=len(losses),
        win_rate=round(100*len(wins)/len(closed),1) if closed else 0,
        total_profit=round(net_sum(rows),2), gross_profit=round(gains,2), gross_loss=round(loss,2),
        cohort_net_profit=round(sum(p['net'] for p in closed),2),
        profit_factor=round(gains/abs(loss),2) if loss else (None if gains else 0),
        today_profit=round(net_sum([r for r in rows if day(r['time']) == today]),2),
        today_trades=sum(p['date'] == today for p in closed),
        avg_profit=round(gains/len(wins),2) if wins else 0,
        avg_loss=round(loss/len(losses),2) if losses else 0,
        best_trade=max((p['net'] for p in closed),default=0), worst_trade=min((p['net'] for p in closed),default=0),
        total_volume=round(sum(r['volume'] for r in rows if r['type'] in (0,1) and r['entry'] in (1,3)),4),
        total_swap=round(sum(r['swap'] for r in rows),2), total_commission=round(sum(r['commission'] for r in rows),2),
        total_fee=round(sum(r['fee'] for r in rows),2),
        unallocated_net=round(net_sum([r for r in rows if not r['position_id']]),2),
        incomplete_positions=len(incomplete))
    return dict(summary=summary, currency=activity['currency'], timezone='UTC',
        basis='Tutarlar seçilen dönemdeki net hesap hareketleridir. Kazanma oranı ve kâr faktörü, dönemde tamamen kapanan pozisyonların açılış giderleri dahil tüm yaşamına göre hesaplanır. Kısmi kapanışlar ayrı pozisyon sayılmaz. Pozisyona bağlanamayan giderler yalnız dönem toplamına dahildir.',
        daily=[dict(date=k,is_today=k==today,**finish(v)) for k,v in sorted(daily.items(),reverse=True)],
        by_symbol=[dict(symbol=k,**finish(v)) for k,v in sorted(symbols.items(),key=lambda x:x[1]['profit'],reverse=True)],
        incomplete_position_ids=incomplete)


def net_sum(rows):
    return sum(net(r) for r in rows)
