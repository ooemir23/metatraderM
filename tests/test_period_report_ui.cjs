// Period report UI: renders the broker's rows as given (zero periods stay zero), loads the API with the
// period's limit, throttles polling and can be reset on account switch.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = {}, calls = [], store = new Map();
const node = id => nodes[id] || (nodes[id] = {innerHTML: '', disabled: false});
const g = {console, Number, Math, Date, Blob, String, Array, URL: {createObjectURL: () => 'blob:', revokeObjectURL() {}},
  setTimeout, localStorage: {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)},
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, createElement: () => ({click() {}, remove() {}}), body: {appendChild() {}}},
  window: {}, fetch: async url => { calls.push(url); return {ok: true, json: async () => report}; }};
g.window = g;
const report = {period: 'week', currency: 'USD', basis: 'Gün = broker sunucu günü', incomplete_position_ids: [],
  summary: {current: 0, previous: -52, average: -26, profitable_periods: 0, profitable_ratio: 0, total: -52},
  rows: [{key: '2026-10-19', label: '19.10 – 25.10.2026', trades: 0, wins: 0, losses: 0, win_rate: 0, gross_profit: 0, gross_loss: 0, commission: 0, swap: 0, fee: 0, net: 0, profit_factor: 0, best: null, worst: null, cumulative: -52},
         {key: '2026-10-12', label: '12.10 – 18.10.2026', trades: 1, wins: 0, losses: 1, win_rate: 0, gross_profit: 0, gross_loss: -52, commission: 0, swap: -2, fee: 0, net: -52, profit_factor: 0, best: -52, worst: -52, cumulative: -52}]};
vm.runInNewContext(fs.readFileSync('app/static/period-report.js', 'utf8'), g);
(async () => {
  const api = g.HMAPeriodReport;
  await api.setPeriod('week');
  assert.deepEqual(calls, ['/api/reports?period=week&limit=12']);
  const html = node('period-report-body').innerHTML;
  assert.match(html, /19\.10 – 25\.10\.2026/);
  assert.match(html, /USD 0,00/, 'an empty period shows zero');
  assert.match(html, /-USD 52,00/);
  assert.equal((html.match(/<tr>/g) || []).length, 3, 'header + one row per broker period, nothing invented');
  assert.equal(node('period-report-csv').disabled, false);
  await api.load();
  assert.equal(calls.length, 1, 'polling within a minute does not reread the broker report');
  api.reset(); await api.load();
  assert.equal(calls.length, 2, 'account switch forces a fresh report');
  await api.setPeriod('quarter');
  assert.equal(calls.length, 2, 'unknown period is ignored');
  console.log('Period report UI: API limits, zero periods, throttle and reset PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
