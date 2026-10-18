// Signal scanner: symbol can be changed from the scanner (header button and card symbol name).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync('app/static/index.html', 'utf8');
assert.match(html, /id="scanner-symbol-button"[^>]*aria-haspopup="dialog"/);
assert.match(fs.readFileSync('app/static/market-watch.js', 'utf8'), /node\('scanner-symbol-button'\)\?\.addEventListener\('click'/);

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', hidden: true, dataset: {}, value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  return nodes.get(id);
};
const opened = [];
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController,
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, MT5Markets: {all: [], open: t => opened.push(t)}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);

ctx.renderScannerSymbolButton('BTCUSD');
assert.equal(node('scanner-symbol-name').textContent, 'BTCUSD');
const trigger = {id: 'x'};
ctx.openScannerSymbolPicker(trigger);
assert.equal(opened[0], trigger, 'opens the Market Watch window');
assert.match(fs.readFileSync('app/static/app.js', 'utf8'),
  /class="scan-symbol-button" onclick="openScannerSymbolPicker\(this\)"/, 'card symbol name is a picker button');
console.log('scanner symbol picker ok');
