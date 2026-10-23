"""MevO — DeepSeek destekli, YALNIZ DANIŞMAN asistan (2026-10-23 yeniden yazıldı).

Kullanıcı kararı: MevO sadece orijinal MT5 terminalinden elle açılmış işlemleri (broker geçmişi) inceleyip
mantığını öğrenir ve bu mantığa uyan fırsatları SUNAR. Emir açmaz, otopilotu yoktur; açmak istersen sen açarsın.

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
        '{"summary":"en fazla 2 cümle","rules":[],"risk_habits":[],"best_hours":[],"weak_points":[],"caveats":[]}')

    def learn(self):
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            trades = self.mt5.get_manual_trades(days=LEARN_DAYS)
            stats = compute_stats(trades)
            if stats['count'] < MIN_TRADES:
                return {'success': False, 'error': f'Son {LEARN_DAYS} günde elle açılmış kapalı işlem sayısı '
                                                   f'{stats["count"]} (en az {MIN_TRADES} gerekli).'}
            key = digest_of(stats, self.model)
            old = self._acct().get('profile')
            if old and old.get('digest') == key:
                return {'success': True, 'cached': True, 'profile': old}   # aynı veri: token harcanmaz
            learned = self._call(self.LEARN_SYSTEM, stats, 'learn', max_tokens=800)
            with self._lock:
                acct = self._acct()
                acct['profile'] = {'learned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                   'days': LEARN_DAYS, 'stats': stats, 'analysis': learned, 'digest': key}
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

    def opportunities(self, symbols, timeframe_minutes=15):
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
            result.sort(key=lambda r: (r['action'] == 'WAIT', -r['confidence']))
            payload = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'timeframe': f'M{timeframe_minutes}', 'items': result, 'used_model': used_model}
            with self._lock:
                self._acct()['opportunities'] = payload
                self._save()
            return {'success': True, **payload}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            self._busy.release()

    def status(self):
        with self._lock:
            acct = self._acct()
            day = datetime.now(timezone.utc).date().isoformat()
            return {'configured': bool(self.api_key), 'key_source': self.key_source(), 'model': self.model, 'profile': acct.get('profile'),
                    'opportunities': acct.get('opportunities'),
                    'usage_today': acct['usage'].get(day, {'calls': 0, 'tokens': 0}),
                    'daily_call_limit': self.daily_call_limit}
