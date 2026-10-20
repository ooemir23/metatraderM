// Independent MT5 chart. TradingView embed remains available as a separate mode.
(() => {
  const defaults = {timeframe:15, type:'candles', theme:'dark', grid:true, volume:true,
    log:false, scale:0, invert:false, crosshair:true, priceLine:true,
    sma:false, ema:true, hma:true, signals:true, rsi:false, rsiPeriod:14, tradeLevels:true, askLine:false, separators:false, shift:true, autoscroll:true, period:50, emaPeriod:34, hmaPeriod:14, slowType:'EMA', confirmClose:true, up:'#10b981', down:'#ef4444'};
  const SLOW_TYPES = ['EMA','SMA','LWMA','HMA'];
  // MT5 recursive indicators (EMA) depend on the whole chart history; load enough bars that the
  // seed no longer affects the value, like MT5's own long chart history.
  const HISTORY_BARS = 5000;
  const frames = {1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'};
  const frameLabels = {1:'1 dk',5:'5 dk',15:'15 dk',30:'30 dk',60:'1 sa',240:'4 sa',1440:'1 gün'};
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } };
  function settings() {
    const raw = read('mt5.chart.settings.v1', {}), result = {...defaults};
    if (Object.hasOwn(frames, raw.timeframe)) result.timeframe = Number(raw.timeframe);
    if (['candles','bars','line','area'].includes(raw.type)) result.type = raw.type;
    if (['dark','black','light'].includes(raw.theme)) result.theme = raw.theme;
    for (const key of ['grid','volume','log','sma','ema','hma','signals','rsi','tradeLevels','askLine','separators','shift','autoscroll','invert','crosshair','priceLine']) if (typeof raw[key] === 'boolean') result[key] = raw[key];
    result.scale = [0,1,2,3].includes(raw.scale) ? raw.scale : (raw.log ? 1 : 0);
    if (Number.isInteger(raw.period) && raw.period >= 2 && raw.period <= 200) result.period = raw.period;
    if (Number.isInteger(raw.emaPeriod) && raw.emaPeriod >= 2 && raw.emaPeriod <= 200) result.emaPeriod = raw.emaPeriod;
    if (Number.isInteger(raw.rsiPeriod) && raw.rsiPeriod >= 2 && raw.rsiPeriod <= 200) result.rsiPeriod = raw.rsiPeriod;
    if (Number.isInteger(raw.hmaPeriod) && raw.hmaPeriod >= 2 && raw.hmaPeriod <= 200) result.hmaPeriod = raw.hmaPeriod;
    if (SLOW_TYPES.includes(raw.slowType)) result.slowType = raw.slowType;
    if (typeof raw.confirmClose === 'boolean') result.confirmClose = raw.confirmClose;
    for (const key of ['up','down']) if (/^#[0-9a-f]{6}$/i.test(raw[key])) result[key] = raw[key];
    return result;
  }
  let prefs = settings(), active = null, generation = 0, selectedMode = null;
  const mode = () => selectedMode ?? (read('mt5.chart.mode.v1', 'mt5') === 'tradingview' ? 'tradingview' : 'mt5');
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
  // Colour schemes (MT5 "Renk şeması"): Koyu, Siyah, Açık. A custom candle colour overrides the scheme.
  const THEMES = {
    dark:{bg:'#14181f',grid:'#1d232c',text:'#8f98a8',up:defaults.up,down:defaults.down},
    black:{bg:'#000000',grid:'#1c1c1c',text:'#9aa0aa',up:'#00e676',down:'#ff5252'},
    light:{bg:'#ffffff',grid:'#e6e8eb',text:'#4a5262',up:'#089981',down:'#f23645'}};
  const theme = () => THEMES[prefs.theme] || THEMES.dark;
  const upColor = () => prefs.up !== defaults.up ? prefs.up : theme().up;
  const downColor = () => prefs.down !== defaults.down ? prefs.down : theme().down;
  function seriesOptions() {
    const up = upColor(), down = downColor();
    return {upColor:up,downColor:down,borderVisible:false,
      wickUpColor:up,wickDownColor:down,color:up,lineColor:up,
      topColor:up+'50',bottomColor:up+'05',priceFormat:{type:'price',precision:5,minMove:.00001}};
  }
  function applyStyle(state) {
    const th = theme();
    state.host.dataset && (state.host.dataset.chartTheme = prefs.theme);
    state.chart.applyOptions({layout:{background:{type:'solid',color:th.bg},textColor:th.text,fontFamily:"'IBM Plex Mono', ui-monospace, monospace",fontSize:12,attributionLogo:true},
      grid:{vertLines:{visible:prefs.grid,color:th.grid},horzLines:{visible:prefs.grid,color:th.grid}},
      rightPriceScale:{mode:prefs.scale,invertScale:prefs.invert,autoScale:true,scaleMargins:{top:.1,bottom:prefs.volume?.25:.1}},
      crosshair:{mode:prefs.crosshair?1:2},
      timeScale:{timeVisible:true,secondsVisible:false,rightOffset:prefs.shift?8:0,shiftVisibleRangeOnNewBar:prefs.autoscroll},localization:{locale:'tr-TR'}});
    state.price.applyOptions(seriesOptions());
    state.price.applyOptions({priceLineVisible:prefs.priceLine,lastValueVisible:prefs.priceLine});
    state.volume.applyOptions({visible:prefs.volume});
    state.average.applyOptions({visible:prefs.sma});
    state.exponential.applyOptions({visible:prefs.ema});
    if (state.hma) state.hma.applyOptions({visible:prefs.hma});
    drawTradeLevels(state);
    drawAskLine(state);
    drawOverlay(state);
  }
  function renderData(state, fit = false) {
    if (!state.rows.length) return;
    const rows = state.rows;
    state.price.setData(['line','area'].includes(prefs.type) ? rows.map(r=>({time:r.time,value:r.close})) : rows);
    // Broker precision takes precedence over floating point string artifacts.
    const digits = window.MT5Markets?.getSymbol?.(state.symbol)?.digits;
    const precision = Number.isInteger(digits) && digits >= 0 && digits <= 8 ? digits
      : Math.min(8, Math.max(2, ...rows.slice(-50).flatMap(r=>['open','high','low','close'].map(k=>(String(r[k]).split('.')[1]||'').length))));
    const priceFormat = {type:'price',precision,minMove:10**-precision};
    for (const series of [state.price,state.average,state.exponential,state.hma].filter(Boolean)) series.applyOptions({priceFormat});
    state.volume.setData(rows.map(r=>({time:r.time,value:r.tick_volume,color:(r.close>=r.open?upColor():downColor())+'70'})));
    let sum = 0; const avg=[];
    rows.forEach((r,i)=>{sum+=r.close;if(i>=prefs.period) sum-=rows[i-prefs.period].close;if(i>=prefs.period-1) avg.push({time:r.time,value:sum/prefs.period});});
    state.average.setData(avg);
    const ema = slowValues(rows, prefs.slowType, prefs.emaPeriod);
    state.exponential.setData(ema);
    state.exponential.applyOptions({title: prefs.slowType});
    const hma = hmaValues(rows,prefs.hmaPeriod);
    if (state.hma) state.hma.setData(hma);
    if (state.rsi) state.rsi.setData(rsiValues(rows, prefs.rsiPeriod));
    state.legendData = {hma, ema};
    renderLegend(state);
    const markers = prefs.signals ? signalMarkers(rows, prefs.hmaPeriod, prefs.emaPeriod, prefs.slowType, prefs.confirmClose) : [];
    if (state.markersPlugin && typeof state.markersPlugin.setMarkers === 'function') {
      state.markersPlugin.setMarkers(markers);
    }
    if (state.signalBadge) {
      const lastMarker = markers.at(-1);
      const tfCode = frames[state.timeframe] || `M${state.timeframe}`;
      const tfText = frameLabels[state.timeframe] || `${state.timeframe}m`;
      if (lastMarker) {
        const isBuy = lastMarker.shape === 'arrowUp';
        const isRecent = rows.length >= 2 && lastMarker.time >= rows[rows.length - 2].time;
        state.signalBadge.textContent = `${isBuy ? '▲ AL Sinyali' : '▼ SAT Sinyali'} (${tfText} · ${tfCode}${isRecent ? ' · Taze' : ''})`;
        state.signalBadge.style.color = isBuy ? '#10b981' : '#ef4444';
        state.signalBadge.hidden = false;
      } else {
        const hmaLatest = hma.at(-1)?.value, emaLatest = ema.at(-1)?.value;
        if (Number.isFinite(hmaLatest) && Number.isFinite(emaLatest)) {
          const isBuy = hmaLatest > emaLatest;
          state.signalBadge.textContent = `${isBuy ? '▲ AL Trendi' : '▼ SAT Trendi'} (${tfText} · ${tfCode})`;
          state.signalBadge.style.color = isBuy ? '#10b981' : '#ef4444';
          state.signalBadge.hidden = false;
        } else {
          state.signalBadge.hidden = true;
        }
      }
    }
    if (fit) { state.chart.timeScale().fitContent(); state.chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-120),to:rows.length+4}); }
  }
  function emaValues(rows, period) {
    // MT5 iMA MODE_EMA: seeded with the first bar's price, then price*k + prev*(1-k) on every bar.
    if (rows.length < period || period <= 0) return [];
    const k = 2 / (period + 1), result = [];
    let value = rows[0].close;
    for (let i = 0; i < rows.length; i++) {
      if (i) value = rows[i].close * k + value * (1 - k);
      if (i >= period - 1) result.push({time: rows[i].time, value});
    }
    return result;
  }
  function rsiValues(rows, period = 14) {
    // MT5 RSI.mq5 (iRSI): first average = simple mean of the first `period` changes, then Wilder
    // smoothing (prev*(p-1)+change)/p. Zero average loss -> 100 (50 when there is no movement).
    const p = Math.max(2, Math.floor(Number(period) || 14));
    if (rows.length <= p) return [];
    let pos = 0, neg = 0;
    for (let i = 1; i <= p; i++) { const d = rows[i].close - rows[i - 1].close; if (d > 0) pos += d; else neg -= d; }
    pos /= p; neg /= p;
    const value = () => neg !== 0 ? 100 - 100 / (1 + pos / neg) : (pos !== 0 ? 100 : 50);
    const result = [{time: rows[p].time, value: value()}];
    for (let i = p + 1; i < rows.length; i++) {
      const d = rows[i].close - rows[i - 1].close;
      pos = (pos * (p - 1) + (d > 0 ? d : 0)) / p;
      neg = (neg * (p - 1) + (d < 0 ? -d : 0)) / p;
      result.push({time: rows[i].time, value: value()});
    }
    return result;
  }
  function smaValues(rows, period) {
    const result = [];
    for (let i = period - 1; i < rows.length; i++) {
      let sum = 0;
      for (let j = 0; j < period; j++) sum += rows[i - j].close;
      result.push({time: rows[i].time, value: sum / period});
    }
    return result;
  }
  function lwmaValues(rows, period) {
    const closes = rows.map(r => r.close), result = [];
    for (let i = period - 1; i < rows.length; i++) result.push({time: rows[i].time, value: wmaAt(closes, i, period)});
    return result;
  }
  function slowValues(rows, type, period) {
    if (type === 'SMA') return smaValues(rows, period);
    if (type === 'LWMA') return lwmaValues(rows, period);
    if (type === 'HMA') return hmaValues(rows, period);
    return emaValues(rows, period);
  }
  function wmaAt(values, idx, period) {
    if (idx < period - 1 || period <= 0) return null;
    let num = 0, den = (period * (period + 1)) / 2;
    for (let j = 0; j < period; j++) num += values[idx - j] * (period - j);
    return num / den;
  }
  function hmaValues(rows, period) {
    const p = Math.max(2, Math.floor(Number(period) || 14));
    const half = Math.max(1, Math.floor(p / 2));
    const sqrtP = Math.max(1, Math.floor(Math.sqrt(p)));
    if (rows.length < p + sqrtP - 1) return [];
    const closes = rows.map(r => r.close);
    const diffs = [];
    for (let i = 0; i < closes.length; i++) {
      const wh = wmaAt(closes, i, half);
      const wf = wmaAt(closes, i, p);
      diffs.push(wh !== null && wf !== null ? 2 * wh - wf : null);
    }
    const result = [];
    for (let i = p - 1 + sqrtP - 1; i < rows.length; i++) {
      let num = 0, den = (sqrtP * (sqrtP + 1)) / 2, ok = true;
      for (let j = 0; j < sqrtP; j++) {
        const d = diffs[i - j];
        if (d === null) { ok = false; break; }
        num += d * (sqrtP - j);
      }
      if (ok) result.push({time: rows[i].time, value: num / den});
    }
    return result;
  }
  function signalMarkers(rows, hmaPeriod = 14, emaPeriod = 34, slowType = 'EMA', confirmClose = true) {
    // Same rule as the scanner/bot: HMA x selected slow MA; with confirmation only closed candles
    // (the forming candle can still change, so an arrow there could disappear).
    if (confirmClose) rows = rows.slice(0, -1);
    const hma = hmaValues(rows, hmaPeriod);
    const ema = slowValues(rows, SLOW_TYPES.includes(slowType) ? slowType : 'EMA', emaPeriod);
    if (!hma.length || !ema.length) return [];
    const hMap = new Map(hma.map(x => [x.time, x.value]));
    const eMap = new Map(ema.map(x => [x.time, x.value]));
    const markers = [];
    for (let i = 1; i < rows.length; i++) {
      const tPrev = rows[i - 1].time, tCurr = rows[i].time;
      const hPrev = hMap.get(tPrev), ePrev = eMap.get(tPrev);
      const hCurr = hMap.get(tCurr), eCurr = eMap.get(tCurr);
      if (hPrev === undefined || ePrev === undefined || hCurr === undefined || eCurr === undefined) continue;
      if (hPrev <= ePrev && hCurr > eCurr) {
        markers.push({time: tCurr, position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'AL'});
      } else if (hPrev >= ePrev && hCurr < eCurr) {
        markers.push({time: tCurr, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'SAT'});
      }
    }
    return markers;
  }
  function fmt(state, value) {
    const digits = window.MT5Markets?.getSymbol?.(state.symbol)?.digits;
    return Number.isFinite(value) ? value.toFixed(Number.isInteger(digits) ? digits : 5) : '—';
  }
  function renderLegend(state, time) {
    const node = state.legend; if (!node) return;
    const rows = state.rows, row = time ? rows.find(r => r.time === time) : rows.at(-1);
    const at = list => (time ? list?.find(x => x.time === time) : list?.at(-1))?.value;
    const parts = [];
    if (prefs.hma) parts.push(`<span class="mt5-legend-item" data-line="hma"><i></i>HMA ${prefs.hmaPeriod} · ${fmt(state, at(state.legendData?.hma))}</span>`);
    if (prefs.ema) parts.push(`<span class="mt5-legend-item" data-line="slow"><i></i>${prefs.slowType} ${prefs.emaPeriod} · ${fmt(state, at(state.legendData?.ema))}</span>`);
    const ohlc = row ? `<span class="mt5-legend-ohlc">${state.symbol},${frames[state.timeframe]||''} &nbsp;O ${fmt(state,row.open)} &nbsp;H ${fmt(state,row.high)} &nbsp;L ${fmt(state,row.low)} &nbsp;C ${fmt(state,row.close)} &nbsp;V ${row.tick_volume}</span>` : '';
    node.innerHTML = `<div class="mt5-legend-lines">${parts.join('')}</div>${ohlc}`;
  }
  // MT5 "İşlem seviyeleri": open positions' entry, SL and TP lines on the chart of their symbol.
  let tradeLevels = [];
  function drawTradeLevels(state) {
    if (!state?.price) return;
    (state.tradeLines || []).forEach(line => { try { state.price.removePriceLine(line); } catch (_) {} });
    state.tradeLines = [];
    if (!prefs.tradeLevels || typeof state.price.createPriceLine !== 'function') return;
    for (const p of tradeLevels) {
      if (p.symbol !== state.symbol) continue;
      const buy = p.type === 'BUY', id = `#${p.ticket}`;
      const add = (price, color, title, style) => { if (Number(price) > 0) state.tradeLines.push(state.price.createPriceLine({price:Number(price),color,lineWidth:1,lineStyle:style,axisLabelVisible:true,title})); };
      add(p.price_open, buy ? '#4c8dff' : '#d64545', `${id} ${p.type} ${p.volume}`, 2);
      add(p.sl, '#ff7b7b', `SL ${id}`, 1);
      add(p.tp, '#3ccf91', `TP ${id}`, 1);
    }
  }
  function setTradeLevels(positions) {
    tradeLevels = (Array.isArray(positions) ? positions : []).filter(p => p && p.symbol && (p.type === 'BUY' || p.type === 'SELL'))
      .map(p => ({ticket:p.ticket, symbol:p.symbol, type:p.type, volume:p.volume, price_open:p.price_open, sl:p.sl, tp:p.tp}));
    if (active?.loaded) drawTradeLevels(active);
  }
  // MT5 "Ask çizgisi": a line at the current Ask (the Bid is the series' own last-price line).
  function drawAskLine(state) {
    if (!state?.price) return;
    if (state.askLine) { try { state.price.removePriceLine(state.askLine); } catch (_) {} state.askLine = null; }
    if (prefs.askLine && state.ask > 0 && typeof state.price.createPriceLine === 'function')
      state.askLine = state.price.createPriceLine({price:state.ask,color:'#ff7b7b',lineWidth:1,lineStyle:0,axisLabelVisible:true,title:'Ask'});
  }
  // Separator period like MT5: M1…H1 -> day, H4 -> week (Monday), D1 -> month. Times are broker time.
  function separatorKey(time, timeframe) {
    const d = new Date(time * 1000);
    if (timeframe >= 1440) return `${d.getUTCFullYear()}-${d.getUTCMonth()}`;
    if (timeframe >= 240) { const monday = Math.floor((time / 86400 + 3) / 7); return `w${monday}`; }
    return `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
  }
  function drawOverlay(state) {
    const layer = state?.overlay; if (!layer || !state.chart) return;
    const scale = state.chart.timeScale(), x = t => scale.timeToCoordinate?.(t);
    const marks = [];
    if (prefs.separators && state.rows.length) {
      let prev = null;
      for (const r of state.rows) { const key = separatorKey(r.time, state.timeframe); if (prev !== null && key !== prev) marks.push({time:r.time, cls:'mt5-sep'}); prev = key; }
    }
    for (const t of state.vlines || []) marks.push({time:t, cls:'mt5-vline'});
    layer.innerHTML = marks.map(m => { const px = x(m.time); return Number.isFinite(px) ? `<i class="${m.cls}" style="left:${Math.round(px)}px"></i>` : ''; }).join('');
  }
  const ZOOM_MIN = 30, ZOOM_MAX = 280;
  function visibleBars(state = active) {
    const range = state?.chart?.timeScale?.().getVisibleLogicalRange?.();
    return range ? Math.max(1, Math.round(range.to - range.from)) : 0;
  }
  function showZoom(state = active) {
    const node = document.getElementById('chart-zoom-count');
    const bars = visibleBars(state);
    if (node && bars) node.textContent = `${bars} mum`;
  }
  function zoom(direction) {
    const state = active; if (!state?.rows.length) return;
    const scale = state.chart.timeScale(), range = scale.getVisibleLogicalRange?.();
    if (!range) return;
    const bars = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((range.to - range.from) * (direction > 0 ? 0.75 : 1.33))));
    scale.setVisibleLogicalRange({from: range.to - bars, to: range.to});
    showZoom(state);
  }
  function latest() { active?.chart?.timeScale().scrollToRealTime(); }
  function setType(type) { if (['candles','bars','line','area'].includes(type)) change('type', type); syncToolbar(); }
  function getType() { return prefs.type; }
  function openMenu() { const state = active; if (!state) return; state.selectedPrice = null; state.selectedTime = null; showMenu(state); }
  function syncToolbar() {
    document.querySelectorAll?.('[data-chart-type]').forEach(btn => btn.setAttribute('aria-pressed', String(btn.dataset.chartType === prefs.type)));
    document.querySelectorAll?.('button[data-chart-mode]').forEach(btn => btn.setAttribute('aria-pressed', String(btn.dataset.chartMode === mode())));
    const card = document.getElementById('chart-card');
    if (card?.dataset) card.dataset.chartMode = mode();
  }
  function csvData(state) {
    // Broker candle timestamps are preserved, not mislabeled as UTC dates.
    return 'symbol,timeframe_minutes,time_broker,open,high,low,close,tick_volume\r\n'+state.rows.map(r=>
      [state.symbol,state.timeframe,r.time,r.open,r.high,r.low,r.close,r.tick_volume].join(',')).join('\r\n');
  }
  function download(blob, filename) {
    const url=URL.createObjectURL(blob), link=document.createElement('a');
    link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  function rebuildPrice(state) {
    if (state.price) state.chart.removeSeries(state.price);
    const types = {candles:LightweightCharts.CandlestickSeries,bars:LightweightCharts.BarSeries,line:LightweightCharts.LineSeries,area:LightweightCharts.AreaSeries};
    state.price = state.chart.addSeries(types[prefs.type], seriesOptions());
    state.markersPlugin = typeof LightweightCharts.createSeriesMarkers === 'function' ? LightweightCharts.createSeriesMarkers(state.price, []) : null;
    state.lines = [];
    state.tradeLines = [];
    for (const price of state.savedLines) state.lines.push(state.price.createPriceLine({price,color:'#38bdf8',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Seviye'}));
  }
  async function load(state, full = false) {
    if (state !== active) return;
    if (document.hidden && !full) { state.timer=setTimeout(()=>load(state),5000); return; }
    const token = generation;
    const controller = new AbortController(); state.abort=controller;
    const timeout = setTimeout(()=>controller.abort(),12000);
    try {
      const count = full || !state.rows.length ? HISTORY_BARS : 3;
      const res = await fetch(`/api/chart/candles?symbol=${encodeURIComponent(state.symbol)}&timeframe_minutes=${state.timeframe}&count=${count}`, {signal:controller.signal});
      const data = await res.json();
      if (token !== generation || state !== active) return;
      if (!res.ok || data.symbol !== state.symbol || data.timeframe_minutes !== state.timeframe) throw new Error(data.detail || 'Grafik verisi alınamadı.');
      const rows = normalize(data.rates);
      if (!rows.length) throw new Error('Broker geçerli mum verisi göndermedi.');
      // Refill history after a long disconnect instead of drawing across missing bars.
      const gap = state.rows.length && rows[0].time > state.rows.at(-1).time + state.timeframe*60;
      if (gap && !full) { clearTimeout(timeout); return await load(state,true); }
      state.rows = normalize(full ? rows : [...state.rows,...rows]).slice(-HISTORY_BARS);
      renderData(state, !state.loaded);
      state.loaded=true;
      const tfCode = frames[state.timeframe] || `M${state.timeframe}`;
      const tfText = frameLabels[state.timeframe] || `${state.timeframe}m`;
      status(state,`${state.symbol} · ${tfCode} (${tfText}) · MT5 · Broker saati · Güncellendi ${new Date().toLocaleTimeString('tr-TR')}`);
    } catch (error) {
      if (state === active && token === generation) status(state,`Veri güncellenemedi · ${error.name === 'AbortError' ? 'Bağlantı zaman aşımı' : error.message} · Yeniden deneniyor`);
    } finally {
      clearTimeout(timeout);
      if (state === active && token === generation) { clearTimeout(state.timer); state.timer=setTimeout(()=>load(state),2000); }
    }
  }
  function tick(symbol, bid, ask, time) {
    const state = active;
    if (!state || !state.loaded || state.symbol !== symbol || !state.rows.length) return;
    if (!Number.isFinite(bid) || bid <= 0) return;
    state.bid = bid;
    if (Number.isFinite(ask) && ask > 0 && ask !== state.ask) { state.ask = ask; if (prefs.askLine) { if (state.askLine?.applyOptions) state.askLine.applyOptions({price:ask}); else drawAskLine(state); } }
    const last = state.rows[state.rows.length - 1];
    const step = state.timeframe * 60;
    const barTime = Number.isInteger(time) && time >= last.time ? time - (time % step) : last.time;
    if (barTime > last.time) {
      // Tick volume comes only from MT5's own bars (2-second refresh), never from counting UI updates.
      state.rows.push({time: barTime, open: bid, high: bid, low: bid, close: bid, tick_volume: 0});
      if (state.rows.length > HISTORY_BARS) state.rows.shift();
    } else {
      last.close = bid;
      if (bid > last.high) last.high = bid;
      if (bid < last.low) last.low = bid;
    }
    const current = state.rows[state.rows.length - 1];
    if (typeof state.price?.update === 'function') {
      state.price.update(['line','area'].includes(prefs.type) ? {time: current.time, value: current.close} : current);
    } else {
      renderData(state, false);
    }
  }
  function saveSettings(state) {
    status(state, write('mt5.chart.settings.v1', prefs) ? 'Ayarlar bu tarayıcıda kaydedildi · Broker saati' : 'Ayar uygulandı; tarayıcı kaydetmeye izin vermedi.');
  }
  function change(key, value) {
    prefs[key] = value;
    const state = active;
    if (!state) {
      write('mt5.chart.settings.v1', prefs);
      if (key === 'timeframe' && typeof updateChartTimeframeButtons === 'function') updateChartTimeframeButtons(value);
      return;
    }
    saveSettings(state);
    if (key === 'timeframe' || key === 'rsi') {
      mount(state.symbol);
      if (key === 'timeframe' && typeof updateChartTimeframeButtons === 'function') updateChartTimeframeButtons(value);
      return;
    }
    const range=state.chart.timeScale().getVisibleLogicalRange();
    if (key === 'type') { rebuildPrice(state); syncToolbar(); }
    applyStyle(state); renderData(state);
    if (range) state.chart.timeScale().setVisibleLogicalRange(range);
  }
  function priceText(state, value) { return value > 0 ? fmt(state, value) : '—'; }
  function showMenu(state, event) {
    event?.preventDefault?.();
    const menu = state.menu;
    menu.hidden=false;
    for (const node of menu.querySelectorAll('[data-setting]')) {
      const value=prefs[node.dataset.setting];
      if (node.type === 'checkbox') node.checked=value; else node.value=String(value);
    }
    // Toggle rows (☑/☐) and segment buttons show the current chart settings.
    for (const node of menu.querySelectorAll('[data-action="toggle"]')) {
      const key=node.dataset.value, on = key === 'hmaema' ? prefs.hma && prefs.ema : !!prefs[key];
      node.setAttribute?.('aria-checked', String(on));
    }
    for (const node of menu.querySelectorAll('[data-action="type"],[data-action="timeframe"],[data-action="scale"],[data-action="theme"]')) {
      const key=node.dataset.action, value=key==='type'||key==='theme'?prefs[key]:Number(prefs[key]);
      node.setAttribute?.('aria-pressed', String(String(value)===node.dataset.value));
    }
    const price=state.selectedPrice, has=price>0;
    menu.querySelector('[data-context-price]').value=has?String(Number(price.toFixed(8))):'Fiyat seçilmedi';
    const label=menu.querySelector('[data-context-label]');
    if (label) {
      const t=state.selectedTime, when=Number.isFinite(t)?new Date(t*1000).toLocaleString('tr-TR',{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
      label.textContent=has?`Fiyat ${priceText(state,price)}${when?' · '+when:''}`:`${state.symbol} · ${frames[state.timeframe]||''}`;
    }
    menu.querySelector('[data-action="copy-price"]').disabled=!has;
    for(const action of ['export-csv','export-png']) menu.querySelector(`[data-action="${action}"]`).disabled=!state.loaded;
    // Market buttons show Bid / Ask; pending suggestions follow the clicked price like MT5:
    // below the Bid -> Buy Limit + Sell Stop, above the Ask -> Sell Limit + Buy Stop.
    const bid=state.bid||state.rows.at(-1)?.close, ask=state.ask||bid;
    const sell=menu.querySelector('[data-quote="bid"]'), buy=menu.querySelector('[data-quote="ask"]');
    if (sell) sell.textContent=priceText(state,bid); if (buy) buy.textContent=priceText(state,ask);
    const pend=[...(menu.querySelectorAll('[data-action="pending"]')||[])];
    const types=!has?[]:price<bid?['BUY_LIMIT','SELL_STOP']:price>ask?['SELL_LIMIT','BUY_STOP']:[];
    const names={BUY_LIMIT:'Buy Limit',SELL_LIMIT:'Sell Limit',BUY_STOP:'Buy Stop',SELL_STOP:'Sell Stop'};
    pend.forEach((node,i)=>{const type=types[i];node.hidden=!type;if(type){node.dataset.value=type;node.textContent=`${names[type]} @ ${priceText(state,price)}`;}});
    for (const action of ['level','vline']) { const node=menu.querySelector(`[data-action="${action}"]`); if (node) node.disabled = action==='level' ? !has : !Number.isFinite(state.selectedTime); }
    const clear=menu.querySelector('[data-action="clear-objects"]');
    if (clear) clear.textContent=`Tüm nesneleri sil (${state.savedLines.length+(state.vlines||[]).length})`;
    state.menuX=event?.clientX; state.menuY=event?.clientY;
    const bounds=state.host.getBoundingClientRect();
    menu.style.maxHeight=`${Math.max(200,Math.min(bounds.height-8,window.innerHeight*0.8||bounds.height))}px`;
    menu.style.left=`${Math.max(4,Math.min((event?.clientX ?? bounds.left+12)-bounds.left,bounds.width-menu.offsetWidth-4))}px`;
    menu.style.top=`${Math.max(4,Math.min((event?.clientY ?? bounds.top+12)-bounds.top,bounds.height-menu.offsetHeight-4))}px`;
    menu.querySelector('button')?.focus?.();
  }
  const orderSymbol = () => typeof currentSymbol !== 'undefined' ? currentSymbol : window.currentSymbol;
  // Fills the order panel's pending form at the clicked price (MT5 chart menu); never sends an order.
  function stagePending(state, type, price) {
    if (state.symbol !== orderSymbol()) return;
    const digits = window.MT5Markets?.getSymbol?.(state.symbol)?.digits;
    const value = Number(price.toFixed(Number.isInteger(digits) ? digits : 5));
    window.setOrderExecutionType?.('PENDING');
    const select = document.getElementById('pending-type'), input = document.getElementById('pending-price');
    if (!select || !input) return;
    select.value = type; input.value = String(value);
    window.changePendingOptions?.();
    document.getElementById('order-card')?.scrollIntoView?.({behavior:'smooth', block:'start'});
    input.focus?.();
    window.showToast?.(`${type.replace('_',' ')} @ ${value}: bekleyen emir formu dolduruldu; emir gönderilmedi.`, 'info');
  }
  function mount(symbol) {
    dispose();
    const host=document.getElementById('tradingview-container'); if (!host) return;
    host.innerHTML=`<div class="mt5-chart-shell">
      <div class="mt5-chart-toolbar"><div class="mt5-chart-legend" aria-live="off"></div><span class="mt5-chart-status" role="status">MT5 grafiği yükleniyor…</span><span class="mt5-chart-signal font-mono font-bold text-xs px-2 py-0.5 rounded bg-gray-800/80" hidden></span><button type="button" class="mt5-settings-button">Grafik ayarları · Sağ tuş</button></div>
      <div class="mt5-chart-canvas" tabindex="0" aria-label="MT5 fiyat grafiği"><div class="mt5-chart-overlay" aria-hidden="true"></div></div>
      <div class="mt5-chart-menu" hidden role="dialog" aria-label="Grafik ayarları">
        <div class="mt5-menu-title"><span data-context-label>Grafik ayarları</span><button type="button" data-action="close" aria-label="Grafik ayarlarını kapat">✕</button></div>
        <input data-context-price readonly aria-label="Sağ tıklanan fiyat" class="sr-only" tabindex="-1">
        <p class="mt5-menu-h">Alım satım</p>
        <div class="mt5-menu-trade"><button type="button" data-action="sell" class="mt5-menu-sell">Sat · <span data-quote="bid">—</span></button><button type="button" data-action="buy" class="mt5-menu-buy">Al · <span data-quote="ask">—</span></button></div>
        <button type="button" data-action="pending" hidden></button>
        <button type="button" data-action="pending" hidden></button>
        <button type="button" data-action="depth">Piyasa derinliği</button>
        <p class="mt5-menu-h">Grafik türü</p>
        <div class="mt5-menu-seg"><button type="button" data-action="type" data-value="candles" aria-pressed="false">Mum</button><button type="button" data-action="type" data-value="bars" aria-pressed="false">Çubuk</button><button type="button" data-action="type" data-value="line" aria-pressed="false">Çizgi</button><button type="button" data-action="type" data-value="area" aria-pressed="false">Alan</button></div>
        <p class="mt5-menu-h">Zaman dilimi</p>
        <div class="mt5-menu-seg">${Object.entries(frames).map(([k,v])=>`<button type="button" data-action="timeframe" data-value="${k}" aria-pressed="false">${v}</button>`).join('')}</div>
        <p class="mt5-menu-h">Göstergeler</p>
        <button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="hmaema" aria-checked="false">HMA / yavaş MA (sinyal)</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="sma" aria-checked="false">SMA (hareketli ortalama)</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="rsi" aria-checked="false">RSI (alt pencere)</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="volume" aria-checked="false">Tick hacmi</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="signals" aria-checked="false">Al / Sat okları</button>
        <p class="mt5-menu-h">Nesneler</p>
        <button type="button" data-action="level">Bu fiyata yatay çizgi ekle</button>
        <button type="button" data-action="vline">Bu zamana dikey çizgi ekle</button>
        <button type="button" data-action="clear-objects">Tüm nesneleri sil</button>
        <p class="mt5-menu-h">Görünüm</p>
        <button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="grid" aria-checked="false">Izgara</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="priceLine" aria-checked="false">Bid çizgisi</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="askLine" aria-checked="false">Ask çizgisi</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="separators" aria-checked="false">Dönem ayırıcı</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="tradeLevels" aria-checked="false">İşlem seviyeleri</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="shift" aria-checked="false">Grafik kaydırma</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="autoscroll" aria-checked="false">Otomatik kaydırma</button><button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="crosshair" aria-checked="false">Artı imleç</button>
        <p class="mt5-menu-h">Ölçek</p>
        <div class="mt5-menu-seg"><button type="button" data-action="scale" data-value="0" aria-pressed="false">Normal</button><button type="button" data-action="scale" data-value="1" aria-pressed="false">Log</button><button type="button" data-action="scale" data-value="2" aria-pressed="false">Yüzde</button></div>
        <button type="button" class="mt5-menu-check" role="menuitemcheckbox" data-action="toggle" data-value="invert" aria-checked="false">Ölçeği ters çevir</button>
        <div class="mt5-menu-row"><button type="button" data-action="zoom-in">Yakınlaştır</button><button type="button" data-action="zoom-out">Uzaklaştır</button></div>
        <div class="mt5-menu-row"><button type="button" data-action="latest">Son muma dön</button><button type="button" data-action="fit">Ekrana sığdır</button></div>
        <p class="mt5-menu-h">Renk şeması</p>
        <div class="mt5-menu-seg"><button type="button" data-action="theme" data-value="dark" aria-pressed="false">Koyu</button><button type="button" data-action="theme" data-value="black" aria-pressed="false">Siyah</button><button type="button" data-action="theme" data-value="light" aria-pressed="false">Açık</button></div>
        <p class="mt5-menu-h">Şablon ve dışa aktarma</p>
        <button type="button" data-action="save-template">Görünüm şablonunu kaydet</button>
        <button type="button" data-action="load-template">Kayıtlı şablonu uygula</button>
        <button type="button" data-action="copy-price">Fiyatı kopyala</button>
        <button type="button" data-action="export-png">Grafik görüntüsü · PNG</button>
        <button type="button" data-action="export-csv">Mum verileri · CSV</button>
        <details class="mt5-menu-more"><summary>Periyotlar ve renkler</summary>
          <label>HMA periyodu<input type="number" min="2" max="200" data-setting="hmaPeriod" aria-label="HMA periyodu"></label>
          <label>Yavaş MA periyodu<input type="number" min="2" max="200" data-setting="emaPeriod" aria-label="Yavaş MA periyodu"></label>
          <label>SMA periyodu<input type="number" min="2" max="200" data-setting="period" aria-label="SMA periyodu"></label>
          <label>RSI periyodu<input type="number" min="2" max="200" data-setting="rsiPeriod" aria-label="RSI periyodu"></label>
          <label>Yükseliş rengi<input type="color" data-setting="up" aria-label="Yükseliş rengi"></label>
          <label>Düşüş rengi<input type="color" data-setting="down" aria-label="Düşüş rengi"></label>
          <button type="button" data-action="clear-indicators">Göstergeleri kaldır</button>
          <button type="button" data-action="reset">Grafik ayarlarını sıfırla</button>
        </details>
        <p class="mt5-menu-note">Ayarlar bu tarayıcıda saklanır. Al / Sat onay penceresinden geçer; bekleyen satırları yalnız formu doldurur, emir göndermez.</p>
      </div>
      <div class="mt5-chart-attribution"><a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView Lightweight Charts™</a> · Copyright © 2025 TradingView, Inc. · Veri: MT5</div>
    </div>`;
    const engine=document.getElementById('chart-engine'); if(engine) engine.value='mt5';
    const link=document.getElementById('chart-tradingview-link'); if(link) link.hidden=true;
    if (!window.LightweightCharts) { host.querySelector('.mt5-chart-status').textContent='Grafik kütüphanesi yüklenemedi. Sayfayı yenileyin.';return; }
    const saved = read(`mt5.chart.levels.${symbol}`,[]);
    const state = active = {symbol,timeframe:prefs.timeframe,host,rows:[],loaded:false,cleanup:[],
      savedLines:Array.isArray(saved)?saved.filter(v=>typeof v==='number'&&Number.isFinite(v)&&v>0).slice(0,50):[],
      menu:host.querySelector('.mt5-chart-menu'),status:host.querySelector('.mt5-chart-status'),
      signalBadge:host.querySelector('.mt5-chart-signal'),legend:host.querySelector('.mt5-chart-legend'),
      overlay:host.querySelector('.mt5-chart-overlay')};
    const savedV = read(`mt5.chart.vlines.${symbol}`,[]);
    state.vlines = Array.isArray(savedV)?savedV.filter(v=>Number.isInteger(v)&&v>0).slice(0,50):[];
    const canvas=host.querySelector('.mt5-chart-canvas');
    state.chart=LightweightCharts.createChart(canvas,{autoSize:true,
      handleScroll:{mouseWheel:false}, handleScale:{mouseWheel:false}});
    // Wheel zooms/scrolls the chart only after a click on it; before that the page scrolls.
    if (window.HMAChartWheel) state.cleanup.push(window.HMAChartWheel.attach(canvas.parentElement || canvas, {
      armTarget: canvas,
      onArm: () => state.chart?.applyOptions({handleScroll:{mouseWheel:true}, handleScale:{mouseWheel:true}}),
      onDisarm: () => state.chart?.applyOptions({handleScroll:{mouseWheel:false}, handleScale:{mouseWheel:false}}),
    }));
    rebuildPrice(state);
    state.volume=state.chart.addSeries(LightweightCharts.HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
    state.volume.priceScale().applyOptions({scaleMargins:{top:.8,bottom:0}});
    state.average=state.chart.addSeries(LightweightCharts.LineSeries,{color:'#fbbf24',lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:'SMA'});
    state.exponential=state.chart.addSeries(LightweightCharts.LineSeries,{color:'#a78bfa',lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:'EMA'});
    state.hma=state.chart.addSeries(LightweightCharts.LineSeries,{color:'#f0b44c',lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:'HMA'});
    if (prefs.rsi) {
      // Separate pane under the price, like MT5's indicator subwindow (levels 30/70).
      state.rsi=state.chart.addSeries(LightweightCharts.LineSeries,{color:'#4c8dff',lineWidth:1,priceLineVisible:false,lastValueVisible:true,title:`RSI ${prefs.rsiPeriod}`,priceFormat:{type:'price',precision:2,minMove:.01}},1);
      for (const level of [30,70]) state.rsi.createPriceLine?.({price:level,color:'#5b6474',lineWidth:1,lineStyle:2,axisLabelVisible:false,title:''});
      state.rsi.applyOptions?.({autoscaleInfoProvider:()=>({priceRange:{minValue:0,maxValue:100}})});
    }
    state.chart.subscribeCrosshairMove?.(param => renderLegend(state, param?.time));
    state.chart.timeScale().subscribeVisibleLogicalRangeChange?.(() => { showZoom(state); drawOverlay(state); });
    state.chart.subscribeSizeChange?.(() => drawOverlay(state));
    applyStyle(state);
    syncToolbar();
    const tfCode = frames[prefs.timeframe] || `M${prefs.timeframe}`;
    const tfText = frameLabels[prefs.timeframe] || `${prefs.timeframe}m`;
    const titleNode = document.getElementById('current-symbol-title');
    if (titleNode) titleNode.textContent = `${symbol} • ${tfText} (${tfCode})`;
    const descNode = document.getElementById('chart-symbol-desc');
    if (descNode) descNode.textContent = window.MT5Markets?.getSymbol?.(symbol)?.description || '';
    if (typeof updateChartTimeframeButtons === 'function') updateChartTimeframeButtons(prefs.timeframe);
    const on=(target,name,fn)=>{target.addEventListener(name,fn);state.cleanup.push(()=>target.removeEventListener(name,fn));};
    on(canvas,'contextmenu',event=>{
      const bounds=canvas.getBoundingClientRect();state.selectedPrice=state.price.coordinateToPrice(event.clientY-bounds.top);
      const t=state.chart.timeScale().coordinateToTime?.(event.clientX-bounds.left);state.selectedTime=Number.isFinite(t)?t:null;
      showMenu(state,event);
    });
    on(canvas,'keydown',event=>{if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){state.selectedPrice=null;state.selectedTime=null;showMenu(state,event);}});
    on(host.querySelector('.mt5-settings-button'),'click',()=>{state.selectedPrice=null;state.selectedTime=null;showMenu(state);});
    on(state.menu,'change',event=>{
      const key=event.target.dataset.setting;if(!key) return;
      const value=event.target.type==='checkbox'?event.target.checked:['timeframe','period','emaPeriod','hmaPeriod','rsiPeriod','scale'].includes(key)?Number(event.target.value):event.target.value;
      if (['period','emaPeriod','hmaPeriod','rsiPeriod'].includes(key) && (!Number.isInteger(value)||value<2||value>200)) {event.target.value=prefs[key];return;}
      change(key,value);
    });
    on(state.menu,'click',event=>{
      const target=event.target.closest('[data-action]'), action=target?.dataset.action, value=target?.dataset.value;if(!action)return;
      if(action==='toggle') {
        if(value==='hmaema'){const on=!(prefs.hma&&prefs.ema);prefs.hma=on;prefs.ema=on;saveSettings(state);applyStyle(state);renderData(state);}
        else if(Object.hasOwn(defaults,value)) change(value,!prefs[value]);
        if(active===state) showMenu(state,{clientX:state.menuX,clientY:state.menuY,preventDefault(){}});
        return;
      }
      if(['type','theme'].includes(action)) { change(action,value); if(active===state) showMenu(state,{clientX:state.menuX,clientY:state.menuY,preventDefault(){}}); return; }
      if(action==='scale') { change('scale',Number(value)); if(active===state) showMenu(state,{clientX:state.menuX,clientY:state.menuY,preventDefault(){}}); return; }
      if(action==='timeframe') { state.menu.hidden=true; if(typeof window.setChartTimeframe==='function') window.setChartTimeframe(Number(value)); else change('timeframe',Number(value)); return; }
      if(action==='sell'||action==='buy') { state.menu.hidden=true; if(typeof window.submitOrder==='function'&&state.symbol===orderSymbol()) window.submitOrder(action==='buy'?'BUY':'SELL'); return; }
      if(action==='pending' && state.selectedPrice>0 && value) { state.menu.hidden=true; stagePending(state,value,state.selectedPrice); return; }
      if(action==='depth') { state.menu.hidden=true; window.setToolsDrawer?.(true); const panel=document.getElementById('market-depth-panel'); if(panel){panel.open=true;panel.scrollIntoView?.({block:'start'});} return; }
      if(action==='zoom-in'||action==='zoom-out') { zoom(action==='zoom-in'?1:-1); return; }
      if(action==='vline' && Number.isFinite(state.selectedTime) && state.vlines.length<50) { state.vlines.push(state.selectedTime); write(`mt5.chart.vlines.${symbol}`,state.vlines); drawOverlay(state); }
      if(action==='clear-objects') { state.vlines=[]; write(`mt5.chart.vlines.${symbol}`,[]); drawOverlay(state); }
      if(action==='fit') {state.chart.priceScale('right').applyOptions({autoScale:true});state.chart.timeScale().fitContent();}
      if(action==='latest') state.chart.timeScale().scrollToRealTime();
      if(action==='copy-price' && state.selectedPrice>0) {
        const value=String(Number(state.selectedPrice.toFixed(8)));
        if(navigator.clipboard?.writeText) navigator.clipboard.writeText(value).then(()=>status(state,'Fiyat panoya kopyalandı')).catch(()=>{showMenu(state);const input=state.menu.querySelector('[data-context-price]');input.focus();input.select();status(state,'Panoya erişilemedi; seçili fiyatı Ctrl+C ile kopyalayın.');});
        else {showMenu(state);const input=state.menu.querySelector('[data-context-price]');input.focus();input.select();status(state,'Seçili fiyatı Ctrl+C ile kopyalayın.');return;}
      }
      if(action==='export-csv' && state.loaded) download(new Blob(['\ufeff'+csvData(state)],{type:'text/csv;charset=utf-8'}),`${symbol}-${frames[state.timeframe]}-MT5.csv`);
      if(action==='export-png' && state.loaded) state.chart.takeScreenshot().toBlob(blob=>{if(blob)download(blob,`${symbol}-${frames[state.timeframe]}-MT5.png`);},'image/png');
      if(action==='clear-indicators') {prefs.sma=false;prefs.ema=false;prefs.hma=false;prefs.signals=false;prefs.volume=false;saveSettings(state);applyStyle(state);renderData(state);}
      if(action==='save-template') status(state,write('mt5.chart.template.v1',prefs)?'Görünüm şablonu bu tarayıcıda kaydedildi.':'Şablon kaydedilemedi; tarayıcı depolama iznini kontrol edin.');
      if(action==='load-template') {
        const template=read('mt5.chart.template.v1',null);
        if(!template) {status(state,'Henüz kayıtlı görünüm şablonu yok.');state.menu.hidden=true;return;}
        if(write('mt5.chart.settings.v1',template)) {prefs=settings();mount(symbol);return;}
        status(state,'Şablon uygulanamadı; tarayıcı depolama iznini kontrol edin.');
      }
      if(action==='reset') {prefs={...defaults};saveSettings(state);mount(symbol);return;}
      if(action==='level' && state.selectedPrice>0 && state.savedLines.length<50) {
        const price=state.selectedPrice;state.savedLines.push(price);state.lines.push(state.price.createPriceLine({price,color:'#38bdf8',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Seviye'}));write(`mt5.chart.levels.${symbol}`,state.savedLines);
      }
      if(action==='clear-levels'||action==='clear-objects') {state.lines.forEach(line=>state.price.removePriceLine(line));state.lines=[];state.savedLines=[];write(`mt5.chart.levels.${symbol}`,[]);}
      state.menu.hidden=true;canvas.focus();
    });
    on(document,'pointerdown',event=>{if(!state.menu.contains(event.target)&&!event.target.closest('.mt5-settings-button'))state.menu.hidden=true;});
    on(document,'keydown',event=>{if(event.key==='Escape'){state.menu.hidden=true;}});
    load(state,true);
  }
  function setMode(value) {
    // A denied storage write must still apply the choice for this session.
    selectedMode = value === 'tradingview' ? 'tradingview' : 'mt5';
    write('mt5.chart.mode.v1', selectedMode);
    // Manual broker selection remains the source of order identity.
    syncToolbar();
    switchSymbol(currentSymbol);
  }
  function getTimeframe() { return prefs.timeframe; }
  function setTimeframe(tf) {
    const num = Number(tf);
    if (!frames[num]) return;
    change('timeframe', num);
  }
  // Keep the chart's HMA / EMA lines and AL/SAT markers on the scanner's signal settings.
  function setSignalPeriods(hma, slow, type = 'EMA', confirmClose = true) {
    const valid = n => Number.isInteger(n) && n >= 2 && n <= 200;
    if (valid(hma) && prefs.hmaPeriod !== hma) change('hmaPeriod', hma);
    if (valid(slow) && prefs.emaPeriod !== slow) change('emaPeriod', slow);
    if (SLOW_TYPES.includes(type) && prefs.slowType !== type) change('slowType', type);
    if (typeof confirmClose === 'boolean' && prefs.confirmClose !== confirmClose) change('confirmClose', confirmClose);
  }
  window.MT5Chart={mode,setMode,mount,dispose,normalize,emaValues,smaValues,lwmaValues,slowValues,hmaValues,rsiValues,signalMarkers,csvData,tick,getTimeframe,setTimeframe,setSignalPeriods,setType,getType,zoom,latest,openMenu,visibleBars,setTradeLevels,syncToolbar,frames,frameLabels};
})();
