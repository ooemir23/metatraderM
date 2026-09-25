const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const nodes = new Map([
    ['trade-preview', {textContent:'Previous broker estimate'}],
    ['lot-input', {value:'0.01'}],
    ['sl-input', {value:'200'}],
  ]);
  const requests = [];
  let nextTimer = 0;
  const timers = new Map();
  const context = vm.createContext({
    window:{currentSymbol:'XAUUSD',MT5I18n:{language:()=> 'en'}},
    document:{getElementById:id=>nodes.get(id),addEventListener(){}},
    currentSymbol:'XAUUSD',
    setTimeout:callback=>{const id=++nextTimer;timers.set(id,callback);return id;},
    clearTimeout:id=>timers.delete(id),
    setInterval(){},
    fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,payload:JSON.parse(options.body),resolve,reject})),
  });
  vm.runInContext(fs.readFileSync('app/static/operations.js','utf8'),context);
  const label = nodes.get('trade-preview');

  const first = context.refreshTradePreview();
  assert.equal(label.textContent,'Previous broker estimate','pending request must not collapse the panel');
  context.scheduleTradePreview('SELL');
  assert.equal(timers.size,1);
  const scheduled = [...timers.values()][0];
  timers.clear();
  const second = scheduled();
  assert.equal(requests[1].payload.order_type,'SELL');
  requests[1].resolve({ok:true,json:async()=>({success:true,currency:'USD',stop_risk:5,risk_pct_equity:0.1,
    margin_required:8,spread_points:2,existing_stop_risk:0,daily_loss:0,positions_without_stop:0})});
  await second;
  assert.match(label.textContent,/Stop risk 5 USD/);
  const currentText = label.textContent;
  requests[0].reject(new Error('Old request failed'));
  await first;
  assert.equal(label.textContent,currentText,'stale error must not erase the current estimate');

  const third = context.refreshTradePreview();
  assert.equal(label.textContent,currentText,'manual refresh must preserve the displayed estimate');
  requests[2].resolve({ok:true,json:async()=>({success:true,currency:'USD',stop_risk:6,risk_pct_equity:0.2,
    margin_required:8,spread_points:3,existing_stop_risk:0,daily_loss:0,positions_without_stop:0})});
  await third;
  assert.match(label.textContent,/Stop risk 6 USD/);
  console.log('Risk preview UI: stable refresh and stale response protection PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
