const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),notices=[],sent=[];
 const node=id=>{if(!nodes.has(id)){const classes=new Set(id==='bot-settings-modal'?['hidden']:[]);nodes.set(id,{value:'',checked:false,innerText:'',textContent:'',classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)}});}return nodes.get(id);};
 const status={is_running:false,symbol:'XAUUSD',timeframe_minutes:60,hma_period:14,second_ma_type:'KAMA',second_ma_period:10,lot_size:.01,kama_fast:2,kama_slow:30,atr_period:14,er_min:.3,confirmation_bars:2,min_distance_atr:.1,risk_mode:'ATR',atr_stop_multiplier:2,atr_target_multiplier:3,sl_points:200,tp_points:400,close_opposite:true,use_atr_filter:true,use_stop_loss:true,use_take_profit:true,current_atr:12.5,current_er:.4,logs:[]};
 let reject=false;
 const ctx=vm.createContext({console,document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},setInterval(){},setTimeout(){},fetch:async(url,opts)=>{
  if(url==='/api/bot/status')return {ok:true,json:async()=>status};
  sent.push(JSON.parse(opts.body));return {ok:!reject,json:async()=>({detail:'Önce botu durdurun.'})};
 }});
 vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),ctx);ctx.showToast=(text,type)=>notices.push({text,type});
 await ctx.toggleBotSettingsModal();
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),false);
 assert.equal(node('cfg-ma2-type').value,'KAMA');assert.equal(node('cfg-ma2-period').value,10);
 assert.equal(node('cfg-timeframe').value,60);assert.equal(node('cfg-atr-filter').checked,true);
 assert.match(node('bot-atr-status').textContent,/ATR\(14\): 12.50000/);
 node('cfg-confirmation-bars').value=3;
 await ctx.saveBotSettings();
 assert.equal(sent[0].confirmation_bars,3);assert.equal(sent[0].risk_mode,'ATR');assert.equal(sent[0].kama_fast,2);
 assert.equal(sent[0].use_take_profit,true);assert.equal(sent[0].atr_target_multiplier,3);
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),true);
 await ctx.toggleBotSettingsModal();reject=true;await ctx.saveBotSettings();
 assert.equal(node('bot-settings-modal').classList.contains('hidden'),false);
 assert.match(notices.at(-1).text,/Önce botu durdurun/);assert.equal(notices.at(-1).type,'error');
 console.log('Bot settings UI: server values, KAMA/ATR payload and rejected-save feedback PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
