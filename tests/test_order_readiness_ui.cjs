const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const tick=(extra={})=>({symbol:'EURUSD',bid:1.1,ask:1.1001,time:1700000000,quote_status:{state:'fresh',broker_connected:true,age_seconds:0},...extra});
const account=(login=12345)=>({connected:true,account_type:'DEMO',login,server:'Broker-Demo',balance:1000,equity:1000,margin_free:900,currency:'USD'});
const success={success:true,currency:'USD',stop_risk:2,risk_pct_equity:.2,margin_required:5,spread_points:10,existing_stop_risk:0,daily_loss:0};
const response=data=>({ok:data.success!==false,json:async()=>data});
function harness(){
 const nodes=new Map(),storage=new Map(),requests=[],timers=new Map();let now=1700000000000,nextTimer=0,version=1;
 const node=id=>{if(!nodes.has(id)){const classes=new Set();nodes.set(id,{value:'',textContent:'',innerText:'',innerHTML:'',dataset:{},disabled:false,
 addEventListener(){},setAttribute(){},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(){}}});}return nodes.get(id)};
 for(const [id,value] of [['lot-input','.01'],['sl-input','200'],['tp-input','400'],['pending-volume','.01'],['pending-type','BUY_LIMIT'],['pending-price','1.08'],['pending-sl','200'],['pending-tp','400']])node(id).value=value;
 class Clock extends Date{static now(){return now}}
 let preview=request=>response(success),order=()=>response({success:true,request_id:'confirmed-request',ticket:7});
 const c=vm.createContext({console,Date:Clock,URL,AbortController,crypto:require('node:crypto').webcrypto,
 document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},MT5Markets:{setAccount(){},setActive(){},isSelectionVerified:()=>true,getSymbol:()=>({digits:5}),version:()=>version}},
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),get length(){return storage.size},key:i=>[...storage.keys()][i]},
 setTimeout(fn,delay){const id=++nextTimer;timers.set(id,{fn,delay});return id},clearTimeout:id=>timers.delete(id),setInterval(){},clearInterval(){},
 fetch:async(url,options={})=>{const r={url,payload:JSON.parse(options.body||'{}'),signal:options.signal};requests.push(r);return url==='/api/trade/preview'?preview(r):order(r)}});
 for(const file of ['app','trading','operations'])vm.runInContext(fs.readFileSync(`app/static/${file}.js`,'utf8'),c);
 for(const fn of ['fetchPositions','fetchAccount','fetchPendingOrders','fetchHistory'])c[fn]=()=>{};
 c.showToast=()=>{};c.renderAccountData(account());
 return {c,node,storage,requests,timers,advance:ms=>{now+=ms},bumpVersion:()=>{version++},preview:fn=>{preview=fn},order:fn=>{order=fn},read:code=>vm.runInContext(code,c),opens:()=>requests.filter(r=>r.url==='/api/order/open')};
}
(async()=>{
 {
  const h=harness(),c=h.c;c.updateOrderButtons();assert.equal(h.node('order-buy-btn').disabled,true);
  c.renderPriceData(tick());assert.equal(h.node('order-buy-btn').disabled,true,'quote alone cannot authorize trading');
  await c.refreshTradePreview();assert.equal(h.node('order-buy-btn').disabled,false);assert.equal(h.node('order-sell-btn').disabled,false);
  c.clearSymbolPrices();assert.equal(h.node('order-buy-btn').disabled,true,'clearing the quote immediately locks the button');c.renderPriceData(tick());
  assert.deepEqual(h.requests.map(r=>r.payload.order_type),['BUY','SELL']);
  assert.deepEqual(h.requests[0].payload.expected_account,[12345,'Broker-Demo','DEMO']);
  await c.submitOrder('BUY');assert.equal(h.opens().length,1);assert.equal(h.requests.length,5,'click performs a fresh preview before submission');
  assert.equal(h.storage.has('order-intent:EURUSD'),false);
  h.advance(5000);c.updateQuoteStatus();assert.equal(h.node('order-buy-btn').disabled,true);
  await c.submitOrder('SELL');assert.equal(h.opens().length,1,'expired connection verification cannot send another order');
 }
 for(const bad of [tick({quote_status:null}),tick({bid:0}),tick({ask:1}),tick({bid:NaN}),tick({quote_status:{state:'stale',broker_connected:true,age_seconds:10}}),tick({quote_status:{state:'future',broker_connected:true,age_seconds:-1}}),tick({quote_status:{state:'fresh',broker_connected:false,age_seconds:0}}),tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:'0'}}),tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:11}})]){
  const h=harness();h.c.renderPriceData(bad);await h.c.refreshTradePreview();assert.equal(h.node('order-buy-btn').disabled,true);
  await h.c.submitOrder('BUY');await h.c.submitPendingOrder();assert.equal(h.opens().length,0);assert.equal(h.storage.size,0,'failed preflight creates no durable trading intent');
  assert.ok(h.requests.every(r=>r.url==='/api/trade/preview'),'invalid quotes must block pending orders too');
 }
 {
  const h=harness();h.c.renderPriceData(tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:9}}));await h.c.refreshTradePreview();h.advance(2000);h.c.updateOrderButtons();assert.equal(h.node('order-buy-btn').disabled,true,'server quote age advances independently of broker timestamp');
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.preview(r=>response(r.payload.order_type==='BUY'?{success:false,error:'BUY margin unavailable'}:success));
  await h.c.refreshTradePreview();assert.equal(h.node('order-buy-btn').disabled,true);assert.equal(h.node('order-sell-btn').disabled,false);
  await h.c.submitOrder('BUY');assert.equal(h.opens().length,0);assert.equal(h.storage.size,0);
  await h.c.submitOrder('SELL');assert.equal(h.opens().length,1,'BUY failure must not block independently verified SELL');
 }
 for(const change of [h=>{h.node('lot-input').value='.02'},h=>{h.read('currentSymbol="BTCUSD"')},h=>{h.c.renderAccountData(account(54321))},h=>{h.bumpVersion()},h=>{h.advance(5000)}]){
  const h=harness(),pending=[];h.c.renderPriceData(tick());h.preview(()=>new Promise(resolve=>pending.push(resolve)));
  const first=h.c.submitOrder('BUY');assert.equal(h.requests.length,2);assert.equal(h.storage.size,0);
  await h.c.submitOrder('BUY');assert.equal(h.requests.length,2,'double-click during preflight cannot start a second submission');
  change(h);pending.forEach(resolve=>resolve(response(success)));await first;assert.equal(h.opens().length,0);assert.equal(h.storage.size,0);
 }
 {
  const h=harness();h.c.renderPriceData(tick());let pending=[];h.preview(()=>new Promise(resolve=>pending.push(resolve)));
  const first=h.c.submitOrder('BUY');h.c.scheduleTradePreview('SELL');pending.forEach(resolve=>resolve(response(success)));await first;
  assert.equal(h.opens().length,0,'superseded preview cannot authorize a click');
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.preview(r=>new Promise((resolve,reject)=>r.signal.addEventListener('abort',()=>reject(Object.assign(new Error('timeout'),{name:'AbortError'})))));
  const first=h.c.submitOrder('BUY');for(const t of [...h.timers.values()].filter(t=>t.delay===5000))t.fn();await first;
  assert.equal(h.opens().length,0);assert.equal(h.storage.size,0);assert.equal(h.read('orderPreflightPending'),false);
  h.preview(()=>response(success));h.c.renderPriceData(tick());await h.c.refreshTradePreview();assert.equal(h.opens().length,0,'recovery never automatically sends an order');
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.order(()=>({ok:false,json:async()=>({detail:'Origin rejected',reason_code:'untrusted_origin',not_submitted:true})}));
  await h.c.submitOrder('BUY');assert.equal(h.storage.has('order-intent:EURUSD'),false,'definitive pre-handler rejection needs no broker recovery');
  h.order(()=>{throw new Error('response lost')});await h.c.submitOrder('BUY');assert.equal(h.storage.has('order-intent:EURUSD'),true,'unknown outcomes retain their identity');
 }
 console.log('Order readiness integration: current quotes, per-side risk, account/input/catalogue races, double click, timeout and definitive rejection PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
