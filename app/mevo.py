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
DEFAULT_MODEL = 'deepseek-chat'
MIN_TRADES = 5
LEARN_DAYS = 90
MIN_INTERVAL = 30            # saniye: aynı işlem türü için iki istek arası
SYMBOLS_MAX = 6


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
    for t in trades[-25:]:
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
        self.api_key = api_key if api_key is not None else os.getenv('DEEPSEEK_API_KEY', '')
        self.model = os.getenv('DEEPSEEK_MODEL', DEFAULT_MODEL)
        self.daily_call_limit = max(1, int(os.getenv('MEVO_DAILY_CALL_LIMIT', '100')))
        self._path = path or state_path('mevo.json')
        self._lock = threading.RLock()
        self._busy = threading.Lock()
        self._last = {}
        self.state = self._load()

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
    def _call(self, system, user, operation, max_tokens=1500):
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
        'Sen bir işlem geçmişi analizcisisin. Kullanıcının orijinal MT5 terminalinden ELLE açtığı kapalı '
        'işlemlerin istatistiklerini alırsın. Sadece verinin desteklediği bulguları yaz; psikoloji, kâr garantisi '
        'ya da veride olmayan neden uydurma. Örnek sayısı azsa bunu belirt. Türkçe JSON üret: '
        '{"summary":"en fazla 3 cümle","style":"kısa","symbols":[{"symbol":"","bias":"AL|SAT|İKİSİ","note":""}],'
        '"rules":["kullanıcının tutarlı biçimde uyguladığı, veriden çıkan kural (en fazla 6)"],'
        '"risk_habits":["lot / SL / TP / tutma süresi alışkanlıkları"],"best_hours":["broker saati aralıkları"],'
        '"weak_points":["veriye dayalı zayıflıklar"],"caveats":["örnek azlığı vb."]}')

    def learn(self):
        if not self._busy.acquire(blocking=False):
            return {'success': False, 'error': 'MevO başka bir istek işliyor; bekleyin.'}
        try:
            trades = self.mt5.get_manual_trades(days=LEARN_DAYS)
            stats = compute_stats(trades)
            if stats['count'] < MIN_TRADES:
                return {'success': False, 'error': f'Son {LEARN_DAYS} günde elle açılmış kapalı işlem sayısı '
                                                   f'{stats["count"]} (en az {MIN_TRADES} gerekli).'}
            learned = self._call(self.LEARN_SYSTEM, stats, 'learn')
            with self._lock:
                acct = self._acct()
                acct['profile'] = {'learned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                   'days': LEARN_DAYS, 'stats': stats, 'analysis': learned}
                acct['opportunities'] = None
                self._save()
            return {'success': True, 'profile': acct['profile']}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}
        finally:
            self._busy.release()

    # ---- fırsatlar ----
    OPP_SYSTEM = (
        'Sen yalnız danışman bir asistansın; emir açmazsın. Kullanıcının elle işlem yapma mantığı (profil) ve '
        'sembollerin güncel fiyat/mum verisi verilir. Her sembol için, KULLANICININ KENDİ mantığına uyan bir '
        'kurulum varsa AL veya SAT fırsatı sun; yoksa BEKLE de. Uydurma kesinlik verme: confidence model '
        'güvenidir, kazanma olasılığı değildir. SL/TP fiyat olarak yaz; AL için sl<giriş<tp, SAT için tp<giriş<sl. '
        'SL/TP mesafelerini kullanıcının alışkanlığına (median_sl_pct / median_tp_pct) yakın tut. Türkçe JSON: '
        '{"opportunities":[{"symbol":"","action":"BUY|SELL|WAIT","confidence":0,"sl":0,"tp":0,'
        '"matched_rule":"profildeki hangi kurala uyuyor","reason":"en fazla 2 cümle"}]}')

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
            ticks, markets = {}, []
            for sym in symbols:
                tick = self.mt5.get_symbol_price(sym)
                rates = self.mt5.get_rates(sym, timeframe_minutes, 60) or []
                if not tick or not tick.get('bid') or not tick.get('ask') or len(rates) < 20:
                    continue
                ticks[sym] = tick
                closed = rates[:-1] if len(rates) > 1 else rates      # oluşan mum hariç (EA kuralı)
                markets.append({'symbol': sym, 'bid': tick['bid'], 'ask': tick['ask'],
                                'timeframe': f'M{timeframe_minutes}',
                                'candles_ohlc': [[round(float(r['open']), 6), round(float(r['high']), 6),
                                                  round(float(r['low']), 6), round(float(r['close']), 6)]
                                                 for r in closed[-40:]]})
            if not markets:
                return {'success': False, 'error': 'Seçilen sembollerin fiyat/mum verisi okunamadı.'}
            sym_stats = {m['symbol']: profile['stats']['symbols'].get(m['symbol']) for m in markets}
            raw = self._call(self.OPP_SYSTEM, {
                'profile': profile['analysis'], 'symbol_stats': sym_stats, 'markets': markets},
                'opportunities', max_tokens=1800)
            items = raw.get('opportunities') if isinstance(raw.get('opportunities'), list) else []
            result = []
            for item in items:
                if isinstance(item, dict) and item.get('symbol') in ticks:
                    result.append(validate_opportunity(item, ticks[item['symbol']]))
            result.sort(key=lambda r: (r['action'] == 'WAIT', -r['confidence']))
            payload = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'timeframe': f'M{timeframe_minutes}', 'items': result}
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
            return {'configured': bool(self.api_key), 'model': self.model, 'profile': acct.get('profile'),
                    'opportunities': acct.get('opportunities'),
                    'usage_today': acct['usage'].get(day, {'calls': 0, 'tokens': 0}),
                    'daily_call_limit': self.daily_call_limit}
