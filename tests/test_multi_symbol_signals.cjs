const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

console.log('Testing multi-symbol and multi-signal bot transfer UI & API...');

const html = fs.readFileSync('app/static/index.html', 'utf8');

// 1. Verify HTML markup
assert.ok(html.includes('id="signal-scan-mode"'), 'Missing signal-scan-mode select in index.html');
assert.ok(!html.includes('id="signal-scan-add-all-btn"'), 'bot follows one symbol; no add-all button');
assert.ok(html.includes('id="bot-symbols-badges"'), 'Missing bot-symbols-badges container in index.html');
assert.ok(html.includes('id="bot-symbols-count"'), 'Missing bot-symbols-count in index.html');
assert.ok(html.includes('id="bot-pending-signals-list"'), 'Missing bot-pending-signals-list in index.html');
assert.ok(html.includes('id="bot-execute-all-signals-btn"'), 'Missing bot-execute-all-signals-btn in index.html');

// 2. VM Environment test
(async () => {
  const nodes = new Map();
  const sentRequests = [];
  const toasts = [];

  const node = (id) => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        id,
        value: '',
        checked: false,
        innerText: '',
        textContent: '',
        innerHTML: '',
        classList: {
          contains: (c) => classes.has(c),
          add: (c) => classes.add(c),
          remove: (c) => classes.delete(c),
          toggle: (c, v) => (v ? classes.add(c) : classes.delete(c)),
        },
      });
    }
    return nodes.get(id);
  };

  const botStatusData = {
    is_running: true,
    symbol: 'EURUSD',
    symbols: ['EURUSD', 'GBPUSD', 'XAUUSD'],
    timeframe_minutes: 15,
    hma_period: 14,
    second_ma_type: 'EMA',
    second_ma_period: 34,
    lot_size: 0.01,
    runtime_state: 'WAITING_SIGNAL',
    runtime_detail: 'Kesişim sinyali bekleniyor.',
    logs: [],
  };

  const botLiveSignalData = {
    available: true,
    symbol: 'EURUSD',
    symbols: ['EURUSD', 'GBPUSD', 'XAUUSD'],
    timeframe_minutes: 15,
    hma: 1.0850,
    ma2: 1.0840,
    trend: 'BUY',
    gap: 0.0010,
    pending_signal_actions: [
      { id: '1', symbol: 'EURUSD', side: 'BUY', price: 1.0850, time: '14:30:00', sl_points: 200, tp_points: 400 },
      { id: '2', symbol: 'GBPUSD', side: 'SELL', price: 1.3050, time: '14:30:15', sl_points: 250, tp_points: 500 },
    ],
  };

  const ctx = vm.createContext({
    console,
    AbortController: globalThis.AbortController,
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      addEventListener() {},
      activeElement: null,
    },
    window: {
      addEventListener() {},
      MT5Markets: { all: [{ name: 'EURUSD' }, { name: 'GBPUSD' }, { name: 'XAUUSD' }], getSymbol() { return null; }, setActive() {} },
      MT5I18n: { language: () => 'tr', translate: (t) => t },
    },
    setInterval() {},
    setTimeout() { return 1; },
    clearTimeout() {},
    fetch: async (url, opts) => {
      sentRequests.push({ url, opts });
      if (url === '/api/bot/status') {
        return { ok: true, json: async () => botStatusData };
      }
      if (url === '/api/bot/live-signal') {
        return { ok: true, json: async () => botLiveSignalData };
      }
      if (url === '/api/bot/add-symbol') {
        const body = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ success: true, symbols: [...botStatusData.symbols, body.symbol] }) };
      }
      if (url === '/api/bot/remove-symbol') {
        const body = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ success: true, symbols: botStatusData.symbols.filter(s => s !== body.symbol) }) };
      }
      if (url === '/api/bot/execute-signal') {
        const body = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ success: true, ticket: 998877, ...body }) };
      }
      if (url.startsWith('/api/signals/scan')) {
        return {
          ok: true,
          json: async () => ({
            timeframe_minutes: 15,
            hma_period: 14,
            ema_period: 34,
            signals: [
              { symbol: 'EURUSD', signal: 'BUY_CROSS', side: 'BUY', price: 1.0850, hma: 1.0852, ema: 1.0848, rsi: 55, atr: 0.0015, suggested_sl: 1.0820, suggested_tp: 1.0895, digits: 5, candle_time: 1700000000 },
              { symbol: 'XAUUSD', signal: 'SELL_CROSS', side: 'SELL', price: 2650.50, hma: 2649.00, ema: 2652.00, rsi: 42, atr: 15.5, suggested_sl: 2680.00, suggested_tp: 2600.00, digits: 2, candle_time: 1700000000 },
            ],
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    },
  });

  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  // The full symbol switch drives the chart/market UI; here only the selected symbol matters.
  vm.runInContext('switchSymbol = function (s) { currentSymbol = s; };', ctx);
  ctx.showToast = (msg, type) => toasts.push({ msg, type });

  // Test 1: only the bot's (selected) symbol is shown, like an MT5 EA on one chart
  await ctx.fetchBotStatus();
  assert.equal(node('bot-symbols-count').textContent, 'EURUSD');
  assert.ok(node('bot-symbols-badges').innerHTML.includes('EURUSD'), 'Badge for EURUSD should exist');
  assert.ok(!node('bot-symbols-badges').innerHTML.includes('GBPUSD'), 'other symbols are not listed');

  // Test 2: "Bota Ekle" makes that symbol the bot symbol (bot stopped)
  botStatusData.is_running = false;
  await ctx.fetchBotStatus();
  await ctx.assignScannerSymbolToBot('BTCUSD', 15);
  const addReq = sentRequests.filter((r) => r.url === '/api/bot/add-symbol').at(-1);
  assert.ok(addReq, 'Must call /api/bot/add-symbol');
  assert.equal(JSON.parse(addReq.opts.body).symbol, 'BTCUSD');
  assert.ok(toasts.some((t) => t.msg.includes('BTCUSD')), 'Toast message for added symbol');

  // Test 3: while running on another symbol, "Bota Ekle" does not change it
  botStatusData.is_running = true;
  await ctx.fetchBotStatus();
  const beforeRunning = sentRequests.filter((r) => r.url === '/api/bot/add-symbol').length;
  await ctx.assignScannerSymbolToBot('GBPUSD', 15);
  assert.equal(sentRequests.filter((r) => r.url === '/api/bot/add-symbol').length, beforeRunning);
  assert.ok(toasts.some((t) => /önce botu durdurun/.test(t.msg)));

  // Test 4: sendSignalDirectToBot calls /api/bot/execute-signal
  await ctx.sendSignalDirectToBot('XAUUSD', 'SELL', 2650.5, {signalId: 'scan-XAUUSD-15-SELL-1700000000', slPrice: 2680.0, tpPrice: 2600.0});
  const execReq = sentRequests.find((r) => r.url === '/api/bot/execute-signal');
  assert.ok(execReq, 'Must call /api/bot/execute-signal');
  const execBody = JSON.parse(execReq.opts.body);
  assert.equal(execBody.symbol, 'XAUUSD');
  assert.equal(execBody.side, 'SELL');
  assert.match(execBody.signal_id, /^scan-XAUUSD-15-SELL-1700000000-[a-z0-9]+$/, 'each click is a new order id (MT5 one-click)');
  const firstId = execBody.signal_id;
  await ctx.sendSignalDirectToBot('XAUUSD', 'SELL', 2650.5, {signalId: 'scan-XAUUSD-15-SELL-1700000000', slPrice: 2680.0, tpPrice: 2600.0});
  const secondId = JSON.parse(sentRequests.filter(r => r.url === '/api/bot/execute-signal').at(-1).opts.body).signal_id;
  assert.notEqual(secondId, firstId, 'a second click is not rejected as a reused id');
  assert.equal(execBody.sl_price, 2680.0, 'Scanner SL is sent as a price');
  assert.equal(execBody.sl_points, undefined, 'Prices and points are never mixed');

  // Bot signals carry points, not prices.
  let mark = sentRequests.length;
  await ctx.sendSignalDirectToBot('EURUSD', 'BUY', 1.085, {signalId: 'EURUSD-BUY-1', slPoints: 200, tpPoints: 400});
  const pointsBody = JSON.parse(sentRequests.slice(mark).find((r) => r.url === '/api/bot/execute-signal').opts.body);
  assert.equal(pointsBody.sl_points, 200);
  assert.equal(pointsBody.sl_price, undefined, 'Points must not be sent as SL price');

  // A signal without a stable id is not sent (no idempotency key).
  mark = sentRequests.length;
  await ctx.sendSignalDirectToBot('EURUSD', 'BUY', 1.085, {});
  assert.ok(!sentRequests.slice(mark).some((r) => r.url === '/api/bot/execute-signal'), 'Missing signal id blocks submission');
  assert.ok(toasts.some((t) => t.msg.includes('998877')), 'Toast shows execution success with ticket');

  // Test 5: refreshSignalScanner in multi mode
  node('signal-scan-mode').value = 'ALL';
  await ctx.refreshSignalScanner(true);
  const scannedList = ctx.window.latestScannedSignals || ctx.latestScannedSignals || [];
  assert.ok(scannedList.length >= 2, 'Should scan multiple signals');
  // MT5 Market Watch rule: only the selected symbol and the user's ★ list, never a built-in list.
  const scanUrl = decodeURIComponent(sentRequests.filter((r) => r.url.startsWith('/api/signals/scan')).at(-1).url);
  const scanned = scanUrl.match(/symbols=([^&]*)/)[1].split(',');
  assert.ok(!scanned.includes('USDJPY'), 'USDJPY is not scanned unless the user picked it');
  ctx.window.MT5Markets.favorites = () => ['USDJPY', 'XAUUSD'];
  await ctx.refreshSignalScanner(true);
  const withStars = decodeURIComponent(sentRequests.filter((r) => r.url.startsWith('/api/signals/scan')).at(-1).url).match(/symbols=([^&]*)/)[1].split(',');
  assert.ok(withStars.includes('USDJPY') && withStars.includes('XAUUSD'), '★ symbols are scanned');

  // Test 6: fetchBotLiveSignal renders multiple pending signals
  await ctx.fetchBotLiveSignal();
  assert.ok(!node('bot-pending-signal-box').classList.contains('hidden'), 'Pending signal box should be visible');
  assert.ok(node('bot-pending-signals-list').innerHTML.includes('EURUSD'), 'Pending list includes EURUSD');
  assert.ok(node('bot-pending-signals-list').innerHTML.includes('GBPUSD'), 'Pending list includes GBPUSD');

  console.log('Multi-symbol and multi-signal bot transfer: ALL TESTS PASS');
})();
