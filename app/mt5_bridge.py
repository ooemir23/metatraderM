"""Executed beside MT5 so each operation crosses RPyC as one JSON response."""
import json
import math
import time
from datetime import datetime, timezone

MANUAL_MAGIC = 123460
HMA_MAGIC = 123461
AI_MAGIC = 123462


def positions(mt5):
    rows = mt5.positions_get()
    if rows is None:
        raise RuntimeError('Pozisyonlar okunamadı: ' + str(mt5.last_error()))
    return [dict(ticket=int(p.ticket), symbol=str(p.symbol),
                 type='BUY' if p.type == 0 else 'SELL', type_raw=int(p.type),
                 magic=int(p.magic), volume=float(p.volume),
                 price_open=float(p.price_open), price_current=float(p.price_current),
                 sl=float(p.sl), tp=float(p.tp), profit=float(p.profit), swap=float(p.swap),
                 time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.time))) for p in rows]


def rates(mt5, symbol, timeframe, count):
    rows = mt5.copy_rates_from_pos(symbol, getattr(mt5, timeframe), 0, count)
    if rows is None:
        raise RuntimeError('Mum verisi okunamadı')
    return [dict(time=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                 close=float(r[4]), tick_volume=int(r[5])) for r in rows]


def risk(mt5):
    account = mt5.account_info()
    if account is None:
        raise RuntimeError('Hesap bilgisi okunamadı; yeni emir engellendi.')
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now.replace(hour=0, minute=0, second=0, microsecond=0), now)
    current = mt5.positions_get()
    pending = mt5.orders_get()
    if deals is None or current is None or pending is None:
        raise RuntimeError('Günlük risk verisi okunamadı; yeni emir engellendi.')
    # Include opening commissions and separate commission/charge deals, not deposits/credits.
    excluded = {2, 3, 5, 6}  # BALANCE, CREDIT, CORRECTION, BONUS
    realized = sum(sum(float(getattr(d, k, 0)) for k in ('profit', 'commission', 'swap', 'fee'))
                   for d in deals if int(d.type) not in excluded)
    floating = sum(float(p.profit) + float(p.swap) for p in current)
    if not math.isfinite(realized) or not math.isfinite(floating):
        raise RuntimeError('Risk verisi geçersiz; yeni emir engellendi.')
    return account, current, pending, realized, floating


def open_deal(mt5, symbol, order_type, volume, sl_points, tp_points, comment, magic,
              daily_loss_limit, expected_login, expected_server, max_tick_age=10, pending_type=None, entry_price=None,
              max_order_lots=.10, max_total_lots=.50, max_open_orders=10):
    try:
        account, current, pending, realized, floating = risk(mt5)
        if expected_login and (int(account.login) != expected_login or str(account.server) != expected_server):
            return {'success': False, 'error': 'Aktif MT5 hesabı seçilen hesapla uyuşmuyor.'}
        # Open gains cannot mask realized losses. Amounts are in account currency, day is UTC.
        loss = max(0.0, -(realized + min(0.0, floating)))
        if not math.isfinite(daily_loss_limit) or daily_loss_limit <= 0 or loss >= daily_loss_limit:
            return {'success': False, 'error': 'Günlük zarar sınırı: yeni emir engellendi.',
                    'daily_loss': loss, 'daily_loss_limit': daily_loss_limit, 'currency': str(account.currency)}
        if (not all(math.isfinite(x) and x > 0 for x in (volume, max_order_lots, max_total_lots))
                or max_open_orders < 1):
            return {'success': False, 'error': 'Geçersiz hesap risk sınırı; yeni emir engellendi.'}
        if volume > max_order_lots + 1e-9:
            return {'success': False, 'error': 'Emir lotu yapılandırılan üst sınırı aşıyor.'}
        committed = sum(float(p.volume) for p in current) + sum(float(o.volume_current) for o in pending)
        if not math.isfinite(committed) or committed < 0:
            return {'success': False, 'error': 'Açık hacim doğrulanamadı; yeni emir engellendi.'}
        if committed + volume > max_total_lots + 1e-9 or len(current) + len(pending) + 1 > max_open_orders:
            return {'success': False, 'error': 'Toplam açık/bekleyen işlem sınırı aşılıyor.'}
        if magic in (HMA_MAGIC, AI_MAGIC) and int(account.margin_mode) != 2:
            return {"success": False, "error": "Otomatik stratejiler için hedging hesabı gerekli; netting sahipliği birleştirir."}
        same_symbol = [p for p in current if str(p.symbol) == symbol]
        # Netting/exchange positions combine ownership; do not merge manual and automatic trades.
        if int(account.margin_mode) != 2 and any(int(p.magic) != magic for p in same_symbol):
            return {'success': False, 'error': 'Netting hesabında bu sembol başka işlem kaynağına ait.'}
        if magic in (HMA_MAGIC, AI_MAGIC) and any(int(p.magic) == magic for p in same_symbol):
            return {'success': False, 'error': 'Bu stratejinin sembolde açık pozisyonu var.'}
        if not mt5.symbol_select(symbol, True):
            return {'success': False, 'error': 'Sembol seçilemedi.'}
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            return {'success': False, 'error': 'Güncel sembol/fiyat bilgisi alınamadı.'}
        if time.time() - float(tick.time) > max_tick_age or tick.bid <= 0 or tick.ask < tick.bid:
            return {'success': False, 'error': 'Fiyat eski veya geçersiz; yeni emir engellendi.'}
        step = float(info.volume_step)
        if step <= 0 or volume < info.volume_min or volume > info.volume_max or not math.isclose(volume / step, round(volume / step), abs_tol=1e-7):
            return {'success': False, 'error': 'Lot miktarı broker minimum/maksimum veya lot adımına uymuyor.'}
        buy = order_type == 'BUY'
        price = float(tick.ask if buy else tick.bid)
        if pending_type:
            types = {'BUY_LIMIT':2, 'SELL_LIMIT':3, 'BUY_STOP':4, 'SELL_STOP':5}
            if pending_type not in types or pending_type.startswith('BUY') != buy:
                raise ValueError('Geçersiz bekleyen emir türü.')
            required_mode = 2 if pending_type.endswith('LIMIT') else 4
            if not (int(info.order_mode) & required_mode) or not (int(info.expiration_mode) & 1):
                raise ValueError('Sembol bu emir türünü veya GTC süresini desteklemiyor.')
            price = float(entry_price)
            if not _price_valid(info, price) or price <= 0:
                raise ValueError('Emir fiyatı broker fiyat adımına uymuyor.')
            gap = {'BUY_LIMIT': tick.ask-price, 'SELL_LIMIT':price-tick.bid,
                   'BUY_STOP':price-tick.ask, 'SELL_STOP':tick.bid-price}[pending_type]
            if gap <= 0 or gap + info.point*1e-5 < info.trade_stops_level*info.point:
                raise ValueError('Emir fiyatı piyasanın doğru tarafında ve minimum mesafe dışında olmalı.')
        sl = round(price + (-1 if buy else 1) * sl_points * info.point, info.digits) if sl_points else 0.0
        tp = round(price + (1 if buy else -1) * tp_points * info.point, info.digits) if tp_points else 0.0
        exit_price = price if pending_type else float(tick.bid if buy else tick.ask)
        minimum = float(info.trade_stops_level) * info.point
        if ((sl and ((exit_price-sl if buy else sl-exit_price) < minimum)) or
                (tp and ((tp-exit_price if buy else exit_price-tp) < minimum))):
            return {'success': False, 'error': 'SL/TP broker stop mesafesine veya spread mesafesine uymuyor.'}
        request = dict(action=1, symbol=symbol, volume=volume, type=0 if buy else 1,
                       price=price, sl=sl, tp=tp, deviation=20, magic=magic,
                       comment=comment[:31], type_time=0)
        if pending_type:
            request.update(action=5, type=types[pending_type], type_filling=2)
            return {**_send_once(mt5, request), 'pending_order': True}
        fillings = [f for bit, f in ((2, 1), (1, 0)) if int(info.filling_mode) & bit]
        if int(info.trade_exemode) != 2:
            fillings.append(2)  # RETURN is not allowed for Market Execution.
        if not fillings:
            return {'success': False, 'error': 'Desteklenen emir doldurma yöntemi yok.'}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    for filling in fillings:
        request['type_filling'] = filling
        try:
            result = mt5.order_send(request)
            if result is None:
                raise RuntimeError('Yanıt yok')
            code = int(result.retcode)
            response = dict(success=code in (10008, 10009, 10010), retcode=code,
                            partial=code == 10010, pending=code == 10008,
                            ticket=int(result.order), price=float(result.price), volume=float(result.volume),
                            comment=str(result.comment))
            if code in (10012, 10031):
                response['uncertain'] = True
            if not response['success']:
                response['error'] = str(result.comment)
            if code != 10030:
                return response
        except Exception:
            return {'success': False, 'uncertain': True,
                    'error': 'Emir sonucu belirsiz; broker durumunu kontrol edin.'}
    return response


def _account(mt5, expected_login=0, expected_server=''):
    account = mt5.account_info()
    if account is None:
        raise RuntimeError('Hesap bilgisi doğrulanamadı.')
    if expected_login and (int(account.login) != expected_login or str(account.server) != expected_server):
        raise RuntimeError('Aktif hesap değişti; işlem gönderilmedi.')
    return account


def _market(mt5, symbol):
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        raise ValueError('Sembol veya fiyat bilgisi alınamadı.')
    if not all(math.isfinite(float(v)) for v in (tick.bid, tick.ask, tick.time)) or not 0 <= time.time() - tick.time <= 10 or tick.bid <= 0 or tick.ask < tick.bid:
        raise ValueError('Fiyat eski veya geçersiz; işlem gönderilmedi.')
    return info, tick


def _volume_valid(info, volume):
    step = float(info.volume_step)
    return math.isfinite(volume) and step > 0 and info.volume_min <= volume <= info.volume_max and math.isclose(volume / step, round(volume / step), rel_tol=0, abs_tol=1e-7)


def _price_valid(info, price):
    step = float(getattr(info, 'trade_tick_size', 0) or info.point)
    return math.isfinite(price) and price >= 0 and (not price or math.isclose(price / step, round(price / step), rel_tol=0, abs_tol=1e-6))


def _send_once(mt5, request, allow_no_change=False):
    try:
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError('Yanıt alınamadı')
        code = int(result.retcode)
        success = code in (10008, 10009, 10010) or (allow_no_change and code == 10025)
        return dict(success=success, retcode=code, partial=code == 10010, pending=code == 10008,
                    uncertain=code in (10012, 10031), ticket=int(result.order),
                    volume=float(result.volume), price=float(result.price),
                    error='' if success else str(result.comment), comment=str(result.comment))
    except Exception:
        return {'success': False, 'uncertain': True, 'error': 'İşlem sonucu belirsiz; MT5 üzerinden kontrol edin.'}


def pending_orders(mt5):
    orders = mt5.orders_get()
    if orders is None:
        raise RuntimeError('Bekleyen emirler okunamadı.')
    names = {2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP', 6:'BUY_STOP_LIMIT', 7:'SELL_STOP_LIMIT'}
    return [dict(ticket=int(o.ticket), symbol=str(o.symbol), type=names.get(int(o.type), str(o.type)),
                 volume=float(o.volume_current), price=float(o.price_open), sl=float(o.sl), tp=float(o.tp),
                 magic=int(o.magic), time=int(o.time_setup), expiration=int(o.time_expiration)) for o in orders]


def manage_position(mt5, ticket, operation, values, expected_login, expected_server):
    try:
        _account(mt5, expected_login, expected_server)
        rows = mt5.positions_get(ticket=ticket)
        if rows is None:
            raise RuntimeError('Pozisyon okunamadı.')
        if len(rows) != 1 or int(rows[0].ticket) != ticket:
            raise ValueError('Pozisyon artık açık değil; listeyi yenileyin.')
        p = rows[0]
        info, tick = _market(mt5, str(p.symbol))
        buy = int(p.type) == 0
        exit_price = float(tick.bid if buy else tick.ask)
        if operation == 'stops':
            # Reject stale edits instead of silently overwriting another terminal's changes.
            for name in ('sl', 'tp'):
                old = values.get('expected_' + name)
                if old is not None and not math.isclose(float(old), float(getattr(p, name)), rel_tol=0, abs_tol=info.point/10):
                    raise ValueError('SL/TP başka bir işlemle değişti; pencereyi yeniden açın.')
            sl = float(p.sl if values.get('sl') is None else values['sl'])
            tp = float(p.tp if values.get('tp') is None else values['tp'])
            if not _price_valid(info, sl) or not _price_valid(info, tp):
                raise ValueError('SL/TP fiyat adımına uymuyor.')
            minimum = max(float(info.trade_stops_level), float(info.trade_freeze_level)) * info.point
            epsilon = info.point * 1e-5
            if (sl and (exit_price-sl if buy else sl-exit_price) + epsilon < minimum) or (tp and (tp-exit_price if buy else exit_price-tp) + epsilon < minimum):
                raise ValueError('SL/TP yönü veya broker stop/dondurma mesafesi uygun değil.')
            request = dict(action=6, position=ticket, symbol=str(p.symbol), sl=sl, tp=tp)
        elif operation == 'partial':
            volume = float(values['volume'])
            remaining = float(p.volume) - volume
            if not _volume_valid(info, volume) or remaining <= 1e-9 or not _volume_valid(info, remaining):
                raise ValueError('Kapatılacak lot ve kalan lot broker adımına uymalı; tam kapatma için Kapat düğmesini kullanın.')
            request = dict(action=1, position=ticket, symbol=str(p.symbol), volume=volume,
                           type=1 if buy else 0, price=exit_price, deviation=50, magic=int(p.magic),
                           comment='Panel partial close', type_time=0)
        else:
            raise ValueError('Desteklenmeyen pozisyon işlemi.')
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    if operation == 'stops':
        result = _send_once(mt5, request, allow_no_change=True)
    else:
        fillings = [f for bit, f in ((2,1), (1,0)) if int(info.filling_mode) & bit]
        if int(info.trade_exemode) != 2:
            fillings.append(2)
        if not fillings:
            return {'success': False, 'error': 'Desteklenen doldurma yöntemi yok.'}
        for filling in fillings:
            request['type_filling'] = filling
            result = _send_once(mt5, request)
            if result.get('retcode') != 10030:
                break
    return {**result, 'position': ticket, 'operation': operation}


def cancel_pending(mt5, ticket, expected_login, expected_server):
    try:
        _account(mt5, expected_login, expected_server)
        orders = mt5.orders_get(ticket=ticket)
        if orders is None:
            raise RuntimeError('Bekleyen emir doğrulanamadı.')
        if not orders:
            raise ValueError('Emir artık beklemiyor; gerçekleşmiş olabilir. Pozisyonları kontrol edin.')
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    return {**_send_once(mt5, dict(action=8, order=ticket)), 'order_ticket': ticket}


def live_snapshot(mt5, symbols):
    account = _account(mt5)
    fields = ('login','trade_mode','balance','equity','profit','margin','margin_free','margin_level','currency','server','leverage')
    account_data = {k:getattr(account, k) for k in fields}
    account_data['connected'] = True
    account_data['server_time'] = time.strftime('%H:%M:%S')
    ticks = {}
    for symbol in symbols:
        tick, info = mt5.symbol_info_tick(symbol), mt5.symbol_info(symbol)
        if tick is not None and info is not None:
            ticks[symbol] = dict(symbol=symbol, bid=float(tick.bid), ask=float(tick.ask), time=int(tick.time),
                                 spread=round((tick.ask-tick.bid)/info.point) if info.point else 0)
    return dict(account=account_data, positions=positions(mt5), orders=pending_orders(mt5),
                prices=ticks, sampled_at=time.time())


def _deal_row(d):
    return dict(ticket=int(d.ticket), position_id=int(d.position_id), symbol=str(d.symbol),
                type=int(d.type), entry=int(d.entry), time=int(d.time), volume=float(d.volume),
                profit=float(d.profit), commission=float(d.commission), swap=float(d.swap), fee=float(d.fee))


def report_activity(mt5, days):
    from datetime import timedelta
    account = _account(mt5)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    deals, current = mt5.history_deals_get(start, end), mt5.positions_get()
    if deals is None or current is None:
        raise RuntimeError('Rapor verisi okunamadı.')
    return dict(account=[int(account.login),str(account.server)], currency=str(account.currency),
                start=int(start.timestamp()), end=int(end.timestamp()),
                deals=[_deal_row(d) for d in deals if int(d.type) not in (2,3,5,6)],
                open_ids=[int(p.identifier) for p in current])


def position_history(mt5, position_id, account_scope):
    _account(mt5, *account_scope)
    rows = mt5.history_deals_get(position=position_id)
    if rows is None:
        raise RuntimeError('Pozisyon yaşam döngüsü okunamadı.')
    return [_deal_row(d) for d in rows]


def account_identity(mt5):
    account = _account(mt5)
    return [int(account.login), str(account.server)]


def account_status(mt5):
    account = _account(mt5)
    return [int(account.login), str(account.server), int(account.trade_mode)]


def reconcile_open(mt5, metadata, created):
    """Positive broker evidence only. Absence never authorizes another send."""
    if account_identity(mt5) != metadata['account']:
        return None
    start = datetime.fromtimestamp(created - 60, timezone.utc)
    history = mt5.history_orders_get(start, datetime.now(timezone.utc))
    active = mt5.orders_get()
    if history is None or active is None:
        return None
    expected = metadata['order']
    matches = {}
    for order in list(history) + list(active):
        if (str(order.comment) == metadata['tag'] and str(order.symbol) == expected['symbol']
                and int(order.magic) == expected['magic'] and int(order.type) == expected['type']
                and float(order.time_setup) >= created - 60
                and math.isclose(float(order.volume_initial), expected['volume'], abs_tol=1e-8)):
            matches[int(order.ticket)] = order
    if len(matches) != 1:
        return None
    order = next(iter(matches.values()))
    state = int(order.state)
    if state not in (1, 2, 3, 4, 5, 6):
        return None
    filled = max(0., float(order.volume_initial) - float(order.volume_current))
    if state == 4:
        filled = float(order.volume_initial)
    pending = state in (1, 3)
    success = pending or state == 4 or filled > 0
    return {'success': success, 'uncertain': False, 'pending': pending,
            'partial': 0 < filled < expected['volume'], 'ticket': int(order.ticket),
            'volume': filled, 'broker_state': state,
            'retcode': 10008 if pending else (10010 if 0 < filled < expected['volume'] else 10009 if success else 10006),
            'error': '' if success else 'Broker emri iptal etti, reddetti veya süresi doldu.',
            'comment': 'Broker emir geçmişinden doğrulandı.'}
