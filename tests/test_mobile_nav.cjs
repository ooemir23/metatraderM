const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
// Mobile: section bar jumps to Chart / Order / Trades / Bot and opens the right workspace tab.
const html = fs.readFileSync('app/static/index.html', 'utf8');
for (const key of ['chart', 'order', 'positions', 'bot']) assert.match(html, new RegExp(`data-mx="${key}"`));
const css = fs.readFileSync('app/static/style.css', 'utf8');
assert.match(css, /\.mx-nav \{ display:grid;[^}]*position:sticky/, 'sticky section bar on mobile');
assert.match(css, /overflow-x:clip/, 'clip (not hidden) so sticky keeps working');
assert.match(css, /\nhtml, body \{ overflow-x:clip; max-width:100%; \}/, 'page never wider than the screen at any width');
assert.match(css, /\.sr-only \{ position:absolute !important; width:1px !important; max-width:1px !important;/, 'hidden select cannot widen the page');
assert.match(css, /\.hx-tabs \{ display:grid !important; grid-template-columns:repeat\(3/, 'all workspace tabs visible');
const tabs = [], scrolls = [];
const el = (top) => ({getBoundingClientRect: () => ({top, bottom: top + 50, height: 50})});
const buttons = ['chart', 'order', 'positions', 'bot'].map((mx) => ({dataset: {mx}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }}));
let click;
const nav = {getBoundingClientRect: () => ({top: 0, bottom: 52, height: 52}), querySelectorAll: () => buttons,
  addEventListener: (t, f) => { click = f; }};
const nodes = {'mx-nav': nav, 'chart-card': el(100), 'order-card': el(900), 'positions-card': {...el(1800), dataset: {hxTab: 'positions'}}, 'bot-card': el(2400)};
const ctx = vm.createContext({document: {getElementById: (id) => nodes[id]}, requestAnimationFrame: (f) => f(),
  window: {scrollY: 0, matchMedia: () => ({matches: true}), addEventListener() {}, scrollTo: (o) => scrolls.push(o.top), hxTab: (n) => tabs.push(n)}});
vm.runInContext(fs.readFileSync('app/static/mobile-nav.js', 'utf8'), ctx);
click({target: {closest: () => buttons[3]}});
assert.deepEqual(tabs, ['signals']);
assert.equal(scrolls.at(-1), 2400 - 60, 'scrolls to the bot card below the sticky bar');
assert.equal(buttons[3].attrs['aria-current'], 'true');
click({target: {closest: () => buttons[2]}});
assert.deepEqual(tabs, ['signals', 'positions']);
console.log('Mobile section bar: jumps and opens the right tab PASS');
