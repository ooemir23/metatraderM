const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
const node=id=>{
  if(!nodes.has(id)) nodes.set(id,{innerHTML:'',textContent:'',innerText:'',open:false,
    showModal(){this.open=true;},close(){this.open=false;}});
  return nodes.get(id);
};
const ctx=vm.createContext({console,Map,document:{addEventListener(){},getElementById:node,
  createElement:()=>({set innerText(value){this.innerHTML=String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}})},
  window:{addEventListener(){}}});
vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),ctx);
const position={ticket:22,symbol:'XAUUSD',type:'SELL',volume:5,price_open:4178.37,price_current:4171.45,
  digits:2,profit:3460,commission:-15,quote:{bid:4171.2,ask:4171.45,point:.01,quote_status:{state:'fresh',message:'Fiyat güncel.'}},
  execution_quality:{state:'recorded',opening_spread_points:20,current_spread_points:25,spread_change_points:5,
    requested_price:4178.4,fill_price:4178.37,opening_bid:4178.4,opening_ask:4178.6,slippage_points:3,
    fills:[{order:123,volume:5,bid:4178.4,ask:4178.6,requested_price:4178.4,fill_price:4178.37,spread_points:20,slippage_points:3}]}};
ctx.renderPositionsData([position]);
let html=node('positions-table-body').innerHTML;
assert.match(html,/Açılış: 20 puan/);
assert.match(html,/Güncel: 25 puan/);
assert.match(html,/Kayma: \+3 puan · Aleyhe/);
ctx.showPositionExecution(22);
html=node('position-execution-content').innerHTML;
assert.match(html,/4178\.40/);
assert.match(html,/4178\.37/);
assert.match(html,/Genişledi/);
assert.match(html,/komisyon gibi ayrıca düşülmez/);
ctx.renderPositionsData([{...position,execution_quality:{...position.execution_quality,slippage_points:-2},
  quote:{...position.quote,quote_status:{state:'stale',message:'<script>old</script>'}}}]);
assert.match(node('position-execution-content').innerHTML,/-2 puan · Lehe/);
assert.match(node('position-execution-content').innerHTML,/Son kotasyon spreadi/);
assert.match(node('position-execution-content').innerHTML,/&lt;script&gt;old/);
assert.match(node('positions-table-body').innerHTML,/Son: 25 puan/);
ctx.renderPositionsData([{...position,execution_quality:{state:'unrecorded',current_spread_points:25}}]);
assert.match(node('position-execution-content').innerHTML,/kaydedilmemiş/);
assert.match(node('positions-table-body').innerHTML,/Kayma: Ölçülemiyor/);
assert.doesNotMatch(node('positions-table-body').innerHTML,/Kayma yok/);
ctx.renderPositionsData([]);
assert.match(node('position-execution-content').textContent,/artık açık listede bulunmuyor/);
assert.match(node('positions-table-body').innerHTML,/colspan="11"/);
console.log('Execution quality UI: live spreads, slippage direction, unknown data and updates PASS');
