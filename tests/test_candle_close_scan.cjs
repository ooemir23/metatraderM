// Scanner scans at the candle close on the broker server clock (EA new-bar moment).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', hidden: true, dataset: {}, value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  return nodes.get(id);
};
const timers = [];
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController,
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, MT5Markets: {all: []}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);

// Server UTC+3, M5. Local 16:12:30 UTC = 19:12:30 server -> close at 19:15:00 server = 150 s.
const now = Date.UTC(2026, 9, 18, 16, 12, 30);
assert.equal(ctx.nextCandleCloseDelayMs(5, 10800, now), 150000);
// H4 bars start at 00/04/08/12/16/20 server time: 19:12:30 server -> 20:00:00.
assert.equal(ctx.nextCandleCloseDelayMs(240, 10800, now), (47 * 60 + 30) * 1000);
// D1 closes at server midnight, not UTC midnight.
assert.equal(ctx.nextCandleCloseDelayMs(1440, 10800, now), (4 * 3600 + 47 * 60 + 30) * 1000);
// Right at a close the timer aims at the next one, never the close just passed.
assert.equal(ctx.nextCandleCloseDelayMs(5, 0, Date.UTC(2026, 9, 18, 16, 15, 0)), 300000);

node('signal-scan-tf').value = '5';
vm.runInContext('latestBotSettings = {clock_offset_seconds: 10800};', ctx);
timers.length = 0;
ctx.scheduleCandleCloseScan();
assert.equal(timers.length, 1);
assert.ok(timers[0] > 0 && timers[0] <= 300000);
console.log('candle close scan ok');
