const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerText:'', innerHTML:'', className:'', value:'30', dataset:{}});
    return nodes.get(id);
  };
  let response;
  const ctx = vm.createContext({console, Date, AbortController,
    localStorage:{getItem:()=>null},
    document:{addEventListener(){}, getElementById:node}, window:{addEventListener(){}},
    setTimeout(){}, clearTimeout(){}, setInterval(){},
    fetch:async()=>({ok:true, json:async()=>response})});
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  vm.runInContext('accountCurrency = "EUR"', ctx);
  const position = {ticket:22, symbol:'EURUSD', type:'BUY', volume:.1, profit:10, commission:-3.75};
  ctx.renderPositionsData([position]);
  assert.match(node('positions-table-body').innerHTML, /EUR -3,75/);
  ctx.renderPositionsData([{...position, commission:null}]);
  assert.match(node('positions-table-body').innerHTML, /—/);
  ctx.renderPositionsData([{...position, commission:0}]);
  assert.match(node('positions-table-body').innerHTML, /EUR 0,00/);
  response = [{ticket:1, symbol:'EURUSD', type:'SELL', profit:10, commission:-2, swap:-1, fee:-.5}];
  await ctx.fetchHistory();
  assert.equal(node('history-total-commission').innerText, 'EUR -2,00');
  assert.equal(node('history-total-profit').innerText, '+EUR 6,50');
  assert.match(node('history-table-body').innerHTML, /EUR -2,00/);
  assert.match(node('history-table-body').innerHTML, /EUR -1,50/);
  response = {currency:'EUR', summary:{total_commission:-5, total_swap:-1, total_fee:-.5}, daily:[], symbols:[]};
  await ctx.fetchReports();
  assert.equal(node('rep-total-commission').innerText, 'EUR -5,00');
  assert.equal(node('rep-total-fee').innerText, 'EUR -1,50');
  response = [];
  await ctx.fetchHistory();
  assert.equal(node('history-total-commission').innerText, 'EUR 0,00');
  assert.match(node('history-table-body').innerHTML, /colspan="9"/);
  console.log('Commission UI checks passed');
})().catch(err => {console.error(err); process.exitCode = 1;});
