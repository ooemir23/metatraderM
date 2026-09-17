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
      "locale": "tr",
      "toolbar_bg": "#121620",
      "enable_publishing": false,
      "hide_side_toolbar": false,
      "allow_symbol_change": true,
      "container_id": "tradingview-container"
    });
  }
}

// Switch Active Symbol
function switchSymbol(symbol) {
  currentSymbol = symbol;
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

// Polling loop for real-time updates
function startPolling() {
  fetchAccount();
  fetchPositions();
  fetchPrice();
  fetchBotStatus();

  setInterval(fetchAccount, 2000);
  setInterval(fetchPositions, 2000);
  setInterval(fetchPrice, 1500);
  setInterval(fetchBotStatus, 3000);
}

// Fetch Account Info
async function fetchAccount() {
  try {
    const res = await fetch("/api/account");
    if (!res.ok) return;
    const data = await res.json();

    const indicator = document.getElementById("status-indicator");
    const statusText = document.getElementById("status-text");
    const lastSyncText = document.getElementById("last-sync-text");
    const uptimeText = document.getElementById("uptime-text");
    const uptimeBadge = document.getElementById("uptime-badge");

    const nowStr = new Date().toLocaleTimeString("tr-TR");

    if (data.connected) {
      indicator.className = "w-2 h-2 rounded-full bg-emerald-400 animate-pulse";
      statusText.innerText = `MT5 Bağlı (#${data.login})`;
      statusText.className = "text-emerald-400 font-semibold";
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

    document.getElementById("acc-balance").innerText = `$${formatMoney(data.balance)}`;
    document.getElementById("acc-equity").innerText = `$${formatMoney(data.equity)}`;
    document.getElementById("acc-margin-free").innerText = `$${formatMoney(data.margin_free)}`;

    const profitEl = document.getElementById("acc-profit");
    const profitVal = data.profit || 0;
    profitEl.innerText = `${profitVal >= 0 ? "+" : ""}$${formatMoney(profitVal)}`;
    if (profitVal > 0) {
      profitEl.className = "font-bold text-emerald-400 tracking-wide";
    } else if (profitVal < 0) {
      profitEl.className = "font-bold text-rose-400 tracking-wide";
    } else {
      profitEl.className = "font-bold text-gray-300 tracking-wide";
    }
  } catch (err) {
    console.error("fetchAccount error:", err);
  }
}

// Fetch Open Positions
async function fetchPositions() {
  try {
    const res = await fetch("/api/positions");
    if (!res.ok) return;
    const positions = await res.json();

    const tbody = document.getElementById("positions-table-body");
    const countBadge = document.getElementById("pos-count-badge");
    countBadge.innerText = positions.length;

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
          <td class="py-2.5 px-3 text-right font-bold ${profitColor}">${profitSign}$${formatMoney(p.profit)}</td>
          <td class="py-2.5 px-3 text-center">
            <button onclick="closePosition(${p.ticket})" class="px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 text-xs font-semibold transition" title="Kapat">
              Kapat
            </button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;
  } catch (err) {
    console.error("fetchPositions error:", err);
  }
}

// Fetch Price for Active Symbol
async function fetchPrice() {
  try {
    const res = await fetch(`/api/price/${currentSymbol}`);
    if (!res.ok) return;
    const tick = await res.json();

    if (tick && tick.bid > 0) {
      document.getElementById("header-bid").innerText = tick.bid;
      document.getElementById("header-ask").innerText = tick.ask;
      document.getElementById("header-spread").innerText = tick.spread;
      document.getElementById("btn-bid-price").innerText = `@ ${tick.bid}`;
      document.getElementById("btn-ask-price").innerText = `@ ${tick.ask}`;
    }
  } catch (err) {
    console.error("fetchPrice error:", err);
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

// Submit Buy/Sell Order
async function submitOrder(type) {
  const volume = parseFloat(document.getElementById("lot-input").value) || 0.01;
  const sl = parseInt(document.getElementById("sl-input").value) || 0;
  const tp = parseInt(document.getElementById("tp-input").value) || 0;

  try {
    showToast(`${currentSymbol} ${type} (${volume} lot) emri gönderiliyor...`, "info");
    const res = await fetch("/api/order/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: currentSymbol,
        order_type: type,
        volume: volume,
        sl_points: sl,
        tp_points: tp,
        comment: "Volta Web Terminal"
      })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ ${type} Emri Başarıyla Açıldı! Bilet #${data.ticket}`, "success");
      fetchPositions();
      fetchAccount();
    } else {
      showToast(`❌ Emir Hatası: ${data.detail || data.error || "Bilinmeyen hata"}`, "error");
    }
  } catch (err) {
    showToast(`❌ Bağlantı Hatası: ${err.message}`, "error");
  }
}

// Close Single Position (Instant 1-Click)
async function closePosition(ticket) {
  try {
    const res = await fetch("/api/order/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket: ticket })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ #${ticket} numaralı pozisyon kapatıldı.`, "success");
      fetchPositions();
      fetchAccount();
    } else {
      const err = data.detail || data.error || "";
      if (err.includes("10018") || err.includes("Market closed")) {
        showToast(`⚠️ Piyasa Kapalı: Altın (XAUUSD) 23:57 - 01:02 arası günlük tatildedir. Saat 01:02'de açılacaktır.`, "error");
      } else {
        showToast(`❌ Kapatma Hatası: ${err}`, "error");
      }
    }
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
  }
}

// Close Filtered Positions (all, profit, loss) - Instant 1-Click
async function closeFilteredPositions(filterType) {
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
    const res = await fetch(endpoint, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      if (data.total_matched === 0) {
        showToast(`Kapatılacak uygun pozisyon bulunamadı.`, "info");
      } else if (data.closed_count === 0 && data.errors && data.errors.length > 0) {
        const err = data.errors[0];
        if (err.includes("10018") || err.includes("Market closed")) {
          showToast(`⚠️ Piyasa Kapalı: Altın (XAUUSD) 23:57 - 01:02 arası günlük tatildedir. Saat 01:02'de açılacaktır.`, "error");
        } else {
          showToast(`❌ Kapatma Hatası: ${err}`, "error");
        }
      } else {
        showToast(`✅ ${data.closed_count} adet pozisyon kapatıldı.`, "success");
      }
      fetchPositions();
      fetchAccount();
    } else {
      showToast(`❌ Hata: ${data.detail || data.error || "İşlem başarısız"}`, "error");
    }
  } catch (err) {
    showToast(`❌ Hata: ${err.message}`, "error");
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
    telegram_token: document.getElementById("cfg-tg-token").value,
    telegram_chat_id: document.getElementById("cfg-tg-chat").value
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
  return Number(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
}

async function submitLogin() {
  const acc = parseInt(document.getElementById("login-acc").value);
  const pass = document.getElementById("login-pass").value;
  const srv = document.getElementById("login-srv").value;

  if (!acc || !pass || !srv) {
    showToast("Lütfen tüm alanları doldurun.", "error");
    return;
  }

  try {
    showToast("Broker hesabına bağlanılıyor...", "info");
    const res = await fetch("/api/account/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login: acc, password: pass, server: srv })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Giriş Başarılı! Hesap: #${data.login}`, "success");
      toggleLoginModal();
      fetchAccount();
    } else {
      showToast(`❌ Giriş Başarısız: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ Bağlantı Hatası: ${err.message}`, "error");
  }
}
