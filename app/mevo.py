"""MevO — DeepSeek destekli asistan (2026-10-23 yeniden yazıldı, aynı gün genişletildi).

Kullanıcı kararları: MevO orijinal MT5 terminalinden elle açılmış işlemleri (broker geçmişi) inceleyip mantığını öğrenir,
bu mantığa uyan fırsatları SUNAR. Kendisi emir açmaz: fırsatı kullanıcı düzenleyip açar (sayfa) ya da kullanıcı botta
'MevO'yu kullan' seçer (bot MevO fırsatıyla girer; bkz. CLAUDE.md). İşlem saati hiçbir yerde kullanılmaz.

Kurallar
- Öğrenme verisi elle açılan, tamamen kapanmış pozisyonlar ve bunlardan hesaplanan istatistiklerdir; kendi (MevO) işlemleri
  yalnız sonuç analizi/ders için okunur.
- Sayıları kod hesaplar; DeepSeek yalnız bu sayıları yorumlar ve fırsatları yazar. Veri dışı psikoloji,
  başarı garantisi uydurmaz (prompt'ta yasak).
- Fırsat seviyeleri doğrulanır: AL için SL < giriş < TP, SAT için TP < giriş < SL değilse BEKLE'ye çevrilir.
- API anahtarı panelden ya da ortam değişkeninden (`DEEPSEEK_API_KEY`) okunur; koda/depoya yazılmaz.
"""
import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

import requests

from app.order_journal import state_path

DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions'
DEEPSEEK_MODELS_URL = 'https://api.deepseek.com/models'
DEFAULT_MODEL = 'deepseek-chat'
MIN_TRADES = 5
LEARN_DAYS = 90
MIN_INTERVAL = 30            # saniye: aynı işlem türü için iki istek arası
SYMBOLS_MAX = 6


def digest_of(value, model=''):
    import hashlib
    return hashlib.sha256(json.dumps([model, value], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _hour(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).hour   # MT5 deal time = broker sunucu saati


PROFILE_VERSION = 2          # 2: işlem saati modele gitmez; eski profiller fırsat istenince yeniden öğrenilir
_HOUR_TEXT = re.compile(r'\b\d{1,2}[:.]\d{2}\b|\bsaat', re.I)


def strip_hours(stats):
    """Kullanıcı kararı: işlem saati hiçbir yerde kullanılmaz; modele saat verisi gitmez."""
    stats['hours'] = {}
    for r in stats.get('recent', []):
        r.pop('open_hour', None)
    return stats


def no_hour_rules(lines):
    """Eski profillerde kalmış saat temelli maddeleri modele göndermez."""
    return [x for x in lines or [] if not _HOUR_TEXT.search(str(x))]


def compute_stats(trades):
    """Elle açılan kapalı pozisyonlardan deterministik özet (yalnız veri, yorum yok)."""
    trades = [t for t in trades if float(t.get('volume') or 0) > 0]
    out = {'count': len(trades), 'symbols': {}, 'hours': {}, 'sides': {}, 'hold_minutes': None, 'recent': []}
    if not trades:
        return out
    wins = [t for t in trades if float(t['net']) > 0]
    out['wins'] = len(wins)
    out['net'] = round(sum(float(t['net']) for t in trades), 2)
    out['win_rate'] = round(len(wins) / len(trades) * 100, 1)
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t['symbol']].append(t)
    for sym, rows in by_sym.items():
        rw = [r for r in rows if float(r['net']) > 0]
        sides = Counter(r['side'] for r in rows)
        sl_pct = [abs(r['open_price'] - r['sl']) / r['open_price'] * 100 for r in rows
                  if float(r.get('sl') or 0) > 0 and r['open_price']]
        tp_pct = [abs(r['open_price'] - r['tp']) / r['open_price'] * 100 for r in rows
                  if float(r.get('tp') or 0) > 0 and r['open_price']]
        out['symbols'][sym] = {
            'count': len(rows), 'wins': len(rw), 'win_rate': round(len(rw) / len(rows) * 100, 1),
            'net': round(sum(float(r['net']) for r in rows), 2),
            'buy': sides.get('BUY', 0), 'sell': sides.get('SELL', 0),
            'median_lot': round(median(float(r['volume']) for r in rows), 4),
            'median_hold_min': round(median((r['close_time'] - r['open_time']) / 60 for r in rows), 1),
            'with_sl': len(sl_pct), 'with_tp': len(tp_pct),
            'median_sl_pct': round(median(sl_pct), 4) if sl_pct else None,
            'median_tp_pct': round(median(tp_pct), 4) if tp_pct else None,
        }
    hours = Counter(_hour(t['open_time']) for t in trades)
    hour_net = defaultdict(float)
    for t in trades:
        hour_net[_hour(t['open_time'])] += float(t['net'])
    out['hours'] = {str(h): {'count': hours[h], 'net': round(hour_net[h], 2)} for h in sorted(hours)}
    sides = Counter(t['side'] for t in trades)
    out['sides'] = dict(sides)
    out['hold_minutes'] = round(median((t['close_time'] - t['open_time']) / 60 for t in trades), 1)
    for t in trades[-8:]:
        out['recent'].append({
            'symbol': t['symbol'], 'side': t['side'], 'lot': round(float(t['volume']), 4),
            'open_hour': _hour(t['open_time']), 'hold_min': round((t['close_time'] - t['open_time']) / 60, 1),
            'net': round(float(t['net']), 2), 'has_sl': float(t.get('sl') or 0) > 0,
            'has_tp': float(t.get('tp') or 0) > 0})
    return out


CAUSE_LABELS = {
    'side': 'Manuel kârlı işlemlerinin baskın yönünün tersine açıldı',
    'early_stop': 'Manuel tutma süresinin çeyreğinden kısa sürede SL/stop ile kapandı',
    'tight_sl': 'SL, manuel kârlı işlemlerin SL mesafesinin yarısından dar',
    'big_lot': 'Lot, manuel kârlı işlem lotunun 2 katından büyük',
    'held_long': 'Zarardaki pozisyon, kârlı işlemlerin tutma süresinin 2 katından uzun tutuldu',
    'no_sl': 'Stop loss (SL) konmamıştı',
    'noise_sl': "SL, kârlı işlemlerin tipik ters hareketinin (MAE) içinde kaldı; piyasa gürültüsüyle stop oldu",
    'wide_spread': 'Girişte spread, volatiliteye göre kârlı işlemlerden belirgin geniş',
    'low_vol': 'Giriş anında volatilite kârlı işlemlerin aralığının altındaydı (durgun piyasa)',
    'high_vol': 'Giriş anında volatilite kârlı işlemlerin aralığının üstündeydi (çok hareketli piyasa)',
    'crowded': 'Aynı anda kârlı işlemlerdeki en yüksek açık işlem sayısından fazla işlem açıktı',
    'giveback': 'Hedefin yarısından fazlasına ulaşıp kârı geri verdi ve zararla kapandı',
    'no_ref': 'Manuelde bu sembolde yeterli kârlı işlem yok (karşılaştırılamadı)',
    'other': 'Belirgin bir fark bulunamadı',
}


def _pctl(values, q):
    vals = sorted(values)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _winner_features(rows):
    """Kârlı işlemlerin bağlam referansı (yalnız broker mumlarından hesaplanmış alanlar varsa)."""
    out = {}
    mae = [r['mae_pct'] for r in rows if r.get('mae_pct') is not None]
    if len(mae) >= 3:
        out['mae_p75'] = round(_pctl(mae, .75), 5)
        out['mae_median'] = round(median(mae), 5)
    ratio = [r['spread_pct'] / r['atr_pct'] for r in rows if r.get('spread_pct') is not None and r.get('atr_pct')]
    if len(ratio) >= 3:
        out['spread_ratio_p90'] = round(_pctl(ratio, .9), 4)
    atr = [r['atr_pct'] for r in rows if r.get('atr_pct')]
    if len(atr) >= 3:
        out['atr_lo'], out['atr_hi'] = round(_pctl(atr, .1), 5), round(_pctl(atr, .9), 5)
    conc = [r['conc_n'] for r in rows if r.get('conc_n') is not None]
    if len(conc) >= 3:
        out['conc_max'] = max(conc)
    return out


def enrich_trades(trades, contexts, universe=None):
    """İşlemlere broker bağlamı (MAE/MFE, spread, aralık) ve eşzamanlı açık işlem sayısı/lotu ekler (kopya döner).
    `universe`: aynı hesabın bilinen tüm kapalı işlemleri (manuel + MevO + bot); eşzamanlılık bunlardan hesaplanır."""
    universe = list(universe if universe is not None else trades)
    out = []
    for t in trades:
        row = dict(t)
        row.update({k: v for k, v in (contexts.get(str(t.get('position_id'))) or {}).items() if k in ('mae_pct', 'mfe_pct', 'spread_pct', 'atr_pct')})
        others = [o for o in universe if o.get('position_id') != t.get('position_id')
                  and o['open_time'] <= t['open_time'] < o['close_time']]
        row['conc_n'] = len(others)
        row['conc_lot'] = round(sum(float(o.get('volume') or 0) for o in others), 4)
        out.append(row)
    return out


def winner_reference(manual):
    """Manuel KÂRLI işlemlerden sembol başına referans (baskın yön, lot, süre, SL mesafesi ve broker bağlamı)."""
    ref = {}
    by = defaultdict(list)
    for t in manual:
        if float(t['net']) > 0 and float(t.get('volume') or 0) > 0:
            by[t['symbol']].append(t)
    for sym, rows in by.items():
        if len(rows) < 3:
            continue
        sides = Counter(r['side'] for r in rows)
        top, n = sides.most_common(1)[0]
        sl = [abs(r['open_price'] - r['sl']) / r['open_price'] * 100 for r in rows
              if float(r.get('sl') or 0) > 0 and r['open_price']]
        feat = _winner_features(rows)
        ref[sym] = {**feat, 'n': len(rows),
                    'side': top if n / len(rows) >= 0.7 else None,
                    'median_lot': median(float(r['volume']) for r in rows),
                    'median_hold_min': median((r['close_time'] - r['open_time']) / 60 for r in rows),
                    'median_sl_pct': median(sl) if sl else None}
    return ref


def context_causes(t, r):
    """Broker bağlamından (MAE/spread/volatilite/eşzamanlılık/kâr geri verme) zarar sebepleri; alan yoksa atlanır."""
    out = []
    price, sl, tp = float(t.get('open_price') or 0), float(t.get('sl') or 0), float(t.get('tp') or 0)
    if sl > 0 and price and r.get('mae_p75') and t.get('close_reason') in (4, 6) \
            and abs(price - sl) / price * 100 < r['mae_p75']:
        out.append('noise_sl')
    if r.get('spread_ratio_p90') and t.get('spread_pct') is not None and t.get('atr_pct') \
            and t['spread_pct'] / t['atr_pct'] > r['spread_ratio_p90'] * 1.25:
        out.append('wide_spread')
    if r.get('atr_lo') and t.get('atr_pct'):
        if t['atr_pct'] < r['atr_lo'] * 0.8:
            out.append('low_vol')
        elif t['atr_pct'] > r['atr_hi'] * 1.25:
            out.append('high_vol')
    if r.get('conc_max') is not None and t.get('conc_n') is not None and t['conc_n'] >= 2 and t['conc_n'] > r['conc_max']:
        out.append('crowded')
    if tp > 0 and price and t.get('mfe_pct') is not None and float(t.get('net') or 0) < 0 \
            and t['mfe_pct'] >= abs(tp - price) / price * 100 * 0.5:
        out.append('giveback')
    return out


def loss_causes(bot_trade, ref):
    """Zarar eden bir bot işleminin manuel kârlı referanstan farkları (yalnız sayısal karşılaştırma)."""
    r = ref.get(bot_trade['symbol'])
    if not r:
        return ['no_ref']
    out = []
    # Saat bilerek karşılaştırılmaz: kullanıcı işlem saatlerinin değiştiğini, botun uyurken de çalışacağını söyledi (2026-10-23).
    if r['side'] and bot_trade['side'] != r['side']:
        out.append('side')
    hold = (bot_trade['close_time'] - bot_trade['open_time']) / 60
    if r['median_hold_min'] and hold < r['median_hold_min'] * 0.25 and bot_trade.get('close_reason') in (4, 6):
        out.append('early_stop')
    if float(bot_trade.get('sl') or 0) > 0 and r['median_sl_pct'] and bot_trade['open_price']:
        if abs(bot_trade['open_price'] - bot_trade['sl']) / bot_trade['open_price'] * 100 < r['median_sl_pct'] * 0.5:
            out.append('tight_sl')
    if r['median_lot'] and float(bot_trade['volume']) > r['median_lot'] * 2:
        out.append('big_lot')
    if r['median_hold_min'] and hold > r['median_hold_min'] * 2:
        out.append('held_long')
    if not float(bot_trade.get('sl') or 0) > 0:
        out.append('no_sl')
    out += context_causes(bot_trade, r)
    return out or ['other']


def _loss_block(trades, ref):
    trades = [t for t in trades if float(t.get('volume') or 0) > 0]
    wins = [t for t in trades if float(t['net']) > 0]
    losses = [t for t in trades if float(t['net']) < 0]
    causes, per_symbol = {}, defaultdict(lambda: defaultdict(lambda: {'count': 0, 'loss': 0.0}))
    for t in losses:
        for key in loss_causes(t, ref):
            for bucket in (causes.setdefault(key, {'count': 0, 'loss': 0.0}), per_symbol[t['symbol']][key]):
                bucket['count'] += 1
                bucket['loss'] = round(bucket['loss'] + float(t['net']), 2)
    return {
        'count': len(trades), 'wins': len(wins), 'losses': len(losses),
        'net': round(sum(float(t['net']) for t in trades), 2),
        'loss_total': round(sum(float(t['net']) for t in losses), 2),
        'causes': [{'key': k, 'label': CAUSE_LABELS[k], **v} for k, v in sorted(causes.items(), key=lambda kv: kv[1]['loss'])],
        'by_symbol': {s: {k: dict(v) for k, v in d.items()} for s, d in per_symbol.items()},
    }


def compare_bot_with_manual(manual, bot_trades):
    """Zararların sebep analizi: hem manuel ZARARLAR hem bot zararları, manuel KÂRLI işlemlerin referansıyla
    karşılaştırılır (yön, lot, tutma süresi, SL ve broker bağlamı)."""
    ref = winner_reference(manual)
    bot_block, manual_block = _loss_block(bot_trades, ref), _loss_block(manual, ref)
    return {
        'bot_count': bot_block['count'], 'bot_wins': bot_block['wins'], 'bot_losses': bot_block['losses'],
        'bot_net': bot_block['net'], 'bot_loss_total': bot_block['loss_total'], 'causes': bot_block['causes'],
        'manual': {k: manual_block[k] for k in ('count', 'wins', 'losses', 'net', 'loss_total', 'causes')},
        'by_symbol': {s: bot_block['by_symbol'].get(s, {}) for s in bot_block['by_symbol']},
        'manual_by_symbol': manual_block['by_symbol'],
        'reference': {s: {**r, 'median_lot': round(r['median_lot'], 4),
                          'median_hold_min': round(r['median_hold_min'], 1),
                          'median_sl_pct': round(r['median_sl_pct'], 4) if r['median_sl_pct'] else None}
                      for s, r in ref.items()},
    }


CLOSE_REASONS = {0: 'elle kapatıldı (masaüstü)', 1: 'elle kapatıldı (mobil)', 2: 'elle kapatıldı (web)',
                 3: 'uzman/otomatik kapandı', 4: 'SL tetiklendi', 5: 'TP\'ye ulaştı', 6: 'stop out'}
STREAK_BLOCK = 3             # aynı sembol + yönde art arda bu kadar MevO zararı = yeni giriş uyarısı/engeli
POOR_WINDOW, POOR_MAX_WIN_RATE = 5, 0.3


def explain_trade(t, ref):
    """MevO'nun açtırdığı bir kapalı işlemin kâr/zarar sebepleri (yalnız koddan, kanıtlanabilir karşılaştırmalar).
    Zarar: manuel kârlı referanstan farklar + nasıl kapandığı. Kâr: referansla uyum + nasıl kapandığı."""
    net = float(t['net'])
    reason = CLOSE_REASONS.get(int(t.get('close_reason', -1)), 'kapanış nedeni bilinmiyor')
    why = [reason[0].upper() + reason[1:]]
    r = ref.get(t['symbol'])
    if net < 0:
        why += [CAUSE_LABELS[k] for k in loss_causes(t, ref) if k != 'other']
        if not r:
            pass
        elif len(why) == 1:
            why.append(CAUSE_LABELS['other'])
    elif net > 0:
        if r:
            agree = []
            if r['side'] and t['side'] == r['side']:
                agree.append('yön manuel kârlı işlemlerinle uyumlu')
            why += agree or ['Manuel referansla belirgin uyum yok (şans payı olabilir)']
        else:
            why.append('Karşılaştırılacak manuel kârlı referans yok')
    sl, tp = float(t.get('sl') or 0), float(t.get('tp') or 0)
    rr = round(abs(tp - t['open_price']) / abs(t['open_price'] - sl), 2) if sl > 0 and tp > 0 and sl != t['open_price'] else None
    return {'position_id': t['position_id'], 'symbol': t['symbol'], 'side': t['side'], 'net': round(net, 2),
            'volume': t['volume'], 'open_time': t['open_time'], 'close_time': t['close_time'],
            'hold_min': round((t['close_time'] - t['open_time']) / 60, 1), 'rr': rr, 'why': why,
            **{k: t[k] for k in ('mae_pct', 'mfe_pct', 'conc_n') if t.get(k) is not None}}


def own_blocks(trades):
    """MevO'nun kendi geçmiş sonuçlarından önlem: aynı sembol+yönde art arda zarar ya da sembolde kötü son sonuçlar."""
    rows = [t for t in trades if float(t.get('volume') or 0) > 0]
    rows.sort(key=lambda t: t['close_time'])
    blocks = []
    by_side = defaultdict(list)
    by_sym = defaultdict(list)
    for t in rows:
        by_side[(t['symbol'], t['side'])].append(t)
        by_sym[t['symbol']].append(t)
    for (sym, side), g in by_side.items():
        tail = g[-STREAK_BLOCK:]
        if len(tail) == STREAK_BLOCK and all(float(t['net']) < 0 for t in tail):
            loss = round(sum(float(t['net']) for t in tail), 2)
            blocks.append({'symbol': sym, 'side': side, 'loss': loss,
                           'text': f"MevO'nun {sym} {'AL' if side == 'BUY' else 'SAT'} işlemlerinin son {STREAK_BLOCK}'ü art arda zararla kapandı "
                                   f"(toplam {loss}); bu yönde yeni girişte zarar edildi."})
    for sym, g in by_sym.items():
        tail = g[-POOR_WINDOW:]
        if len(tail) == POOR_WINDOW and sum(float(t['net']) > 0 for t in tail) / POOR_WINDOW < POOR_MAX_WIN_RATE \
                and sum(float(t['net']) for t in tail) < 0:
            loss = round(sum(float(t['net']) for t in tail), 2)
            blocks.append({'symbol': sym, 'side': None, 'loss': loss,
                           'text': f"MevO'nun {sym} son {POOR_WINDOW} işleminin kazanç oranı %{round(sum(float(t['net']) > 0 for t in tail) / POOR_WINDOW * 100)}"
                                   f" (toplam {loss}); bu sembolde zarar edildi."})
    return blocks


LESSON_ACTIONS = {
    'noise_sl': "SL'yi kârlı işlemlerin tipik ters hareketinden (MAE) daha uzağa koy; MevO bu sınırın altındaki SL'yi otomatik genişletir.",
    'wide_spread': 'Spread, volatiliteye göre yüksekken girme.',
    'low_vol': 'Piyasa durgunken (kârlı işlemlerin aralığının altında) girme.',
    'high_vol': 'Aşırı hareketli piyasada (kârlı işlemlerin aralığının üstünde) girme.',
    'crowded': 'Aynı anda açık işlem sayısını kârlı işlemlerdeki en yükseği geçirme.',
    'giveback': 'Hedefin yarısına gelince kârı korumayı düşün (TP\'yi yakınlaştır ya da elle kâr al).',
    'side': 'Kârlı işlemlerinin baskın yönünün tersine girme.',
    'tight_sl': "SL'yi kârlı işlemlerin SL mesafesinin yarısından dar tutma.",
    'big_lot': 'Lotu kârlı işlem lotunun 2 katının üstüne çıkarma.',
    'held_long': 'Zarardaki pozisyonu kârlı işlemlerin süresinin 2 katından uzun tutma.',
    'early_stop': 'Çok kısa sürede stop oluyorsa SL/giriş zamanlamasını gözden geçir.',
    'no_sl': 'Her işlemde SL kullan.',
}
LESSON_MIN = 2           # bir sebep en az bu kadar zararda görülmeden ders sayılmaz


def build_lessons(causes):
    """Zarar sebeplerinden kalıcı dersler (kod): sebep + kaç kez + toplam zarar + önlem."""
    out = []
    for c in causes:
        if c['count'] >= LESSON_MIN and c['key'] in LESSON_ACTIONS:
            out.append({'key': c['key'], 'count': c['count'], 'loss': c['loss'],
                        'text': f"{c['label']} ({c['count']} işlem, {c['loss']}). Önlem: {LESSON_ACTIONS[c['key']]}"})
    return out


def build_guards(ref):
    """Sembol başına sayısal koruma sınırları (kârlı işlemlerin bağlamından)."""
    g = {}
    for sym, r in (ref or {}).items():
        row = {}
        if r.get('mae_p75'):
            row['min_sl_pct'] = round(r['mae_p75'] * 1.1, 5)
        if r.get('spread_ratio_p90'):
            row['max_spread_ratio'] = round(r['spread_ratio_p90'] * 1.25, 4)
        if r.get('atr_lo'):
            row['atr_pct_range'] = [round(r['atr_lo'] * 0.8, 5), round(r['atr_hi'] * 1.25, 5)]
        if r.get('conc_max') is not None:
            row['max_open'] = max(int(r['conc_max']) + 1, 2)
        if row:
            g[sym] = row
    return g


def adaptive_policy(rows, base=65):
    """MevO kendi sonuçlarından güven eşiğini ayarlar: kötü gidişte yükselir, düzelince taban değere döner."""
    rows = sorted(rows, key=lambda t: t['close_time'])
    last5, last10 = rows[-5:], rows[-10:]
    net = lambda group: sum(float(t['net']) for t in group)
    if len(last5) == 5 and sum(float(t['net']) > 0 for t in last5) / 5 < 0.4 and net(last5) < 0:
        return {'min_confidence': min(85, base + 10), 'reason': 'Son 5 MevO işleminin kazanç oranı %40\'ın altında ve net zarar: eşik yükseltildi.'}
    if len(last10) >= 6 and net(last10) < 0:
        return {'min_confidence': min(85, base + 5), 'reason': 'Son MevO işlemleri net zararda: eşik biraz yükseltildi.'}
    return {'min_confidence': base, 'reason': 'MevO sonuçları eşiği yükseltmeyi gerektirmiyor.'}


def review_trades(trades, ref):
    """MevO sonuç raporu: kazanç/zarar özeti, son işlemler (sebepleriyle), sembol+yön kırılımı, alınan önlemler."""
    rows = [t for t in trades if float(t.get('volume') or 0) > 0]
    rows.sort(key=lambda t: t['close_time'], reverse=True)
    wins = [t for t in rows if float(t['net']) > 0]
    losses = [t for t in rows if float(t['net']) < 0]
    groups = defaultdict(lambda: {'count': 0, 'wins': 0, 'net': 0.0})
    for t in rows:
        g = groups[(t['symbol'], t['side'])]
        g['count'] += 1; g['wins'] += float(t['net']) > 0; g['net'] = round(g['net'] + float(t['net']), 2)
    block = _loss_block(rows, ref)
    capture = []
    for t in rows:
        if float(t['net']) > 0 and t.get('mfe_pct') and t['open_price']:
            moved = abs(t['close_price'] - t['open_price']) / t['open_price'] * 100
            capture.append(min(1.0, moved / t['mfe_pct']))
    return {'count': len(rows), 'wins': len(wins), 'losses': len(losses),
            'avg_capture': round(sum(capture) / len(capture) * 100) if capture else None,
            'lessons': build_lessons(block['causes']), 'guards': build_guards(ref), 'policy': adaptive_policy(rows),
            'win_rate': round(len(wins) / len(rows) * 100, 1) if rows else None,
            'net': round(sum(float(t['net']) for t in rows), 2), 'loss_total': block['loss_total'],
            'causes': block['causes'],
            'groups': [{'symbol': k[0], 'side': k[1], **v} for k, v in sorted(groups.items(), key=lambda kv: kv[1]['net'])],
            'trades': [explain_trade(t, ref) for t in rows[:20]],
            'blocks': own_blocks(rows)}


def trade_warnings(item, comparison, broker_hour, entry_sl_pct, own=None, live=None):
    """Fırsat için işlem uyarıları: tamamen koddur, token harcamaz. Manuel kârlı referanstan sapmaları ve
    botun geçmişte bu sebeplerle zarar edip etmediğini söyler."""
    if item.get('action') not in ('BUY', 'SELL'):
        return []
    own = [b['text'] for b in (own or []) if b['symbol'] == item['symbol'] and b['side'] in (None, item['action'])]
    if not comparison:
        return own
    ref = comparison['reference'].get(item['symbol'])
    seen = {}
    for source, label in ((comparison.get('manual_by_symbol', {}), 'manuel'), (comparison['by_symbol'], 'bot')):
        for key, c in source.get(item['symbol'], {}).items():
            row = seen.setdefault(key, {'count': 0, 'loss': 0.0, 'who': []})
            row['count'] += c['count']; row['loss'] = round(row['loss'] + c['loss'], 2); row['who'].append(label)
    out = list(own)

    def lost(key, need=1):
        c = seen.get(key)
        if c and c['count'] >= need:
            return f" Geçmişte ({'+'.join(c['who'])}) bu sebeple {c['count']} kez toplam {c['loss']} zarar edildi."
        return f" (Geçmişte bu sebeple {c['count']} zarar var.)" if c else ''
    if not ref:
        return own + [f"{item['symbol']} için manuel kârlı işlem referansı yetersiz; karşılaştırma yapılamadı."]
    if ref['side'] and item['action'] != ref['side']:
        out.append(f"Manuel kârlı işlemlerin çoğu {ref['side']}; bu fırsat tersi." + lost('side'))
    if entry_sl_pct and ref['median_sl_pct'] and entry_sl_pct < ref['median_sl_pct'] * 0.5:
        out.append(f"Önerilen SL (%{entry_sl_pct:.3f}) manuel kârlı SL'nin (%{ref['median_sl_pct']}) yarısından dar." + lost('tight_sl'))
    live = live or {}
    if ref.get('mae_p75') and entry_sl_pct and entry_sl_pct < ref['mae_p75']:
        out.append(f"SL (%{entry_sl_pct:.3f}) kârlı işlemlerin tipik ters hareketinden (%{ref['mae_p75']}) dar; gürültüyle stop olabilir." + lost('noise_sl', 2))
    if ref.get('spread_ratio_p90') and live.get('spread_pct') is not None and live.get('atr_pct') \
            and live['spread_pct'] / live['atr_pct'] > ref['spread_ratio_p90'] * 1.25:
        out.append('Spread, volatiliteye göre kârlı işlemlerdeki girişlerden geniş.' + lost('wide_spread', 2))
    if ref.get('atr_lo') and live.get('atr_pct'):
        if live['atr_pct'] < ref['atr_lo'] * 0.8:
            out.append('Piyasa kârlı işlemlerin açıldığı aralıktan daha durgun.' + lost('low_vol', 2))
        elif live['atr_pct'] > ref['atr_hi'] * 1.25:
            out.append('Piyasa kârlı işlemlerin açıldığı aralıktan çok daha hareketli.' + lost('high_vol', 2))
    if ref.get('conc_max') is not None and live.get('conc_n') is not None and live['conc_n'] >= 2 and live['conc_n'] > ref['conc_max']:
        out.append(f"Şu an {live['conc_n']} açık işlem var; kârlı işlemlerde en çok {ref['conc_max']} idi." + lost('crowded', 2))
    return out


LOSS_MARK = 'zarar edildi'     # trade_warnings: geçmiş zarara dayanan uyarılar bu ifadeyi içerir


def blocking_warnings(warnings):
    """Botun girişi engelleyen uyarıları: yalnız geçmişte gerçekten zarar edilmiş durumlar (salt sapma uyarıları engellemez)."""
    return [w for w in warnings or [] if LOSS_MARK in w]


def validate_opportunity(item, tick):
    """Model çıktısını güvenli forma çevirir; mantıksız seviye varsa BEKLE olur (emir açılmaz zaten)."""
    action = str(item.get('action', 'WAIT')).upper()
    action = {'AL': 'BUY', 'SAT': 'SELL', 'BEKLE': 'WAIT'}.get(action, action)
    res = {'symbol': str(item.get('symbol', '')), 'action': 'WAIT',
           'confidence': 0, 'reason': str(item.get('reason', ''))[:400],
           'matched_rule': str(item.get('matched_rule', ''))[:200]}
    try:
        conf = max(0, min(100, int(float(item.get('confidence', 0)))))
    except (TypeError, ValueError):
        conf = 0
    res['confidence'] = conf
    if action not in ('BUY', 'SELL'):
        return res
    try:
        entry = float(tick['ask'] if action == 'BUY' else tick['bid'])
        sl, tp = float(item.get('sl')), float(item.get('tp'))
    except (TypeError, ValueError, KeyError):
        res['reason'] = (res['reason'] + ' (SL/TP okunamadı; fırsat sunulmadı)').strip()
        return res
    ok = all(math.isfinite(v) and v > 0 for v in (entry, sl, tp))
    ok = ok and ((sl < entry < tp) if action == 'BUY' else (tp < entry < sl))
    if not ok:
        res['reason'] = (res['reason'] + ' (SL/TP yönle uyumsuz; fırsat sunulmadı)').strip()
        return res
    res.update(action=action, entry=entry, sl=sl, tp=tp,
               rr=round(abs(tp - entry) / abs(entry - sl), 2))
    return res


def apply_sl_guard(item, guard):
    """SL, kârlı işlemlerin tipik ters hareketinden (MAE) dar ise kod SL'yi bu sınıra genişletir (TP aynı kalır).
    Bu yalnız MevO'nun önerisini değiştirir; emir yine kullanıcı onayıyla ya da bot kurallarıyla açılır."""
    if item.get('action') not in ('BUY', 'SELL') or not guard or not guard.get('min_sl_pct') or not item.get('entry'):
        return item
    entry, sl = item['entry'], item['sl']
    dist = abs(entry - sl)
    need = entry * guard['min_sl_pct'] / 100
    if dist >= need:
        return item
    new_sl = entry - need if item['action'] == 'BUY' else entry + need
    item['sl_original'] = sl
    item['sl'] = round(new_sl, 8)
    item['rr'] = round(abs(item['tp'] - entry) / need, 2)
    item['adjusted'] = f"SL, kârlı işlemlerin tipik ters hareketine göre genişletildi (%{guard['min_sl_pct']})."
    return item


class MevO:
    def __init__(self, mt5_client, api_key=None, path=None):
        self.mt5 = mt5_client
        self._explicit_key = api_key
        self._key_path = state_path('mevo_key.json') if path is None else f'{path}.key'
        self.bot_position_ids = None      # main.py: bot MevO girişlerinin pozisyon numaraları
        self.model = os.getenv('DEEPSEEK_MODEL', DEFAULT_MODEL)
        self.daily_call_limit = max(1, int(os.getenv('MEVO_DAILY_CALL_LIMIT', '100')))
        self._path = path or state_path('mevo.json')
        self._lock = threading.RLock()
        self._busy = threading.Lock()
        self._last = {}
        self.state = self._load()

    # ---- API anahtarı: önce panelden kaydedilen, yoksa DEEPSEEK_API_KEY ortam değişkeni ----
    def _stored_key(self):
        try:
            return str(json.loads(open(self._key_path, encoding='utf-8').read()).get('api_key') or '')
        except Exception:
            return ''

    @property
    def api_key(self):
        if self._explicit_key is not None:
            return self._explicit_key
        return self._stored_key() or os.getenv('DEEPSEEK_API_KEY', '')

    @api_key.setter
    def api_key(self, value):
        self._explicit_key = value

    def key_source(self):
        return 'explicit' if self._explicit_key else 'panel' if self._stored_key() else 'env' if os.getenv('DEEPSEEK_API_KEY') else None

    def set_key(self, key):
        """Ücretsiz GET /models ile doğrular (token harcamaz); geçerliyse 0600 dosyaya yazar. Anahtar asla geri dönmez."""
        key = str(key or '').strip()
        if not (8 <= len(key) <= 200) or any(c.isspace() for c in key):
            return {'success': False, 'error': 'Anahtar biçimi geçersiz.'}
        try:
            r = requests.get(DEEPSEEK_MODELS_URL, headers={'Authorization': f'Bearer {key}'}, timeout=15)
        except Exception as exc:
            return {'success': False, 'error': f'DeepSeek\'e ulaşılamadı: {exc}'}
        if r.status_code in (401, 403):
            return {'success': False, 'error': 'DeepSeek bu anahtarı reddetti (401). Anahtarı kontrol et.'}
        if r.status_code >= 400:
            return {'success': False, 'error': f'DeepSeek doğrulaması başarısız ({r.status_code}).'}
        tmp = f'{self._key_path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'api_key': key}, f)
        os.replace(tmp, self._key_path)
        self._explicit_key = None
        return {'success': True}

    # ---- kalıcı durum (hesap bazlı; başka hesabın profili kullanılmaz) ----
    def _load(self):
        try:
            return json.loads(open(self._path, encoding='utf-8').read())
        except Exception:
            return {}

    def _save(self):
        tmp = f'{self._path}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False)
        os.replace(tmp, self._path)

    def _scope(self):
        return f"{getattr(self.mt5, 'login_id', 0)}@{getattr(self.mt5, 'server', '')}"

    def _acct(self):
        return self.state.setdefault(self._scope(), {'profile': None, 'opportunities': None, 'usage': {}})

    # ---- DeepSeek ----
    def _call(self, system, user, operation, max_tokens=700):
        if not self.api_key:
            raise ValueError('DeepSeek API anahtarı tanımlı değil (DEEPSEEK_API_KEY).')
        with self._lock:
            acct = self._acct()
            day = datetime.now(timezone.utc).date().isoformat()
            usage = acct['usage'].setdefault(day, {'calls': 0, 'tokens': 0})
            if usage['calls'] >= self.daily_call_limit:
                raise ValueError('Günlük MevO istek sınırına ulaşıldı (UTC).')
            if time.time() - self._last.get(operation, 0) < MIN_INTERVAL:
                raise ValueError(f'Aynı işlem için {MIN_INTERVAL} saniye bekleyin.')
            self._last[operation] = time.time()
            usage['calls'] += 1
            acct['usage'] = dict(sorted(acct['usage'].items())[-7:])
            self._save()
        resp = requests.post(
            DEEPSEEK_API_URL, headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
            json={'model': self.model, 'response_format': {'type': 'json_object'}, 'max_tokens': max_tokens,
                  'temperature': 0.2,
                  'messages': [{'role': 'system', 'content': system},
                               {'role': 'user', 'content': json.dumps(user, ensure_ascii=False, separators=(',', ':'))}]},
            timeout=60)
        resp.raise_for_status()
        data = resp.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ValueError('Yanıt uzunluk sınırında kesildi; tekrar deneyin.')
        parsed = json.loads(choice['message']['content'])
        if not isinstance(parsed, dict):
            raise ValueError('Geçerli bir JSON nesnesi dönmedi.')
        with self._lock:
            u = self._acct()['usage'][day]
            u['tokens'] += max(0, int((data.get('usage') or {}).get('total_tokens', 0)))
            self._save()
        return parsed

    # ---- öğrenme ----
    LEARN_SYSTEM = (
        'MT5 elle işlem istatistiklerinden kullanıcının mantığını çıkar. Yalnız veriye dayan; psikoloji/garanti '
        'uydurma; işlem saati/gün kuralı YAZMA (kullanıcı saatlerinin değiştiğini söyledi). '
        'uydurma; örnek azsa belirt. Çok kısa Türkçe JSON (her liste en fazla 4 kısa madde): '
        '{"summary":"en fazla 2 cümle","rules":[],"risk_habits":[],"weak_points":[],"caveats":[],"manual_loss_reasons":[],"bot_loss_reasons":[],"lessons":[]}. '
        'mevo_results varsa MevO\'nun kendi işlemlerinin sonuçlarından en fazla 4 somut ders yaz (sebep -> yapılacak/yapılmayacak), sayılara dayan; yoksa lessons boş. '
        'loss_analysis varsa manuel ve bot ZARARLARININ sebebini yalnız bu sayılara dayanarak ilgili listeye yaz '
        '(en fazla 4 madde, sebep + zarar tutarı + hangi durumda); kaçınılacak durumları da belirt; yoksa boş bırak.')

    def learn(self):
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            trades = self._manual_trades(self.mt5.get_manual_trades(days=LEARN_DAYS))
            stats = strip_hours(compute_stats(trades))
            try:
                bot_trades = self.mt5.get_bot_trades(days=LEARN_DAYS)
                universe = self._universe(trades, bot_trades)
                ctx = self._contexts(trades + bot_trades)
                trades_e = enrich_trades(trades, ctx, universe)
                comparison = compare_bot_with_manual(trades_e, enrich_trades(bot_trades, ctx, universe))
            except Exception:
                comparison = None            # bot geçmişi okunamazsa yalnız manuel öğrenme yapılır
            if stats['count'] < MIN_TRADES:
                return {'success': False, 'error': f'Son {LEARN_DAYS} günde elle açılmış kapalı işlem sayısı '
                                                   f'{stats["count"]} (en az {MIN_TRADES} gerekli).'}
            if comparison and (comparison['bot_count'] or comparison['manual']['losses']):
                # Modele yalnız özet sayılar gider (referans/sembol ayrıntısı kodda kalır).
                short = lambda causes: [{k: c[k] for k in ('label', 'count', 'loss')} for c in causes]
                stats = {**stats, 'loss_analysis': {
                    'manual': {**{k: comparison['manual'][k] for k in ('losses', 'loss_total')},
                               'causes': short(comparison['manual']['causes'])},
                    'bot': {**{k: comparison[k] for k in ('bot_count', 'bot_wins', 'bot_losses', 'bot_loss_total')},
                            'causes': short(comparison['causes'])}}}
            mine = self.review(force=True, ref=(comparison or {}).get('reference'))
            if mine and mine['count']:
                # Modele yalnız MevO'nun kendi sonuçlarının özeti gider: model bundan ders çıkarır.
                stats = {**stats, 'mevo_results': {
                    'count': mine['count'], 'win_rate': mine['win_rate'], 'net': mine['net'], 'avg_capture_pct': mine['avg_capture'],
                    'causes': [{k: c[k] for k in ('label', 'count', 'loss')} for c in mine['causes'][:5]]}}
            key = digest_of([stats, comparison], self.model)
            old = self._acct().get('profile')
            if old and old.get('digest') == key and old.get('version', 1) >= PROFILE_VERSION:
                return {'success': True, 'cached': True, 'profile': old}   # aynı veri: token harcanmaz
            learned = self._call(self.LEARN_SYSTEM, stats, 'learn', max_tokens=900)
            with self._lock:
                acct = self._acct()
                acct['profile'] = {'learned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                   'days': LEARN_DAYS, 'stats': stats, 'analysis': learned, 'digest': key, 'version': PROFILE_VERSION,
                                   'comparison': comparison}
                acct['opportunities'] = None
                self._save()
            return {'success': True, 'profile': acct['profile']}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            self._busy.release()

    # ---- fırsatlar ----
    OPP_SYSTEM = (
        'Yalnız danışmansın. Kullanıcının mantığına (rules) ve sembol verisine göre her sembol için AL/SAT fırsatı '
        'ya da BEKLE ver. confidence model güvenidir, olasılık değil. SL/TP fiyat: AL sl<giriş<tp, SAT tp<giriş<sl; '
        'mesafeleri kullanıcının median_sl_pct/median_tp_pct değerine yakın tut; SL mesafesi guards.min_sl_pct (giriş fiyatının %) değerinden dar olmasın. '
        'Fırsatta SL/TP\'yi SEN veriyorsun: "SL/TP olmadan işlem yasağı" gibi kurallar BEKLE sebebi değildir, SL/TP ile uygula. '
        'Saat/gün kuralı kullanma. Kurallar karışık/zayıfsa bile sembol verisinden (trend, aralık) bir yön çıkıyorsa fırsat ver; yalnız gerçekten belirsizse BEKLE. '
        'lessons/avoid maddelerine aykırı ya da spread_pct/atr_pct yüksek, trend_pct yönün tersine güçlüyse BEKLE ver. BEKLE ise sl/tp/matched_rule yazma. '
        'Türkçe, reason en fazla 1 cümle. JSON: {"opportunities":[{"symbol":"","action":"BUY|SELL|WAIT",'
        '"confidence":0,"sl":0,"tp":0,"matched_rule":"","reason":""}]}')

    def opportunities(self, symbols, timeframe_minutes=15, store=True):
        old = self._acct().get('profile')
        if store and old and old.get('version', 1) < PROFILE_VERSION and self.api_key:
            self.learn()          # eski (saat kurallı) profil: bir kez yeniden öğren; başarısızsa eskisiyle devam
        symbols = [s for s in dict.fromkeys(str(s).strip() for s in symbols) if s][:SYMBOLS_MAX]
        if not symbols:
            return {'success': False, 'error': 'Sembol seçilmedi.'}
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            profile = self._acct().get('profile')
            if not profile:
                return {'success': False, 'error': 'Önce "Manuel işlemlerimi öğren" düğmesine bas.'}
            mine = self.review() or {}
            guards, lessons = mine.get('guards') or {}, [l['text'] for l in (mine.get('lessons') or [])][:4]
            ai_lessons = [str(x)[:160] for x in no_hour_rules((profile.get('analysis') or {}).get('lessons'))][:4]
            policy = mine.get('policy') or {'min_confidence': 65}
            open_now = None
            try:
                open_now = len(list(self.mt5.get_positions()))
            except Exception:
                pass
            lessons_key = digest_of([guards, lessons, ai_lessons])[:10]
            live, ticks, markets, keys = {}, {}, [], {}
            cache = self._acct().setdefault('raw_cache', {})
            raws = {}
            for sym in symbols:
                tick = self.mt5.get_symbol_price(sym)
                rates = self.mt5.get_rates(sym, timeframe_minutes, 40) or []
                if not tick or not tick.get('bid') or not tick.get('ask') or len(rates) < 20:
                    continue
                ticks[sym] = tick
                closed = rates[:-1] if len(rates) > 1 else rates      # oluşan mum hariç (EA kuralı)
                # Aynı kapanmış mum + aynı profil = aynı cevap: modele tekrar sorulmaz (token tasarrufu).
                last = closed[-24:]
                mid = (float(tick['bid']) + float(tick['ask'])) / 2
                avg_range = sum(float(r['high']) - float(r['low']) for r in last) / len(last)
                live[sym] = {'spread_pct': (float(tick['ask']) - float(tick['bid'])) / mid * 100,
                             'atr_pct': avg_range / mid * 100, 'conc_n': open_now}
                keys[sym] = f"{sym}|{timeframe_minutes}|{closed[-1]['time']}|{profile.get('digest')}|{lessons_key}"
                if keys[sym] in cache:
                    raws[sym] = cache[keys[sym]]
                    continue
                hi, lo = max(float(r['high']) for r in last), min(float(r['low']) for r in last)
                markets.append({'symbol': sym, 'bid': tick['bid'], 'ask': tick['ask'],
                                'spread_pct': round(live[sym]['spread_pct'], 5), 'atr_pct': round(live[sym]['atr_pct'], 5),
                                'trend_pct': round((float(last[-1]['close']) - float(last[max(0, len(last) - 13)]['close'])) / mid * 100, 4),
                                'pos_in_range': round((float(last[-1]['close']) - lo) / (hi - lo), 2) if hi > lo else 0.5,
                                'closes': [round(float(r['close']), 6) for r in last],
                                'hi': round(max(float(r['high']) for r in last), 6),
                                'lo': round(min(float(r['low']) for r in last), 6),
                                'avg_range': round(sum(float(r['high']) - float(r['low']) for r in last) / len(last), 6)})
            if not ticks:
                return {'success': False, 'error': 'Seçilen sembollerin fiyat/mum verisi okunamadı.'}
            used_model = False
            if markets:
                analysis = profile['analysis']
                raw = self._call(self.OPP_SYSTEM, {
                    'rules': no_hour_rules(analysis.get('rules')), 'habits': no_hour_rules(analysis.get('risk_habits')),
                    'symbol_stats': {m['symbol']: profile['stats']['symbols'].get(m['symbol']) for m in markets},
                    'guards': {m['symbol']: guards.get(m['symbol']) for m in markets if guards.get(m['symbol'])},
                    'lessons': lessons + ai_lessons, 'avoid': no_hour_rules(analysis.get('weak_points'))[:3],
                    'tf': f'M{timeframe_minutes}', 'markets': markets},
                    'opportunities', max_tokens=min(1200, 200 * len(markets) + 100))
                items = raw.get('opportunities') if isinstance(raw.get('opportunities'), list) else []
                fresh = {i['symbol']: i for i in items if isinstance(i, dict) and i.get('symbol') in ticks}
                for m in markets:
                    # Modelin cevap vermediği sembol de önbelleğe BEKLE olarak girer; boşuna tekrar sorulmaz.
                    if m['symbol'] in fresh:
                        raws[m['symbol']] = cache[keys[m['symbol']]] = fresh[m['symbol']]
                    else:   # yanıtsız sembol geçici sayılır: BEKLE gösterilir ama önbelleğe girmez (mum boyunca takılı kalmasın)
                        raws[m['symbol']] = {'symbol': m['symbol'], 'action': 'WAIT', 'reason': 'Model bu sembol için yanıt vermedi; tekrar denenecek.'}
                used_model = True
            while len(cache) > 40:
                cache.pop(next(iter(cache)))
            result = [validate_opportunity(raws[sym], ticks[sym]) for sym in symbols if sym in raws]
            own_list = (self.review() or {}).get('blocks', [])      # MevO'nun kendi geçmiş sonuçlarından önlemler
            for r in result:
                r['min_confidence'] = policy['min_confidence']
                apply_sl_guard(r, guards.get(r['symbol']))
                sl_pct = abs(r['entry'] - r['sl']) / r['entry'] * 100 if r.get('entry') else None
                r['warnings'] = trade_warnings(r, profile.get('comparison'), None, sl_pct, own_list, live.get(r['symbol']))
            result.sort(key=lambda r: (r['action'] == 'WAIT', -r['confidence']))
            payload = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'timeframe': f'M{timeframe_minutes}', 'items': result, 'used_model': used_model,
                       'policy': policy}
            with self._lock:
                if store:
                    self._acct()['opportunities'] = payload
                self._save()
            return {'success': True, **payload}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            self._busy.release()

    def _remember_bot_ids(self, ids):
        """Bot MevO girişlerinin pozisyon numaraları kalıcı tutulur: bot geçmişi 50 kayıtla sınırlı ve temizlenebilir,
        sonuç analizi eski işlemleri kaybetmesin."""
        with self._lock:
            acct = self._acct()
            known = [int(i) for i in acct.get('bot_mevo_ids', [])]
            merged = known + [int(i) for i in ids or () if int(i) not in set(known)]
            merged = merged[-2000:]
            if merged != known:
                acct['bot_mevo_ids'] = merged
                self._save()
            return merged

    def _contexts(self, trades):
        """Kapalı işlemlerin broker bağlamı (MAE/MFE, spread, aralık). Kapanmış işlem değişmez: bir kez okunur ve
        hesap durumunda saklanır (her çağrıda en yeni 60 eksik işlem; okunamazsa eldekiyle devam)."""
        with self._lock:
            cache = self._acct().setdefault('ctx', {})
            need = [t for t in sorted(trades, key=lambda t: t['open_time']) if str(t['position_id']) not in cache][-60:]
        if need:
            try:
                got = self.mt5.get_trade_contexts(need)
                got = got if isinstance(got, dict) else {}
                with self._lock:
                    cache = self._acct().setdefault('ctx', {})
                    for t in need:
                        cache[str(t['position_id'])] = got.get(str(t['position_id']), {})
                    while len(cache) > 800:
                        cache.pop(next(iter(cache)))
                    self._save()
            except Exception:
                pass
        return self._acct().get('ctx', {})

    def _manual_trades(self, fresh=None):
        """Elle açılmış kapalı işlemler (10 dk önbellek; eşzamanlılık hesabı için, her sonuç okumasında broker'a gidilmez)."""
        if fresh is not None:
            self._manual_cache = (time.monotonic(), fresh)
            return fresh
        cached = getattr(self, '_manual_cache', None)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        try:
            return self._manual_trades(self.mt5.get_manual_trades(days=LEARN_DAYS))
        except Exception:
            return cached[1] if cached else []

    def _universe(self, *groups):
        seen, out = set(), []
        for g in groups:
            for t in g or ():
                if t.get('position_id') not in seen:
                    seen.add(t.get('position_id')); out.append(t)
        return out

    def review(self, force=False, ref=None):
        """MevO'nun açtırdığı işlemlerin sonuç raporu (60 sn önbellek; okunamazsa None)."""
        now = time.monotonic()
        cached = getattr(self, '_review_cache', None)
        if cached and not force and now - cached[0] < 60:
            return cached[1]
        try:
            ids = self._remember_bot_ids(self.bot_position_ids() if callable(self.bot_position_ids) else ())
            trades = self.mt5.get_mevo_trades(days=LEARN_DAYS, bot_position_ids=ids)
            profile = self._acct().get('profile') or {}
            ref = ref if ref is not None else (profile.get('comparison') or {}).get('reference') or {}
            manual = self._manual_trades()
            ctx = self._contexts(trades)
            trades = enrich_trades(trades, ctx, self._universe(manual, trades))
            data = review_trades(trades, ref)
            self._record_journal(data)
        except Exception:
            return cached[1] if cached else None
        self._review_cache = (now, data)
        return data

    def _record_journal(self, data):
        """Gelişim defteri: sonuç sayısı değiştikçe kısa özet eklenir (kalıcı), kendini geliştirme trendi görülsün."""
        with self._lock:
            acct = self._acct()
            journal = acct.setdefault('journal', [])
            if data['count'] and (not journal or journal[-1]['count'] != data['count']):
                journal.append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'count': data['count'],
                                'net': data['net'], 'win_rate': data['win_rate'], 'lessons': len(data['lessons']),
                                'min_confidence': data['policy']['min_confidence']})
                del journal[:-60]
                self._save()
            data['journal'] = list(journal)[-12:]

    def signal_for(self, symbol, timeframe_minutes):
        """Bot için tek sembol fırsatı (sayfadaki listeyi değiştirmez). Aynı kapanmış mum önbellekten gelir."""
        res = self.opportunities([symbol], timeframe_minutes, store=False)
        if not res.get('success'):
            raise RuntimeError(res.get('error', 'MevO fırsatı alınamadı'))
        return next((i for i in res['items'] if i['symbol'] == symbol), None)

    def status(self):
        with self._lock:
            acct = self._acct()
            day = datetime.now(timezone.utc).date().isoformat()
            return {'configured': bool(self.api_key), 'key_source': self.key_source(), 'model': self.model, 'profile': acct.get('profile'),
                    'opportunities': acct.get('opportunities'),
                    'usage_today': acct['usage'].get(day, {'calls': 0, 'tokens': 0}),
                    'daily_call_limit': self.daily_call_limit}
