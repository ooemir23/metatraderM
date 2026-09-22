const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),storage=new Map(),sent=[],sources=[];
 const node=id=>{if(!nodes.has(id)) nodes.set(id,{value:'',innerHTML:'',innerText:'',textContent:'',disabled:false,dataset:{},className:'',open:false,
   classList:{add(){},remove(){},toggle(){}},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);};
 let reply={success:true,request_id:'test-request'};
 class Source {constructor(url){this.url=url;sources.push(this);}close(){this.closed=true;}}
 const ctx=vm.createContext({console,Date,AbortController,crypto:require('node:crypto').webcrypto,EventSource:Source,
 document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){}},
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 setInterval(){},setTimeout(){},clearTimeout(){},confirm:()=>true,
 fetch:async(url,opts)=>{sent.push({url,...JSON.parse(opts?.body || "{}")});return {ok:true,json:async()=>reply};}});
 for(const name of ['app','trading']) vm.runInContext(fs.readFileSync(`app/static/${name}.js`,'utf8'),ctx);
 ctx.showToast=()=>{};ctx.fetchPositions=()=>{};ctx.fetchAccount=()=>{};ctx.fetchPendingOrders=()=>{};
 const position={ticket:1,symbol:'EURUSD',type:'BUY',volume:.03,sl:1.08,tp:1.12};
 ctx.rememberPositions([position]);ctx.showPositionEditor(1);
 assert.equal(node('position-editor').open,true);
 node('edit-sl').value='1.085';await ctx.savePositionStops();
 assert.equal(sent[0].sl,1.085);assert.equal(sent[0].tp,1.12);assert.equal(sent[0].expected_sl,1.08);
 node('partial-volume').value='.03';await ctx.partialClosePosition();assert.equal(sent.length,1);
 node('partial-volume').value='.01';await ctx.partialClosePosition();
 assert.equal(sent[1].volume,.01);assert.equal(node('position-editor').open,false);
 node('pending-type').value='SELL_STOP';node('pending-volume').value='.01';node('pending-price').value='1.07';node('pending-sl').value='200';node('pending-tp').value='400';
 reply={success:false,uncertain:true,request_id:'unknown'};
 await ctx.submitPendingOrder();const first=sent.at(-1);
 await ctx.submitPendingOrder();assert.equal(sent.at(-1).request_id,first.request_id);
 assert.equal(first.order_type,'SELL');assert.equal(first.pending_type,'SELL_STOP');
 node('pending-price').value='1.06';const count=sent.length;await ctx.submitPendingOrder();assert.equal(sent.length,count);
 let renders=0;ctx.renderAccountData=()=>renders++;ctx.renderPositionsData=()=>{};ctx.renderPriceData=()=>{};ctx.renderPendingOrders=()=>{};
 ctx.startLiveFeed();const old=sources.at(-1);ctx.startLiveFeed();const current=sources.at(-1);
 assert.ok(old.closed);
 old.onmessage({data:JSON.stringify({account:{},positions:[],orders:[],prices:{}})});assert.equal(renders,0);
 current.onmessage({data:JSON.stringify({account:{},positions:[],orders:[],prices:{}})});assert.equal(renders,1);assert.equal(ctx.liveFeedHealthy(),true);
 current.onerror();assert.equal(ctx.liveFeedHealthy(),false);
 storage.set('order-intent:EURUSD',JSON.stringify({request_id:'lost-open'}));
 reply={found:true,uncertain:true};await ctx.checkReconciledOrders();
 assert.ok(storage.has('order-intent:EURUSD'));
 reply={found:true,reconciled:true,pending:true,success:true};await ctx.checkReconciledOrders();
 assert.ok(storage.has('order-intent:EURUSD'));
 reply={found:true,reconciled:true,pending:false,uncertain:false,success:true,ticket:42};
 const beforeCheck=sent.length;await ctx.checkReconciledOrders();
 assert.equal(storage.has('order-intent:EURUSD'),false);
 assert.equal(storage.has('managed-intent:pending:EURUSD'),false);
 assert.ok(sent.slice(beforeCheck).every(x=>x.url.startsWith('/api/operations/status?')),'reconciliation only reads');
 console.log('Position management, pending intent reuse and SSE lifecycle PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
