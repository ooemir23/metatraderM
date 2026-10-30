// Volta Trading Dashboard Client Script

// Last chosen symbol survives a reload (MT5 reopens the last chart symbol on restart).
const LAST_SYMBOL_KEY = 'hma.symbol.last.v1';
function readLastSymbol() {
  try {
    const v = localStorage.getItem(LAST_SYMBOL_KEY);
    return typeof v === 'string' && /^[A-Za-z0-9._#&+\-]{1,32}$/.test(v) ? v : null;
  } catch (_) { return null; }
}
function saveLastSymbol(symbol) {
  try { localStorage.setItem(LAST_SYMBOL_KEY, symbol); } catch (_) { /* session-only */ }
}
let currentSymbol = readLastSymbol() || "EURUSD";
let tvWidget = null;
let chartSelectionBlocked = false;
let orderSymbolSource = 'panel';
let chartSymbolTimer = null;
let chartSymbolRequest = 0;
const chartSymbolChecks = new Map();
let isBotRunning = false;
let executableQuote = null;
let orderPreflightPending = false;

// TradingView TV symbol map
const TV_SYMBOLS = {
  "EURUSD": "FX:EURUSD",
  "GBPUSD": "FX:GBPUSD",
  "XAUUSD": "OANDA:XAUUSD",
  "USDJPY": "FX:USDJPY",
  "BTCUSD": "BINANCE:BTCUSDT",
  "US30": "FOREXCOM:DJI"
};

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  if (typeof restoreBotSections === 'function') restoreBotSections();
  for(const id of ['sl-input','tp-input'])document.getElementById(id)?.addEventListener('input',()=>{ticketProtectionEdited=true;});
  window.MT5Markets?.init({onSelect:switchSymbol, onChange:marketCatalogueChanged});
  window.MT5Markets?.setActive(currentSymbol);
  renderScannerSymbolButton(currentSymbol);
  const tag = document.getElementById("order-symbol-tag");
  if (tag) tag.innerText = currentSymbol;
  const title = document.getElementById("current-symbol-title");
  if (title) title.innerText = title.innerText.replace(/^[^\s•]+/, currentSymbol);
  updateChartTimeframeButtons(window.MT5Chart?.getTimeframe?.() || 15);
  initTradingView(currentSymbol);
  initChartResizer();
  initPositionsResizer();
  initPositionsTopResizer();
  startPolling();
});

function updateChartTimeframeButtons(activeTf) {
  const num = Number(activeTf) || 15;
  document.querySelectorAll('.chart-tf-btn').forEach(btn => {
    const on = Number(btn.dataset.tf) === num;
    btn.classList?.toggle('is-active', on);
    btn.setAttribute?.('aria-pressed', String(on));
  });
  const tfNode = document.getElementById('chart-tf-code');
  if (tfNode) tfNode.textContent = window.MT5Chart?.frames?.[num] || '';
  const scanTf = document.getElementById('signal-scan-tf');
  if (scanTf && Number(scanTf.value) !== num) {
    scanTf.value = String(num);
  }
}

function setChartTimeframe(tf) {
  const num = Number(tf);
  if (!num) return;
  if (window.MT5Chart) {
    window.MT5Chart.setTimeframe(num);
  }
  updateChartTimeframeButtons(num);
  const scanTf = document.getElementById('signal-scan-tf');
  if (scanTf && Number(scanTf.value) !== num) {
    scanTf.value = String(num);
    if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true);
  }
  const botTf = document.getElementById('cfg-timeframe');
  if (botTf && [1,5,15,30,60,240,1440].includes(num)) {
    botTf.value = String(num);
  }
  // Bot follows the chart timeframe, also while running (MT5 EA re-init on a chart period change).
  if (typeof syncBotTimeframe === 'function') syncBotTimeframe(num);
}

// The HMA bot trades what the panel shows: the selected symbol, the chart timeframe and the scanner's
// HMA × slow MA. A running bot follows too, like an MT5 EA re-initialised when its chart symbol/period or
// inputs change: positions stay open and the next closed-candle crossover is traded. The server runs the
// same readiness checks as a bot start; if they fail the bot keeps its current settings and the reason is shown.
const BOT_TIMEFRAMES = [1, 5, 15, 30, 60, 240, 1440];
const BOT_FOLLOW_KEYS = ['symbol', 'timeframe_minutes', 'fast_ma_type', 'hma_period', 'second_ma_type', 'second_ma_period'];
let botFollowSyncing = null;
let botFollowError = null;   // {key, message}: a failed change is not retried on every status poll

function desiredBotSelection(overrides = {}) {
  const want = {};
  const sym = String(overrides.symbol ?? (typeof currentSymbol === 'string' ? currentSymbol : '') ?? '').trim();
  if (sym) want.symbol = sym;
  const tf = Number(overrides.timeframe_minutes ?? window.MT5Chart?.getTimeframe?.());
  if (BOT_TIMEFRAMES.includes(tf)) want.timeframe_minutes = tf;
  const sig = window.HMASignalSettings?.get?.();
  if (sig) Object.assign(want, {fast_ma_type: sig.fast_type || 'HMA', hma_period: sig.hma_period, second_ma_type: sig.ma_type, second_ma_period: sig.ema_period});
  for (const key of ['fast_ma_type', 'hma_period', 'second_ma_type', 'second_ma_period']) if (overrides[key] != null) want[key] = overrides[key];
  return want;
}

function botSelectionDiff(bot, want) {
  const diff = {};
  for (const key of BOT_FOLLOW_KEYS) {
    if (want[key] == null) continue;
    if (key === 'symbol') {
      const symbols = Array.isArray(bot.symbols) ? bot.symbols : [bot.symbol];
      if (bot.symbol !== want.symbol || symbols.length !== 1 || symbols[0] !== want.symbol) diff.symbol = want.symbol;
    } else if (bot[key] !== want[key]) diff[key] = want[key];
  }
  return diff;
}

const botTfCode = (tf) => ({1: 'M1', 5: 'M5', 15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', 1440: 'D1'}[tf] || `${tf} dk`);

async function syncBotToSelection(overrides = {}, {announce = true, retry = true} = {}) {
  const bot = latestBotSettings;
  if (!bot) return false;
  if (overrides.timeframe_minutes != null && !BOT_TIMEFRAMES.includes(Number(overrides.timeframe_minutes))) return false;
  if (botFollowSyncing) {
    await botFollowSyncing.catch(() => {});
    return syncBotToSelection(overrides, {announce, retry});
  }
  const want = desiredBotSelection(overrides);
  const diff = botSelectionDiff(latestBotSettings, want);
  if (!Object.keys(diff).length) { botFollowError = null; return true; }
  const key = JSON.stringify(want);
  if (!retry && botFollowError?.key === key) return false;
  const running = Boolean(latestBotSettings.is_running);
  const before = latestBotSettings;
  const run = (async () => {
    try {
      const res = await fetch('/api/bot/follow', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(diff)});
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Bot ayarları seçime göre güncellenemedi.');
      botFollowError = null;
      latestBotSettings = data && typeof data === 'object' && data.symbol ? data : {...before, ...diff, ...(diff.symbol ? {symbols: [diff.symbol], symbol_status: {}} : {})};
      const now = latestBotSettings;
      const label = `${now.symbol} ${botTfCode(now.timeframe_minutes)} · ${now.fast_ma_type || 'HMA'}(${now.hma_period}) × ${now.second_ma_type}(${now.second_ma_period})`;
      if (running) {
        const kept = diff.symbol ? `${before.symbol} pozisyonları açık kalır` : 'açık pozisyonlar olduğu gibi kalır';
        showToast(`🎯 Bot seçimi izledi: ${label}. ${kept}; yeni kesişim bekleniyor.`, 'success');
      } else if (announce) showToast(`🎯 Bot seçimi izliyor: ${label}`, 'info');
      if (typeof renderBotSymbolStatus === 'function') renderBotSymbolStatus(latestBotSettings);
      return true;
    } catch (err) {
      botFollowError = {key, message: err.message};
      if (announce || running) showToast(`❌ ${err.message}`, 'error');
      return false;
    }
  })();
  botFollowSyncing = run;
  try { return await run; } finally { if (botFollowSyncing === run) botFollowSyncing = null; }
}

// Older call sites: symbol and timeframe changes go through the same follow path.
function syncBotTimeframe(tf, opts = {}) {
  const num = Number(tf ?? window.MT5Chart?.getTimeframe?.());
  if (!BOT_TIMEFRAMES.includes(num)) return Promise.resolve(false);
  return syncBotToSelection({timeframe_minutes: num}, opts);
}
function syncBotSymbol(sym, opts = {}) {
  sym = String(sym || '').trim();
  if (!sym) return Promise.resolve(false);
  return syncBotToSelection({symbol: sym}, opts);
}

function onScannerTimeframeChange(tf) {
  const num = Number(tf);
  setChartTimeframe(num);
  if (typeof scheduleCandleCloseScan === 'function') scheduleCandleCloseScan();
}

// Initialize / Reload TradingView Widget
function initTradingView(symbol) {
  const container = document.getElementById("tradingview-container");
  if (!container) return;
  container.innerHTML = ""; // Clear old container
  clearInterval(chartSymbolTimer);
  tvWidget = null;
  const sourceNotice = document.getElementById('chart-source-notice');
  if (sourceNotice && TV_SYMBOLS[symbol]) { sourceNotice.hidden = true; sourceNotice.textContent = ''; }

  if (window.MT5Chart) {
    window.MT5Chart.dispose();
    const engine = document.getElementById('chart-engine');
    if (engine) engine.value = window.MT5Chart.mode();
    const link = document.getElementById('chart-tradingview-link');
    if (link) link.hidden = window.MT5Chart.mode() !== 'tradingview';
    if (window.MT5Chart.mode() === 'mt5') {
      window.MT5Chart.mount(symbol);
      return;
    }
  }

  const tvSymbol = TV_SYMBOLS[symbol];
  if (!tvSymbol) {
    if (window.MT5Chart) {
      if (window.MT5Chart.mode() !== 'mt5') window.MT5Chart.setMode('mt5');
      else window.MT5Chart.mount(symbol);
    }
    const notice = document.getElementById('chart-source-notice');
    if (notice) {
      notice.hidden = false;
      notice.textContent = window.MT5I18n?.language?.() === 'en'
        ? 'No verified TradingView mapping for this broker instrument. Showing the MT5 broker chart.'
        : 'Bu broker ürünü için doğrulanmış TradingView eşlemesi yok. MT5 broker grafiği gösteriliyor.';
    }
    return;
  }
  const notice = document.getElementById('chart-source-notice');
  if (notice) { notice.hidden = true; notice.textContent = ''; }
  updateTradingViewLink(tvSymbol);

  if (window.TradingView) {
    tvWidget = new TradingView.widget({
      "autosize": true,
      "symbol": tvSymbol,
      "tvwidgetsymbol": tvSymbol,
      "interval": "15",
      "timezone": "Etc/UTC",
      "theme": "dark",
      "style": "1",
      "locale": window.MT5I18n?.language?.() || "tr",
      "toolbar_bg": "#14181f",
      "enable_publishing": false,
      "hide_side_toolbar": false,
      "allow_symbol_change": true,
      "container_id": "tradingview-container"
    });
    const widget = tvWidget;
    window.HMAChartWheel?.guardFrame(container);
    widget.ready(() => {
      if (widget !== tvWidget) return;
      const frame = document.getElementById(widget.id);
      if (!frame) return;
      const origin = new URL(frame.src).origin;
      frame.contentWindow.postMessage(JSON.stringify({provider:'TradingView', type:'post', name:'quoteSubscribe'}), origin);
      const inspectSymbol = () => {
        chartSymbolRequest++;
        frame.contentWindow.postMessage(JSON.stringify({provider:'TradingView', type:'get', name:'symbolInfo',
          id:chartSymbolRequest, client_id:tvWidget.id, data:{}}), origin);
      };
      inspectSymbol();
      chartSymbolTimer = setInterval(inspectSymbol, 1000);
    });
  }
}

function brokerSymbolForChart(name, exchange, type) {
  name = name.trim().split(':').pop().toUpperCase();
  exchange = exchange.trim().toUpperCase();
  type = typeof type === 'string' ? type.trim().toLowerCase() : '';
  if (['CRYPTOCAP', 'ECONOMICS', 'INDEX'].includes(exchange)) return null;
  // TradingView's symbolInfo calls crypto instruments "bitcoin" as well as
  // "crypto". Quote updates omit type; both messages must resolve identically.
  if (type && !['forex', 'crypto', 'bitcoin', 'cfd', 'index', 'commodity', 'spot'].includes(type)) return null;
  const aliases = {BTCUSDT:'BTCUSD', BTCUSD:'BTCUSD', DJI:'US30'};
  const candidate = aliases[name] || name;
  return Object.hasOwn(TV_SYMBOLS, candidate) && (!window.MT5Markets || !!window.MT5Markets.getSymbol(candidate)) ? candidate : null;
}

function applyChartSymbol(name, exchange, type) {
  name = name.trim().split(':').pop().toUpperCase();
  exchange = exchange.trim().toUpperCase();
  updateTradingViewLink(`${exchange}:${name}`);
  // Replace a market-cap measurement with the executable Bitcoin price chart.
  if (exchange === 'CRYPTOCAP' && ['BITCOIN', 'BTC'].includes(name)
      && (!window.MT5Markets || !!window.MT5Markets.getSymbol('BTCUSD'))) {
    switchSymbol('BTCUSD');
    setOrderNotice('BTCUSD', 'Bitcoin işlem grafiği seçildi',
      'Piyasa değeri yerine BTC fiyat grafiğine geçildi. Emirler MT5 BTCUSD sembolüne gönderilir; henüz emir gönderilmedi.', 'neutral');
    return;
  }
  const brokerSymbol = brokerSymbolForChart(name, exchange, type);
  if (brokerSymbol) {
    if (brokerSymbol !== currentSymbol || chartSelectionBlocked) switchSymbol(brokerSymbol, false);
    return;
  }
  chartSelectionBlocked = true;
  orderSymbolSource = 'chart';
  updateOrderButtons();
  document.getElementById('current-symbol-title').textContent = `${exchange}:${name} · MT5 eşleşmesi yok`;
  document.getElementById('order-symbol-tag').textContent = `${name} · İşlem kapalı`;
  clearSymbolPrices();
  const preview = document.getElementById('trade-preview');
  if (preview) preview.textContent = 'Grafik ürünü MT5 sembolüyle eşleşmiyor; emir girişi kapalı.';
  renderOrderNotice();
}

function updateTradingViewLink(symbol) {
  const link = document.getElementById('chart-tradingview-link');
  if (link) link.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=15`;
}

window.addEventListener('message', event => {
  const frame = tvWidget && document.getElementById(tvWidget.id);
  if (!frame || event.source !== frame.contentWindow || event.origin !== new URL(frame.src).origin) return;
  let message;
  try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; } catch (_) { return; }
  if (message?.provider !== 'TradingView') return;
  if (message.type === 'on' && message.name === 'symbolInfo' && (message.id === chartSymbolRequest || chartSymbolChecks.has(message.id))) {
    const data = message.data;
    const valid = typeof data?.name === 'string' && typeof data.exchange === 'string';
    if (valid) applyChartSymbol(data.name, data.exchange, data.type);
    const check = chartSymbolChecks.get(message.id);
    if (check) { chartSymbolChecks.delete(message.id); check(valid && !chartSelectionBlocked); }
  } else if (message.type === 'post' && message.name === 'quoteUpdate') {
    const original = message.data?.original_name;
    if (typeof original !== 'string' || !original.includes(':')) return;
    const [exchange, name] = original.split(':');
    applyChartSymbol(name, exchange);
  }
});

async function verifyChartOrderSymbol(side) {
  if (window.MT5Markets?.isSelectionVerified(currentSymbol, side) === false) return false;
  if (chartSelectionBlocked) return false;
  // Explicit MT5 selections remain usable if the third-party chart is offline.
  // Broker identity, quote freshness and risk checks still run server-side.
  if (orderSymbolSource === 'panel') return true;
  if (!tvWidget) return !chartSelectionBlocked;
  const symbol = currentSymbol, widget = tvWidget;
  const frame = document.getElementById(widget.id);
  if (!frame) return false;
  const id = ++chartSymbolRequest;
  const verified = await new Promise(resolve => {
    const timer = setTimeout(() => { chartSymbolChecks.delete(id); resolve(false); }, 2500);
    chartSymbolChecks.set(id, ok => { clearTimeout(timer); resolve(ok); });
    frame.contentWindow.postMessage(JSON.stringify({provider:'TradingView', type:'get', name:'symbolInfo',
      id, client_id:widget.id, data:{}}), new URL(frame.src).origin);
  });
  return verified && tvWidget === widget && currentSymbol === symbol && !chartSelectionBlocked
    && window.MT5Markets?.isSelectionVerified(currentSymbol, side) !== false;
}

function clearSymbolPrices() {
  executableQuote = null;
  if (typeof priceRenderRevision !== 'undefined') priceRenderRevision++;
  if (typeof renderStopGuidance === 'function') renderStopGuidance(null, tradePreviewIdentity());
  if (typeof displayedTickTime !== 'undefined') displayedTickTime = 0;
  if (typeof displayedQuoteStatus !== 'undefined') displayedQuoteStatus = null;
  if (typeof updateQuoteStatus === 'function') updateQuoteStatus();
  for (const id of ['header-bid', 'header-ask', 'header-spread', 'btn-bid-price', 'btn-ask-price', 'ticket-bid-price', 'ticket-ask-price']) {
    const node = document.getElementById(id);
    if (node) node.textContent = '—';
  }
  updateOrderButtons();
}

function quoteOrderIssue() {
  const en = window.MT5I18n?.language?.() === 'en';
  if(typeof nativeTerminalOpen!=='undefined'&&nativeTerminalOpen)
    return en?'Place new orders in the original MT5 terminal while it is open.':'Orijinal terminal açıkken yeni emirleri MT5 ekranından verin.';
  const quote = executableQuote, status = quote?.status;
  const elapsed = quote ? (Date.now() - quote.receivedAt) / 1000 : NaN;
  if (!quote || quote.symbol !== currentSymbol || !status || !Number.isFinite(elapsed) || elapsed < 0 || elapsed >= 5
      || (typeof quoteDiagnosticFailed !== 'undefined' && quoteDiagnosticFailed))
    return en ? 'Waiting for a verified broker quote.' : 'Doğrulanmış broker fiyatı bekleniyor.';
  if (status.broker_connected !== true)
    return en ? 'Waiting for the MT5 broker connection.' : 'MT5 broker bağlantısı bekleniyor.';
  // MT5 rule: the last tick is the current price whatever its age; only a missing/invalid price blocks.
  if (status.state === 'missing' || status.state === 'invalid') {
    return status.message || (en ? 'Waiting for a valid broker price.' : 'Geçerli broker fiyatı bekleniyor.');
  }
  if (!Number.isFinite(quote.bid) || quote.bid <= 0 || !Number.isFinite(quote.ask) || quote.ask < quote.bid)
    return en ? 'Waiting for a valid broker price.' : 'Geçerli broker fiyatı bekleniyor.';
  return '';
}

function updateOrderButtons() {
  updateLotRules();
  const quoteIssue = quoteOrderIssue();
  const messages = [];
  for (const id of ['order-buy-btn', 'order-sell-btn']) {
    const button = document.getElementById(id);
    if (button) {
      const side = id === 'order-buy-btn' ? 'BUY' : 'SELL';
      const catalogueBlocked = window.MT5Markets?.isSelectionVerified(currentSymbol, side) === false;
      const issue = quoteIssue;
      button.disabled = chartSelectionBlocked || catalogueBlocked || orderPending || orderPreflightPending || accountSwitching || (accountVerificationKnown && !currentAccountIdentity)
        || !!issue
        || window.MT5Permissions?.can?.('submitOrder') === false;
      button.title = chartSelectionBlocked ? 'Önce bir MT5 işlem sembolü seçin.'
        : catalogueBlocked ? window.MT5Markets.selectionIssue(currentSymbol, side) : issue || `${currentSymbol} · ${side === 'BUY' ? 'Alış' : 'Satış'}`;
      if (issue && !messages.includes(issue)) messages.push(issue);
    }
  }
  const status = document.getElementById('order-readiness-status');
  if (status) { status.textContent = messages.join(' · '); status.hidden = messages.length === 0; }
}

function marketCatalogueChanged() {
  updateOrderButtons();
  clearSymbolPrices();
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
  if (window.MT5Markets?.getSymbol(currentSymbol)) fetchPrice(true);
  if(document.getElementById('market-depth-panel')?.open&&typeof refreshMarketDepth==='function')refreshMarketDepth();
}

// Check if currently in fullscreen
function isChartFullscreen() {
  const card = document.getElementById("chart-card");
  if (!card) return false;
  return !!(document.fullscreenElement === card || document.webkitFullscreenElement === card || card.classList.contains("chart-fullscreen-fallback"));
}

// Toggle TradingView Chart Fullscreen Mode
function toggleChartFullscreen() {
  const card = document.getElementById("chart-card");
  if (!card) return;

  if (!isChartFullscreen()) {
    // Enter Fullscreen on chart-card directly
    if (card.requestFullscreen) {
      card.requestFullscreen().catch(() => {
        enterFallbackFullscreen(card);
      });
    } else if (card.webkitRequestFullscreen) {
      card.webkitRequestFullscreen();
    } else {
      enterFallbackFullscreen(card);
    }
  } else {
    // Exit Fullscreen
    if (document.fullscreenElement === card || document.webkitFullscreenElement === card) {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      }
    } else {
      exitFallbackFullscreen(card);
    }
  }
}

function enterFallbackFullscreen(card) {
  card.classList.add("chart-fullscreen-fallback");
  document.body.classList.add("overflow-hidden");
  updateFullscreenUI(true);
  setTimeout(() => window.dispatchEvent(new Event("resize")), 150);
}

function exitFallbackFullscreen(card) {
  card.classList.remove("chart-fullscreen-fallback");
  document.body.classList.remove("overflow-hidden");
  updateFullscreenUI(false);
  setTimeout(() => window.dispatchEvent(new Event("resize")), 150);
}

function updateFullscreenUI(isFs) {
  const fsBtn = document.getElementById("chart-fs-btn");
  const fsIcon = document.getElementById("chart-fs-icon");
  const fsText = document.getElementById("chart-fs-text");

  if (isFs) {
    if (fsIcon) fsIcon.className = "ph-bold ph-arrows-in-simple";
    if (fsText) fsText.innerText = "Küçült (ESC)";
    if (fsBtn) {
      fsBtn.className = "cx-btn is-active";
    }
  } else {
    if (fsIcon) fsIcon.className = "ph-bold ph-arrows-out-simple";
    if (fsText) fsText.innerText = "Tam ekran";
    if (fsBtn) {
      fsBtn.className = "cx-btn";
    }
  }
}

// Native HTML5 Fullscreen change listener (handles browser ESC key, F11, etc.)
function onFullscreenChange() {
  const card = document.getElementById("chart-card");
  const isFs = !!(document.fullscreenElement === card || document.webkitFullscreenElement === card);
  if (!isFs && card) {
    card.classList.remove("chart-fullscreen-fallback");
    document.body.classList.remove("overflow-hidden");
  }
  updateFullscreenUI(isFs);
  setTimeout(() => window.dispatchEvent(new Event("resize")), 150);
}

document.addEventListener("fullscreenchange", onFullscreenChange);
document.addEventListener("webkitfullscreenchange", onFullscreenChange);

// Fallback ESC key listener
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" || e.keyCode === 27) {
    const card = document.getElementById("chart-card");
    if (card && card.classList.contains("chart-fullscreen-fallback")) {
      exitFallbackFullscreen(card);
    }
  }
});

// Chart Resizer Handle Logic (Drag to stretch or shrink height)
function initChartResizer() {
  const card = document.getElementById("chart-card");
  const resizer = document.getElementById("chart-resizer");
  if (!card || !resizer) return;

  // Restore saved height from localStorage if available
  const savedHeight = localStorage.getItem("hma_chart_height");
  if (savedHeight) {
    const h = parseInt(savedHeight, 10);
    if (!isNaN(h) && h >= 250 && h <= 1400) {
      card.style.height = `${h}px`;
    }
  }

  let startY = 0;
  let startHeight = 0;
  let isDragging = false;

  function onPointerDown(e) {
    if (e.button !== undefined && e.button !== 0) return;
    if (isChartFullscreen()) return;

    isDragging = true;
    startY = e.clientY;
    startHeight = card.getBoundingClientRect().height;

    try {
      resizer.setPointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.add("resizing-chart");
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!isDragging) return;
    const dy = e.clientY - startY;
    const minH = 260;
    const maxH = Math.max(minH, window.innerHeight * 0.9);
    const newHeight = Math.max(minH, Math.min(maxH, startHeight + dy));

    card.style.height = `${Math.round(newHeight)}px`;
  }

  function onPointerUp(e) {
    if (!isDragging) return;
    isDragging = false;

    try {
      resizer.releasePointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.remove("resizing-chart");
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);

    const currentH = Math.round(card.getBoundingClientRect().height);
    localStorage.setItem("hma_chart_height", currentH);

    window.dispatchEvent(new Event("resize"));
  }

  function onDoubleClick() {
    localStorage.removeItem("hma_chart_height");
    card.style.height = "";
    window.dispatchEvent(new Event("resize"));
  }

  resizer.addEventListener("pointerdown", onPointerDown);
  resizer.addEventListener("dblclick", onDoubleClick);
}

// Positions Card Resizer Handle Logic (Drag to stretch or shrink height)
function initPositionsResizer() {
  const card = document.getElementById("positions-card");
  const resizer = document.getElementById("positions-resizer");
  if (!card || !resizer) return;

  // Restore saved height from localStorage if available
  const savedHeight = localStorage.getItem("hma_positions_height");
  if (savedHeight) {
    const h = parseInt(savedHeight, 10);
    if (!isNaN(h) && h >= 200 && h <= 2000) {
      card.style.height = `${h}px`;
    }
  }

  let startY = 0;
  let startHeight = 0;
  let isDragging = false;

  function onPointerDown(e) {
    if (e.button !== undefined && e.button !== 0) return;

    isDragging = true;
    startY = e.clientY;
    startHeight = card.getBoundingClientRect().height;

    try {
      resizer.setPointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.add("resizing-positions");
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!isDragging) return;
    const dy = e.clientY - startY;
    const minH = 220;
    const maxH = Math.max(minH, window.innerHeight * 2);
    const newHeight = Math.max(minH, Math.min(maxH, startHeight + dy));

    card.style.height = `${Math.round(newHeight)}px`;
  }

  function onPointerUp(e) {
    if (!isDragging) return;
    isDragging = false;

    try {
      resizer.releasePointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.remove("resizing-positions");
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);

    const currentH = Math.round(card.getBoundingClientRect().height);
    localStorage.setItem("hma_positions_height", currentH);
  }

  function onDoubleClick() {
    localStorage.removeItem("hma_positions_height");
    card.style.height = "";
  }

  resizer.addEventListener("pointerdown", onPointerDown);
  resizer.addEventListener("dblclick", onDoubleClick);
}

// Positions Card Top Resizer Handle Logic (Drag to expand/shrink boundary with Chart)
function initPositionsTopResizer() {
  const topResizer = document.getElementById("positions-top-resizer");
  const chartCard = document.getElementById("chart-card");
  const posCard = document.getElementById("positions-card");
  if (!topResizer || !chartCard || !posCard) return;

  let startY = 0;
  let startChartH = 0;
  let startPosH = 0;
  let isDragging = false;

  function onPointerDown(e) {
    if (e.button !== undefined && e.button !== 0) return;

    isDragging = true;
    startY = e.clientY;
    startChartH = chartCard.getBoundingClientRect().height;
    startPosH = posCard.getBoundingClientRect().height;

    try {
      topResizer.setPointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.add("resizing-positions");
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!isDragging) return;
    const dy = e.clientY - startY;

    // Dragging UP (dy < 0): chart shrinks, positions grows upwards
    // Dragging DOWN (dy > 0): chart grows, positions shrinks downwards
    const minChartH = 220;
    const maxChartH = 1000;
    const minPosH = 200;
    const maxPosH = 1200;

    let newChartH = startChartH + dy;
    let newPosH = startPosH - dy;

    if (newChartH < minChartH) {
      newChartH = minChartH;
      newPosH = startPosH + (startChartH - minChartH);
    } else if (newChartH > maxChartH) {
      newChartH = maxChartH;
      newPosH = startPosH - (maxChartH - startChartH);
    }

    if (newPosH < minPosH) {
      newPosH = minPosH;
      newChartH = startChartH + (startPosH - minPosH);
    } else if (newPosH > maxPosH) {
      newPosH = maxPosH;
      newChartH = startChartH - (maxPosH - startPosH);
    }

    chartCard.style.height = `${Math.round(newChartH)}px`;
    posCard.style.height = `${Math.round(newPosH)}px`;
  }

  function onPointerUp(e) {
    if (!isDragging) return;
    isDragging = false;

    try {
      topResizer.releasePointerCapture(e.pointerId);
    } catch (err) {}

    document.body.classList.remove("resizing-positions");
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);

    localStorage.setItem("hma_chart_height", Math.round(chartCard.getBoundingClientRect().height));
    localStorage.setItem("hma_positions_height", Math.round(posCard.getBoundingClientRect().height));

    window.dispatchEvent(new Event("resize"));
  }

  function onDoubleClick() {
    localStorage.removeItem("hma_chart_height");
    localStorage.removeItem("hma_positions_height");
    chartCard.style.height = "";
    posCard.style.height = "";
    window.dispatchEvent(new Event("resize"));
  }

  topResizer.addEventListener("pointerdown", onPointerDown);
  topResizer.addEventListener("dblclick", onDoubleClick);
}




// Switch Active Symbol
function switchSymbol(symbol, reloadChart = true) {
  if (typeof scheduleLossWarning === 'function' && typeof setTimeout === 'function') setTimeout(scheduleLossWarning, 0);
  chartSelectionBlocked = false;
  orderSymbolSource = reloadChart ? 'panel' : 'chart';
  if (symbol !== currentSymbol) {
    ticketProtectionEdited=false;nettingProtectionSeed=null;
    if (document.getElementById('order-stop-mode')?.value === 'price') changeOrderStopMode();
    if (document.getElementById('pending-stop-mode')?.value === 'price') changeOrderStopMode(true);
    const entry = document.getElementById('pending-price');
    if (entry) entry.value = '';
  }
  currentSymbol = symbol;
  saveLastSymbol(symbol);
  if (typeof syncBotSymbol === 'function' && latestBotSettings) syncBotSymbol(symbol, {announce: false});
  if (typeof refreshMarketOpen === 'function') refreshMarketOpen(symbol);
  if (typeof renderScannerSymbolButton === 'function') renderScannerSymbolButton(symbol);
  if(document.getElementById('market-depth-panel')?.open&&typeof refreshMarketDepth==='function')refreshMarketDepth();
  updateLotRules();
  window.MT5Markets?.setActive(symbol);
  updateOrderButtons();
  clearSymbolPrices();
  if (typeof startLiveFeed === "function") startLiveFeed();
  renderOrderNotice();
  const curTf = window.MT5Chart?.getTimeframe?.() || 15;
  const curTfCode = {1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'}[curTf] || `M${curTf}`;
  const curTfText = {1:'1 dk',5:'5 dk',15:'15 dk',30:'30 dk',60:'1 sa',240:'4 sa',1440:'1 gün'}[curTf] || `${curTf}m`;
  const symbolTitleNode = document.getElementById("current-symbol-title");
  if (symbolTitleNode) symbolTitleNode.innerText = `${symbol} • ${curTfText} (${curTfCode})`;
  updateChartTimeframeButtons(curTf);
  document.getElementById("order-symbol-tag").innerText = symbol;

  if (reloadChart) initTradingView(symbol);
  fetchPrice();
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
  if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
}

let currentPositionTab = "open";

let isFetchingAccount = false;
let isFetchingPositions = false;
let isFetchingHistory = false;
let isFetchingPrice = false;
// Ignore responses captured before an account switch or a newer live snapshot.
let accountSessionGeneration = 0;
let accountRenderRevision = 0;
let positionsRenderRevision = 0;
let priceRenderRevision = 0;
let accountSwitching = false;

// Polling loop for real-time updates
function startPolling() {
  fetchAccount();
  refreshTradingStatus();
  fetchPositions();
  fetchHistory();
  fetchPrice();
  fetchBotStatus();

  setInterval(fetchAccount, 1500);
  setInterval(fetchPositions, 1500);
  setInterval(fetchPrice, 800);
  setInterval(fetchBotStatus, 3000);
  refreshMarketOpen(currentSymbol);
  setInterval(() => refreshMarketOpen(currentSymbol), 60000);
  setInterval(() => { if (marketOpenInfo.get(currentSymbol)?.data?.closed) renderOrderNotice(); }, 30000);   // countdown
  setInterval(refreshTradingStatus, 5000);
  if (typeof refreshSignalScanner === 'function') {
    refreshSignalScanner(false);
    scheduleCandleCloseScan();
    setInterval(() => { refreshSignalScanner(false); scheduleCandleCloseScan(); }, 10000);
  }
  if (typeof fetchBotLiveSignal === 'function') {
    fetchBotLiveSignal();
    setInterval(fetchBotLiveSignal, 5000);
  }
  setInterval(() => {
    if (currentPositionTab === "closed") fetchHistory();
    else if (currentPositionTab === "reports") fetchReports();
  }, 5000);
}


// Switch between Open, Closed Positions, Reports, and AI tabs
function switchPositionTab(tab) {
  currentPositionTab = tab;
  const hxCard = document.getElementById('positions-card');
  if (tab !== 'open' && hxCard?.dataset) hxCard.dataset.pendingMode = '';
  setTimeout(() => window.hxSyncTabs?.(), 0);
  const btnOpen = document.getElementById("tab-btn-open");
  const btnClosed = document.getElementById("tab-btn-closed");
  const btnReports = document.getElementById("tab-btn-reports");
  const btnAI = document.getElementById("tab-btn-ai");
  const openActions = document.getElementById("open-actions-bar");
  const closedSummary = document.getElementById("closed-summary-bar");
  const reportsActions = document.getElementById("reports-actions-bar");
  const openContainer = document.getElementById("open-positions-container");
  const closedContainer = document.getElementById("closed-positions-container");
  const reportsContainer = document.getElementById("reports-container");
  const aiContainer = document.getElementById("ai-container");

  const defaultTabClass = "px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 text-gray-400 hover:text-gray-200";
  if (btnOpen) btnOpen.className = defaultTabClass;
  if (btnClosed) btnClosed.className = defaultTabClass;
  if (btnReports) btnReports.className = defaultTabClass;
  if (btnAI) btnAI.className = defaultTabClass;

  if (openActions) openActions.classList.add("hidden");
  if (closedSummary) { closedSummary.classList.add("hidden"); closedSummary.classList.remove("flex"); }
  if (reportsActions) { reportsActions.classList.add("hidden"); reportsActions.classList.remove("flex"); }
  if (openContainer) openContainer.classList.add("hidden");
  if (closedContainer) closedContainer.classList.add("hidden");
  if (reportsContainer) { reportsContainer.classList.add("hidden"); reportsContainer.classList.remove("flex"); }
  if (aiContainer) { aiContainer.classList.add("hidden"); aiContainer.classList.remove("flex"); }

  if (tab === "open") {
    if (btnOpen) btnOpen.className = "px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-emerald-500/20 text-emerald-400 shadow-sm";
    if (openActions) openActions.classList.remove("hidden");
    if (openContainer) openContainer.classList.remove("hidden");
    fetchPositions();
  } else if (tab === "closed") {
    if (btnClosed) btnClosed.className = "px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-cyan-500/20 text-cyan-400 shadow-sm";
    if (closedSummary) {
      closedSummary.classList.remove("hidden");
      closedSummary.classList.add("flex");
    }
    if (closedContainer) closedContainer.classList.remove("hidden");
    fetchHistory();
  } else if (tab === "reports") {
    if (btnReports) btnReports.className = "px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-indigo-500/20 text-indigo-400 shadow-sm";
    if (reportsActions) {
      reportsActions.classList.remove("hidden");
      reportsActions.classList.add("flex");
    }
    if (reportsContainer) {
      reportsContainer.classList.remove("hidden");
      reportsContainer.classList.add("flex");
    }
    fetchReports();
  } else if (tab === "ai") {
    if (window.MainView) window.MainView.set("mevo"); else window.location.href = "/ai";
    return;
  }
}

function showAccountConnectionError(message) {
  currentAccountType = null;
  currentAccountIdentity = null;
  accountVerificationKnown = true;
  window.MT5Markets?.setAccount(null, accountSessionGeneration);
  updateOrderButtons();
  const indicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  if (indicator) indicator.className = "w-2 h-2 rounded-full bg-amber-400";
  if (statusText) {
    statusText.innerText = message;
    statusText.className = "text-amber-400";
  }
  for (const id of ['acc-balance','acc-equity','acc-margin-free','acc-profit','acc-margin','acc-margin-level']) {
    const node = document.getElementById(id);
    if (node) node.innerText = '—';
  }
  const typeBadge = document.getElementById('acc-type-badge');
  if (typeBadge) typeBadge.hidden = true;
  const preview = document.getElementById('trade-preview');
  if (preview) preview.textContent = 'Hesap doğrulanamadı; risk tahmini kullanılamaz.';
  if (typeof markTradePreview === 'function') markTradePreview('unavailable', '');
  if (typeof renderTradePolicy === 'function') renderTradePolicy(null);
}

// Fetch Account Info
let accountCurrency = "USD";
let currentAccountType = null;
let currentAccountIdentity = null;
let currentAccountMarginMode = null;
let ticketProtectionEdited = false;
let nettingProtectionSeed = null;
let lastObservedAccountIdentity = null;
let accountVerificationKnown = false;
async function tradeRequest(endpoint, payload, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload), signal:controller.signal});
    const data = await response.json();
    return {response,data};
  } finally { clearTimeout(timer); }
}
function tradingAccountPayload() {
  return currentAccountIdentity ? {expected_account:[...currentAccountIdentity]} : {};
}
function renderAccountData(data) {
    if (accountSwitching || !data || typeof data !== 'object') return;
    accountRenderRevision++;
    accountCurrency = data.currency || accountCurrency;
    currentAccountType = data.connected && !data.account_mismatch && ['DEMO','REAL'].includes(data.account_type) ? data.account_type : null;
    const identity = currentAccountType && Number.isSafeInteger(data.login) && typeof data.server === 'string'
      ? [data.login, data.server, currentAccountType] : null;
    const identityChanged = JSON.stringify(identity) !== JSON.stringify(currentAccountIdentity);
    if (identity && lastObservedAccountIdentity && JSON.stringify(identity) !== JSON.stringify(lastObservedAccountIdentity)) {
      accountSessionGeneration++;
      window.HMAPeriodReport?.reset?.();
    }
    currentAccountIdentity = identity;
    currentAccountMarginMode = identity && [0,1,2].includes(data.margin_mode) ? data.margin_mode : null;
    if(identityChanged){ticketProtectionEdited=false;nettingProtectionSeed=null;}
    if (identityChanged && identity) restoreOrderTicketPreferences();
    if (identity) lastObservedAccountIdentity = identity;
    accountVerificationKnown = true;
    window.MT5Markets?.setAccount(identity, accountSessionGeneration);
    updateOrderButtons();
    if (identityChanged && typeof scheduleTradePreview === 'function') scheduleTradePreview();
    const indicator = document.getElementById("status-indicator");
    const statusText = document.getElementById("status-text");
    const lastSyncText = document.getElementById("last-sync-text");
    const uptimeText = document.getElementById("uptime-text");
    const uptimeBadge = document.getElementById("uptime-badge");

    const nowStr = new Date().toLocaleTimeString(window.MT5I18n?.language?.() === "en" ? "en-US" : "tr-TR");

    if (data.connected && data.account_mismatch) {
      indicator.className = "w-2 h-2 rounded-full bg-rose-400 animate-pulse";
      statusText.innerText = `Hesap uyuşmuyor (#${data.login}); işlem engellendi`;
      statusText.className = "text-rose-400 font-semibold";
      if (lastSyncText) lastSyncText.innerText = `Son Veri: ${data.server_time || nowStr}`;
      if (uptimeText) {
        uptimeText.innerText = "Hesap doğrulanmadı";
        uptimeBadge.className = "text-xs font-mono px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 flex items-center gap-1";
      }
    } else if (data.connected && !currentAccountType) {
      showAccountConnectionError('Hesap türü doğrulanamadı; işlem engellendi');
    } else if (data.connected) {
      const real = data.account_type === "REAL";
      indicator.className = "w-2 h-2 rounded-full bg-emerald-400";
      statusText.innerText = `Bağlı · ${data.server || 'MT5'} · #${data.login}`;
      statusText.className = "text-gray-300";
      if (lastSyncText) lastSyncText.innerText = `Son veri ${data.server_time || nowStr}`;
      if (uptimeText) {
        uptimeText.innerText = `Bağlı: ${data.connected_since || "Aktif"}`;
        uptimeBadge.className = "text-xs font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1";
      }
    } else {
      indicator.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
      statusText.innerText = "MT5 Bekleniyor / Çevrimdışı";
      statusText.className = "text-amber-400";
      if (lastSyncText) lastSyncText.innerText = `Son Veri: Bekleniyor (${nowStr})`;
      if (uptimeText) {
        uptimeText.innerText = "Bağlantı: Yok";
        uptimeBadge.className = "text-xs font-mono px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1";
      }
    }

    const verifiedMoney = value => !!currentAccountType && Number.isFinite(value)
      ? `${accountCurrency} ${formatMoney(value)}` : '—';
    document.getElementById("acc-balance").innerText = verifiedMoney(data.balance);
    document.getElementById("acc-equity").innerText = verifiedMoney(data.equity);
    document.getElementById("acc-margin-free").innerText = verifiedMoney(data.margin_free);
    renderAccountExtras(data);

    const profitEl = document.getElementById("acc-profit");
    const profitVal = data.profit || 0;
    profitEl.innerText = currentAccountType && Number.isFinite(data.profit)
      ? `${profitVal >= 0 ? "+" : ""}${accountCurrency} ${formatMoney(profitVal)}` : '—';
    if (profitVal > 0) {
      profitEl.className = "font-bold text-emerald-400 tracking-wide";
    } else if (profitVal < 0) {
      profitEl.className = "font-bold text-rose-400 tracking-wide";
    } else {
      profitEl.className = "font-bold text-gray-300 tracking-wide";
    }
}

// Header extras of the terminal design: account type badge, used margin and margin level (MT5 values).
function renderAccountExtras(data) {
  const badge = document.getElementById('acc-type-badge');
  const verified = !!currentAccountType && data && data.connected && !data.account_mismatch;
  if (badge) {
    badge.hidden = !verified;
    if (verified) {
      const en = window.MT5I18n?.language?.() === 'en';
      if (badge.dataset) badge.dataset.type = currentAccountType;
      badge.textContent = currentAccountType === 'REAL' ? (en ? 'REAL ACCOUNT' : 'GERÇEK HESAP') : (en ? 'DEMO ACCOUNT' : 'DEMO HESAP');
    }
  }
  const margin = document.getElementById('acc-margin');
  if (margin) margin.innerText = verified && Number.isFinite(data.margin) ? `${accountCurrency} ${formatMoney(data.margin)}` : '—';
  const level = document.getElementById('acc-margin-level');
  if (level) {
    // MT5 shows the level only while margin is in use.
    const value = verified && Number.isFinite(data.margin_level) && data.margin > 0 ? data.margin_level : null;
    level.innerText = value == null ? '—' : `%${value.toLocaleString('tr-TR', {maximumFractionDigits: 2})}`;
    if (level.dataset) { if (value != null && value < 200) level.dataset.level = 'low'; else delete level.dataset.level; }
  }
}

async function fetchAccount(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingAccount) return;
  isFetchingAccount = true;
  const generation = accountSessionGeneration, revision = accountRenderRevision;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch("/api/account", { signal: controller.signal });
    if (!res.ok) throw new Error(`Hesap bilgisi alınamadı (HTTP ${res.status})`);
    const data = await res.json();

    if (generation === accountSessionGeneration && revision === accountRenderRevision) renderAccountData(data);
  } catch (err) {
    if (generation !== accountSessionGeneration || revision !== accountRenderRevision) return;
    showAccountConnectionError(
      err.name === "AbortError" ? "Sunucu yanıtı gecikti" : "Sunucuya ulaşılamıyor"
    );
    if (err.name !== "AbortError") {
      console.error("fetchAccount error:", err);
    }
  } finally {
    clearTimeout(timer);
    isFetchingAccount = false;
  }
}

// Fetch Open Positions
const executionPositions = new Map();
let executionPositionTicket = null;

function executionPoints(value, signed = false) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const text = Number(value).toLocaleString('tr-TR', {maximumFractionDigits:3});
  return `${signed && value > 0 ? '+' : ''}${text} puan`;
}

function slippageText(value) {
  if (value == null) return 'Ölçülemiyor';
  return `${executionPoints(value, true)} · ${value > 0 ? 'Aleyhe' : value < 0 ? 'Lehe' : 'Kayma yok'}`;
}

function showPositionExecution(ticket) {
  executionPositionTicket = ticket;
  const modal = document.getElementById('position-execution-modal');
  if (!modal.open) modal.showModal();
  updatePositionExecution();
}

function updatePositionExecution() {
  const modal = document.getElementById('position-execution-modal');
  if (!modal?.open) return;
  const position = executionPositions.get(executionPositionTicket);
  const content = document.getElementById('position-execution-content');
  if (!position) {
    content.textContent = 'Pozisyon artık açık listede bulunmuyor. Kapanış detaylarını geçmiş sekmesinden görebilirsiniz.';
    return;
  }
  document.getElementById('position-execution-title').textContent = `${position.symbol} ${position.type} · #${position.ticket} · Spread / Slippage`;
  content.innerHTML = renderPositionExecution(position);
}

// "2026-10-23 14:05:09" (MT5 server clock, already formatted by the backend) -> "23.10 14:05:09".
function positionOpenTime(p) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})/.exec(String(p?.time || ''));
  return m ? `${m[3]}.${m[2]}.${m[1].slice(2)} ${m[4]}` : '—';
}
// Table cell: date over time, so the column stays narrow.
function positionOpenTimeStacked(p) {
  const t = positionOpenTime(p), i = t.indexOf(' ');
  return i < 0 ? escapeHtml(t) : `<span class="block leading-tight">${escapeHtml(t.slice(0, i))}</span><span class="block leading-tight text-gray-400">${escapeHtml(t.slice(i + 1))}</span>`;
}

// Which part of the app opened this position: bot (and with which entry mode / strategy), MevO, signal scanner, web, MT5.
function positionOrigin(p) {
  const magic = Number(p?.magic || (Array.isArray(p?.opening_fills) && p.opening_fills[0] ? p.opening_fills[0].magic : 0) || 0);
  const comment = String(p?.comment || '');
  const row = (typeof latestBotSignalHistory !== 'undefined' ? latestBotSignalHistory : []).find(r => r && Number(r.ticket) === Number(p?.ticket));
  const modes = {CROSSOVER: 'Strateji (MA kesişimi)', MANUAL_STYLE: 'Manuel işlemlerim tarzı', MEVO: 'MevO fırsatı'};
  if (/^mevo/i.test(comment)) return {name: 'MevO', detail: 'MevO sayfasından fırsat kartıyla açıldı (elle onaylandı).'};
  if (magic === 123461 || /hma bot/i.test(comment) || /^HMA Sinyal/i.test(comment)) {
    const tf = /hma bot (\w+)/i.exec(comment)?.[1];
    const mode = row?.entry_mode;
    const how = mode ? modes[mode] || mode : 'giriş türü kayıtlı değil';
    const lines = [`Giriş: ${how}`];
    if (row?.strategy && mode !== 'MEVO') lines.push(`Strateji: ${row.strategy}`);
    if (tf) lines.push(`Zaman dilimi: ${tf}`);
    if (/^HMA Sinyal/i.test(comment)) lines.push('Sinyal tarayıcıdan onaylandı.');
    return {name: 'HMA Bot', detail: lines.join(' · ')};
  }
  if (magic === 123462) return {name: 'MevO', detail: 'MevO (eski AI etiketi).'};
  if (magic === 123460 || comment.startsWith('vm:') || /hma web|web sinyal|panel/i.test(comment)) {
    return {name: 'HMA Web', detail: /web sinyal/i.test(comment) ? 'Sinyal tarayıcıdaki "Şimdi AL/SAT" ile açıldı.' : 'HMA web panelinden elle açıldı.'};
  }
  if (magic === 0) return {name: 'MT5 (elle)', detail: 'Orijinal MetaTrader 5 terminalinden elle açıldı.'};
  return {name: 'Uzman Danışman / EA', detail: `Harici EA (Magic #${magic}).`};
}

function renderPositionExecution(position) {
  const quality = position.execution_quality || {};
  const quote = position.quote || {};
  const digits = Number.isInteger(position.digits) ? position.digits : 5;
  const price = value => value == null ? '—' : Number(value).toFixed(digits);
  const card = (label, value) => `<div class="bg-[#1a1f28] rounded-xl p-4"><div class="text-xs text-gray-400 mb-2">${label}</div><div class="font-bold text-white tabular-nums">${value}</div></div>`;
  const change = quality.spread_change_points;
  const changeText = change == null ? 'Karşılaştırılamıyor' : `${executionPoints(change, true)} · ${change > 0 ? 'Genişledi' : change < 0 ? 'Daraldı' : 'Değişmedi'}`;
  const note = quality.state === 'recorded'
    ? 'Açılış bilgileri emir gönderim kotasyonu ile brokerın gerçekleşen açılış hareketlerinden hesaplanır. Birden fazla gerçekleşmede lot ağırlıklı ortalama gösterilir.'
    : quality.state === 'pending_quote_missing'
      ? 'Bekleyen emrin tetiklendiği andaki kotasyon kaydedilmedi. Emir oluşturma spreadi açılış spreadi olarak kullanılmaz.'
      : quality.state === 'changed_direction'
        ? 'Pozisyon yön değiştirmiş; açılış spreadi ve kayma tek bir başlangıç fiyatıyla güvenilir biçimde karşılaştırılamıyor.'
        : 'Bu pozisyon için emir gönderim kotasyonu kaydedilmemiş. Açılış spreadi ve slippage sıfır varsayılmaz. Yeni panel emirlerinde kotasyon kalıcı olarak kaydedilir.';
  const status = quote.quote_status || {};
  const currentLabel = status.state === 'fresh' ? 'Güncel spread' : 'Son kotasyon spreadi';
  const fills = (quality.fills || []).map(d => `<tr class="border-b border-gray-700/50"><td class="p-3">#${d.order}</td><td class="p-3">${d.volume}</td><td class="p-3">${price(d.bid)} / ${price(d.ask)}</td><td class="p-3">${price(d.requested_price)}</td><td class="p-3">${price(d.fill_price)}</td><td class="p-3">${executionPoints(d.spread_points)}</td><td class="p-3 whitespace-nowrap">${slippageText(d.slippage_points)}</td></tr>`).join('');
  const origin = positionOrigin(position);
  return `<div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
    ${card('Açan', escapeHtml(origin.name))}${card('Açılış saati (MT5 sunucu)', escapeHtml(positionOpenTime(position)))}${card('Nasıl açıldı', `<span class="text-xs font-normal text-gray-300">${escapeHtml(origin.detail)}</span>`)}
    ${card('Açılış spreadi (gönderim)', executionPoints(quality.opening_spread_points))}${card(currentLabel, executionPoints(quality.current_spread_points))}${card('Spread farkı', changeText)}
    ${card('İstenen açılış fiyatı', price(quality.requested_price))}${card('Gerçekleşen açılış fiyatı', price(quality.fill_price ?? position.price_open))}${card('Açılış slippage', slippageText(quality.slippage_points))}
    </div><div class="text-xs text-gray-400 mb-4">Gönderim Bid / Ask: <span class="text-gray-200">${price(quality.opening_bid)} / ${price(quality.opening_ask)}</span><br>Son Bid / Ask: <span class="text-gray-200">${price(quote.bid > 0 ? quote.bid : null)} / ${price(quote.ask > 0 ? quote.ask : null)}</span><br>1 puan: ${price(quote.point)} fiyat birimi</div>
    <p class="text-xs ${status.state === 'fresh' ? 'text-emerald-400' : 'text-amber-300'} mb-3">${escapeHtml(status.message || 'Güncel kotasyon doğrulanamadı.')}</p>
    <p class="text-xs text-gray-400 mb-3">${note}</p>
    <p class="text-xs text-gray-400 mb-4">BUY işlemleri Ask ile açılır, Bid ile kapanır; SELL işlemleri Bid ile açılır, Ask ile kapanır. Spread değişebilir. Kaymada pozitif değer aleyhe, negatif değer lehe farktır. Spread ve slippage fiyata yansır; komisyon gibi ayrıca düşülmez. Açık pozisyonun gelecekteki kapanış slippage'i henüz bilinmez.</p>
    ${fills ? `<div class="overflow-x-auto"><table class="w-full text-xs text-left whitespace-nowrap"><thead class="bg-[#1a1f28] text-gray-400"><tr><th class="p-3">Açılış emri</th><th class="p-3">Lot</th><th class="p-3">Gönderim Bid / Ask</th><th class="p-3">İstenen</th><th class="p-3">Gerçekleşen</th><th class="p-3">Spread</th><th class="p-3">Slippage</th></tr></thead><tbody>${fills}</tbody></table></div>` : ''}`;
}

// Position source, same rules and colours as the badge in the positions table.
const TRADE_SOURCES = {
  bot: 'Otomatik Sinyal (HMA Bot)', ai: 'Otomatik Sinyal (MevO AI)', web: 'HMA Sitesi (Manuel)',
  mt5: 'Orijinal MT5 (Manuel)', ea: 'Harici Bot / EA'};
// Entry mode the bot used for this position (from the bot's signal records): CROSSOVER / MANUAL_STYLE / MEVO, or null.
function botEntryMode(item) {
  const rows = typeof latestBotSignalHistory !== 'undefined' ? latestBotSignalHistory : [];
  const row = (rows || []).find(r => r && r.ticket != null && Number(r.ticket) === Number(item?.ticket));
  return row?.entry_mode || null;
}
function tradeSource(item) {
  const magic = Number(item?.magic || 0);
  const fillMagic = Array.isArray(item?.opening_fills) && item.opening_fills.length ? Number(item.opening_fills[0]?.magic || 0) : 0;
  const effectiveMagic = magic || fillMagic;
  const comment = String(item?.comment || '');
  if (/^mevo/i.test(comment)) return 'ai';                                    // opened from the MevO page
  if (effectiveMagic === 123461 || /hma bot/i.test(comment)) return botEntryMode(item) === 'MEVO' ? 'ai' : 'bot';
  if (effectiveMagic === 123462 || /deepseek/i.test(comment)) return 'ai';
  if (effectiveMagic === 123460 || comment.startsWith('vm:') || /hma web|panel/i.test(comment)) return 'web';
  return effectiveMagic === 0 ? 'mt5' : 'ea';
}
// "Pozisyonlar" tab: one coloured count per source (e.g. amber 7 = MT5 manual, cyan 1 = HMA bot).
function renderPositionSourceCounts(positions) {
  const node = document.getElementById('ws-tab-positions-count');
  if (!node) return;
  const counts = {};
  for (const p of positions || []) { const k = tradeSource(p); counts[k] = (counts[k] || 0) + 1; }
  const order = ['mt5', 'bot', 'ai', 'web', 'ea'].filter(k => counts[k]);
  node.innerHTML = order.length
    ? order.map(k => `<span class="ws-src" data-src="${k}" title="${counts[k]} pozisyon · ${TRADE_SOURCES[k]}">${counts[k]}</span>`).join('')
    : '<span class="ws-src" data-src="none" title="Açık pozisyon yok">0</span>';
  node.setAttribute?.('aria-label', order.length ? order.map(k => `${counts[k]} ${TRADE_SOURCES[k]}`).join(', ') : 'Açık pozisyon yok');
}

function tradeSourceBadge(item) {
  const magic = Number(item?.magic || 0);
  const fillMagic = Array.isArray(item?.opening_fills) && item.opening_fills.length
    ? Number(item.opening_fills[0]?.magic || 0)
    : 0;
  const effectiveMagic = magic || fillMagic;
  const comment = String(item?.comment || '');
  const chip = (tone, title, text) => `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded bg-${tone}-500/20 text-${tone}-300 border border-${tone}-500/40 text-xs font-sans font-semibold leading-tight" title="${title}">${text}</span>`;
  if (/^mevo/i.test(comment)) {
    return chip('purple', 'MevO fırsatı; MevO sayfasından elle onaylanıp açıldı', '🧠 MevO (Manuel)');
  }
  if (effectiveMagic === 123461 || /hma bot/i.test(comment)) {
    const mode = botEntryMode(item);
    if (mode === 'MEVO') return chip('purple', 'HMA botu MevO modunda otomatik açtı (Magic #123461)', '🧠 Otomatik Sinyal (MevO)');
    if (mode === 'MANUAL_STYLE') return chip('amber', 'HMA botu "Manuel işlemlerim tarzı" modunda otomatik açtı (Magic #123461)', '🤖 Otomatik Sinyal (Manuel tarzı)');
    return chip('cyan', 'HMA Algoritmik Bot / Otomatik Sinyal Yakalama (Magic #123461)', '🤖 Otomatik Sinyal (HMA Bot)');
  }
  if (effectiveMagic === 123462 || /deepseek/i.test(comment)) {
    return chip('purple', 'MevO Yapay Zeka Sinyali (Magic #123462)', '🧠 Otomatik Sinyal (MevO AI)');
  }
  if (effectiveMagic === 123460 || comment.startsWith('vm:') || /hma web|panel/i.test(comment)) {
    return `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-xs font-sans font-semibold leading-tight" title="HMA Web Sitesinden Manuel Açıldı (Magic #123460)">🌐 HMA Sitesi (Manuel)</span>`;
  }
  if (effectiveMagic === 0) {
    return `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-xs font-sans font-semibold leading-tight" title="Orijinal MetaTrader 5 Terminalinden Manuel Açıldı (Magic #0)">🖥️ Orijinal MT5 (Manuel)</span>`;
  }
  return `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded bg-gray-800 text-gray-200 border border-gray-700 text-xs font-sans font-semibold leading-tight" title="Harici Uzman Danışman / EA (Magic #${effectiveMagic})">⚙️ Harici Bot (#${effectiveMagic})</span>`;
}

// Click a column header of the open positions table: ascending → descending → original order (choice is remembered).
const POSITION_SORT_KEY = 'hma.positions.sort.v1';
let positionSort = (() => {
  try { const v = JSON.parse(localStorage.getItem(POSITION_SORT_KEY) || 'null'); return v && typeof v.key === 'string' && (v.dir === 1 || v.dir === -1) ? v : null; }
  catch (_) { return null; }
})();
function positionSortValue(p, key) {
  if (key === 'time') return String(p.time || '');                      // "YYYY-MM-DD HH:MM:SS" sorts as text
  if (key === 'symbol' || key === 'type') return String(p[key] || '');
  const n = Number(key === 'ticket' ? p.ticket : p[key]);
  return Number.isFinite(n) ? n : (key === 'sl' || key === 'tp' ? 0 : -Infinity);
}
function sortPositions(rows, sort = positionSort) {
  if (!sort) return rows;
  const out = rows.slice();
  out.sort((a, b) => {
    const x = positionSortValue(a, sort.key), y = positionSortValue(b, sort.key);
    const c = typeof x === 'string' ? x.localeCompare(y) : (x < y ? -1 : x > y ? 1 : 0);
    return (c || Number(a.ticket) - Number(b.ticket)) * sort.dir;
  });
  return out;
}
function markPositionSort() {
  document.querySelectorAll('.hx-pos-table thead th[data-sort]').forEach(th => {
    const on = positionSort && positionSort.key === th.dataset.sort;
    th.setAttribute('aria-sort', on ? (positionSort.dir === 1 ? 'ascending' : 'descending') : 'none');
  });
}
function setPositionSort(key) {
  positionSort = !positionSort || positionSort.key !== key ? {key, dir: 1} : positionSort.dir === 1 ? {key, dir: -1} : null;
  try { positionSort ? localStorage.setItem(POSITION_SORT_KEY, JSON.stringify(positionSort)) : localStorage.removeItem(POSITION_SORT_KEY); } catch (_) { /* session only */ }
  markPositionSort();
  if (Array.isArray(window.lastPositionsData)) renderPositionsData(window.lastPositionsData);
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.hx-pos-table thead th[data-sort]').forEach(th => {
    th.tabIndex = 0; th.setAttribute('role', 'columnheader'); th.classList.add('hx-sortable');
    th.addEventListener('click', () => setPositionSort(th.dataset.sort));
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setPositionSort(th.dataset.sort); } });
  });
  markPositionSort();
});

function renderPositionsData(positions) {
    if (accountSwitching) return;
    if (!Array.isArray(positions)) throw new Error('Pozisyon listesi doğrulanamadı.');
    positionsRenderRevision++;
    window.lastPositionsData = positions;
    pollPositionRules();
    executionPositions.clear();
    (positions || []).forEach(p => executionPositions.set(p.ticket, p));
    updatePositionExecution();
    if (typeof renderBotSignalHistory === 'function') renderBotSignalHistory();
    if (typeof rememberPositions === "function") rememberPositions(positions);
    seedNettingProtection(positions);
    window.MT5Chart?.setTradeLevels?.(positions);
    const tbody = document.getElementById("positions-table-body");
    const countBadge = document.getElementById("pos-count-badge");
    if (countBadge) countBadge.innerText = positions.length;
    renderPositionSourceCounts(positions);

    if (!positions || positions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="13" class="py-8 text-center text-gray-400 font-sans text-sm">Henüz açık pozisyon bulunmuyor.</td></tr>`;
      return;
    }

    let rowsHtml = "";
    sortPositions(positions).forEach(p => {
      const digits = Number.isInteger(p.digits) ? p.digits : 5;
      const price = value => Number(value).toFixed(digits);
      const money = value => value == null ? "—" : `${value > 0 ? "+" : ""}${accountCurrency} ${formatMoney(value)}`;
      const side = p.type === "BUY" ? "BUY" : "SELL";
      const profitClass = p.profit >= 0 ? "hx-profit" : "hx-loss";
      const ticket = Number(p.ticket);
      rowsHtml += `
        <tr>
          <td data-label="Bilet" class="whitespace-nowrap">
            <span class="font-mono font-semibold text-gray-100">#${ticket}</span><button type="button" class="hx-detail" onclick="showPositionExecution(${ticket})" title="Gönderim Bid/Ask, açılış spreadi ve kayma">Detay</button>
            <div>${tradeSourceBadge(p)}</div>
          </td>
          <td data-label="Açılış saati" class="whitespace-nowrap text-gray-300" title="MT5 sunucu saati: ${escapeHtml(positionOpenTime(p))}">${positionOpenTimeStacked(p)}</td>
          <td data-label="Sembol" class="font-sans font-bold text-white whitespace-nowrap">${escapeHtml(p.symbol)}</td>
          <td data-label="Yön"><span class="hx-dir" data-side="${side}">${side}</span></td>
          <td data-label="Hacim" class="num">${p.volume}</td>
          <td data-label="Açılış" class="num text-gray-300">${price(p.price_open)}</td>
          <td data-label="Güncel" class="num text-white">${price(p.price_current)}</td>
          <td data-label="SL" class="num">${p.sl ? price(p.sl) : '<span class="hx-none">Yok</span>'}</td>
          <td data-label="TP" class="num">${p.tp ? price(p.tp) : '<span class="text-gray-500">—</span>'}</td>
          <td data-label="Swap" class="num text-gray-300">${p.swap == null ? "—" : money(p.swap)}</td>
          <td data-label="Komisyon" class="num text-gray-300">${money(p.commission)}</td>
          <td data-label="Kâr / Zarar" class="num font-semibold ${profitClass}">${p.profit >= 0 ? "+" : ""}${accountCurrency} ${formatMoney(p.profit)}</td>
          <td data-label="" class="num hx-actions-cell">
            <span class="hx-row-actions">
              <button type="button" onclick="showPositionEditor(${ticket})" title="SL/TP değiştir veya kısmi kapat">Değiştir</button>
              ${positionRuleButton(ticket)}
              ${closePending.has(Number(ticket))
                ? `<button type="button" data-close-ticket="${ticket}" disabled aria-busy="true" class="is-closing" title="Kapatma isteği gönderildi; broker sonucu bekleniyor">Kapatılıyor…</button>`
                : `<button type="button" data-close-ticket="${ticket}" onclick="closePosition(${ticket})" title="Pozisyon #${ticket} kapat">Kapat</button>`}
            </span>
          </td>
        </tr>
      `;
    });

    patchPositionRows(tbody, rowsHtml);
}

function positionRuleText(rule) {
  const part = (kind, v) => kind === 'percent' ? `%${v}` : `${accountCurrency} ${formatMoney(v)}`;
  const bits = [];
  if (rule.profit) bits.push(`+${part(rule.profit_kind, rule.profit)}`);
  if (rule.loss) bits.push(`−${part(rule.loss_kind, rule.loss)}`);
  return bits.join(' / ');
}
function positionRuleButton(ticket) {
  const rule = (window.positionRules || {})[ticket];
  if (!rule) return `<button type="button" class="hx-rule" onclick="showPositionRule(${ticket})" title="Kâr hedefi veya zarar sınırına ulaşınca otomatik kapat">Hedef</button>`;
  const warn = rule.status !== 'armed';
  return `<button type="button" class="hx-rule is-set${warn ? ' is-warn' : ''}" onclick="showPositionRule(${ticket})" title="${escapeHtml(positionRuleText(rule))}${warn ? ' · ' + escapeHtml(rule.message || rule.status) : ''}">Hedef: ${escapeHtml(positionRuleText(rule))}</button>`;
}
async function refreshPositionRules() {
  try {
    const res = await fetch('/api/position-rules');
    if (!res.ok) return;
    const rows = await res.json();
    const next = {};
    (Array.isArray(rows) ? rows : []).forEach(r => { next[r.ticket] = r; });
    if (JSON.stringify(next) === JSON.stringify(window.positionRules || {})) return;
    window.positionRules = next;
    if (Array.isArray(window.lastPositionsData)) renderPositionsData(window.lastPositionsData);
  } catch (_) { /* panel keeps the last known rules */ }
}
let positionRulesPolled = 0;
function pollPositionRules() {
  if (Date.now() - positionRulesPolled < 3000) return;
  positionRulesPolled = Date.now();
  refreshPositionRules();
}

// The live feed re-renders several times a second. Replacing every row would destroy the "Değiştir" / "Kapat"
// button between mouse-down and mouse-up and the click would be lost, so only cells whose content changed are patched.
function patchPositionRows(tbody, rowsHtml) {
  const probe = typeof document.createElement === 'function' ? document.createElement('tbody') : null;
  if (!probe || !('rows' in probe)) { tbody.innerHTML = rowsHtml; return; }
  probe.innerHTML = rowsHtml;
  const next = Array.from(probe.rows), current = Array.from(tbody.rows || []);
  const key = (row) => row.querySelector('[data-close-ticket]')?.dataset.closeTicket || '';
  const sameShape = next.length === current.length && next.every((row, i) => key(row) && key(row) === key(current[i])
    && row.cells.length === current[i].cells.length);
  if (!sameShape) {
    tbody.innerHTML = rowsHtml;
    Array.from(tbody.rows || []).forEach((row, i) => Array.from(row.cells).forEach((cell, c) => { cell.__src = next[i]?.cells[c]?.innerHTML; }));
    return;
  }
  next.forEach((row, i) => {
    Array.from(row.cells).forEach((cell, c) => {
      const old = current[i].cells[c];
      // Compare with what was rendered last time, not the live DOM: the language layer rewrites live text.
      if (old.__src !== cell.innerHTML) { old.innerHTML = cell.innerHTML; old.__src = cell.innerHTML; }
      if (old.className !== cell.className && old.__cls !== cell.className) { old.className = cell.className; old.__cls = cell.className; }
    });
  });
}

async function fetchPositions(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingPositions) return;
  isFetchingPositions = true;
  const generation = accountSessionGeneration, revision = positionsRenderRevision;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch("/api/positions", { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error("Pozisyonlar doğrulanamadı");
    const positions = await res.json();

    if (generation === accountSessionGeneration && revision === positionsRenderRevision) renderPositionsData(positions);
  } catch (err) {
    if (generation !== accountSessionGeneration || revision !== positionsRenderRevision) return;
    const badge = document.getElementById("pos-count-badge");
    if (badge) badge.innerText = "Veri güncel değil";
  } finally {
    clearTimeout(timer);
    isFetchingPositions = false;
  }
}

// Fetch Closed Positions / Trade History
let historyDetailsRequest = 0;

function closeHistoryDetails() {
  historyDetailsRequest++;
  document.getElementById('history-details-modal').close();
}

async function showHistoryDetails(positionId, ticket) {
  if (!Number.isSafeInteger(positionId) || positionId <= 0) return;
  const modal = document.getElementById('history-details-modal');
  const content = document.getElementById('history-details-content');
  const request = ++historyDetailsRequest;
  const generation = accountSessionGeneration;
  document.getElementById('history-details-title').textContent = `İşlem #${ticket} · Pozisyon #${positionId}`;
  content.textContent = 'Broker işlem detayları yükleniyor…';
  if (!modal.open) modal.showModal();
  try {
    const response = await fetch(`/api/history/position/${positionId}`);
    const data = await response.json();
    if (generation !== accountSessionGeneration || request !== historyDetailsRequest || !modal.open) return;
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'İşlem detayları alınamadı.');
    if (Number(data.position_id) !== positionId || !data.deals?.some(d => Number(d.ticket) === ticket)) {
      throw new Error('Seçilen işlem broker geçmişinde bulunamadı. Kapanış listesini yenileyin.');
    }
    content.innerHTML = renderHistoryDetails(data, ticket);
  } catch (error) {
    if (generation === accountSessionGeneration && request === historyDetailsRequest && modal.open) content.textContent = error.message || 'İşlem detayları alınamadı.';
  }
}

function renderHistoryDetails(data, selectedTicket) {
  const deals = data.deals;
  const trading = deals.filter(d => d.type === 0 || d.type === 1);
  const opens = trading.filter(d => d.entry === 0);
  const exits = trading.filter(d => d.entry === 1 || d.entry === 3);
  const reversed = trading.some(d => d.entry === 2);
  const digits = Number.isInteger(data.digits) ? Math.max(0, Math.min(10, data.digits)) : 5;
  const price = value => Number(value).toLocaleString('tr-TR', {minimumFractionDigits:digits, maximumFractionDigits:digits});
  const money = value => `${escapeHtml(data.currency || accountCurrency)} ${formatMoney(value)}`;
  const sum = key => deals.reduce((total, d) => total + Number(d[key] || 0), 0);
  const net = d => ['profit', 'commission', 'swap', 'fee'].reduce((total, key) => total + Number(d[key] || 0), 0);
  const selected = deals.find(d => Number(d.ticket) === selectedTicket);
  const side = type => type === 0 ? 'BUY · Alış' : type === 1 ? 'SELL · Satış' : 'Maliyet / diğer';
  const summary = (label, rows) => {
    if (!rows.length || reversed) return `<div class="bg-[#1a1f28] rounded-xl p-4"><div class="text-gray-400 text-xs mb-2">${label}</div><div class="text-sm">${reversed ? 'Tersine dönüş var; hareket listesine bakın.' : 'Broker geçmişinde kayıt bulunamadı.'}</div></div>`;
    const volume = rows.reduce((total, d) => total + d.volume, 0);
    const average = volume > 0 ? rows.reduce((total, d) => total + d.price * d.volume, 0) / volume : null;
    const times = rows.length === 1 ? escapeHtml(rows[0].time_text) : `${escapeHtml(rows[0].time_text)} → ${escapeHtml(rows[rows.length - 1].time_text)}`;
    return `<div class="bg-[#1a1f28] rounded-xl p-4"><div class="text-gray-400 text-xs mb-2">${label}${rows.length > 1 ? ' · Lot ağırlıklı ortalama' : ''}</div><div class="font-bold ${rows[0].type === 0 ? 'text-emerald-400' : 'text-rose-400'}">${side(rows[0].type)}</div><div class="text-xl font-mono text-white mt-1">${average === null ? '—' : price(average)}</div><div class="text-xs text-gray-400 mt-2">${times}<br>${volume} lot · ${rows.length} hareket</div></div>`;
  };
  const entryLabel = entry => ({0:'Açılış / artırma', 1:'Kapanış / azaltma', 2:'Tersine dönüş', 3:'Karşı pozisyonla kapanış'}[entry] || 'Diğer');
  const quality = data.execution_quality || {};
  const recorded = quality.state === 'recorded';
  const observedPrice = value => typeof value === 'number' && Number.isFinite(value) ? price(value) : 'Ölçülemiyor';
  const executionCard = (label, value) => `<div class="bg-[#1a1f28] rounded-xl p-3"><div class="text-gray-400 mb-1">${label}</div><div class="font-mono text-gray-100">${value}</div></div>`;
  const executionDetails = `<div class="border border-gray-700 rounded-xl p-4 mb-4"><div class="text-sm font-bold mb-3">Açılış spreadi ve slippage</div>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs mb-3">
      ${executionCard('Spread (emir gönderimi)', recorded ? executionPoints(quality.opening_spread_points) : 'Ölçülemiyor')}
      ${executionCard('İstenen açılış fiyatı', observedPrice(quality.requested_price))}
      ${executionCard('Gerçekleşen açılış fiyatı', observedPrice(quality.fill_price))}
      ${executionCard('Açılış slippage', recorded ? slippageText(quality.slippage_points) : 'Ölçülemiyor')}
    </div><p class="text-xs text-gray-400">${recorded ? `Gönderim Bid / Ask: ${observedPrice(quality.opening_bid)} / ${observedPrice(quality.opening_ask)}. Birden fazla açılışta lot ağırlıklı ortalamalar gösterilir.` : 'Bu pozisyonun açılış kotasyonu güvenle eşlenemedi; eksik spread ve slippage sıfır varsayılmaz.'} Kapanış spreadi ve slippage için kayıtlı gönderim kotasyonu bulunmuyor. Pozisyonun broker gerçekleşme fiyatları aşağıdadır.</p></div>`;
  const rows = deals.map(d => `<tr class="border-b border-gray-700/50 ${Number(d.ticket) === selectedTicket ? 'bg-cyan-500/10' : ''}">
    <td class="p-3 whitespace-nowrap">#${d.ticket}${Number(d.ticket) === selectedTicket ? '<span class="block text-cyan-400 text-xs">Seçilen kapanış</span>' : ''}</td>
    <td class="p-3 whitespace-nowrap">${escapeHtml(d.time_text)}</td>
    <td class="p-3 whitespace-nowrap">${entryLabel(d.entry)}<span class="block text-xs ${d.type === 0 ? 'text-emerald-400' : d.type === 1 ? 'text-rose-400' : 'text-gray-400'}">${side(d.type)}</span></td>
    <td class="p-3 text-right">${d.volume}</td><td class="p-3 font-mono text-right">${d.type === 0 || d.type === 1 ? price(d.price) : '—'}</td>
    <td class="p-3 text-right whitespace-nowrap ${net(d) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${money(net(d))}</td>
    <td class="p-3 text-xs text-gray-400">${escapeHtml(d.comment)}</td></tr>`).join('');
  return `<div class="text-sm text-gray-400 mb-4">${escapeHtml(data.symbol)} · Pozisyon #${data.position_id}</div>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">${summary('Açılış', opens)}${summary('Kapanış', exits)}</div>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs mb-4">
      <div>Seçilen kapanış neti<div class="font-bold text-white mt-1">${money(net(selected))}</div></div>
      <div>Pozisyon gerçekleşen neti<div class="font-bold text-white mt-1">${money(sum('profit') + sum('commission') + sum('swap') + sum('fee'))}</div></div>
      <div>Toplam komisyon<div class="text-amber-400 mt-1">${money(sum('commission'))}</div></div>
      <div>Toplam swap / diğer ücret<div class="text-gray-300 mt-1">${money(sum('swap') + sum('fee'))}</div></div>
    </div>
    <p class="text-xs text-gray-400 mb-4">Tüm pozisyon hareketleri aşağıda gösterilir. Net tutarlar açılış ve kapanış maliyetlerini içerir; kısmi kapanışlarda pozisyonun tamamı kapanmış olmayabilir. Saatler broker geçmişindeki saatlerdir.</p>
    ${executionDetails}
    <div class="overflow-x-auto"><table class="w-full text-xs text-left"><thead class="text-gray-400 bg-[#1a1f28]"><tr><th class="p-3">Bilet</th><th class="p-3">Zaman</th><th class="p-3">Hareket / yön</th><th class="p-3 text-right">Lot</th><th class="p-3 text-right">Fiyat</th><th class="p-3 text-right">Net</th><th class="p-3">Açıklama</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function fetchHistory() {
  if (isFetchingHistory) return;
  isFetchingHistory = true;
  const generation = accountSessionGeneration;
  try {
    const res = await fetch("/api/history?days=30");
    if (!res.ok) return;
    const history = await res.json();
    if (generation !== accountSessionGeneration || accountSwitching) return;

    const tbody = document.getElementById("history-table-body");
    const countBadge = document.getElementById("history-count-badge");
    const totalProfitEl = document.getElementById("history-total-profit");
    const totalCommissionEl = document.getElementById("history-total-commission");

    if (countBadge) countBadge.innerText = history.length;

    let totalProfit = 0;
    let totalCommission = 0;
    history.forEach(d => {
      totalProfit += (d.profit || 0) + (d.commission || 0) + (d.swap || 0) + (d.fee || 0);
      totalCommission += (d.commission || 0);
    });
    if (totalCommissionEl) totalCommissionEl.innerText = `${totalCommission > 0 ? "+" : ""}${accountCurrency} ${formatMoney(totalCommission)}`;

    if (totalProfitEl) {
      const pColor = totalProfit >= 0 ? "text-emerald-400" : "text-rose-400";
      const pSign = totalProfit >= 0 ? "+" : "";
      totalProfitEl.className = `font-bold ${pColor}`;
      totalProfitEl.innerText = `${pSign}${accountCurrency} ${formatMoney(totalProfit)}`;
    }

    if (!tbody) return;

    if (!history || history.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" class="py-6 text-center text-gray-500 font-sans">Henüz kapalı işlem geçmişi bulunmuyor.</td></tr>`;
      return;
    }

    let rowsHtml = "";
    history.forEach(d => {
      const isBuy = (d.position_type || d.type) === "BUY";
      const typeBadge = isBuy
        ? `<span class="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-bold tracking-wide">BUY</span>`
        : `<span class="px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30 text-xs font-bold tracking-wide">SELL</span>`;

      const netProfit=(d.profit||0)+(d.commission||0)+(d.swap||0)+(d.fee||0);
      const profitColor = netProfit >= 0 ? "text-emerald-400" : "text-rose-400";
      const profitSign = netProfit >= 0 ? "+" : "";

      const commission = d.commission || 0;
      const commissionText = `${commission > 0 ? "+" : ""}${accountCurrency} ${formatMoney(commission)}`;
      const fee = (d.swap || 0) + (d.fee || 0);
      const feeText = fee !== 0 ? `${fee >= 0 ? "+" : ""}${accountCurrency} ${formatMoney(fee)}` : "-";
      const positionId = Number(d.position_id || d.order);
      const ticket = Number(d.ticket);

      rowsHtml += `
        <tr data-testid="history-deal-${ticket}" onclick="showHistoryDetails(${positionId}, ${ticket}, this)" class="cursor-pointer hover:bg-[#14181f]/70 transition border-b border-gray-800/60" title="Açılış ve kapanış detaylarını göster">
          <td data-label="Bilet" class="py-2 px-2.5">
            <button type="button" onclick="event.stopPropagation(); showHistoryDetails(${positionId}, ${ticket}, this)" class="text-left font-mono font-bold text-[13px] text-white hover:text-cyan-400 focus:outline focus:outline-2 focus:outline-cyan-400 rounded" aria-label="İşlem #${ticket} detayları">
              #${ticket} <span class="text-xs text-gray-400 block font-normal">Pos: #${positionId} · Detay</span>
            </button>
            <div>${tradeSourceBadge(d)}</div>
          </td>
          <td data-label="Sembol" class="py-2 px-2.5 font-bold text-white text-[13px] tracking-wide">${escapeHtml(d.symbol)}</td>
          <td data-label="Yön" class="py-2 px-2.5">${typeBadge}</td>
          <td data-label="Hacim" class="py-2 px-2.5 text-white font-semibold text-[13px]">${d.volume}</td>
          <td data-label="Kapanış fiyatı" class="py-2 px-2.5 text-gray-200 font-mono text-[13px] font-medium">${d.price}</td>
          <td data-label="Komisyon" class="py-2 px-2.5 text-right font-mono font-bold text-[13px] text-amber-400 whitespace-nowrap">${commissionText}</td>
          <td data-label="Swap / ücret" class="py-2 px-2.5 text-right font-mono text-xs text-gray-300 whitespace-nowrap">${feeText}</td>
          <td data-label="Kapanış zamanı" class="py-2 px-2.5 font-mono text-xs text-gray-300 whitespace-nowrap" title="Broker saati: ${escapeHtml(positionOpenTime(d))}">${positionOpenTimeStacked(d)}</td>
          <td data-label="Net K/Z" class="py-2 px-2.5 text-right font-bold font-mono text-[13px] whitespace-nowrap ${profitColor}">${profitSign}${accountCurrency} ${formatMoney(netProfit)}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;
  } catch (err) {
    console.error("fetchHistory error:", err);
  } finally {
    isFetchingHistory = false;
  }
}

// Fetch Performance & Daily Reports
let reportsRequestGeneration = 0;
async function fetchReports() {
  const generation = accountSessionGeneration, request = ++reportsRequestGeneration;
  window.HMAPeriodReport?.load?.();
  try {
    const daysSelect = document.getElementById("reports-days-select");
    const days = daysSelect ? daysSelect.value : 30;
    const res = await fetch(`/api/reports?days=${days}`);
    if (!res.ok) throw new Error("Rapor doğrulanamadı");
    const data = await res.json();
    if (generation !== accountSessionGeneration || request !== reportsRequestGeneration || accountSwitching) return;
    if (!data || !data.summary) return;

    const s = data.summary;
    const currency = data.currency || accountCurrency;
    const note=document.getElementById("reports-basis");
    if (note) note.textContent=data.basis + (s.incomplete_positions ? ` ${s.incomplete_positions} pozisyonun geçmişi eksik; başarı oranından çıkarıldı.` : "");

    // Today's Profit
    const repTodayProfit = document.getElementById("rep-today-profit");
    const repTodaySub = document.getElementById("rep-today-sub");
    if (repTodayProfit) {
      const isPos = s.today_profit >= 0;
      repTodayProfit.className = `text-base md:text-lg font-bold font-mono ${isPos ? "text-emerald-400" : "text-rose-400"}`;
      repTodayProfit.innerText = `${isPos ? "+" : ""}${currency} ${formatMoney(s.today_profit)}`;
    }
    if (repTodaySub) {
      repTodaySub.innerText = `${s.today_trades} pozisyon tamamen kapandı · broker sunucu günü`;
    }

    // Total Net Profit
    const repTotalProfit = document.getElementById("rep-total-profit");
    const repTotalSub = document.getElementById("rep-total-sub");
    if (repTotalProfit) {
      const isPos = s.total_profit >= 0;
      repTotalProfit.className = `text-base md:text-lg font-bold font-mono ${isPos ? "text-emerald-400" : "text-rose-400"}`;
      repTotalProfit.innerText = `${isPos ? "+" : ""}${currency} ${formatMoney(s.total_profit)}`;
    }
    if (repTotalSub) {
      repTotalSub.innerText = `Kapananlar: +${currency} ${formatMoney(s.gross_profit)} | Zarar: -${currency} ${formatMoney(Math.abs(s.gross_loss))}`;
    }

    // Win Rate
    const repWinRate = document.getElementById("rep-win-rate");
    const repWinSub = document.getElementById("rep-win-sub");
    if (repWinRate) {
      const color = s.win_rate >= 50 ? "text-emerald-400" : (s.total_trades === 0 ? "text-white" : "text-rose-400");
      repWinRate.className = `text-base md:text-lg font-bold font-mono ${color}`;
      repWinRate.innerText = `${s.win_rate}%`;
    }
    if (repWinSub) {
      repWinSub.innerText = `${s.winning_trades} Kazanç / ${s.losing_trades} Kayıp (${s.total_trades} Toplam)`;
    }

    // Profit Factor
    const repPF = document.getElementById("rep-profit-factor");
    const repPFSub = document.getElementById("rep-pf-sub");
    if (repPF) {
      const pfVal = s.profit_factor === null ? "∞" : s.profit_factor;
      repPF.innerText = pfVal;
    }
    if (repPFSub) {
      repPFSub.innerText = `Ort. Kâr: ${currency} ${formatMoney(s.avg_profit)} | Zarar: -${currency} ${formatMoney(Math.abs(s.avg_loss))}`;
    }

    // Quick Stats Bar
    const repBest = document.getElementById("rep-best-trade");
    const repWorst = document.getElementById("rep-worst-trade");
    const repVol = document.getElementById("rep-total-volume");
    const repFee = document.getElementById("rep-total-fee");
    const repCommission = document.getElementById("rep-total-commission");

    if (repBest) repBest.innerText = `+${currency} ${formatMoney(s.best_trade)}`;
    if (repWorst) repWorst.innerText = `-${currency} ${formatMoney(Math.abs(s.worst_trade))}`;
    if (repVol) repVol.innerText = `${s.total_volume} Lot`;
    if (repCommission) {
      const commission = s.total_commission || 0;
      repCommission.innerText = `${commission > 0 ? "+" : ""}${currency} ${formatMoney(commission)}`;
    }
    if (repFee) {
      const fee = (s.total_swap || 0) + (s.total_fee || 0);
      repFee.innerText = `${fee >= 0 ? "+" : ""}${currency} ${formatMoney(fee)}`;
    }

    // Daily Table
    const dailyTbody = document.getElementById("daily-reports-tbody");
    if (dailyTbody) {
      if (!data.daily || data.daily.length === 0) {
        dailyTbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-gray-500 font-sans">Seçilen dönemde işlem geçmişi bulunmuyor.</td></tr>`;
      } else {
        let dHtml = "";
        data.daily.forEach(d => {
          const isPos = d.profit >= 0;
          const pColor = isPos ? "text-emerald-400" : "text-rose-400";
          const pSign = isPos ? "+" : "";
          const dateBadge = d.is_today 
            ? `<span class="text-white font-bold">${d.date}</span> <span class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-xs uppercase font-sans font-bold">Bugün</span>`
            : `<span class="text-gray-300">${d.date}</span>`;
          
          const wrColor = d.win_rate >= 50 ? "text-emerald-400" : "text-rose-400";

          dHtml += `
            <tr class="hover:bg-[#14181f]/60 transition border-b border-gray-800/40">
              <td class="py-2.5 px-2.5 whitespace-nowrap">${dateBadge}</td>
              <td class="py-2.5 px-2.5 text-center text-gray-300 font-semibold whitespace-nowrap">${d.trades_count}</td>
              <td class="py-2.5 px-2.5 text-center text-xs text-gray-400 whitespace-nowrap"><span class="text-emerald-400 font-bold">${d.winning_trades}</span> / <span class="text-rose-400 font-bold">${d.losing_trades}</span></td>
              <td class="py-2.5 px-2.5 text-center font-bold font-mono ${wrColor} whitespace-nowrap">${d.win_rate}%</td>
              <td class="py-2.5 px-2.5 text-center text-gray-400 whitespace-nowrap">${d.volume} L</td>
              <td class="py-2.5 px-2.5 text-right font-bold font-mono ${pColor} whitespace-nowrap">${pSign}${currency} ${formatMoney(d.profit)}</td>
            </tr>
          `;
        });
        dailyTbody.innerHTML = dHtml;
      }
    }

    // Symbol Table
    const symTbody = document.getElementById("symbol-reports-tbody");
    if (symTbody) {
      if (!data.by_symbol || data.by_symbol.length === 0) {
        symTbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-gray-500 font-sans">Veri yok.</td></tr>`;
      } else {
        let sHtml = "";
        data.by_symbol.forEach(sym => {
          const isPos = sym.profit >= 0;
          const pColor = isPos ? "text-emerald-400" : "text-rose-400";
          const pSign = isPos ? "+" : "";

          sHtml += `
            <tr class="hover:bg-[#14181f]/60 transition border-b border-gray-800/40">
              <td class="py-2 px-2.5 font-bold text-white whitespace-nowrap">${escapeHtml(sym.symbol)}</td>
              <td class="py-2 px-2.5 text-center text-gray-300 whitespace-nowrap">${sym.trades_count}</td>
              <td class="py-2 px-2.5 text-center text-gray-400 whitespace-nowrap">${sym.volume} L</td>
              <td class="py-2 px-2.5 text-right font-bold font-mono ${pColor} whitespace-nowrap">${pSign}${currency} ${formatMoney(sym.profit)}</td>
            </tr>
          `;
        });
        symTbody.innerHTML = sHtml;
      }
    }

  } catch (err) {
    if (generation !== accountSessionGeneration || request !== reportsRequestGeneration || accountSwitching) return;
    const note=document.getElementById("reports-basis");
    if (note) note.textContent="Rapor güncellenemedi; gösterilen veriler eski olabilir.";
  }
}


// Fetch Price for Active Symbol
function renderPriceData(tick) {
    if (!tick || typeof tick !== 'object' || accountSwitching) return;
    if (chartSelectionBlocked || (tick.symbol && tick.symbol !== currentSymbol)) return;
    if (window.MT5Markets && !window.MT5Markets.getSymbol(currentSymbol)) return;
    priceRenderRevision++;
    executableQuote = {symbol:currentSymbol, bid:tick.bid, ask:tick.ask,
      status:tick.quote_status ? {...tick.quote_status} : null, receivedAt:Date.now()};
    if (typeof displayedTickTime !== "undefined") displayedTickTime = tick.time || 0;
    if (typeof displayedQuoteStatus !== "undefined") {
      displayedQuoteStatus = tick.quote_status || null;
      displayedQuoteReceivedAt = Date.now();
      quoteDiagnosticFailed = false;
    }
    if (typeof updateQuoteStatus === "function") updateQuoteStatus();
    refreshQuoteOrderNotice(tick);
    if (Number.isFinite(tick.bid) && tick.bid > 0 && Number.isFinite(tick.ask) && tick.ask >= tick.bid) {
      const metadataDigits = window.MT5Markets?.getSymbol(currentSymbol)?.digits;
      const digits = Number.isInteger(tick.digits) && tick.digits >= 0 && tick.digits <= 8 ? tick.digits
        : Number.isInteger(metadataDigits) && metadataDigits >= 0 && metadataDigits <= 8 ? metadataDigits : null;
      const bid = digits === null ? tick.bid : tick.bid.toFixed(digits);
      const ask = digits === null ? tick.ask : tick.ask.toFixed(digits);
      document.getElementById("header-bid").innerText = bid;
      document.getElementById("header-ask").innerText = ask;
      document.getElementById("header-spread").innerText = tick.spread;
      document.getElementById("btn-bid-price").innerText = String(bid);
      document.getElementById("btn-ask-price").innerText = String(ask);
      const ticketBid = document.getElementById("ticket-bid-price");
      const ticketAsk = document.getElementById("ticket-ask-price");
      if (ticketBid) ticketBid.innerText = bid;
      if (ticketAsk) ticketAsk.innerText = ask;
      window.MT5Chart?.tick?.(currentSymbol, tick.bid, tick.ask, tick.time);
    } else {
      for (const id of ['header-bid','header-ask','header-spread','btn-bid-price','btn-ask-price','ticket-bid-price','ticket-ask-price']) {
        const node = document.getElementById(id);
        if (node) node.innerText = '—';
      }
    }
    updateOrderButtons();
}

async function fetchPrice(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingPrice) return;
  isFetchingPrice = true;
  const symbol = currentSymbol;
  const generation = accountSessionGeneration, revision = priceRenderRevision;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch(`/api/price/${encodeURIComponent(symbol)}`, {signal:controller.signal});
    if (!res.ok) return;
    const tick = await res.json();

    if (generation === accountSessionGeneration && revision === priceRenderRevision && symbol === currentSymbol) renderPriceData(tick);
  } catch (err) {
    console.error("fetchPrice error:", err);
  } finally {
    clearTimeout(timer);
    isFetchingPrice = false;
  }
}

// Fetch Strategy Bot Status
let latestBotSettings = null;

async function fetchBotStatus() {
  try {
    const res = await fetch("/api/bot/status");
    if (!res.ok) throw new Error('Bot durumu doğrulanamadı.');
    const bot = await res.json();

    latestBotSettings = bot;
    isBotRunning = bot.is_running;
    const badge = document.getElementById("bot-status-badge");
    const toggleBtn = document.getElementById("bot-toggle-btn");

    if (bot.is_running) {
      badge.className = "text-xs font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 animate-pulse";
      badge.innerText = "ÇALIŞIYOR";
      toggleBtn.className = "bot-head-btn bot-head-stop";
      toggleBtn.innerHTML = `<i class="ph-bold ph-stop"></i><span>Botu Durdur</span>`;
    } else {
      badge.className = "text-xs font-bold px-2 py-0.5 rounded bg-gray-800 text-gray-400";
      badge.innerText = "DURDURULDU";
      toggleBtn.className = "bot-head-btn bot-head-start";
      toggleBtn.innerHTML = `<i class="ph-bold ph-play"></i><span>Botu Başlat</span>`;
    }
    const tabState = document.getElementById('ws-tab-bot-state');
    if (tabState) {
      if (tabState.dataset) tabState.dataset.running = String(Boolean(bot.is_running));
      else tabState.setAttribute?.('data-running', String(Boolean(bot.is_running)));
      tabState.textContent = bot.is_running ? `Bot çalışıyor · ${bot.symbol}` : 'Bot durdu';
    }
    const runtime = document.getElementById('bot-runtime-status');
    if (runtime) {
      const en = window.MT5I18n?.language?.() === 'en';
      const labels = {STOPPED:['Durduruldu','Stopped'],WAITING_CONNECTION:['Bağlantı bekleniyor','Waiting for connection'],
        WAITING_DATA:['Mum verisi bekleniyor','Waiting for candle data'],WAITING_SIGNAL:['Sinyal bekleniyor','Waiting for a signal'],
        WAITING_CONFIRMATION:['Sinyal onayı bekleniyor','Waiting for signal confirmation'],SUBMITTING:['Emir gönderiliyor','Submitting an order'],
        ORDER_REJECTED:['Son emir reddedildi','Last order rejected'],ORDER_UNRESOLVED:['Emir sonucu doğrulanmalı','Order outcome requires verification'],ERROR:['Bot hatası','Bot error']};
      const state = labels[bot.runtime_state]?.[en ? 1 : 0] || (bot.is_running ? (en ? 'Running; readiness unverified' : 'Çalışıyor; işlem hazırlığı doğrulanamadı') : (en ? 'Stopped' : 'Durduruldu'));
      runtime.textContent = state + (bot.runtime_detail ? ` · ${window.MT5I18n?.translate?.(bot.runtime_detail) || bot.runtime_detail}` : '');
      runtime.className = 'bot-line ' + (['ORDER_REJECTED','ORDER_UNRESOLVED','ERROR'].includes(bot.runtime_state) ? 'text-rose-300' : ['WAITING_CONNECTION','WAITING_DATA'].includes(bot.runtime_state) ? 'text-amber-300' : 'text-gray-300');
    }

    document.getElementById("bot-disp-hma-p").innerText = bot.hma_period;
    const fastT = document.getElementById("bot-disp-fast-t");
    if (fastT) fastT.innerText = bot.fast_ma_type || "HMA";
    document.getElementById("bot-disp-hma-val").innerText = bot.current_hma || "0.00000";
    document.getElementById("bot-disp-ma2-t").innerText = bot.second_ma_type;
    document.getElementById("bot-disp-ma2-p").innerText = bot.second_ma_period;
    document.getElementById("bot-disp-ma2-val").innerText = bot.current_ma2 || "0.00000";

    const symbolsBadges = document.getElementById("bot-symbols-badges");
    const symbolsCount = document.getElementById("bot-symbols-count");
    // Like an MT5 EA on its chart, the bot trades only the selected symbol.
    const botSym = bot.symbol || 'EURUSD';
    if (symbolsCount) symbolsCount.textContent = botSym;
    if (symbolsBadges) {
      const want = desiredBotSelection();
      const pendingDiff = Object.keys(botSelectionDiff(bot, want)).length > 0;
      const failed = pendingDiff && botFollowError && botFollowError.key === JSON.stringify(want) ? botFollowError.message : '';
      // The symbol name is in the heading; this line only explains why the bot could not follow the selection.
      symbolsBadges.innerHTML = failed ? `<span class="bot-symbol-differs">Bot seçimi izleyemedi: ${escapeHtml(failed)}</span>` : '';
      symbolsBadges.hidden = !failed;
    }
    // A running bot changes only on an explicit selection in this panel (symbol, timeframe, scanner settings).
    // A status poll never re-initialises it: two open tabs with different selections would otherwise reset
    // the bot back and forth (each reset skips the last closed candle, like MT5 OnInit) and it would never trade.
    if (!bot.is_running) syncBotToSelection({}, {announce: false, retry: false});

    document.getElementById("bot-atr-status").textContent = `ATR(${bot.atr_period || 14}): ${Number(bot.current_atr || 0).toFixed(5)} · ER: ${Number(bot.current_er || 0).toFixed(2)} · ${bot.use_atr_filter ? (bot.pending_confirmation ? 'Onay bekleniyor' : 'Filtre açık') : 'Filtre kapalı'} · ${bot.risk_mode === 'ATR' ? 'ATR stop/hedef' : 'Sabit puan stop/hedef'}`;
    const lastSigEl = document.getElementById("bot-last-signal");
    lastSigEl.innerText = bot.last_signal || "YOK";
    if (bot.last_signal === "BUY") {
      lastSigEl.className = "text-emerald-400 font-bold";
    } else if (bot.last_signal === "SELL") {
      lastSigEl.className = "text-rose-400 font-bold";
    } else {
      lastSigEl.className = "text-gray-200";
    }
    document.getElementById("bot-last-signal-time").innerText = bot.last_signal_time || "--:--:--";

    renderBotSignalHistory(bot.signal_history);
    announceBotSignals(bot);
    renderBotProtection(bot);
    renderBotSymbolStatus(bot);
    renderBotLotHint(bot);
    updateBotQuickSummary(bot);

    // Render Logs
    if (bot.logs && bot.logs.length > 0) {
      const logsContainer = document.getElementById("system-logs");
      let logsHtml = "";
      bot.logs.forEach(l => {
        let color = "text-gray-400";
        if (l.level === "SIGNAL") color = "text-cyan-300 font-bold";
        if (l.level === "ERROR") color = "text-rose-400";
        logsHtml += `<div class="${color}">[${l.time}] ${escapeHtml(l.message)}</div>`;
      });
      logsContainer.innerHTML = logsHtml;
    }
  } catch (err) {
    const runtime = document.getElementById('bot-runtime-status');
    if (runtime) { runtime.textContent = 'Bot durumu doğrulanamadı; gösterilen bilgiler eski olabilir.'; runtime.className = 'bot-line text-amber-300'; }
    console.error("fetchBotStatus error:", err);
  }
}

// MT5 lot controls follow the selected broker contract, including 0.001 steps.
function brokerLotRules() {
  const spec = window.MT5Markets?.getSymbol(currentSymbol);
  return spec && [spec.volume_min,spec.volume_max,spec.volume_step].every(n => Number.isFinite(n) && n > 0)
    ? {min:spec.volume_min,max:spec.volume_max,step:spec.volume_step} : {min:0.01,max:Infinity,step:0.01};
}
function updateLotRules() {
  updateBrokerTicketChoices();
  const rules = brokerLotRules();
  for (const id of ['lot-input','pending-volume']) {
    const input = document.getElementById(id);
    if (!input) continue;
    input.min = String(rules.min); input.step = String(rules.step);
    if (Number.isFinite(rules.max)) input.max = String(rules.max);
    else input.removeAttribute?.('max');
  }
  const label = document.getElementById('lot-rules');
  if (label) label.textContent = Number.isFinite(rules.max) ? `${currentSymbol}: min ${rules.min} · max ${rules.max} · ${window.MT5I18n?.language?.() === 'en' ? 'step' : 'adım'} ${rules.step} lot` : '';
}
function adjustLot(delta) {
  const rules = brokerLotRules(), lotInput = document.getElementById('lot-input');
  const value = Number(lotInput.value);
  const units = Math.round((Number.isFinite(value) ? value : rules.min) / rules.step) + Math.sign(delta);
  lotInput.value = String(Number(Math.min(rules.max, Math.max(rules.min, units * rules.step)).toFixed(8)));
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
}
function setLot(val) {
  document.getElementById('lot-input').value = String(val);
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
}
function adjustStopPrice(field, direction) {
  const input = document.getElementById(field + '-input');
  if (!input) return;
  ticketProtectionEdited = true;
  const spec = window.MT5Markets?.getSymbol(currentSymbol);
  const digits = Number.isInteger(spec?.digits) && spec.digits >= 0 && spec.digits <= 8 ? spec.digits : 5;
  const point = Number.isFinite(spec?.point) && spec.point > 0 ? spec.point : Math.pow(10, -digits);
  const current = Number(input.value);
  let next;
  if (!Number.isFinite(current) || current <= 0) {
    const base = Number.isFinite(executableQuote?.bid) && executableQuote.bid > 0 ? executableQuote.bid : 0;
    next = Math.max(0, base + direction * point);
  } else {
    next = Math.max(0, current + direction * point);
  }
  input.value = next > 0 ? next.toFixed(digits) : '0';
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
}
function adjustDeviation(delta) {
  const input = document.getElementById('order-deviation');
  if (!input) return;
  const current = Number(input.value);
  const next = Math.max(0, Math.min(100000, (Number.isInteger(current) ? current : 20) + delta));
  input.value = String(next);
  saveOrderTicketPreferences();
}
// Segment buttons of the order panel drive the (hidden) MT5 order-type select.
function setOrderExecutionType(type) {
  const select = document.getElementById('order-execution-type');
  if (!select || !['MARKET', 'PENDING'].includes(type)) return;
  select.value = type;
  changeOrderExecutionType();
}
function changeOrderExecutionType() {
  const isPending = document.getElementById('order-execution-type')?.value === 'PENDING';
  document.querySelectorAll?.('[data-order-type]').forEach(btn =>
    btn.setAttribute('aria-pressed', String(btn.dataset.orderType === (isPending ? 'PENDING' : 'MARKET'))));
  document.getElementById('pending-order-fields')?.classList.toggle('hidden', !isPending);
  document.getElementById('market-filling-row')?.classList.toggle('hidden', isPending);
  document.getElementById('market-order-buttons')?.classList.toggle('hidden', isPending);
  document.getElementById('pending-order-buttons')?.classList.toggle('hidden', !isPending);
  document.getElementById('market-deviation-row')?.classList.toggle('hidden', isPending);
  if (isPending && typeof changePendingOptions === 'function') changePendingOptions();
}
async function submitUnifiedPendingOrder() {
  const lot = document.getElementById('lot-input')?.value;
  const sl = document.getElementById('sl-input')?.value;
  const tp = document.getElementById('tp-input')?.value;
  const stopMode = document.getElementById('order-stop-mode')?.value;
  if (stopMode && document.getElementById('pending-stop-mode')) document.getElementById('pending-stop-mode').value = stopMode;
  if (lot != null && document.getElementById('pending-volume')) document.getElementById('pending-volume').value = lot;
  if (sl != null && document.getElementById('pending-sl')) document.getElementById('pending-sl').value = sl;
  if (tp != null && document.getElementById('pending-tp')) document.getElementById('pending-tp').value = tp;
  if (typeof submitPendingOrder === 'function') await submitPendingOrder();
}

// Keep the last result per symbol; a broker rejection is not a live session calendar.
const orderNotices = new Map();
let orderPending = false;
const closePending = new Set();
function newOrderId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
}
function unresolvedIntents(symbol = currentSymbol, allOrders = false) {
  const entries = new Map();
  try {
    const orderKey = "order-intent:" + symbol;
    if (localStorage.getItem(orderKey)) entries.set(orderKey, localStorage.getItem(orderKey));
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith("close-intent:") || key.startsWith("close-bulk:") || key.startsWith("managed-intent:") || (allOrders && key.startsWith('order-intent:'))) entries.set(key, localStorage.getItem(key));
    }
  } catch (_) { /* Storage failures are reported by order submission. */ }
  return entries;
}
function tradeActionBusy() {
  return orderPending || orderPreflightPending || closePending.size || (typeof actionLocks !== "undefined" && actionLocks.size) || tradingRiskSettingsBusy;
}
function refreshOrderRecovery() {
  const button = document.getElementById("order-reset-btn");
  if (button) button.hidden = !!tradeActionBusy() || !unresolvedIntents().size;
}
async function clearUncertainOrder() {
  if (tradeActionBusy()) return;
  const symbol = currentSymbol, entries = unresolvedIntents(symbol);
  if (!entries.size) return;
  const approved = await confirmAction({
    title: "Önce MT5 durumunu kontrol edin",
    message: "Sonucu doğrulanamayan talepler var. Yeni talep, önceki işlem gerçekleştiyse ikinci bir işlem açabilir. Bu işlem günlük zarar sınırını kaldırmaz.",
    details: [["Al / sat sembolü", symbol], ["Kontrol edilecek talep", entries.size]],
    acknowledgement: "MT5 açık pozisyonlarını, bekleyen emirleri ve işlem geçmişini kontrol ettim. Kapatma ve yönetim talepleri diğer sembolleri de kapsayabilir.",
    confirmLabel: "Yeni talebe izin ver"
  });
  if (!approved || tradeActionBusy() || currentSymbol !== symbol) return;
  for (const [key, value] of entries) {
    if (localStorage.getItem(key) === value) localStorage.removeItem(key);
  }
  setOrderNotice(symbol, "Yeni talep hazır", "Sonraki tıklama yeni bir işlem talebi oluşturur. Risk kontrolleri geçerlidir.", "neutral");
}

// Closed market: estimated reopening from the broker's own bar history (server clock + local time).
const marketOpenInfo = new Map();
async function refreshMarketOpen(sym = currentSymbol, force = false) {
  if (!sym) return;
  const cached = marketOpenInfo.get(sym);
  if (!force && cached && (cached.loading || Date.now() - cached.at < 60000)) return;
  marketOpenInfo.set(sym, {...(cached || {}), loading: true, at: cached?.at || 0});
  try {
    const res = await fetch(`/api/market/next-open?symbol=${encodeURIComponent(sym)}`);
    const data = await res.json();
    marketOpenInfo.set(sym, res.ok ? {data, at: Date.now()} : {at: Date.now()});
  } catch (_) {
    marketOpenInfo.set(sym, {at: Date.now()});
  }
  if (sym === currentSymbol) renderOrderNotice();
}
function marketOpenText(info) {
  if (!info?.closed) return '';
  if (!info.known || !info.next_open) return 'Açılış saati geçmiş verilerden çıkarılamadı.';
  const offset = Number(info.clock_offset_seconds) || 0;
  const serverNow = Date.now() / 1000 + offset;
  const left = Math.max(0, Math.round(info.next_open - serverNow));
  const at = new Date(info.next_open * 1000), opt = {timeZone: 'UTC'};
  const server = `${at.toLocaleDateString('tr-TR', {...opt, weekday: 'long'})} ${at.toLocaleDateString('tr-TR', {...opt, day: '2-digit', month: '2-digit'})} ${at.toLocaleTimeString('tr-TR', {...opt, hour: '2-digit', minute: '2-digit'})}`;
  const local = new Date((info.next_open - offset) * 1000).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'});
  if (info.overdue || left === 0) return `Beklenen açılış: ${server} (sunucu saati). Broker henüz fiyat göndermedi; tatil olabilir.`;
  const d = Math.floor(left / 86400), h = Math.floor(left % 86400 / 3600), m = Math.ceil(left % 3600 / 60);
  const parts = [d ? `${d} gün` : '', h ? `${h} sa` : '', m ? `${m} dk` : ''].filter(Boolean).join(' ') || '1 dk';
  return `Tahmini açılış: ${server} (sunucu saati · senin saatinle ${local}) · ${parts} sonra. Brokerın geçmiş haftalardaki kapanışlarına göre hesaplandı; tatillerde değişebilir.`;
}
function renderOrderNotice(flash = false) {
  const box = document.getElementById("order-notice");
  if (!box) return;
  const openInfo = marketOpenInfo.get(currentSymbol)?.data;
  const closedNow = !chartSelectionBlocked && openInfo?.closed && openInfo.symbol === currentSymbol;
  let notice = chartSelectionBlocked ? {
    title: 'Grafikteki ürün için MT5 eşleşmesi yok', message: 'Al/Sat için hızlı emir bölümündeki MT5 sembolünü seçin. Grafik ürünü işlem hesabında eşlenemedi.', tone: 'error'
  } : orderNotices.get(currentSymbol) || (closedNow ? {
    title: `${currentSymbol} · Piyasa kapalı`, message: 'Bu sembolde işlem seansı kapalı.', tone: 'warning'
  } : {
    title: "İşlem durumu", message: "Emir sonucu ve işlem uyarıları burada gösterilir.", tone: "neutral"
  });
  if (closedNow) notice = {...notice, message: `${notice.message} ${marketOpenText(openInfo)}`};
  refreshOrderRecovery();
  box.dataset.tone = notice.tone;
  document.getElementById("order-notice-title").textContent = notice.title;
  document.getElementById("order-notice-message").textContent = notice.message;
  box.classList.remove("order-notice-flash");
  if (flash) {
    void box.offsetWidth; // Restart the finite animation on each rejected attempt.
    box.classList.add("order-notice-flash");
  }
}

function setOrderNotice(symbol, title, message, tone, flash = false, quoteDiagnostic = false) {
  orderNotices.set(symbol, {title: `${symbol} · ${title}`, message, tone, quoteDiagnostic});
  if (/piyasa kapalı/i.test(title)) refreshMarketOpen(symbol, true);
  if (symbol === currentSymbol) renderOrderNotice(flash);
}

function refreshQuoteOrderNotice(tick) {
  const symbol = tick.symbol || currentSymbol;
  const notice = orderNotices.get(symbol), status = tick.quote_status;
  // Only refresh a definitive quote rejection. Unknown fills and other order
  // results must remain visible until broker reconciliation resolves them.
  if (!notice?.quoteDiagnostic || !status) return;
  if (status.broker_connected === true && status.state === 'fresh') {
    setOrderNotice(symbol, 'Bağlantı ve fiyat doğrulandı',
      'MT5 broker bağlantısı açık. Fiyat güncel. Önceki emir gönderilmedi; yeni işlem için emir vermeniz gerekir.',
      'neutral');
  } else {
    const [title] = orderErrorMessage({error:status.message});
    setOrderNotice(symbol, title, status.message + ' Önceki emir gönderilmedi.',
      status.broker_connected === true && status.state === 'stale' ? 'warning' : 'error', false, true);
  }
}

function orderErrorMessage(data) {
  const raw = typeof data.detail === "string" ? data.detail : (data.error || "Emir reddedildi. Girdiğiniz değerleri kontrol edin.");
  const code = Number(data.retcode);
  if (data.uncertain) return ["Emir sonucu belirsiz", "Tekrar göndermeden önce açık pozisyonları kontrol edin."];
  if (data.reason_code === "trading_halted") return ["Yeni emirler durduruldu", raw];
  if (raw.includes("MT5 broker bağlantısı kesik.")) return ["MT5 broker bağlantısı kesik", raw];
  if (raw.includes("MT5 broker bağlantısı doğrulanamadı.")) return ["MT5 bağlantısı doğrulanamadı", raw];
  if (raw.startsWith("Son fiyat ")) return ["Güncel fiyat bekleniyor", raw];
  if (raw.startsWith("Fiyat zamanı sunucu")) return ["Fiyat saatini kontrol edin", raw];
  if (raw.startsWith("Broker geçerli fiyat")) return ["Geçerli fiyat bekleniyor", raw];
  if (code === 10018 || /market closed/i.test(raw))
    return ["Son emir: piyasa kapalı", "Bu sembolde işlem seansı kapalı."];
  if (code === 10027 || /autotrading disabled/i.test(raw))
    return ["İşlem izni kapalı", "MT5 Algo Trading ve Python API işlem izinlerini kontrol edin."];
  if (code === 10019 || /not enough money|no money/i.test(raw))
    return ["Yetersiz teminat", "Kullanılabilir teminatınızı ve lot miktarını kontrol edin."];
  if (code === 10016 || /invalid stops/i.test(raw))
    return ["SL / TP uygun değil", "Stop Loss ve Take Profit mesafelerini kontrol edin."];
  if (code === 10014 || /invalid volume/i.test(raw))
    return ["Lot miktarı uygun değil", "Bu sembolün minimum lot ve lot adımını kontrol edin."];
  if (data.requote || code === 10004 || code === 10020) {
    const quote=data.broker_quote;
    return ['Broker fiyatı değişti',`Emir gerçekleşmedi. ${quote ? `Yeni Bid ${quote.bid} · Ask ${quote.ask}. ` : ''}Güncel fiyatı kontrol edip yeniden onaylayın.`];
  }
  if (code === 10017 || /trade disabled/i.test(raw))
    return ["İşlem devre dışı", "Broker bu sembol veya hesap için işlem izni vermiyor."];
  return ["Emir gönderilemedi", raw];
}

const TICKET_PREFERENCES = {
  'order-rule-mode':['MT5','PANEL'], 'order-filling':['AUTO','FOK','IOC','RETURN'],
  'order-stop-mode':['price','points'], 'pending-stop-mode':['price','points']
};
function seedNettingProtection(positions) {
  if(ticketProtectionEdited||!currentAccountIdentity||![0,1].includes(currentAccountMarginMode)
      ||manualOrderMode()!=='MT5'||document.getElementById('order-stop-mode')?.value!=='price')return;
  const matches=positions.filter(row=>row.symbol===currentSymbol);
  if(matches.length!==1)return;
  const p=matches[0],key=JSON.stringify([currentAccountIdentity,currentSymbol,p.identifier||p.ticket]);
  if(nettingProtectionSeed===key)return;
  const sl=document.getElementById('sl-input'),tp=document.getElementById('tp-input');
  if(!sl||!tp||Number(sl.value)!==0||Number(tp.value)!==0)return;
  sl.value=String(p.sl||0);tp.value=String(p.tp||0);nettingProtectionSeed=key;
  if(typeof scheduleTradePreview==='function')scheduleTradePreview();
}
function ticketPreferencesKey() {
  return currentAccountIdentity ? 'mt5.ticket.preferences:'+JSON.stringify(currentAccountIdentity) : null;
}
function saveOrderTicketPreferences() {
  const key = ticketPreferencesKey();
  if (!key) return;
  const values = {};
  for (const id of Object.keys(TICKET_PREFERENCES)) values[id] = document.getElementById(id)?.value;
  const deviation = Number(document.getElementById('order-deviation')?.value);
  if (Number.isInteger(deviation) && deviation >= 0 && deviation <= 100000) values['order-deviation'] = deviation;
  try { localStorage.setItem(key,JSON.stringify(values)); } catch (_) {}
}
function restoreOrderTicketPreferences() {
  let values={};
  try { values=JSON.parse(localStorage.getItem(ticketPreferencesKey())||'{}'); } catch (_) {}
  for (const [id, choices] of Object.entries(TICKET_PREFERENCES)) {
    const input = document.getElementById(id);
    if (input) input.value = choices.includes(values?.[id]) ? values[id] : choices[0];
  }
  const deviation=document.getElementById('order-deviation');
  if (deviation) deviation.value=String(Number.isInteger(values?.['order-deviation'])&&values['order-deviation']>=0&&values['order-deviation']<=100000 ? values['order-deviation'] : 20);
  changeOrderStopMode(); changeOrderStopMode(true); changeOrderRules();
}
function updateBrokerTicketChoices() {
  const spec=window.MT5Markets?.getSymbol(currentSymbol);
  if (!spec) return;
  const configure=(id, allowed)=>{
    const select=document.getElementById(id);
    if (!select?.options) return;
    for(const option of select.options) option.disabled=!allowed(option.value);
  };
  if(Number.isInteger(spec.order_mode)&&spec.order_mode>0) configure('pending-type',kind=>!!(spec.order_mode&(kind.endsWith('STOP_LIMIT')?8:kind.endsWith('LIMIT')?2:4)));
  if(Number.isInteger(spec.expiration_mode)&&spec.expiration_mode>0) configure('pending-time-type',type=>!!(spec.expiration_mode&(1<<({GTC:0,DAY:1,SPECIFIED:2,SPECIFIED_DAY:3}[type]))));
  if([0,1,2,3].includes(spec.execution_mode)) configure('order-filling',policy=>policy==='AUTO'||spec.execution_mode<2||(policy==='RETURN'?spec.execution_mode===3:!!(spec.filling_mode&(policy==='FOK'?1:2))));
}

function manualOrderMode() {
  return document.getElementById('order-rule-mode')?.value === 'PANEL' ? 'PANEL' : 'MT5';
}
function changeOrderRules() {
  saveOrderTicketPreferences();
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
}

// MT5 ticket: explicit price levels or point distances; zero means no level.
function readOrderProtection(pending = false) {
  const id = pending ? 'pending-' : '';
  const mode = document.getElementById(pending ? 'pending-stop-mode' : 'order-stop-mode')?.value || 'points';
  const value = name => {
    const raw = String(document.getElementById(pending ? id + name : name + '-input')?.value ?? '').trim();
    return raw === '' && mode === 'price' ? 0 : raw === '' ? NaN : Number(raw);
  };
  const sl = value('sl'), tp = value('tp');
  const deviation = Number(document.getElementById('order-deviation')?.value ?? 20);
  const selectedFilling = document.getElementById('order-filling')?.value || 'AUTO';
  const options = {manual_mode:manualOrderMode(),filling_policy:['AUTO','FOK','IOC','RETURN'].includes(selectedFilling) ? selectedFilling : 'AUTO'};
  if (![sl,tp].every(n => Number.isFinite(n) && n >= 0 && (mode === 'price' || Number.isInteger(n)))
      || !Number.isInteger(deviation) || deviation < 0 || deviation > 100000) return null;
  return mode === 'price' ? {sl_points:0,tp_points:0,sl_price:sl,tp_price:tp,deviation_points:deviation,...options}
    : {sl_points:sl,tp_points:tp,deviation_points:deviation,...options};
}
function changeOrderStopMode(pending = false) {
  saveOrderTicketPreferences();
  // Never reinterpret a point distance as an absolute price or silently widen SL.
  for (const name of ['sl','tp']) {
    const input = document.getElementById(pending ? 'pending-' + name : name + '-input');
    if (input) {input.value = '0'; input.step = (document.getElementById(pending ? 'pending-stop-mode' : 'order-stop-mode')?.value === 'price') ? 'any' : '1';}
  }
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();
}

// Submit Buy/Sell Order
async function submitOrder(type) {
  if (orderPending || orderPreflightPending || accountSwitching) return;
  if (!['BUY','SELL'].includes(type)) return;
  if (accountVerificationKnown && !currentAccountIdentity) {
    setOrderNotice(currentSymbol, 'Hesap doğrulanamadı', 'Broker hesap bağlantısını doğrulayın. Emir gönderilmedi.', 'error');
    return;
  }
  if (window.MT5Markets?.isSelectionVerified(currentSymbol, type) === false) {
    setOrderNotice(currentSymbol, 'Ürün işlem için doğrulanamadı', window.MT5Markets.selectionIssue(currentSymbol, type), 'error');
    return;
  }
  const selectedSymbol = currentSymbol;
  if (orderSymbolSource === 'chart' && !await verifyChartOrderSymbol(type)) {
    setOrderNotice(currentSymbol, 'Grafik sembolünü kontrol edin', 'Grafik ve emir sembolü doğrulanamadı veya değişti. Sembolü kontrol edip yeniden deneyin; emir gönderilmedi.', 'error');
    return;
  }
  if (currentSymbol !== selectedSymbol || window.MT5Markets?.isSelectionVerified(currentSymbol, type) === false) return;
  if (chartSelectionBlocked) {
    setOrderNotice(currentSymbol, 'Grafik ürünü eşleşmiyor', 'Emir göndermek için panelden bir MT5 sembolü seçin.', 'error');
    return;
  }
  if (orderPending) return;
  const symbol = currentSymbol;
  const volume = Number(document.getElementById("lot-input").value);
  if (!Number.isFinite(volume) || volume <= 0) {
    setOrderNotice(symbol, "Geçersiz lot", "Sıfırdan büyük bir lot miktarı girin.", "error");
    return;
  }
  const protection = readOrderProtection();
  if (!protection) {
    setOrderNotice(symbol, 'Geçersiz SL / TP veya sapma', 'Fiyat seviyelerini veya tam sayı puan mesafelerini kontrol edin.', 'error');
    return;
  }
  const {sl_points:sl, tp_points:tp} = protection;
  if (protection.manual_mode !== 'MT5' && currentAccountType === 'REAL' && !(protection.sl_price ?? sl)) {
    setOrderNotice(symbol, 'Uygulama risk kuralı', 'Bu panelde gerçek hesapta yeni emir için Stop Loss zorunlu.', 'error');
    return;
  }
  const quoteIssue = quoteOrderIssue();
  if (quoteIssue) {
    setOrderNotice(symbol, 'Emir girişi bekliyor', quoteIssue + ' Emir gönderilmedi.', 'warning');
    updateOrderButtons();
    return;
  }
  let intent;
  try {
    const stored = localStorage.getItem("order-intent:" + symbol);
    if (stored) {
      intent = JSON.parse(stored);
      if (currentAccountIdentity && JSON.stringify(intent.expected_account) !== JSON.stringify(currentAccountIdentity)) {
        setOrderNotice(symbol, 'Önceki emir hesabı doğrulanmalı', 'Kayıtlı talep bu hesaba ait değil veya hesap kimliği bilinmiyor. Önce MT5 durumunu kontrol edin.', 'error');
        return;
      }
      if (intent.order_type !== type || intent.volume !== volume || intent.sl_points !== sl || intent.tp_points !== tp || (intent.sl_price ?? null) !== (protection.sl_price ?? null)
          || (intent.tp_price ?? null) !== (protection.tp_price ?? null) || (intent.deviation_points ?? 20) !== protection.deviation_points
          || (intent.manual_mode ?? 'PANEL') !== protection.manual_mode || (intent.filling_policy ?? 'AUTO') !== protection.filling_policy) {
        setOrderNotice(symbol, "Önceki emir doğrulanmalı", "MT5 durumunu kontrol edip ‘Kontrol ettim’ düğmesini kullanın.", "error");
        return;
      }
    } else {
      intent = {request_id: newOrderId(), symbol, order_type: type, volume, ...protection, comment: "Volta Web Terminal", ...tradingAccountPayload()};
      localStorage.setItem("order-intent:" + symbol, JSON.stringify(intent));
    }
  } catch (_) {
    setOrderNotice(symbol, "Emir gönderilemedi", "Emir kimliğini saklamak için tarayıcı depolama izni gerekli.", "error");
    return;
  }
  orderPending = true;
  const buttons = [document.getElementById("order-buy-btn"), document.getElementById("order-sell-btn")];
  buttons.forEach(btn => { if (btn) btn.disabled = true; });
  setOrderNotice(symbol, "Emir gönderiliyor", `${type === "BUY" ? "Alış" : "Satış"} · ${volume} lot · Broker yanıtı bekleniyor.`, "pending");
  try {
    const {response:res,data} = await tradeRequest('/api/order/open', intent);
    if ((data.request_id && !data.uncertain && !data.pending) || data.reason_code === "trading_halted"
        || (data.reason_code === 'untrusted_origin' && data.not_submitted === true))
      localStorage.removeItem("order-intent:" + symbol);
    if (res.ok && data.success) {
      const title = data.partial ? "Emir kısmen gerçekleşti" : (data.pending || data.retcode === 10008 ? "Emir kabul edildi; gerçekleşme bekleniyor" : "Emir gerçekleşti");
      setOrderNotice(symbol, title, `Bilet #${data.ticket} · Açık pozisyonlardan durumu takip edebilirsiniz.`, data.partial || data.pending ? "warning" : "success");
      fetchPositions(true);
      fetchAccount(true);
    } else {
      const [title, message] = orderErrorMessage(data);
      const quoteDiagnostic = !!data.request_id && !data.uncertain && !data.pending &&
        message.includes('Güncel fiyat ve broker bağlantısı doğrulanmadan işlem yapılamaz.');
      setOrderNotice(symbol, title, message, "error", true, quoteDiagnostic);
    }
  } catch (err) {
    setOrderNotice(symbol, "Yanıt alınamadı", "Emir sonucu belirsiz olabilir. Yeniden göndermeden önce açık pozisyonları kontrol edin.", "error", true);
  } finally {
    orderPending = false;
    refreshOrderRecovery();
    updateOrderButtons();
  }
}

// Close Single Position (Instant 1-Click)
// What actually happened on a close, as MT5's History shows it, plus where the time went.
function closeResultText(data, position) {
  const r = data.realized, ex = data.execution || {};
  const digits = Number.isInteger(position?.digits) ? position.digits : 5;
  const parts = [];
  if (r && Number.isFinite(r.price)) parts.push(`${r.volume ?? ''} lot @ ${Number(r.price).toFixed(digits)}`.trim());
  if (r && Number.isFinite(r.net)) parts.push(`net ${r.net >= 0 ? '+' : ''}${formatMoney(r.net)}`
    + (r.commission || r.swap || r.fee ? ` (kâr ${formatMoney(r.profit)}, komisyon/swap ${formatMoney(r.commission + r.swap + r.fee)})` : ''));
  // Requested = Bid for closing a BUY, Ask for closing a SELL; positive slippage = against you.
  if (r && Number.isFinite(ex.requested_price) && Number(ex.point) > 0) {
    const closingBuy = position ? position.type === 'BUY' : ex.requested_price === ex.bid;
    const slip = Math.round(((closingBuy ? ex.requested_price - r.price : r.price - ex.requested_price) / ex.point) * 10) / 10;
    parts.push(`istenen ${Number(ex.requested_price).toFixed(digits)} · kayma ${slip > 0 ? slip + ' puan aleyhe' : slip < 0 ? -slip + ' puan lehe' : 'yok'}`);
  }
  const times = [Number.isFinite(data.queue_ms) ? `sırada ${Math.round(data.queue_ms)} ms` : '',
                 Number.isFinite(data.execution_ms) ? `gönderim ${Math.round(data.execution_ms)} ms` : ''].filter(Boolean);
  if (times.length) parts.push(times.join(' + '));
  return parts.join(' · ');
}
// Close button state: the click always gets visible feedback (never a silent no-op).
function setCloseButtonBusy(ticket, busy) {
  document.querySelectorAll(`[data-close-ticket="${Number(ticket)}"]`).forEach((b) => {
    b.disabled = busy;
    b.classList.toggle('is-closing', busy);
    b.setAttribute('aria-busy', busy ? 'true' : 'false');
    b.textContent = busy ? 'Kapatılıyor…' : 'Kapat';
  });
}
async function closePosition(ticket) {
  ticket = Number(ticket);
  if (closePending.has(ticket)) return showToast(`⏳ #${ticket} zaten kapatılıyor; broker sonucu bekleniyor.`, 'info');
  if (accountSwitching) return showToast('Hesap değiştiriliyor; birkaç saniye sonra tekrar deneyin.', 'warning');
  const position = executionPositions.get(Number(ticket)) || executionPositions.get(ticket);
  if (accountVerificationKnown && !currentAccountIdentity) return showToast('Kapatmadan önce broker hesap bağlantısını doğrulayın.', 'error');
  closePending.add(ticket);
  setCloseButtonBusy(ticket, true);
  const started = Date.now();
  try {
    // MT5 close button: every click is a new close request at the current price. The bridge only
    // closes a position that is still open, so a repeat after an unclear reply cannot close twice.
    const key = "close-intent:" + ticket;
    const requestId = newOrderId();
    localStorage.setItem(key, requestId);
    const {response:res,data} = await tradeRequest('/api/order/close',
      {ticket,request_id:requestId,...tradingAccountPayload()});
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(key);
    if (res.ok && data.success) {
      const message = data.pending ? `#${ticket} kapatma talebi kabul edildi; broker sonucu bekleniyor.`
        : data.partial ? `#${ticket} pozisyonu kısmen kapatıldı; kalan lotu kontrol edin.`
        : `✅ #${ticket} kapatıldı · ${closeResultText(data, position) || 'sonuç MT5 geçmişinde'}`;
      showToast(message, data.pending || data.partial ? "warning" : "success");
      if (position?.symbol && !data.pending && !data.partial)
        setOrderNotice(position.symbol, `#${ticket} kapatıldı`, closeResultText(data, position) || 'Pozisyon kapandı.', 'neutral');
      fetchPositions(true);
      fetchAccount(true);
      fetchHistory();
      if (currentPositionTab === "reports") fetchReports();
    } else {
      const err = data.detail || data.error || "";
      if (err.includes("10018") || err.includes("Market closed")) {
        showToast(`⚠️ Broker bu sembol için piyasanın kapalı olduğunu bildirdi. İşlem seansını MT5 üzerinden kontrol edin.`, "error");
      } else {
        showToast(`❌ Kapatma Hatası: ${err}`, "error");
      }
    }
  } catch (err) {
    showToast(err?.name === 'AbortError'
      ? `#${ticket} için ${Math.round((Date.now() - started) / 1000)} sn içinde yanıt gelmedi; pozisyon listesi yenileniyor, tekrar basmadan önce kontrol edin.`
      : "Kapatma sonucu belirsiz; MT5 durumunu kontrol edin.", "error");
    fetchPositions(true);
  } finally {
    closePending.delete(ticket);
    setCloseButtonBusy(ticket, false);
    refreshOrderRecovery();
  }
}

// Close Filtered Positions (all, profit, loss) - Instant 1-Click
async function refreshTradingStatus() {
  try {
    const response = await fetch('/api/trading/status');
    if (!response.ok) return;
    const state = await response.json();
    const en = window.MT5I18n?.language() === 'en';
    const button = document.getElementById('resume-trading-btn');
    if (button) {
      button.classList.toggle('hidden', !state.new_orders_halted);
      button.textContent = state.new_orders_halted
        ? (en ? 'Trading Risk · Locked' : 'İşlem Riski · Kilitli')
        : state.daily_limit_enabled
          ? (en ? 'Trading Risk · Limited' : 'İşlem Riski · Limitli')
          : (en ? 'Trading Risk · Free' : 'İşlem Riski · Serbest');
    }
    const warning = document.getElementById('trading-halt-message');
    if (warning) {
      warning.classList.toggle('hidden', !state.new_orders_halted);
      warning.textContent = state.new_orders_halted
        ? (en ? 'New orders are locked. Click to manage the lock and daily limit.' : 'Yeni emirler kilitli. Kilidi açmak veya günlük limiti yönetmek için tıklayın.')
        : '';
    }
  } catch (_) {}
}

let tradingRiskDialogState = null;
let tradingRiskDialogLoad = 0;
let tradingRiskSettingsBusy = false;
function tradingRiskAccountPayload(state) {
  const risk = state?.risk;
  return Number.isSafeInteger(risk?.login) && typeof risk?.server === 'string' && ['DEMO','REAL'].includes(risk?.account_type)
    ? {expected_account:[risk.login,risk.server,risk.account_type]} : tradingAccountPayload();
}
function tradingRiskRequestCurrent(state, payload) {
  return state === tradingRiskDialogState && document.getElementById('trading-risk-dialog').open && !accountSwitching
    && (!accountVerificationKnown || JSON.stringify(payload) === JSON.stringify(tradingAccountPayload()));
}
function renderTradingRiskDialog() {
  const state = tradingRiskDialogState;
  if (!state) return;
  const {risk, halted, admin} = state;
  const en = window.MT5I18n?.language() === 'en';
  const money = amount => `${Number(amount).toLocaleString(en ? 'en-US' : 'tr-TR', {maximumFractionDigits:2})} ${risk.currency}`;
  document.getElementById('trading-risk-account').textContent = `#${risk.login} · ${risk.server} · ${risk.currency}`;
  document.getElementById('trading-risk-loss').textContent = money(risk.daily_loss);
  document.getElementById('trading-risk-limit').textContent = money(risk.daily_loss_limit) +
    (risk.daily_limit_enabled ? '' : (en ? ' (off)' : ' (kapalı)'));
  document.getElementById('trading-risk-limit-input').value = risk.daily_loss_limit;
  document.getElementById('trading-risk-limit-input').disabled = !admin;
  document.getElementById('trading-risk-save').disabled = !admin || tradingRiskSettingsBusy;
  const lockButton = document.getElementById('trading-risk-resume');
  lockButton.disabled = !admin || tradingRiskSettingsBusy;
  lockButton.textContent = halted ? (en ? 'Unlock for free trading' : 'Kilidi Aç · Serbest İşlem')
    : (en ? 'Lock new orders' : 'Yeni Emirleri Kilitle');
  document.getElementById('trading-risk-explanation').textContent = !admin
    ? (en ? 'Only an administrator can change the limit or trading lock.' : 'Limiti ve işlem kilidini yalnızca yönetici değiştirebilir.')
    : halted
      ? (en ? 'New orders are locked. Unlocking also turns off the daily loss check for this account.' : 'Yeni emirler kilitli. Kilidi açmak bu hesapta günlük zarar kontrolünü de kapatır.')
      : risk.daily_limit_enabled
        ? (en ? 'Orders are allowed while the daily loss check remains active.' : 'Günlük zarar kontrolü açıkken yeni emirlere izin veriliyor.')
        : (en ? 'Free trading is active. The daily loss check is off for this account.' : 'Serbest işlem açık. Bu hesapta günlük zarar kontrolü kapalı.');
}

async function openTradingRiskDialog() {
  const dialog = document.getElementById('trading-risk-dialog');
  if (!dialog) return;
  const load = ++tradingRiskDialogLoad;
  const generation = accountSessionGeneration, expectedAccount = tradingAccountPayload();
  tradingRiskDialogState = null;
  document.getElementById('trading-risk-account').textContent = window.MT5I18n?.language() === 'en' ? 'Checking account…' : 'Hesap kontrol ediliyor…';
  document.getElementById('trading-risk-loss').textContent = '—';
  document.getElementById('trading-risk-limit').textContent = '—';
  document.getElementById('trading-risk-explanation').textContent = '';
  document.getElementById('trading-risk-save').disabled = true;
  document.getElementById('trading-risk-resume').disabled = true;
  dialog.showModal();
  try {
    const [riskResponse, statusResponse, operatorResponse] = await Promise.all([
      fetch('/api/risk/status'), fetch('/api/trading/status'), fetch('/api/auth/me')]);
    if (!riskResponse.ok || !statusResponse.ok || !operatorResponse.ok) throw new Error('Hesap riski doğrulanamadı.');
    const [risk, status, operator] = await Promise.all([riskResponse.json(), statusResponse.json(), operatorResponse.json()]);
    if (!dialog.open || load !== tradingRiskDialogLoad) return;
    if (generation !== accountSessionGeneration || JSON.stringify(expectedAccount) !== JSON.stringify(tradingAccountPayload())) {
      throw new Error('Hesap değişti; işlem riskini yeniden açıp doğrulayın.');
    }
    tradingRiskDialogState = {risk, halted:status.new_orders_halted, admin:operator.role === 'ADMIN'};
    if (!tradingRiskRequestCurrent(tradingRiskDialogState, tradingRiskAccountPayload(tradingRiskDialogState))) {
      tradingRiskDialogState = null;
      throw new Error('Hesap değişti; işlem riskini yeniden açıp doğrulayın.');
    }
    renderTradingRiskDialog();
  } catch (error) {
    if (dialog.open && load === tradingRiskDialogLoad) document.getElementById('trading-risk-explanation').textContent = error.message;
  }
}

async function saveTradingDailyLimit() {
  const state = tradingRiskDialogState;
  if (!state?.admin || tradingRiskSettingsBusy) return;
  const expectedAccount = tradingRiskAccountPayload(state);
  if (!tradingRiskRequestCurrent(state, expectedAccount)) {
    document.getElementById('trading-risk-explanation').textContent = 'Hesap değişti; işlem riskini yeniden açıp doğrulayın.';
    return;
  }
  const amount = Number(document.getElementById('trading-risk-limit-input').value);
  if (!Number.isFinite(amount) || amount <= 0 || amount > 1000000) {
    document.getElementById('trading-risk-explanation').textContent = '0 ile 1.000.000 arasında geçerli bir limit girin.';
    return;
  }
  if (amount === state.risk.daily_loss_limit) return;
  const en = window.MT5I18n?.language() === 'en';
  tradingRiskSettingsBusy = true;
  document.getElementById('trading-risk-save').disabled = true;
  document.getElementById('trading-risk-resume').disabled = true;
  try {
    let acknowledgeRisk = false;
    if (amount > state.risk.daily_loss_limit) {
      acknowledgeRisk = await confirmAction({
        title: en ? 'Increase daily loss limit' : 'Günlük zarar limitini yükselt',
        message: en ? 'Increasing this limit can allow new orders after the safety lock is released.' : 'Bu limiti yükseltmek, güvenlik kilidi kaldırıldıktan sonra yeni emirlere izin verebilir.',
        details: [[en ? 'Current loss' : 'Bugünkü zarar', `${state.risk.daily_loss} ${state.risk.currency}`],
          [en ? 'Current limit' : 'Mevcut limit', `${state.risk.daily_loss_limit} ${state.risk.currency}`],
          [en ? 'New limit' : 'Yeni limit', `${amount} ${state.risk.currency}`]],
        acknowledgement: en ? 'I reviewed the account and accept the higher daily loss limit.' : 'Hesabı kontrol ettim ve daha yüksek günlük zarar limitini kabul ediyorum.',
        confirmLabel: en ? 'Save higher limit' : 'Yüksek limiti kaydet'
      });
      if (!acknowledgeRisk) return;
    }
    if (!tradingRiskRequestCurrent(state, expectedAccount)) throw new Error('Onay sırasında hesap değişti; limit kaydedilmedi.');
    const {response,data} = await tradeRequest('/api/risk/daily-limit',
      {amount,acknowledge_risk:acknowledgeRisk,...expectedAccount});
    if (!tradingRiskRequestCurrent(state, expectedAccount)) return;
    if (!response.ok) throw new Error(data.detail || 'Limit kaydedilemedi.');
    state.risk = data;
    renderTradingRiskDialog();
  } catch (error) {
    if (state === tradingRiskDialogState) document.getElementById('trading-risk-explanation').textContent = error.message;
  } finally {
    tradingRiskSettingsBusy = false;
    if (state === tradingRiskDialogState) {
      document.getElementById('trading-risk-save').disabled = !state.admin;
      document.getElementById('trading-risk-resume').disabled = !state.admin;
    }
  }
}

async function toggleTradingLock() {
  const state = tradingRiskDialogState;
  if (!state?.admin || tradingRiskSettingsBusy) return;
  const expectedAccount = tradingRiskAccountPayload(state);
  if (!tradingRiskRequestCurrent(state, expectedAccount)) {
    document.getElementById('trading-risk-explanation').textContent = 'Hesap değişti; işlem riskini yeniden açıp doğrulayın.';
    return;
  }
  const en = window.MT5I18n?.language() === 'en', locked = !state.halted;
  tradingRiskSettingsBusy = true;
  document.getElementById('trading-risk-save').disabled = true;
  document.getElementById('trading-risk-resume').disabled = true;
  try {
    if (!locked) {
      const approved = await confirmAction({
        title: en ? 'Unlock for free trading' : 'Kilidi aç ve serbest işlem yap',
        message: en ? 'New orders will be allowed even though the daily loss limit has been exceeded. Broker margin and order rules still apply.'
          : 'Günlük zarar limiti aşılmış olsa bile yeni emirlere izin verilecek. Broker teminatı ve emir kuralları geçerli kalır.',
        details: [[en ? 'Account' : 'Hesap', `#${state.risk.login} · ${state.risk.account_type}`],
          [en ? 'Daily loss' : 'Bugünkü zarar', `${state.risk.daily_loss} ${state.risk.currency}`],
          [en ? 'Daily limit' : 'Günlük limit', `${state.risk.daily_loss_limit} ${state.risk.currency}`]],
        acknowledgement: en ? 'I reviewed the account and accept trading without the daily loss limit.'
          : 'Hesabı kontrol ettim; günlük zarar sınırı olmadan işlem yapmayı kabul ediyorum.',
        confirmLabel: en ? 'Unlock trading' : 'Kilidi aç'
      });
      if (!approved) return;
    }
    if (!tradingRiskRequestCurrent(state, expectedAccount)) throw new Error('Onay sırasında hesap değişti; işlem kilidi değiştirilmedi.');
    const {response,data} = await tradeRequest('/api/trading/lock', {locked,acknowledge_unlimited:!locked,...expectedAccount});
    if (!tradingRiskRequestCurrent(state, expectedAccount)) return;
    if (!response.ok) throw new Error(data.detail || 'İşlem kilidi değiştirilemedi.');
    state.halted = data.new_orders_halted;
    state.risk.daily_limit_enabled = data.daily_limit_enabled;
    renderTradingRiskDialog();
    refreshTradingStatus();
  } catch (error) {
    if (state === tradingRiskDialogState) document.getElementById('trading-risk-explanation').textContent = error.message || 'İşlem kilidi değiştirilemedi.';
  } finally {
    tradingRiskSettingsBusy = false;
    if (state === tradingRiskDialogState) {
      document.getElementById('trading-risk-save').disabled = !state.admin;
      document.getElementById('trading-risk-resume').disabled = !state.admin;
    }
  }
}

async function closeFilteredPositions(filterType) {
  if (closePending.has("bulk") || accountSwitching) return;
  if (!['all','profit','loss'].includes(filterType)) return;
  if (accountVerificationKnown && !currentAccountIdentity) return showToast('Kapatmadan önce broker hesap bağlantısını doğrulayın.', 'error');
  closePending.add("bulk");
  let label = "Tüm pozisyonlar";
  let endpoint = "/api/order/close-all";
  if (filterType === "profit") {
    label = "Kârdaki pozisyonlar";
    endpoint = "/api/order/close-profit";
  } else if (filterType === "loss") {
    label = "Zarardaki pozisyonlar";
    endpoint = "/api/order/close-loss";
  }

  try {
    showToast(`${label} kapatılıyor...`, "info");
    const key = "close-bulk:" + filterType;
    const requestId = localStorage.getItem(key) || newOrderId();
    localStorage.setItem(key, requestId);
    const {response:res,data} = await tradeRequest(endpoint,
      {request_id:requestId,...tradingAccountPayload()},60000);
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(key);
    if (res.ok) {
      if (data.pending || data.uncertain || data.partial) {
        showToast(`${data.closed_count || 0} pozisyon kapatıldı. ${data.uncertain ? 'Sonuç belirsiz; MT5 durumunu kontrol edin.' : data.pending ? 'Kapatma taleplerinin broker sonucu bekleniyor.' : 'Bazı pozisyonlar kısmen kapandı; kalan lotları kontrol edin.'}`, 'warning');
      } else if (!data.success || (data.errors && data.errors.length)) {
        showToast(`${data.closed_count || 0} pozisyon kapatıldı. Tamamlanamayanlar: ${(data.errors || [data.error || "Sonuç belirsiz"]).join("; ")}`, "error");
      } else if (data.total_matched === 0 && !data.cancelled_count) {
        showToast("Kapatılacak uygun pozisyon bulunamadı.", "info");
      } else {
        showToast(`${data.closed_count} pozisyon kapatıldı; ${data.cancelled_count || 0} bekleyen emir iptal edildi.`, "success");
      }
      fetchPositions(true);
      fetchAccount(true);
      fetchHistory();
      if (currentPositionTab === "reports") fetchReports();
    } else {
      showToast(`❌ Hata: ${data.detail || data.error || "İşlem başarısız"}`, "error");
    }
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
  } finally {
    closePending.delete("bulk");
    refreshOrderRecovery();
    refreshTradingStatus();
  }
}

function closeAllPositions() {
  closeFilteredPositions("all");
}

// Signals sent to the bot: newest first, live status from the open-positions feed.
let latestBotSignalHistory = [];
function editBotHistoryEntry(ticket) {
  if (typeof showPositionEditor !== 'function') return showToast('Pozisyon düzenleyici yüklenemedi.', 'error');
  showPositionEditor(ticket);
}
async function deleteBotHistoryEntry(id) {
  const row = (latestBotSignalHistory || []).find(r => r.id === id);
  const open = row && row.success && row.ticket != null && (executionPositions.get(Number(row.ticket)) || executionPositions.get(row.ticket));
  if (open && !confirm(`${row.symbol} pozisyonu hâlâ açık. Kayıt listeden silinir, pozisyon MT5'te açık kalır. Devam edilsin mi?`)) return;
  try {
    const res = await fetch('/api/bot/signal-history/delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id})});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Kayıt silinemedi.');
    renderBotSignalHistory(data.signal_history || []);
    showToast('🗑️ Kayıt listeden silindi.', 'info');
  } catch (err) {
    showToast(`❌ ${err.message}`, 'error');
    fetchBotStatus();
  }
}
// Plain-language reason for a signal that did not open a trade.
function botHistoryReason(error, source) {
  const text = String(error || 'Emir gönderilemedi.');
  if (/Aynı emir kimliği farklı işlem/.test(text)) return source === 'AUTO'
    ? 'Bot aynı mumda yeniden başlatıldığı için bu sinyalin emir kimliği önceki farklı lot/SL/TP kaydıyla çakıştı; güvenlik için emir gönderilmedi (düzeltildi: her başlatma yeni kimlik kullanır).'
    : 'Aynı sinyal farklı ayarla tekrar gönderilmişti; artık her tıklama yeni emir olarak gönderiliyor.';
  return text;
}
function renderBotSignalHistory(rows) {
  const list = document.getElementById('bot-history-list');
  if (!list) return;
  if (Array.isArray(rows)) latestBotSignalHistory = rows;
  rows = latestBotSignalHistory;
  const count = document.getElementById('bot-history-count');
  if (count) count.textContent = String(rows.length);
  const clear = document.getElementById('bot-history-clear');
  if (clear) clear.hidden = !rows.length;
  if (!rows.length) { list.innerHTML = '<p class="bot-history-empty">Henüz sinyalden işlem açılmadı.</p>'; return; }
  const money = (v) => (typeof formatMoney === 'function' ? formatMoney(v) : Number(v).toFixed(2));
  list.innerHTML = rows.map(r => {
    const when = Number.isFinite(r.time) ? new Date(r.time * 1000) : null;
    const time = !when ? '--:--' : when.toDateString() === new Date().toDateString()
      ? when.toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})
      : when.toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
    const buy = r.side === 'BUY';
    const open = r.success && r.ticket != null ? executionPositions.get(Number(r.ticket)) || executionPositions.get(r.ticket) : null;
    let status, tone, reason = '';
    if (!r.success && !r.pending) { status = 'Açılmadı'; tone = 'error'; reason = botHistoryReason(r.error, r.source); }
    else if (r.pending) { status = 'Sonuç bekleniyor'; tone = 'pending'; reason = 'MT5 işlem geçmişini kontrol edin.'; }
    else if (open) {
      const profit = Number(open.profit);
      status = `Açık ${profit >= 0 ? '+' : ''}${money(profit)}`; tone = profit >= 0 ? 'profit' : 'loss';
    } else { status = 'Kapandı'; tone = 'closed'; }
    const details = [
      `${r.lot != null ? r.lot : '—'} lot${r.price != null ? ` @ ${r.price}` : ''}`,
      r.sl_points ? `SL ${r.sl_points} p` : '',
      r.tp_points ? `TP ${r.tp_points} p` : '',
      r.ticket != null ? `#${r.ticket}` : '',
    ].filter(Boolean).join(' · ');
    return `<div class="bot-history-row" data-tone="${tone}">
      <span class="bot-history-side ${buy ? 'is-buy' : 'is-sell'}">${buy ? 'AL' : 'SAT'}</span>
      <span class="bot-history-main"><b class="bot-history-symbol">${escapeHtml(r.symbol || '')}</b><span class="bot-history-meta">${escapeHtml(details)}</span></span>
      <span class="bot-history-status">${escapeHtml(status)}</span>
      <span class="bot-history-sub"><span class="bot-history-time">${time}</span><span class="bot-history-source" data-source="${r.source === 'AUTO' ? 'auto' : 'manual'}">${r.source === 'AUTO' ? 'Otomatik' : 'Manuel'}</span></span>
      <span class="bot-history-actions">
        ${open ? `<button type="button" class="bot-history-btn" onclick="editBotHistoryEntry(${Number(r.ticket)})" title="MT5'teki gibi açık pozisyonun SL/TP'sini değiştir veya kapat"><i class="ph-bold ph-pencil-simple"></i> Düzenle</button>` : ''}
        ${r.id ? `<button type="button" class="bot-history-btn is-delete" onclick="deleteBotHistoryEntry('${escapeHtml(String(r.id))}')" title="Kaydı listeden sil (pozisyon kapanmaz)"><i class="ph-bold ph-trash"></i> Sil</button>` : ''}
      </span>
      ${reason ? `<span class="bot-history-reason">${escapeHtml(reason)}</span>` : ''}
    </div>`;
  }).join('');
}
// Which lot applies where: manual confirmations vs the automatic bot's per-order cap.
function renderBotLotHint(bot) {
  const node = document.getElementById('bot-lot-hint');
  if (!node || !bot) return;
  const cap = bot.max_order_lots == null ? NaN : Number(bot.max_order_lots);
  if (bot.lot_mode === 'RISK') {
    node.textContent = `Lot risk modunda (%${bot.risk_pct}) stopa göre hesaplanır; bu kutu kullanılmaz.`;
    node.dataset.tone = '';
  } else if (cap > 0 && bot.lot_size > cap + 1e-9) {
    node.innerHTML = `"Şimdi AL/SAT" ${escapeHtml(String(bot.lot_size))} lot açar. Otomatik bot en fazla ${escapeHtml(String(cap))} lot açabilir; bu lotla bot başlatılamaz. `
      + `<button type="button" class="bot-lot-raise" onclick="raiseBotLotLimit()">Sınırı ${escapeHtml(String(bot.lot_size))} lot yap</button>`;
    node.dataset.tone = 'warn';
    const quick = document.getElementById('bot-quick-settings');
    if (quick && !quick.open) quick.open = true;
  } else {
    node.textContent = `"Şimdi AL/SAT" ve otomatik bot ${bot.lot_size} lot açar${cap > 0 ? ` (otomatik bot sınırı ${cap} lot)` : ''}.`;
    node.dataset.tone = '';
  }
  node.hidden = false;
}

// Collapsible bot sections remember whether they were open (per browser).
const BOT_SECTIONS_KEY = 'hma.bot.sections.v1';
function rememberBotSection(el) {
  try {
    const saved = JSON.parse(localStorage.getItem(BOT_SECTIONS_KEY) || '{}') || {};
    saved[el.id] = el.open;
    localStorage.setItem(BOT_SECTIONS_KEY, JSON.stringify(saved));
  } catch (_) { /* session-only */ }
}
function restoreBotSections() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(BOT_SECTIONS_KEY) || '{}') || {}; } catch (_) {}
  for (const id of ['bot-quick-settings', 'bot-tech-details']) {
    const el = document.getElementById(id);
    if (el && typeof saved[id] === 'boolean') el.open = saved[id];
  }
}
// Closed "Hızlı ayarlar" still shows the essentials in its title line.
function updateBotQuickSummary(bot) {
  const node = document.getElementById('bot-quick-summary');
  if (!node || !bot) return;
  const mode = bot.execution_mode === 'SIGNAL_ONLY' ? 'Sadece sinyal' : 'Otomatik al/sat';
  const lot = bot.lot_mode === 'RISK' ? `risk %${bot.risk_pct}` : `${bot.lot_size} lot`;
  const stops = bot.risk_mode === 'ATR'
    ? `SL ${bot.use_stop_loss ? 'ATR×' + bot.atr_stop_multiplier : 'yok'} · TP ${bot.use_take_profit ? 'ATR×' + bot.atr_target_multiplier : 'yok'}`
    : `SL ${bot.use_stop_loss ? bot.sl_points + ' p' : 'yok'} · TP ${bot.use_take_profit ? bot.tp_points + ' p' : 'yok'}`;
  node.textContent = `${mode} · ${lot} · ${stops}`;
}

// Per-symbol bot decision status ("neden işlem açmadı?").
const BOT_STATE_LABELS = {
  WAITING_CROSS: ['Kesişim bekleniyor', 'wait'], WAITING_CONFIRMATION: ['Onay bekleniyor', 'wait'],
  WAITING_DATA: ['Veri bekleniyor', 'idle'], AWAITING_APPROVAL: ['Onayın bekleniyor', 'action'],
  SKIPPED: ['Sinyal atlandı', 'warn'], REJECTED: ['Emir reddedildi', 'error'], ORDER_OPENED: ['Emir açıldı', 'ok'],
};
// "Botla İzle" list: signals the user handed to the bot, shown in their own frame ("Botun İzledikleri").
// Remembered in this browser; each entry follows the latest scan of its symbol.
const ADDED_SIGNALS_KEY = 'hma.bot.added-signals.v2';
let botAddedSignals = {};
try { botAddedSignals = JSON.parse(localStorage.getItem(ADDED_SIGNALS_KEY) || '{}') || {}; } catch (_) { botAddedSignals = {}; }
function saveAddedSignals() {
  try { localStorage.setItem(ADDED_SIGNALS_KEY, JSON.stringify(botAddedSignals)); } catch (_) { /* session-only */ }
}
function rememberAddedSignal(sym, timeframeMinutes) {
  const item = (latestScannedSignals || []).find(r => r.symbol === sym);
  const previous = botAddedSignals[sym] || {};
  delete botAddedSignals[sym];   // newest first
  botAddedSignals = {[sym]: {...previous, ...(item || {}), symbol: sym, added_at: Date.now(),
    timeframe_minutes: Number(timeframeMinutes) || item?.timeframe_minutes || previous.timeframe_minutes}, ...botAddedSignals};
  saveAddedSignals();
}
function dismissAddedSignal(sym) {
  delete botAddedSignals[sym];
  saveAddedSignals();
  renderBotAddedSignals(latestBotSettings || lastRenderedBotStatus);
}
function clearAddedSignals() {
  botAddedSignals = {};
  saveAddedSignals();
  renderBotAddedSignals(latestBotSettings || lastRenderedBotStatus);
}
function addedSignalHtml(sym, bot) {
  const saved = botAddedSignals[sym];
  if (!saved) return '';
  const live = (latestScannedSignals || []).find(r => r.symbol === sym);
  const item = live ? {...saved, ...live} : saved;
  if (live) botAddedSignals[sym] = {...item};
  const digits = Number.isInteger(item.digits) ? item.digits : 5;
  const fmt = v => (v != null && Number(v) > 0) ? Number(v).toFixed(digits) : '—';
  const directional = item.side === 'BUY' || item.side === 'SELL';
  const sideText = item.side === 'BUY' ? 'AL' : item.side === 'SELL' ? 'SAT' : item.signal === 'FILTERED' ? 'Filtrelendi' : item.side ? 'Nötr' : '—';
  const cross = item.signal === 'BUY_CROSS' || item.signal === 'SELL_CROSS';
  const tfCode = {1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'}[item.timeframe_minutes] || '';
  const lot = bot?.lot_mode === 'RISK' ? `özsermayenin %${bot.risk_pct} riski kadar lot` : `${bot?.lot_size ?? '—'} lot`;
  const botStops = !bot ? '' : bot.risk_mode === 'ATR'
    ? `SL ATR×${bot.use_stop_loss ? bot.atr_stop_multiplier : '—'}, TP ${bot.use_take_profit ? 'ATR×' + bot.atr_target_multiplier : 'yok'}`
    : `SL ${bot.use_stop_loss ? bot.sl_points + ' puan' : 'yok'}, TP ${bot.use_take_profit ? bot.tp_points + ' puan' : 'yok'}`;
  const onBot = bot && bot.symbol === sym;
  const nowLabel = `Şimdi ${sideText}`;
  // MT5 EA rule: the bot trades only a new crossover on a closed candle, never a signal already on the chart.
  const plan = !onBot
    ? `Bot şu an ${bot?.symbol || '—'} sembolünde. Bu sembolü izlemesi için "Botla İzle"ye basın.`
    : !directional
      ? (item.filtered ? `Filtre: ${item.filtered}` : 'Yön yok; bot kesişim bekler.')
      : bot.is_running
        ? `Bot bir sonraki kapanmış mumda yeni kesişim olursa ${lot} açar (${botStops}). Bu sinyale hemen girmek için "${nowLabel}".`
        : `Bot durdu. Başlatınca mevcut sinyali açmaz, sonraki yeni kesişimde ${lot} açar (${botStops}). Hemen girmek için "${nowLabel}".`;
  const lines = item.price
    ? `<span class="bot-added-line"><span class="bot-symstat-trade-side" data-side="${escapeHtml(item.side || 'HOLD')}">${escapeHtml(sideText)}</span>`
      + ` ${cross ? 'Yeni kesişim' : 'Trend'} · Fiyat <b>${fmt(item.price)}</b>${directional ? ` · ${escapeHtml(lot)}` : ''}</span>`
      + (directional ? `<span class="bot-added-line">"${nowLabel}" ile: SL <b>${fmt(item.suggested_sl)}</b> · TP <b>${fmt(item.suggested_tp)}</b></span>` : '')
    : '<span class="bot-added-line">Sinyal bilgisi bir sonraki taramada gelecek.</span>';
  return `<div class="bot-added-item" data-on-bot="${onBot ? 'true' : 'false'}" data-bot-symbol="${escapeHtml(sym)}">
    <div class="bot-added-head"><b>${escapeHtml(sym)}</b>${tfCode ? ` <small>${tfCode}</small>` : ''}
      <span class="bot-added-badge">${onBot ? 'Bot bu sembolde' : 'Bot başka sembolde'}</span>
      <button type="button" class="bot-symstat-trade-delete" onclick="dismissAddedSignal('${escapeHtml(sym)}')" title="Listeden kaldır (emir veya pozisyon etkilenmez)"><i class="ph-bold ph-trash"></i> Sil</button></div>
    ${lines}<small>${escapeHtml(plan)}</small>
  </div>`;
}
function renderBotAddedSignals(bot) {
  const node = document.getElementById('bot-added-signals');
  if (!node) return;
  const symbols = Object.keys(botAddedSignals);
  if (!symbols.length) { node.hidden = true; node.innerHTML = ''; return; }
  // The bot's own symbol first, then most recently added.
  symbols.sort((a, b) => (bot && b === bot.symbol) - (bot && a === bot.symbol));
  node.innerHTML = `<div class="bot-added-title"><span><i class="ph-bold ph-robot"></i> Botun İzledikleri <span class="bot-history-count">${symbols.length}</span></span>`
    + `<button type="button" class="bot-history-clear" onclick="clearAddedSignals()">Temizle</button></div>`
    + symbols.map(sym => addedSignalHtml(sym, bot)).join('');
  node.hidden = false;
  saveAddedSignals();
}
// MT5 shows bar times on the broker server clock. Bar timestamps already are server time,
// so format them as UTC (no browser time zone shift); "today" is the server's today.
function brokerCandleText(candleTime, clockOffsetSeconds = 0) {
  const candle = new Date(Number(candleTime) * 1000);
  const serverNow = new Date(Date.now() + (Number(clockOffsetSeconds) || 0) * 1000);
  const day = d => d.toLocaleDateString('tr-TR', {timeZone: 'UTC'});
  return day(candle) === day(serverNow)
    ? candle.toLocaleTimeString('tr-TR', {timeZone: 'UTC', hour: '2-digit', minute: '2-digit'})
    : candle.toLocaleString('tr-TR', {timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
}
let lastRenderedBotStatus = null;
// Timeframe chips: the chart timeframe is the bot timeframe; more chips attach the same EA to other charts
// of the symbol (each one opens and closes only its own position), also while the bot runs.
let botTimeframesSaving = false;
function renderBotTimeframes(bot) {
  const node = document.getElementById('bot-timeframes');
  if (!node || !bot) return;
  const extras = new Set((bot.extra_timeframes || []).map(Number));
  const primary = Number(bot.timeframe_minutes);
  node.innerHTML = '<span>Zaman dilimleri:</span>' + BOT_TIMEFRAMES.map(tf => {
    const isPrimary = tf === primary;
    const on = isPrimary || extras.has(tf);
    return `<button type="button" data-tf="${tf}" aria-pressed="${on}" data-primary="${isPrimary}" ${botTimeframesSaving ? 'disabled' : ''}
      title="${isPrimary ? 'Grafiğin zaman dilimi (bot bunu her zaman izler)' : on ? 'Bu zaman dilimini bottan çıkar' : 'Bu zaman diliminde de sinyal yakalayıp kendi pozisyonunu açsın'}"
      onclick="toggleBotTimeframe(${tf})">${botTfCode(tf)}</button>`;
  }).join('');
}
async function toggleBotTimeframe(tf) {
  const bot = latestBotSettings;
  tf = Number(tf);
  if (!bot || botTimeframesSaving || tf === Number(bot.timeframe_minutes)) return;
  const extras = new Set((bot.extra_timeframes || []).map(Number));
  if (extras.has(tf)) extras.delete(tf); else extras.add(tf);
  botTimeframesSaving = true;
  renderBotTimeframes(bot);
  try {
    const res = await fetch('/api/bot/follow', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({extra_timeframes: [...extras].sort((a, b) => a - b)})});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Zaman dilimi güncellenemedi.');
    latestBotSettings = data && data.symbol ? data : {...bot, extra_timeframes: [...extras]};
    const list = (latestBotSettings.instance_timeframes || [bot.timeframe_minutes, ...extras]).map(botTfCode).join(', ');
    showToast(`🧩 Bot zaman dilimleri: ${list}. Her biri kendi sinyalinde kendi pozisyonunu açar.`, 'success');
  } catch (err) {
    showToast(`❌ ${err.message}`, 'error');
  } finally {
    botTimeframesSaving = false;
    renderBotSymbolStatus(latestBotSettings || bot);
  }
}

function renderBotSymbolStatus(bot) {
  const node = document.getElementById('bot-symbol-status');
  if (!node || !bot) return;
  lastRenderedBotStatus = bot;
  const symbols = Array.isArray(bot.symbols) && bot.symbols.length ? bot.symbols : [bot.symbol];
  const status = bot.symbol_status || {};
  const tfCode = (m) => ({1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'}[m] || `M${m}`);
  const scanTf = Number(document.getElementById('signal-scan-tf')?.value || 0);
  const notes = [];
  if (!bot.is_running) notes.push('Bot durdurulmuş; işlem açmaz.');
  else if (bot.execution_mode === 'SIGNAL_ONLY') notes.push('"Sadece Sinyal" modunda: bot işlem açmaz, sinyalleri onayına sunar.');
  if (scanTf && bot.timeframe_minutes && scanTf !== bot.timeframe_minutes)
    notes.push(bot.is_running
      ? `Bot ${tfCode(bot.timeframe_minutes)} ile çalışıyor, grafik ${tfCode(scanTf)} gösteriyor. Botu durdurup yeniden başlatınca grafikle eşitlenir.`
      : `Bot ${tfCode(bot.timeframe_minutes)}, grafik ${tfCode(scanTf)}; bot başlatılırken grafikle eşitlenir.`);
  renderBotTimeframes(bot);
  const instances = Array.isArray(bot.instance_timeframes) && bot.instance_timeframes.length ? bot.instance_timeframes : [bot.timeframe_minutes];
  const keys = symbols.flatMap(sym => instances.map(tf => [sym, Number(tf) === Number(bot.timeframe_minutes) ? sym : `${sym}@${tfCode(Number(tf))}`, Number(tf)]));
  const rows = keys.map(([sym, key, tf]) => {
    const row = status[key] || {timeframe_minutes: tf};
    const [label, tone] = BOT_STATE_LABELS[row.state] || (bot.is_running ? ['Kontrol ediliyor', 'idle'] : ['Bot durdu', 'idle']);
    const candleText = row.candle_time ? ` · son mum ${brokerCandleText(row.candle_time, bot.clock_offset_seconds)}` : '';
    return `<div class="bot-symstat-row" data-tone="${tone}" data-bot-symbol="${escapeHtml(sym)}" data-bot-tf="${tf}">
      <span class="bot-symstat-sym">${escapeHtml(sym)} <small>${tfCode(row.timeframe_minutes || bot.timeframe_minutes)}${candleText}</small></span>
      <span class="bot-symstat-state">${label}</span>
      <span class="bot-symstat-detail">${escapeHtml(row.detail || (bot.is_running ? 'İlk kapanmış mum değerlendiriliyor…' : 'Botu başlatınca burada ne beklediği görünür.'))}</span>
    </div>`;
  }).join('');
  node.innerHTML = `${notes.map(n => `<p class="bot-symstat-note">${escapeHtml(n)}</p>`).join('')}${rows}`;
  node.hidden = false;
  renderBotAddedSignals(bot);
}

// Which way the bot works (entry mode), shown prominently at the top of the bot card.
function renderBotMode(bot) {
  const node = document.getElementById('bot-mode-line');
  if (!node || !bot) return;
  const pair = `${bot.fast_ma_type || 'HMA'} ${bot.hma_period} × ${bot.second_ma_type} ${bot.second_ma_period}`;
  const modes = {
    MEVO: ['ph-brain', 'purple', 'MevO (DeepSeek)', 'Her kapanan mumda MevO fırsatına bakar; güven ≥ %65 ve geçmiş zarar uyarısı yoksa girer.'],
    MANUAL_STYLE: ['ph-hand-pointing', 'amber', 'Manuel işlemlerim tarzı', 'Manuel işlemlerinden öğrenilen sinyal AL/SAT olunca girer.'],
    CROSSOVER: ['ph-chart-line', 'cyan', `Strateji: ${pair} kesişimi`, 'Kapanmış mumda iki ortalama kesişince girer, ters kesişimde kapatır.'],
  };
  const [icon, tone, name, hint] = modes[bot.entry_mode] || modes.CROSSOVER;
  node.dataset.tone = tone;
  node.innerHTML = `<i class="ph-bold ${icon}"></i><span class="bot-mode-label">Çalışma şekli</span><strong>${escapeHtml(name)}</strong><span class="bot-mode-hint">${escapeHtml(hint)}</span>`;
  node.hidden = false;
}

// Protection / sizing summary line under the bot status.
function renderBotProtection(bot) {
  renderBotMode(bot);
  const node = document.getElementById('bot-protect-status');
  if (!node || !bot) return;
  const copy = bot.copy_manual_trades ? bot.manual_copy : null;
  const parts = [];
  if (bot.copy_manual_trades && copy?.available) {
    const lot = copy.applied_lot ?? copy.lot;
    parts.push(`Lot/SL/TP: manuel kopya (${copy.since} sonrası ${copy.count} kârlı işlem) · ${lot} lot · SL ${copy.sl_points ?? `bot ${bot.sl_points}`} p · TP ${copy.tp_points ?? 'bot'} p${copy.capped ? ' · lot sınırda' : copy.demo_full_lot ? ' · demo: tam lot' : ''}`);
  } else if (bot.copy_manual_trades) {
    parts.push(`Lot: sabit ${bot.lot_size} (manuel kopya ${copy?.reason ? 'yok: ' + copy.reason : 'ilk sinyalde okunacak'})`);
  } else {
    parts.push(bot.lot_mode === 'RISK' ? `Lot: özsermayenin %${bot.risk_pct} riski` : `Lot: sabit ${bot.lot_size}`);
  }
  if (bot.entry_mode === 'MANUAL_STYLE') parts.unshift('Giriş: manuel işlemlerinden öğrenilen sinyal');
  else if (bot.entry_mode === 'MEVO') parts.unshift('Giriş: MevO fırsatları (DeepSeek)');
  else if (bot.trend_entry_on_start) parts.push('Başlarken trend yönünde girer');
  if (bot.reenter_after_stop) parts.push('SL/TP sonrası sinyal sürüyorsa yeniden girer');
  if (bot.stop_profit_amount || bot.stop_loss_amount) {
    const now = Number.isFinite(bot.session_pnl) ? ` (şu an ${bot.session_pnl > 0 ? '+' : ''}${bot.session_pnl})` : '';
    parts.push(`Hedef: ${bot.stop_profit_amount ? 'kâr +' + bot.stop_profit_amount : 'kâr yok'} / ${bot.stop_loss_amount ? 'zarar -' + bot.stop_loss_amount : 'zarar yok'}${now}`);
  }
  if (bot.max_consecutive_losses) parts.push(`${bot.max_consecutive_losses} kayıpta dur`);
  if (bot.trade_hours_enabled) parts.push(`Saat ${bot.trade_start}–${bot.trade_end}`);
  if (Array.isArray(bot.trade_days) && bot.trade_days.length < 7) parts.push(`${bot.trade_days.length} gün`);
  if (bot.blocked_windows?.length) parts.push(`${bot.blocked_windows.length} engelli aralık`);
  const issue = bot.trading_window_issue;
  node.innerHTML = `<i class="ph-bold ph-shield-check"></i> ${escapeHtml(parts.join(' · '))}${issue ? ` <span class="bot-protect-issue">⏸ ${escapeHtml(issue)}</span>` : ''}`;
  node.hidden = false;
}

// Bot performance report (observed results of closed bot positions).
let botPerfRequest = 0;
async function fetchBotPerformance() {
  const body = document.getElementById('bot-perf-body');
  if (!body) return;
  const days = Number(document.getElementById('bot-perf-days')?.value || 30);
  const request = ++botPerfRequest;
  body.innerHTML = '<p class="bot-history-empty">Yükleniyor…</p>';
  try {
    const res = await fetch(`/api/bot/performance?days=${days}`);
    const data = await res.json().catch(() => ({}));
    if (request !== botPerfRequest) return;
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Performans okunamadı.');
    body.innerHTML = renderBotPerformance(data);
  } catch (err) {
    if (request === botPerfRequest) body.innerHTML = `<p class="bot-history-empty">${escapeHtml(err.message)}</p>`;
  }
}
function renderBotPerformance(d) {
  if (!d.closed_positions) return `<p class="bot-history-empty">Son ${d.days} günde kapanmış bot pozisyonu yok.</p>`;
  const m = (v) => v == null ? '—' : (typeof formatMoney === 'function' ? formatMoney(v) : Number(v).toFixed(2));
  const signed = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${m(v)}`;
  const tone = (v) => v == null ? '' : v >= 0 ? 'is-pos' : 'is-neg';
  const kpi = (label, value, cls = '', hint = '') => `<div class="bot-kpi" ${hint ? `title="${escapeHtml(hint)}"` : ''}><span>${label}</span><strong class="${cls}">${value}</strong></div>`;
  const table = (title, groups, keyLabel) => {
    const rows = Object.entries(groups || {});
    if (!rows.length) return '';
    return `<div class="bot-perf-table"><p>${title}</p><table><thead><tr><th>${keyLabel}</th><th>İşlem</th><th>Kazanma</th><th>Net</th></tr></thead><tbody>${
      rows.map(([k, g]) => `<tr><td>${escapeHtml(k === 'AUTO' ? 'Otomatik' : k === 'MANUAL' ? 'Manuel' : k)}</td><td>${g.count}</td><td>%${g.win_rate_pct}</td><td class="${tone(g.net)}">${signed(g.net)}</td></tr>`).join('')
    }</tbody></table></div>`;
  };
  return `${d.early_sample ? `<p class="bot-perf-warn">Örnek küçük (${d.closed_positions} işlem). 30 işlemden az sonuçlar tesadüf olabilir; karar için yetersiz.</p>` : ''}
    <div class="bot-kpis">
      ${kpi('Kapanan işlem', `${d.closed_positions} <small>(${d.wins}K / ${d.losses}Z)</small>`)}
      ${kpi('Kazanma oranı', d.win_rate_pct == null ? '—' : `%${d.win_rate_pct}`)}
      ${kpi('Net sonuç', signed(d.net), tone(d.net), 'Komisyon, swap ve ücretler dahil')}
      ${kpi('Profit factor', d.profit_factor ?? '—', d.profit_factor == null ? '' : d.profit_factor >= 1 ? 'is-pos' : 'is-neg', 'Toplam kazanç / toplam kayıp. 1 altı zarar ettirir.')}
      ${kpi('İşlem başı beklenen', signed(d.expectancy), tone(d.expectancy))}
      ${kpi('Ortalama R', d.avg_r == null ? '—' : `${d.avg_r} <small>(${d.r_samples})</small>`, tone(d.avg_r), 'Sonuç / girişteki risk. Yalnız risk % lot modunda açılan işlemler için hesaplanır.')}
      ${kpi('Ort. kazanç / kayıp', `${m(d.avg_win)} / ${m(d.avg_loss)}`)}
      ${kpi('En büyük düşüş', m(d.max_drawdown), d.max_drawdown ? 'is-neg' : '', 'Kapanmış işlemlerin kümülatif sonucunda tepeden en büyük geri çekilme')}
      ${kpi('Şu anki kayıp serisi', d.current_loss_streak)}
    </div>
    <div class="bot-perf-tables">
      ${table('Sembole göre', d.by_symbol, 'Sembol')}
      ${table(`Giriş saatine göre (UTC${d.utc_offset_min >= 0 ? '+' : ''}${d.utc_offset_min / 60})`, d.by_hour, 'Saat')}
      ${table('Yöne göre (AL / SAT)', d.by_side, 'Yön')}
      ${table('Kaynağa göre', d.by_source, 'Kaynak')}
    </div>
    <p class="bot-protect-note">Geçmiş sonuçlardır, gelecek tahmini değildir. Sayılar MT5 geçmişindeki kapanmış HMA botu pozisyonlarından hesaplanır ("Şimdi AL/SAT" manuel işlemdir, dahil değildir).</p>`;
}

async function clearBotSignalHistory() {
  try {
    const res = await fetch('/api/bot/signal-history/clear', {method: 'POST'});
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Liste temizlenemedi.');
    renderBotSignalHistory([]);
  } catch (err) { showToast(`❌ ${err.message}`, 'error'); }
}

// Toggle Bot
let botTogglePending = false;
// Starting with a fixed lot above the automation cap: offer to raise the cap instead of failing.
async function ensureBotLotWithinLimit() {
  const bot = latestBotSettings;
  const lot = Number(bot?.lot_size), cap = bot?.max_order_lots == null ? NaN : Number(bot.max_order_lots);
  // Unknown cap (null/0 from an older server) never triggers the prompt; the server still checks.
  if (!bot || bot.lot_mode === 'RISK' || !(lot > 0) || !(cap > 0) || lot <= cap + 1e-9) return true;
  const ok = await confirmAction({title: 'Otomatik bot lot sınırı',
    message: `Bot emir lotu ${lot} lot, otomatik bot için tek emir sınırı ${cap} lot. Sınırı ${lot} lot yapıp botu başlatayım mı? ` +
      'Bot bundan sonra her sinyalde bu lotla işlem açabilir.',
    confirmLabel: `Sınırı ${lot} lot yap ve başlat`,
    details: [['Emir lotu', `${lot} lot`], ['Mevcut sınır', `${cap} lot`], ['Yeni sınır', `${lot} lot`]]});
  if (!ok) return false;
  try {
    await saveLotLimits({max_order_lots: lot, max_total_lots: Math.max(Number(bot.max_total_lots) || 0, lot)});
    showToast(`✅ Otomatik bot tek emir sınırı ${lot} lot yapıldı.`, 'success');
    await fetchBotStatus();
    return true;
  } catch (err) {
    showToast(`❌ ${err.message}`, 'error');
    return false;
  }
}

async function toggleBot() {
  if (botTogglePending || accountSwitching) return;
  if (!isBotRunning && !(latestBotSettings?.is_running) && !(await ensureBotLotWithinLimit())) return;
  botTogglePending = true;
  const button = document.getElementById('bot-toggle-btn');
  if (button) button.disabled = true;
  try {
    // Starting: align the bot with the chart timeframe first.
    if (!isBotRunning && !(latestBotSettings?.is_running)) {
      await syncBotToSelection({}, {announce: false});
    }
    const res = await fetch("/api/bot/toggle", { method: "POST" });
    const data = await res.json();
    if (!res.ok || data.success === false) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'Bot komutu doğrulanamadı.');
    if (data.is_running) {
      showToast("🚀 HMA Algoritmik Bot Başlatıldı!", "success");
      for (const warning of (data.start_warnings || []))
        showToast(`⚠️ Başlangıç uyarısı · ${warning}`, 'warning');
      for (const note of (data.start_notes || []))
        showToast(`ℹ️ ${note}`, 'info');
    } else {
      showToast("🛑 HMA Algoritmik Bot Durduruldu.", "info");
    }
    fetchBotStatus();
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
  } finally {
    botTogglePending = false;
    if (button) button.disabled = window.MT5Permissions?.can?.('toggleBot') === false;
  }
}

// Bot Settings Modal
// Bot form = HMA_Crossover_EA.mq5 inputs only (plus the chart symbol / timeframe the EA is attached to).
const botSettingInputs = {
  'cfg-symbol': 'symbol', 'cfg-timeframe': 'timeframe_minutes', 'cfg-hma-period': 'hma_period',
  'cfg-ma2-type': 'second_ma_type', 'cfg-ma2-period': 'second_ma_period', 'cfg-lot': 'lot_size',
  'cfg-sl-points': 'sl_points', 'cfg-tp-points': 'tp_points', 'cfg-copy-since': 'copy_manual_since',
  'cfg-stop-profit': 'stop_profit_amount', 'cfg-stop-loss': 'stop_loss_amount', 'cfg-copy-lot': 'copy_lot_override', 'cfg-max-bot-pos': 'max_bot_positions',
};
const botSettingChecks = {'cfg-close-opp': 'close_opposite', 'cfg-use-sl': 'use_stop_loss', 'cfg-use-tp': 'use_take_profit',
  'cfg-trend-entry': 'trend_entry_on_start', 'cfg-reenter': 'reenter_after_stop', 'cfg-copy-manual': 'copy_manual_trades', 'cfg-auto-lot-demo': 'auto_lot_limits_demo'};
// Automation lot limits (MAX_ORDER_LOTS / MAX_TOTAL_OPEN_LOTS / MAX_OPEN_ORDERS), editable from the panel.
let loadedLotLimits = null;
async function loadLotLimitsIntoForm() {
  try {
    const res = await fetch('/api/risk/lot-limits');
    if (!res.ok) throw new Error();
    loadedLotLimits = await res.json();
    document.getElementById('cfg-max-order-lots').value = loadedLotLimits.max_order_lots;
    document.getElementById('cfg-max-total-lots').value = loadedLotLimits.max_total_lots;
    document.getElementById('cfg-max-open-orders').value = loadedLotLimits.max_open_orders;
  } catch (_) { loadedLotLimits = null; }
}
async function saveLotLimits(values) {
  const res = await fetch('/api/risk/lot-limits', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail
    : res.status === 403 ? 'Sınırları değiştirmek için yönetici yetkisi ve gerçek hesapta İşlem Güvenliği (TOTP) gerekli.' : 'Sınırlar kaydedilemedi.');
  loadedLotLimits = data;
  return data;
}
async function raiseBotLotLimit() {
  const bot = latestBotSettings;
  if (!bot) return;
  const lot = Number(bot.lot_size);
  try {
    await saveLotLimits({max_order_lots: lot, max_total_lots: Math.max(Number(bot.max_total_lots) || 0, lot)});
    showToast(`✅ Otomatik bot tek emir sınırı ${lot} lot yapıldı.`, 'success');
    fetchBotStatus();
  } catch (err) { showToast(`❌ ${err.message}`, 'error'); }
}
// HMA_Crossover_EA.mq5 inputs (MT5 EA): fills the bot form with the same values; nothing is saved until "Kaydet".
// Symbol and timeframe stay as they are (the EA works on the chart it is attached to).
const EA_INPUTS = {
  'cfg-hma-period': 14, 'cfg-ma2-type': 'EMA', 'cfg-ma2-period': 34,            // InpHMAPeriod, InpSecondMAType, InpSecondMAPeriod
  'cfg-lot': 0.01,                                                                   // InpLotSize
  'cfg-close-opp': true,                                                            // InpCloseOpposite
  'cfg-use-sl': true, 'cfg-sl-points': 200,                                         // InpUseStopLoss, InpStopLossPoints
  'cfg-use-tp': false, 'cfg-tp-points': 400,                                        // InpUseTakeProfit, InpTakeProfitPoints
  'cfg-max-order-lots': 0.10, 'cfg-max-total-lots': 0.50, 'cfg-max-open-orders': 10, // InpMaxOrderLots, InpMaxTotalLots, InpMaxOpenOrders
};
function applyEaDefaults() {
  for (const [id, value] of Object.entries(EA_INPUTS)) {
    const node = document.getElementById(id);
    if (!node) continue;
    if (node.type === 'checkbox') node.checked = value; else node.value = String(value);
    node.dispatchEvent?.(new Event('change', {bubbles: true}));
  }
  showToast('MT5 EA değerleri forma yüklendi (lot 0.01, SL 200 puan, TP kapalı, ters sinyalde kapat, sınırlar 0.10 / 0.50 / 10). Uygulamak için Kaydet\'e basın.', 'info');
}
async function toggleBotSettingsModal() {
  const modal = document.getElementById("bot-settings-modal");
  if (!modal.classList.contains('hidden')) { modal.classList.add('hidden'); return; }
  document.getElementById('bot-research-transfer-note').textContent = '';
  latestBotSettings = null;
  await fetchBotStatus();
  if (!latestBotSettings) { showToast('Bot ayarları sunucudan okunamadı.', 'error'); return; }
  for (const [id, key] of Object.entries(botSettingInputs)) {
    if (latestBotSettings[key] != null) document.getElementById(id).value = latestBotSettings[key];
  }
  for (const [id, key] of Object.entries(botSettingChecks)) document.getElementById(id).checked = !!latestBotSettings[key];
  if (latestBotSettings.second_ma_type === 'KAMA') document.getElementById('cfg-ma2-type').value = 'EMA';
  await loadLotLimitsIntoForm();
  modal.classList.remove('hidden');
}

async function saveBotSettings() {
  // HMA_Crossover_EA inputs only; the server keeps every non-EA feature off.
  // HMA / 2nd MA are shared with the scanner (2–200), so the bot and the scanner never disagree.
  for (const id of ['cfg-hma-period', 'cfg-ma2-period']) {
    const n = Number(document.getElementById(id)?.value);
    if (!Number.isInteger(n) || n < 2 || n > 200) { showToast('❌ HMA ve 2. MA periyodu 2–200 arası tam sayı olmalı (sinyal tarayıcıyla ortak).', 'error'); return; }
  }
  const cfg = {
    symbol: document.getElementById("cfg-symbol").value,
    timeframe_minutes: Number(document.getElementById('cfg-timeframe').value),
    hma_period: parseInt(document.getElementById("cfg-hma-period").value),
    second_ma_type: document.getElementById("cfg-ma2-type").value,
    second_ma_period: parseInt(document.getElementById("cfg-ma2-period").value),
    lot_size: parseFloat(document.getElementById("cfg-lot").value),
    close_opposite: document.getElementById("cfg-close-opp").checked,
    use_stop_loss: document.getElementById('cfg-use-sl').checked,
    use_take_profit: document.getElementById('cfg-use-tp').checked,
    sl_points: Number(document.getElementById('cfg-sl-points').value),
    tp_points: Number(document.getElementById('cfg-tp-points').value),
  };
  // User options on top of the EA (sent only when the form has them).
  const trendEntry = document.getElementById('cfg-trend-entry');
  if (trendEntry) cfg.trend_entry_on_start = Boolean(trendEntry.checked);
  const copyManual = document.getElementById('cfg-copy-manual');
  if (copyManual) cfg.copy_manual_trades = Boolean(copyManual.checked);
  const reenter = document.getElementById('cfg-reenter');
  if (reenter) cfg.reenter_after_stop = Boolean(reenter.checked);
  const autoLot = document.getElementById('cfg-auto-lot-demo');
  if (autoLot) cfg.auto_lot_limits_demo = Boolean(autoLot.checked);
  for (const [id, key] of [['cfg-stop-profit', 'stop_profit_amount'], ['cfg-stop-loss', 'stop_loss_amount'], ['cfg-copy-lot', 'copy_lot_override']]) {
    const node = document.getElementById(id);
    if (node) { const v = Number(node.value || 0); if (!Number.isFinite(v) || v < 0) { showToast('❌ Kâr hedefi, zarar sınırı ve kopya lotu 0 veya pozitif sayı olmalı.', 'error'); return; } cfg[key] = v; }
  }
  const maxPos = document.getElementById('cfg-max-bot-pos');
  if (maxPos && String(maxPos.value || '') !== '') {
    const n = Number(maxPos.value);
    if (!Number.isInteger(n) || n < 1 || n > 20) { showToast('❌ En fazla bot pozisyonu 1–20 arası tam sayı olmalı.', 'error'); return; }
    cfg.max_bot_positions = n;
  }
  const copySince = document.getElementById('cfg-copy-since')?.value;
  if (copySince) cfg.copy_manual_since = copySince;
  const limits = {
    max_order_lots: Number(document.getElementById('cfg-max-order-lots').value),
    max_total_lots: Number(document.getElementById('cfg-max-total-lots').value),
    max_open_orders: parseInt(document.getElementById('cfg-max-open-orders').value, 10),
  };
  const limitsChanged = loadedLotLimits && Object.entries(limits).some(([k, val]) => Number.isFinite(val) && val !== loadedLotLimits[k]);
  if (limitsChanged) {
    try { await saveLotLimits(limits); }
    catch (err) { showToast(`❌ ${err.message}`, 'error'); return; }
  }

  try {
    const res = await fetch("/api/bot/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg)
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(typeof error.detail === 'string' ? error.detail : 'Bot parametreleri geçersiz; sayı sınırlarını kontrol edin.');
    }
    if (res.ok) {
      showToast("✅ Bot ayarları güncellendi!", "success");
      const saved = await res.json().catch(() => null);
      if (saved && typeof saved === 'object' && saved.symbol) latestBotSettings = saved;
      toggleBotSettingsModal();
      // The bot follows the panel selection; carry these choices to the scanner, chart and symbol.
      try {
        window.HMASignalSettings?.setPeriods?.({hma_period: cfg.hma_period, ma_type: cfg.second_ma_type, ema_period: cfg.second_ma_period});
        if (cfg.symbol && cfg.symbol !== currentSymbol && typeof switchSymbol === 'function') switchSymbol(cfg.symbol);
        if (BOT_TIMEFRAMES.includes(cfg.timeframe_minutes) && cfg.timeframe_minutes !== Number(window.MT5Chart?.getTimeframe?.())) setChartTimeframe(cfg.timeframe_minutes);
      } catch (err) { console.warn('Bot settings could not be carried to the panel selection', err); }
      fetchBotStatus();
    }
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
  }
}

// Toast Notification
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  let bg = "bg-[#0f1218] border-gray-700 text-white";
  if (type === "success") bg = "bg-emerald-950 border-emerald-600 text-emerald-200";
  if (type === "error") bg = "bg-rose-950 border-rose-600 text-rose-200";
  if (type === "warning") bg = "bg-amber-950 border-amber-600 text-amber-200";

  toast.className = `toast px-4 py-3 rounded-xl border shadow-xl text-xs font-medium max-w-sm pointer-events-auto flex items-center gap-2 ${bg}`;
  toast.innerText = message;
  // Fixable errors: clicking the toast opens the place where the problem is solved.
  const fixable = window.HMAToastFix?.attach?.(toast, message, type) === true;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.3s";
    setTimeout(() => toast.remove(), 300);
  }, fixable ? 9000 : type === 'error' || type === 'warning' ? 9000 : 6000);
}

// Helpers
function formatMoney(val) {
  if (val === undefined || val === null || isNaN(val)) return "0.00";
  return Number(val).toLocaleString(window.MT5I18n?.language?.() === "en" ? "en-US" : "tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
}

// Login Modal Functions
function toggleLoginModal() {
  const modal = document.getElementById("login-modal");
  modal.classList.toggle("hidden");
  if (modal.classList.contains("hidden")) {
    document.getElementById("login-pass").value = "";
  } else {
    document.getElementById(currentAccountType === "REAL" ? "login-mode-real" : "login-mode-demo").checked = true;
    updateLoginMode();
  }
}

function updateLoginMode() {
  const real = document.getElementById("login-mode-real").checked;
  const help = document.getElementById("login-mode-help");
  help.textContent = real
    ? "Gerçek hesapta gönderilen emirler gerçek para ile işlem yapar. Brokerın tam sunucu adını girin."
    : "Demo hesapta sanal bakiye kullanılır. Brokerın tam sunucu adını girin.";
  help.className = `mt-1 text-xs ${real ? "text-rose-300" : "text-gray-400"}`;
}

async function submitLogin() {
  const accountValue = document.getElementById("login-acc").value.trim();
  const acc = /^\d+$/.test(accountValue) ? Number(accountValue) : NaN;
  const pass = document.getElementById("login-pass").value;
  const srv = document.getElementById("login-srv").value.trim();
  const accountType = document.getElementById("login-mode-real").checked ? "REAL" : "DEMO";
  const button = document.getElementById("login-submit-btn");

  if (!Number.isSafeInteger(acc) || acc <= 0 || !pass || !srv) {
    showToast("Lütfen tüm alanları doldurun.", "error");
    return;
  }

  if (button.disabled) return;
  if (tradeActionBusy() || unresolvedIntents(currentSymbol, true).size) {
    showToast('Hesap değiştirmeden önce aktif ve sonucu belirsiz işlem taleplerini doğrulayın.', 'error');
    return;
  }
  button.disabled = true;
  accountSwitching = true;
  accountSessionGeneration++;
  window.HMAPeriodReport?.reset?.();
  currentAccountType = null;
  if (typeof liveSource !== 'undefined' && liveSource) { liveSource.close(); liveSource = null; }
  if (typeof lastLiveMessage !== 'undefined') lastLiveMessage = 0;
  clearSymbolPrices();
  for (const id of ['acc-balance','acc-equity','acc-margin-free','acc-profit']) {
    const node = document.getElementById(id);
    if (node) node.innerText = '—';
  }
  document.getElementById('positions-table-body').innerHTML = '';
  const historyTable = document.getElementById('history-table-body');
  if (historyTable) historyTable.innerHTML = '';
  for (const id of ['pos-count-badge','history-count-badge','history-total-profit','history-total-commission']) {
    const node = document.getElementById(id);
    if (node) node.innerText = '—';
  }
  historyDetailsRequest++;
  for (const id of ['history-details-modal','position-execution-modal']) {
    const modal = document.getElementById(id);
    if (modal?.open) modal.close();
  }
  executionPositions.clear();
  if (typeof managedPositions !== 'undefined') managedPositions.clear();
  if (typeof editorPosition !== 'undefined') editorPosition = null;
  const editor = document.getElementById('position-editor');
  if (editor?.open) editor.close();
  const pending = document.getElementById('pending-orders-list');
  if (pending) pending.textContent = 'Hesap bilgisi doğrulanıyor…';
  showAccountConnectionError('Hesap bilgisi doğrulanıyor…');
  try {
    showToast("Broker hesabına bağlanılıyor...", "info");
    const res = await fetch("/api/account/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login: acc, password: pass, server: srv, account_type: accountType })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`${accountType === "REAL" ? "Gerçek" : "Demo"} hesaba giriş başarılı: #${data.login}`, "success");
      toggleLoginModal();
    } else {
      showToast(`❌ Giriş Başarısız: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ Bağlantı Hatası: ${err.message}`, "error");
  } finally {
    accountSwitching = false;
    accountSessionGeneration++;
    window.HMAPeriodReport?.reset?.();
    if (typeof startLiveFeed === 'function') startLiveFeed();
    fetchAccount(true);
    fetchPositions(true);
    fetchHistory();
    if (currentPositionTab === 'reports') fetchReports();
    if (typeof fetchPendingOrders === 'function') fetchPendingOrders();
    document.getElementById("login-pass").value = "";
    button.disabled = false;
  }
}

let signalScannerBusy = false;
// Every scan gets a sequence number; a slower, older scan must never repaint
// the list after the user switched symbol, timeframe or scan mode.
let signalScanSeq = 0;
let signalScanController = null;
let signalAutoAlertEnabled = true;
let sharedSignalAudioCtx = null;
const announcedSignalCrosses = new Map();

function ensureSignalAudioCtx() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return null;
    if (!sharedSignalAudioCtx || sharedSignalAudioCtx.state === 'closed') {
      sharedSignalAudioCtx = new AudioCtx();
    }
    if (sharedSignalAudioCtx.state === 'suspended') {
      sharedSignalAudioCtx.resume().catch(() => {});
    }
    return sharedSignalAudioCtx;
  } catch (_) {
    return null;
  }
}

if (typeof window !== 'undefined') {
  const unlockAudio = () => {
    ensureSignalAudioCtx();
  };
  window.addEventListener('pointerdown', unlockAudio, {passive: true});
  window.addEventListener('keydown', unlockAudio, {passive: true});
}

// Bot signals (automatic orders, or signals awaiting approval) also play the alert, like MT5's
// Alert/PlaySound in the EA; the first status load only records what is already there.
let lastAnnouncedBotHistory = null;
const announcedBotPending = new Set();
let botPendingPrimed = false;
function announceBotSignals(bot) {
  const rows = Array.isArray(bot?.signal_history) ? bot.signal_history : [];
  const newest = rows[0] ? Number(rows[0].time) || 0 : 0;
  if (lastAnnouncedBotHistory === null) { lastAnnouncedBotHistory = newest; return; }
  const fresh = rows.filter(r => (Number(r.time) || 0) > lastAnnouncedBotHistory && r.source === 'AUTO');
  lastAnnouncedBotHistory = Math.max(lastAnnouncedBotHistory, newest);
  if (!fresh.length || !signalAutoAlertEnabled) return;
  const r = fresh[0];
  // A trade the bot actually sent has its own sound, so it is never mistaken for a scanner signal.
  playBotTradeSound(r.success ? 'opened' : 'failed');
  if (typeof showToast === 'function') showToast(r.success
    ? `🤖 Bot ${r.symbol} ${r.side === 'BUY' ? 'AL' : 'SAT'} ${r.lot} lot açtı${r.ticket != null ? ` (#${r.ticket})` : ''}`
    : `🤖 Bot ${r.symbol} ${r.side === 'BUY' ? 'AL' : 'SAT'} sinyali açılamadı: ${botHistoryReason(r.error, r.source)}`, r.success ? 'success' : 'error');
}
function announceBotPending(list) {
  const fresh = (list || []).filter(p => p && p.id && !announcedBotPending.has(p.id));
  fresh.forEach(p => announcedBotPending.add(p.id));
  if (!botPendingPrimed) { botPendingPrimed = true; return; }
  if (!fresh.length || !signalAutoAlertEnabled) return;
  playSignalBeep(fresh[0].side === 'BUY', false);
  if (typeof showToast === 'function') showToast(`🔔 Bot sinyali: ${fresh[0].symbol} ${fresh[0].side === 'BUY' ? 'AL' : 'SAT'} · onayın bekleniyor`, 'info');
}

// Automatic bot order: a longer square-wave "cash register" chord (C-E-G-C, twice) for an opened
// trade and a falling low buzz for a refused one. Scanner signals keep the short three-note beep.
function playBotTradeSound(kind = 'opened', isTest = false) {
  try {
    const ctx = ensureSignalAudioCtx();
    if (!ctx) return;
    if (ctx.state !== 'running' && !isTest && !signalAudioHintShown && !navigator.userActivation?.hasBeenActive) {
      signalAudioHintShown = true;
      if (typeof showToast === 'function') showToast('🔇 Tarayıcı sesi engelledi. Bot sesleri için sayfada bir kez herhangi bir yere tıklayın.', 'info');
    }
    const now = ctx.currentTime;
    const notes = kind === 'failed'
      ? [[220, 0, 0.35, 'sawtooth', 150]]
      : [[523.25, 0, .12], [659.25, .1, .12], [783.99, .2, .12], [1046.5, .3, .28],
         [523.25, .7, .12], [659.25, .8, .12], [783.99, .9, .12], [1046.5, 1.0, .35]];
    for (const [freq, at, length, type = 'square', endFreq] of notes) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      const start = now + at;
      osc.frequency.setValueAtTime(freq, start);
      if (endFreq) osc.frequency.exponentialRampToValueAtTime?.(endFreq, start + length);
      gain.gain.setValueAtTime(0.12, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + length);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + length + 0.02);
    }
  } catch (_) {}
}

function toggleSignalAutoAlert(enabled) {
  signalAutoAlertEnabled = Boolean(enabled);
  if (signalAutoAlertEnabled) {
    playSignalBeep(true, true);
  }
}

let signalAudioHintShown = false;
function playSignalBeep(isBuy = true, isTest = false) {
  try {
    const ctx = ensureSignalAudioCtx();
    if (!ctx) return;
    // Browsers keep audio muted until the page has been clicked once after loading.
    if (ctx.state !== 'running' && !isTest && !signalAudioHintShown && !navigator.userActivation?.hasBeenActive) {
      signalAudioHintShown = true;
      if (typeof showToast === 'function') showToast('🔇 Tarayıcı sesi engelledi. Sinyal sesleri için sayfada bir kez herhangi bir yere tıklayın.', 'info');
    }
    const now = ctx.currentTime;
    const freqs = isBuy ? [587.33, 880, 1174.66] : [880, 659.25, 440];
    freqs.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = isBuy ? 'triangle' : 'sine';
      const start = now + idx * 0.11;
      osc.frequency.setValueAtTime(freq, start);
      gain.gain.setValueAtTime(0.22, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.14);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.15);
    });
  } catch (_) {}
}

// Briefly flash fields that were changed programmatically so the eye finds them.
function flashFields(nodes) {
  for (const node of nodes) {
    if (!node) continue;
    node.classList.remove('field-flash');
    void node.offsetWidth;                       // restart the animation if it is already running
    node.classList.add('field-flash');
    clearTimeout(node._flashTimer);
    node._flashTimer = setTimeout(() => node.classList.remove('field-flash'), 1900);   // also covers reduced motion
  }
}

function applyScannedSignalToTicket(symbol, slPrice, tpPrice) {
  // The order window lives on the Grafik screen; the scanner is on the Bot screen, so switch back first.
  if (window.MainView && window.MainView.get() !== 'chart') window.MainView.set('chart', {scroll: false});
  const symbolChanged = !!symbol && symbol !== currentSymbol;
  if (symbolChanged) switchSymbol(symbol);
  const stopMode = document.getElementById('order-stop-mode');
  const modeChanged = !!stopMode && stopMode.value !== 'price';
  if (stopMode) stopMode.value = 'price';
  const slInput = document.getElementById('sl-input');
  const tpInput = document.getElementById('tp-input');
  const changed = [];
  for (const [input, value] of [[slInput, slPrice], [tpInput, tpPrice]]) {
    if (!input || !Number.isFinite(value) || value <= 0) continue;
    input.step = 'any';
    if (input.value !== String(value)) changed.push(input);
    input.value = String(value);
  }
  ticketProtectionEdited = true;
  if (typeof saveOrderTicketPreferences === 'function' && modeChanged) saveOrderTicketPreferences();
  if (typeof scheduleTradePreview === 'function') scheduleTradePreview();

  // Bring the order window into view, focus the volume and flash what changed.
  const card = document.getElementById('order-card');
  const lot = document.getElementById('lot-input');
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  card?.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'});
  setTimeout(() => {
    if (lot) { lot.focus({preventScroll: true}); lot.select?.(); }
    flashFields([card, ...(symbolChanged ? [document.getElementById('order-symbol-select'), document.getElementById('order-symbol-tag')] : []),
      ...changed, lot]);
  }, reduce ? 0 : 450);
  if (typeof showToast === 'function') {
    showToast(`🎯 ${symbol} SL ${slPrice || '—'} / TP ${tpPrice || '—'} emir penceresine aktarıldı. Hacmi kontrol edip Sell / Buy'a basın.`, 'info');
  }
}

// Symbol change from the scanner: the same Market Watch window as the chart (MT5: one symbol list for every window).
function openScannerSymbolPicker(trigger) {
  if (window.MT5Markets?.open) window.MT5Markets.open(trigger);
}
function renderScannerSymbolButton(symbol) {
  const name = document.getElementById('scanner-symbol-name');
  if (name) name.textContent = symbol || '';
}

// Scanner mode: "SELECTED" (default) shows only the chart's symbol; "ALL" scans a watch list.
const SCAN_MODE_KEY = 'hma.scanner.mode.v1';
function applyScannerModeUi(mode) {
}
function onScannerModeChange(mode) {
  mode = mode === 'ALL' ? 'ALL' : 'SELECTED';
  try { localStorage.setItem(SCAN_MODE_KEY, mode); } catch (_) { /* session-only */ }
  applyScannerModeUi(mode);
  refreshSignalScanner(true, true);
}
(function restoreScannerMode() {
  const init = () => {
    const select = document.getElementById('signal-scan-mode');
    if (!select) return;
    let mode = 'SELECTED';
    try { mode = localStorage.getItem(SCAN_MODE_KEY) === 'ALL' ? 'ALL' : 'SELECTED'; } catch (_) {}
    select.value = mode;
    applyScannerModeUi(mode);
  };
  if (typeof document === 'undefined' || !document.addEventListener) return;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

var latestScannedSignals = [];
if (typeof window !== 'undefined') window.latestScannedSignals = latestScannedSignals;

// "Şimdi AL/SAT": opens a market order right away (like MT5's Buy/Sell by Market); the label shows side and lot.
function nowTradeLot() {
  const bot = latestBotSettings;
  if (!bot) return '';
  return bot.lot_mode === 'RISK' ? `risk %${bot.risk_pct}` : `${bot.lot_size} lot`;
}
function nowTradeLabel(item, tradable) {
  if (!tradable) return '⚡ Şimdi işleme gir';
  const lot = nowTradeLot();
  return `⚡ Şimdi ${item.side === 'BUY' ? 'AL' : 'SAT'}${lot ? ' · ' + lot : ''}`;
}
function nowTradeTitle(item, sl, tp) {
  const digits = Number.isInteger(item.digits) ? item.digits : 5;
  const fmt = v => Number(v) > 0 ? Number(v).toFixed(digits) : 'yok';
  return `Bu sinyalin yönünde hemen piyasa emri açar · SL ${fmt(sl)} · TP ${fmt(tp)} · MT5'te elle açılan işlem gibi manueldir; bot yönetmez`;
}
// Past signals of the history test, as a list (time on the MT5 server clock, like MT5's chart).
const openSignalTradeLists = new Set();
function brokerDateTime(epoch) {
  return new Date(Number(epoch) * 1000).toLocaleString('tr-TR', {timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
}
function signalTradesHtml(item, tf) {
  const trades = item?.stats?.trades;
  if (!Array.isArray(trades) || !trades.length) return '';
  const digits = Number.isInteger(item.digits) ? item.digits : 5;
  const key = `${item.symbol}:${tf}`;
  const names = window.HMASignalSettings?.filterNames || {};
  const rowsHtml = trades.map(t => {
    const buy = t.side === 'BUY';
    const result = t.outcome === 'win' ? `<span class="good">TP ✓ +${t.r}R</span>`
      : t.outcome === 'loss' ? '<span class="bad">SL ✗ −1R</span>'
      : t.outcome === 'open' ? '<span class="muted">Açık</span>'
      : `<span class="muted">Filtre: ${escapeHtml((t.filters || []).map(k => names[k] || k).join(', '))}</span>`;
    return `<tr data-outcome="${escapeHtml(t.outcome)}"><td>${brokerDateTime(t.time)}</td>`
      + `<td><span class="signal-trade-side ${buy ? 'is-buy' : 'is-sell'}">${buy ? 'AL' : 'SAT'}</span></td>`
      + `<td>${Number(t.entry).toFixed(digits)}</td><td>${result}</td><td>${t.bars ? t.bars + ' mum' : '—'}</td></tr>`;
  }).join('');
  return `<details class="signal-trades" ${openSignalTradeLists.has(key) ? 'open' : ''} ontoggle="toggleSignalTrades('${escapeHtml(key)}', this.open)">
    <summary>Sinyal listesi (${trades.length}) · en yeni üstte · sunucu saati</summary>
    <div class="signal-trades-scroll"><table><thead><tr><th>Tarih</th><th>Yön</th><th>Giriş</th><th>Sonuç</th><th>Süre</th></tr></thead><tbody>${rowsHtml}</tbody></table></div>
  </details>`;
}
function toggleSignalTrades(key, open) { if (open) openSignalTradeLists.add(key); else openSignalTradeLists.delete(key); }

// Same rule on M5 / M15 / H1 / H4 for the selected symbol (refreshed at most once a minute).
const signalMtfCache = new Map();
const SIGNAL_MTF_TTL = 60000;
function signalMtfKey(sym) {
  const s = window.HMASignalSettings?.get?.() || {};
  return `${sym}|${s.fast_type || 'HMA'}|${s.hma_period ?? 14}|${s.ema_period ?? 34}|${s.ma_type || 'EMA'}|${s.confirm_close !== false}`;
}
function signalMtfHtml(data) {
  if (!data) return '<span class="signal-mtf-label">Zaman dilimleri yükleniyor…</span>';
  if (data.error) return `<span class="signal-mtf-label">Zaman dilimleri alınamadı.</span>`;
  const code = m => ({1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'}[m] || `M${m}`);
  const chips = data.timeframes.map(r => {
    if (!r.available) return `<span class="signal-mtf-chip" data-side="none">${code(r.timeframe_minutes)} —</span>`;
    const buy = r.side === 'BUY', sell = r.side === 'SELL';
    const cross = r.signal === 'BUY_CROSS' || r.signal === 'SELL_CROSS';
    const ago = r.bars_ago == null ? '25+' : r.bars_ago;
    return `<span class="signal-mtf-chip" data-side="${buy ? 'buy' : sell ? 'sell' : 'none'}" title="${code(r.timeframe_minutes)}: ${cross ? 'yeni kesişim' : `son kesişim ${ago} mum önce`}">`
      + `${code(r.timeframe_minutes)} ${buy ? '▲ AL' : sell ? '▼ SAT' : '• Nötr'}${cross ? ' · yeni' : ''}</span>`;
  }).join('');
  const total = data.timeframes.length;
  const summary = data.aligned
    ? `<b class="${data.aligned === 'BUY' ? 'good' : 'bad'}">${total}/${total} ${data.aligned === 'BUY' ? 'AL' : 'SAT'} · uyumlu</b>`
    : `<span>${data.buy} AL · ${data.sell} SAT · karışık</span>`;
  return `<span class="signal-mtf-label">Zaman dilimleri</span>${chips}<span class="signal-mtf-summary">${summary}</span>`;
}
async function refreshSignalMtf(sym) {
  if (!sym) return;
  const key = signalMtfKey(sym), cached = signalMtfCache.get(key);
  const paint = () => document.querySelectorAll(`[data-mtf-symbol="${window.CSS?.escape ? CSS.escape(sym) : sym}"]`)
    .forEach(n => { n.innerHTML = signalMtfHtml(signalMtfCache.get(key)); });
  if (cached && (cached.loading || Date.now() - cached.at < SIGNAL_MTF_TTL)) { paint(); return; }
  signalMtfCache.set(key, {...(cached || {}), loading: true, at: cached?.at || 0, timeframes: cached?.timeframes || []});
  const s = window.HMASignalSettings?.get?.() || {};
  try {
    const q = new URLSearchParams({symbol: sym, timeframes: '5,15,60,240', hma_period: s.hma_period ?? 14, fast_type: s.fast_type || 'HMA',
      ema_period: s.ema_period ?? 34, ma_type: s.ma_type || 'EMA', confirm_close: String(s.confirm_close !== false)});
    const res = await fetch(`/api/signals/mtf?${q}`);
    const data = await res.json();
    if (!res.ok || !Array.isArray(data.timeframes)) throw new Error('mtf');
    signalMtfCache.set(key, {...data, at: Date.now()});
  } catch (_) {
    signalMtfCache.set(key, {error: true, at: Date.now() - SIGNAL_MTF_TTL + 15000, timeframes: []});   // retry in ~15 s
  }
  paint();
}

// Scan right when the candle closes on the broker server clock (the EA's new-bar moment), so a new
// crossover shows within about a second instead of up to 10 s later. MT5 starts the new bar on its
// first tick, so a few follow-up scans catch a late first tick. The 10 s scan stays as a backup and
// keeps the forming candle's price/RSI current; it also re-aims this timer after a timeframe or
// server-clock change.
let candleCloseScanTimer = null;
const CANDLE_CLOSE_SCANS_MS = [1000, 3000, 6000];
function nextCandleCloseDelayMs(tfMinutes, offsetSeconds, nowMs = Date.now()) {
  const period = (Number(tfMinutes) || 15) * 60;
  const serverNow = (nowMs + 500) / 1000 + (Number(offsetSeconds) || 0);   // +0.5 s: never re-aim at the close just passed
  const next = Math.floor(serverNow / period) * period + period;
  return Math.max(0, Math.round((next - serverNow) * 1000) + 500);
}
function scheduleCandleCloseScan() {
  if (candleCloseScanTimer) clearTimeout(candleCloseScanTimer);
  const tf = Number(document.getElementById('signal-scan-tf')?.value || 15) || 15;
  const offset = typeof latestBotSettings !== 'undefined' ? latestBotSettings?.clock_offset_seconds : 0;
  candleCloseScanTimer = setTimeout(() => {
    for (const after of CANDLE_CLOSE_SCANS_MS) setTimeout(() => refreshSignalScanner(false), after);
    scheduleCandleCloseScan();
  }, nextCandleCloseDelayMs(tf, offset));
}

async function refreshSignalScanner(manual = false, forceReplace = false) {
  if (signalScannerBusy && !forceReplace && !manual) return;
  const listNode = document.getElementById('signal-scanner-list');
  const statusNode = document.getElementById('signal-scanner-status');
  if (!listNode) return;
  const seq = ++signalScanSeq;
  if (signalScanController) signalScanController.abort();
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  signalScanController = controller;
  const isCurrent = () => seq === signalScanSeq;
  signalScannerBusy = true;
  try {
    const tfNode = document.getElementById('signal-scan-tf');
    const tf = Number(tfNode?.value || 15) || 15;
    const mode = document.getElementById('signal-scan-mode')?.value || 'SELECTED';
    const activeSym = currentSymbol || document.getElementById('order-symbol-select')?.value || 'EURUSD';

    let symbolsToScan = [];
    if (mode === 'SELECTED') {
      symbolsToScan = [activeSym];
    } else {
      // Like MT5's Market Watch: only symbols the user chose (★ list) plus the selected one.
      // Never a built-in list of symbols the user did not pick.
      const watchList = typeof window.MT5Markets?.favorites === 'function' ? window.MT5Markets.favorites() : [];
      symbolsToScan = Array.from(new Set([activeSym, ...watchList])).slice(0, 8);
    }

    if (statusNode && (manual || forceReplace)) statusNode.textContent = mode === 'SELECTED'
      ? `${activeSym} taranıyor…`
      : symbolsToScan.length > 1 ? `${symbolsToScan.length} sembol taranıyor (seçili + ★ listen)…`
      : `${activeSym} taranıyor · Çoklu tarama için Piyasa Gözlemi'nde sembollere ★ ekleyin.`;
    if (forceReplace && mode === 'SELECTED') {
      listNode.innerHTML = `<div class="text-gray-400 text-xs py-3 text-center">${escapeHtml(activeSym)} için sinyal hesaplanıyor…</div>`;
    }
    const settingsQuery = window.HMASignalSettings?.query?.() || '';
    const sigSettings = window.HMASignalSettings?.get?.() || {};
    const res = await fetch(`/api/signals/scan?symbols=${encodeURIComponent(symbolsToScan.join(','))}&timeframe_minutes=${tf}${settingsQuery ? '&' + settingsQuery : ''}`,
      controller ? {signal: controller.signal} : undefined);
    if (!res.ok) throw new Error('Sinyal taraması alınamadı');
    const data = await res.json();
    // Drop results that belong to an older symbol / timeframe / mode selection.
    if (!isCurrent() || activeSym !== currentSymbol || Number(data.timeframe_minutes) !== tf
        || (document.getElementById('signal-scan-mode')?.value || 'SELECTED') !== mode) return;
    let rows = Array.isArray(data.signals) ? data.signals : [];
    // Selected symbol always first so a symbol switch is visible at a glance.
    rows = [...rows.filter(r => r.symbol === activeSym), ...rows.filter(r => r.symbol !== activeSym)];
    if (mode === 'SELECTED') {
      rows = rows.filter(r => r.symbol === activeSym);
    }
    const allRows = rows;
    if (sigSettings.only_crosses) rows = rows.filter(r => r.signal === 'BUY_CROSS' || r.signal === 'SELL_CROSS');
    latestScannedSignals = rows;
    if (typeof window !== 'undefined') window.latestScannedSignals = rows;
    // Keep the "Taranan sinyal" line in the bot cards current with the new scan.
    if (latestBotSettings && typeof renderBotSymbolStatus === 'function') renderBotSymbolStatus(latestBotSettings);
    // Header badge on the "Sinyal & Bot" toggle: directional signals and fresh crossovers.
    try {
      window.dispatchEvent(new CustomEvent('hma:scanner', {detail: {
        signals: rows.filter(r => r.side === 'BUY' || r.side === 'SELL').length,
        crosses: rows.filter(r => r.signal === 'BUY_CROSS' || r.signal === 'SELL_CROSS').length,
        timeframe: tf}}));
    } catch (_) {}

    const tfText = {1:'1 dk',5:'5 dk',15:'15 dk',30:'30 dk',60:'1 sa',240:'4 sa',1440:'1 gün'}[tf] || `${tf} dk`;
    const tfCode = {1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4',1440:'D1'}[tf] || `M${tf}`;
    const used = data.settings || {};
    const fastLabel = `${used.fast_type || "HMA"}(${used.hma_period || data.hma_period || 14})`;
    const slowLabel = `${used.ma_type || 'EMA'}(${used.ema_period || data.ema_period || 34})`;
    const rsiLabel = `RSI(${used.rsi_period || 14})`;
    if (statusNode) {
      statusNode.textContent = `${rows.length} Sinyal (${tfText} mum) · ${fastLabel} × ${slowLabel} · Son: ${new Date().toLocaleTimeString('tr-TR')}`;
    }
    if (!rows.length) {
      listNode.innerHTML = allRows.length && sigSettings.only_crosses
        ? `<div class="text-gray-400 text-xs py-3 text-center">${escapeHtml(activeSym)} için yeni kesişim yok (yalnız kesişimler gösteriliyor).</div>`
        : `<div class="text-gray-400 text-xs py-3 text-center">${escapeHtml(activeSym)} için MT5 mum verisi bekleniyor…</div>`;
      return;
    }
    listNode.innerHTML = rows.map(item => {
      const isCrossBuy = item.signal === 'BUY_CROSS';
      const isCrossSell = item.signal === 'SELL_CROSS';
      const isBuy = item.side === 'BUY';
      const isSell = item.side === 'SELL';
      const tradable = isBuy || isSell;
      const isActive = item.symbol === activeSym;
      const badgeClass = !tradable
        ? 'bg-gray-800 text-gray-300 border border-gray-700'
        : isCrossBuy
        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold'
        : isCrossSell
        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 font-bold'
        : isBuy
        ? 'bg-emerald-950/50 text-emerald-400 border border-emerald-800/50'
        : 'bg-rose-950/50 text-rose-400 border border-rose-800/50';
      const filterNames = window.HMASignalSettings?.filterNames || {};
      const filterText = (item.filters || []).map(k => filterNames[k] || k).join(', ');
      const badgeText = item.signal === 'FILTERED'
        ? `⛔ Filtrelendi${filterText ? ' · ' + filterText : ''}`
        : !tradable
        ? '• Nötr (yön yok)'
        : isCrossBuy
        ? '▲ YENİ AL (Kesişim)'
        : isCrossSell
        ? '▼ YENİ SAT (Kesişim)'
        : isBuy
        ? `↗ AL Trend (${item.bars_ago == null ? '25+' : item.bars_ago} mum)`
        : `↘ SAT Trend (${item.bars_ago == null ? '25+' : item.bars_ago} mum)`;
      const key = `${item.symbol}:${tf}:${item.signal}:${item.candle_time}`;
      if (signalAutoAlertEnabled) {
        const prevKey = announcedSignalCrosses.get(`${item.symbol}:${tf}`);
        const isNewCrossover = (isCrossBuy || isCrossSell) && prevKey !== key;
        if (isNewCrossover) {
          announcedSignalCrosses.set(`${item.symbol}:${tf}`, key);
          playSignalBeep(isCrossBuy, false);
          if (typeof showToast === 'function') {
            showToast(`${isCrossBuy ? '🟢 YENİ AL SİNYALİ' : '🔴 YENİ SAT SİNYALİ'}: ${item.symbol} (${tfText}) @ ${item.price}`, isCrossBuy ? 'success' : 'info');
          }
        } else if (!prevKey) {
          announcedSignalCrosses.set(`${item.symbol}:${tf}`, key);
        }
      }
      const symEsc = escapeHtml(item.symbol);
      const digits = Number.isInteger(item.digits) ? item.digits : 5;
      const fmtNum = (val) => (val != null && !isNaN(Number(val))) ? Number(val).toFixed(digits) : '—';
      const sl = Number(item.suggested_sl || 0);
      const tp = Number(item.suggested_tp || 0);
      const slFmt = sl ? fmtNum(sl) : '—';
      const tpFmt = tp ? fmtNum(tp) : '—';
      const hmaFmt = fmtNum(item.hma);
      const emaFmt = fmtNum(item.ema);
      const priceFmt = fmtNum(item.price);
      return `<div class="bg-[#0f1218] border ${isActive ? 'border-cyan-500/70 ring-1 ring-cyan-500/30' : 'border-gray-800/90'} rounded-xl p-3 space-y-2.5" data-scan-symbol="${symEsc}">
        <div class="flex items-center justify-between gap-2">
          <div class="min-w-0">
            <span class="font-bold text-white text-[13px] block tracking-wide"><button type="button" class="scan-symbol-button" onclick="openScannerSymbolPicker(this)" aria-haspopup="dialog" title="Sembol değiştir (Piyasa Gözlemi)">${symEsc}<i class="ph-bold ph-caret-down" aria-hidden="true"></i></button>${isActive ? ' <span class="text-xs px-1.5 py-0.5 rounded bg-cyan-600 text-white font-bold align-middle">SEÇİLİ</span>' : ''} <span class="text-xs px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-mono font-bold">${tfText} (${tfCode})</span></span>
            <span class="text-xs text-gray-200 font-mono">Fiyat: <strong class="text-white">${priceFmt}</strong> · ${rsiLabel}: <strong>${Number(item.rsi || 50).toFixed(1)}</strong> · ATR: <strong>${item.atr}</strong></span>
          </div>
          <span class="px-2.5 py-1.5 rounded-lg text-xs font-bold ${badgeClass}">${badgeText}</span>
        </div>
        <div class="grid grid-cols-2 gap-2 text-xs font-mono bg-[#14181f] p-2.5 rounded-lg border border-gray-800/80">
          <div class="text-gray-300">${fastLabel}: <span class="text-cyan-300 font-bold">${hmaFmt}</span></div>
          <div class="text-gray-300">${slowLabel}: <span class="text-amber-300 font-bold">${emaFmt}</span></div>
          <div class="text-gray-300">Önerilen SL: <span class="text-rose-300 font-bold">${slFmt}</span></div>
          <div class="text-gray-300">Önerilen TP: <span class="text-emerald-300 font-bold">${tpFmt}</span></div>
        </div>
        ${item.reason ? `<p class="signal-reason"><i class="ph-bold ph-info"></i> <span>${escapeHtml(item.reason)}</span></p>` : ''}
        ${item.stats ? `<p class="signal-stats"><i class="ph-bold ph-flask"></i> ${window.HMASignalSettings?.statsHtml?.(item.stats) || ''}</p>` : ''}
        ${signalTradesHtml(item, tf)}
        ${isActive ? `<div class="signal-mtf" data-mtf-symbol="${symEsc}">${signalMtfHtml(signalMtfCache.get(signalMtfKey(item.symbol)))}</div>` : ''}
        <div class="flex items-center gap-2 pt-0.5">
          <button type="button" onclick="applyScannedSignalToTicket('${symEsc}', ${sl}, ${tp})" class="flex-1 py-2 px-2.5 rounded-lg bg-cyan-700/80 hover:bg-cyan-600 text-white text-xs font-bold transition active:scale-95" title="SL/TP değerlerini manuel emir formuna aktar">🎯 ${isBuy ? 'AL · ' : isSell ? 'SAT · ' : ''}Emre Aktar</button>
          <button type="button" onclick="assignScannerSymbolToBot('${symEsc}', ${tf})" class="flex-1 py-2 px-2.5 rounded-lg bg-emerald-700/80 hover:bg-emerald-600 text-white text-xs font-bold transition active:scale-95" title="İşlem açmaz: ${symEsc} botun sembolü olur, bot bir sonraki yeni kesişimi bekler">🤖 Botla İzle</button>
          <button type="button" ${tradable ? `title="${escapeHtml(nowTradeTitle(item, sl, tp))}"` : `disabled title="${item.signal === 'FILTERED' ? escapeHtml('Filtre bu sinyali engelledi: ' + (filterText || 'filtre')) : 'Nötr sinyal: yön yok, emir gönderilemez'}"`} onclick="sendSignalDirectToBot('${symEsc}', '${item.side}', ${item.price}, {signalId: 'scan-${symEsc}-${tf}-${item.side}-${item.candle_time}', slPrice: ${sl}, tpPrice: ${tp}})" class="disabled:opacity-40 disabled:cursor-not-allowed flex-1 py-2 px-2.5 rounded-lg ${!tradable ? 'bg-gray-700' : item.side === 'BUY' ? 'bg-sky-700 hover:bg-sky-600' : 'bg-rose-700 hover:bg-rose-600'} text-white text-xs font-bold transition active:scale-95">${escapeHtml(nowTradeLabel(item, tradable))}</button>
        </div>
      </div>`;
    }).join('');
    refreshSignalMtf(activeSym);
  } catch (err) {
    if (!isCurrent() || err?.name === 'AbortError') return;
    if (statusNode) statusNode.textContent = 'Sinyal taraması şu an yapılamadı.';
    if (listNode && /yükleniyor|hesaplanıyor/.test(listNode.textContent)) {
      listNode.innerHTML = '<div class="text-amber-300/80 text-xs py-3 text-center font-medium">Sinyal servisine ulaşılamadı, yeniden deneniyor…</div>';
    }
  } finally {
    if (isCurrent()) { signalScannerBusy = false; signalScanController = null; }
  }
}

let latestPendingBotSignal = null;

async function fetchBotLiveSignal() {
  try {
    const res = await fetch('/api/bot/live-signal');
    if (!res.ok) return;
    const data = await res.json();
    const execMode = document.getElementById('bot-exec-mode');
    const rsiFilter = document.getElementById('bot-rsi-filter');
    const beToggle = document.getElementById('bot-be-toggle');
    const trailInput = document.getElementById('bot-trail-input');
    if (execMode && document.activeElement !== execMode && data.execution_mode) execMode.value = data.execution_mode;
    if (rsiFilter && document.activeElement !== rsiFilter) rsiFilter.checked = Boolean(data.use_rsi_filter);
    if (beToggle && document.activeElement !== beToggle) beToggle.checked = Boolean(data.use_breakeven);
    if (trailInput && document.activeElement !== trailInput && data.trailing_stop_points != null) trailInput.value = String(data.trailing_stop_points);

    const quickLot = document.getElementById('bot-quick-lot');
    if (quickLot && document.activeElement !== quickLot && latestBotSettings?.lot_size != null) {
      quickLot.value = String(latestBotSettings.lot_size);
    }

    if (data.available) {
      const hmaNode = document.getElementById('bot-disp-hma-val');
      const ma2Node = document.getElementById('bot-disp-ma2-val');
      if (hmaNode && (!latestBotSettings?.current_hma || Number(latestBotSettings.current_hma) === 0)) {
        hmaNode.innerText = String(data.hma ?? '0.00000');
      }
      if (ma2Node && (!latestBotSettings?.current_ma2 || Number(latestBotSettings.current_ma2) === 0)) {
        ma2Node.innerText = String(data.ma2 ?? '0.00000');
      }
      const trendNode = document.getElementById('bot-live-trend-status');
      if (trendNode) {
        const trendText = data.trend === 'BUY' ? '▲ AL Yönlü' : data.trend === 'SELL' ? '▼ SAT Yönlü' : 'Yatay';
        const relation = data.trend === 'BUY' ? 'HMA, yavaş ortalamanın üstünde' : data.trend === 'SELL' ? 'HMA, yavaş ortalamanın altında' : 'HMA ve yavaş ortalama eşit';
        trendNode.textContent = `${trendText} · ${relation} (fark ${data.gap}) · RSI(14) ${Number(data.rsi || 50).toFixed(1)}`;
        trendNode.className = 'bot-line bot-trend-line ' + (data.trend === 'BUY' ? 'text-emerald-300' : data.trend === 'SELL' ? 'text-rose-300' : 'text-cyan-300');
      }
    }

    const box = document.getElementById('bot-pending-signal-box');
    const text = document.getElementById('bot-pending-signal-text');
    const list = document.getElementById('bot-pending-signals-list');
    const allBtn = document.getElementById('bot-execute-all-signals-btn');
    const pendingList = Array.isArray(data.pending_signal_actions) && data.pending_signal_actions.length > 0
      ? data.pending_signal_actions
      : (data.pending_signal_action && data.pending_signal_action.side ? [data.pending_signal_action] : []);

    try { window.dispatchEvent(new CustomEvent('hma:bot-pending', {detail: {count: pendingList.length}})); } catch (_) {}
    announceBotPending(pendingList);
    if (box) {
      if (pendingList.length > 0) {
        latestPendingBotSignal = pendingList[0];
        box.classList.remove('hidden');
        if (text) text.textContent = `🔔 ${pendingList.length} Sinyal Yakalandı (Onay Bekliyor)`;
        if (allBtn) allBtn.classList.toggle('hidden', pendingList.length <= 1);
        if (list) {
          list.innerHTML = pendingList.map(p => {
            const sym = escapeHtml(p.symbol);
            const sideClass = p.side === 'BUY' ? 'text-emerald-300' : 'text-rose-300';
            return `<div class="flex items-center justify-between gap-2 p-2 rounded-lg bg-[#14181f] border border-gray-800">
              <div class="min-w-0">
                <span class="font-bold text-white text-xs">${sym} <span class="${sideClass} font-black">${p.side === 'BUY' ? '▲ AL' : '▼ SAT'}</span></span>
                <span class="text-xs text-gray-400 font-mono block">@ ${p.price} · Saat: ${p.time || '--:--'}</span>
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                <button type="button" onclick="sendSignalDirectToBot('${sym}', '${p.side}', ${p.price}, {signalId: '${escapeHtml(p.id)}', slPoints: ${Number(p.sl_points) || 0}, tpPoints: ${Number(p.tp_points) || 0}})" class="px-2.5 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition">Uygula</button>
                <button type="button" onclick="dismissPendingBotSignal('${sym}')" class="px-2 py-1 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white font-bold text-xs transition" title="Kapat">✕</button>
              </div>
            </div>`;
          }).join('');
        }
      } else {
        latestPendingBotSignal = null;
        box.classList.add('hidden');
        if (list) list.innerHTML = '';
      }
    }
  } catch (_) {}
}

async function saveBotQuickLot() {
  try {
    if (typeof isBotRunning !== 'undefined' && isBotRunning) {
      showToast('Lot miktarını değiştirmeden önce çalışan botu durdurun.', 'warning');
      return;
    }
    const raw = parseFloat(document.getElementById('bot-quick-lot')?.value || '0.01');
    const lot = Number.isFinite(raw) && raw > 0 ? Math.min(100, Math.max(0.01, Number(raw.toFixed(2)))) : 0.01;
    const input = document.getElementById('bot-quick-lot');
    if (input) input.value = String(lot);
    const res = await fetch('/api/bot/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lot_size: lot}),
    });
    if (res.ok) {
      showToast(`✅ Bot emir lotu ${lot} olarak güncellendi.`, 'success');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(`❌ Bot lotu kaydedilemedi: ${typeof err.detail === 'string' ? err.detail : 'sunucu reddetti'}`, 'error');
    }
    fetchBotStatus();
  } catch (_) {
    showToast('❌ Bot lotu kaydedilemedi: sunucuya ulaşılamadı.', 'error');
  }
}

async function saveBotSignalRules() {
  try {
    const payload = {
      execution_mode: document.getElementById('bot-exec-mode')?.value || 'AUTO_TRADE',
      use_rsi_filter: Boolean(document.getElementById('bot-rsi-filter')?.checked),
      use_breakeven: Boolean(document.getElementById('bot-be-toggle')?.checked),
      trailing_stop_points: Math.max(0, parseInt(document.getElementById('bot-trail-input')?.value || '0', 10) || 0),
    };
    const res = await fetch('/api/bot/signal-rules', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      showToast('✅ Bot sinyal ve koruma kuralları güncellendi.', 'success');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(`❌ Bot kuralları kaydedilemedi: ${typeof err.detail === 'string' ? err.detail : 'geçersiz değer'}`, 'error');
    }
    // Re-sync the controls with what the server actually holds.
    fetchBotLiveSignal();
  } catch (_) {
    showToast('❌ Bot kuralları kaydedilemedi: sunucuya ulaşılamadı.', 'error');
  }
}

async function executePendingBotSignal() {
  if (!latestPendingBotSignal) return;
  const sig = latestPendingBotSignal;
  await sendSignalDirectToBot(sig.symbol, sig.side, sig.price, {signalId: sig.id, slPoints: sig.sl_points || 0, tpPoints: sig.tp_points || 0, once: true});
}

async function executeAllPendingBotSignals() {
  const res = await fetch('/api/bot/live-signal');
  if (!res.ok) return;
  const data = await res.json();
  const list = Array.isArray(data.pending_signal_actions) && data.pending_signal_actions.length > 0
    ? data.pending_signal_actions
    : (data.pending_signal_action ? [data.pending_signal_action] : []);
  for (const p of list) {
    await sendSignalDirectToBot(p.symbol, p.side, p.price, {signalId: p.id, slPoints: p.sl_points || 0, tpPoints: p.tp_points || 0, once: true});
  }
}

async function dismissPendingBotSignal(symbol) {
  try {
    const res = await fetch('/api/bot/clear-pending-signal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol}),
    });
    if (!res.ok) showToast('❌ Sinyal kapatılamadı.', 'error');
  } catch (_) {
    showToast('❌ Sinyal kapatılamadı: sunucuya ulaşılamadı.', 'error');
  }
  fetchBotLiveSignal();
}

// Scroll the bot card's tracked-symbols block into view and flash what changed.
function focusBotSymbol(sym, {flashToggle = false} = {}) {
  if (typeof setWorkspaceView === 'function') setWorkspaceView('signals', {focus: false});
  const anchor = document.getElementById('bot-symbols-badges');
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  (flashToggle ? document.getElementById('bot-card') : anchor?.parentElement)
    ?.scrollIntoView?.({behavior: reduce ? 'auto' : 'smooth', block: flashToggle ? 'start' : 'center'});
  setTimeout(() => {
    const esc = window.CSS?.escape ? CSS.escape(sym) : sym;
    flashFields([
      ...(flashToggle ? [document.getElementById('bot-toggle-btn'), document.getElementById('bot-status-badge')] : []),
      ...document.querySelectorAll(`[data-bot-symbol="${esc}"]`),
      flashToggle ? null : document.getElementById('bot-symbols-count'),
    ]);
  }, reduce ? 0 : 350);
}

async function assignScannerSymbolToBot(symbol, timeframeMinutes) {
  const sym = String(symbol || currentSymbol || 'EURUSD').trim();
  rememberAddedSignal(sym, timeframeMinutes);
  if (latestBotSettings?.is_running && latestBotSettings.symbol !== sym) {
    // Running bot follows the selection (MT5 EA re-init on chart symbol change).
    if (sym !== currentSymbol) switchSymbol(sym);
    if (await syncBotSymbol(sym, {announce: false})) { await fetchBotStatus(); focusBotSymbol(sym); fetchBotLiveSignal(); }
    return;
  }
  if (sym !== currentSymbol) switchSymbol(sym);
  try {
    const res = await fetch('/api/bot/add-symbol', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: sym}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === 'string' ? err.detail : 'Sembol bota eklenemedi.');
    }
    showToast(`🤖 Bot sembolü ${sym} oldu.`, 'success');
    await fetchBotStatus();        // re-render before flashing the card
    focusBotSymbol(sym);
    fetchBotLiveSignal();
  } catch (err) {
    showToast(`❌ ${err.message}`, 'error');
  }
}


const signalSendsInFlight = new Set();
async function sendSignalDirectToBot(symbol, side, price, opts = {}) {
  const base = String(opts.signalId || '');
  try {
    const sym = String(symbol || currentSymbol || '').trim();
    if (!base) throw new Error('Sinyal kimliği eksik; listeyi yenileyin.');
    // One request at a time per signal (double-click guard). A scanner click is a new order each time,
    // like MT5 one-click trading, so it gets its own id. A signal the bot caught ("Sadece Sinyal") is one
    // order: its id is kept, so approving it twice ("Uygula" + "Tümünü Uygula") maps to the same request.
    if (signalSendsInFlight.has(base)) { showToast(`⏳ ${sym} ${side} emri zaten gönderiliyor.`, 'info'); return; }
    signalSendsInFlight.add(base);
    const signalId = opts.once ? base.slice(0, 100)
      : `${base.slice(0, 80)}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    showToast(`⚡ ${sym} ${side} sinyali bota gönderiliyor...`, 'info');
    // SL/TP is sent either as prices (scanner) or as points (bot signal), never mixed.
    const body = {symbol: sym, side, signal_id: signalId, price: Number(price) || undefined};
    if (Number(opts.slPrice) > 0 || Number(opts.tpPrice) > 0) {
      body.sl_price = Number(opts.slPrice) > 0 ? Number(opts.slPrice) : undefined;
      body.tp_price = Number(opts.tpPrice) > 0 ? Number(opts.tpPrice) : undefined;
    } else {
      body.sl_points = Number(opts.slPoints) > 0 ? Math.round(Number(opts.slPoints)) : undefined;
      body.tp_points = Number(opts.tpPoints) > 0 ? Math.round(Number(opts.tpPoints)) : undefined;
    }
    const res = await fetch('/api/bot/execute-signal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const result = await res.json();
    if (!res.ok || !result.success) {
      throw new Error(result.error || (typeof result.detail === 'string' ? result.detail : '') || 'Emir iletilemedi.');
    }
    showToast(`✅ ${sym} ${side} emri HMA Bot ile açıldı! (Bilet: #${result.ticket})`, 'success');
    if (typeof fetchPositions === 'function') fetchPositions();
    fetchBotStatus();
    fetchBotLiveSignal();
  } catch (err) {
    showToast(`❌ ${err.message}`, 'error');
    fetchBotStatus();
  } finally {
    signalSendsInFlight.delete(base);
  }
}



function toggleBotHistory(force) {
  const box = document.getElementById('bot-history-box');
  const btn = document.getElementById('bot-history-toggle');
  if (!box) return;
  const open = typeof force === 'boolean' ? force : box.classList.contains('is-collapsed');
  box.classList.toggle('is-collapsed', !open);
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  try { localStorage.setItem('botHistoryOpen', open ? '1' : '0'); } catch (e) {}
}
document.addEventListener('DOMContentLoaded', () => {
  try { if (localStorage.getItem('botHistoryOpen') === '1') toggleBotHistory(true); } catch (e) {}
});


// Loss profile: which kinds of closed manual + bot trades lose (advisory; never blocks an order).
var lossProfileRequest = 0;
async function fetchLossProfile() {
  const body = document.getElementById('loss-profile-body');
  if (!body) return;
  const days = Number(document.getElementById('loss-profile-days')?.value || 90);
  const request = ++lossProfileRequest;
  body.innerHTML = '<p class="bot-history-empty">Yükleniyor…</p>';
  try {
    const res = await fetch(`/api/loss-profile?days=${days}`);
    const data = await res.json().catch(() => ({}));
    if (request !== lossProfileRequest) return;
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Zarar profili okunamadı.');
    body.innerHTML = renderLossProfile(data);
  } catch (err) {
    if (request === lossProfileRequest) body.innerHTML = `<p class="bot-history-empty">${escapeHtml(err.message)}</p>`;
  }
}
function renderLossProfile(d) {
  if (!d.trades) return `<p class="bot-history-empty">Son ${d.days} günde kapanmış manuel veya bot işlemi yok.</p>`;
  const m = (v) => typeof formatMoney === 'function' ? formatMoney(v) : Number(v).toFixed(2);
  const row = (g) => `<tr class="${g.risky ? 'is-neg' : ''}"><td>${escapeHtml(g.dim)}</td><td>${escapeHtml(g.key)}</td><td>${g.count}</td><td>%${g.win_rate_pct}</td><td class="${g.net >= 0 ? 'is-pos' : 'is-neg'}">${g.net >= 0 ? '+' : ''}${m(g.net)}</td></tr>`;
  const risky = d.groups.filter(g => g.risky), rest = d.groups.filter(g => !g.risky && g.count >= 3 && g.net < 0).slice(0, 8);
  const reasons = Object.entries(d.loss_reasons || {}).map(([k, v]) => `${escapeHtml(k)}: ${v}`).join(' · ');
  const table = (rows) => `<div class="bot-perf-table"><table><thead><tr><th>Boyut</th><th>Grup</th><th>İşlem</th><th>Kazanma</th><th>Net</th></tr></thead><tbody>${rows.map(row).join('')}</tbody></table></div>`;
  return `${d.early_sample ? `<p class="bot-perf-warn">Örnek küçük (${d.trades} işlem). Küçük gruplar tesadüf olabilir.</p>` : ''}
    <p class="bot-protect-note">${d.trades} kapanmış işlem (manuel + bot) · net ${d.net >= 0 ? '+' : ''}${m(d.net)} · kazanma %${d.win_rate_pct}${reasons ? ` · zarar kapanışları: ${reasons}` : ''}</p>
    ${risky.length ? `<p class="bot-perf-warn">Riskli gruplar (en az ${d.min_trades} işlem, kazanma ≤%35, net zarar):</p>${table(risky)}` : '<p class="bot-history-empty">Yeterli işlemle belirgin zarar ettiren bir grup bulunamadı.</p>'}
    ${rest.length ? `<p class="bot-protect-note">Gözlemde (az işlemli, zarar eden gruplar):</p>${table(rest)}` : ''}
    <p class="bot-protect-note">Geçmiş sonuçlardır; bir grubun zarar etmesi gelecekte de edeceği anlamına gelmez. Emir penceresinde yalnız bilgi olarak uyarı çıkar, emri engellemez.</p>`;
}

// Advisory line under the order buttons: risky loss-profile groups matching the typed order (never blocks).
var lossWarnTimer = null, lossWarnSeq = 0;
function scheduleLossWarning() {
  clearTimeout(lossWarnTimer);
  lossWarnTimer = setTimeout(refreshLossWarning, 600);
}
async function refreshLossWarning() {
  const box = document.getElementById('loss-warning');
  if (!box || accountSwitching) return;
  const volume = Number(document.getElementById('lot-input')?.value);
  const protection = readOrderProtection();
  const symbol = currentSymbol, seq = ++lossWarnSeq;
  if (!(volume > 0) || !symbol) { box.hidden = true; return; }
  try {
    const parts = await Promise.all(['BUY', 'SELL'].map(async side => {
      const q = new URLSearchParams({symbol, side, volume: String(volume)});
      if (protection?.sl_price) q.set('sl_price', String(protection.sl_price));
      else if (protection?.sl_points) q.set('sl_points', String(protection.sl_points));
      const res = await fetch('/api/loss-profile/check?' + q);
      return res.ok ? (await res.json()).warnings || [] : [];
    }));
    if (seq !== lossWarnSeq) return;
    const lines = parts.flatMap((list, i) => list.map(g => `${i ? 'SAT' : 'AL'}: ${g.key} — ${g.count} işlemde %${g.win_rate_pct} kazanma, net ${g.net}`));
    box.hidden = !lines.length;
    if (lines.length) {
      box.textContent = '';
      const title = document.createElement('strong'); title.textContent = 'Geçmişte zarar eden kurulum (bilgi, emri engellemez)'; box.appendChild(title);
      lines.slice(0, 4).forEach(t => { const p = document.createElement('div'); p.textContent = t; box.appendChild(p); });
    }
  } catch (_) { box.hidden = true; }
}
['lot-input', 'sl-input', 'tp-input', 'order-stop-mode'].forEach(id => { const el = document.getElementById(id); if (el && typeof el.addEventListener === 'function') el.addEventListener('input', scheduleLossWarning); });
if (typeof setTimeout === 'function') setTimeout(scheduleLossWarning, 2500);
