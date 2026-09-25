const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const nodes = new Map(), calls = [], confirmations = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'',textContent:'',disabled:false,open:false,
      classList:{toggle(){}},showModal(){this.open=true;},close(){this.open=false;}});
    return nodes.get(id);
  };
  let risk = {daily_loss:96640,daily_loss_limit:500,currency:'USD',login:25373161,server:'Tickmill-Demo'};
  let halted = true;
  const context = vm.createContext({console,Date,AbortController,
    window:{MT5I18n:{language:()=> 'tr'},addEventListener(){}},
    document:{getElementById:node,addEventListener(){},querySelectorAll:()=>[]},
    localStorage:{getItem(){return null}},setTimeout(){},clearTimeout(){},setInterval(){},
    confirmAction:async options=>{confirmations.push(options);return true;},showToast(){},
    fetch:async(url,options)=>{
      calls.push({url,body:options?.body && JSON.parse(options.body)});
      if (url === '/api/risk/status') return {ok:true,json:async()=>risk};
      if (url === '/api/trading/status') return {ok:true,json:async()=>({new_orders_halted:halted})};
      if (url === '/api/auth/me') return {ok:true,json:async()=>({role:'ADMIN'})};
      if (url === '/api/risk/daily-limit') {
        risk = {...risk,daily_loss_limit:JSON.parse(options.body).amount};
        return {ok:true,json:async()=>risk};
      }
      if (url === '/api/trading/resume') {
        halted = false;
        return {ok:true,json:async()=>({new_orders_halted:false})};
      }
      throw Error(url);
    },
  });
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
  context.showToast=()=>{};
  await context.openTradingRiskDialog();
  assert.equal(node('trading-risk-dialog').open,true);
  assert.equal(node('trading-risk-resume').disabled,true,'loss above limit keeps resume unavailable');
  assert.match(node('trading-risk-loss').textContent,/96\.640/);
  node('trading-risk-limit-input').value='100000';
  await context.saveTradingDailyLimit();
  assert.equal(calls.find(call=>call.url==='/api/risk/daily-limit').body.acknowledge_risk,true);
  assert.equal(confirmations.length,1,'raising an exceeded limit requires confirmation');
  assert.equal(node('trading-risk-resume').disabled,false);
  await context.resumeTrading();
  assert.equal(halted,false);
  assert.equal(node('trading-risk-dialog').open,false);
  console.log('Trading risk dialog: exceeded limit, explicit change and resume PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
