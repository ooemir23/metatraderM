// "Botla İzle": handed symbols appear in their own frame ("Botun İzledikleri").
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
ctx.renderBotSymbolStatus(bot);
assert.equal(node('bot-added-signals').hidden, true, 'no frame until something is added');

vm.runInContext(`latestScannedSignals = [{symbol: 'EURUSD', signal: 'SELL_CROSS', side: 'SELL', price: 1.16513,
  suggested_sl: 1.16700, suggested_tp: 1.16233, digits: 5, candle_time: 1700000000}];`, ctx);
ctx.rememberAddedSignal('EURUSD', 15);
ctx.renderBotSymbolStatus(bot);
let frame = node('bot-added-signals');
assert.equal(frame.hidden, false);
assert.match(frame.innerHTML, /Botun İzledikleri <span class="bot-history-count">1<\/span>/);
assert.match(frame.innerHTML, /data-side="SELL">SAT</);
assert.match(frame.innerHTML, /Yeni kesişim · Fiyat <b>1\.16513<\/b> · 0\.05 lot/);
assert.match(frame.innerHTML, /"Şimdi SAT" ile: SL <b>1\.16700<\/b> · TP <b>1\.16233<\/b>/, 'Bota Gönder SL/TP shown');
assert.match(frame.innerHTML, /mevcut sinyali açmaz, sonraki yeni kesişimde 0\.05 lot açar \(SL 200 puan, TP yok\)/,
  'MT5 EA rule: an existing signal is not traded; bot SL/TP settings are shown');
assert.match(frame.innerHTML, /Bot bu sembolde/);
assert.doesNotMatch(node('bot-symbol-status').innerHTML, /Taranan sinyal|bot-added/, 'not inside the symbol status card');

// Stays when a later scan no longer lists the symbol.
vm.runInContext('latestScannedSignals = [];', ctx);
ctx.renderBotSymbolStatus({...bot, is_running: true});
assert.match(node('bot-added-signals').innerHTML, /bir sonraki kapanmış mumda yeni kesişim olursa 0\.05 lot açar/);

// A second added symbol: the bot's symbol is listed first, the other one says the bot is elsewhere.
vm.runInContext(`latestScannedSignals = [{symbol: 'XAUUSD', signal: 'BUY_TREND', side: 'BUY', price: 4165.44, digits: 2, candle_time: 1700000000}];`, ctx);
ctx.rememberAddedSignal('XAUUSD', 60);
ctx.renderBotSymbolStatus(bot);
frame = node('bot-added-signals');
assert.ok(frame.innerHTML.indexOf('data-bot-symbol="EURUSD"') < frame.innerHTML.indexOf('data-bot-symbol="XAUUSD"'));
assert.match(frame.innerHTML, /Bot şu an EURUSD sembolünde/);

// "Sil" removes one; "Temizle" removes all.
ctx.dismissAddedSignal('XAUUSD');
assert.doesNotMatch(node('bot-added-signals').innerHTML, /XAUUSD/);
ctx.clearAddedSignals();
assert.equal(node('bot-added-signals').hidden, true);
console.log('bot added signals frame ok');

// Candle times use the MT5 server clock: a server-time bar of 17/10 21:00 is never shown as 18/10 00:00.
{
  const bar = Date.UTC(2026, 9, 17, 21, 0) / 1000;
  const realNow = Date.UTC(2026, 9, 17, 18, 24);           // 21:24 on a UTC+3 server
  vm.runInContext(`globalThis.__savedNow = Date.now; Date.now = () => ${realNow};`, ctx);  // the app runs in its own realm
  try {
    assert.equal(ctx.brokerCandleText(bar, 10800), '21:00');
    assert.match(ctx.brokerCandleText(Date.UTC(2026, 9, 16, 23, 30) / 1000, 10800), /16.10 23:30/);
  } finally { vm.runInContext('Date.now = globalThis.__savedNow;', ctx); }
}
console.log('broker candle time ok');
