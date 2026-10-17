const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// Bot timeframe follows the chart: applied when stopped, never while running; H4 filter dropped off H1.
(async () => {
  const requests = [];
  const toasts = [];
  let botState = {is_running: false, timeframe_minutes: 1, use_h4_filter: true};
  const node = () => ({value: '', textContent: '', innerHTML: '', classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  const ctx = vm.createContext({
    console, AbortController, URLSearchParams,
    document: {getElementById: () => null, querySelectorAll: () => [], addEventListener() {}, readyState: 'complete', activeElement: null},
    window: {addEventListener() {}, MT5Chart: {getTimeframe: () => 5}, MT5I18n: {language: () => 'tr', translate: (t) => t}},
    localStorage: {getItem: () => null, setItem() {}}, setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    fetch: async (url, opts) => {
      requests.push({url, body: opts?.body ? JSON.parse(opts.body) : null});
      if (url === '/api/bot/config') { botState = {...botState, ...JSON.parse(opts.body)}; return {ok: true, json: async () => botState}; }
      return {ok: true, json: async () => ({})};
    },
  });
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  vm.runInContext('showToast = (m, t) => globalThis.__toasts.push(m)', Object.assign(ctx, {__toasts: toasts}));

  vm.runInContext('latestBotSettings = {is_running: false, timeframe_minutes: 1, use_h4_filter: true}', ctx);
  assert.equal(await ctx.syncBotTimeframe(5), true);
  const cfg = requests.find((r) => r.url === '/api/bot/config');
  assert.deepEqual(cfg.body, {timeframe_minutes: 5, use_h4_filter: false}, 'stopped bot follows chart; H4 filter off');
  assert.ok(toasts.some((m) => m.includes('M5')));

  requests.length = 0;
  vm.runInContext('latestBotSettings = {is_running: true, timeframe_minutes: 1}', ctx);
  assert.equal(await ctx.syncBotTimeframe(15), false);
  assert.equal(requests.length, 0, 'running bot is never reconfigured');

  vm.runInContext('latestBotSettings = {is_running: false, timeframe_minutes: 5}', ctx);
  assert.equal(await ctx.syncBotTimeframe(5), true);
  assert.equal(requests.length, 0, 'no request when already aligned');
  assert.equal(await ctx.syncBotTimeframe(7), false, 'unsupported timeframe ignored');
  console.log('Bot timeframe sync: ALL TESTS PASS');
})().catch((err) => { console.error(err); process.exit(1); });
