// Volta Trading Dashboard Client Script

let currentSymbol = "EURUSD";
let tvWidget = null;
let isBotRunning = false;

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
  initTradingView(currentSymbol);
  initChartResizer();
  initPositionsResizer();
  initPositionsTopResizer();
  startPolling();
});

// Initialize / Reload TradingView Widget
function initTradingView(symbol) {
  const container = document.getElementById("tradingview-container");
  if (!container) return;
  container.innerHTML = ""; // Clear old container

  const tvSymbol = TV_SYMBOLS[symbol] || `FX:${symbol}`;

  if (window.TradingView) {
    new TradingView.widget({
      "autosize": true,
      "symbol": tvSymbol,
      "interval": "15",
      "timezone": "Etc/UTC",
      "theme": "dark",
      "style": "1",
      "locale": window.MT5I18n?.language?.() || "tr",
      "toolbar_bg": "#121620",
      "enable_publishing": false,
      "hide_side_toolbar": false,
      "allow_symbol_change": true,
      "container_id": "tradingview-container"
    });
  }
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
    if (fsIcon) fsIcon.className = "ph-bold ph-arrows-in-simple text-amber-400";
    if (fsText) fsText.innerText = "Küçült (ESC)";
    if (fsBtn) {
      fsBtn.className = "px-2.5 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-xs font-semibold flex items-center gap-1.5 transition border border-amber-500/30 active:scale-95 shadow-sm";
    }
  } else {
    if (fsIcon) fsIcon.className = "ph-bold ph-arrows-out-simple text-cyan-400";
    if (fsText) fsText.innerText = "Tam Ekran";
    if (fsBtn) {
      fsBtn.className = "px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-semibold flex items-center gap-1.5 transition border border-gray-700 active:scale-95 shadow-sm";
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
function switchSymbol(symbol) {
  currentSymbol = symbol;
  if (typeof startLiveFeed === "function") startLiveFeed();
  renderOrderNotice();
  document.getElementById("current-symbol-title").innerText = `${symbol} • M15`;
  document.getElementById("order-symbol-tag").innerText = symbol;

  // Highlight active tab
  document.querySelectorAll(".sym-btn").forEach(btn => {
    if (btn.innerText.trim() === symbol) {
      btn.className = "sym-btn px-2.5 py-1 rounded text-xs font-semibold bg-emerald-500/20 text-emerald-400";
    } else {
      btn.className = "sym-btn px-2.5 py-1 rounded text-xs font-semibold text-gray-400 hover:text-white";
    }
  });

  initTradingView(symbol);
  fetchPrice();
}

let currentPositionTab = "open";

let isFetchingAccount = false;
let isFetchingPositions = false;
let isFetchingHistory = false;
let isFetchingPrice = false;

// Polling loop for real-time updates
function startPolling() {
  fetchAccount();
  refreshTradingStatus();
  fetchPositions();
  fetchHistory();
  fetchPrice();
  fetchBotStatus();
  fetchAIStatus();
  fetchAIPerformance();
  setInterval(fetchAIPerformance, 300000);

  setInterval(fetchAccount, 2000);
  setInterval(fetchPositions, 2000);
  setInterval(fetchPrice, 1500);
  setInterval(fetchBotStatus, 3000);
  setInterval(refreshTradingStatus, 5000);
  setInterval(() => {
    if (currentPositionTab === "closed") fetchHistory();
    else if (currentPositionTab === "reports") fetchReports();
    else if (currentPositionTab === "ai") fetchAIStatus();
  }, 5000);
}

async function fetchAIPerformance() {
  const el = document.getElementById('ai-performance');
  if (!el) return;
  try {
    const response = await fetch('/api/ai/performance');
    if (!response.ok) return;
    const data = await response.json();
    const english = window.MT5I18n?.language?.() === 'en';
    const buckets = Object.entries(data.confidence_buckets || {}).map(([range, row]) =>
      `${range}%: ${row.count} ${english ? 'trades' : 'işlem'} / ${row.win_rate_pct ?? '-'}%`).join(' · ');
    el.innerText = english
      ? `Last ${data.days} days · ${data.account_type} · ${data.closed_positions} closed AI positions · ${data.wins} wins / ${data.losses} losses · Net ${data.net} ${data.currency}\nCosts ${data.costs} · Max drawdown ${data.max_drawdown} · Profit factor ${data.profit_factor ?? '-'} · Mean adverse slippage ${data.mean_adverse_slippage_pct ?? '-'}%\nConfidence (observed outcomes only): ${buckets}\n${data.early_sample ? 'Early sample: fewer than 30 closed positions. ' : ''}Model confidence is not a validated win probability. Costs and fills may be incomplete.`
      : `Son ${data.days} gün · ${data.account_type} · ${data.closed_positions} kapanmış AI pozisyonu · ${data.wins} kâr / ${data.losses} zarar · Net ${data.net} ${data.currency}\nMaliyetler ${data.costs} · En yüksek düşüş ${data.max_drawdown} · Kâr faktörü ${data.profit_factor ?? '-'} · Ortalama olumsuz kayma %${data.mean_adverse_slippage_pct ?? '-'}\nGüven aralıkları (yalnız gerçekleşenler): ${buckets}\n${data.early_sample ? 'Erken örneklem: 30’dan az kapanmış pozisyon. ' : ''}Modelin güveni doğrulanmış kazanma olasılığı değildir. Maliyet ve gerçekleşmeler eksik olabilir.`;
  } catch (error) { console.debug('AI performance unavailable', error); }
}

// Switch between Open, Closed Positions, Reports, and AI tabs
function switchPositionTab(tab) {
  currentPositionTab = tab;
  const btnOpen = document.getElementById("tab-btn-open");
  const btnClosed = document.getElementById("tab-btn-closed");
  const btnReports = document.getElementById("tab-btn-reports");
  const btnAI = document.getElementById("tab-btn-ai");
  const openActions = document.getElementById("open-actions-bar");
  const closedSummary = document.getElementById("closed-summary-bar");
  const reportsActions = document.getElementById("reports-actions-bar");
  const aiActions = document.getElementById("ai-actions-bar");
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
  if (aiActions) { aiActions.classList.add("hidden"); aiActions.classList.remove("flex"); }
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
    window.open("/ai", "_blank");
    return;
  }
}

function showAccountConnectionError(message) {
  const indicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  if (indicator) indicator.className = "w-2 h-2 rounded-full bg-amber-400";
  if (statusText) {
    statusText.innerText = message;
    statusText.className = "text-amber-400";
  }
}

// Fetch Account Info
let accountCurrency = "USD";
let currentAccountType = null;
function renderAccountData(data) {
    accountCurrency = data.currency || accountCurrency;
    currentAccountType = data.connected ? data.account_type : null;
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
        uptimeBadge.className = "text-[11px] font-mono px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 flex items-center gap-1";
      }
    } else if (data.connected) {
      const real = data.account_type === "REAL";
      indicator.className = `w-2 h-2 rounded-full ${real ? "bg-rose-400" : "bg-emerald-400"} animate-pulse`;
      statusText.innerText = `${real ? "GERÇEK" : "DEMO"} Hesap #${data.login}`;
      statusText.className = `${real ? "text-rose-400" : "text-emerald-400"} font-semibold`;
      if (lastSyncText) lastSyncText.innerText = `Son Veri: ${data.server_time || nowStr}`;
      if (uptimeText) {
        uptimeText.innerText = `Bağlı: ${data.connected_since || "Aktif"}`;
        uptimeBadge.className = "text-[11px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1";
      }
    } else {
      indicator.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
      statusText.innerText = "MT5 Bekleniyor / Çevrimdışı";
      statusText.className = "text-amber-400";
      if (lastSyncText) lastSyncText.innerText = `Son Veri: Bekleniyor (${nowStr})`;
      if (uptimeText) {
        uptimeText.innerText = "Bağlantı: Yok";
        uptimeBadge.className = "text-[11px] font-mono px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1";
      }
    }

    document.getElementById("acc-balance").innerText = `${accountCurrency} ${formatMoney(data.balance)}`;
    document.getElementById("acc-equity").innerText = `${accountCurrency} ${formatMoney(data.equity)}`;
    document.getElementById("acc-margin-free").innerText = `${accountCurrency} ${formatMoney(data.margin_free)}`;

    const profitEl = document.getElementById("acc-profit");
    const profitVal = data.profit || 0;
    profitEl.innerText = `${profitVal >= 0 ? "+" : ""}${accountCurrency} ${formatMoney(profitVal)}`;
    if (profitVal > 0) {
      profitEl.className = "font-bold text-emerald-400 tracking-wide";
    } else if (profitVal < 0) {
      profitEl.className = "font-bold text-rose-400 tracking-wide";
    } else {
      profitEl.className = "font-bold text-gray-300 tracking-wide";
    }
}

async function fetchAccount(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingAccount) return;
  isFetchingAccount = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch("/api/account", { signal: controller.signal });
    if (!res.ok) throw new Error(`Hesap bilgisi alınamadı (HTTP ${res.status})`);
    const data = await res.json();

    renderAccountData(data);
  } catch (err) {
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
function renderPositionsData(positions) {
    if (typeof rememberPositions === "function") rememberPositions(positions);
    const tbody = document.getElementById("positions-table-body");
    const countBadge = document.getElementById("pos-count-badge");
    if (countBadge) countBadge.innerText = positions.length;

    if (!positions || positions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" class="py-6 text-center text-gray-500 font-sans">Henüz açık pozisyon bulunmuyor.</td></tr>`;
      return;
    }

    let rowsHtml = "";
    positions.forEach(p => {
      const isBuy = p.type === "BUY";
      const typeBadge = isBuy
        ? `<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold">BUY</span>`
        : `<span class="px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 font-bold">SELL</span>`;

      const profitColor = p.profit >= 0 ? "text-emerald-400" : "text-rose-400";
      const profitSign = p.profit >= 0 ? "+" : "";

      rowsHtml += `
        <tr class="hover:bg-[#151a26]/60 transition border-b border-gray-800/40">
          <td class="py-2.5 px-3 text-gray-400">#${p.ticket}</td>
          <td class="py-2.5 px-3 font-bold text-white">${p.symbol}</td>
          <td class="py-2.5 px-3">${typeBadge}</td>
          <td class="py-2.5 px-3 text-gray-200 font-semibold">${p.volume}</td>
          <td class="py-2.5 px-3 text-gray-400">${p.price_open}</td>
          <td class="py-2.5 px-3 text-white font-semibold">${p.price_current}</td>
          <td class="py-2.5 px-3 text-gray-500">${p.sl || "-"} / ${p.tp || "-"}</td>
          <td class="py-2.5 px-3 text-right font-bold ${profitColor}">${profitSign}${accountCurrency} ${formatMoney(p.profit)}</td>
          <td class="py-2.5 px-3 text-center">
            <button onclick="showPositionEditor(${p.ticket})" class="px-2 py-1 rounded bg-cyan-500/10 text-cyan-300 text-xs">Yönet</button>
            <button onclick="closePosition(${p.ticket})" class="px-2.5 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 text-xs font-semibold transition active:scale-95" title="Kapat">
              Kapat
            </button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;
}

async function fetchPositions(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingPositions) return;
  isFetchingPositions = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch("/api/positions", { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error("Pozisyonlar doğrulanamadı");
    const positions = await res.json();

    renderPositionsData(positions);
  } catch (err) {
    const badge = document.getElementById("pos-count-badge");
    if (badge) badge.innerText = "Veri güncel değil";
  } finally {
    clearTimeout(timer);
    isFetchingPositions = false;
  }
}

// Fetch Closed Positions / Trade History
async function fetchHistory() {
  if (isFetchingHistory) return;
  isFetchingHistory = true;
  try {
    const res = await fetch("/api/history?days=30");
    if (!res.ok) return;
    const history = await res.json();

    const tbody = document.getElementById("history-table-body");
    const countBadge = document.getElementById("history-count-badge");
    const totalProfitEl = document.getElementById("history-total-profit");

    if (countBadge) countBadge.innerText = history.length;

    let totalProfit = 0;
    history.forEach(d => {
      totalProfit += (d.profit || 0) + (d.commission || 0) + (d.swap || 0) + (d.fee || 0);
    });

    if (totalProfitEl) {
      const pColor = totalProfit >= 0 ? "text-emerald-400" : "text-rose-400";
      const pSign = totalProfit >= 0 ? "+" : "";
      totalProfitEl.className = `font-bold ${pColor}`;
      totalProfitEl.innerText = `${pSign}${accountCurrency} ${formatMoney(totalProfit)}`;
    }

    if (!tbody) return;

    if (!history || history.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-gray-500 font-sans">Henüz kapalı işlem geçmişi bulunmuyor.</td></tr>`;
      return;
    }

    let rowsHtml = "";
    history.forEach(d => {
      const isBuy = d.type === "BUY";
      const typeBadge = isBuy
        ? `<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold text-[10px]">BUY</span>`
        : `<span class="px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 font-bold text-[10px]">SELL</span>`;

      const netProfit=(d.profit||0)+(d.commission||0)+(d.swap||0)+(d.fee||0);
      const profitColor = netProfit >= 0 ? "text-emerald-400" : "text-rose-400";
      const profitSign = netProfit >= 0 ? "+" : "";

      const fee = (d.swap || 0) + (d.commission || 0) + (d.fee || 0);
      const feeText = fee !== 0 ? `${fee >= 0 ? "+" : ""}${accountCurrency} ${formatMoney(fee)}` : "-";

      rowsHtml += `
        <tr class="hover:bg-[#151a26]/60 transition border-b border-gray-800/40">
          <td class="py-2.5 px-3 text-gray-400">#${d.ticket} <span class="text-[10px] text-gray-600 block">Pos: #${d.position_id || d.order}</span></td>
          <td class="py-2.5 px-3 font-bold text-white">${d.symbol}</td>
          <td class="py-2.5 px-3">${typeBadge}</td>
          <td class="py-2.5 px-3 text-gray-200 font-semibold">${d.volume}</td>
          <td class="py-2.5 px-3 text-gray-300 font-mono">${d.price}</td>
          <td class="py-2.5 px-3 text-gray-500 text-[11px]">${feeText}</td>
          <td class="py-2.5 px-3 text-gray-400 text-[11px]">${d.time}</td>
          <td class="py-2.5 px-3 text-right font-bold font-mono ${profitColor}">${profitSign}${accountCurrency} ${formatMoney(netProfit)}</td>
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
async function fetchReports() {
  try {
    const daysSelect = document.getElementById("reports-days-select");
    const days = daysSelect ? daysSelect.value : 30;
    const res = await fetch(`/api/reports?days=${days}`);
    if (!res.ok) throw new Error("Rapor doğrulanamadı");
    const data = await res.json();
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
      repTodaySub.innerText = `${s.today_trades} pozisyon tamamen kapandı · UTC`;
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

    if (repBest) repBest.innerText = `+${currency} ${formatMoney(s.best_trade)}`;
    if (repWorst) repWorst.innerText = `-${currency} ${formatMoney(Math.abs(s.worst_trade))}`;
    if (repVol) repVol.innerText = `${s.total_volume} Lot`;
    if (repFee) {
      const fee = (s.total_swap || 0) + (s.total_commission || 0) + (s.total_fee || 0);
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
            ? `<span class="text-white font-bold">${d.date}</span> <span class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[9px] uppercase font-sans font-bold">Bugün</span>`
            : `<span class="text-gray-300">${d.date}</span>`;
          
          const wrColor = d.win_rate >= 50 ? "text-emerald-400" : "text-rose-400";

          dHtml += `
            <tr class="hover:bg-[#151a26]/60 transition border-b border-gray-800/40">
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
            <tr class="hover:bg-[#151a26]/60 transition border-b border-gray-800/40">
              <td class="py-2 px-2.5 font-bold text-white whitespace-nowrap">${sym.symbol}</td>
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
    const note=document.getElementById("reports-basis");
    if (note) note.textContent="Rapor güncellenemedi; gösterilen veriler eski olabilir.";
  }
}


// Fetch Price for Active Symbol
function renderPriceData(tick) {
    if (typeof displayedTickTime !== "undefined") displayedTickTime = tick.time || 0;
    if (tick && tick.bid > 0) {
      document.getElementById("header-bid").innerText = tick.bid;
      document.getElementById("header-ask").innerText = tick.ask;
      document.getElementById("header-spread").innerText = tick.spread;
      document.getElementById("btn-bid-price").innerText = `@ ${tick.bid}`;
      document.getElementById("btn-ask-price").innerText = `@ ${tick.ask}`;
    }
}

async function fetchPrice(force = false) {
  if (!force && typeof liveFeedHealthy === "function" && liveFeedHealthy()) return;
  if (isFetchingPrice) return;
  isFetchingPrice = true;
  try {
    const res = await fetch(`/api/price/${currentSymbol}`);
    if (!res.ok) return;
    const tick = await res.json();

    renderPriceData(tick);
  } catch (err) {
    console.error("fetchPrice error:", err);
  } finally {
    isFetchingPrice = false;
  }
}

// Fetch Strategy Bot Status
async function fetchBotStatus() {
  try {
    const res = await fetch("/api/bot/status");
    if (!res.ok) return;
    const bot = await res.json();

    isBotRunning = bot.is_running;
    const badge = document.getElementById("bot-status-badge");
    const toggleBtn = document.getElementById("bot-toggle-btn");

    if (bot.is_running) {
      badge.className = "text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 animate-pulse";
      badge.innerText = "ÇALIŞIYOR";
      toggleBtn.className = "flex-1 py-2.5 rounded-xl bg-gradient-to-r from-rose-500 to-red-600 hover:from-rose-600 text-white font-bold text-xs shadow-lg shadow-red-500/20 flex items-center justify-center gap-1.5 transition";
      toggleBtn.innerHTML = `<i class="ph-bold ph-stop"></i><span>Botu Durdur</span>`;
    } else {
      badge.className = "text-[11px] font-bold px-2 py-0.5 rounded bg-gray-800 text-gray-400";
      badge.innerText = "DURDURULDU";
      toggleBtn.className = "flex-1 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-600 text-white font-bold text-xs shadow-lg shadow-cyan-500/20 flex items-center justify-center gap-1.5 transition";
      toggleBtn.innerHTML = `<i class="ph-bold ph-play"></i><span>Botu Başlat</span>`;
    }

    document.getElementById("bot-disp-hma-p").innerText = bot.hma_period;
    document.getElementById("bot-disp-hma-val").innerText = bot.current_hma || "0.00000";
    document.getElementById("bot-disp-ma2-t").innerText = bot.second_ma_type;
    document.getElementById("bot-disp-ma2-p").innerText = bot.second_ma_period;
    document.getElementById("bot-disp-ma2-val").innerText = bot.current_ma2 || "0.00000";

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
    console.error("fetchBotStatus error:", err);
  }
}

// Lot adjustments
function adjustLot(delta) {
  const lotInput = document.getElementById("lot-input");
  let val = parseFloat(lotInput.value) || 0.01;
  val = Math.max(0.01, Math.round((val + delta) * 100) / 100);
  lotInput.value = val.toFixed(2);
}

function setLot(val) {
  document.getElementById("lot-input").value = val.toFixed(2);
}

// Keep the last result per symbol; a broker rejection is not a live session calendar.
const orderNotices = new Map();
let orderPending = false;
const closePending = new Set();
function newOrderId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
}
function unresolvedIntents(symbol = currentSymbol) {
  const entries = new Map();
  try {
    const orderKey = "order-intent:" + symbol;
    if (localStorage.getItem(orderKey)) entries.set(orderKey, localStorage.getItem(orderKey));
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith("close-intent:") || key.startsWith("close-bulk:") || key.startsWith("managed-intent:")) entries.set(key, localStorage.getItem(key));
    }
  } catch (_) { /* Storage failures are reported by order submission. */ }
  return entries;
}
function tradeActionBusy() {
  return orderPending || closePending.size || (typeof actionLocks !== "undefined" && actionLocks.size);
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

function renderOrderNotice(flash = false) {
  const box = document.getElementById("order-notice");
  if (!box) return;
  const notice = orderNotices.get(currentSymbol) || {
    title: "İşlem durumu", message: "Emir sonucu ve işlem uyarıları burada gösterilir.", tone: "neutral"
  };
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

function setOrderNotice(symbol, title, message, tone, flash = false) {
  orderNotices.set(symbol, {title: `${symbol} · ${title}`, message, tone});
  if (symbol === currentSymbol) renderOrderNotice(flash);
}

function orderErrorMessage(data) {
  const raw = typeof data.detail === "string" ? data.detail : (data.error || "Emir reddedildi. Girdiğiniz değerleri kontrol edin.");
  const code = Number(data.retcode);
  if (data.uncertain) return ["Emir sonucu belirsiz", "Tekrar göndermeden önce açık pozisyonları kontrol edin."];
  if (data.reason_code === "trading_halted") return ["Yeni emirler durduruldu", raw];
  if (code === 10018 || /market closed/i.test(raw))
    return ["Son emir: piyasa kapalı", "Bu sembolde işlem seansı kapalı. Piyasa açıldığında yeniden deneyin."];
  if (code === 10027 || /autotrading disabled/i.test(raw))
    return ["İşlem izni kapalı", "MT5 Algo Trading ve Python API işlem izinlerini kontrol edin."];
  if (code === 10019 || /not enough money|no money/i.test(raw))
    return ["Yetersiz teminat", "Kullanılabilir teminatınızı ve lot miktarını kontrol edin."];
  if (code === 10016 || /invalid stops/i.test(raw))
    return ["SL / TP uygun değil", "Stop Loss ve Take Profit mesafelerini kontrol edin."];
  if (code === 10014 || /invalid volume/i.test(raw))
    return ["Lot miktarı uygun değil", "Bu sembolün minimum lot ve lot adımını kontrol edin."];
  if (code === 10017 || /trade disabled/i.test(raw))
    return ["İşlem devre dışı", "Broker bu sembol veya hesap için işlem izni vermiyor."];
  return ["Emir gönderilemedi", raw];
}

// Submit Buy/Sell Order
async function submitOrder(type) {
  if (orderPending) return;
  if (typeof scheduleTradePreview === "function") scheduleTradePreview(type);
  const symbol = currentSymbol;
  const volume = Number(document.getElementById("lot-input").value);
  if (!Number.isFinite(volume) || volume <= 0) {
    setOrderNotice(symbol, "Geçersiz lot", "Sıfırdan büyük bir lot miktarı girin.", "error");
    return;
  }
  const sl = parseInt(document.getElementById("sl-input").value) || 0;
  const tp = parseInt(document.getElementById("tp-input").value) || 0;
  let intent;
  try {
    const stored = localStorage.getItem("order-intent:" + symbol);
    if (stored) {
      intent = JSON.parse(stored);
      if (intent.order_type !== type || intent.volume !== volume || intent.sl_points !== sl || intent.tp_points !== tp) {
        setOrderNotice(symbol, "Önceki emir doğrulanmalı", "MT5 durumunu kontrol edip ‘Kontrol ettim’ düğmesini kullanın.", "error");
        return;
      }
    } else {
      intent = {request_id: newOrderId(), symbol, order_type: type, volume, sl_points: sl, tp_points: tp, comment: "Volta Web Terminal"};
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
    const res = await fetch("/api/order/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(intent)
    });
    const data = await res.json();
    if (data.request_id && !data.uncertain) localStorage.removeItem("order-intent:" + symbol);
    if (res.ok && data.success) {
      const title = data.partial ? "Emir kısmen gerçekleşti" : (data.retcode === 10008 ? "Emir kabul edildi" : "Emir gerçekleşti");
      setOrderNotice(symbol, title, `Bilet #${data.ticket} · Açık pozisyonlardan durumu takip edebilirsiniz.`, "success");
      fetchPositions();
      fetchAccount();
    } else {
      const [title, message] = orderErrorMessage(data);
      setOrderNotice(symbol, title, message, "error", true);
    }
  } catch (err) {
    setOrderNotice(symbol, "Yanıt alınamadı", "Emir sonucu belirsiz olabilir. Yeniden göndermeden önce açık pozisyonları kontrol edin.", "error", true);
  } finally {
    orderPending = false;
    refreshOrderRecovery();
    buttons.forEach(btn => { if (btn) btn.disabled = false; });
  }
}

// Close Single Position (Instant 1-Click)
async function closePosition(ticket) {
  if (closePending.has(ticket)) return;
  closePending.add(ticket);
  try {
    const key = "close-intent:" + ticket;
    const requestId = localStorage.getItem(key) || newOrderId();
    localStorage.setItem(key, requestId);
    const res = await fetch("/api/order/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket, request_id: requestId })
    });

    const data = await res.json();
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(key);
    if (res.ok && data.success) {
      showToast(`✅ #${ticket} numaralı pozisyon kapatıldı.`, "success");
      fetchPositions();
      fetchAccount();
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
    showToast("Kapatma sonucu belirsiz; MT5 durumunu kontrol edin.", "error");
  } finally {
    closePending.delete(ticket);
    refreshOrderRecovery();
  }
}

// Close Filtered Positions (all, profit, loss) - Instant 1-Click
async function refreshTradingStatus() {
  try {
    const response = await fetch('/api/trading/status');
    if (!response.ok) return;
    const state = await response.json();
    const button = document.getElementById('resume-trading-btn');
    if (button) button.classList.toggle('hidden', !state.new_orders_halted);
    const warning = document.getElementById('trading-halt-message');
    if (warning) warning.classList.toggle('hidden', !state.new_orders_halted);
  } catch (_) {}
}

async function resumeTrading() {
  try {
    const response = await fetch('/api/trading/resume', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Yeni emirler başlatılamadı.');
    showToast('Yeni emirlere yeniden izin verildi.', 'success');
    refreshTradingStatus();
  } catch (error) {
    showToast(error.message || 'Yeni emirler başlatılamadı.', 'error');
  }
}

async function closeFilteredPositions(filterType) {
  if (closePending.has("bulk")) return;
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
    const res = await fetch(endpoint, { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({request_id: requestId}) });
    const data = await res.json();
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(key);
    if (res.ok) {
      if (!data.success || (data.errors && data.errors.length)) {
        showToast(`${data.closed_count || 0} pozisyon kapatıldı. Tamamlanamayanlar: ${(data.errors || [data.error || "Sonuç belirsiz"]).join("; ")}`, "error");
      } else if (data.total_matched === 0 && !data.cancelled_count) {
        showToast("Kapatılacak uygun pozisyon bulunamadı.", "info");
      } else {
        showToast(`${data.closed_count} pozisyon kapatıldı; ${data.cancelled_count || 0} bekleyen emir iptal edildi.`, "success");
      }
      fetchPositions();
      fetchAccount();
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

// Toggle Bot
async function toggleBot() {
  try {
    const res = await fetch("/api/bot/toggle", { method: "POST" });
    const data = await res.json();
    if (data.is_running) {
      showToast("🚀 HMA Algoritmik Bot Başlatıldı!", "success");
    } else {
      showToast("🛑 HMA Algoritmik Bot Durduruldu.", "info");
    }
    fetchBotStatus();
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
  }
}

// Bot Settings Modal
function toggleBotSettingsModal() {
  const modal = document.getElementById("bot-settings-modal");
  modal.classList.toggle("hidden");
}

async function saveBotSettings() {
  const cfg = {
    symbol: document.getElementById("cfg-symbol").value,
    hma_period: parseInt(document.getElementById("cfg-hma-period").value),
    second_ma_type: document.getElementById("cfg-ma2-type").value,
    second_ma_period: parseInt(document.getElementById("cfg-ma2-period").value),
    lot_size: parseFloat(document.getElementById("cfg-lot").value),
    close_opposite: document.getElementById("cfg-close-opp").checked,
  };

  try {
    const res = await fetch("/api/bot/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg)
    });
    if (res.ok) {
      showToast("✅ Bot ayarları güncellendi!", "success");
      toggleBotSettingsModal();
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
  let bg = "bg-[#181d2a] border-gray-700 text-white";
  if (type === "success") bg = "bg-emerald-950 border-emerald-600 text-emerald-200";
  if (type === "error") bg = "bg-rose-950 border-rose-600 text-rose-200";

  toast.className = `toast px-4 py-3 rounded-xl border shadow-xl text-xs font-medium max-w-sm pointer-events-auto flex items-center gap-2 ${bg}`;
  toast.innerText = message;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.3s";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Helpers
function formatMoney(val) {
  if (val === undefined || val === null || isNaN(val)) return "0.00";
  return Number(val).toLocaleString(window.MT5I18n?.language?.() === "en" ? "en-US" : "tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.innerText = text;
  return div.innerHTML;
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
  help.className = `mt-1 text-[11px] ${real ? "text-rose-300" : "text-gray-400"}`;
}

async function submitLogin() {
  const acc = parseInt(document.getElementById("login-acc").value);
  const pass = document.getElementById("login-pass").value;
  const srv = document.getElementById("login-srv").value.trim();
  const accountType = document.getElementById("login-mode-real").checked ? "REAL" : "DEMO";
  const button = document.getElementById("login-submit-btn");

  if (!Number.isSafeInteger(acc) || acc <= 0 || !pass || !srv) {
    showToast("Lütfen tüm alanları doldurun.", "error");
    return;
  }

  if (button.disabled) return;
  button.disabled = true;
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
      fetchAccount(true);
    } else {
      showToast(`❌ Giriş Başarısız: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ Bağlantı Hatası: ${err.message}`, "error");
  } finally {
    document.getElementById("login-pass").value = "";
    button.disabled = false;
  }
}

// ==========================================
// DEEPSEEK AI ADVISOR & AUTONOMOUS ENGINE
// ==========================================

let currentAIAdvice = null;

async function fetchAIStatus() {
  try {
    const res = await fetch("/api/ai/status");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.success) return;

    renderAIMemory(data.memory, {...data.autopilot,
      mode: data.autopilot?.enabled ? data.autopilot.mode : "DISABLED"});
    const usageEl = document.getElementById("ai-usage");
    if (usageEl && data.usage) {
      usageEl.innerText = `Bugün (UTC): ${data.usage.calls}/${data.limits.daily_calls} AI çağrısı · ${data.usage.total_tokens.toLocaleString("tr-TR")}/${Number(data.limits.daily_tokens).toLocaleString("tr-TR")} raporlanan token` +
        (data.usage.unknown_usage_calls ? ` · ${data.usage.unknown_usage_calls} isteğin token bilgisi alınamadı` : "");
    }
    const breakdownEl = document.getElementById("ai-usage-breakdown");
    if (breakdownEl) {
      const rows = Object.entries(data.usage_by_operation || {}).sort((a, b) => b[1].total_tokens - a[1].total_tokens);
      const english = window.MT5I18n?.language?.() === 'en';
      breakdownEl.innerText = rows.length ? rows.map(([name, row]) => {
        const label = name === 'learn' ? (english ? 'Trading profile' : 'İşlem profili') : name === 'ping' ? (english ? 'Connection test' : 'Bağlantı testi') : name.startsWith('advice:autopilot:') ? (english ? 'Automatic scan' : 'Otomatik tarama') + ' ' + name.split(':')[2] : (english ? 'Manual advice' : 'Manuel tavsiye') + ' ' + (name.split(':')[2] || '');
        return `${label}: ${row.calls} ${english ? 'calls' : 'çağrı'} · ${Number(row.total_tokens).toLocaleString(english ? 'en-US' : 'tr-TR')} token${row.unknown_usage_calls ? ` · ${row.unknown_usage_calls} ${english ? 'unknown' : 'belirsiz'}` : ''}`;
      }).join(' | ') : '';
    }
  } catch (err) {
    console.error("AI Status fetch error:", err);
  }
}

function renderAIMemory(memory, config) {
  if (!memory) return;
  const selectedLanguage = window.MT5I18n?.language() || "tr";
  const profilePending = memory.last_analyzed && (memory.language || "tr") !== selectedLanguage;
  if (profilePending) {
    memory = {...memory, persona: {style: selectedLanguage === "en" ? "Pending" : "Bekleniyor",
      summary: selectedLanguage === "en" ? "Analyze your trading history to generate an English trading profile." : "Türkçe işlem profilini oluşturmak için geçmiş işlemlerinizi analiz edin."},
      learned_rules: [], learned_habits: [], strengths: [], weaknesses: [], revenue_tips: []};
  }

  // 1. Status Badge & Trades Analyzed
  const statusBadge = document.getElementById("ai-status-badge");
  const tradesCount = document.getElementById("ai-learned-trades-count");
  const personaStyle = document.getElementById("ai-persona-style");
  const personaRR = document.getElementById("ai-persona-rr");
  const lastDate = document.getElementById("ai-last-learned-date");
  const personaDesc = document.getElementById("ai-persona-desc");

  if (profilePending) {
    if (statusBadge) {
      statusBadge.innerText = selectedLanguage === "en" ? "English profile pending" : "Türkçe profil bekleniyor";
      statusBadge.className = "text-sm md:text-base font-bold text-amber-400";
    }
  } else if (memory.last_analyzed && (memory.analyzed_trades_count || 0) > 0) {
    if (statusBadge) {
      statusBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400"></span> Öğrenildi & Aktif`;
      statusBadge.className = "text-sm md:text-base font-bold text-emerald-400 flex items-center gap-1.5";
    }
  } else {
    if (statusBadge) {
      statusBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400"></span> Analiz Bekleniyor`;
      statusBadge.className = "text-sm md:text-base font-bold text-gray-300 flex items-center gap-1.5";
    }
  }

  if (tradesCount) {
    tradesCount.innerText = `${memory.analyzed_trades_count || 0} işlem analiz edildi`;
  }

  if (personaStyle) {
    personaStyle.innerText = memory.persona?.style || "Bekleniyor";
  }

  if (personaRR) {
    personaRR.innerText = `R:R Oranı: ${memory.risk_reward_ratio || "Belirlenmedi"}`;
  }

  if (lastDate) {
    lastDate.innerText = `Son Güncelleme: ${memory.last_analyzed || "-"}`;
  }

  if (personaDesc && memory.persona?.summary) {
    personaDesc.innerText = memory.persona?.summary;
  }

  // 2. Rules List
  const rulesList = document.getElementById("ai-rules-list");
  if (rulesList && Array.isArray(memory.learned_rules)) {
    if (memory.learned_rules.length > 0) {
      rulesList.innerHTML = memory.learned_rules.map(r => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      rulesList.innerHTML = `<li class="text-gray-500 list-none">Öğrenilen kural yok</li>`;
    }
  }

  // 3. Habits List
  const habitsList = document.getElementById("ai-habits-list");
  if (habitsList && Array.isArray(memory.learned_habits)) {
    if (memory.learned_habits.length > 0) {
      habitsList.innerHTML = memory.learned_habits.map(h => `<li>${escapeHtml(h)}</li>`).join("");
    } else {
      habitsList.innerHTML = `<li class="text-gray-500 list-none">Öğrenilen alışkanlık yok</li>`;
    }
  }

  // 4. Strengths & Weaknesses
  const strengthsList = document.getElementById("ai-strengths-list");
  if (strengthsList && Array.isArray(memory.strengths)) {
    if (memory.strengths.length > 0) {
      strengthsList.innerHTML = memory.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("");
    } else {
      strengthsList.innerHTML = `<li class="text-gray-500 list-none">-</li>`;
    }
  }

  const weaknessesList = document.getElementById("ai-weaknesses-list");
  if (weaknessesList && Array.isArray(memory.weaknesses)) {
    if (memory.weaknesses.length > 0) {
      weaknessesList.innerHTML = memory.weaknesses.map(w => `<li>${escapeHtml(w)}</li>`).join("");
    } else {
      weaknessesList.innerHTML = `<li class="text-gray-500 list-none">-</li>`;
    }
  }

  // 5. Strategic Income Ideas
  const ideasList = document.getElementById("ai-ideas-list");
  if (ideasList && Array.isArray(memory.revenue_tips)) {
    if (memory.revenue_tips.length > 0) {
      ideasList.innerHTML = memory.revenue_tips.map(idea => `
        <li class="flex items-start gap-2 bg-[#11151f] p-2 rounded-lg border border-gray-800/60">
          <i class="ph-bold ph-check text-emerald-400 mt-0.5 shrink-0"></i>
          <span>${escapeHtml(idea)}</span>
        </li>
      `).join("");
    } else {
      ideasList.innerHTML = `<li class="text-gray-500 text-[11px] py-1">Geçmiş işlemleriniz analiz edildiğinde gelir artırıcı öneriler burada listelenecektir.</li>`;
    }
  }

  // 6. Autopilot UI & Settings Modal Sync
  if (config) {
    const autoStatus = document.getElementById("ai-autopilot-status");
    const autoSub = document.getElementById("ai-autopilot-sub");
    const autoPill = document.getElementById("ai-autopilot-pill");

    let modeText = "KAPALI";
    let modeClass = "text-[9px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400 font-mono";

    if (config.mode === "SEMI_AUTO") {
      modeText = "YARI OTOMATİK";
      modeClass = "text-[9px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-mono";
    } else if (config.mode === "FULL_AUTO") {
      modeText = "TAM OTOMATİK";
      modeClass = "text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 border border-purple-500/30 font-mono animate-pulse";
    }

    if (autoStatus) {
      autoStatus.innerText = modeText;
      autoStatus.className = config.mode === "FULL_AUTO" ? "text-sm md:text-base font-bold text-purple-400" : (config.mode === "SEMI_AUTO" ? "text-sm md:text-base font-bold text-cyan-400" : "text-sm md:text-base font-bold text-gray-400");
    }

    if (autoSub) {
      autoSub.innerText = `Maks: ${config.max_lot} Lot | Min: %${config.min_confidence}`;
    }

    if (autoPill) {
      autoPill.innerText = modeText;
      autoPill.className = modeClass;
    }

    // Modal inputs sync
    const cfgMode = document.getElementById("ai-cfg-mode");
    const cfgLot = document.getElementById("ai-cfg-max-lot");
    const cfgConf = document.getElementById("ai-cfg-min-conf");
    const cfgLoss = document.getElementById("ai-cfg-max-loss");
    const cfgSyms = document.getElementById("ai-cfg-symbols");

    if (cfgMode && !cfgMode.dataset.userEditing) cfgMode.value = config.mode || "DISABLED";
    if (cfgLot && !cfgLot.dataset.userEditing) cfgLot.value = config.max_lot || 0.01;
    if (cfgConf && !cfgConf.dataset.userEditing) cfgConf.value = config.min_confidence || 75;
    if (cfgLoss && !cfgLoss.dataset.userEditing) cfgLoss.value = config.daily_loss_limit || 50.0;
    if (cfgSyms && !cfgSyms.dataset.userEditing) cfgSyms.value = (config.allowed_symbols || []).join(",");
  }

  // 7. Last Advice Sync
  if (memory.latest_recommendation && !currentAIAdvice) {
    renderAIAdvice(memory.latest_recommendation);
  }
}

function renderAIAdvice(advice) {
  if (!advice) return;
  currentAIAdvice = advice;

  const signalEl = document.getElementById("ai-advice-signal");
  const confEl = document.getElementById("ai-advice-confidence");
  const symbolEl = document.getElementById("ai-advice-symbol");
  const entryEl = document.getElementById("ai-advice-entry");
  const slEl = document.getElementById("ai-advice-sl");
  const tpEl = document.getElementById("ai-advice-tp");
  const reasonEl = document.getElementById("ai-advice-reasoning");
  const timeEl = document.getElementById("ai-advice-time");
  const execBtn = document.getElementById("ai-execute-btn");

  const sig = (advice.action || "WAIT").toUpperCase();
  if (signalEl) {
    if (sig === "BUY") {
      signalEl.innerText = "AL (BUY)";
      signalEl.className = "text-xl font-black tracking-wide text-emerald-400";
    } else if (sig === "SELL") {
      signalEl.innerText = "SAT (SELL)";
      signalEl.className = "text-xl font-black tracking-wide text-rose-400";
    } else {
      signalEl.innerText = "BEKLE / YOL HARİTASI YOK";
      signalEl.className = "text-base font-bold tracking-wide text-amber-400";
    }
  }

  if (confEl) {
    const conf = advice.confidence || 0;
    confEl.innerText = `%${conf}`;
    confEl.className = conf >= 75 ? "text-base font-bold font-mono text-emerald-400" : "text-base font-bold font-mono text-amber-400";
  }

  if (symbolEl) symbolEl.innerText = advice.symbol || currentSymbol;
  if (entryEl) entryEl.innerText = advice.entry_price || "-";
  if (slEl) slEl.innerText = advice.sl_price || "-";
  if (tpEl) tpEl.innerText = advice.tp_price || "-";
  if (timeEl) timeEl.innerText = advice.generated_at || new Date().toLocaleTimeString("tr-TR");

  if (reasonEl) {
    reasonEl.innerText = advice.reasoning || "Gerekçe belirtilmedi.";
  }

  if (execBtn) {
    if ((sig === "BUY" || sig === "SELL") && Number(advice.expires_at || 0) * 1000 > Date.now()) {
      execBtn.disabled = false;
      execBtn.innerHTML = `<i class="ph-bold ph-paper-plane-tilt text-base"></i> <span>Bu Tavsiyeyi MT5'te Uygula (${sig} - ${advice.symbol || currentSymbol})</span>`;
    } else {
      execBtn.disabled = true;
      execBtn.innerHTML = `<i class="ph-bold ph-hand text-base"></i> <span>Bekle Modunda (Emir Açılmaz)</span>`;
    }
  }
}

async function triggerAILearn() {
  const btn = document.getElementById("ai-learn-btn");
  if (btn?.disabled) return;
  let originalHtml = "";
  if (btn) {
    originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="ph-bold ph-spinner animate-spin"></i> <span>İşlemler Öğreniliyor...</span>`;
  }

  try {
    showToast("🧠 DeepSeek geçmiş işlemlerinizi analiz ediyor ve tarzınızı öğreniyor...", "info");
    const res = await fetch("/api/ai/learn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days: 90, language: window.MT5I18n?.language?.() || "tr" })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Başarılı! DeepSeek ${data.memory?.analyzed_trades_count || 0} işlemi analiz etti ve tarzınızı hafızasına kaydetti.`, "success");
      renderAIMemory(data.memory, null);
      if (data.cached) showToast("İşlem geçmişi değişmedi; kayıtlı analiz kullanıldı. Yeni token harcanmadı.", "info");
      fetchAIStatus();
      fetchAIStatus();
    } else {
      showToast(`❌ Analiz Hatası: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ Bağlantı Hatası: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
}

async function triggerAIAdvice() {
  const btn = document.getElementById("ai-advice-btn");
  if (btn?.disabled) return;
  let originalHtml = "";
  if (btn) {
    originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="ph-bold ph-spinner animate-spin text-cyan-400"></i> <span>Piyasa Analiz Ediliyor...</span>`;
  }

  try {
    showToast(`⚡ ${currentSymbol} için DeepSeek canlı piyasa tavsiyesi hazırlanıyor...`, "info");
    const res = await fetch("/api/ai/advice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: currentSymbol, timeframe: "M15", language: window.MT5I18n?.language?.() || "tr" })
    });

    const data = await res.json();
    if (res.ok && data.success && data.recommendation) {
      renderAIAdvice(data.recommendation);
      if (data.cached) {
        showToast(data.stale ? "Yeni kapanmış mum yok; önceki analiz gösteriliyor. Yeni token harcanmadı." : "Kayıtlı analiz gösteriliyor; yeni token harcanmadı.", "info");
        return;
      }
      showToast(`💡 ${currentSymbol}: ${data.recommendation.action} Sinyali Üretildi (Güven: %${data.recommendation.confidence})`, "success");
    } else {
      showToast(`❌ Tavsiye Üretilemedi: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ Tavsiye Hatası: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
}

async function executeCurrentAdvice() {
  if (document.getElementById("ai-execute-btn")?.disabled) return;
  if (!currentAIAdvice) {
    showToast("Uygulanacak aktif bir AI tavsiyesi bulunmuyor.", "error");
    return;
  }

  const sig = (currentAIAdvice.action || "").toUpperCase();
  if (sig !== "BUY" && sig !== "SELL") {
    showToast("Bekleme sinyalinde işlem açılamaz.", "error");
    return;
  }

  if (Number(currentAIAdvice.expires_at || 0) * 1000 <= Date.now()) {
    showToast("Tavsiyenin süresi doldu; yeni mum sonrası analiz alın.", "error");
    return;
  }
  const symbol = currentAIAdvice.symbol || currentSymbol;
  const sl = currentAIAdvice.sl_price ? Number(currentAIAdvice.sl_price) : null;
  const tp = currentAIAdvice.tp_price ? Number(currentAIAdvice.tp_price) : null;

  const advice = {...currentAIAdvice};
  const conf = await confirmAction({
    title: "AI emrini onayla",
    message: "Bu emir MT5 hesabınıza gönderilecek.",
    details: [["Sembol", symbol], ["Yön", sig === "BUY" ? "Alış" : "Satış"], ["Lot", "0.01"], ["Stop loss", sl || "Belirtilmedi"], ["Take profit", tp || "Belirtilmedi"]],
    confirmLabel: "Emri gönder"
  });
  if (!conf || document.getElementById("ai-execute-btn")?.disabled) return;
  if (Number(advice.expires_at || 0) * 1000 <= Date.now()) {
    showToast("Tavsiyenin süresi doldu; yeniden analiz alın.", "error");
    return;
  }

  const btn = document.getElementById("ai-execute-btn");
  let originalHtml = "";
  if (btn) {
    originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="ph-bold ph-spinner animate-spin"></i> <span>MT5'e Gönderiliyor...</span>`;
  }

  try {
    const res = await fetch("/api/ai/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({recommendation: {...advice, suggested_lot: 0.01}})
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`${data.partial ? "Emir kısmen gerçekleşti" : data.pending ? "Emir kabul edildi; gerçekleşme bekleniyor" : "Emir gerçekleşti"} · #${data.ticket}`, "success");
      fetchPositions();
      fetchAccount();
    } else {
      showToast(`${data.uncertain ? "Emir sonucu belirsiz; MT5 durumunu kontrol edin" : "Emir tamamlanamadı"}: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ İstek Hatası: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
}

function toggleAIAutopilotModal() {
  const modal = document.getElementById("ai-autopilot-modal");
  if (modal) modal.classList.toggle("hidden");
}

async function saveAIAutopilot() {
  const mode = document.getElementById("ai-cfg-mode")?.value || "DISABLED";
  const maxLot = parseFloat(document.getElementById("ai-cfg-max-lot")?.value || "0.01");
  const minConf = parseInt(document.getElementById("ai-cfg-min-conf")?.value || "75");
  const maxLoss = parseFloat(document.getElementById("ai-cfg-max-loss")?.value || "50.0");
  const symsRaw = document.getElementById("ai-cfg-symbols")?.value || "EURUSD,GBPUSD";
  const symbols = symsRaw.split(",").map(s => s.trim().toUpperCase()).filter(s => s.length > 0);
  let confirmRealFullAuto = false;
  if (mode === "FULL_AUTO" && currentAccountType === "REAL") {
    confirmRealFullAuto = await confirmAction({
      title: "Gerçek hesapta otomatik işlem",
      message: "Otopilot bu gerçek hesapta sizden ayrıca emir onayı almadan işlem açabilir.",
      details: [["Mod", "Tam otomatik"], ["Azami lot", String(maxLot)], ["Semboller", symbols.join(", ")]],
      confirmLabel: "Gerçek hesapta etkinleştir"
    });
    if (!confirmRealFullAuto) return;
  }

  try {
    const res = await fetch("/api/ai/autopilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: mode !== "DISABLED",
        mode: mode === "DISABLED" ? "ADVISORY" : mode,
        max_lot: maxLot,
        min_confidence: minConf,
        daily_loss_limit: maxLoss,
        allowed_symbols: symbols,
        confirm_real_full_auto: confirmRealFullAuto
      })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Otopilot Ayarları Kaydedildi! (${mode})`, "success");
      toggleAIAutopilotModal();
      fetchAIStatus();
    } else {
      showToast(`❌ Kayıt Hatası: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ İstek Hatası: ${err.message}`, "error");
  }
}
