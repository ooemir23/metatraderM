const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// Signal settings: sanitising, persistence, query string, chart sync, explanation, validation.
function load(stored, {blockStorage = false} = {}) {
  const store = new Map(stored ? [['hma.signal.settings.v1', JSON.stringify(stored)]] : []);
  const inputs = {};
  const field = (name, type = 'number') => (inputs[name] = {name, type, value: '', checked: false, disabled: false, tagName: type === 'select' ? 'SELECT' : 'INPUT'});
  ['hma_period', 'ema_period', 'rsi_period', 'atr_period', 'rsi_buy_max', 'rsi_sell_min', 'sl_atr', 'tp_atr'].forEach((n) => field(n));
  field('ma_type', 'select'); ['confirm_close', 'rsi_filter', 'only_crosses'].forEach((n) => field(n, 'checkbox'));
  const nodes = {
    'signal-settings-form': {elements: {namedItem: (n) => inputs[n] || null}, addEventListener() {}},
    'signal-settings-summary': {textContent: ''}, 'signal-settings-explain': {innerHTML: ''},
    'signal-settings-preview': {innerHTML: ''}, 'signal-settings-status': {textContent: ''},
    'signal-settings-dialog': {open: false, showModal() { this.open = true; }, close() { this.open = false; }},
  };
  const chart = [];
  const calls = [];
  const ctx = vm.createContext({
    URLSearchParams, JSON, Number, Object,
    document: {readyState: 'complete', getElementById: (id) => nodes[id] || null, addEventListener() {}},
    localStorage: {getItem: (k) => { if (blockStorage) throw new Error('blocked'); return store.get(k) ?? null; },
                   setItem: (k, v) => { if (blockStorage) throw new Error('blocked'); store.set(k, v); }},
    window: {MT5Chart: {setSignalPeriods: (...a) => chart.push(a)}},
    refreshSignalScanner: (...a) => calls.push(a), showToast() {},
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync('app/static/signal-settings.js', 'utf8'), ctx);
  return {api: ctx.window.HMASignalSettings, inputs, nodes, store, chart, calls};
}

let s = load(null);
assert.equal(s.api.get().hma_period, 14);
assert.equal(s.api.get().confirm_close, true, 'closed-candle confirmation by default');
assert.match(s.api.query(), /hma_period=14&ema_period=34&ma_type=EMA/);
assert.match(s.nodes['signal-settings-summary'].textContent, /HMA\(14\) × EMA\(34\)/);
assert.match(s.nodes['signal-settings-explain'].innerHTML, /kapanmış mumda/);
assert.deepEqual(s.chart.at(-1), [14, 34, 'EMA', true], 'chart markers synced on load');

s.api.open();
assert.equal(s.nodes['signal-settings-dialog'].open, true);
assert.equal(s.inputs.hma_period.value, '14');
s.inputs.hma_period.value = '21'; s.inputs.ma_type.value = 'SMA'; s.inputs.ema_period.value = '50';
s.inputs.rsi_filter.checked = true; s.inputs.rsi_buy_max.value = '65';
assert.equal(s.api.save(), true);
assert.equal(s.api.get().hma_period, 21);
assert.equal(s.api.get().ma_type, 'SMA');
assert.match(s.api.query(), /ma_type=SMA/);
assert.match(s.api.query(), /rsi_filter=true&rsi_buy_max=65/);
assert.equal(JSON.parse(s.store.get('hma.signal.settings.v1')).ema_period, 50, 'persisted');
assert.deepEqual(s.chart.at(-1), [21, 50, 'SMA', true], 'non-EMA slow MA is drawn and used by chart markers');
assert.deepEqual(s.calls.at(-1), [true, true], 'saving rescans immediately');
assert.equal(s.nodes['signal-settings-dialog'].open, false);

s.api.open(); s.inputs.hma_period.value = '1';
assert.equal(s.api.save(), false, 'invalid value rejected');
assert.match(s.nodes['signal-settings-status'].textContent, /hma_period/);
assert.equal(s.api.get().hma_period, 21, 'previous valid settings kept');

s = load({hma_period: 999, ma_type: 'XYZ', confirm_close: 'yes', sl_atr: 1.5});
assert.equal(s.api.get().hma_period, 14, 'out-of-range stored value ignored');
assert.equal(s.api.get().ma_type, 'EMA');
assert.equal(s.api.get().confirm_close, true);
assert.equal(s.api.get().sl_atr, 1.5);

s = load(null, {blockStorage: true});
s.api.open(); s.inputs.ema_period.value = '40';
assert.equal(s.api.save(), true, 'works when storage is blocked');
assert.equal(s.api.get().ema_period, 40);
console.log('Signal settings UI: ALL TESTS PASS');
