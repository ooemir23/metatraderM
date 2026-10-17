const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// Interface size control: clamps, persists, resets, survives blocked storage.
function load(stored, {blockStorage = false} = {}) {
  const listeners = {};
  const nodes = {};
  const button = (id) => (nodes[id] = {id, disabled: false, textContent: '', addEventListener: (t, fn) => (listeners[id] = fn)});
  const slot = {dataset: {}, setAttribute() {}, set innerHTML(v) { ['ui-scale-down', 'ui-scale-value', 'ui-scale-up'].forEach(button); },
    querySelector: (sel) => nodes[sel.slice(1)]};
  const style = {zoom: '', props: {}, setProperty(k, v) { this.props[k] = v; }};
  const store = new Map(stored == null ? [] : [['hma.ui.scale.v1', String(stored)]]);
  const localStorage = {
    getItem: (k) => { if (blockStorage) throw new Error('blocked'); return store.has(k) ? store.get(k) : null; },
    setItem: (k, v) => { if (blockStorage) throw new Error('blocked'); store.set(k, v); },
  };
  const ctx = vm.createContext({
    localStorage, Event: class { constructor(t) { this.type = t; } },
    document: {readyState: 'complete', documentElement: {style}, querySelector: () => slot,
      getElementById: (id) => nodes[id] || null, addEventListener() {}},
    window: {dispatchEvent() {}},
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync('app/static/ui-scale.js', 'utf8'), ctx);
  return {ctx, style, store, nodes, click: (id) => listeners[id]()};
}

let ui = load(null);
assert.equal(ui.style.zoom, '', 'default size leaves zoom unset');
assert.equal(ui.nodes['ui-scale-value'].textContent, '100%');
ui.click('ui-scale-down');
assert.equal(ui.style.zoom, '0.9');
assert.equal(ui.style.props['--ui-scale'], '0.9');
assert.equal(ui.store.get('hma.ui.scale.v1'), '0.9', 'choice is saved');
for (let i = 0; i < 10; i++) ui.click('ui-scale-down');
assert.equal(ui.style.zoom, '0.7', 'clamped at 70%');
assert.equal(ui.nodes['ui-scale-down'].disabled, true);
ui.click('ui-scale-value');
assert.equal(ui.style.zoom, '', 'percentage button resets to default');
for (let i = 0; i < 10; i++) ui.click('ui-scale-up');
assert.equal(ui.style.zoom, '1.5', 'clamped at 150%');

ui = load('0.8');
assert.equal(ui.style.zoom, '0.8', 'saved size restored on load');
ui = load('garbage');
assert.equal(ui.style.zoom, '', 'invalid saved value ignored');
ui = load(null, {blockStorage: true});
ui.click('ui-scale-up');
assert.equal(ui.style.zoom, '1.1', 'works for the session when storage is blocked');
console.log('UI scale control: ALL TESTS PASS');
