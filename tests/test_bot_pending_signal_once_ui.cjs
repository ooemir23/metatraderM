// A signal the bot caught in "Sadece Sinyal" mode is one order: approving it again reuses its id,
// while a scanner "Şimdi AL/SAT" click is a new order each time (MT5 one-click trading).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', hidden: true, dataset: {}, value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  return nodes.get(id);
};
const sent = [];
const pending = {symbol: 'XAUUSD', side: 'BUY', price: 4130.5, sl_points: 2000, tp_points: 3000, id: 'bot-XAUUSD-60-BUY-1792490400'};
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController, navigator: {userActivation: {hasBeenActive: true}},
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, MT5Markets: {all: []}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
  fetch: async (url, opts = {}) => {
    if (url === '/api/bot/live-signal') return {ok: true, json: async () => ({pending_signal_actions: [pending]})};
    if (url === '/api/bot/execute-signal') { sent.push(JSON.parse(opts.body)); return {ok: true, json: async () => ({success: true, ticket: 7})}; }
    return {ok: true, json: async () => ({})};
  },
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
ctx.showToast = () => {};
ctx.fetchPositions = () => {}; ctx.fetchBotStatus = () => {}; ctx.fetchBotLiveSignal = () => {};

(async () => {
  await ctx.executeAllPendingBotSignals();
  await ctx.executeAllPendingBotSignals();
  assert.equal(sent.length, 2);
  assert.equal(sent[0].signal_id, pending.id);
  assert.equal(sent[1].signal_id, pending.id, 'same caught signal -> same id -> one MT5 order (journal replay)');
  assert.equal(sent[0].sl_points, 2000);

  await ctx.sendSignalDirectToBot('XAUUSD', 'SELL', 4130, {signalId: 'scan-XAUUSD-60-SELL-1', slPrice: 4150, tpPrice: 4100});
  await ctx.sendSignalDirectToBot('XAUUSD', 'SELL', 4130, {signalId: 'scan-XAUUSD-60-SELL-1', slPrice: 4150, tpPrice: 4100});
  assert.notEqual(sent[2].signal_id, sent[3].signal_id, 'scanner clicks stay separate orders');
  assert.ok(sent[2].signal_id.startsWith('scan-XAUUSD-60-SELL-1-'));
  console.log('pending bot signal approval is one order: PASS');
})().catch((e) => { console.error(e); process.exit(1); });
