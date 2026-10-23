const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
// Desktop main screen switch: Grafik | İşlemler & Bot; any workspace tab request shows the trades screen.
const html = fs.readFileSync('app/static/index.html', 'utf8');
assert.match(html, /id="main-view-switch"[\s\S]*data-view="chart"[\s\S]*data-view="work"/);
const css = fs.readFileSync('app/static/style.css', 'utf8');
assert.match(css, /body\[data-main-view="chart"\] #positions-card \{ display:none !important; \}/);
assert.match(css, /body\[data-main-view="work"\] \.hx-main-grid \{ display:none !important; \}/);
const store = new Map(), tabs = [];
const buttons = ['chart', 'work'].map((v) => ({dataset: {view: v}, attrs: {}, setAttribute(k, val) { this.attrs[k] = val; }}));
let click;
const sw = {querySelectorAll: () => buttons, addEventListener: (t, f) => { click = f; }};
const body = {dataset: {}};
const ctx = vm.createContext({
  localStorage: {getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)},
  document: {getElementById: (id) => (id === 'main-view-switch' ? sw : null), body, addEventListener() {}},
  Event: class { constructor(t) { this.type = t; } },
  window: {scrollY: 0, dispatchEvent() {}, scrollTo() {}, hxTab: (n) => tabs.push(n)},
});
vm.runInContext(fs.readFileSync('app/static/main-view.js', 'utf8'), ctx);
assert.equal(body.dataset.mainView, 'chart', 'chart screen by default');
click({target: {closest: () => buttons[1]}});
assert.equal(body.dataset.mainView, 'work');
assert.equal(store.get('hma.main.view.v1'), 'work', 'choice is remembered');
assert.equal(buttons[1].attrs['aria-selected'], 'true');
click({target: {closest: () => buttons[0]}});
ctx.window.hxTab('signals');
assert.equal(body.dataset.mainView, 'work', 'opening a workspace tab shows the trades screen');
assert.deepEqual(tabs, ['signals']);
console.log('Main screen switch: chart / trades & bot PASS');
