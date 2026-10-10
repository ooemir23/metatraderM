const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const nodes = new Map(), requests = [], mounts = [], catalog = new Map([
    ['EURUSD.a', {name:'EURUSD.a', digits:5}], ['BTCUSD#', {name:'BTCUSD#', digits:2}]
  ]);
  const node = id => { if (!nodes.has(id)) nodes.set(id, {innerText:'', textContent:'', innerHTML:'', value:'0.01', dataset:{}}); return nodes.get(id); };
  let mode = 'tradingview', eligible = true, version = 1;
  const context = vm.createContext({console, URL, AbortController,
    setTimeout:() => 1, clearTimeout(){}, setInterval(){}, clearInterval(){},
    document:{addEventListener(){}, getElementById:node, querySelectorAll:() => []},
    window:{addEventListener(){}, MT5Markets:{getSymbol:name => catalog.get(name), setActive(){},
      isSelectionVerified:() => eligible, selectionIssue:() => 'Katalog ürünü işlem için doğrulanamadı.', version:() => version},
      MT5Chart:{dispose(){}, mode:() => mode, mount:symbol => mounts.push(symbol), setMode:value => { mode = value; context.switchSymbol(vm.runInContext('currentSymbol', context)); }}},
    fetch:async(url, options) => { requests.push({url, options}); return {ok:true, json:async() => ({symbol:'BTCUSD#', bid:82401.2, ask:82402.3, spread:110})}; }});
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8'), context);
  context.renderOrderNotice = () => {}; context.setOrderNotice = () => {};
  vm.runInContext("currentSymbol='EURUSD.a'", context);
  context.renderPriceData({symbol:'EURUSD.a', bid:1.120159999999, ask:1.120169999999999, spread:1});
  assert.equal(node('header-bid').innerText, '1.12016');
  assert.equal(node('header-ask').innerText, '1.12017', 'catalogue precision suppresses floating-point artefacts');
  assert.equal(node('btn-ask-price').innerText, '@ 1.12017');
  vm.runInContext("currentSymbol='BTCUSD#'", context);
  await context.fetchPrice(true);
  assert.equal(requests[0].url, '/api/price/BTCUSD%23', 'hash suffix is encoded rather than lost as a URL fragment');
  assert.equal(node('header-bid').innerText, '82401.20');
  context.renderPriceData({symbol:'BTCUSD#',bid:123.456,ask:123.556,spread:1,digits:3});
  assert.equal(node('header-bid').innerText, '123.456', 'valid tick precision takes precedence');
  context.renderPriceData({symbol:'BTCUSD#',bid:123.456,ask:123.556,spread:1,digits:100000});
  assert.equal(node('header-bid').innerText, '123.46', 'invalid tick precision uses validated metadata');
  vm.runInContext("currentSymbol='LEGACY'", context);
  catalog.set('LEGACY', {name:'LEGACY'});
  context.renderPriceData({symbol:'LEGACY',bid:1.123456789,ask:1.123456999,spread:1,digits:-1});
  assert.equal(node('header-bid').innerText, 1.123456789, 'without metadata legacy numeric rendering remains unchanged');
  vm.runInContext("currentSymbol='EURUSD.a'", context);
  context.initTradingView('EURUSD.a');
  assert.equal(mode, 'mt5');
  assert.equal(mounts.at(-1), 'EURUSD.a');
  assert.equal(node('chart-engine').value, 'mt5');
  assert.equal(node('chart-tradingview-link').hidden, true);
  assert.match(node('chart-source-notice').textContent, /doğrulanmış TradingView eşlemesi yok/);
  assert.equal(context.brokerSymbolForChart('EURUSD', 'FX', 'forex'), null, 'TradingView bare names cannot silently replace a suffix broker instrument');
  context.applyChartSymbol('EURUSD', 'FX', 'forex');
  assert.equal(vm.runInContext('currentSymbol', context), 'EURUSD.a');
  assert.equal(vm.runInContext('chartSelectionBlocked', context), true);
  vm.runInContext("chartSelectionBlocked=false;orderSymbolSource='panel'", context);
  eligible = false;
  context.updateOrderButtons();
  assert.equal(node('order-buy-btn').disabled, true);
  assert.equal(await context.verifyChartOrderSymbol('BUY'), false, 'manual selection cannot bypass catalogue eligibility');
  const before = requests.length;
  await context.submitOrder('BUY');
  assert.equal(requests.length, before, 'unavailable catalogue cannot send a market order');
  vm.runInContext(fs.readFileSync('app/static/operations.js','utf8'), context);
  node('lot-input').value = '0.01'; node('sl-input').value = '200'; node('tp-input').value = '400';
  await context.refreshTradePreview();
  assert.equal(requests.length, before, 'risk preview uses the same eligibility predicate');
  assert.match(node('trade-preview').textContent, /Katalog ürünü/);
  const identity = context.tradePreviewIdentity().key; version++;
  assert.notEqual(context.tradePreviewIdentity().key, identity, 'a catalogue refresh invalidates in-flight risk identity');
  const malicious = '<img src=x onerror="evil()">';
  context.renderPositionsData([{ticket:1,symbol:malicious,type:'BUY',volume:.01,price_open:1,price_current:1,profit:0}]);
  assert.match(node('positions-table-body').innerHTML, /&lt;img src=x onerror=&quot;evil\(\)&quot;&gt;/);
  assert.doesNotMatch(node('positions-table-body').innerHTML, /<img/);
  context.fetch = async() => ({ok:true,json:async() => [{ticket:2,position_id:1,symbol:malicious,type:'SELL',profit:0}]});
  await context.fetchHistory();
  assert.match(node('history-table-body').innerHTML, /&lt;img/);
  assert.doesNotMatch(node('history-table-body').innerHTML, /<img/);
  context.fetch = async() => ({ok:true,json:async() => ({summary:{},daily:[],by_symbol:[{symbol:malicious,trades_count:1,volume:.01,profit:0}]})});
  await context.fetchReports();
  assert.match(node('symbol-reports-tbody').innerHTML, /&lt;img/);
  assert.doesNotMatch(node('symbol-reports-tbody').innerHTML, /<img/);
  console.log('Market integration: suffix precision, encoded quotes, explicit chart mapping and shared order/preview eligibility PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
