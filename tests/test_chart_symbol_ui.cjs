const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
(async()=>{
 const nodes=new Map(), listeners={};
 const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',innerText:'',value:'0.01',className:''});return nodes.get(id);};
 const frameWindow={postMessage(){}};
 node('chart-frame').src='https://s.tradingview.com/widgetembed/';node('chart-frame').contentWindow=frameWindow;
 const context=vm.createContext({console,URL,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
 document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[],createElement:()=>({})},
 window:{addEventListener:(name,fn)=>{listeners[name]=fn;}},fetch:async()=>({ok:true,json:async()=>({symbol:'BTCUSD',bid:100,ask:101,spread:1})})});
 vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
 vm.runInContext('startLiveFeed=()=>{}; renderOrderNotice=()=>{}; setOrderNotice=()=>{}; tvWidget={id:"chart-frame"};',context);
 assert.equal(context.brokerSymbolForChart('BTCUSDT','BINANCE','crypto'),'BTCUSD');
 assert.equal(context.brokerSymbolForChart('BTCUSDT','Binance','bitcoin'),'BTCUSD');
 assert.equal(context.brokerSymbolForChart(' binance:btcusdt ','Binance','Bitcoin'),'BTCUSD');
 assert.equal(context.brokerSymbolForChart('EURUSD','OANDA','forex'),'EURUSD');
 assert.equal(context.brokerSymbolForChart('BITCOIN','CRYPTOCAP','index'),null);
 assert.equal(context.brokerSymbolForChart('BTCUSD','INDEX','index'),null);
 assert.equal(context.brokerSymbolForChart('AAPL','NASDAQ','stock'),null);
 const send=(name,exchange,source=frameWindow,origin='https://s.tradingview.com')=>listeners.message({source,origin,data:JSON.stringify({provider:'TradingView',type:'post',name:'quoteUpdate',data:{original_name:exchange+':'+name}})});
 send('BTCUSDT','BINANCE',{});assert.equal(vm.runInContext('currentSymbol',context),'EURUSD');
 send('BTCUSDT','BINANCE',frameWindow,'https://evil.example');assert.equal(vm.runInContext('currentSymbol',context),'EURUSD');
 send('BTCUSDT','BINANCE');assert.equal(vm.runInContext('currentSymbol',context),'BTCUSD');assert.equal(node('order-symbol-tag').innerText,'BTCUSD');
 let restarts=0;
 context.startLiveFeed=()=>{restarts++;};
 context.renderPriceData({symbol:'BTCUSD',bid:82391.5,ask:82405.5,spread:1400});
 for(let cycle=0;cycle<20;cycle++) {
   send('BTCUSDT','BINANCE');
   listeners.message({source:frameWindow,origin:'https://s.tradingview.com',data:JSON.stringify({provider:'TradingView',type:'on',name:'symbolInfo',id:0,
     data:{name:'BTCUSDT',exchange:'Binance',type:'bitcoin'}})});
   assert.equal(vm.runInContext('chartSelectionBlocked',context),false,'quote and symbolInfo must agree');
   assert.equal(node('order-symbol-tag').innerText,'BTCUSD');
   assert.equal(node('header-bid').innerText,82391.5,'broker quote must remain visible');
 }
 assert.equal(restarts,0,'repeated same-symbol updates must not restart live prices');
 context.renderPriceData({symbol:'EURUSD',bid:1.2,ask:1.3});assert.equal(node('header-bid').textContent,'—');
 send('TOTAL','CRYPTOCAP');assert.equal(vm.runInContext('chartSelectionBlocked',context),true);
 assert.match(node('order-symbol-tag').textContent,/İşlem kapalı/);
 frameWindow.postMessage=raw=>{const req=JSON.parse(raw);listeners.message({source:frameWindow,origin:'https://s.tradingview.com',data:JSON.stringify({...req,type:'on',data:{name:'BITCOIN',exchange:'CRYPTOCAP',type:'index'}})});};
 assert.equal(await context.verifyChartOrderSymbol(),false);
 frameWindow.postMessage=raw=>{const req=JSON.parse(raw);listeners.message({source:frameWindow,origin:'https://s.tradingview.com',data:JSON.stringify({...req,type:'on',data:{name:'BTCUSDT',exchange:'BINANCE',type:'crypto'}})});};
 send('BTCUSDT','BINANCE');
 assert.equal(await context.verifyChartOrderSymbol(),true);assert.equal(vm.runInContext('chartSelectionBlocked',context),false);
 let chartReload;
 context.initTradingView=symbol=>{chartReload=symbol;};
 send('BITCOIN','CRYPTOCAP');
 assert.equal(chartReload,'BTCUSD','market-cap selection reloads the price chart as well as the ticket');
 assert.equal(vm.runInContext('currentSymbol',context),'BTCUSD');
 assert.equal(node('order-buy-btn').disabled,false);
 assert.equal(context.brokerSymbolForChart('BINANCE:BTCUSDT','BINANCE','crypto'),'BTCUSD');
 frameWindow.postMessage=()=>{throw new Error('Third-party unavailable');};
 assert.equal(await context.verifyChartOrderSymbol(),true,'manual MT5 selection does not depend on TradingView uptime');
 console.log('Chart symbol UI: mapping, iframe identity, unsupported products, stale prices and order confirmation PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
