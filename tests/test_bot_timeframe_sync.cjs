const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// The bot follows the chart timeframe and the scanner's HMA × MA, also while running
// (MT5: an EA re-initialises when its chart period or inputs change).
(async () => {
  const requests = [];
  const toasts = [];
  let botState = {is_running: false, symbol: 'XAUUSD', symbols: ['XAUUSD'], timeframe_minutes: 1,
    hma_period: 75, second_ma_type: 'EMA', second_ma_period: 34};
  let signal = {hma_period: 14, ma_type: 'EMA', ema_period: 34};
  let failNext = null;
  const ctx = vm.createContext({
    console, AbortController, URLSearchParams,
    document: {getElementById: () => null, querySelectorAll: () => [], addEventListener() {}, readyState: 'complete', activeElement: null},
    window: {addEventListener() {}, MT5Chart: {getTimeframe: () => 5}, MT5I18n: {language: () => 'tr', translate: (t) => t},
      HMASignalSettings: {get: () => ({...signal})}},
    localStorage: {getItem: () => null, setItem() {}}, setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    fetch: async (url, opts) => {
      requests.push({url, body: opts?.body ? JSON.parse(opts.body) : null});
      if (url === '/api/bot/follow') {
        if (failNext) { const detail = failNext; failNext = null; return {ok: false, json: async () => ({detail})}; }
        const body = JSON.parse(opts.body);
        botState = {...botState, ...body, ...(body.symbol ? {symbols: [body.symbol]} : {})};
        return {ok: true, json: async () => ({...botState})};
      }
      return {ok: true, json: async () => ({})};
    },
  });
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  vm.runInContext('showToast = (m, t) => globalThis.__toasts.push(m)', Object.assign(ctx, {__toasts: toasts}));
  vm.runInContext('currentSymbol = "XAUUSD"', ctx);
  const follows = () => requests.filter((r) => r.url === '/api/bot/follow');

  // Stopped bot: chart timeframe + scanner crossover are applied in one request.
  vm.runInContext(`latestBotSettings = ${JSON.stringify(botState)}`, ctx);
  assert.equal(await ctx.syncBotTimeframe(5), true);
  assert.deepEqual(follows().at(-1).body, {timeframe_minutes: 5, hma_period: 14});

  // Running bot follows a scanner change too.
  requests.length = 0;
  botState.is_running = true;
  vm.runInContext(`latestBotSettings = ${JSON.stringify(botState)}`, ctx);
  signal = {hma_period: 21, ma_type: 'SMA', ema_period: 50};
  assert.equal(await ctx.syncBotToSelection({}, {announce: false}), true);
  assert.deepEqual(follows().at(-1).body, {hma_period: 21, second_ma_type: 'SMA', second_ma_period: 50});
  assert.ok(toasts.some((m) => /Bot seçimi izledi: XAUUSD M5 · HMA\(21\) × SMA\(50\)/.test(m)));

  // No request when already aligned; unsupported timeframe ignored.
  requests.length = 0;
  assert.equal(await ctx.syncBotTimeframe(5), true);
  assert.equal(requests.length, 0, 'no request when already aligned');
  assert.equal(await ctx.syncBotTimeframe(7), false, 'unsupported timeframe ignored');

  // A refused change is shown once and not retried on every status poll.
  signal = {hma_period: 30, ma_type: 'SMA', ema_period: 50};
  failNext = 'XAUUSD M5 için bot hazır değil';
  assert.equal(await ctx.syncBotToSelection({}, {announce: false, retry: false}), false);
  const n = follows().length;
  assert.equal(await ctx.syncBotToSelection({}, {announce: false, retry: false}), false);
  assert.equal(follows().length, n, 'no retry loop');
  assert.ok(toasts.some((m) => m.includes('hazır değil')));
  console.log('Bot follows selection: ALL TESTS PASS');
})().catch((err) => { console.error(err); process.exit(1); });
