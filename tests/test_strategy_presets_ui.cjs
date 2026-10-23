const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
// Strategies dialog: "Manuel işlemlerim" switches the bot to the manual-style entry; a preset switches it back.
(async () => {
  const sent = [], toasts = [], nodes = {};
  const node = (id) => (nodes[id] ||= {id, innerHTML: '', textContent: '', value: '', open: false, showModal() { this.open = true; }, close() { this.open = false; }});
  let bot = {symbol: 'XAUUSD', entry_mode: 'CROSSOVER', is_running: true, copy_manual_trades: true, manual_copy: {available: true, lot: 22.5}};
  let periods = null;
  const ctx = vm.createContext({
    console, URLSearchParams, AbortController,
    document: {getElementById: node},
    window: {MT5Chart: {getTimeframe: () => 60}, HMASignalSettings: {get: () => ({fast_type: 'HMA', hma_period: 14, ma_type: 'EMA', ema_period: 34}), setPeriods: (p) => { periods = p; return true; }}},
    showToast: (m, t) => toasts.push([m, t]),
    fetchBotStatus() {}, syncBotToSelection: async () => true,
    fetch: async (url, opts) => {
      sent.push({url, body: opts?.body ? JSON.parse(opts.body) : null});
      if (url === '/api/bot/follow') { bot = {...bot, ...JSON.parse(opts.body)}; return {ok: true, json: async () => bot}; }
      if (url.startsWith('/api/manual-style')) return {ok: true, json: async () => ({symbol: 'XAUUSD', timeframe_minutes: 60, status: 'OK', decisions: 43, wins: 30,
        verdict: {reliable: false, reason: 'fark şans olabilir'}, history_test: {signals: {count: 20, hit_rate: .55}, baseline: {hit_rate: .5}}, signal: {side: 'HOLD'}})};
      if (url.startsWith('/api/strategy-presets')) return {ok: true, json: async () => ({symbol: 'XAUUSD', timeframe_minutes: 60, candles: 2000, from_time: 1, to_time: 2,
        sl_points: 200, tp_points: 289, exit_source: 'manual_copy', spread_points: 20, commission_points: 0, disclaimer: 'garanti etmez',
        presets: [{id: 'ema-9-21', name: 'EMA 9 × EMA 21', fast_type: 'EMA', fast: 9, slow_type: 'EMA', slow: 21, available: true, net_points: 100, trades: 30, win_rate_pct: 50, avg_points: 3, max_drawdown_points: 50, out_of_sample: {net_points: 10, count: 9}}]})};
      return {ok: true, json: async () => ({})};
    },
  });
  Object.defineProperty(ctx, 'latestBotSettings', {get: () => bot, set: (v) => { bot = v; }});
  ctx.currentSymbol = 'XAUUSD';
  vm.runInContext(fs.readFileSync('app/static/strategy-presets.js', 'utf8'), ctx);
  const SP = ctx.window.StrategyPresets;
  SP.open();
  await new Promise((r) => setTimeout(r, 10));
  assert.match(node('sp-manual').innerHTML, /Manuel işlemlerim/);
  assert.match(node('sp-manual').innerHTML, /43 manuel karar/);
  assert.match(node('sp-manual').innerHTML, /deneysel/);
  await SP.applyManual();
  assert.deepEqual(sent.filter((r) => r.url === '/api/bot/follow').at(-1).body, {entry_mode: 'MANUAL_STYLE', copy_manual_trades: true});
  assert.equal(bot.entry_mode, 'MANUAL_STYLE');
  await SP.apply('ema-9-21');
  assert.deepEqual(sent.filter((r) => r.url === '/api/bot/follow').at(-1).body, {entry_mode: 'CROSSOVER', copy_manual_trades: false});
  assert.deepEqual(JSON.parse(JSON.stringify(periods)), {fast_type: 'EMA', hma_period: 9, ma_type: 'EMA', ema_period: 21});
  console.log('Strategies dialog: manual-style entry on top, presets switch back PASS');
})().catch((err) => { console.error(err); process.exit(1); });
