// An automatic bot order plays its own sound, different from the scanner signal beep.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, {id, innerHTML: '', textContent: '', hidden: true, dataset: {}, value: '',
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false}});
  return nodes.get(id);
};
const oscillators = [];
class FakeAudio {
  constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
  resume() { return Promise.resolve(); }
  createOscillator() {
    const o = {type: '', freqs: [], frequency: {setValueAtTime(f) { o.freqs.push(f); }, exponentialRampToValueAtTime() {}},
               connect() {}, start() {}, stop() {}};
    oscillators.push(o);
    return o;
  }
  createGain() { return {gain: {setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}}; }
}
const toasts = [];
const ctx = vm.createContext({
  console, AbortController: globalThis.AbortController, navigator: {userActivation: {hasBeenActive: true}},
  document: {getElementById: node, querySelectorAll: () => [], addEventListener() {}, activeElement: null},
  window: {addEventListener() {}, AudioContext: FakeAudio, MT5Markets: {all: []}, MT5I18n: {language: () => 'tr', translate: t => t}},
  setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
});
ctx.window.window = ctx.window;
vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
ctx.showToast = (m, t) => toasts.push([m, t]);

const take = () => oscillators.splice(0);
const now = Math.floor(Date.now() / 1000);

// Scanner signal: the short three-note beep (triangle for BUY).
ctx.playSignalBeep(true, false);
const scanner = take();
assert.equal(scanner.length, 3);
assert.ok(scanner.every(o => o.type === 'triangle'));

// Page load: existing history only primes, no sound.
ctx.announceBotSignals({signal_history: [{time: now - 60, source: 'AUTO', symbol: 'XAUUSD', side: 'SELL', lot: 1, success: true, ticket: 1}]});
assert.equal(take().length, 0);

// The bot opens a trade automatically: its own 8-note square-wave sound.
ctx.announceBotSignals({signal_history: [{time: now + 5, source: 'AUTO', symbol: 'XAUUSD', side: 'BUY', lot: 1, success: true, ticket: 2}]});
const opened = take();
assert.equal(opened.length, 8);
assert.ok(opened.every(o => o.type === 'square'), 'bot trade sound differs from the scanner beep');
assert.deepEqual(opened.slice(0, 4).map(o => o.freqs[0]), [523.25, 659.25, 783.99, 1046.5]);
assert.match(toasts.at(-1)[0], /Bot XAUUSD AL 1 lot açtı \(#2\)/);

// An automatic order the broker refused: a low falling buzz.
ctx.announceBotSignals({signal_history: [{time: now + 10, source: 'AUTO', symbol: 'XAUUSD', side: 'SELL', lot: 1, success: false, error: 'Market closed'}]});
const failed = take();
assert.equal(failed.length, 1);
assert.equal(failed[0].type, 'sawtooth');

// Your own "Şimdi AL/SAT" click (MANUAL) does not play the bot sound.
ctx.announceBotSignals({signal_history: [{time: now + 15, source: 'MANUAL', symbol: 'XAUUSD', side: 'BUY', lot: 1, success: true, ticket: 3}]});
assert.equal(take().length, 0);

// Sound switch off: nothing plays.
ctx.toggleSignalAutoAlert(false);
ctx.announceBotSignals({signal_history: [{time: now + 20, source: 'AUTO', symbol: 'XAUUSD', side: 'BUY', lot: 1, success: true, ticket: 4}]});
assert.equal(take().length, 0);
console.log('bot trade sound: PASS');
