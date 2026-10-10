const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;

function harness(files = ['app.js', 'trading.js', 'operations.js']) {
  const nodes = new Map(), storage = new Map(), requests = [], notices = [], timers = new Map(), feeds = [];
  let timer = 0;
  function node(id) {
    if (!nodes.has(id)) {
      const classes = new Set(['hidden']);
      const n = {value:'',disabled:false,checked:false,open:false,dataset:{},innerHTML:'',innerText:'',textContent:'',className:'',
        addEventListener(){},setAttribute(k,v){this[k]=v},showModal(){this.open=true},close(){this.open=false},
        classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),contains:k=>classes.has(k),
          toggle(k,force){const yes=force===undefined?!classes.has(k):force;yes?classes.add(k):classes.delete(k);return yes;}}};
      nodes.set(id,n);
    }
    return nodes.get(id);
  }
  for (const [id,value] of [['lot-input','0.01'],['sl-input','200'],['tp-input','400'],['pending-volume','0.01'],
    ['pending-price','1.08'],['pending-type','BUY_LIMIT'],['pending-sl','200'],['pending-tp','400'],
    ['login-acc','12345'],['login-pass','example'],['login-srv','Broker-Demo']]) node(id).value=value;
  let reply = async()=>({ok:true,json:async()=>({success:true,request_id:'test-request-id-123',ticket:10})});
  const context = vm.createContext({Date,URL,AbortController,crypto,console:{error(){},debug(){}},
    document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){}},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),
      get length(){return storage.size},key:i=>[...storage.keys()][i]},
    setTimeout(fn){const id=++timer;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),setInterval(){},clearInterval(){},
    EventSource:class {constructor(url){this.url=url;feeds.push(this)} close(){this.closed=true}},
    confirmAction:async()=>true,
    fetch:async(url,options={})=>{const request={url,payload:options.body?JSON.parse(options.body):null,signal:options.signal};requests.push(request);return reply(request)}
  });
  for (const file of files) vm.runInContext(fs.readFileSync('app/static/'+file,'utf8'),context);
  // Isolate the existing account/intent regressions; readiness integration is tested independently.
  context.quoteOrderIssue=()=>'';context.window.MT5TradePreview={issue:()=>'',verify:async()=>true};
  context.showToast=(text,tone)=>notices.push({text,tone});
  return {context,node,nodes,storage,requests,notices,timers,feeds,setReply:fn=>{reply=fn},read:s=>vm.runInContext(s,context)};
}
const account = (login=12345) => ({connected:true,account_mismatch:false,account_type:'DEMO',login,server:'Broker-Demo',
  balance:1000,equity:1000,margin_free:900,profit:0,currency:'USD'});
const recommendation = (symbol='EURUSD',id='advice-a') => ({id,symbol,action:'BUY',confidence:80,
  generated_at:'test-time',expires_at:Date.now()/1000+900,sl_points:200,tp_points:400,sl_price:1.08,tp_price:1.14});
const response = data=>({ok:true,json:async()=>data});
function stopRefreshes(h) { for(const fn of ['fetchAccount','fetchPositions','fetchHistory','fetchPendingOrders','refreshTradingStatus']) h.context[fn]=()=>{}; }

(async()=>{
  // A delayed REST response must not overwrite a newer live account/position snapshot.
  {
    const h=harness(),c=h.context; let release;
    h.setReply(()=>new Promise(resolve=>{release=resolve}));
    const fetch=c.fetchAccount(true);
    c.renderAccountData(account());
    release(response({...account(),balance:1})); await fetch;
    assert.equal(h.node('acc-balance').innerText,'USD 1.000,00');
    const positions=c.fetchPositions(true);
    c.renderPositionsData([{ticket:7,symbol:'EURUSD',type:'BUY',volume:.01,price_open:1.1,price_current:1.1,profit:0}]);
    release(response([])); await positions;
    assert.equal(h.node('pos-count-badge').innerText,1);
    const pending=c.fetchPendingOrders();
    c.renderPendingOrders([{ticket:8,symbol:'EURUSD',type:'BUY_LIMIT',volume:.01,price:1.08}]);
    release(response([])); await pending;
    assert.match(h.node('pending-orders-list').innerHTML,/#8/);
    const price=c.fetchPrice(true);
    c.renderPriceData({symbol:'EURUSD',bid:1.1,ask:1.11,spread:10,time:Date.now()/1000});
    release(response({symbol:'EURUSD',bid:1.0,ask:1.01,spread:10,time:Date.now()/1000}));await price;
    assert.equal(h.node('header-bid').innerText,1.1,'old REST quote must not replace a newer live quote');
    c.renderPriceData({symbol:'EURUSD',bid:0,ask:0,time:0,quote_status:{state:'missing'}});
    assert.equal(h.node('header-bid').innerText,'—','missing quote must clear the previous executable price');
  }
  // Every broker mutation carries the account that the operator actually saw.
  {
    const h=harness(),c=h.context;c.renderAccountData(account());stopRefreshes(h);
    await c.submitOrder('BUY'); await c.closePosition(7); await c.closeFilteredPositions('profit');
    await c.submitPendingOrder();await c.cancelPendingOrder(8);
    c.rememberPositions([{ticket:7,symbol:'EURUSD',type:'BUY',volume:.03,sl:1.08,tp:1.14}]);c.showPositionEditor(7);
    h.node('edit-sl').value='1.09';h.node('edit-tp').value='1.15';await c.savePositionStops();
    h.node('partial-volume').value='.01';await c.partialClosePosition();
    c.renderAIAdvice(recommendation());await c.executeCurrentAdvice();
    for(const request of h.requests) assert.deepEqual(request.payload.expected_account,[12345,'Broker-Demo','DEMO'],request.url);
    assert.equal(h.requests.length,8);
    // A stored manual request may never migrate to a different account.
    h.storage.set('order-intent:EURUSD',JSON.stringify({request_id:'old-request-id-123',symbol:'EURUSD',order_type:'BUY',volume:.01,sl_points:200,tp_points:400,expected_account:[99,'Other','REAL']}));
    await c.submitOrder('BUY');assert.equal(h.requests.length,8);
    c.showAccountConnectionError('offline');await c.submitOrder('BUY');assert.equal(h.requests.length,8);
    assert.equal(h.node('order-buy-btn').disabled,true);
    assert.equal(h.node('acc-equity').innerText,'—');
  }
  // Partial and accepted close requests must not claim the position is closed.
  {
    const h=harness(),c=h.context;stopRefreshes(h);
    h.setReply(async()=>response({success:true,pending:true,request_id:'pending-close-id-123',ticket:7}));
    await c.closePosition(7);assert.equal(h.notices.at(-1).tone,'warning');assert.match(h.notices.at(-1).text,/bekleniyor/);
    assert.ok(h.storage.has('close-intent:7'));
    h.setReply(async()=>response({success:true,partial:true,request_id:'partial-close-id-123',ticket:8}));
    await c.closePosition(8);assert.match(h.notices.at(-1).text,/kısmen/);assert.equal(h.notices.at(-1).tone,'warning');
    // A lost response with an already definitive journal result can be recovered without replaying the order.
    h.storage.set('order-intent:EURUSD',JSON.stringify({request_id:'lost-open-id-123'}));
    h.setReply(async()=>response({found:true,success:true,request_id:'lost-open-id-123',ticket:10}));
    await c.checkReconciledOrders();assert.equal(h.storage.has('order-intent:EURUSD'),false);
    assert.ok(h.requests.at(-1).url.startsWith('/api/operations/status?'));
  }
  // A timed-out HTTP request releases the UI while retaining its durable request identity.
  {
    const h=harness(),c=h.context;stopRefreshes(h);
    h.setReply(request=>new Promise((resolve,reject)=>request.signal.addEventListener('abort',()=>reject(new Error('timeout')))));
    const order=c.submitOrder('BUY');await new Promise(setImmediate);
    [...h.timers.values()].at(-1)();await order;
    assert.ok(h.storage.has('order-intent:EURUSD'));
    assert.equal(h.read('orderPending'),false);
    assert.equal(h.node('order-reset-btn').hidden,false);
  }
  // Programmatic lot shortcuts refresh risk; invalid TP must not reach the preview API.
  {
    const h=harness(),c=h.context; c.setLot(.05);
    assert.equal(h.node('lot-input').value,'0.05');assert.equal(h.read('previewGeneration'),1);
    c.adjustLot(.01);assert.equal(h.read('previewGeneration'),2);
    h.node('tp-input').value='1.5';await c.refreshTradePreview();assert.equal(h.requests.length,0);
    assert.equal(h.node('trade-preview').dataset.state,'unavailable');
  }
  // Both screens reject stale symbols, changes during confirmation and duplicate AI execution.
  for(const file of ['app.js','ai.js']) {
    const h=harness([file]),c=h.context;stopRefreshes(h);
    c.renderAIAdvice(recommendation());assert.equal(h.node('ai-execute-btn').disabled,false);
    c.confirmAction=async()=>{c.invalidateAIAdvice('EURUSD');return true};
    await c.executeCurrentAdvice();assert.equal(h.requests.length,0,'changed confirmation: '+file);
    c.renderAIAdvice(recommendation());c.confirmAction=async()=>true;
    await c.executeCurrentAdvice();await c.executeCurrentAdvice();assert.equal(h.requests.length,1,'duplicate: '+file);
    let release;
    h.setReply(()=>new Promise(resolve=>{release=resolve}));
    const first=c.triggerAIAdvice();
    h.read('currentSymbol="XAUUSD"');c.invalidateAIAdvice('XAUUSD');
    release(response({success:true,recommendation:recommendation('EURUSD','late-a')}));await first;
    assert.equal(h.read('currentAIAdvice'),null,'late old symbol: '+file);
    assert.equal(h.node('ai-execute-btn').disabled,true);
    // Polling must preserve unfinished autopilot edits, including an explicit zero loss budget.
    h.node('ai-autopilot-modal').classList.remove('hidden');h.node('ai-cfg-max-loss').value='0';
    c.renderAIMemory({}, {mode:'FULL_AUTO',max_lot:.01,min_confidence:75,daily_loss_limit:50,allowed_symbols:['EURUSD']});
    assert.equal(h.node('ai-cfg-max-loss').value,'0','polling overwrites edit: '+file);
  }
  // Double-clicking an automation toggle may not stop and immediately restart it.
  {
    const h=harness(),c=h.context;let release;c.fetchBotStatus=()=>{};
    h.setReply(()=>new Promise(resolve=>{release=resolve}));
    const toggle=c.toggleBot();await c.toggleBot();assert.equal(h.requests.length,1);
    release({ok:false,json:async()=>({detail:'Start denied'})});await toggle;
    assert.equal(h.notices.at(-1).tone,'error');assert.match(h.notices.at(-1).text,/Start denied/);
    assert.equal(h.node('bot-toggle-btn').disabled,false);
  }
  // Empty fields require an explicit zero before any protection may be removed.
  {
    const h=harness(),c=h.context;stopRefreshes(h);
    h.node('sl-input').value='';await c.submitOrder('BUY');assert.equal(h.requests.length,0);
    c.rememberPositions([{ticket:7,symbol:'EURUSD',type:'BUY',volume:.03,sl:1.08,tp:1.14}]);c.showPositionEditor(7);
    h.node('edit-sl').value='';await c.savePositionStops();assert.equal(h.requests.length,0);
    h.node('pending-sl').value='';await c.submitPendingOrder();assert.equal(h.requests.length,0);
    c.renderAccountData({...account(),account_type:'REAL'});h.node('sl-input').value='0';
    await c.submitOrder('BUY');assert.equal(h.requests.length,0);
    c.renderAccountData({...account(),account_type:'CONTEST'});
    assert.match(h.node('status-text').innerText,/türü doğrulanamadı/);
    assert.equal(h.node('order-buy-btn').disabled,true);
  }
  // Permissions survive polling and apply to newly rendered pending/cancel/bulk actions.
  {
    const h=harness(['app.js']),c=h.context;let initialize,observer;
    const handlers=['submitOrder(\'BUY\')','submitPendingOrder()','cancelPendingOrder(8)','closeFilteredPositions(\'all\')','executeCurrentAdvice()'];
    const buttons=handlers.map((handler,i)=>{const n=h.node(i===0?'order-buy-btn':i===4?'ai-execute-btn':'role-action-'+i);n.getAttribute=()=>handler;return n});
    c.document.addEventListener=(name,fn)=>{initialize=fn};c.document.querySelectorAll=()=>buttons;c.document.body={};
    c.MutationObserver=class {constructor(fn){observer=fn}observe(){}};
    h.setReply(async()=>response({username:'readonly',role:'VIEWER'}));
    vm.runInContext(fs.readFileSync('app/static/permissions.js','utf8'),c);await initialize();
    for(const button of buttons) assert.equal(button.disabled,true);
    c.updateOrderButtons();assert.equal(h.node('order-buy-btn').disabled,true);
    c.renderAIAdvice(recommendation());assert.equal(h.node('ai-execute-btn').disabled,true);
    buttons[2].disabled=false;observer();assert.equal(buttons[2].disabled,true);
  }
  // Risk settings bind the displayed account, serialize updates, and cancel an account change during confirmation.
  for(const method of ['saveTradingDailyLimit','toggleTradingLock']) {
    const h=harness(),c=h.context;stopRefreshes(h);
    const risk={...account(),daily_loss:10,daily_loss_limit:100,daily_limit_enabled:true};
    const setup=()=>{c.renderAccountData(account());h.read('tradingRiskDialogState='+JSON.stringify({risk,halted:true,admin:true}));h.node('trading-risk-dialog').open=true;h.node('trading-risk-limit-input').value='200'};
    setup();c.confirmAction=async()=>{c.renderAccountData(account(54321));return true};
    await c[method]();assert.equal(h.requests.length,0,'changed account: '+method);
    assert.match(h.node('trading-risk-explanation').textContent,/hesap değişti/);
    setup();let release;
    c.confirmAction=()=>new Promise(resolve=>{release=resolve});
    h.setReply(async()=>response(method==='saveTradingDailyLimit'?{...risk,daily_loss_limit:200}:{new_orders_halted:false,daily_limit_enabled:false}));
    const save=c[method]();await c[method]();
    await c[method==='saveTradingDailyLimit'?'toggleTradingLock':'saveTradingDailyLimit']();
    assert.equal(h.requests.length,0,'confirmation must serialize risk changes: '+method);
    release(true);await save;assert.equal(h.requests.length,1);
    assert.deepEqual(h.requests[0].payload.expected_account,[12345,'Broker-Demo','DEMO']);
  }
  for(const file of ['app.js','ai.js']) {
    const h=harness([file]),c=h.context;stopRefreshes(h);c.fetchAIStatus=()=>{};
    const setup=()=>{if(file==='app.js')c.renderAccountData({...account(),account_type:'REAL'});else h.read('currentAIAccountType="REAL";currentAIAccountIdentity=[12345,"Broker-Demo","REAL"];aiAccountVerificationKnown=true');h.node('ai-cfg-mode').value='FULL_AUTO'};
    setup();c.confirmAction=async()=>{if(file==='app.js')c.renderAccountData({...account(54321),account_type:'REAL'});else h.read('currentAIAccountIdentity=[54321,"Broker-Demo","REAL"]');return true};
    await c.saveAIAutopilot();assert.equal(h.requests.length,0,'changed autopilot account: '+file);
    setup();let release;c.confirmAction=()=>new Promise(resolve=>{release=resolve});
    const save=c.saveAIAutopilot();await c.saveAIAutopilot();assert.equal(h.requests.length,0);
    release(true);await save;assert.equal(h.requests.length,1,'duplicate autopilot save: '+file);
    assert.deepEqual(h.requests[0].payload.expected_account,[12345,'Broker-Demo','REAL']);
  }
  // Account input cannot silently truncate malformed values; other-symbol unresolved orders block switching.
  {
    const h=harness(),c=h.context;stopRefreshes(h);
    for(const value of ['123abc','123.9','1e3']) {h.node('login-acc').value=value;await c.submitLogin()}
    assert.equal(h.requests.length,0);
    h.node('login-acc').value='12345';h.storage.set('order-intent:BTCUSD','old');
    await c.submitLogin();assert.equal(h.requests.length,0);
  }
  console.log('UI audit: account snapshots, live/REST races, quote clearing, truthful fills, risk updates, AI races and login guards PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
