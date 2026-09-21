const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const storage = new Map(), nodes = new Map(), sent = [], notices = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:id==='lot-input'?'0.01':'0', disabled:false,
      textContent:'',innerText:'',innerHTML:'',dataset:{},classList:{add(){},remove(){},toggle(){}}});
    return nodes.get(id);
  };
  let reply = () => {throw new Error('network response lost');};
  const context = vm.createContext({console,Date,AbortController,crypto:require('node:crypto').webcrypto,
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),get length(){return storage.size},key:i=>Array.from(storage.keys())[i]},
    document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){}},
    setTimeout(){},clearTimeout(){},setInterval(){},confirmAction:async()=>true,
    fetch:async(url,opts)=>{sent.push({url,payload:JSON.parse(opts.body)});return reply();}});
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
  context.fetchPositions=()=>{};context.fetchAccount=()=>{};context.fetchHistory=()=>{};
  context.showToast=(text,tone)=>notices.push({text,tone});
  await context.submitOrder('BUY');
  const id = sent[0].payload.request_id;
  assert.ok(id.length>=16);
  await context.submitOrder('BUY');
  assert.equal(sent[1].payload.request_id,id,'lost response must reuse the durable intent');
  await context.submitOrder('SELL');
  assert.equal(sent.length,2,'different trade is blocked until uncertain outcome is checked');
  reply = () => ({ok:true,json:async()=>({success:true,ticket:5,retcode:10009,request_id:id,replayed:true})});
  await context.submitOrder('BUY');
  assert.equal(storage.has('order-intent:EURUSD'),false);
  node('lot-input').value='0';
  await context.submitOrder('BUY');
  assert.equal(sent.length,3,'zero lot must not become 0.01');
  reply = () => ({ok:true,json:async()=>({success:false,closed_count:0,total_matched:0,errors:['MT5 offline'],request_id:'bulk-result'})});
  await context.closeFilteredPositions('all');
  assert.equal(notices.at(-1).tone,'error');
  assert.match(notices.at(-1).text,/MT5 offline/);
  reply = () => ({ok:true,json:async()=>({success:false,closed_count:1,total_matched:2,errors:['Ticket #2: rejected'],request_id:'bulk-result'})});
  await context.closeFilteredPositions('all');
  assert.equal(notices.at(-1).tone,'error');
  assert.match(notices.at(-1).text,/Ticket #2/);
  let release;
  reply = () => new Promise(resolve=>{release=resolve;});
  const closing=context.closePosition(42), before=sent.length;
  await context.closePosition(42);
  assert.equal(sent.length,before,'double click on close is suppressed');
  release({ok:false,json:async()=>({success:false,uncertain:true,error:'unknown',request_id:'close-42'})});
  await closing;
  assert.ok(storage.has('close-intent:42'));
  context.refreshOrderRecovery();
  assert.equal(node('order-reset-btn').hidden,false,'uncertain close exposes recovery');
  context.confirmAction=async()=>false;
  await context.clearUncertainOrder();
  assert.ok(storage.has('close-intent:42'),'cancel must preserve intent');
  context.confirmAction=async()=>true;
  await context.clearUncertainOrder();
  assert.equal(storage.has('close-intent:42'),false);
  assert.equal(node('order-reset-btn').hidden,true,'resolved errors do not expose recovery');
  storage.set('close-intent:99','old');
  context.confirmAction=async()=>{storage.set('close-intent:99','new');return true};
  await context.clearUncertainOrder();
  assert.equal(storage.get('close-intent:99'),'new','confirmation must not erase a newer request');
  console.log('Trade safety UI: uncertain intent reuse, invalid lot, partial close and double close PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
