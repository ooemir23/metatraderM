const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const storage=new Map(),code=fs.readFileSync('app/static/research.js','utf8');
 const defaults={start:'',end:'',lot:'0.01',capital:'10000',hma:'14',kama:'10',atr:'14',er:'0.30',stop:'2',target:'3',commission:'0',slippage:'0','swap-long':'0','swap-short':'0'};
 function page(){
  const nodes=new Map(),events={},sent=[],downloads=[];
  const node=id=>{if(!nodes.has(id)){const value=defaults[id.replace('research-','')]??'';nodes.set(id,{value,defaultValue:value,disabled:false,events:{},textContent:'',addEventListener(k,v){this.events[k]=v;},replaceChildren(){this.cleared=true;}});}return nodes.get(id);};
  const ctx=vm.createContext({console,Date,Number,JSON,localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
   document:{getElementById:node,addEventListener:(k,v)=>events[k]=v,createElement:()=>({click(){downloads.push(this.download);}})},
   setTimeout:callback=>callback(),URL:{createObjectURL:()=>'/fake.xlsx',revokeObjectURL(){}},fetch:async(url,opts)=>{sent.push({url,body:JSON.parse(opts.body)});return {ok:true,blob:async()=>({})};}});
  vm.runInContext(code,ctx);events.DOMContentLoaded();return {ctx,node,sent,downloads};
 }
 const first=page();first.node('research-lot').value='0.07';first.node('research-lot').events.input();
 first.node('research-start').value='2024-01-02';first.node('research-target').value='0';first.node('research-target').events.change();
 const reopened=page();assert.equal(reopened.node('research-lot').value,'0.07');assert.equal(reopened.node('research-start').value,'2024-01-02');assert.equal(reopened.node('research-target').value,'0');
 reopened.node('research-run').disabled=true;reopened.ctx.resetResearchSettings();assert.equal(reopened.node('research-lot').value,'0.07');
 reopened.node('research-run').disabled=false;reopened.ctx.resetResearchSettings();assert.equal(reopened.node('research-lot').value,'0.01');assert.equal(reopened.node('research-target').value,'3');assert.equal(storage.size,0);assert.equal(reopened.node('research-download').disabled,true);
 const afterReset=page();assert.equal(afterReset.node('research-lot').value,'0.01');
 afterReset.ctx.fakeReport={settings:{lot_size:.03},baseline_holdout:{}};vm.runInContext('latestResearchReport = fakeReport;',afterReset.ctx);
 await afterReset.ctx.downloadResearchReport();assert.equal(afterReset.sent[0].url,'/api/research/export');assert.equal(afterReset.sent[0].body.settings.lot_size,.03);assert.match(afterReset.downloads[0],/\.xlsx$/);
 storage.set('metatraderm.research.settings.v1','bad json');const corrupt=page();assert.equal(corrupt.node('research-hma').value,'14');assert.match(corrupt.node('research-status').textContent,/okunamadı/);
 console.log('Research settings: autosave/reopen, zero values, reset, corrupt storage and XLSX download PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
