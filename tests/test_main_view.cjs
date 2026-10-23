const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
// Desktop main screen switch: Grafik | İşlemler | Bot | MevO; workspace tab requests pick the matching screen.
const html = fs.readFileSync('app/static/index.html', 'utf8');
assert.match(html, /id="main-view-switch"[\s\S]*data-view="chart"[\s\S]*data-view="trades"[\s\S]*data-view="bot"[\s\S]*data-view="mevo"/);
const css = fs.readFileSync('app/static/style.css', 'utf8');
assert.match(css, /body\[data-main-view="chart"\] #positions-card, body\[data-main-view="mevo"\] #positions-card \{ display:none !important; \}/);
assert.match(css, /body:not\(\[data-main-view="chart"\]\) \.hx-main-grid \{ display:none !important; \}/);
assert.match(css, /body\[data-main-view="mevo"\] #positions-card/);
const store = new Map(), tabs = [], panes = [];
const buttons = ['chart', 'trades', 'bot', 'mevo'].map((v) => ({dataset: {view: v}, attrs: {}, setAttribute(k, val) { this.attrs[k] = val; }}));
let click;
const sw = {querySelectorAll: () => buttons, addEventListener: (t, f) => { click = f; }};
const body = {dataset: {}};
const ctx = vm.createContext({
  localStorage: {getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)},
  document: {getElementById: (id) => (id === 'main-view-switch' ? sw : null), body, addEventListener() {}},
  Event: class { constructor(t) { this.type = t; } },
  window: {scrollY: 0, dispatchEvent() {}, scrollTo() {}, hxTab: (n) => tabs.push(n), setWorkspaceView: (v) => panes.push(v)},
});
vm.runInContext(fs.readFileSync('app/static/main-view.js', 'utf8'), ctx);
assert.equal(body.dataset.mainView, 'chart', 'chart screen by default');
click({target: {closest: () => buttons[1]}});
assert.equal(body.dataset.mainView, 'trades');
assert.equal(panes.at(-1), 'positions', 'trades screen shows the positions pane');
assert.equal(store.get('hma.main.view.v1'), 'trades', 'choice is remembered');
click({target: {closest: () => buttons[2]}});
assert.equal(body.dataset.mainView, 'bot');
assert.equal(panes.at(-1), 'signals', 'bot screen shows the scanner + bot pane');
click({target: {closest: () => buttons[3]}});
assert.equal(body.dataset.mainView, 'mevo');
assert.equal(buttons[3].attrs['aria-selected'], 'true');
click({target: {closest: () => buttons[0]}});
ctx.window.hxTab('signals');
assert.equal(body.dataset.mainView, 'bot', 'opening the bot tab shows the bot screen');
ctx.window.hxTab('history');
assert.equal(body.dataset.mainView, 'trades', 'other tabs show the trades screen');
assert.deepEqual(tabs, ['signals', 'history']);
console.log('Main screen switch: chart / trades / bot / mevo PASS');
