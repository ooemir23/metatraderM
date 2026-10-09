const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerText:'', innerHTML:'', textContent:'', className:'', open:false,
      showModal(){this.open=true;}, close(){this.open=false;}});
    return nodes.get(id);
  };
  const context = vm.createContext({console,
    document:{addEventListener(){}, getElementById:node, createElement:()=>({set innerText(value){this.innerHTML=String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}})},
    window:{addEventListener(){}},
    fetch:async()=>({ok:true, json:async()=>[
      {ticket:1, symbol:'XAUUSD', type:'BUY', position_type:'SELL', entry:1, profit:3585, commission:-15},
      {ticket:2, symbol:'XAUUSD', type:'SELL', position_type:'BUY', entry:1, profit:115, commission:-15},
    ]})});
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), context);
  await context.fetchHistory();
  const rows = node('history-table-body').innerHTML.split('</tr>');
  assert.match(rows[0], />SELL<\/span>/);
  assert.match(rows[1], />BUY<\/span>/);
  assert.equal(node('history-count-badge').innerText, 2);
  assert.match(node('history-total-profit').innerText, /3\.670,00/);
  assert.match(rows[0], /onclick="showHistoryDetails/);
  const details = {position_id:11, symbol:'XAUUSD', currency:'USD', digits:2, deals:[
    {ticket:1, type:1, entry:0, volume:5, price:4178.37, time_text:'2026-10-09 10:00:00', commission:-15, comment:'<script>bad</script>'},
    {ticket:2, type:0, entry:1, volume:2, price:4171.20, time_text:'2026-10-09 12:00:00', profit:1434, commission:-6},
    {ticket:3, type:0, entry:1, volume:3, price:4170.20, time_text:'2026-10-09 15:00:00', profit:2451, commission:-9},
  ]};
  context.fetch = async()=>({ok:true,json:async()=>details});
  await context.showHistoryDetails(11, 2);
  const html=node('history-details-content').innerHTML;
  assert.equal(node('history-details-modal').open, true);
  assert.match(html,/SELL · Satış/);
  assert.match(html,/BUY · Alış/);
  assert.match(html,/4\.178,37/);
  assert.match(html,/4\.170,60/,'closing price is volume-weighted');
  assert.match(html,/USD 1\.428,00/,'selected partial close net');
  assert.match(html,/USD 3\.855,00/,'whole lifetime includes opening commission');
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
  const reversed=context.renderHistoryDetails({...details,deals:[...details.deals,{...details.deals[2],entry:2}]},2);
  assert.match(reversed,/Tersine dönüş var/);
  assert.match(reversed,/Tersine dönüş<span/);
  let release;
  context.fetch=()=>new Promise(resolve=>{release=resolve;});
  const pending=context.showHistoryDetails(11, 2);
  context.closeHistoryDetails();
  node('history-details-content').innerHTML='closed';
  release({ok:true,json:async()=>details});
  await pending;
  assert.equal(node('history-details-content').innerHTML,'closed','late response cannot replace a closed dialog');
  context.fetch=async()=>({ok:false,json:async()=>({detail:'Broker unavailable'})});
  await context.showHistoryDetails(11, 2);
  assert.equal(node('history-details-content').textContent,'Broker unavailable');
  console.log('History UI: direction, lifecycle details, partial fills, escaping and late responses PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
