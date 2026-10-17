const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// A slow scan for the old symbol must not repaint the list after a symbol switch.
(async () => {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {id, value: '', checked: false, textContent: '', innerHTML: '', innerText: '',
      classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
    return nodes.get(id);
  };
  const pending = [];
  const ctx = vm.createContext({
    console, AbortController: globalThis.AbortController,
    document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
    window: {addEventListener() {}, MT5I18n: {language: () => 'tr', translate: (t) => t}},
    setInterval() {}, setTimeout() { return 1; }, clearTimeout() {}, localStorage: {getItem: () => null, setItem() {}},
    fetch: (url, opts) => new Promise((resolve, reject) => {
      const symbols = decodeURIComponent(url.split('symbols=')[1].split('&')[0]).split(',');
      opts?.signal?.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
      pending.push({symbols, resolve: (rows) => resolve({ok: true, json: async () => ({timeframe_minutes: 15, signals: rows})})});
    }),
  });
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  node('signal-scan-tf').value = '15';
  node('signal-scan-mode').value = 'SELECTED';

  vm.runInContext("currentSymbol = 'EURUSD'", ctx);
  const oldScan = ctx.refreshSignalScanner(false);
  vm.runInContext("currentSymbol = 'XAUUSD'", ctx);
  const newScan = ctx.refreshSignalScanner(true, true);
  assert.equal(pending.length, 2);
  assert.deepEqual(pending[1].symbols, ['XAUUSD']);

  pending[1].resolve([{symbol: 'XAUUSD', signal: 'SELL_TREND', side: 'SELL', price: 2650.5, digits: 2, candle_time: 1}]);
  await newScan;
  // Old request is aborted; even if a server answered, the older sequence is ignored.
  pending[0].resolve([{symbol: 'EURUSD', signal: 'BUY_TREND', side: 'BUY', price: 1.08, digits: 5, candle_time: 1}]);
  await oldScan;
  const html = node('signal-scanner-list').innerHTML;
  assert.ok(html.includes('XAUUSD') && !html.includes('EURUSD'), 'list must show the newly selected symbol only');
  assert.ok(html.includes('SEÇİLİ'), 'selected symbol is marked');

  // Neutral rows are labelled neutral and cannot be sent to the bot.
  const neutral = ctx.refreshSignalScanner(true, true);
  pending[2].resolve([{symbol: 'XAUUSD', signal: 'NEUTRAL', side: 'HOLD', price: 2650.5, digits: 2, candle_time: 2}]);
  await neutral;
  const neutralHtml = node('signal-scanner-list').innerHTML;
  assert.ok(neutralHtml.includes('Nötr') && !neutralHtml.includes('SAT Trend'));
  assert.ok(/<button type="button" disabled[^>]*sendSignalDirectToBot/.test(neutralHtml), 'neutral row send button disabled');

  // In multi mode the selected symbol is listed first even if the server orders it later.
  node('signal-scan-mode').value = 'ALL';
  const multi = ctx.refreshSignalScanner(true, true);
  pending[3].resolve([
    {symbol: 'EURUSD', signal: 'BUY_TREND', side: 'BUY', price: 1.08, digits: 5, candle_time: 3},
    {symbol: 'XAUUSD', signal: 'SELL_TREND', side: 'SELL', price: 2650.5, digits: 2, candle_time: 3}]);
  await multi;
  const multiHtml = node('signal-scanner-list').innerHTML;
  assert.ok(multiHtml.indexOf('XAUUSD') < multiHtml.indexOf('EURUSD'), 'selected symbol first');
  console.log('Scanner symbol switch: ALL TESTS PASS');
})().catch((err) => { console.error(err); process.exit(1); });
