const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync('app/static/index.html', 'utf8');
// Scanner and bot live inside the positions card accordion, next to the positions pane.
const split = html.indexOf('id="workspace-split"'), signals = html.indexOf('id="signals-pane"');
assert.ok(split > 0 && signals > split, 'signals pane is inside the workspace split');
assert.ok(html.indexOf('id="signal-scanner-card"') > signals && html.indexOf('id="bot-card"') > signals, 'scanner and bot are in the signals pane');
assert.equal((html.match(/id="signal-scanner-card"/g) || []).length, 1, 'scanner card exists once');
assert.ok(html.includes('id="signals-toggle"') && html.includes('id="positions-strip"'));
assert.ok(html.includes('/static/workspace.js'), 'script included');
const drawerAt = html.indexOf('id="tools-drawer"');
for (const id of ['research-panel', 'market-depth-panel', 'system-logs']) assert.ok(html.indexOf(`id="${id}"`) > drawerAt, `${id} lives in the tools drawer`);
assert.ok(html.indexOf('id="positions-card"') < html.indexOf('</main>') && html.indexOf('id="positions-card"') > html.indexOf('id="order-buy-btn"'), 'positions row is full width below the grid');

function load(stored, {blockStorage = false} = {}) {
  const attrs = (el) => Object.assign(el, {attrs: {}, toggleAttribute(n, on) { if (on) this.attrs[n] = ''; else delete this.attrs[n]; },
    setAttribute(n, v) { this.attrs[n] = String(v); }, focus() {}, querySelector: () => ({focus() {}}),
    classList: {set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); }, contains(c) { return this.set.has(c); }}});
  const nodes = {
    'workspace-split': attrs({dataset: {view: 'positions'}}), 'positions-pane': attrs({}), 'signals-pane': attrs({}),
    'positions-card': attrs({}), 'signals-toggle': attrs({}), 'positions-strip': attrs({}),
    'pos-count-badge': attrs({textContent: '7'}), 'acc-profit': attrs({textContent: 'USD -12.151,26', className: 'text-rose-400'}),
    'positions-strip-count': attrs({textContent: ''}), 'positions-strip-pnl': attrs({textContent: '', dataset: {}}),
    'signals-count': attrs({hidden: true, textContent: ''}), 'signals-pending': attrs({hidden: true, textContent: ''}),
    'tools-drawer': attrs({}), 'tools-backdrop': attrs({hidden: true}), 'tools-toggle': attrs({}),
    'tools-error-badge': attrs({hidden: true, textContent: ''}), 'system-logs': attrs({}),
  };
  let errorRows = [];
  nodes['system-logs'].querySelectorAll = () => errorRows.map((t) => ({textContent: t}));
  const store = new Map(stored ? [['hma.workspace.view.v1', stored]] : []);
  const calls = [];
  const listeners = {};
  const ctx = vm.createContext({
    document: {readyState: 'complete', getElementById: (id) => nodes[id] || null, addEventListener() {}},
    localStorage: {getItem: (k) => { if (blockStorage) throw new Error('blocked'); return store.get(k) ?? null; },
                   setItem: (k, v) => { if (blockStorage) throw new Error('blocked'); store.set(k, v); }},
    window: {dispatchEvent() {}, addEventListener: (t, fn) => (listeners[t] = fn)}, Event: class {}, setTimeout: () => 0,
    refreshSignalScanner: (...a) => calls.push(['scan', ...a]), fetchBotStatus: () => calls.push(['bot']), fetchBotLiveSignal: () => calls.push(['live']),
  });
  ctx.window = Object.assign(ctx.window, ctx);
  vm.runInContext(fs.readFileSync('app/static/workspace.js', 'utf8'), ctx);
  return {ctx, nodes, store, calls, listeners, setErrors: (rows) => { errorRows = rows; }};
}

let w = load(null);
assert.equal(w.nodes['workspace-split'].dataset.view, 'positions', 'positions view by default');
assert.ok('inert' in w.nodes['signals-pane'].attrs, 'hidden signals pane is inert');
assert.equal(w.nodes['positions-strip-count'].textContent, '7', 'strip mirrors the position count');
assert.equal(w.nodes['positions-strip-pnl'].dataset.tone, 'loss');
assert.equal(w.nodes['signals-count'].hidden, true, 'no scan yet -> no badge');
w.listeners['hma:scanner']({detail: {signals: 3, crosses: 1}});
assert.equal(w.nodes['signals-count'].hidden, false);
assert.equal(w.nodes['signals-count'].textContent, '3', 'toggle shows the signal count');
assert.ok(w.nodes['signals-count'].classList.contains('has-cross'), 'fresh crossover pulses');
w.listeners['hma:bot-pending']({detail: {count: 2}});
assert.equal(w.nodes['signals-pending'].textContent, '2', 'pending approvals shown');
w.listeners['hma:bot-pending']({detail: {count: 0}});
assert.equal(w.nodes['signals-pending'].hidden, true);

// Tools drawer: opens/closes, counts unseen errors only while closed.
w.ctx.window.setToolsDrawer(true);
assert.ok(w.nodes['tools-drawer'].classList.contains('open') && !('inert' in w.nodes['tools-drawer'].attrs));
assert.equal(w.nodes['tools-backdrop'].hidden, false);
w.ctx.window.setToolsDrawer(false);
assert.ok('inert' in w.nodes['tools-drawer'].attrs, 'closed drawer is inert');
w.setErrors(['[14:05:48] Sinyal emri açılamadı']);
w.ctx.window.setToolsDrawer(false);
assert.equal(w.nodes['tools-error-badge'].textContent, '1', 'new error badge while closed');
w.ctx.window.setToolsDrawer(true);
w.ctx.window.setToolsDrawer(false);
assert.equal(w.nodes['tools-error-badge'].hidden, true, 'seen errors clear the badge');

w.ctx.window.setWorkspaceView('signals');
assert.equal(w.nodes['workspace-split'].dataset.view, 'signals');
assert.ok('inert' in w.nodes['positions-pane'].attrs && !('inert' in w.nodes['signals-pane'].attrs));
assert.equal(w.nodes['signals-toggle'].attrs['aria-expanded'], 'true');
assert.equal(w.store.get('hma.workspace.view.v1'), 'signals', 'view remembered');
assert.deepEqual(w.calls[0], ['scan', true, true], 'opening refreshes the scanner immediately');
w.ctx.window.setWorkspaceView('positions');
assert.ok(!('inert' in w.nodes['positions-pane'].attrs));

w = load('signals');
assert.equal(w.nodes['workspace-split'].dataset.view, 'signals', 'restores remembered view');
w = load(null, {blockStorage: true});
w.ctx.window.setWorkspaceView('signals');
assert.equal(w.nodes['workspace-split'].dataset.view, 'signals', 'works with blocked storage');
console.log('Workspace accordion: ALL TESTS PASS');
