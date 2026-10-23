"""MevO — DeepSeek destekli, DANIŞMAN asistan (2026-10-23 yeniden yazıldı).

Kullanıcı kararı: MevO sadece orijinal MT5 terminalinden elle açılmış işlemleri (broker geçmişi) inceleyip
mantığını öğrenir ve bu mantığa uyan fırsatları SUNAR. Kendisi emir açmaz: fırsatı kullanıcı düzenleyip açar (sayfa) ya da kullanıcı botta 'MevO'yu kullan' seçer.

Kurallar
- Öğrenme verisi yalnız `MT5Client.get_manual_trades` (elle açılan, tamamen kapanmış pozisyonlar) ve bunlardan
  hesaplanan istatistiklerdir. Panel/bot/web emirleri dahil edilmez.
- Sayıları kod hesaplar; DeepSeek yalnız bu sayıları yorumlar ve fırsatları yazar. Veri dışı psikoloji,
  başarı garantisi uydurmaz (prompt'ta yasak).
- Fırsat seviyeleri doğrulanır: AL için SL < giriş < TP, SAT için TP < giriş < SL değilse BEKLE'ye çevrilir.
- API anahtarı yalnız ortam değişkeninden (`DEEPSEEK_API_KEY`) okunur; koda/depoya yazılmaz.
"""
import json
import math
import os
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
    'no_ref': 'Manuelde bu sembolde yeterli kârlı işlem yok (karşılaştırılamadı)',
    'other': 'Belirgin bir fark bulunamadı',
}


def winner_reference(manual):
    """Manuel KÂRLI işlemlerden sembol başına referans (saatler, baskın yön, lot, süre, SL mesafesi)."""
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
        ref[sym] = {'n': len(rows), 'hours': sorted({_hour(r['open_time']) for r in rows}),
                    'side': top if n / len(rows) >= 0.7 else None,
                    'median_lot': median(float(r['volume']) for r in rows),
                    'median_hold_min': median((r['close_time'] - r['open_time']) / 60 for r in rows),
                    'median_sl_pct': median(sl) if sl else None}
    return ref


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
    karşılaştırılır (saat, yön, lot, tutma süresi, SL)."""
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
            'hold_min': round((t['close_time'] - t['open_time']) / 60, 1), 'rr': rr, 'why': why}


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
    return {'count': len(rows), 'wins': len(wins), 'losses': len(losses),
            'win_rate': round(len(wins) / len(rows) * 100, 1) if rows else None,
            'net': round(sum(float(t['net']) for t in rows), 2), 'loss_total': block['loss_total'],
            'causes': block['causes'],
            'groups': [{'symbol': k[0], 'side': k[1], **v} for k, v in sorted(groups.items(), key=lambda kv: kv[1]['net'])],
            'trades': [explain_trade(t, ref) for t in rows[:20]],
            'blocks': own_blocks(rows)}


def trade_warnings(item, comparison, broker_hour, entry_sl_pct, own=None):
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

    def lost(key):
        c = seen.get(key)
        return f" Geçmişte ({'+'.join(c['who'])}) bu sebeple {c['count']} kez toplam {c['loss']} zarar edildi." if c else ''
    if not ref:
        return own + [f"{item['symbol']} için manuel kârlı işlem referansı yetersiz; karşılaştırma yapılamadı."]
    if ref['side'] and item['action'] != ref['side']:
        out.append(f"Manuel kârlı işlemlerin çoğu {ref['side']}; bu fırsat tersi." + lost('side'))
    if entry_sl_pct and ref['median_sl_pct'] and entry_sl_pct < ref['median_sl_pct'] * 0.5:
        out.append(f"Önerilen SL (%{entry_sl_pct:.3f}) manuel kârlı SL'nin (%{ref['median_sl_pct']}) yarısından dar." + lost('tight_sl'))
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
        'uydurma; örnek azsa belirt. Çok kısa Türkçe JSON (her liste en fazla 4 kısa madde): '
        '{"summary":"en fazla 2 cümle","rules":[],"risk_habits":[],"best_hours":[],"weak_points":[],"caveats":[],"manual_loss_reasons":[],"bot_loss_reasons":[]}. '
        'loss_analysis varsa manuel ve bot ZARARLARININ sebebini yalnız bu sayılara dayanarak ilgili listeye yaz '
        '(en fazla 4 madde, sebep + zarar tutarı + hangi durumda); kaçınılacak durumları da belirt; yoksa boş bırak.')

    def learn(self):
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            trades = self.mt5.get_manual_trades(days=LEARN_DAYS)
            stats = compute_stats(trades)
            try:
                comparison = compare_bot_with_manual(trades, self.mt5.get_bot_trades(days=LEARN_DAYS))
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
            key = digest_of([stats, comparison], self.model)
            old = self._acct().get('profile')
            if old and old.get('digest') == key:
                return {'success': True, 'cached': True, 'profile': old}   # aynı veri: token harcanmaz
            learned = self._call(self.LEARN_SYSTEM, stats, 'learn', max_tokens=900)
            with self._lock:
                acct = self._acct()
                acct['profile'] = {'learned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                   'days': LEARN_DAYS, 'stats': stats, 'analysis': learned, 'digest': key,
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
        'mesafeleri kullanıcının median_sl_pct/median_tp_pct değerine yakın tut. BEKLE ise sl/tp/matched_rule yazma. '
        'Türkçe, reason en fazla 1 cümle. JSON: {"opportunities":[{"symbol":"","action":"BUY|SELL|WAIT",'
        '"confidence":0,"sl":0,"tp":0,"matched_rule":"","reason":""}]}')

    def opportunities(self, symbols, timeframe_minutes=15, store=True):
        symbols = [s for s in dict.fromkeys(str(s).strip() for s in symbols) if s][:SYMBOLS_MAX]
        if not symbols:
            return {'success': False, 'error': 'Sembol seçilmedi.'}
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            profile = self._acct().get('profile')
            if not profile:
                return {'success': False, 'error': 'Önce "Manuel işlemlerimi öğren" düğmesine bas.'}
            ticks, markets, keys = {}, [], {}
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
                keys[sym] = f"{sym}|{timeframe_minutes}|{closed[-1]['time']}|{profile.get('digest')}"
                if keys[sym] in cache:
                    raws[sym] = cache[keys[sym]]
                    continue
                last = closed[-24:]
                markets.append({'symbol': sym, 'bid': tick['bid'], 'ask': tick['ask'],
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
                    'rules': analysis.get('rules'), 'habits': analysis.get('risk_habits'),
                    'symbol_stats': {m['symbol']: profile['stats']['symbols'].get(m['symbol']) for m in markets},
                    'tf': f'M{timeframe_minutes}', 'markets': markets},
                    'opportunities', max_tokens=min(1200, 200 * len(markets) + 100))
                items = raw.get('opportunities') if isinstance(raw.get('opportunities'), list) else []
                fresh = {i['symbol']: i for i in items if isinstance(i, dict) and i.get('symbol') in ticks}
                for m in markets:
                    # Modelin cevap vermediği sembol de önbelleğe BEKLE olarak girer; boşuna tekrar sorulmaz.
                    raws[m['symbol']] = fresh.get(m['symbol'], {'symbol': m['symbol'], 'action': 'WAIT',
                                                                 'reason': 'Model bu sembol için yanıt vermedi.'})
                    cache[keys[m['symbol']]] = raws[m['symbol']]
                used_model = True
            while len(cache) > 40:
                cache.pop(next(iter(cache)))
            result = [validate_opportunity(raws[sym], ticks[sym]) for sym in symbols if sym in raws]
            hour = (datetime.now(timezone.utc).hour
                    + int(getattr(self.mt5, 'tick_clock_offset', 0) or 0) // 3600) % 24     # broker sunucu saati
            own_list = (self.review() or {}).get('blocks', [])      # MevO'nun kendi geçmiş sonuçlarından önlemler
            for r in result:
                sl_pct = abs(r['entry'] - r['sl']) / r['entry'] * 100 if r.get('entry') else None
                r['warnings'] = trade_warnings(r, profile.get('comparison'), hour, sl_pct, own_list)
            result.sort(key=lambda r: (r['action'] == 'WAIT', -r['confidence']))
            payload = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'timeframe': f'M{timeframe_minutes}', 'items': result, 'used_model': used_model}
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

    def review(self, force=False):
        """MevO'nun açtırdığı işlemlerin sonuç raporu (60 sn önbellek; okunamazsa None)."""
        now = time.monotonic()
        cached = getattr(self, '_review_cache', None)
        if cached and not force and now - cached[0] < 60:
            return cached[1]
        try:
            ids = self._remember_bot_ids(self.bot_position_ids() if callable(self.bot_position_ids) else ())
            trades = self.mt5.get_mevo_trades(days=LEARN_DAYS, bot_position_ids=ids)
            profile = self._acct().get('profile') or {}
            ref = (profile.get('comparison') or {}).get('reference') or {}
            data = review_trades(trades, ref)
        except Exception:
            return cached[1] if cached else None
        self._review_cache = (now, data)
        return data

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
