// "Bota Ekle": the focused bot symbol card shows which trade the added signal is.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, {id, value: '', checked: false, innerText: '', textContent: '', innerHTML: '', hidden: true, dataset: {},
      classList: {contains: c => classes.has(c), add: c => classes.add(c), remove: c => classes.delete(c), toggle: (c, v) => (v ? classes.add(c) : classes.delete(c))}});
  }
  return nodes.get(id);
};
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController,
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, MT5Markets: {all: []}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);

const bot = {is_running: false, symbol: 'EURUSD', symbols: ['EURUSD'], timeframe_minutes: 15, lot_size: 0.05,
  lot_mode: 'FIXED', risk_mode: 'POINTS', use_stop_loss: true, sl_points: 200, use_take_profit: false, tp_points: 400, symbol_status: {}};
vm.runInContext(`latestScannedSignals = [{symbol: 'EURUSD', signal: 'SELL_CROSS', side: 'SELL', price: 1.16513,
  suggested_sl: 1.16700, suggested_tp: 1.16233, digits: 5, candle_time: 1700000000}];`, ctx);
ctx.rememberAddedSignal('EURUSD', 15);
ctx.renderBotSymbolStatus(bot);
let html = node('bot-symbol-status').innerHTML;
assert.match(html, /Taranan sinyal/);
assert.match(html, /data-side="SELL">SAT</);
assert.match(html, /Yeni kesişim · Fiyat <b>1\.16513<\/b> · 0\.05 lot/);
assert.match(html, /SL <b>1\.16700<\/b> · TP <b>1\.16233<\/b>/, 'Bota Gönder SL/TP shown');
assert.match(html, /mevcut sinyali açmaz, sonraki yeni kesişimde 0\.05 lot açar \(SL 200 puan, TP yok\)/,
  'MT5 EA rule: an existing signal is not traded; bot SL/TP settings are shown, not the scanner suggestion');

// The added signal stays visible when a later scan no longer lists the symbol.
vm.runInContext('latestScannedSignals = [];', ctx);
ctx.renderBotSymbolStatus({...bot, is_running: true});
html = node('bot-symbol-status').innerHTML;
assert.match(html, /data-side="SELL"/);
assert.match(html, /bir sonraki kapanmış mumda yeni kesişim olursa 0\.05 lot açar/);

// Symbols without an added signal keep the plain card.
ctx.renderBotSymbolStatus({...bot, symbols: ['GBPUSD']});
assert.doesNotMatch(node('bot-symbol-status').innerHTML, /Taranan sinyal/);
console.log('bot added signal ui ok');
