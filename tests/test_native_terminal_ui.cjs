const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),posts=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:true,value:'0',disabled:false,textContent:'',innerText:'',dataset:{},classList:{add(){},remove(){},toggle(){}}});return nodes.get(id)};
 const ctx=vm.createContext({console,Date,AbortController,crypto:require('node:crypto').webcrypto,
 document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){},MT5Permissions:{can:()=>true}},
 localStorage:{getItem(){return null},setItem(){},removeItem(){}},setTimeout(){},clearTimeout(){},setInterval(){},
 fetch:async(...args)=>{posts.push(args);return {ok:true,json:async()=>({success:true})}}});
 for(const file of ['app','trading'])vm.runInContext(fs.readFileSync('app/static/'+file+'.js','utf8'),ctx);
 ctx.showToast=()=>{};await ctx.openNativeTerminal();
 assert.equal(node('native-terminal-frame').hidden,false);
 assert.match(node('native-terminal-frame').src,/^\/native-mt5\/vnc\/index.html\?/);
 assert.doesNotMatch(node('native-terminal-frame').src,/password|token|authorization/i);
 assert.match(ctx.quoteOrderIssue(),/Orijinal terminal/);
 await ctx.submitPendingOrder();assert.equal(posts.length,1,'custom order form cannot trade the wrong symbol while the native screen is active');
 assert.equal(posts[0][0],'/api/terminal/activate','opening the desktop is an explicit configuration action');
 ctx.closeNativeTerminal();assert.equal(node('native-terminal-frame').hidden,true);assert.equal(node('native-terminal-frame').src,'about:blank');
 ctx.window.MT5Permissions.can=()=>false;await ctx.openNativeTerminal();assert.equal(node('native-terminal-frame').hidden,true);
 console.log('Original MT5 UI: lazy embedded desktop, no credentials in URLs, native/custom order isolation and admin gate PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
