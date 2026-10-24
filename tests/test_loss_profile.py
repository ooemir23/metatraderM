from app import loss_profile as lp


def row(symbol='BTCUSD', side='BUY', volume=10, net=-100, sl=0, price=100.0, minutes=30, reason=-1):
    return dict(symbol=symbol, side=side, volume=volume, net=net, sl=sl, open_price=price, open_time=0,
                close_time=minutes*60, close_reason=reason)


def test_risky_needs_enough_trades_low_win_rate_and_net_loss():
    few = lp.build([row() for _ in range(4)])
    assert not any(g['risky'] for g in few['groups'])
    losing = lp.build([row() for _ in range(6)])
    assert any(g['risky'] and g['key'] == 'BTCUSD' for g in losing['groups'])
    winning = lp.build([row(net=50) for _ in range(6)])
    assert not any(g['risky'] for g in winning['groups'])
    mixed = lp.build([row(net=-10)]*3 + [row(net=50)]*3)
    assert not any(g['risky'] for g in mixed['groups'])


def test_check_matches_symbol_side_lot_and_stop():
    rows = [row(volume=1, net=5) for _ in range(6)] + [row(volume=10, side='SELL', sl=99.95, net=-80) for _ in range(6)]
    profile = lp.build(rows)
    hits = {(g['dim'], g['key']) for g in lp.check(profile, 'BTCUSD', 'SELL', 10, 100, 99.95)}
    assert ('Yön', 'BTCUSD SAT') in hits and ('Stop', 'SL çok dar (<%0,1)') in hits and ('Lot', 'Lot büyük (1,5–3×)') in hits
    assert not [g for g in lp.check(profile, 'BTCUSD', 'BUY', 1, 100, 90) if g['dim'] == 'Yön']
    assert lp.check(profile, 'EURUSD', 'BUY', 1) == []


def test_loss_reasons_counted():
    out = lp.build([row(reason=4), row(reason=4), row(reason=0), row(net=20)])
    assert out['loss_reasons'] == {'SL': 2, 'Elle/diğer': 1}
