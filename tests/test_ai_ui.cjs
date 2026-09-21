const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
(async () => {
  for (const file of ['app/static/app.js', 'app/static/ai.js']) {
    const nodes = new Map();
    const node = id => {
      if (!nodes.has(id)) nodes.set(id, {innerText:'', innerHTML:'', className:'', value:'', dataset:{}, disabled:false,
        classList:{toggle(){},add(){},remove(){}}});
      return nodes.get(id);
    };
    const rec = {action:'BUY', symbol:'EURUSD', confidence:80, sl_price:1.05, tp_price:1.2,
      sl_points:200, tp_points:400, suggested_lot:.02, expires_at:Date.now()/1000+900, reasoning:'test'};
    let sent;
    const ctx = vm.createContext({console, Date, AbortController,
      document:{addEventListener(){}, getElementById:node, querySelectorAll:()=>[]},
      window:{addEventListener(){}}, setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,
      confirmAction:async()=>true,
      fetch: async (url, opts) => {
        if (url.endsWith('/status')) return {ok:true,json:async()=>({success:true,
          memory:{last_analyzed:'2026-09-19',analyzed_trades_count:5,persona:{style:'test',summary:'summary'}},
          autopilot:{enabled:false,mode:'ADVISORY',allowed_symbols:['EURUSD']},
          usage:{calls:2,total_tokens:130,unknown_usage_calls:0},limits:{daily_calls:100}})};
        if (url.endsWith('/advice')) return {ok:true,json:async()=>({success:true,recommendation:rec,cached:true})};
        if (url.endsWith('/execute')) {sent=JSON.parse(opts.body);return {ok:true,json:async()=>({success:true,ticket:123})};}
        return {ok:true,json:async()=>({})};
      }
    });
    vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
    ctx.showToast=()=>{};
    ctx.fetchPositions=()=>{};
    ctx.fetchAccount=()=>{};
    await ctx.fetchAIStatus();
    assert.match(node('ai-usage').innerText,/2\/100/);
    assert.match(node('ai-learned-trades-count').innerText,/5/);
    await ctx.triggerAIAdvice();
    assert.equal(node('ai-advice-signal').innerText,'AL (BUY)');
    assert.equal(node('ai-advice-sl').innerText,1.05);
    await ctx.executeCurrentAdvice();
    assert.equal(sent.recommendation.action,'BUY');
    assert.equal(sent.recommendation.symbol,'EURUSD');
    assert.equal(sent.recommendation.suggested_lot,.01);
    console.log(file+': status, usage, advice and execution payload PASS');
  }
})().catch(err=>{console.error(err);process.exitCode=1;});
