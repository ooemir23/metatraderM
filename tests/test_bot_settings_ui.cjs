// Bot settings form = HMA_Crossover_EA inputs only; the payload carries no non-EA feature; rejected saves keep the form open.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),notices=[],sent=[];
 const node=id=>{if(!nodes.has(id)){const classes=new Set(id==='bot-settings-modal'?['hidden']:[]);nodes.set(id,{value:'',checked:false,innerText:'',textContent:'',dataset:{},classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c),toggle(){}}});}return nodes.get(id);};
 const status={is_running:false,symbol:'XAUUSD',timeframe_minutes:60,hma_period:14,second_ma_type:'KAMA',second_ma_period:10,lot_size:.01,sl_points:200,tp_points:400,close_opposite:true,use_stop_loss:true,use_take_profit:true,logs:[]};
 let reject=false;
 const ctx=vm.createContext({console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},setInterval(){},setTimeout(){},fetch:async(url,opts)=>{
  if(url==='/api/bot/status')return {ok:true,json:async()=>status};
  if(url==='/api/bot/config')sent.push(JSON.parse(opts.body));return {ok:!reject,json:async()=>({detail:'Önce botu durdurun.'})};
 }});
 vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),ctx);ctx.showToast=(text,type)=>notices.push({text,type});
 await ctx.toggleBotSettingsModal();
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),false);
 assert.equal(node('cfg-ma2-type').value,'EMA','old saved KAMA is shown as the EA default EMA');
 assert.equal(node('cfg-ma2-period').value,10);assert.equal(node('cfg-timeframe').value,60);
 node('cfg-sl-points').value=150;
 await ctx.saveBotSettings();
 assert.equal(sent[0].sl_points,150);assert.equal(sent[0].use_take_profit,true);
 for(const key of ['risk_mode','kama_fast','use_atr_filter','use_h4_filter','lot_mode','max_consecutive_losses','trade_hours_enabled','blocked_windows'])
   assert.ok(!(key in sent[0]),`${key} is not an EA input`);
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),true);
 await ctx.toggleBotSettingsModal();reject=true;await ctx.saveBotSettings();
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),false);
 assert.match(notices.at(-1).text,/Önce botu durdurun/);assert.equal(notices.at(-1).type,'error');
 const html=fs.readFileSync('app/static/index.html','utf8');
 for(const id of ['cfg-kama-fast','cfg-atr-filter','cfg-h4-filter','cfg-risk-mode','cfg-lot-mode','cfg-max-losses','cfg-blocked'])
   assert.ok(!html.includes(`id="${id}"`),`${id} removed`);
 assert.ok(!html.includes('<option value="KAMA">'),'KAMA is not an EA 2nd MA type');
 console.log('Bot settings UI: EA inputs only, no non-EA payload, rejected-save feedback PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
