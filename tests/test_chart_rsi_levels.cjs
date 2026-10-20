// Chart RSI = MT5 RSI.mq5 (iRSI) and trade-level lines follow the open positions of the chart symbol.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const g = {console, Number, Map, window: {}, localStorage: {getItem: () => null, setItem() {}},
  document: {getElementById: () => null, querySelectorAll: () => [], addEventListener() {}, removeEventListener() {}}};
vm.runInNewContext(fs.readFileSync('app/static/mt5-chart.js', 'utf8'), g);
const api = g.window.MT5Chart;
const rows = c => c.map((close, i) => ({time: i + 1, open: close, high: close, low: close, close, tick_volume: 1}));

// Reference values written out from RSI.mq5: simple mean of the first p changes, then (prev*(p-1)+x)/p.
const closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64];
const p = 14; let up = 0, dn = 0;
for (let i = 1; i <= p; i++) { const d = closes[i] - closes[i - 1]; up += Math.max(d, 0); dn += Math.max(-d, 0); }
up /= p; dn /= p; const expected = [100 - 100 / (1 + up / dn)];
for (let i = p + 1; i < closes.length; i++) { const d = closes[i] - closes[i - 1]; up = (up * 13 + Math.max(d, 0)) / p; dn = (dn * 13 + Math.max(-d, 0)) / p; expected.push(100 - 100 / (1 + up / dn)); }
const rsi = api.rsiValues(rows(closes), 14);
assert.equal(rsi.length, closes.length - 14, 'first RSI value is on bar `period`, like MT5');
assert.equal(rsi[0].time, 15);
rsi.forEach((r, i) => assert.ok(Math.abs(r.value - expected[i]) < 1e-9));
assert.ok(Math.abs(rsi[0].value - 70.46) < 0.01, 'classic Wilder example: 70.46');
assert.equal(api.rsiValues(rows([1, 2, 3, 4]), 3)[0].value, 100, 'no losses -> 100');
assert.equal(api.rsiValues(rows([5, 5, 5, 5]), 3)[0].value, 50, 'no movement -> 50');
assert.equal(api.rsiValues(rows([1, 2]), 3).length, 0, 'not enough bars -> no value');
assert.equal(typeof api.setTradeLevels, 'function');
api.setTradeLevels([{ticket: 1, symbol: 'XAUUSD', type: 'BUY', volume: 0.1, price_open: 2648, sl: 2600, tp: 2700}, {ticket: 2, type: 'BALANCE'}]);
console.log('Chart RSI (MT5 iRSI) and trade levels API PASS');
