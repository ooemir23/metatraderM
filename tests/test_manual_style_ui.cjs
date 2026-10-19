// Manuel Stil card: rendering, experimental label and alert sound rules.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', checked: false, value: '', addEventListener() {}});
  return nodes.get(id);
};
node('signal-scan-tf').value = '15';
const beeps = [], toasts = [], urls = [];
let payload = null;
const store = {};
const ctx = vm.createContext({
  console, Number, Math, Date, JSON, Set, Object, String, encodeURIComponent,
  document: {getElementById: node, readyState: 'complete', visibilityState: 'visible', addEventListener() {}},
  localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }},
  setInterval() {}, setTimeout() {},
  fetch: async (url) => { urls.push(url); return {ok: true, json: async () => payload}; },
  currentSymbol: 'XAUUSD', signalAutoAlertEnabled: true,
  playSignalBeep: (isBuy) => beeps.push(isBuy), showToast: (m) => toasts.push(m),
  escapeHtml: (t) => String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
});
ctx.window = ctx;
vm.runInContext(fs.readFileSync('app/static/manual-style.js', 'utf8'), ctx);
const api = ctx.HMAManualStyle;

function data(side, candle, {reliable = false} = {}) {
  return {status: 'OK', symbol: 'XAUUSD', timeframe_minutes: 15, days: 90, positions: 216, decisions: 45, wins: 30, net: 162436,
    manual_symbols: {XAUUSD: 183}, verdict: {reliable, reason: 'Geçmiş testte sinyaller her mumda girmekten daha iyi değil.'},
    entry_quality: {hit_rate: 0.4, avg_move: -0.1},
    profile: {features: [{label: 'HMA/EMA trend yönü', kind: 'share', wins: 0.6, losses: 0.5},
                         {label: 'RSI', kind: 'median', wins: 48.5, losses: 46.6}]},
    history_test: {horizon_bars: 8, signals: {count: 40, all_signals: 70, hit_rate: 0.45, avg_move: -0.1},
                   baseline: {hit_rate: 0.48, avg_move: 0}, recent: [{candle_time: 1792400000, side: 'SELL', after: {move: 0.5}}]},
    signal: {side, candidate_side: side === 'HOLD' ? 'SELL' : side, score: 0.6, similar: true, win_share: 0.71,
             candle_time: candle, price: 4146.5, experimental: !reliable,
             neighbours: [{win: true}, {win: true}, {win: false}]}};
}

(async () => {
  // 1. Request uses the selected symbol and scanner timeframe; render shows the learned side.
  payload = data('SELL', 1792430000);
  await api.refresh(true);
  assert.match(urls.at(-1), /\/api\/manual-style\?symbol=XAUUSD&timeframe_minutes=15&refresh=true/);
  const html = node('manual-style-body').innerHTML;
  assert.match(html, /ms-live" data-side="SELL"/);
  assert.match(html, /<span class="ms-side">SAT<\/span>/);
  assert.match(html, /En benzer 3 geçmiş girişin 2'i kazandı/);
  assert.match(html, /ms-badge is-warn">Deneysel/);
  assert.match(html, /40 bağımsız sinyal \(toplam 70\)/);
  assert.match(node('manual-style-status').textContent, /216 pozisyon → 45 karar \(30 kazanan\)/);
  assert.equal(beeps.length, 0, 'a signal already there when the page opens does not ring');

  // 2. A new experimental signal stays silent unless the user allowed it.
  payload = data('BUY', 1792430900);
  await api.refresh(false);
  assert.equal(beeps.length, 0);
  store['hma.manualStyle.experimentalSound.v1'] = '1';
  payload = data('SELL', 1792431800);
  await api.refresh(false);
  assert.deepEqual(beeps, [false]);
  assert.match(toasts.at(-1), /Manuel stil: XAUUSD M15 SAT \(deneysel\)/);

  // 3. A signal that passed the history test rings with the main alert switch; once per candle.
  store['hma.manualStyle.experimentalSound.v1'] = '0';
  payload = data('BUY', 1792432700, {reliable: true});
  await api.refresh(false);
  await api.refresh(false);
  assert.deepEqual(beeps, [false, true]);
  assert.match(node('manual-style-body').innerHTML, /ms-badge is-ok">Geçmiş testi geçti/);
  ctx.signalAutoAlertEnabled = false;
  payload = data('SELL', 1792433600, {reliable: true});
  await api.refresh(false);
  assert.equal(beeps.length, 2, 'main sound switch off: no sound');

  // 4. HOLD and not-enough-data states.
  payload = data('HOLD', 1792434500);
  await api.refresh(false);
  assert.match(node('manual-style-body').innerHTML, /BEKLE/);
  payload = {status: 'NOT_ENOUGH', symbol: 'EURUSD', timeframe_minutes: 15, days: 90, positions: 2, decisions: 2, wins: 1, net: 12,
             message: 'EURUSD için öğrenecek yeterli manuel karar yok (2/12).', manual_symbols: {EURUSD: 2, XAUUSD: 183}};
  await api.refresh(false);
  assert.match(node('manual-style-body').innerHTML, /yeterli manuel karar yok/);
  assert.match(node('manual-style-body').innerHTML, /XAUUSD \(183\)/);
  console.log('manual style ui ok');
})().catch((e) => { console.error(e); process.exit(1); });
