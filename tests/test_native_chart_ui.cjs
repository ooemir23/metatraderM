const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
  const pending=[], charts=[], storage=new Map(), downloads=[], blobs=[], copied=[];
  function element() { return {textContent:'',value:'',hidden:true,dataset:{},style:{},offsetWidth:290,offsetHeight:400,events:{},addEventListener(name,fn){this.events[name]=fn;},removeEventListener(name){delete this.events[name];},querySelector:()=>element(),querySelectorAll:()=>[],contains:()=>false,focus(){},getBoundingClientRect:()=>({left:0,top:0,width:800,height:500})}; }
  const host=element(), nodes=new Map();
  host.querySelector=selector=>{if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);};
  const globals={console,URL:{createObjectURL(blob){blobs.push(blob);return 'blob:test';},revokeObjectURL(){}},Blob,AbortController,Number,Map,
    navigator:{clipboard:{writeText:async value=>copied.push(value)}},
    setTimeout:()=>1,clearTimeout(){},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    document:{hidden:false,getElementById:id=>id==='tradingview-container'?host:element(),addEventListener(){},removeEventListener(){},body:{appendChild(){}},createElement:()=>({click(){downloads.push(this.download);},remove(){}})},
    fetch:url=>new Promise(resolve=>pending.push({url,resolve})), window:{},
    LightweightCharts:{CandlestickSeries:'candles',BarSeries:'bars',LineSeries:'line',AreaSeries:'area',HistogramSeries:'volume',
      createChart(){ const chart={series:[],removed:false,applyOptions(){},remove(){this.removed=true;},removeSeries(){},
        timeScale:()=>({fitContent(){},setVisibleLogicalRange(){}}),takeScreenshot:()=>({toBlob:fn=>fn(new Blob(['PNG'],{type:'image/png'}))}),
        addSeries(type){const s={type,data:[],options:{},applyOptions(options){Object.assign(this.options,options);},setData(rows){assert(!chart.removed,'late response cannot update disposed chart');this.data=rows;},
          priceScale:()=>({applyOptions(){}}),createPriceLine(){return {};},coordinateToPrice:()=>1};chart.series.push(s);return s;}};charts.push(chart);return chart;}}
  };
  globals.window.LightweightCharts=globals.LightweightCharts;
  vm.runInNewContext(fs.readFileSync('app/static/mt5-chart.js','utf8'),globals);
  const api=globals.window.MT5Chart;
  const good=(time,close)=>({time,open:close,high:close+1,low:close-1,close,tick_volume:10});
  const ema=api.emaValues([1,2,3,4,5].map(v=>good(v,v)),3);
  assert.deepEqual(Array.from(ema,r=>[r.time,r.value]),[[3,2],[4,3],[5,4]],'EMA uses SMA seed and standard smoothing');
  assert.equal(api.emaValues([good(1,1)],3).length,0,'insufficient bars never fabricate an indicator');
  const csv=api.csvData({symbol:'BTCUSD',timeframe:15,rows:[good(10,82000)]});
  assert.match(csv,/time_broker/);assert.match(csv,/BTCUSD,15,10,82000,82001,81999,82000,10/);
  const normalized=api.normalize([good(2,20),good(1,10),good(2,21),good(3,NaN),{...good(4,20),high:5}]);
  assert.deepEqual(Array.from(normalized,r=>[r.time,r.close]),[[1,10],[2,21]],'sorted, deduplicated, valid broker candles only');
  api.mount('EURUSD');const oldStatus=nodes.get('.mt5-chart-status');
  nodes.clear();api.mount('BTCUSD');const currentStatus=nodes.get('.mt5-chart-status');
  pending[0].resolve({ok:true,json:async()=>({symbol:'EURUSD',timeframe_minutes:15,rates:[good(1,10)]})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(charts[0].removed,true);assert.equal(charts[0].series[0].data.length,0);
  assert.equal(currentStatus.textContent,'','stale EURUSD reply cannot overwrite BTCUSD status');
  pending[1].resolve({ok:true,json:async()=>({symbol:'BTCUSD',timeframe_minutes:15,rates:[good(2,82000)]})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(charts[1].series[0].data[0].close,82000);
  assert.match(currentStatus.textContent,/BTCUSD/);
  const menu=nodes.get('.mt5-chart-menu');
  const action=value=>menu.events.click({target:{closest:()=>({dataset:{action:value}})}});
  action('export-csv');action('export-png');
  assert.deepEqual(downloads,['BTCUSD-M15-MT5.csv','BTCUSD-M15-MT5.png']);
  assert.match(await blobs[0].text(),/BTCUSD,15,2,82000/);assert.equal(blobs[1].type,'image/png');
  nodes.get('.mt5-chart-canvas').events.contextmenu({clientX:50,clientY:50,preventDefault(){}});
  action('copy-price');await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(copied,['1']);
  assert(pending.every(p=>p.url.startsWith('/api/chart/candles?')),'chart only requests history, never orders');
  globals.window.MT5Markets={getSymbol:symbol=>symbol==='BTCUSDm'?{digits:2}:symbol==='EURUSD.a'?{digits:5}:null};
  api.mount('BTCUSDm');
  assert.match(pending[2].url,/symbol=BTCUSDm/,'broker suffix keeps its exact case in the chart request');
  pending[2].resolve({ok:true,json:async()=>({symbol:'BTCUSDm',timeframe_minutes:15,rates:[good(3,82000.12999999999)]})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(charts[2].series[0].options.priceFormat.precision,2,'crypto chart uses broker precision instead of float string length');
  assert.equal(charts[2].series[2].options.priceFormat.precision,2,'indicator precision follows the broker too');
  api.mount('EURUSD.a');
  pending[3].resolve({ok:true,json:async()=>({symbol:'EURUSD.a',timeframe_minutes:15,rates:[good(4,1.120169999999999)]})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(charts[3].series[0].options.priceFormat.precision,5);
  api.dispose();
  storage.set('mt5.chart.mode.v1',JSON.stringify('tradingview'));assert.equal(api.mode(),'tradingview');
  globals.localStorage.setItem=()=>{throw new Error('Storage writes denied');};
  globals.currentSymbol='BTCUSDm';
  const modes=[];
  globals.switchSymbol=()=>{modes.push(api.mode());if(api.mode()==='tradingview')api.setMode('mt5');};
  api.setMode('tradingview');
  assert.deepEqual(modes,['tradingview','mt5'],'fallback terminates even when the saved TradingView preference cannot be changed');
  assert.equal(api.mode(),'mt5','broker chart selection takes effect in memory with blocked storage');
  console.log('Native chart: invalid candle rejection, stale symbol response, cleanup, order isolation and reversible engine PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
