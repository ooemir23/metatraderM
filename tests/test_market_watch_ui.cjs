const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.handlers = {}; this.attributes = {};
    this.value = ''; this.className = ''; this._text = ''; this.hidden = false; this.disabled = false; this.open = false;
    this.classList = {add:name => { this.className += ' ' + name; }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Catalogue text must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, listener) { (this.handlers[type] ||= []).push(listener); }
  emit(type, extra = {}) {
    const event = {currentTarget:this, target:this, preventDefault(){ this.prevented = true; }, ...extra};
    for (const listener of this.handlers[type] || []) listener(event);
    return event;
  }
  click() { if (!this.disabled) this.emit('click'); }
  focus() { this.document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll(selector)])
      .filter(child => child.className.split(/\s+/).includes(selector.slice(1)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const A = [101, 'Broker-Demo', 'DEMO'], B = [202, 'Broker-Real', 'REAL'];
const symbol = (name, category = 'forex', trade_mode = 4, selectable = true) =>
  ({name, description:name === 'EURUSD.a' ? 'Euro / US Dollar' : name === 'BTCUSD#' ? '<img src=x onerror=evil()>' : name,
    path:'Broker\\' + category, category, trade_mode, digits:name === 'BTCUSD#' ? 2 : 5, visible:true, selectable});

function harness(storage = new Map()) {
  const requests = [], selected = [], nodes = new Map();
  const document = {activeElement:null, getElementById:id => nodes.get(id) || null,
    createElement:tag => { const item = new Element(tag); item.document = document; return item; }};
  for (const id of ['market-dialog', 'market-open', 'market-close', 'market-search', 'market-category', 'market-results',
    'market-status', 'market-result-count', 'market-retry', 'market-favorites', 'market-selection-note',
    'order-symbol-select', 'order-symbol-name', 'market-manage-favorites']) {
    const item = document.createElement('div'); nodes.set(id, item);
  }
  nodes.get('market-category').value = 'all';
  const ctx = vm.createContext({console, AbortController, document,
    setTimeout:() => 1, clearTimeout(){},
    localStorage:{getItem:key => storage.get(key) || null, setItem:(key,value) => storage.set(key,value)},
    window:{MT5I18n:{language:() => 'tr'}},
    fetch:(url, options) => new Promise(resolve => requests.push({url, options, resolve}))});
  vm.runInContext(fs.readFileSync('app/static/market-watch.js', 'utf8'), ctx);
  const api = ctx.window.MT5Markets;
  api.init({onSelect:name => selected.push(name)});
  const reply = (index, account, symbols, ok = true) => requests[index].resolve({ok, json:async() => ({account, symbols, total:symbols.length})});
  return {api, nodes, requests, selected, reply, storage, document};
}

(async () => {
  const h = harness();
  assert.match(h.nodes.get('market-favorites').textContent, /Hesap doğrulandığında/);
  assert.equal(h.requests.length, 0, 'no catalogue request without a verified account');
  const first = h.api.setAccount(A, 1), second = h.api.setAccount(B, 2);
  h.reply(1, B, [symbol('EURUSD.a'), symbol('BTCUSD#', 'crypto'), symbol('BUYONLY.a', 'stocks', 1),
    symbol('SELLONLY.a', 'stocks', 2), symbol('CLOSE.a', 'stocks', 3, false), symbol('OFF.a', 'other', 0, false),
    symbol('EUR/USD', 'forex', 4, false)]);
  await second;
  h.reply(0, A, [symbol('OLDUSD')]);
  await first;
  assert.equal(h.api.getSymbol('OLDUSD'), null, 'old-account catalogue must never replace current instruments');
  assert.equal(h.api.getSymbol('EURUSD.A'), null, 'case is part of the exact broker identity');
  assert.equal(h.api.getSymbol('EURUSD.a').digits, 5);
  assert.equal(h.api.isSelectionVerified('EURUSD'), false, 'bare canonical symbol is not invented for a suffix broker');
  assert.equal(h.api.isSelectionVerified('BUYONLY.a', 'SELL'), false);
  assert.equal(h.api.isSelectionVerified('BUYONLY.a', 'BUY'), true);
  assert.equal(h.api.isSelectionVerified('SELLONLY.a', 'BUY'), false);
  assert.equal(h.api.isSelectionVerified('CLOSE.a'), false);
  assert.match(h.api.selectionIssue('CLOSE.a'), /yalnız mevcut pozisyonları kapatmaya/);
  assert.match(h.api.selectionIssue('OFF.a'), /yeni işlemleri kapatmış/);
  assert.match(h.api.selectionIssue('EUR/USD'), /Bu ad web terminalinde desteklenmiyor/);
  assert.match(h.nodes.get('market-favorites').textContent, /Henüz favori yok/);
  assert.equal(h.storage.size, 0, 'never create fake default favourites');

  h.nodes.get('market-search').value = 'dollar'; h.nodes.get('market-search').emit('input');
  assert.equal(h.nodes.get('market-results').children.length, 1, 'search includes broker descriptions');
  const firstRow = h.nodes.get('market-results').children[0];
  firstRow.children[2].click();
  assert.equal(h.selected.length, 0, 'adding a favourite does not select an instrument or submit a trade');
  const key = 'mt5.market-watch.favorites.v1:' + JSON.stringify(B);
  assert.deepEqual(JSON.parse(h.storage.get(key)), ['EURUSD.a']);
  assert.equal(h.nodes.get('market-favorites').children[0].textContent, 'EURUSD.a');
  h.api.setActive('EURUSD.a');
  assert.equal(h.nodes.get('market-favorites').children[0].attributes['aria-pressed'], 'true');
  h.nodes.get('market-open').click();
  assert.equal(h.nodes.get('market-dialog').open, true);
  assert.equal(h.document.activeElement, h.nodes.get('market-search'));
  h.nodes.get('market-search').emit('keydown', {key:'ArrowDown'});
  assert.equal(h.document.activeElement.className, 'market-select');
  h.nodes.get('market-results').emit('keydown', {key:'ArrowUp', target:h.document.activeElement});
  assert.equal(h.document.activeElement, h.nodes.get('market-search'));
  h.nodes.get('market-dialog').emit('cancel');
  assert.equal(h.nodes.get('market-dialog').open, false);
  assert.equal(h.document.activeElement, h.nodes.get('market-open'), 'Escape restores the opener focus');
  h.nodes.get('market-favorites').children[0].click();
  assert.deepEqual(h.selected, ['EURUSD.a'], 'favourite selection preserves the exact broker suffix');

  h.nodes.get('market-search').value = ''; h.nodes.get('market-category').value = 'crypto';
  h.nodes.get('market-category').emit('change');
  assert.equal(h.nodes.get('market-results').children.length, 1);
  assert.equal(h.nodes.get('market-results').children[0].children[0].children[1].textContent, '<img src=x onerror=evil()>',
    'broker description is inert text');
  h.nodes.get('market-results').children[0].children[0].click();
  assert.equal(h.selected.at(-1), 'BTCUSD#');
  h.nodes.get('market-category').value = 'all'; h.nodes.get('market-category').emit('change');
  assert.equal(h.nodes.get('market-results').children.find(row => row.children[0].children[0].textContent === 'EUR/USD').children[0].disabled, true);

  h.nodes.get('market-retry').click();
  assert.equal(h.requests[2].url, '/api/symbols?refresh=true', 'operator refresh bypasses the broker catalogue cache');
  assert.equal(h.api.isSelectionVerified('EURUSD.a'), false, 'refresh must invalidate eligibility until the broker responds');
  h.reply(2, B, [symbol('BTCUSD#', 'crypto')]);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(h.nodes.get('market-favorites').textContent, /Kaydedilen favoriler/);
  h.nodes.get('market-manage-favorites').click();
  assert.equal(h.nodes.get('market-results').children.length, 1, 'missing stored favourites stay manageable');
  assert.equal(h.nodes.get('market-results').children[0].children[0].disabled, true);
  h.nodes.get('market-results').children[0].children[2].click();
  assert.deepEqual(JSON.parse(h.storage.get(key)), []);

  const third = h.api.setAccount(A, 3); h.reply(3, A, [symbol('EURUSD.a')]); await third;
  assert.match(h.nodes.get('market-favorites').textContent, /Henüz favori yok/, 'another account does not inherit favourites');
  h.nodes.get('market-category').value = 'all'; h.nodes.get('market-category').emit('change');
  h.nodes.get('market-results').children[0].children[2].click();
  const reloaded = harness(h.storage); const restored = reloaded.api.setAccount(A, 3);
  reloaded.reply(0, A, [symbol('EURUSD.a')]); await restored;
  assert.equal(reloaded.nodes.get('market-favorites').children[0].textContent, 'EURUSD.a', 'favourites survive a reload in their own account scope');
  const mismatch = reloaded.api.refresh(); reloaded.reply(1, B, [symbol('BTCUSD#', 'crypto')]); await mismatch;
  assert.equal(reloaded.api.getSymbol('BTCUSD#'), null, 'response account mismatch is rejected');
  assert.match(reloaded.nodes.get('market-status').textContent, /alınamadı/);
  assert.equal(reloaded.api.isSelectionVerified('EURUSD.a'), false);
  assert.equal(reloaded.nodes.get('market-retry').hidden, false);
  reloaded.api.setAccount(null, 4);
  assert.equal(reloaded.nodes.get('order-symbol-select').disabled, true);
  assert.ok([...h.requests, ...reloaded.requests].every(request => !request.options.method || request.options.method === 'GET'), 'catalogue/favourite actions issue only read requests');
  console.log('Market watch: exact broker names, safe text, account races, scoped favourites, category/search, restrictions, keyboard and retry PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
