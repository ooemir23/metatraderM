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

function load(stored, {blockStorage = false} = {}) {
  const attrs = (el) => Object.assign(el, {attrs: {}, toggleAttribute(n, on) { if (on) this.attrs[n] = ''; else delete this.attrs[n]; },
    setAttribute(n, v) { this.attrs[n] = String(v); }, focus() {}, querySelector: () => ({focus() {}}),
    classList: {set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); }, contains(c) { return this.set.has(c); }}});
  const nodes = {
    'workspace-split': attrs({dataset: {view: 'positions'}}), 'positions-pane': attrs({}), 'signals-pane': attrs({}),
    'positions-card': attrs({}), 'signals-toggle': attrs({}), 'positions-strip': attrs({}),
    'pos-count-badge': attrs({textContent: '7'}), 'acc-profit': attrs({textContent: 'USD -12.151,26', className: 'text-rose-400'}),
    'positions-strip-count': attrs({textContent: ''}), 'positions-strip-pnl': attrs({textContent: '', dataset: {}}),
    'bot-pending-signal-box': attrs({}), 'signals-toggle-dot': attrs({hidden: true}),
  };
  nodes['bot-pending-signal-box'].classList.set.add('hidden');
  const store = new Map(stored ? [['hma.workspace.view.v1', stored]] : []);
  const calls = [];
  const ctx = vm.createContext({
    document: {readyState: 'complete', getElementById: (id) => nodes[id] || null, addEventListener() {}},
    localStorage: {getItem: (k) => { if (blockStorage) throw new Error('blocked'); return store.get(k) ?? null; },
                   setItem: (k, v) => { if (blockStorage) throw new Error('blocked'); store.set(k, v); }},
    window: {dispatchEvent() {}}, Event: class {}, setTimeout: () => 0,
    refreshSignalScanner: (...a) => calls.push(['scan', ...a]), fetchBotStatus: () => calls.push(['bot']), fetchBotLiveSignal: () => calls.push(['live']),
  });
  ctx.window = Object.assign(ctx.window, ctx);
  vm.runInContext(fs.readFileSync('app/static/workspace.js', 'utf8'), ctx);
  return {ctx, nodes, store, calls};
}

let w = load(null);
assert.equal(w.nodes['workspace-split'].dataset.view, 'positions', 'positions view by default');
assert.ok('inert' in w.nodes['signals-pane'].attrs, 'hidden signals pane is inert');
assert.equal(w.nodes['positions-strip-count'].textContent, '7', 'strip mirrors the position count');
assert.equal(w.nodes['positions-strip-pnl'].dataset.tone, 'loss');
assert.equal(w.nodes['signals-toggle-dot'].hidden, true, 'no pending signal -> no dot');

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
