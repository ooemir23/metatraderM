const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const root = {rows:[], replaceChildren(){this.rows=[];}, append(row){this.rows.push(row);}};
  const badge = {textContent:''}, notices = [];
  let events = [], language = 'tr';
  const ctx = vm.createContext({console, Date,
    window:{MT5I18n:{language:()=>language, translate:text=>text}},
    document:{addEventListener(){}, getElementById:id=>id==='operations-events'?root:badge,
      createElement:()=>({textContent:'',className:''})},
    showToast:(message,tone)=>notices.push({message,tone}),
    fetch:async()=>({ok:true,json:async()=>events}), setInterval(){},setTimeout(){},clearTimeout(){}});
  vm.runInContext(fs.readFileSync('app/static/operations.js','utf8'),ctx);
  await ctx.refreshOperationEvents();
  events.push({id:1,created:1,level:'warning',kind:'operator',message:'admin POST /api/trade/preview HTTP 400'});
  await ctx.refreshOperationEvents();
  assert.equal(notices.length,0,'preview warning stays in panel without a toast');
  assert.match(root.rows[0].textContent,/HTTP 400/);
  events.push({id:2,created:2,level:'error',kind:'order',message:'rejected',request_id:'order-1',
    symbol:'EURUSD',detail:'Bu sembolde güncel fiyat alınmıyor.'},
    {id:3,created:3,level:'warning',kind:'operator',message:'admin POST /api/order/open HTTP 400'});
  await ctx.refreshOperationEvents();
  assert.deepEqual(notices[0],{message:'EURUSD · Reddedildi: Bu sembolde güncel fiyat alınmıyor.',tone:'error'});
  events.push({id:4,created:4,level:'warning',kind:'order',message:'partial',request_id:'order-2'});
  await ctx.refreshOperationEvents();
  assert.deepEqual(notices[1],{message:'Kısmi gerçekleşme',tone:'warning'});
  events.push({id:5,created:5,level:'warning',kind:'order',message:'partial',request_id:'order-2'});
  await ctx.refreshOperationEvents();
  assert.equal(notices.length,2,'same request must not repeat a notification immediately');
  language = 'en';
  events.push({id:6,created:6,level:'error',kind:'connection',message:'MT5 connection lost'});
  await ctx.refreshOperationEvents();
  assert.deepEqual(notices[2],{message:'MT5 connection lost',tone:'error'});
  await ctx.refreshOperationEvents();
  assert.equal(notices.length,3,'polling unchanged events must not repeat toasts');
  events.push({id:7,created:7,level:'error',kind:'order',message:'rejected',request_id:'order-2',detail:'Broker rejected the remaining volume.'});
  await ctx.refreshOperationEvents();
  assert.equal(notices.length,4,'a changed outcome for the same request must still alert');
  assert.match(notices[3].message,/Broker rejected the remaining volume/);
  console.log('Event notices: actual reason, severity and duplicate suppression PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
