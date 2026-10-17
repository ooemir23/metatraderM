const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// Signal filters, presets and history-test rendering in the settings dialog.
function load(stored = {}) {
  const store = new Map(Object.entries(stored).map(([k, v]) => [k, JSON.stringify(v)]));
  const inputs = {};
  const field = (name, type = 'number') => (inputs[name] = {name, type, value: '', checked: false, disabled: false, tagName: type === 'select' ? 'SELECT' : 'INPUT'});
  ['hma_period', 'ema_period', 'rsi_period', 'atr_period', 'rsi_buy_max', 'rsi_sell_min', 'sl_atr', 'tp_atr', 'adx_period', 'adx_min',
   'max_spread_points', 'min_rr', 'cooldown_bars', 'volume_mult', 'stats_bars'].forEach((n) => field(n));
  field('ma_type', 'select'); field('htf_minutes', 'select');
  ['confirm_close', 'rsi_filter', 'only_crosses', 'htf_filter', 'adx_filter', 'volume_filter', 'show_stats',
   'session_ASIA', 'session_LONDON', 'session_NEWYORK'].forEach((n) => field(n, 'checkbox'));
  const nodes = {
    'signal-settings-form': {elements: {namedItem: (n) => inputs[n] || null}, addEventListener() {}},
    'signal-settings-summary': {textContent: ''}, 'signal-settings-explain': {innerHTML: ''},
    'signal-settings-preview': {innerHTML: ''}, 'signal-settings-status': {textContent: ''},
    'signal-settings-dialog': {open: false, showModal() { this.open = true; }, close() { this.open = false; }},
    'signal-preset-select': {innerHTML: '', value: ''}, 'signal-preset-name': {value: ''}, 'signal-preset-delete': {disabled: true},
    'signal-test-result': {innerHTML: '', textContent: ''}, 'signal-scan-tf': {value: '60'},
  };
  const fetches = [];
  const ctx = vm.createContext({
    URLSearchParams, JSON, Number, Object, Array, String, encodeURIComponent,
    document: {readyState: 'complete', getElementById: (id) => nodes[id] || null, addEventListener() {}},
    localStorage: {getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)},
    window: {MT5Chart: {setSignalPeriods() {}}},
    currentSymbol: 'XAUUSD',
    fetch: async (url) => { fetches.push(url); return {ok: true, json: async () => ({signals: [{symbol: 'XAUUSD', stats: {
      available: true, bars: 500, passed: 12, crosses: 30, filtered: 18, filter_counts: {htf: 10, adx: 8},
      wins: 5, losses: 6, open: 1, win_rate_pct: 45.5, avg_r: 0.14, small_sample: true}}]})}; },
    refreshSignalScanner() {}, showToast() {},
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync('app/static/signal-settings.js', 'utf8'), ctx);
  return {api: ctx.window.HMASignalSettings, inputs, nodes, store, fetches};
}

(async () => {
  let s = load();
  assert.equal(s.api.get().show_stats, true, 'history stats on by default');
  assert.match(s.api.query(), /htf_filter=false.*adx_filter=false/);
  assert.match(s.api.query(), /stats=true&stats_bars=500/);
  assert.doesNotMatch(s.api.query(), /sessions=/, 'no session filter by default');
  assert.match(s.nodes['signal-settings-summary'].textContent, /filtre yok/);

  s.api.open();
  assert.match(s.nodes['signal-preset-select'].innerHTML, /Temkinli/);
  assert.equal(s.inputs.adx_min.disabled, true, 'ADX inputs follow the ADX switch');
  s.api.applyPreset('builtin:cautious');
  assert.equal(s.inputs.htf_filter.checked, true);
  assert.equal(s.inputs.adx_min.value, '25');
  assert.equal(s.inputs.session_LONDON.checked, true);
  assert.equal(s.inputs.session_ASIA.checked, false);
  assert.equal(s.inputs.hma_period.value, '14', 'presets keep the crossover periods');
  assert.equal(s.api.save(), true);
  const q = s.api.query();
  assert.match(q, /htf_filter=true/); assert.match(q, /adx_min=25/); assert.match(q, /cooldown_bars=5/);
  assert.match(q, /min_rr=1.5/); assert.match(q, /volume_filter=true&volume_mult=1.2/);
  assert.match(q, /sessions=LONDON%2CNEWYORK/);
  assert.match(s.nodes['signal-settings-summary'].textContent, /7 filtre açık/);
  assert.match(s.nodes['signal-settings-explain'].innerHTML, /otomatik üst zaman dilimi/);
  assert.match(s.nodes['signal-settings-explain'].innerHTML, /Londra, New York/);

  // Custom presets: save, reload, apply, delete.
  s.api.open();
  s.inputs.adx_min.value = '30';
  s.nodes['signal-preset-name'].value = 'Altın H1';
  assert.equal(s.api.savePreset(), true);
  assert.equal(JSON.parse(s.store.get('hma.signal.presets.v1'))['Altın H1'].adx_min, 30);
  s = load({'hma.signal.presets.v1': JSON.parse(s.store.get('hma.signal.presets.v1'))});
  s.api.open();
  assert.match(s.nodes['signal-preset-select'].innerHTML, /Altın H1/);
  s.api.applyPreset('custom:Altın H1');
  assert.equal(s.inputs.adx_min.value, '30');
  assert.equal(s.nodes['signal-preset-delete'].disabled, false);
  s.nodes['signal-preset-select'].value = 'custom:Altın H1';
  assert.equal(s.api.deletePreset(), true);
  assert.equal(Object.keys(JSON.parse(s.store.get('hma.signal.presets.v1'))).length, 0);
  s.nodes['signal-preset-name'].value = '';
  assert.equal(s.api.savePreset(), false, 'a preset needs a name');

  // Invalid filter values are rejected like the other fields.
  s.api.open(); s.inputs.cooldown_bars.value = '500';
  assert.equal(s.api.save(), false);
  assert.match(s.nodes['signal-settings-status'].textContent, /cooldown_bars/);

  // History test runs for the chart symbol and scanner timeframe with the form's settings.
  s = load(); s.api.open(); s.inputs.adx_filter.checked = true;
  const stats = await s.api.testHistory();
  assert.equal(stats.passed, 12);
  assert.match(s.fetches.at(-1), /symbols=XAUUSD&timeframe_minutes=60/);
  assert.match(s.fetches.at(-1), /adx_filter=true/); assert.match(s.fetches.at(-1), /stats=true/);
  const html = s.nodes['signal-test-result'].innerHTML;
  assert.match(html, /%45.5 kazanma/); assert.match(html, /\+0.14R/); assert.match(html, /Üst zaman dilimi 10, ADX 8/);
  assert.match(html, /az örnek/);
  assert.match(s.api.statsHtml({available: false, reason: 'SL ve TP gerekli'}), /SL ve TP/);

  // Stored garbage is ignored.
  s = load({'hma.signal.settings.v1': {sessions: ['TOKYO', 'ASIA'], htf_minutes: 7, min_rr: -2, show_stats: 'x'}});
  assert.deepEqual([...s.api.get().sessions], ['ASIA']);
  assert.equal(s.api.get().htf_minutes, 0); assert.equal(s.api.get().min_rr, 0); assert.equal(s.api.get().show_stats, true);
  console.log('Signal filters UI: ALL TESTS PASS');
})().catch((err) => { console.error(err); process.exit(1); });
