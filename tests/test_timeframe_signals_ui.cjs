const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

console.log('Testing candle timeframe controls and signal synchronization...');

const htmlContent = fs.readFileSync('app/static/index.html', 'utf8');

// 1. Verify HTML elements
assert(htmlContent.includes('id="chart-timeframe-toolbar"'), 'Missing chart-timeframe-toolbar in index.html');
assert(htmlContent.includes('data-tf="1"'), 'Missing 1m button');
assert(htmlContent.includes('data-tf="5"'), 'Missing 5m button');
assert(htmlContent.includes('data-tf="15"'), 'Missing 15m button');
assert(htmlContent.includes('data-tf="30"'), 'Missing 30m button');
assert(htmlContent.includes('data-tf="60"'), 'Missing 1h button');
assert(htmlContent.includes('data-tf="240"'), 'Missing 4h button');
assert(htmlContent.includes('data-tf="1440"'), 'Missing 1d button');

// Verify signal-scan-tf options
assert(htmlContent.includes('<option value="1">1 dk (M1)</option>'), 'Missing M1 in signal scanner');
assert(htmlContent.includes('<option value="30">30 dk (M30)</option>'), 'Missing M30 in signal scanner');
assert(htmlContent.includes('<option value="1440">1 gün (D1)</option>'), 'Missing D1 in signal scanner');

// 2. Mock environment for mt5-chart.js
function element() {
  return {
    textContent: '',
    innerText: '',
    value: '',
    hidden: true,
    dataset: {},
    style: {},
    className: '',
    offsetWidth: 290,
    offsetHeight: 400,
    events: {},
    addEventListener(name, fn) { this.events[name] = fn; },
    removeEventListener(name) { delete this.events[name]; },
    querySelector: () => element(),
    querySelectorAll: () => [],
    contains: () => false,
    focus() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 500 })
  };
}

const host = element();
const nodes = new Map();
host.querySelector = sel => {
  if (!nodes.has(sel)) nodes.set(sel, element());
  return nodes.get(sel);
};

const storage = new Map();
const globals = {
  console,
  URL: { createObjectURL() { return 'blob:test'; }, revokeObjectURL() {} },
  Blob,
  AbortController,
  Number,
  Map,
  setTimeout: () => 1,
  clearTimeout() {},
  localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) },
  document: {
    hidden: false,
    getElementById: id => (id === 'tradingview-container' ? host : element()),
    addEventListener() {},
    removeEventListener() {},
    body: { appendChild() {} },
    createElement: () => ({ click() {}, remove() {} })
  },
  window: {},
  LightweightCharts: {
    CandlestickSeries: 'candles', BarSeries: 'bars', LineSeries: 'line', AreaSeries: 'area', HistogramSeries: 'volume',
    createChart() {
      return {
        series: [],
        removed: false,
        applyOptions() {},
        remove() { this.removed = true; },
        removeSeries() {},
        timeScale: () => ({ fitContent() {}, setVisibleLogicalRange() {}, scrollToRealTime() {} }),
        takeScreenshot: () => ({ toBlob: fn => fn(new Blob(['PNG'], { type: 'image/png' })) }),
        addSeries(type) {
          return {
            type, data: [], options: {},
            applyOptions(o) { Object.assign(this.options, o); },
            setData(r) { this.data = r; },
            priceScale: () => ({ applyOptions() {} }),
            createPriceLine: () => ({}),
            coordinateToPrice: () => 1
          };
        }
      };
    }
  }
};
globals.window.LightweightCharts = globals.LightweightCharts;

vm.runInNewContext(fs.readFileSync('app/static/mt5-chart.js', 'utf8'), globals);
const api = globals.window.MT5Chart;

assert(api, 'window.MT5Chart must be defined');
assert.strictEqual(typeof api.getTimeframe, 'function', 'getTimeframe must be a function');
assert.strictEqual(typeof api.setTimeframe, 'function', 'setTimeframe must be a function');
assert.strictEqual(api.frames[1], 'M1', 'Frame 1 must be M1');
assert.strictEqual(api.frameLabels[1], '1 dk', 'FrameLabel 1 must be 1 dk');
assert.strictEqual(api.frameLabels[15], '15 dk', 'FrameLabel 15 must be 15 dk');
assert.strictEqual(api.frameLabels[60], '1 sa', 'FrameLabel 60 must be 1 sa');

// Check initial timeframe
const initialTf = api.getTimeframe();
assert([1, 5, 15, 30, 60, 240, 1440].includes(initialTf), 'Initial timeframe must be valid');

// Set to 1m
api.setTimeframe(1);
assert.strictEqual(api.getTimeframe(), 1, 'Timeframe must be set to 1m');

// Set to 15m
api.setTimeframe(15);
assert.strictEqual(api.getTimeframe(), 15, 'Timeframe must be set to 15m');

console.log('Candle timeframe and signal synchronization: ALL TESTS PASS');
