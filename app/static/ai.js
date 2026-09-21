// ==========================================
// DEDICATED DEEPSEEK AI DASHBOARD SCRIPT
// ==========================================

let currentSymbol = "EURUSD";
let currentAIAdvice = null;

document.addEventListener("DOMContentLoaded", () => {
  fetchAccount();
  fetchAIStatus();

  // Periodic polling
  setInterval(fetchAccount, 3000);
  setInterval(fetchAIStatus, 5000);
});

async function fetchAccount() {
  try {
    const res = await fetch("/api/account");
    if (!res.ok) return;
    const data = await res.json();

    const balEl = document.getElementById("ai-acc-balance");
    const profEl = document.getElementById("ai-acc-profit");

    if (balEl) balEl.innerText = `$${formatMoney(data.balance)}`;
    if (profEl) {
      const p = parseFloat(data.profit || 0);
      profEl.innerText = `${p >= 0 ? '+' : ''}$${formatMoney(p)}`;
      profEl.className = p >= 0 ? "font-bold text-emerald-400 text-xs sm:text-sm" : "font-bold text-rose-400 text-xs sm:text-sm";
    }
  } catch (err) {
    console.debug("fetchAccount error:", err);
  }
}

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
  } catch (err) {
    console.debug("fetchAIStatus error:", err);
  }
}

function renderAIMemory(memory, config) {
  if (!memory) return;

  const statusBadge = document.getElementById("ai-status-badge");
  const tradesCount = document.getElementById("ai-learned-trades-count");
  const personaStyle = document.getElementById("ai-persona-style");
  const personaRR = document.getElementById("ai-persona-rr");
  const lastDate = document.getElementById("ai-last-learned-date");
  const personaDesc = document.getElementById("ai-persona-desc");

  if (memory.last_analyzed && (memory.analyzed_trades_count || 0) > 0) {
    if (statusBadge) {
      statusBadge.innerHTML = `<span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span> Öğrenildi & Aktif`;
      statusBadge.className = "text-base font-bold text-emerald-400 flex items-center gap-2";
    }
  } else {
    if (statusBadge) {
      statusBadge.innerHTML = `<span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span> Analiz Bekleniyor`;
      statusBadge.className = "text-base font-bold text-gray-300 flex items-center gap-2";
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

  // Rules
  const rulesList = document.getElementById("ai-rules-list");
  if (rulesList && Array.isArray(memory.learned_rules)) {
    if (memory.learned_rules.length > 0) {
      rulesList.innerHTML = memory.learned_rules.map(r => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      rulesList.innerHTML = `<li class="text-gray-500 list-none text-xs py-1">Öğrenilen kural yok</li>`;
    }
  }

  // Habits
  const habitsList = document.getElementById("ai-habits-list");
  if (habitsList && Array.isArray(memory.learned_habits)) {
    if (memory.learned_habits.length > 0) {
      habitsList.innerHTML = memory.learned_habits.map(h => `<li>${escapeHtml(h)}</li>`).join("");
    } else {
      habitsList.innerHTML = `<li class="text-gray-500 list-none text-xs py-1">Öğrenilen alışkanlık yok</li>`;
    }
  }

  // Strengths
  const strengthsList = document.getElementById("ai-strengths-list");
  if (strengthsList && Array.isArray(memory.strengths)) {
    if (memory.strengths.length > 0) {
      strengthsList.innerHTML = memory.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("");
    } else {
      strengthsList.innerHTML = `<li class="text-gray-500 list-none text-xs">-</li>`;
    }
  }

  // Weaknesses
  const weaknessesList = document.getElementById("ai-weaknesses-list");
  if (weaknessesList && Array.isArray(memory.weaknesses)) {
    if (memory.weaknesses.length > 0) {
      weaknessesList.innerHTML = memory.weaknesses.map(w => `<li>${escapeHtml(w)}</li>`).join("");
    } else {
      weaknessesList.innerHTML = `<li class="text-gray-500 list-none text-xs">-</li>`;
    }
  }

  // Income Ideas
  const ideasList = document.getElementById("ai-ideas-list");
  if (ideasList && Array.isArray(memory.revenue_tips)) {
    if (memory.revenue_tips.length > 0) {
      ideasList.innerHTML = memory.revenue_tips.map(idea => `
        <li class="flex items-start gap-2.5 bg-[#11151f] p-3 rounded-xl border border-gray-800/70">
          <i class="ph-bold ph-check text-emerald-400 mt-0.5 shrink-0 text-base"></i>
          <span>${escapeHtml(idea)}</span>
        </li>
      `).join("");
    } else {
      ideasList.innerHTML = `<li class="text-gray-500 text-xs py-2">Geçmiş işlemleriniz analiz edildiğinde gelir artırıcı özel öneriler burada listelenecektir.</li>`;
    }
  }

  // Autopilot Status
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
      autoStatus.className = config.mode === "FULL_AUTO" ? "text-base font-bold text-purple-400" : (config.mode === "SEMI_AUTO" ? "text-base font-bold text-cyan-400" : "text-base font-bold text-gray-400");
    }

    if (autoSub) {
      autoSub.innerText = `Maks: ${config.max_lot} Lot | Min: %${config.min_confidence}`;
    }

    if (autoPill) {
      autoPill.innerText = modeText;
      autoPill.className = modeClass;
    }

    // Inputs
    const cfgMode = document.getElementById("ai-cfg-mode");
    const cfgLot = document.getElementById("ai-cfg-max-lot");
    const cfgConf = document.getElementById("ai-cfg-min-conf");
    const cfgLoss = document.getElementById("ai-cfg-max-loss");
    const cfgSyms = document.getElementById("ai-cfg-symbols");

    if (cfgMode) cfgMode.value = config.mode || "DISABLED";
    if (cfgLot) cfgLot.value = config.max_lot || 0.01;
    if (cfgConf) cfgConf.value = config.min_confidence || 75;
    if (cfgLoss) cfgLoss.value = config.daily_loss_limit || 50.0;
    if (cfgSyms) cfgSyms.value = (config.allowed_symbols || []).join(",");
  }

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
      signalEl.className = "text-xl sm:text-2xl font-black tracking-wide text-emerald-400 mt-0.5";
    } else if (sig === "SELL") {
      signalEl.innerText = "SAT (SELL)";
      signalEl.className = "text-xl sm:text-2xl font-black tracking-wide text-rose-400 mt-0.5";
    } else {
      signalEl.innerText = "BEKLE / YOL HARİTASI YOK";
      signalEl.className = "text-base font-bold tracking-wide text-amber-400 mt-0.5";
    }
  }

  if (confEl) {
    const conf = advice.confidence || 0;
    confEl.innerText = `%${conf}`;
    confEl.className = conf >= 75 ? "text-lg sm:text-xl font-bold font-mono text-emerald-400 mt-0.5" : "text-lg sm:text-xl font-bold font-mono text-amber-400 mt-0.5";
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
      execBtn.innerHTML = `<i class="ph-bold ph-paper-plane-tilt text-lg"></i> <span>Bu Tavsiyeyi MT5'te Uygula (${sig} - ${advice.symbol || currentSymbol})</span>`;
    } else {
      execBtn.disabled = true;
      execBtn.innerHTML = `<i class="ph-bold ph-hand text-lg"></i> <span>Bekle Modunda (Emir Açılmaz)</span>`;
    }
  }
}

function changeAdviceSymbol(sym) {
  currentSymbol = sym;
  document.querySelectorAll(".ai-sym-btn").forEach(btn => {
    if (btn.innerText === sym) {
      btn.className = "ai-sym-btn px-2.5 py-1 rounded-lg font-bold bg-purple-500/20 text-purple-400 border border-purple-500/30";
    } else {
      btn.className = "ai-sym-btn px-2.5 py-1 rounded-lg font-bold text-gray-400 hover:text-white";
    }
  });

  const symLabel = document.getElementById("ai-advice-symbol");
  if (symLabel) symLabel.innerText = sym;

  triggerAIAdvice();
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
      body: JSON.stringify({ days: 90 })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Başarılı! DeepSeek ${data.memory?.analyzed_trades_count || 0} işlemi analiz etti ve hafızasına kaydetti.`, "success");
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
      body: JSON.stringify({ symbol: currentSymbol, timeframe: "M15" })
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
        allowed_symbols: symbols
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

function formatMoney(val) {
  if (val === undefined || val === null || isNaN(val)) return "0.00";
  return Number(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.innerText = text;
  return div.innerHTML;
}
