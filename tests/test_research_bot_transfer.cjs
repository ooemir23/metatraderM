// The KAMA/ATR research is an analysis tool: its strategy is not in HMA_Crossover_EA, so nothing is sent to the bot.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const sent=[],notices=[];
const ctx=vm.createContext({console,document:{getElementById:()=>({}),querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},
  setInterval(){},setTimeout(){},fetch:async(url,opts)=>{sent.push(url);return {ok:true,json:async()=>({})};}});
vm.runInContext(fs.readFileSync('app/static/research.js','utf8'),ctx);
ctx.showToast=m=>notices.push(m);
ctx.reviewResearchBotSettings();
assert.equal(sent.length,0,'no bot settings are written');
assert.match(notices[0],/MT5 EA/);
const html=fs.readFileSync('app/static/index.html','utf8');
assert.ok(!/onclick="reviewResearchBotSettings\(\)"/.test(html),'no transfer button');
console.log('Research to bot: KAMA/ATR research is not transferred to the EA bot PASS');
