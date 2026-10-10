"""Executed beside MT5 so each operation crosses RPyC as one JSON response."""
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

MANUAL_MAGIC = 123460
HMA_MAGIC = 123461
AI_MAGIC = 123462

SYMBOL_NAME_PATTERN = r'^[A-Za-z0-9_.#-]+$'


def _symbol_category(info):
    """Broker folders/metadata provide discovery hints; unknown assets stay other."""
    name = str(info.name).upper()
    path = str(getattr(info, 'path', '')).casefold()
    text = str(getattr(info, 'description', '')).casefold()
    base = str(getattr(info, 'currency_base', '')).upper()
    folders = [('crypto', ('crypto', 'kripto')), ('indices', ('indices', 'index', 'endeks')),
               ('stocks', ('stock', 'equities', 'equity', 'share', 'hisse')),
               ('metals', ('metal', 'gold', 'silver', 'altın', 'altin', 'gümüş')),
               ('commodities', ('commodit', 'energies', 'energy', 'oil', 'crude', 'gas', 'agricultur', 'emtia', 'petrol')),
               ('forex', ('forex', 'foreign exchange', 'döviz'))]
    for category, keywords in folders:
        if any(word in path for word in keywords):
            return category
    if any(word in text for word in ('crypto', 'bitcoin', 'ethereum', 'kripto')) or base in {'BTC','ETH','LTC','BCH','XRP','SOL','ADA','DOGE'} or re.match(r'^(BTC|ETH|LTC|BCH|XRP|SOL|ADA|DOGE)(USD|EUR|GBP|JPY|USDT)', name):
        return 'crypto'
    if re.search(r'\b(metal|gold|silver|platinum|palladium|altın|altin|gümüş)\b', text) or base in {'XAU','XAG','XPT','XPD'} or name.startswith(('XAU','XAG','XPT','XPD')):
        return 'metals'
    if any(word in text for word in ('indices', 'index', 'endeks')):
        return 'indices'
    if any(word in text for word in ('stock', 'equities', 'equity', 'share', 'hisse')):
        return 'stocks'
    if any(word in text for word in ('commodit', 'energies', 'energy', 'oil', 'crude', 'gas', 'agricultur', 'emtia', 'petrol')):
        return 'commodities'
    currencies = {'USD','EUR','GBP','JPY','CHF','AUD','CAD','NZD','TRY','ZAR','CNH','CNY','MXN','NOK','SEK','DKK','PLN','HUF','CZK','SGD','HKD','ILS','RUB','BRL','THB'}
    if ('forex' in text or 'foreign exchange' in text or 'döviz' in text
            or getattr(info, 'trade_calc_mode', None) in (0, 5)
            or (len(name) >= 6 and name[:3] in currencies and name[3:6] in currencies and name[:3] != name[3:6])):
        return 'forex'
    return 'other'


def symbol_catalog(mt5, expected_login, expected_server, expected_trade_mode=None):
    """Read all terminal symbols once, without subscribing quotes or checking/sending orders."""
    account = _account(mt5, expected_login, expected_server)
    mode = int(account.trade_mode)
    if mode not in (0, 2) or (expected_trade_mode is not None and mode != expected_trade_mode):
        raise RuntimeError('Sembol kataloğu için seçili hesap türü doğrulanamadı.')
    status = [int(account.login), str(account.server), mode]
    rows = mt5.symbols_get()
    if rows is None:
        raise RuntimeError('Broker sembol kataloğu okunamadı.')
    result = []
    for info in rows:
        name = str(info.name)
        trade_mode = int(getattr(info, 'trade_mode', 0))
        result.append({'name': name, 'description': str(getattr(info, 'description', '')),
                       'path': str(getattr(info, 'path', '')), 'category': _symbol_category(info),
                       'trade_mode': trade_mode, 'digits': int(getattr(info, 'digits', 0)),
                       'visible': bool(getattr(info, 'visible', False)),
                       'selectable': trade_mode in (1, 2, 4) and 0 < len(name) <= 32
                                     and re.fullmatch(SYMBOL_NAME_PATTERN, name) is not None})
    if account_status(mt5) != status:
        raise RuntimeError('Sembol kataloğu okunurken aktif hesap değişti.')
    result.sort(key=lambda row: (row['name'].casefold(), row['name']))
    return {'account': status[:2] + ['DEMO' if mode == 0 else 'REAL'],
            'symbols': result, 'total': len(result)}


def positions(mt5, include_commission=False, tick_offset=0, quotes=None):
    rows = mt5.positions_get()
    if rows is None:
        raise RuntimeError('Pozisyonlar okunamadı: ' + str(mt5.last_error()))
    result = []
    quotes = {} if quotes is None else quotes
    for p in rows:
        symbol_info = mt5.symbol_info(p.symbol)
        # MT5 positions have no commission field; use the stable position identifier
        # to include commissions already charged on its opening/partial-close deals.
        commission = None
        opening_fills = []
        if include_commission:
            try:
                deals = mt5.history_deals_get(position=int(p.identifier))
                if deals:
                    commission = round(sum(float(getattr(d, 'commission', 0)) for d in deals), 2)
                    opening_fills = [dict(ticket=int(d.ticket), order=int(d.order),
                        symbol=str(d.symbol), magic=int(d.magic), type=int(d.type), entry=int(d.entry),
                        volume=float(d.volume), price=float(d.price)) for d in deals
                        if int(getattr(d, 'entry', -1)) in (0, 2) and int(getattr(d, 'type', -1)) in (0, 1)]
            except Exception:
                pass  # Keep live positions available when only cost history is unavailable.
        row = dict(ticket=int(p.ticket), identifier=int(p.identifier), symbol=str(p.symbol),
                 type='BUY' if p.type == 0 else 'SELL', type_raw=int(p.type),
                 magic=int(p.magic), volume=float(p.volume),
                 price_open=float(p.price_open), price_current=float(p.price_current),
                 digits=int(symbol_info.digits) if symbol_info else 5,
                 sl=float(p.sl), tp=float(p.tp), profit=float(p.profit), swap=float(p.swap),
                 commission=commission,
                 time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.time)))
        if include_commission:
            if str(p.symbol) not in quotes:
                try:
                    quotes[str(p.symbol)] = price(mt5, str(p.symbol), tick_offset)
                except Exception:
                    quotes[str(p.symbol)] = None
            row.update(opening_fills=opening_fills, quote=quotes[str(p.symbol)])
        result.append(row)
    return result


def positions_snapshot(mt5, tick_offset=0):
    account = account_identity(mt5)
    rows = positions(mt5, include_commission=True, tick_offset=tick_offset)
    if account_identity(mt5) != account:
        raise RuntimeError('Pozisyonlar okunurken aktif hesap değişti.')
    return dict(account=account, positions=rows)


def rates(mt5, symbol, timeframe, count):
    rows = mt5.copy_rates_from_pos(symbol, getattr(mt5, timeframe), 0, count)
    if rows is None:
        raise RuntimeError('Mum verisi okunamadı')
    return [dict(time=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                 close=float(r[4]), tick_volume=int(r[5])) for r in rows]


def research_rates(mt5, symbol, timeframe, start, end, clock_offset=0):
    """Read-only, chunked history. End is exclusive; forming bars are excluded."""
    seconds = {'TIMEFRAME_H1': 3600, 'TIMEFRAME_H4': 14400}.get(timeframe)
    if seconds is None or end <= start or end - start > 5 * 366 * 86400:
        raise ValueError('Geçersiz araştırma tarih aralığı.')
    # Existing installation reports broker-shifted timestamps. Keep raw bar times
    # and apply its configured offset only when testing whether a bar is closed.
    cutoff = min(int(end), int(time.time()) + int(clock_offset))
    result = {}
    cursor = int(start)
    while cursor < cutoff:
        chunk_end = min(cursor + 90 * 86400, cutoff)
        rows = mt5.copy_rates_range(symbol, getattr(mt5, timeframe),
                                   datetime.fromtimestamp(cursor, timezone.utc),
                                   datetime.fromtimestamp(chunk_end - 1, timezone.utc))
        if rows is None:
            raise RuntimeError('Geçmiş mumlar okunamadı: ' + str(mt5.last_error()))
        for r in rows:
            t = int(r[0])
            if cursor <= t < chunk_end and t + seconds <= cutoff:
                result[t] = dict(time=t, open=float(r[1]), high=float(r[2]), low=float(r[3]),
                                 close=float(r[4]), tick_volume=int(r[5]), spread=int(r[6]))
        if len(result) > 50000:
            raise ValueError('Araştırma 50000 mum sınırını aşıyor.')
        cursor = chunk_end
    return [result[t] for t in sorted(result)]


def risk(mt5, tick_offset=0):
    account = mt5.account_info()
    if account is None:
        raise RuntimeError('Hesap bilgisi okunamadı; yeni emir engellendi.')
    now = datetime.now(timezone.utc)
    # This terminal reports deal timestamps in broker time. Shift the UTC day
    # window by the measured terminal offset, as with get_history().
    offset = timedelta(seconds=tick_offset)
    deals = mt5.history_deals_get(now.replace(hour=0, minute=0, second=0, microsecond=0) + offset,
                                  now + offset)
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
    _account(mt5, int(account.login), str(account.server))
    return account, current, pending, realized, floating


def risk_status(mt5, expected_login, expected_server, daily_loss_limit, tick_offset=0):
    account, current, pending, realized, floating = risk(mt5, tick_offset)
    if int(account.login) != expected_login or str(account.server) != expected_server:
        raise RuntimeError('Aktif hesap değişti; risk durumu doğrulanamadı.')
    loss = max(0.0, -(realized + min(0.0, floating)))
    return {'daily_loss':round(loss, 2), 'daily_loss_limit':daily_loss_limit,
            'ratio':round(loss/daily_loss_limit, 4) if daily_loss_limit > 0 else None,
            'currency':str(account.currency), 'login':int(account.login), 'server':str(account.server),
            'open_positions':len(current), 'pending_orders':len(pending)}


def stop_guidance(info, tick, pending_type=None):
    spread = 0 if pending_type else (float(tick.ask) - float(tick.bid)) / info.point
    stops = float(info.trade_stops_level)
    return {'point': float(info.point), 'spread_points': round(spread, 3),
            'stops_level_points': stops,
            'min_sl_points': max(1, math.ceil(spread + stops - 1e-7) + 1),
            'min_tp_points': max(1, math.ceil(stops - spread - 1e-7) + 1)}


def stop_distance_error(symbol, guidance):
    return (f"SL/TP broker stop mesafesine veya spread mesafesine uymuyor. {symbol}: "
            f"spread {guidance['spread_points']:g} puan; SL için en az {guidance['min_sl_points']} puan, "
            f"TP için en az {guidance['min_tp_points']} puan girin. 0 korumayı kaldırır.")


def trade_preview(mt5, symbol, order_type, volume, sl_points, pending_type, entry_price,
                  expected_login, expected_server, tick_offset=0, daily_loss_limit=None,
                  enforce_daily_limit=True, max_order_lots=None, max_total_lots=None,
                  max_open_orders=None, tp_points=0, max_trade_risk_pct=2.0):
    """Broker-calculated figures only; unknown values remain unknown."""
    try:
        account, current, pending, realized, floating = risk(mt5, tick_offset)
        if int(account.login) != expected_login or str(account.server) != expected_server:
            raise ValueError('Aktif hesap değişti; risk önizlemesi geçersiz.')
        if order_type not in ('BUY', 'SELL') or not math.isfinite(volume) or volume <= 0:
            raise ValueError('Geçersiz emir parametresi.')
        loss = max(0.0, -(realized + min(0.0, floating)))
        if enforce_daily_limit and daily_loss_limit is not None and (
                not math.isfinite(daily_loss_limit) or daily_loss_limit <= 0 or loss >= daily_loss_limit):
            raise ValueError('Günlük zarar sınırı: yeni emir engellendi.')
        if max_order_lots is not None and (not math.isfinite(max_order_lots) or volume > max_order_lots + 1e-9):
            raise ValueError('Emir lotu yapılandırılan üst sınırı aşıyor.')
        committed = _committed_volume(current, pending)
        if max_open_orders is not None and (type(max_open_orders) is not int or max_open_orders < 1):
            raise ValueError('Geçersiz açık işlem sayısı sınırı.')
        if ((max_total_lots is not None and (
                not math.isfinite(max_total_lots) or committed + volume > max_total_lots + 1e-9)) or
                (max_open_orders is not None and len(current) + len(pending) + 1 > max_open_orders)):
            raise ValueError('Toplam açık/bekleyen işlem sınırı aşılıyor.')
        info, tick = _market(mt5, symbol, tick_offset)
        if not _volume_valid(info, volume):
            raise ValueError('Lot miktarı broker sınırlarına uymuyor.')
        if pending_type:
            if pending_type not in ('BUY_LIMIT','SELL_LIMIT','BUY_STOP','SELL_STOP') or pending_type.startswith('BUY') != (order_type == 'BUY') or entry_price is None:
                raise ValueError('Geçersiz bekleyen emir parametresi.')
            required = 2 if pending_type.endswith('LIMIT') else 4
            if not (int(info.order_mode) & required) or not (int(info.expiration_mode) & 1):
                raise ValueError('Broker bu bekleyen emir türünü desteklemiyor.')
        price = float(entry_price) if pending_type else float(tick.ask if order_type == 'BUY' else tick.bid)
        if not _price_valid(info, price) or price <= 0:
            raise ValueError('Geçersiz giriş fiyatı.')
        if pending_type:
            gap = {'BUY_LIMIT':tick.ask-price, 'SELL_LIMIT':price-tick.bid,
                   'BUY_STOP':price-tick.ask, 'SELL_STOP':tick.bid-price}[pending_type]
            if gap <= 0 or gap + info.point*1e-5 < info.trade_stops_level*info.point:
                raise ValueError('Bekleyen emir fiyatı broker mesafesine uymuyor.')
        sl, tp = _protection_prices(info, price, order_type == 'BUY', sl_points, tp_points)
        _opening_permissions(info, order_type, pending_type, sl, tp)
        _directional_volume(info, symbol, order_type, volume, current, pending)
        guidance = stop_guidance(info, tick, pending_type)
        exit_price = price if pending_type else float(tick.bid if order_type == 'BUY' else tick.ask)
        if tp and not _stop_distance_valid(info, tp-exit_price if order_type == 'BUY' else exit_price-tp):
            return {'success': False, 'error': stop_distance_error(symbol, guidance), 'stop_guidance': guidance}
        if sl:
            exit_price = price if pending_type else float(tick.bid if order_type == 'BUY' else tick.ask)
            stop_distance = exit_price - sl if order_type == 'BUY' else sl - exit_price
            if not _stop_distance_valid(info, stop_distance):
                return {'success': False, 'error': 'SL broker mesafesi uygun değil. ' + stop_distance_error(symbol, guidance), 'stop_guidance': guidance}
        trade_risk = _trade_risk_guard(mt5, account, symbol, order_type, volume, price, sl, max_trade_risk_pct)
        if not trade_risk['success']:
            return trade_risk
        broker_type = 0 if order_type == 'BUY' else 1
        calc_profit = getattr(mt5, 'order_calc_profit', None)
        calc_margin = getattr(mt5, 'order_calc_margin', None)
        margin_result = calc_margin(broker_type, symbol, volume, price) if calc_margin else None
        if margin_result is not None and not math.isfinite(float(margin_result)):
            margin_result = None
        existing_risk, unprotected = 0.0, 0
        for p in current:
            stop = float(getattr(p, 'sl', 0) or 0)
            if not stop:
                unprotected += 1
                continue
            outcome = calc_profit(int(p.type), str(p.symbol), float(p.volume),
                                  float(p.price_open), stop) if calc_profit else None
            if outcome is None or not math.isfinite(float(outcome)):
                unprotected += 1
            else:
                existing_risk += max(0.0, -float(outcome))
        _account(mt5, expected_login, expected_server)
        return {**trade_risk, 'success': True, 'symbol': symbol, 'side': order_type, 'volume': volume,
                'currency': str(account.currency), 'account_type': {0:'DEMO', 2:'REAL'}.get(int(account.trade_mode), 'OTHER'),
                'entry_price': price, 'stop_price': sl,
                'margin_required': float(margin_result) if margin_result is not None else None,
                'free_margin': float(account.margin_free), 'spread_points': round((tick.ask-tick.bid)/info.point, 1),
                'existing_stop_risk': round(existing_risk, 2), 'positions_without_stop': unprotected,
                'daily_loss': round(loss, 2), 'pending_orders': len(pending),
                'commission_included': False, 'quote_time': int(tick.time)-tick_offset, 'stop_guidance': guidance}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}


def symbol_spec(mt5, symbol):
    account = mt5.account_info()
    if account is None or not mt5.symbol_select(symbol, True):
        raise RuntimeError('Broker hesabı veya sembol doğrulanamadı.')
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        raise RuntimeError('Sembol özellikleri veya fiyat alınamadı.')
    return {'symbol':symbol, 'point':float(info.point), 'digits':int(info.digits),
            'contract_size':float(getattr(info, 'trade_contract_size', 0)),
            'profit_currency':str(getattr(info, 'currency_profit', '')),
            'account_currency':str(getattr(account, 'currency', '')),
            'linear_profit':getattr(info, 'trade_calc_mode', -1) in {
                getattr(mt5, name, None) for name in ('SYMBOL_CALC_MODE_FOREX',
                'SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE', 'SYMBOL_CALC_MODE_CFD',
                'SYMBOL_CALC_MODE_CFDINDEX', 'SYMBOL_CALC_MODE_CFDLEVERAGE',
                'SYMBOL_CALC_MODE_EXCH_STOCKS', 'SYMBOL_CALC_MODE_EXCH_STOCKS_MOEX')},
            'volume_min':float(info.volume_min), 'volume_max':float(info.volume_max),
            'volume_step':float(info.volume_step), 'stops_level_points':int(info.trade_stops_level),
            'freeze_level_points':int(getattr(info, 'trade_freeze_level', 0)),
            'filling_mode':int(info.filling_mode), 'execution_mode':int(info.trade_exemode),
            'order_mode':int(getattr(info, 'order_mode', 0)),
            'expiration_mode':int(getattr(info, 'expiration_mode', 0)),
            'tick_time':int(tick.time), 'account_mode':int(account.margin_mode),
            'account_type':{0:'DEMO',1:'CONTEST',2:'REAL'}.get(int(account.trade_mode),'UNKNOWN')}


def research_margin_rules(mt5, symbol, volume, low, high, expected_login, expected_server):
    """Read-only calibration at historical price bounds. Never checks/sends an order."""
    account = _account(mt5, expected_login, expected_server)
    if int(account.margin_mode) not in (0, 2):
        raise ValueError('Research margin supports retail accounts only; exchange margin is unsupported.')
    info = mt5.symbol_info(symbol)
    if info is None or not _volume_valid(info, volume) or not 0 < low < high:
        raise ValueError('Invalid research margin inputs.')
    initial = float(getattr(info, 'margin_initial', 0))
    maintenance = float(getattr(info, 'margin_maintenance', 0))
    if maintenance and (not initial or not math.isclose(initial, maintenance)):
        raise ValueError('Different initial/maintenance margin requires a separate model.')
    mode, call, stop = int(account.margin_so_mode), float(account.margin_so_call), float(account.margin_so_so)
    if mode not in (0, 1) or not all(math.isfinite(v) and v >= 0 for v in (call, stop)) or call < stop:
        raise ValueError('Broker stop-out settings could not be verified.')
    models = {}
    for side, order_type in (('BUY', 0), ('SELL', 1)):
        prices = [low + (high-low)*fraction for fraction in (0, .25, .5, .75, 1)]
        margins = [mt5.order_calc_margin(order_type, symbol, volume, price) for price in prices]
        if any(v is None or not math.isfinite(float(v)) or v <= 0 for v in margins):
            raise ValueError('Broker margin calculation unavailable.')
        margins = [float(v) for v in margins]
        slope = (margins[-1]-margins[0])/(high-low)
        intercept = margins[0]-slope*low
        if any(not math.isclose(value, intercept+slope*price, rel_tol=1e-5, abs_tol=.01)
               for price, value in zip(prices, margins)):
            raise ValueError('Nonlinear broker margin cannot be simulated with this model.')
        models[side] = {'intercept': intercept, 'slope': slope}
    _account(mt5, expected_login, expected_server)
    return {'mode': 'PERCENT' if mode == 0 else 'MONEY', 'margin_call': call, 'stop_out': stop,
            'leverage': int(account.leverage), 'models': models, 'volume': volume,
            'price_min': low, 'price_max': high, 'captured_at': int(time.time()),
            'tick_size': float(getattr(info, 'trade_tick_size', 0) or info.point),
            'stops_level_points': int(info.trade_stops_level),
            'source': 'MT5 order_calc_margin; current broker rules applied to historical prices',
            'margin_policy': 'single isolated position; margin fixed at entry; full commission reserved'}


def broker_compatibility(mt5, symbol, expected_login, expected_server, tick_offset=0):
    """OrderCheck matrix; this function never calls order_send."""
    account = mt5.account_info()
    if account is None or int(account.login) != expected_login or str(account.server) != expected_server:
        raise RuntimeError('Aktif broker hesabı doğrulanamadı.')
    spec = symbol_spec(mt5, symbol)
    info, tick = _market(mt5, symbol, tick_offset)
    if not callable(getattr(mt5, 'order_check', None)):
        return {'available':False, 'checks':[], 'symbol':symbol}
    volume = float(info.volume_min)
    gap = max(int(info.trade_stops_level)+5, 10)*float(info.point)
    configs = [('BUY_FOK', 1, 0, float(tick.ask), 0), ('SELL_FOK', 1, 1, float(tick.bid), 0),
               ('BUY_IOC', 1, 0, float(tick.ask), 1), ('SELL_IOC', 1, 1, float(tick.bid), 1)]
    if spec['order_mode'] & 2 and spec['expiration_mode'] & 1:
        configs += [('BUY_LIMIT', 5, 2, round(tick.ask-gap, info.digits), 2),
                    ('SELL_LIMIT', 5, 3, round(tick.bid+gap, info.digits), 2)]
    if spec['order_mode'] & 4 and spec['expiration_mode'] & 1:
        configs += [('BUY_STOP', 5, 4, round(tick.ask+gap, info.digits), 2),
                    ('SELL_STOP', 5, 5, round(tick.bid-gap, info.digits), 2)]
    checks = []
    for name, action, kind, price, filling in configs:
        request = {'action':action, 'symbol':symbol, 'volume':volume, 'type':kind,
                   'price':price, 'type_filling':filling, 'type_time':0}
        try:
            result = mt5.order_check(request)
            checks.append({'name':name, 'retcode':int(result.retcode) if result else None,
                           'comment':str(result.comment)[:120] if result else 'No response',
                           'accepted':bool(result and int(result.retcode) == 0)})
        except Exception as exc:
            checks.append({'name':name, 'retcode':None, 'comment':type(exc).__name__, 'accepted':False})
    return {'available':True, 'symbol':symbol, 'checks':checks, 'order_sent':False,
            'account_type':spec['account_type']}


def open_deal(mt5, symbol, order_type, volume, sl_points, tp_points, comment, magic,
              daily_loss_limit, expected_login, expected_server, max_tick_age=10, pending_type=None, entry_price=None,
              max_order_lots=.10, max_total_lots=.50, max_open_orders=10, tick_offset=0,
              ai_max_trade_risk_pct=1.0, enforce_daily_limit=True, max_trade_risk_pct=2.0):
    try:
        if order_type not in ('BUY', 'SELL') or not isinstance(symbol, str) or not symbol.strip():
            raise ValueError('Geçersiz sembol veya emir yönü.')
        account, current, pending, realized, floating = risk(mt5, tick_offset)
        if expected_login and (int(account.login) != expected_login or str(account.server) != expected_server):
            return {'success': False, 'error': 'Aktif MT5 hesabı seçilen hesapla uyuşmuyor.'}
        # Open gains cannot mask realized losses. Amounts are in account currency, day is UTC.
        loss = max(0.0, -(realized + min(0.0, floating)))
        if enforce_daily_limit and (not math.isfinite(daily_loss_limit) or daily_loss_limit <= 0 or loss >= daily_loss_limit):
            return {'success': False, 'error': 'Günlük zarar sınırı: yeni emir engellendi.',
                    'daily_loss': loss, 'daily_loss_limit': daily_loss_limit, 'currency': str(account.currency)}
        if (not all(math.isfinite(x) and x > 0 for x in (volume, max_order_lots, max_total_lots))
                or type(max_open_orders) is not int or max_open_orders < 1):
            return {'success': False, 'error': 'Geçersiz hesap risk sınırı; yeni emir engellendi.'}
        if volume > max_order_lots + 1e-9:
            return {'success': False, 'error': 'Emir lotu yapılandırılan üst sınırı aşıyor.'}
        scope = (int(account.login), str(account.server))
        committed = _committed_volume(current, pending)
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
        if info is None:
            return {'success': False, 'error': 'Sembol bilgisi alınamadı.'}
        quote_error = _quote_error(tick, tick_offset, max_tick_age, mt5)
        if quote_error:
            return {'success': False, 'error': quote_error}
        _symbol_rules(info)
        if not _volume_valid(info, volume):
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
        if not _price_valid(info, price) or price <= 0:
            raise ValueError('Giriş fiyatı broker fiyat adımına uymuyor.')
        sl, tp = _protection_prices(info, price, buy, sl_points, tp_points)
        _opening_permissions(info, order_type, pending_type, sl, tp)
        _directional_volume(info, symbol, order_type, volume, current, pending)
        exit_price = price if pending_type else float(tick.bid if buy else tick.ask)
        if ((sl and not _stop_distance_valid(info, exit_price-sl if buy else sl-exit_price)) or
                (tp and not _stop_distance_valid(info, tp-exit_price if buy else exit_price-tp))):
            guidance = stop_guidance(info, tick, pending_type)
            return {'success': False, 'error': stop_distance_error(symbol, guidance), 'stop_guidance': guidance}
        trade_risk = _trade_risk_guard(mt5, account, symbol, order_type, volume, price, sl,
                                     max_trade_risk_pct, ai_max_trade_risk_pct if magic == AI_MAGIC else None)
        if not trade_risk['success']:
            return trade_risk
        if magic == AI_MAGIC and (not sl or not tp):
            return {**trade_risk, 'success': False, 'reason_code': 'ai_protection_required',
                    'error': 'AI emri için geçerli Stop Loss ve Kâr Al zorunlu.'}
        request = dict(action=1, symbol=symbol, volume=volume, type=0 if buy else 1,
                       price=price, sl=sl, tp=tp, deviation=20, magic=magic,
                       comment=comment[:31], type_time=0)
        execution = dict(kind='pending' if pending_type else 'market', requested_price=price,
                         bid=float(tick.bid), ask=float(tick.ask), point=float(info.point),
                         quote_time=int(tick.time) - int(tick_offset), deviation_points=20)
        if pending_type:
            request.update(action=5, type=types[pending_type], type_filling=2)
            return {**trade_risk, **_checked_send(mt5, request, scope, tick_offset=tick_offset, max_tick_age=max_tick_age),
                    'pending_order': True, 'execution': execution}
        fillings = _fillings(info)
        if not fillings:
            return {'success': False, 'error': 'Desteklenen emir doldurma yöntemi yok.'}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    for filling in fillings:
        request['type_filling'] = filling
        response = {**trade_risk, **_checked_send(mt5, request, scope, tick_offset=tick_offset, max_tick_age=max_tick_age),
                    'execution': execution}
        # Only an explicit INVALID_FILL is proof that a different policy is safe.
        if response.get('retcode') != 10030:
            return response
    return response


def _account(mt5, expected_login=0, expected_server=''):
    account = mt5.account_info()
    if account is None:
        raise RuntimeError('Hesap bilgisi doğrulanamadı.')
    if expected_login and (int(account.login) != expected_login or str(account.server) != expected_server):
        raise RuntimeError('Aktif hesap değişti; işlem gönderilmedi.')
    return account


def broker_connection(mt5):
    try:
        terminal = mt5.terminal_info()
        value = getattr(terminal, 'connected', None)
        return value if isinstance(value, bool) else None
    except Exception:
        return None


def quote_status(tick, tick_offset=0, max_tick_age=10, connected=None):
    """Report observed connectivity and quote freshness, never infer trading sessions."""
    now = time.time()
    result = dict(broker_connected=connected, checked_at=now, age_seconds=None)
    connection = ('MT5 broker bağlantısı açık.' if connected is True else
                  'MT5 broker bağlantısı kesik.' if connected is False else
                  'MT5 broker bağlantısı doğrulanamadı.')
    state, message = 'missing', 'MT5 bu sembol için fiyat bildirmedi.'
    if tick is not None:
        values = [float(v) for v in (tick.bid, tick.ask, tick.time)]
        if not all(math.isfinite(v) for v in values) or tick.time <= 0 or tick.bid <= 0 or tick.ask < tick.bid:
            state, message = 'invalid', 'Broker geçerli fiyat bildirmedi.'
        else:
            age = now - (float(tick.time) - tick_offset)
            result['age_seconds'] = age
            if age < 0:
                state = 'future'
                message = 'Fiyat zamanı sunucu saatinden ileride. Sunucu saati ve broker saat farkı ayarını kontrol edin.'
            elif age > max_tick_age:
                state = 'stale'
                hours, remainder = divmod(int(age), 3600)
                minutes, seconds = divmod(remainder, 60)
                message = (f'Son fiyat {hours} saat {minutes} dakika {seconds} saniye önce alındı. '
                           'Bu sembolde güncel fiyat alınmıyor.')
            else:
                state, message = 'fresh', 'Bu sembolde fiyat güncel.'
    result.update(state=state, message=f'{message} {connection}')
    return result


def _quote_error(tick, tick_offset=0, max_tick_age=10, mt5=None):
    status = quote_status(tick, tick_offset, max_tick_age, broker_connection(mt5))
    if status['state'] != 'fresh' or status['broker_connected'] is not True:
        return status['message'] + ' Güncel fiyat ve broker bağlantısı doğrulanmadan işlem yapılamaz.'
    return None


def price(mt5, symbol, tick_offset=0):
    info = mt5.symbol_info(symbol)
    selected = bool(info is not None and getattr(info, 'select', False))
    if not selected:
        selected = bool(mt5.symbol_select(symbol, True))
        if selected:
            info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol) if selected else None
    status = quote_status(tick, tick_offset, connected=broker_connection(mt5))
    if not selected:
        status['message'] = status['message'].replace(
            'MT5 bu sembol için fiyat bildirmedi.', 'MT5 sembolü fiyat takibine alamadı.')
    valid = status['state'] not in ('missing', 'invalid')
    return dict(symbol=symbol, bid=float(tick.bid) if valid else 0.,
                ask=float(tick.ask) if valid else 0.,
                point=float(info.point) if info is not None and info.point else None,
                time=int(tick.time)-tick_offset if valid else 0,
                spread=round((tick.ask-tick.bid)/info.point) if valid and info is not None and info.point else 0,
                quote_status=status)


def _market(mt5, symbol, tick_offset=0):
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    if info is None:
        raise ValueError('Sembol bilgisi alınamadı.')
    _symbol_rules(info)
    quote_error = _quote_error(tick, tick_offset, mt5=mt5)
    if quote_error:
        raise ValueError(quote_error)
    return info, tick


def _symbol_rules(info):
    values = (float(info.point), float(info.volume_min), float(info.volume_max),
              float(info.volume_step), float(getattr(info, 'trade_tick_size', 0) or info.point),
              float(info.trade_stops_level), float(getattr(info, 'trade_freeze_level', 0)))
    if (not all(math.isfinite(v) for v in values) or any(v <= 0 for v in values[:5])
            or values[2] < values[1] or any(v < 0 for v in values[5:])):
        raise ValueError('Broker sembol fiyat/lot kuralları doğrulanamadı.')


def _committed_volume(current, pending):
    volumes = [float(p.volume) for p in current] + [float(o.volume_current) for o in pending]
    if any(not math.isfinite(v) or v < 0 for v in volumes):
        raise ValueError('Açık hacim doğrulanamadı; yeni emir engellendi.')
    total = sum(volumes)
    if not math.isfinite(total):
        raise ValueError('Açık hacim doğrulanamadı; yeni emir engellendi.')
    return total


def _protection_prices(info, price, buy, sl_points, tp_points):
    if any(not math.isfinite(float(v)) or float(v) < 0 for v in (sl_points, tp_points)):
        raise ValueError('SL/TP mesafeleri sonlu ve sıfır veya pozitif olmalı.')
    sl = round(price + (-1 if buy else 1) * sl_points * info.point, info.digits) if sl_points else 0.0
    tp = round(price + (1 if buy else -1) * tp_points * info.point, info.digits) if tp_points else 0.0
    # Decimal precision alone does not make a valid price: tick size can exceed point.
    if (not _price_valid(info, sl) or not _price_valid(info, tp)
            or (sl_points and (sl <= 0 or (price-sl if buy else sl-price) <= 0))
            or (tp_points and (tp <= 0 or (tp-price if buy else price-tp) <= 0))):
        raise ValueError('SL/TP yönü veya fiyatı broker fiyat adımına uymuyor.')
    return sl, tp


def _trade_risk_guard(mt5, account, symbol, side, volume, price, sl, max_trade_risk_pct,
                      ai_max_trade_risk_pct=None):
    """Money risk uses broker contract/currency calculations, never just lot size."""
    real = int(getattr(account, 'trade_mode', -1)) == 2
    fields = {'stop_required': real, 'stop_risk': None, 'trade_risk_amount': None,
              'risk_pct_equity': None, 'max_trade_risk_pct': None,
              'effective_trade_risk_pct': None, 'trade_risk_limit': None,
              'currency': str(account.currency)}

    def reject(code, message):
        return {**fields, 'success': False, 'uncertain': False, 'reason_code': code, 'error': message}

    try:
        maximum = float(max_trade_risk_pct)
        if not math.isfinite(maximum) or maximum <= 0:
            return reject('trade_risk_configuration_invalid', 'Emir başına parasal risk sınırı geçersiz; yeni emir engellendi.')
        fields['max_trade_risk_pct'] = maximum
        if ai_max_trade_risk_pct is not None:
            ai_maximum = float(ai_max_trade_risk_pct)
            if not math.isfinite(ai_maximum) or ai_maximum <= 0:
                return reject('trade_risk_configuration_invalid', 'AI parasal risk sınırı geçersiz; yeni emir engellendi.')
            maximum = min(maximum, ai_maximum)
        fields['effective_trade_risk_pct'] = maximum
        try:
            equity = float(getattr(account, 'equity', None))
        except (TypeError, ValueError):
            equity = None
        if equity is not None and math.isfinite(equity) and equity > 0:
            limit = equity * (maximum / 100)
            if not math.isfinite(limit) or limit <= 0:
                return reject('trade_risk_configuration_invalid', 'Parasal risk sınırı hesaplanamadı; yeni emir engellendi.')
            fields['trade_risk_limit'] = limit
        if real and not sl:
            return reject('real_stop_required', 'Gerçek hesapta yeni emir için geçerli Stop Loss zorunlu.')
        if not sl:
            return {**fields, 'success': True}
        if (not math.isfinite(float(volume)) or volume <= 0 or not math.isfinite(float(price))
                or price <= 0 or not math.isfinite(float(sl)) or sl <= 0):
            return reject('trade_risk_unverified', 'Stop riski için giriş fiyatı veya hacim doğrulanamadı.')
        if fields['trade_risk_limit'] is None:
            return reject('trade_risk_unverified', 'Stop riski için pozitif ve sonlu hesap özsermayesi doğrulanamadı.')
        calc = getattr(mt5, 'order_calc_profit', None)
        outcome = calc(0 if side == 'BUY' else 1, symbol, volume, price, sl) if callable(calc) else None
        if outcome is None or not math.isfinite(float(outcome)) or float(outcome) >= 0:
            return reject('trade_risk_unverified', 'Stop riski broker tarafından doğrulanamadı; yeni emir engellendi.')
        amount = -float(outcome)
        fields.update(stop_risk=amount, trade_risk_amount=amount,
                      risk_pct_equity=round(100 * (amount / equity), 3))
        limit = fields['trade_risk_limit']
        if amount > limit and not math.isclose(amount, limit, rel_tol=1e-10, abs_tol=0):
            return reject('trade_risk_exceeded',
                          f"Emrin stop riski {amount:g} {account.currency}; özsermayenin %{maximum:g} "
                          f"sınırı ({limit:g} {account.currency}) aşılıyor.")
        return {**fields, 'success': True}
    except Exception:
        return reject('trade_risk_unverified', 'Stop riski broker tarafından doğrulanamadı; yeni emir engellendi.')


def _stop_distance_valid(info, distance, freeze=False):
    minimum = float(info.trade_stops_level)
    if freeze:
        minimum = max(minimum, float(getattr(info, 'trade_freeze_level', 0)))
    return distance > 0 and distance + float(info.point) * 1e-5 >= minimum * info.point


def _opening_permissions(info, side, pending_type, sl, tp):
    trade_mode = int(getattr(info, 'trade_mode', 4))
    if trade_mode not in (1, 2, 4) or (trade_mode == 1 and side != 'BUY') or (trade_mode == 2 and side != 'SELL'):
        raise ValueError('Broker bu sembolde yeni emrin yönüne izin vermiyor.')
    modes = int(getattr(info, 'order_mode', 127))
    required = (2 if pending_type.endswith('LIMIT') else 4) if pending_type else 1
    if not modes & required or (sl and not modes & 16) or (tp and not modes & 32):
        raise ValueError('Broker bu emir türünü veya SL/TP korumasını desteklemiyor.')


def _directional_volume(info, symbol, side, volume, current, pending):
    limit = float(getattr(info, 'volume_limit', 0))
    if not math.isfinite(limit) or limit < 0:
        raise ValueError('Broker yönsel lot sınırı doğrulanamadı.')
    if not limit:
        return
    buy = side == 'BUY'
    committed = sum(float(p.volume) for p in current
                    if str(p.symbol) == symbol and (int(p.type) == 0) == buy)
    committed += sum(float(o.volume_current) for o in pending
                     if str(getattr(o, 'symbol', '')) == symbol
                     and int(getattr(o, 'type', -1)) in ((0, 2, 4, 6) if buy else (1, 3, 5, 7)))
    if committed + volume > limit + 1e-9:
        raise ValueError('Emir brokerın aynı yöndeki toplam lot sınırını aşıyor.')


def _fillings(info):
    execution = int(info.trade_exemode)
    if execution in (0, 1):  # Request and Instant ignore SYMBOL_FILLING_MODE.
        return [0, 1, 2]
    if execution not in (2, 3):
        return []
    # Prefer a full fill; IOC can leave partial exposure and RETURN a live remainder.
    fillings = [f for bit, f in ((1, 0), (2, 1)) if int(info.filling_mode) & bit]
    if execution == 3:
        fillings.append(2)
    return fillings


def _volume_valid(info, volume):
    step = float(info.volume_step)
    return math.isfinite(volume) and math.isfinite(step) and step > 0 and info.volume_min <= volume <= info.volume_max and math.isclose(volume / step, round(volume / step), rel_tol=0, abs_tol=1e-7)


def _price_valid(info, price):
    step = float(getattr(info, 'trade_tick_size', 0) or info.point)
    return math.isfinite(step) and step > 0 and math.isfinite(price) and price >= 0 and (not price or math.isclose(price / step, round(price / step), rel_tol=0, abs_tol=1e-6))


def _checked_send(mt5, request, account_scope, allow_no_change=False, tick_offset=0, max_tick_age=10,
                  before_send=None):
    """Validate the actual request, then send once. Check failures never imply a send."""
    try:
        account = _account(mt5, *account_scope)
        terminal = mt5.terminal_info()
        if (terminal is None or getattr(terminal, 'connected', None) is not True
                or getattr(terminal, 'trade_allowed', True) is False
                or getattr(terminal, 'tradeapi_disabled', False) is True
                or getattr(account, 'trade_allowed', True) is False
                or getattr(account, 'trade_expert', True) is False):
            raise ValueError('Broker veya terminal otomatik işlem izni doğrulanamadı; işlem gönderilmedi.')
        check = getattr(mt5, 'order_check', None)
        if not callable(check):
            raise ValueError('Broker emir ön kontrolü kullanılamıyor; işlem gönderilmedi.')
        checked = check(request)
        code = getattr(checked, 'retcode', None)
        if type(code) is not int:
            raise ValueError('Broker emir ön kontrolü geçerli yanıt vermedi; işlem gönderilmedi.')
        if code != 0:
            success = allow_no_change and code == 10025
            return {'success': success, 'retcode': code, 'uncertain': False, 'preflight': True,
                    'reason_code': 'broker_preflight_rejected' if not success else 'no_change',
                    'error': '' if success else 'Broker emir ön kontrolü reddetti: ' + str(getattr(checked, 'comment', code))}
        # Account/quote may change while the broker calculates the check result.
        _account(mt5, *account_scope)
        if request.get('action') in (1, 5, 6):
            message = _quote_error(mt5.symbol_info_tick(request['symbol']), tick_offset, max_tick_age, mt5)
            if message:
                raise ValueError(message)
        if before_send is not None:
            before_send()
        _account(mt5, *account_scope)
    except Exception as exc:
        return {'success': False, 'uncertain': False, 'preflight': True,
                'reason_code': 'broker_preflight_failed', 'error': str(exc)}
    return {**_send_once(mt5, request, allow_no_change=allow_no_change),
            'preflight_retcode': 0, 'preflight_comment': str(getattr(checked, 'comment', ''))[:200]}


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


def close_deal(mt5, ticket, expected_magic=None, expected_login=0, expected_server='', tick_offset=0):
    """Close one fresh position through the same checked execution path as opens."""
    try:
        account = _account(mt5, expected_login, expected_server)
        scope = (int(account.login), str(account.server))
        rows = mt5.positions_get(ticket=ticket)
        if rows is None:
            raise RuntimeError('Pozisyon okunamadı; kapatma gönderilmedi.')
        if len(rows) != 1 or int(rows[0].ticket) != ticket:
            raise ValueError('Pozisyon artık açık değil; listeyi yenileyin.')
        p = rows[0]
        if expected_magic is not None and (int(p.magic) != int(expected_magic) or int(account.margin_mode) != 2):
            raise ValueError('Pozisyon stratejiye ait değil veya hedging hesabı doğrulanamadı; kapatma gönderilmedi.')
        expected_state = _position_state(p)
        info, tick = _market(mt5, str(p.symbol), tick_offset)
        volume = float(p.volume)
        if not _volume_valid(info, volume) or int(p.type) not in (0, 1):
            raise ValueError('Pozisyon hacmi veya yönü doğrulanamadı; kapatma gönderilmedi.')
        exit_price = float(tick.bid if int(p.type) == 0 else tick.ask)
        request = dict(action=1, position=ticket, symbol=str(p.symbol), volume=volume,
                       type=1 if int(p.type) == 0 else 0, price=exit_price, deviation=50,
                       magic=int(p.magic), comment='Panel close', type_time=0)
        execution = dict(kind='close', requested_price=exit_price,
                         bid=float(tick.bid), ask=float(tick.ask), point=float(info.point),
                         quote_time=int(tick.time)-int(tick_offset), deviation_points=50)
        fillings = _fillings(info)
        if not fillings:
            raise ValueError('Desteklenen doldurma yöntemi yok; kapatma gönderilmedi.')
    except Exception as exc:
        return {'success': False, 'uncertain': False, 'error': str(exc)}
    for filling in fillings:
        request['type_filling'] = filling
        response = _checked_send(mt5, request, scope, tick_offset=tick_offset,
                                 before_send=lambda: _check_position_state(mt5, ticket, expected_state))
        if response.get('retcode') != 10030:
            break
    if response.get('partial') or response.get('pending'):
        response['error'] = ('Pozisyon kısmen kapandı; kalan hacmi kontrol edin.' if response.get('partial')
                             else 'Kapatma emri kabul edildi; pozisyonun kapanması henüz doğrulanmadı.')
    response['success'] = bool(response.get('success') and response.get('retcode') == 10009)
    return {**response, 'ticket': ticket, 'position': ticket, 'order_ticket': response.get('ticket'),
            'requested_volume': volume, 'execution': execution}


def manage_position(mt5, ticket, operation, values, expected_login, expected_server, tick_offset=0,
                    max_trade_risk_pct=2.0, ai_max_trade_risk_pct=1.0):
    try:
        account = _account(mt5, expected_login, expected_server)
        scope = (int(account.login), str(account.server))
        rows = mt5.positions_get(ticket=ticket)
        if rows is None:
            raise RuntimeError('Pozisyon okunamadı.')
        if len(rows) != 1 or int(rows[0].ticket) != ticket:
            raise ValueError('Pozisyon artık açık değil; listeyi yenileyin.')
        p = rows[0]
        expected_state = _position_state(p)
        info, tick = _market(mt5, str(p.symbol), tick_offset)
        buy = int(p.type) == 0
        exit_price = float(tick.bid if buy else tick.ask)
        trade_risk = {}
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
            if ((sl and not _stop_distance_valid(info, exit_price-sl if buy else sl-exit_price, freeze=True))
                    or (tp and not _stop_distance_valid(info, tp-exit_price if buy else exit_price-tp, freeze=True))):
                raise ValueError('SL/TP yönü veya broker stop/dondurma mesafesi uygun değil.')
            old_sl = float(p.sl)
            widens_stop = sl and (not old_sl or (sl < old_sl if buy else sl > old_sl))
            if (int(account.trade_mode) == 2 and not sl) or widens_stop:
                # Price movement is already in equity. Bound the extra loss from the
                # current liquidation quote to the new stop, not from original entry.
                trade_risk = _trade_risk_guard(mt5, account, str(p.symbol), 'BUY' if buy else 'SELL',
                                             float(p.volume), exit_price, sl, max_trade_risk_pct,
                                             ai_max_trade_risk_pct if int(p.magic) == AI_MAGIC else None)
                if not trade_risk['success']:
                    return trade_risk
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
    guard = lambda: _check_position_state(mt5, ticket, expected_state)
    if operation == 'stops':
        result = _checked_send(mt5, request, scope, allow_no_change=True,
                               tick_offset=tick_offset, before_send=guard)
    else:
        fillings = _fillings(info)
        if not fillings:
            return {'success': False, 'error': 'Desteklenen doldurma yöntemi yok.'}
        for filling in fillings:
            request['type_filling'] = filling
            result = _checked_send(mt5, request, scope, tick_offset=tick_offset, before_send=guard)
            if result.get('retcode') != 10030:
                break
    return {**trade_risk, **result, 'position': ticket, 'operation': operation}


def cancel_pending(mt5, ticket, expected_login, expected_server):
    try:
        account = _account(mt5, expected_login, expected_server)
        scope = (int(account.login), str(account.server))
        _check_pending_ticket(mt5, ticket)
    except Exception as exc:
        return {'success': False, 'error': str(exc)}
    return {**_checked_send(mt5, dict(action=8, order=ticket), scope,
                           before_send=lambda: _check_pending_ticket(mt5, ticket)), 'order_ticket': ticket}


def _position_state(position):
    return {k: getattr(position, k) for k in ('ticket', 'symbol', 'type', 'magic', 'volume', 'sl', 'tp')}


def _check_position_state(mt5, ticket, expected):
    rows = mt5.positions_get(ticket=ticket)
    if rows is None or len(rows) != 1 or _position_state(rows[0]) != expected:
        raise ValueError('Pozisyon kontrol sırasında değişti veya kapandı; listeyi yenileyin. İşlem gönderilmedi.')


def _check_pending_ticket(mt5, ticket):
    orders = mt5.orders_get(ticket=ticket)
    if orders is None:
        raise RuntimeError('Bekleyen emir doğrulanamadı.')
    if len(orders) != 1 or int(orders[0].ticket) != ticket:
        raise ValueError('Emir artık beklemiyor; gerçekleşmiş olabilir. Pozisyonları kontrol edin.')


def live_snapshot(mt5, symbols, tick_offset=0):
    account = _account(mt5)
    fields = ('login','trade_mode','balance','equity','profit','margin','margin_free','margin_level','currency','server','leverage')
    account_data = {k:getattr(account, k) for k in fields}
    account_data['connected'] = broker_connection(mt5) is True
    account_data['server_time'] = time.strftime('%H:%M:%S')
    ticks = {symbol: price(mt5, symbol, tick_offset) for symbol in symbols}
    open_positions = positions(mt5, include_commission=True, tick_offset=tick_offset, quotes=ticks)
    if account_identity(mt5) != [int(account.login), str(account.server)]:
        raise RuntimeError('Canlı pozisyonlar okunurken aktif hesap değişti.')
    return dict(account=account_data, positions=open_positions, orders=pending_orders(mt5),
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


def position_details(mt5, position_id, account_scope):
    account = _account(mt5, *account_scope)
    rows = mt5.history_deals_get(position=int(position_id))
    if rows is None:
        raise RuntimeError('Pozisyon detayları okunamadı.')
    # Recheck identity after the broker read, before exposing account-specific data.
    _account(mt5, *account_scope)
    deals = []
    for d in sorted(rows, key=lambda d: (int(getattr(d, 'time_msc', d.time * 1000)), int(d.ticket))):
        if int(d.position_id) != int(position_id):
            raise RuntimeError('Pozisyon geçmişi eşleşmiyor.')
        row = _deal_row(d)
        row.update(order=int(d.order), price=float(d.price), magic=int(getattr(d, 'magic', 0)),
                   comment=str(getattr(d, 'comment', '')),
                   time_text=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(d.time)))
        deals.append(row)
    symbol = next((d['symbol'] for d in deals if d['symbol']), '')
    info = mt5.symbol_info(symbol) if symbol else None
    return dict(position_id=int(position_id), symbol=symbol,
                digits=int(info.digits) if info else 5,
                currency=str(account.currency), deals=deals)


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
