const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(),store=new Map(),posts=[],notices=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',disabled:false,open:false,textContent:'',innerText:'',innerHTML:'',dataset:{},
   classList:{add(){},remove(){},toggle(){}},showModal(){this.open=true},close(){this.open=false}});return nodes.get(id)};
 let caps={close_by:true,opposites:[{ticket:2,volume:.01}],volume_min:.01,volume_step:.01,fillings:[0,1]},trail=[];
 let depth={available:true,symbol:'EURUSD',account:[1,'broker'],digits:5,sampled_at:Date.now()/1000,
   levels:[{side:'BUY',price:1.099,volume:2,market:false}]};
 const ctx=vm.createContext({console,Date,AbortController,crypto:require('node:crypto').webcrypto,
   document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){}},
   localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
   setTimeout(){},clearTimeout(){},setInterval(){},fetch:async(url,options)=>{
     if(url.endsWith('/capabilities'))return {ok:true,json:async()=>caps};
     if(url==='/api/position/trailing'&&!options?.body)return {ok:true,json:async()=>trail};
     if(url.startsWith('/api/market/depth'))return {ok:true,json:async()=>depth};
     posts.push({url,payload:JSON.parse(options.body)});return {ok:true,json:async()=>({success:true,request_id:'resolved-request-123'})};
   }});
 for(const name of ['app','trading'])vm.runInContext(fs.readFileSync('app/static/'+name+'.js','utf8'),ctx);
 ctx.showToast=(text,tone)=>notices.push({text,tone});
 for(const fn of ['fetchAccount','fetchPositions','fetchPendingOrders'])ctx[fn]=()=>{};
 ctx.renderAccountData({connected:true,account_mismatch:false,account_type:'DEMO',login:1,server:'broker',currency:'USD',balance:100,equity:100,margin_free:100,profit:0});
 node('order-deviation').value='7';
 ctx.rememberPositions([{ticket:1,type:'BUY',symbol:'EURUSD',volume:.03,sl:0,tp:0}]);
 ctx.showPositionEditor(1);await ctx.loadPositionManagement(vm.runInContext('editorPosition',ctx));
 assert.equal(node('close-by-button').disabled,false);
 node('close-by-ticket').value='2';await ctx.closeByPosition();
 assert.equal(posts[0].url,'/api/position/close-by');assert.equal(posts[0].payload.position_by,2);
 assert.deepEqual(posts[0].payload.expected_account,[1,'broker','DEMO']);
 ctx.showPositionEditor(1);node('close-filling').value='IOC';await ctx.closeEditorPosition();
 assert.equal(posts.at(-1).payload.deviation_points,7);assert.equal(posts.at(-1).payload.filling_policy,'IOC');
 ctx.showPositionEditor(1);node('trailing-distance').value='200';await ctx.saveTrailingStop();
 assert.equal(posts.at(-1).url,'/api/position/trailing');assert.equal(posts.at(-1).payload.distance_points,200);
 const count=posts.length;vm.runInContext('accountSessionGeneration++',ctx);await ctx.saveTrailingStop();await ctx.closeByPosition();
 assert.equal(posts.length,count,'old editor cannot manage a changed account');
 await ctx.refreshMarketDepth();ctx.stageDepthOrder(0);
 assert.equal(node('pending-type').value,'BUY_LIMIT');assert.equal(node('pending-price').value,'1.099');
 assert.equal(posts.length,count,'selecting depth price never sends a trade');
 vm.runInContext('currentSymbol="BTCUSD"',ctx);node('pending-price').value='';ctx.stageDepthOrder(0);
 assert.equal(node('pending-price').value,'','old book cannot populate a new symbol');
 vm.runInContext('currentSymbol="EURUSD"',ctx);
 depth={available:false,message:'No broker book'};await ctx.refreshMarketDepth();
 assert.equal(node('market-depth-levels').innerHTML,'');assert.equal(node('market-depth-status').textContent,'No broker book');
 ctx.renderAccountData({connected:true,account_mismatch:false,account_type:'DEMO',login:1,server:'broker',margin_mode:0,currency:'USD',balance:100,equity:100,margin_free:100,profit:0});
 node('order-rule-mode').value='MT5';node('order-stop-mode').value='price';node('sl-input').value='0';node('tp-input').value='0';
 ctx.seedNettingProtection([{ticket:1,symbol:'EURUSD',sl:1.08,tp:1.12}]);
 assert.equal(node('sl-input').value,'1.08');assert.equal(node('tp-input').value,'1.12');
 vm.runInContext('ticketProtectionEdited=true',ctx);node('sl-input').value='0';
 ctx.seedNettingProtection([{ticket:2,symbol:'EURUSD',sl:1.09,tp:1.13}]);
 assert.equal(node('sl-input').value,'0','explicitly clearing a protection cannot be overwritten by later broker updates');
 console.log('MT5 position UI: Close By, execution settings, trailing, account guards and broker book staging PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
