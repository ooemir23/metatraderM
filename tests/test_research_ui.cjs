const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  class Element {
    constructor(tag='div') { this.tag=tag; this.children=[]; this.value=''; this.disabled=false; this.style={}; this.attrs={}; this.events={}; const classes=new Set(); this.classList={add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)}; }
    append(...items) { this.children.push(...items); } appendChild(item) { this.append(item); }
    replaceChildren(...items) { this.children=items; }
    createTHead() { const e=new Element('thead'); this.append(e); return e; }
    createTBody() { const e=new Element('tbody'); this.append(e); return e; }
    insertRow() { const e=new Element('tr'); this.append(e); return e; }
    insertCell() { const e=new Element('td'); this.append(e); return e; }
    setAttribute(k,v) { this.attrs[k]=v; } addEventListener(k,v) { this.events[k]=v; }
  }
  const nodes=new Map(), listeners={};
  const node=id=>{if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id);};
  const document={getElementById:node,createElement:tag=>new Element(tag),body:new Element(),fullscreenElement:null,addEventListener:(k,v)=>listeners[k]=v};
  const ctx=vm.createContext({console,document,Date,Number,JSON});
  vm.runInContext(fs.readFileSync('app/static/research.js','utf8'),ctx);
  const panel=node('research-panel'), button=node('research-fullscreen');
  const event={preventDefault(){},stopPropagation(){}};
  document.body.style.overflow='auto';
  await ctx.toggleResearchFullscreen(event); // No native API: fallback.
  assert.equal(panel.open,true); assert.equal(button.attrs['aria-pressed'],'true');
  assert.equal(document.body.style.overflow,'hidden');
  listeners.keydown({key:'Escape'});
  assert.equal(panel.classList.contains('research-fullscreen-fallback'),false);
  assert.equal(document.body.style.overflow,'auto');
  panel.requestFullscreen=async()=>{document.fullscreenElement=panel; listeners.fullscreenchange();};
  document.exitFullscreen=async()=>{document.fullscreenElement=null;listeners.fullscreenchange();};
  await ctx.toggleResearchFullscreen(event); assert.equal(button.attrs['aria-pressed'],'true');
  await ctx.toggleResearchFullscreen(event); assert.equal(button.attrs['aria-pressed'],'false');
  const trades=Array.from({length:121},(_,i)=>({side:'BUY',lot_size:.01,entry_time:1700000000+i*7200,exit_time:1700003600+i*7200,entry_price:2000,exit_price:2001,net_profit:1,net_points:100,balance_after:10001+i,reason:'target'}));
  const stats={count:121,wins:121,losses:0,breakeven:0,initial_capital:10000,gross_profit:121,gross_loss:0,net_profit:121,final_capital:10121,return_pct:1.21,max_drawdown_money:2,net_points:12100};
  ctx.renderResearchReport({settings:{currency:'USD',lot_size:.01},data_quality:{H1:{count:1000,long_intervals:0},H4:{count:250,long_intervals:0}},segments:{train:{start:1700000000},holdout:{end_exclusive:1701000000}},selected:null,baseline_holdout:{stats,trades},comparisons:[]});
  const all=[]; const walk=e=>{all.push(e); e.children.forEach(walk);};walk(node('research-result'));
  const select=all.find(e=>e.tag==='select'); select.value='0';
  const next=all.find(e=>e.textContent==='Sonraki'),previous=all.find(e=>e.textContent==='Önceki');
  const info=all.find(e=>e.attrs['aria-live']==='polite');
  assert.equal(info.textContent,'1–50 / 121 işlem'); assert.equal(previous.disabled,true);
  next.events.click(); assert.equal(info.textContent,'51–100 / 121 işlem');
  next.events.click(); assert.equal(info.textContent,'101–121 / 121 işlem'); assert.equal(next.disabled,true);
  previous.events.click(); assert.equal(info.textContent,'51–100 / 121 işlem');
  assert.ok(all.some(e=>e.textContent==='Doğru (kârlı)'));
  const loss = {side:'SELL',lot_size:.01,entry_time:1700000000,exit_time:1700003600,
    entry_price:2000,exit_price:2001,net_points:-120,net_profit:-1.2,balance_after:9998.8,reason:'stop',
    diagnostics:{market_points:-100,cost_points:{spread:10,slippage:5,commission:5,swap:0},entry_atr:2,entry_er:.4,entry_distance_atr:.1,stop_price:2001,target_price:1997,stop_gap:false}};
  ctx.renderResearchReport({settings:{currency:'USD',lot_size:.01,point:.01,contract_size:100},data_quality:{H1:{count:1000,long_intervals:0},H4:{count:250,long_intervals:0}},segments:{train:{start:1700000000},holdout:{end_exclusive:1701000000}},selected:null,baseline_holdout:{stats:{...stats,count:2,losses:1,wins:1},trades:[loss,trades[0]]},comparisons:[]});
  const collect=()=>{const output=[];const visit=e=>{output.push(e);e.children.forEach(visit);};visit(node('research-result'));return output;};
  const lossCard=collect().find(e=>e.attrs['aria-label']?.startsWith('Yanlış (zararlı)'));
  assert.equal(lossCard.tag,'button'); lossCard.events.click();
  let rendered=collect();
  assert.equal(lossCard.attrs['aria-expanded'],'true');
  assert.equal(rendered.find(e=>e.id==='research-outcome-details').hidden,false);
  assert.equal(rendered.filter(e=>e.className==='research-trade-diagnosis').length,1);
  assert.ok(rendered.some(e=>e.textContent?.includes('ters yönünde ilerleyerek')));
  assert.ok(rendered.some(e=>e.textContent?.startsWith('Ne denenebilir?')));
  assert.ok(rendered.some(e=>e.textContent?.includes('Spread:')));
  rendered.find(e=>e.textContent==='Detayları kapat').events.click();
  assert.equal(lossCard.attrs['aria-expanded'],'false');
  const costDriven=ctx.explainResearchTrade({...loss,reason:'target',diagnostics:{...loss.diagnostics,market_points:10}});
  assert.match(costDriven.cause,/maliyet|Maliyet/);
  assert.match(ctx.explainResearchTrade({...loss,reason:'segment end'}).cause,/dönemi sona/);
  assert.match(ctx.explainResearchTrade({...loss,diagnostics:{...loss.diagnostics,stop_gap:true}}).cause,/ötesinde açıldığı/);
  ctx.window = ctx;
  ctx.localStorage = {getItem: () => 'en'};
  ctx.navigator = {language: 'en-US'};
  document.readyState = 'loading';
  vm.runInContext(fs.readFileSync('app/static/i18n.js', 'utf8'), ctx);
  const config = {mode:'H1_H4',filtered:true,wait:2,distance_atr:.1};
  const result = {stats, trades:[loss,trades[0]],signals:{crossovers:10,confirmed:2,filtered_at_deadline:3,expired:4,reversed:1}};
  ctx.renderResearchReport({settings:{currency:'USD',lot_size:.01,point:.01,contract_size:100},data_quality:{H1:{count:1000,long_intervals:0},H4:{count:250,long_intervals:0}},segments:{train:{start:1700000000},holdout:{end_exclusive:1701000000}},selected:{config,train:stats,validation:stats},selected_holdout:result,baseline_holdout:result,comparisons:[{config,train:stats}]});
  collect().find(e=>e.attrs['aria-label']?.startsWith('Yanlış (zararlı)')).events.click();
  const translate = ctx.MT5I18n.translate;
  const english = collect().flatMap(e=>[e.textContent,...Object.values(e.attrs)]).filter(v=>typeof v==='string').map(translate);
  for (const text of english) assert.doesNotMatch(text, /[çğıöşüÇĞİÖŞÜ]/, `Untranslated research text: ${text}`);
  assert.ok(english.some(text=>text.startsWith('Outcome explanation: The price moved against')));
  assert.ok(english.some(text=>text.includes('-1.20 USD')));
  assert.equal(translate('H1 + H4 · filtreli · 2 mum · %10 ATR'),'H1 + H4 · filtered · 2 candles · 10% ATR');
  assert.equal(translate('⛶ Tam ekrandan çık'),'⛶ Exit full screen');
  console.log('Research UI: money summaries, full trade pagination, native/fallback fullscreen and Escape PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
