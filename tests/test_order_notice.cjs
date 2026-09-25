const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map();
 function node(id){if(!nodes.has(id)) {const classes=new Set();nodes.set(id,{value:'0.01',dataset:{},disabled:false,textContent:'',innerText:'',offsetWidth:1,
 classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)}});}return nodes.get(id);}
 let release,calls=0;
 const storage=new Map();
 const ctx=vm.createContext({console,Date,AbortController,crypto:require("node:crypto").webcrypto,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){}},setInterval(){},setTimeout(){},clearTimeout(){},
 fetch:async()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
 vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),ctx);
 assert.deepEqual(Array.from(ctx.orderErrorMessage({reason_code:'trading_halted',detail:'Safety lock'})),
   ['Yeni emirler durduruldu','Safety lock']);
 ctx.initTradingView=()=>{};ctx.fetchPrice=()=>{};ctx.fetchPositions=()=>{};ctx.fetchAccount=()=>{};
 const request=ctx.submitOrder('BUY');
 assert.equal(node('order-buy-btn').disabled,true);
 await ctx.submitOrder('BUY');assert.equal(calls,1);
 release({ok:false,json:async()=>({request_id:'test-id',detail:'Market closed',retcode:10018})});await request;
 assert.match(node('order-notice-title').textContent,/piyasa kapalı/);
 assert.equal(node('order-notice').classList.contains('order-notice-flash'),true);
 assert.equal(node('order-buy-btn').disabled,false);
 ctx.switchSymbol('XAUUSD');assert.equal(node('order-notice-title').textContent,'İşlem durumu');
 ctx.switchSymbol('EURUSD');assert.match(node('order-notice-title').textContent,/piyasa kapalı/);
 const pending=ctx.submitOrder('SELL');ctx.switchSymbol('GBPUSD');
 release({ok:false,json:async()=>({request_id:'test-id',detail:'Not enough money',retcode:10019})});await pending;
 assert.equal(node('order-notice-title').textContent,'İşlem durumu');
 ctx.switchSymbol('EURUSD');assert.match(node('order-notice-title').textContent,/Yetersiz teminat/);
 const halted=ctx.submitOrder('BUY');
 release({ok:false,json:async()=>({reason_code:'trading_halted',detail:'Safety lock'})});await halted;
 assert.equal(storage.has('order-intent:EURUSD'),false,'definitive safety rejection clears the pending intent');
 assert.equal(node('order-reset-btn').hidden,true,'safety rejection does not require MT5 recovery');
 console.log('Order feedback: localization, animation, duplicate prevention and symbol isolation PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
