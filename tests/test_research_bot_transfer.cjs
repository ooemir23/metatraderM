const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),sent=[],notices=[];
 const node=id=>{if(!nodes.has(id)){const classes=new Set(id==='bot-settings-modal'?['hidden']:[]);nodes.set(id,{value:'',checked:false,innerText:'',textContent:'',classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)}});}return nodes.get(id);};
 let accountType='DEMO',running=false;
 const status={is_running:false,symbol:'EURUSD',timeframe_minutes:15,hma_period:14,second_ma_type:'EMA',second_ma_period:34,lot_size:.01,logs:[]};
 const ctx=vm.createContext({console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},setInterval(){},setTimeout(){},fetch:async(url,opts)=>{
  if(opts)sent.push({url,opts});
  return {ok:true,json:async()=>url==='/api/account'?{connected:true,account_mismatch:false,account_type:accountType}:{...status,is_running:running}};
 }});
 vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),ctx);ctx.showToast=(message)=>notices.push(message);
 vm.runInContext(fs.readFileSync('app/static/research.js','utf8'),ctx);
 const report={symbol:'XAUUSD',settings:{hma_period:21,kama_period:10,kama_fast:2,kama_slow:30,atr_period:14,er_min:.4,stop_atr:2.5,target_atr:0,lot_size:.02,initial_capital:10000},selected:{config:{mode:'H1_H4',filtered:true,wait:3,distance_atr:.2}}};
 const plan=ctx.researchBotPlan(report);
 assert.equal(plan.use_h4_filter,true);assert.equal(plan.timeframe_minutes,60);assert.equal(plan.use_take_profit,false);
 assert.equal(plan.single_position,true);assert.equal(plan.second_ma_period,10);assert.ok(!('initial_capital' in plan));
 for(const type of ['DEMO','REAL']){
  accountType=type;await ctx.previewResearchBotSettings(plan,'Validation failed.');
  assert.equal(node('cfg-symbol').value,'XAUUSD');assert.equal(node('cfg-h4-filter').checked,true);
  assert.equal(node('cfg-confirmation-bars').value,3);assert.equal(node('cfg-lot').value,.02);
  assert.ok(node('bot-research-transfer-note').textContent.includes(type));
  assert.ok(node('bot-research-transfer-note').textContent.includes('Validation failed.'));
 }
 assert.equal(sent.length,0,'Review must not save or start the bot');
 running=true;await ctx.previewResearchBotSettings(plan,'');assert.equal(node('bot-settings-modal').classList.contains('hidden'),true);
 assert.ok(notices.at(-1).includes('durdurun'));
 assert.throws(()=>ctx.researchBotPlan({...report,selected:null}));
 console.log('Research to bot: exact snapshot, H1/H4, Demo/Real review and no automatic writes PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
