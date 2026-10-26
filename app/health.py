"""System health: read-only findings about the app, the broker link and the bot (never changes a setting or sends an order).

`evaluate(ctx)` is pure: it takes a snapshot dict and returns findings
`{id, level: 'error'|'warn'|'info', title, detail, hint, target}`. `target` names where the user can fix it
('bot-settings', 'risk', 'connection', 'positions').
"""
import math

BOT_STALL_SECONDS = 15 * 60
LIMIT_NEAR = 0.95
MARKET_QUIET_SECONDS = 10 * 60


def _f(id_, level, title, detail='', hint='', target=''):
    return {'id': id_, 'level': level, 'title': title, 'detail': detail, 'hint': hint, 'target': target}


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def evaluate(ctx):
    out = []
    bot = ctx.get('bot') or {}
    now = ctx.get('now') or 0
    real = ctx.get('account_type') == 'REAL'

    if not ctx.get('connected'):
        out.append(_f('mt5_down', 'error', 'MT5 bağlantısı yok', 'Broker bağlantısı doğrulanamadı; fiyat, emir ve bot çalışmaz.',
                      'Hesap > Hesap bağlantısı bölümünden yeniden bağlanın.', 'connection'))
    if ctx.get('trading_halted'):
        out.append(_f('trading_halted', 'error', 'Yeni emirler kilitli',
                      'Günlük zarar sınırı ya da güvenlik kilidi nedeniyle yeni emir açılamaz (bot dahil).',
                      'Üst çubuktaki İşlem Riski bölümünden durumu kontrol edin.', 'risk'))

    quote = ctx.get('quote') or {}
    state, age = quote.get('state'), _num(quote.get('age_seconds'))
    if ctx.get('connected') and state in ('missing', 'invalid'):
        out.append(_f('quote_bad', 'error', f"{quote.get('symbol', 'Sembol')}: geçerli fiyat yok",
                      'Broker bu sembol için fiyat bildirmiyor.', 'Sembolü Piyasa Gözlemi’nde etkinleştirin ya da başka sembol seçin.', 'connection'))
    elif ctx.get('connected') and state == 'stale' and age is not None and age > MARKET_QUIET_SECONDS:
        out.append(_f('quote_stale', 'warn', f"{quote.get('symbol', 'Sembol')}: {int(age // 60)} dk fiyat gelmiyor",
                      'Piyasa kapalı olabilir; bot yeni mum oluşunca çalışır.', 'Hafta sonu/tatilse normaldir.', ''))

    running = bool(bot.get('is_running'))
    runtime = bot.get('runtime_state')
    if bot and not running and bot.get('last_error'):
        out.append(_f('bot_error', 'error', 'Bot hata ile durdu', str(bot['last_error'])[:300], 'Bot günlüğüne bakın; sebebi giderip yeniden başlatın.', 'bot-settings'))
    if running and runtime == 'ORDER_UNRESOLVED':
        out.append(_f('order_unresolved', 'error', 'Bot emri belirsiz', str(bot.get('runtime_detail') or '')[:300],
                      'MT5’te pozisyonları ve geçmişi kontrol edin; sonra botu yeniden başlatın.', 'positions'))
    if runtime in ('PROFIT_TARGET', 'LOSS_LIMIT'):
        out.append(_f('session_target', 'warn', 'Bot kâr hedefi / zarar sınırı nedeniyle durdu', str(bot.get('runtime_detail') or '')[:300],
                      'Yeniden başlatmak için Bot Ayarları’ndaki hedefi gözden geçirin.', 'bot-settings'))

    if running:
        rows = bot.get('symbol_status') or {}
        primary_key = bot.get('symbol')
        for key, row in rows.items():
            updated = _num(row.get('updated'))
            if key == primary_key and updated and now - updated > BOT_STALL_SECONDS and row.get('state') not in ('WAITING_MARKET',):
                out.append(_f('bot_stalled', 'warn', f'{key}: bot {int((now - updated) // 60)} dk durum güncellemedi',
                              'Bot çalışıyor görünüyor ama döngü yanıt vermiyor olabilir.', 'MT5 bağlantısını ve bot günlüğünü kontrol edin.', 'bot-settings'))
            if row.get('state') == 'SKIPPED':
                out.append(_f(f'skipped:{key}', 'info', f'{key}: sinyal atlandı', str(row.get('detail') or '')[:300], '', 'positions'))
            elif row.get('state') == 'REJECTED':
                out.append(_f(f'rejected:{key}', 'warn', f'{key}: emir reddedildi', str(row.get('detail') or '')[:300], '', ''))
        for note in (bot.get('start_warnings') or [])[:5]:
            out.append(_f('start_warning:' + str(abs(hash(note)) % 10**6), 'info', 'Başlangıç uyarısı', str(note)[:300], '', 'risk'))
        if bot.get('copy_manual_trades'):
            copy = bot.get('manual_copy')
            if isinstance(copy, dict) and copy.get('available') is False:
                out.append(_f('copy_unavailable', 'warn', 'Manuel kopya kullanılamıyor', str(copy.get('reason') or '')[:300],
                              'Bot kendi lot/SL/TP ayarını kullanır.', 'bot-settings'))

    limits = ctx.get('limits') or {}
    max_total, max_order, max_count = _num(limits.get('max_total_lots')), _num(limits.get('max_order_lots')), limits.get('max_open_orders')
    positions = ctx.get('positions') or []
    committed = sum(_num(p.get('volume')) or 0 for p in positions)
    if max_total and committed >= LIMIT_NEAR * max_total:
        out.append(_f('lots_near_limit', 'warn', 'Toplam lot sınırına yaklaşıldı',
                      f'Açık {committed:g} lot, sınır {max_total:g} lot. Yeni emirler reddedilebilir.', 'Bot Ayarları > Lot ve risk bölümünden sınırı gözden geçirin.', 'bot-settings'))
    if isinstance(max_count, int) and not isinstance(max_count, bool) and max_count > 0 and len(positions) >= max_count:
        out.append(_f('count_at_limit', 'warn', 'Açık işlem sayısı sınırında', f'{len(positions)} işlem açık, sınır {max_count}.',
                      'Sınırı artırın ya da bir işlemi kapatın.', 'bot-settings'))
    lot = _num(bot.get('lot_size'))
    demo_full = bool(bot.get('auto_lot_limits_demo')) and ctx.get('account_type') == 'DEMO' and bot.get('copy_manual_trades')
    if running and lot and max_order and lot > max_order + 1e-9 and not demo_full:
        out.append(_f('lot_over_cap', 'warn', 'Bot lotu otomatik bot sınırının üstünde', f'Bot lotu {lot:g}, tek emir sınırı {max_order:g}.',
                      'Bot Ayarları’nda lotu azaltın ya da sınırı artırın.', 'bot-settings'))

    if real and bot.get('use_stop_loss') is False:
        out.append(_f('real_no_sl', 'error', 'Gerçek hesapta Stop Loss kapalı', 'Bot ayarlarında SL kapalı; gerçek hesapta bot SL’siz emir açamaz.',
                      'Bot Ayarları’nda Stop Loss’u açın.', 'bot-settings'))
    if running and bot.get('use_stop_loss') is False and not real:
        out.append(_f('demo_no_sl', 'info', 'Botta Stop Loss kapalı', 'Pozisyonlar yalnız TP/ters sinyalle kapanır; SL koruması yok.', '', 'bot-settings'))

    order = {'error': 0, 'warn': 1, 'info': 2}
    out.sort(key=lambda f: order[f['level']])
    return out


def overall(findings):
    levels = {f['level'] for f in findings}
    return 'error' if 'error' in levels else 'warn' if 'warn' in levels else 'ok'
