// Independent MT5 chart. TradingView embed remains available as a separate mode.
(() => {
  const defaults = {timeframe:15, type:'candles', theme:'dark', grid:true, volume:true,
    log:false, sma:false, period:20, up:'#10b981', down:'#ef4444'};
  const frames = {1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'};
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } };
  function settings() {
    const raw = read('mt5.chart.settings.v1', {}), result = {...defaults};
    if (Object.hasOwn(frames, raw.timeframe)) result.timeframe = Number(raw.timeframe);
    if (['candles','bars','line','area'].includes(raw.type)) result.type = raw.type;
    if (['dark','light'].includes(raw.theme)) result.theme = raw.theme;
    for (const key of ['grid','volume','log','sma']) if (typeof raw[key] === 'boolean') result[key] = raw[key];
    if (Number.isInteger(raw.period) && raw.period >= 2 && raw.period <= 200) result.period = raw.period;
    for (const key of ['up','down']) if (/^#[0-9a-f]{6}$/i.test(raw[key])) result[key] = raw[key];
    return result;
  }
  let prefs = settings(), active = null, generation = 0;
  const mode = () => read('mt5.chart.mode.v1', 'mt5') === 'tradingview' ? 'tradingview' : 'mt5';
  function dispose() {
    generation++;
    if (!active) return;
    clearTimeout(active.timer);
    active.abort?.abort();
    active.cleanup.forEach(fn => fn());
    active.chart?.remove();
    active = null;
  }
  function status(state, message) {
    if (!state || state !== active) return;
    state.status.textContent = message;
  }
  function normalize(rows) {
    const map = new Map();
    for (const r of rows || []) {
      const row = {time:Number(r.time),open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close),tick_volume:Number(r.tick_volume)||0};
      if (!Number.isInteger(row.time) || row.time <= 0 || !['open','high','low','close'].every(k=>Number.isFinite(row[k]) && row[k]>0)) continue;
      if (row.high < Math.max(row.open,row.close,row.low) || row.low > Math.min(row.open,row.close)) continue;
      map.set(row.time, row);
    }
    return [...map.values()].sort((a,b)=>a.time-b.time);
  }
  function seriesOptions() {
    return {upColor:prefs.up,downColor:prefs.down,borderVisible:false,
      wickUpColor:prefs.up,wickDownColor:prefs.down,color:prefs.up,lineColor:prefs.up,
      topColor:prefs.up+'50',bottomColor:prefs.up+'05',priceFormat:{type:'price',precision:5,minMove:.00001}};
  }
  function applyStyle(state) {
    const light = prefs.theme === 'light';
    state.chart.applyOptions({layout:{background:{type:'solid',color:light?'#f8fafc':'#0b0e14'},textColor:light?'#334155':'#94a3b8',attributionLogo:true},
      grid:{vertLines:{visible:prefs.grid,color:light?'#e2e8f0':'#1e293b'},horzLines:{visible:prefs.grid,color:light?'#e2e8f0':'#1e293b'}},
      rightPriceScale:{mode:prefs.log?1:0,autoScale:true,scaleMargins:{top:.1,bottom:prefs.volume?.25:.1}},
      timeScale:{timeVisible:true,secondsVisible:false},localization:{locale:'tr-TR'}});
    state.price.applyOptions(seriesOptions());
    state.volume.applyOptions({visible:prefs.volume});
    state.average.applyOptions({visible:prefs.sma});
  }
  function renderData(state, fit = false) {
    if (!state.rows.length) return;
    const rows = state.rows;
    state.price.setData(['line','area'].includes(prefs.type) ? rows.map(r=>({time:r.time,value:r.close})) : rows);
    // Match precision to the actual broker prices, with a minimum of two decimals.
    const precision = Math.min(8, Math.max(2, ...rows.slice(-50).flatMap(r=>['open','high','low','close'].map(k=>(String(r[k]).split('.')[1]||'').length))));
    state.price.applyOptions({priceFormat:{type:'price',precision,minMove:10**-precision}});
    state.volume.setData(rows.map(r=>({time:r.time,value:r.tick_volume,color:(r.close>=r.open?prefs.up:prefs.down)+'70'})));
    let sum = 0; const avg=[];
    rows.forEach((r,i)=>{sum+=r.close;if(i>=prefs.period) sum-=rows[i-prefs.period].close;if(i>=prefs.period-1) avg.push({time:r.time,value:sum/prefs.period});});
    state.average.setData(avg);
    if (fit) { state.chart.timeScale().fitContent(); state.chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-120),to:rows.length+4}); }
  }
  function rebuildPrice(state) {
    if (state.price) state.chart.removeSeries(state.price);
    const types = {candles:LightweightCharts.CandlestickSeries,bars:LightweightCharts.BarSeries,line:LightweightCharts.LineSeries,area:LightweightCharts.AreaSeries};
    state.price = state.chart.addSeries(types[prefs.type], seriesOptions());
    state.lines = [];
    for (const price of state.savedLines) state.lines.push(state.price.createPriceLine({price,color:'#38bdf8',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Seviye'}));
  }
  async function load(state, full = false) {
    if (state !== active) return;
    if (document.hidden && !full) { state.timer=setTimeout(()=>load(state),5000); return; }
    const token = generation;
    const controller = new AbortController(); state.abort=controller;
    const timeout = setTimeout(()=>controller.abort(),12000);
    try {
      const count = full || !state.rows.length ? 600 : 3;
      const res = await fetch(`/api/chart/candles?symbol=${encodeURIComponent(state.symbol)}&timeframe_minutes=${state.timeframe}&count=${count}`, {signal:controller.signal});
      const data = await res.json();
      if (token !== generation || state !== active) return;
      if (!res.ok || data.symbol !== state.symbol || data.timeframe_minutes !== state.timeframe) throw new Error(data.detail || 'Grafik verisi alınamadı.');
      const rows = normalize(data.rates);
      if (!rows.length) throw new Error('Broker geçerli mum verisi göndermedi.');
      // Refill history after a long disconnect instead of drawing across missing bars.
      const gap = state.rows.length && rows[0].time > state.rows.at(-1).time + state.timeframe*60;
      if (gap && !full) { clearTimeout(timeout); return await load(state,true); }
      state.rows = normalize(full ? rows : [...state.rows,...rows]).slice(-1000);
      renderData(state, !state.loaded);
      state.loaded=true;
      status(state,`${state.symbol} · ${frames[state.timeframe]} · MT5 · Broker saati · Güncellendi ${new Date().toLocaleTimeString('tr-TR')}`);
    } catch (error) {
      if (state === active && token === generation) status(state,`Veri güncellenemedi · ${error.name === 'AbortError' ? 'Bağlantı zaman aşımı' : error.message} · Yeniden deneniyor`);
    } finally {
      clearTimeout(timeout);
      if (state === active && token === generation) { clearTimeout(state.timer); state.timer=setTimeout(()=>load(state),5000); }
    }
  }
  function saveSettings(state) {
    status(state, write('mt5.chart.settings.v1', prefs) ? 'Ayarlar bu tarayıcıda kaydedildi · Broker saati' : 'Ayar uygulandı; tarayıcı kaydetmeye izin vermedi.');
  }
  function change(key, value) {
    const state = active; if (!state) return;
    prefs[key] = value; saveSettings(state);
    if (key === 'timeframe') { mount(state.symbol); return; }
    const range=state.chart.timeScale().getVisibleLogicalRange();
    if (key === 'type') rebuildPrice(state);
    applyStyle(state); renderData(state);
    if (range) state.chart.timeScale().setVisibleLogicalRange(range);
  }
  function showMenu(state, event) {
    event?.preventDefault();
    const menu = state.menu;
    menu.hidden=false;
    for (const node of menu.querySelectorAll('[data-setting]')) {
      const value=prefs[node.dataset.setting];
      if (node.type === 'checkbox') node.checked=value; else node.value=String(value);
    }
    const bounds=state.host.getBoundingClientRect();
    menu.style.left=`${Math.max(4,Math.min((event?.clientX ?? bounds.left+12)-bounds.left,bounds.width-menu.offsetWidth-4))}px`;
    menu.style.top=`${Math.max(4,Math.min((event?.clientY ?? bounds.top+12)-bounds.top,bounds.height-menu.offsetHeight-4))}px`;
    menu.querySelector('select').focus();
  }
  function mount(symbol) {
    dispose();
    const host=document.getElementById('tradingview-container'); if (!host) return;
    host.innerHTML=`<div class="mt5-chart-shell">
      <div class="mt5-chart-toolbar"><span class="mt5-chart-status" role="status">MT5 grafiği yükleniyor…</span><button type="button" class="mt5-settings-button">Grafik ayarları · Sağ tuş</button></div>
      <div class="mt5-chart-canvas" tabindex="0" aria-label="MT5 fiyat grafiği"></div>
      <div class="mt5-chart-menu" hidden role="dialog" aria-label="Grafik ayarları">
        <div class="mt5-menu-title">Grafik ayarları <button type="button" data-action="close" aria-label="Grafik ayarlarını kapat">✕</button></div>
        <label>Zaman dilimi<select data-setting="timeframe" aria-label="Grafik zaman dilimi">${Object.entries(frames).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
        <label>Grafik türü<select data-setting="type" aria-label="Grafik türü"><option value="candles">Mum</option><option value="bars">Çubuk</option><option value="line">Çizgi</option><option value="area">Alan</option></select></label>
        <label>Tema<select data-setting="theme" aria-label="Grafik teması"><option value="dark">Koyu</option><option value="light">Açık</option></select></label>
        <label>Yükseliş rengi<input type="color" data-setting="up" aria-label="Yükseliş rengi"></label>
        <label>Düşüş rengi<input type="color" data-setting="down" aria-label="Düşüş rengi"></label>
        <label>Izgara<input type="checkbox" data-setting="grid" aria-label="Grafik ızgarası"></label>
        <label>Tick hacmi<input type="checkbox" data-setting="volume" aria-label="Tick hacmi"></label>
        <label>Logaritmik ölçek<input type="checkbox" data-setting="log" aria-label="Logaritmik ölçek"></label>
        <label>Hareketli ortalama (SMA)<input type="checkbox" data-setting="sma" aria-label="Hareketli ortalama"></label>
        <label>SMA periyodu<input type="number" min="2" max="200" data-setting="period" aria-label="SMA periyodu"></label>
        <button type="button" data-action="level">Bu fiyata yatay çizgi ekle</button>
        <button type="button" data-action="clear-levels">Yatay çizgileri temizle</button>
        <button type="button" data-action="fit">Grafiği ekrana sığdır</button>
        <button type="button" data-action="reset">Grafik ayarlarını sıfırla</button>
        <p>Ayarlar bu tarayıcıda saklanır. Grafik ayarları emir veya bot ayarlarını değiştirmez.</p>
      </div>
      <div class="mt5-chart-attribution"><a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView Lightweight Charts™</a> · Copyright © 2025 TradingView, Inc. · Veri: MT5</div>
    </div>`;
    const engine=document.getElementById('chart-engine'); if(engine) engine.value='mt5';
    const link=document.getElementById('chart-tradingview-link'); if(link) link.hidden=true;
    if (!window.LightweightCharts) { host.querySelector('.mt5-chart-status').textContent='Grafik kütüphanesi yüklenemedi. Sayfayı yenileyin.';return; }
    const saved = read(`mt5.chart.levels.${symbol}`,[]);
    const state = active = {symbol,timeframe:prefs.timeframe,host,rows:[],loaded:false,cleanup:[],
      savedLines:Array.isArray(saved)?saved.filter(v=>typeof v==='number'&&Number.isFinite(v)&&v>0).slice(0,50):[],
      menu:host.querySelector('.mt5-chart-menu'),status:host.querySelector('.mt5-chart-status')};
    const canvas=host.querySelector('.mt5-chart-canvas');
    state.chart=LightweightCharts.createChart(canvas,{autoSize:true});
    rebuildPrice(state);
    state.volume=state.chart.addSeries(LightweightCharts.HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
    state.volume.priceScale().applyOptions({scaleMargins:{top:.8,bottom:0}});
    state.average=state.chart.addSeries(LightweightCharts.LineSeries,{color:'#fbbf24',lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:'SMA'});
    applyStyle(state);
    document.getElementById('current-symbol-title').textContent=`${symbol} · ${frames[prefs.timeframe]} · MT5`;
    const on=(target,name,fn)=>{target.addEventListener(name,fn);state.cleanup.push(()=>target.removeEventListener(name,fn));};
    on(canvas,'contextmenu',event=>{
      const bounds=canvas.getBoundingClientRect();state.selectedPrice=state.price.coordinateToPrice(event.clientY-bounds.top);
      showMenu(state,event);
      state.menu.querySelector('[data-action="level"]').disabled=!(state.selectedPrice>0);
    });
    on(canvas,'keydown',event=>{if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){state.selectedPrice=null;showMenu(state,event);state.menu.querySelector('[data-action="level"]').disabled=true;}});
    on(host.querySelector('.mt5-settings-button'),'click',()=>{state.selectedPrice=null;showMenu(state);state.menu.querySelector('[data-action="level"]').disabled=true;});
    on(state.menu,'change',event=>{
      const key=event.target.dataset.setting;if(!key) return;
      const value=event.target.type==='checkbox'?event.target.checked:['timeframe','period'].includes(key)?Number(event.target.value):event.target.value;
      if (key==='period' && (!Number.isInteger(value)||value<2||value>200)) {event.target.value=prefs.period;return;}
      change(key,value);
    });
    on(state.menu,'click',event=>{
      const action=event.target.closest('[data-action]')?.dataset.action;if(!action)return;
      if(action==='fit') {state.chart.priceScale('right').applyOptions({autoScale:true});state.chart.timeScale().fitContent();}
      if(action==='reset') {prefs={...defaults};saveSettings(state);mount(symbol);return;}
      if(action==='level' && state.selectedPrice>0 && state.savedLines.length<50) {
        const price=state.selectedPrice;state.savedLines.push(price);state.lines.push(state.price.createPriceLine({price,color:'#38bdf8',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Seviye'}));write(`mt5.chart.levels.${symbol}`,state.savedLines);
      }
      if(action==='clear-levels') {state.lines.forEach(line=>state.price.removePriceLine(line));state.lines=[];state.savedLines=[];write(`mt5.chart.levels.${symbol}`,[]);}
      state.menu.hidden=true;canvas.focus();
    });
    on(document,'pointerdown',event=>{if(!state.menu.contains(event.target)&&!event.target.closest('.mt5-settings-button'))state.menu.hidden=true;});
    on(document,'keydown',event=>{if(event.key==='Escape'){state.menu.hidden=true;}});
    load(state,true);
  }
  function setMode(value) {
    write('mt5.chart.mode.v1',value==='tradingview'?'tradingview':'mt5');
    // Manual broker selection remains the source of order identity.
    switchSymbol(currentSymbol);
    if(value==='tradingview') {
      const engine=document.getElementById('chart-engine');if(engine)engine.value='tradingview';
      const link=document.getElementById('chart-tradingview-link');if(link)link.hidden=false;
    }
  }
  window.MT5Chart={mode,setMode,mount,dispose,normalize};
})();
