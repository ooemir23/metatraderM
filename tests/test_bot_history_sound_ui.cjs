// Sent-signal list layout and signal alert sounds (scanner + bot).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', hidden: true, dataset: {}, value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  return nodes.get(id);
};
const beeps = [], toasts = [];
class FakeAudio {
  constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
  resume() { return Promise.resolve(); }
  createOscillator() { beeps.push('osc'); return {type: '', frequency: {setValueAtTime() {}}, connect() {}, start() {}, stop() {}}; }
  createGain() { return {gain: {setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}}; }
}
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController, navigator: {userActivation: {hasBeenActive: true}},
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, AudioContext: FakeAudio, MT5Markets: {all: []}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
ctx.showToast = (m, t) => toasts.push([m, t]);

// 1. List: side pill, symbol + details on one line, status pill, readable reason for failures.
const now = Math.floor(Date.now() / 1000);
ctx.renderBotSignalHistory([
  {time: now, source: 'MANUAL', symbol: 'EURUSD', side: 'BUY', lot: 1, success: false,
   error: 'Aynı emir kimliği farklı işlem için kullanılamaz.'},
  {time: now - 60, source: 'AUTO', symbol: 'BTCUSD', side: 'SELL', lot: 0.1, price: 81424, sl_points: 200, success: true, ticket: 42},
]);
const html = node('bot-history-list').innerHTML;
assert.match(html, /bot-history-side is-buy">AL</);
assert.match(html, /<b class="bot-history-symbol">EURUSD<\/b><span class="bot-history-meta">1 lot<\/span>/);
assert.match(html, /bot-history-status">Açılmadı</);
assert.match(html, /bot-history-reason">Aynı sinyal farklı ayarla tekrar gönderilmişti/);
assert.match(html, /0\.1 lot @ 81424 · SL 200 p · #42/);
assert.match(html, /data-source="auto">Otomatik/);

// 2. Bot alerts: first load only records; a new automatic order beeps once.
const bot = {signal_history: [{time: now - 60, source: 'AUTO', symbol: 'BTCUSD', side: 'SELL', lot: 0.1, success: true, ticket: 42}]};
ctx.announceBotSignals(bot);
assert.equal(beeps.length, 0, 'existing history does not beep on page load');
ctx.announceBotSignals({signal_history: [{time: now + 5, source: 'AUTO', symbol: 'BTCUSD', side: 'BUY', lot: 0.1, success: true, ticket: 43}, ...bot.signal_history]});
assert.ok(beeps.length > 0, 'new bot order plays the alert');
assert.match(toasts.at(-1)[0], /Bot BTCUSD AL 0\.1 lot açtı \(#43\)/);
const afterAuto = beeps.length;
ctx.announceBotSignals({signal_history: [{time: now + 9, source: 'MANUAL', symbol: 'EURUSD', side: 'BUY', lot: 1, success: true}]});
assert.equal(beeps.length, afterAuto, 'your own "Bota Gönder" click does not beep again');

// 3. Signals awaiting approval beep once per new signal.
ctx.announceBotPending([{id: 'a', symbol: 'EURUSD', side: 'SELL'}]);
const primed = beeps.length;
ctx.announceBotPending([{id: 'a', symbol: 'EURUSD', side: 'SELL'}]);
assert.equal(beeps.length, primed);
ctx.announceBotPending([{id: 'b', symbol: 'GBPUSD', side: 'BUY'}, {id: 'a', symbol: 'EURUSD', side: 'SELL'}]);
assert.ok(beeps.length > primed);
assert.match(toasts.at(-1)[0], /Bot sinyali: GBPUSD AL · onayın bekleniyor/);

// 4. Muted alert setting: no sound.
ctx.toggleSignalAutoAlert(false);
const muted = beeps.length;
ctx.announceBotPending([{id: 'c', symbol: 'XAUUSD', side: 'BUY'}]);
assert.equal(beeps.length, muted);
console.log('bot history and alert sound ok');

// 5. Row actions: "Düzenle" only for an open position (MT5 Modify), "Sil" for every saved row.
vm.runInContext('executionPositions.set(43, {profit: 1})', ctx);
ctx.renderBotSignalHistory([
  {id: 'aa1', time: now, source: 'AUTO', symbol: 'BTCUSD', side: 'BUY', lot: 0.1, success: true, ticket: 43},
  {id: 'bb2', time: now, source: 'MANUAL', symbol: 'EURUSD', side: 'BUY', lot: 1, success: false, error: 'x'},
]);
const rowsHtml = node('bot-history-list').innerHTML;
assert.equal((rowsHtml.match(/editBotHistoryEntry\(43\)/g) || []).length, 1);
assert.match(rowsHtml, /deleteBotHistoryEntry\('aa1'\)/);
assert.match(rowsHtml, /deleteBotHistoryEntry\('bb2'\)/);
console.log('history row actions ok');

// 6. "Pozisyonlar" tab: one coloured count per position source.
ctx.renderPositionSourceCounts([
  ...Array(7).fill({magic: 0, comment: ''}),
  {magic: 123461, comment: 'HMA Bot'},
]);
const counts = node('ws-tab-positions-count').innerHTML;
assert.match(counts, /data-src="mt5" title="7 pozisyon · Orijinal MT5 \(Manuel\)">7</);
assert.match(counts, /data-src="bot" title="1 pozisyon · Otomatik Sinyal \(HMA Bot\)">1</);
ctx.renderPositionSourceCounts([]);
assert.match(node('ws-tab-positions-count').innerHTML, /data-src="none"[^>]*>0</);
console.log('position source counts ok');
