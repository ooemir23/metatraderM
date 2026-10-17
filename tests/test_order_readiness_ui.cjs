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
  c.renderPriceData(tick());assert.equal(h.node('order-buy-btn').disabled,false,'verified quote enables trading directly without pre-trade risk gate');
  assert.equal(h.node('order-sell-btn').disabled,false);
  c.clearSymbolPrices();assert.equal(h.node('order-buy-btn').disabled,true,'clearing the quote immediately locks the button');c.renderPriceData(tick());
  await c.submitOrder('BUY');assert.equal(h.opens().length,1);assert.equal(h.requests.length,1,'click submits directly without preflight preview');
  assert.deepEqual(h.opens()[0].payload.expected_account,[12345,'Broker-Demo','DEMO']);
  assert.equal(h.storage.has('order-intent:EURUSD'),false);
  h.advance(5000);c.updateQuoteStatus();assert.equal(h.node('order-buy-btn').disabled,true);
  await c.submitOrder('SELL');assert.equal(h.opens().length,1,'expired connection verification cannot send another order');
 }
 for(const bad of [tick({quote_status:null}),tick({bid:0}),tick({ask:1}),tick({bid:NaN}),tick({quote_status:{state:'future',broker_connected:true,age_seconds:-1}}),tick({quote_status:{state:'fresh',broker_connected:false,age_seconds:0}})]){
  const h=harness();h.c.renderPriceData(bad);assert.equal(h.node('order-buy-btn').disabled,true);
  await h.c.submitOrder('BUY');await h.c.submitPendingOrder();assert.equal(h.opens().length,0);assert.equal([...h.storage.keys()].filter(k=>k.includes('intent:')).length,0,'invalid quote creates no durable trading intent');
  assert.equal(h.requests.length,0,'invalid quotes must block pending orders too');
 }
 for(const stale of [tick({quote_status:{state:'stale',broker_connected:true,age_seconds:10}}),tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:'0'}}),tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:11}})]){
  const h=harness();h.c.renderPriceData(stale);assert.equal(h.node('order-buy-btn').disabled,false,'MT5 manual mode stays enabled when broker is connected and quote is valid');
  h.node('order-rule-mode').value='PANEL';h.c.updateOrderButtons();assert.equal(h.node('order-buy-btn').disabled,true,'PANEL mode blocks stale quotes');
 }
 {
  const h=harness();h.node('order-rule-mode').value='PANEL';h.c.renderPriceData(tick({quote_status:{state:'fresh',broker_connected:true,age_seconds:9}}));h.advance(2000);h.c.updateOrderButtons();assert.equal(h.node('order-buy-btn').disabled,true,'server quote age advances independently of broker timestamp in PANEL mode');
 }
 {
  const h=harness();h.c.renderPriceData(tick());
  let release;h.order(()=>new Promise(resolve=>{release=resolve}));
  const first=h.c.submitOrder('BUY');assert.equal(h.opens().length,1);
  await h.c.submitOrder('BUY');assert.equal(h.opens().length,1,'double-click during order submission cannot start a second submission');
  release(response({success:true,request_id:'confirmed-request',ticket:7}));await first;
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.order(()=>({ok:false,json:async()=>({detail:'Origin rejected',reason_code:'untrusted_origin',not_submitted:true})}));
  await h.c.submitOrder('BUY');assert.equal(h.storage.has('order-intent:EURUSD'),false,'definitive pre-handler rejection needs no broker recovery');
  h.order(()=>{throw new Error('response lost')});await h.c.submitOrder('BUY');assert.equal(h.storage.has('order-intent:EURUSD'),true,'unknown outcomes retain their identity');
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.node('order-stop-mode').value='price';
  h.node('sl-input').value='1.099';h.node('tp-input').value='1.102';h.node('order-deviation').value='37';
  await h.c.submitOrder('BUY');assert.equal(h.opens().length,1);
  const sent=h.opens()[0].payload;
  assert.equal(sent.sl_price,1.099);assert.equal(sent.tp_price,1.102);assert.equal(sent.sl_points,0);assert.equal(sent.deviation_points,37);
  h.c.changeOrderStopMode();assert.equal(h.node('sl-input').value,'0');assert.equal(h.node('tp-input').value,'0');
 }
 {
  const h=harness();h.c.window.MT5Markets.getSymbol=()=>({digits:5,volume_min:.001,volume_max:50,volume_step:.001});
  h.node('lot-input').value='.001';h.c.adjustLot(.01);assert.equal(h.node('lot-input').value,'0.002');
  h.c.adjustLot(-.01);assert.equal(h.node('lot-input').value,'0.001');h.c.adjustLot(-.01);assert.equal(h.node('lot-input').value,'0.001');
  h.c.updateLotRules();assert.equal(h.node('lot-input').step,'0.001');assert.equal(h.node('pending-volume').min,'0.001');
 }
 {
  const h=harness();h.c.renderPriceData(tick());let release;
  h.c.verifyChartOrderSymbol=()=>new Promise(resolve=>{release=resolve});
  const attempt=h.c.submitPendingOrder();await h.c.submitPendingOrder();
  h.node('pending-type').value='BUY_STOP';release(true);await attempt;
  assert.equal(h.requests.filter(r=>r.url==='/api/order/pending').length,0,'changed pending type must not submit a stale ticket');
  assert.equal(h.read('pendingOrderPreflight'),false);
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.c.renderAccountData({...account(),account_type:'REAL'});
  h.node('order-rule-mode').value='MT5';h.node('order-stop-mode').value='price';h.node('sl-input').value='0';h.node('tp-input').value='0';h.node('order-deviation').value='20';
  await h.c.submitOrder('BUY');assert.equal(h.opens().length,1,'MT5 manual real mode permits optional SL');
  assert.equal(h.opens()[0].payload.manual_mode,'MT5');assert.equal(h.opens()[0].payload.sl_price,0);
  h.node('order-rule-mode').value='PANEL';await h.c.submitOrder('BUY');assert.equal(h.opens().length,1,'panel mode retains stop requirement');
 }
 {
  const h=harness();h.c.renderPriceData(tick());h.node('pending-type').value='BUY_STOP_LIMIT';h.node('pending-price').value='1.11';h.node('pending-stoplimit').value='1.105';
  h.node('pending-time-type').value='SPECIFIED';h.node('pending-expiration').value='2030-01-01T10:00';h.node('pending-stop-mode').value='price';h.node('pending-sl').value='0';h.node('pending-tp').value='0';
  await h.c.submitPendingOrder();const req=h.requests.find(r=>r.url==='/api/order/pending');
  assert.ok(req);assert.equal(req.payload.pending_type,'BUY_STOP_LIMIT');assert.equal(req.payload.stoplimit_price,1.105);assert.equal(req.payload.time_type,'SPECIFIED');assert.ok(req.payload.expiration>1700000000);
 }
 {
  const h=harness();h.node('order-rule-mode').value='PANEL';h.node('order-filling').value='IOC';h.node('order-deviation').value='35';h.c.saveOrderTicketPreferences();
  h.c.renderAccountData(account(54321));assert.equal(h.node('order-rule-mode').value,'MT5','new accounts default to native MT5 settings');
  h.c.renderAccountData(account());assert.equal(h.node('order-rule-mode').value,'PANEL');assert.equal(h.node('order-filling').value,'IOC');assert.equal(h.node('order-deviation').value,'35');
 }
 {
  const h=harness();h.node('pending-type').options=[{value:'BUY_LIMIT'},{value:'BUY_STOP'},{value:'BUY_STOP_LIMIT'}];
  h.node('pending-time-type').options=[{value:'GTC'},{value:'DAY'}];h.node('order-filling').options=[{value:'AUTO'},{value:'FOK'},{value:'IOC'},{value:'RETURN'}];
  h.c.window.MT5Markets.getSymbol=()=>({order_mode:3,expiration_mode:1,execution_mode:2,filling_mode:2});h.c.updateBrokerTicketChoices();
  assert.deepEqual(h.node('pending-type').options.map(x=>x.disabled),[false,true,true]);assert.deepEqual(h.node('pending-time-type').options.map(x=>x.disabled),[false,true]);
  assert.deepEqual(h.node('order-filling').options.map(x=>x.disabled),[false,true,false,true]);
 }
 {
  const h=harness();for(const id of ['pending-editor','pending-edit-limit-field','pending-edit-expiration-field']) {h.node(id).showModal=()=>{};h.node(id).close=()=>{h.node(id).closed=true};}
  h.c.renderPendingOrders([{ticket:432,symbol:'EURUSD',type:'BUY_STOP_LIMIT',volume:.01,price:1.11,sl:0,tp:0,stoplimit:1.105,type_time:0,expiration:0,magic:123460}]);
  h.c.showPendingOrderEditor(432);h.node('pending-edit-price').value='1.12';h.node('pending-edit-stoplimit').value='1.106';
  await h.c.savePendingOrder();const req=h.requests.find(r=>r.url==='/api/order/modify');assert.ok(req);assert.equal(req.payload.entry_price,1.12);assert.equal(req.payload.stoplimit_price,1.106);
  assert.equal(req.payload.expected_state[5],1.11);assert.deepEqual(req.payload.expected_account,[12345,'Broker-Demo','DEMO']);
  const count=h.requests.length;h.c.renderAccountData(account(54321));await h.c.savePendingOrder();assert.equal(h.requests.length,count,'an old pending editor cannot act on another account');
 }
 console.log('Order readiness integration: current quotes, per-side risk, account/input/catalogue races, double click, timeout and definitive rejection PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
