// ==========================================
// DEDICATED DEEPSEEK AI DASHBOARD SCRIPT
// ==========================================

let currentSymbol = "EURUSD";
let currentAIAdvice = null;
let aiAdviceGeneration = 0;
let aiAdviceRequestSymbol = null;
let aiAdviceRequestGeneration = 0;
let aiAdviceButtonHtml = null;
let aiExecutionBusy = false;
let aiSettingsBusy = false;
const handledAIAdvice = new Set();
function aiAdviceKey(advice) {
  return advice?.id || JSON.stringify([advice?.symbol,advice?.action,advice?.generated_at,advice?.expires_at]);
}
function aiAdviceExecutable(advice) {
  return window.MT5Permissions?.can?.('executeCurrentAdvice') !== false && (!aiAccountVerificationKnown || !!currentAIAccountIdentity) && !!advice && ['BUY','SELL'].includes(String(advice.action).toUpperCase())
    && advice.symbol === currentSymbol && Number.isFinite(Number(advice.expires_at))
    && Number(advice.expires_at)*1000 > Date.now() && !handledAIAdvice.has(aiAdviceKey(advice));
}
function invalidateAIAdvice(symbol) {
  aiAdviceGeneration++;
  currentAIAdvice = null;
  const button = document.getElementById('ai-execute-btn');
  if (button) { button.disabled = true; button.innerText = 'Yeni tavsiye bekleniyor'; }
  for (const id of ['ai-advice-entry','ai-advice-sl','ai-advice-tp','ai-advice-confidence']) {
    const node = document.getElementById(id);
    if (node) node.innerText = '—';
  }
  const label = document.getElementById('ai-advice-symbol');
  if (label) label.innerText = symbol;
}
let currentAIAccountType = null;
let currentAIAccountIdentity = null;
let lastObservedAIAccountIdentity = null;
let aiAccountVerificationKnown = false;
let aiAccountRequestGeneration = 0;
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
  return currentAIAccountIdentity ? {expected_account:[...currentAIAccountIdentity]} : {};
}

document.addEventListener("DOMContentLoaded", () => {
  fetchAccount();
  fetchAIStatus();
  fetchAIPerformance();

  // Periodic polling
  setInterval(fetchAccount, 3000);
  setInterval(fetchAIStatus, 5000);
  setInterval(fetchAIPerformance, 300000);
});

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

async function fetchAccount() {
  const generation = ++aiAccountRequestGeneration;
  try {
    const res = await fetch("/api/account");
    if (!res.ok) throw new Error('Hesap doğrulanamadı.');
    const data = await res.json();
    if (generation !== aiAccountRequestGeneration) return;
    aiAccountVerificationKnown = true;
    currentAIAccountType = data.connected && !data.account_mismatch && ['DEMO','REAL'].includes(data.account_type) ? data.account_type : null;
    const identity = currentAIAccountType && Number.isSafeInteger(data.login) && typeof data.server === 'string'
      ? [data.login,data.server,currentAIAccountType] : null;
    if (identity && lastObservedAIAccountIdentity && JSON.stringify(identity) !== JSON.stringify(lastObservedAIAccountIdentity)) invalidateAIAdvice(currentSymbol);
    currentAIAccountIdentity = identity;
    if (identity) lastObservedAIAccountIdentity = identity;

    const balEl = document.getElementById("ai-acc-balance");
    const profEl = document.getElementById("ai-acc-profit");
    const typeEl = document.getElementById("ai-account-type");
    const currency = data.currency || "USD";

    if (typeEl) {
      const real = data.connected && data.account_type === "REAL" && !data.account_mismatch;
      typeEl.innerText = data.account_mismatch ? "HESAP UYUŞMUYOR" : real ? `GERÇEK #${data.login}` : currentAIAccountType === 'DEMO' ? `DEMO #${data.login}` : "HESAP DOĞRULANAMADI";
      typeEl.className = `px-2 py-1 rounded-lg border font-bold ${real ? "border-rose-500/40 bg-rose-500/10 text-rose-300" : data.account_mismatch ? "border-rose-500/40 bg-rose-500/10 text-rose-300" : data.connected ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-amber-500/30 bg-amber-500/10 text-amber-300"}`;
    }
    if (balEl) balEl.innerText = currentAIAccountType && Number.isFinite(data.balance) ? `${currency} ${formatMoney(data.balance)}` : '—';
    if (profEl) {
      const p = parseFloat(data.profit || 0);
      profEl.innerText = currentAIAccountType && Number.isFinite(data.profit) ? `${p >= 0 ? '+' : ''}${currency} ${formatMoney(p)}` : '—';
      profEl.className = p >= 0 ? "font-bold text-emerald-400 text-xs sm:text-sm" : "font-bold text-rose-400 text-xs sm:text-sm";
    }
  } catch (err) {
    if (generation !== aiAccountRequestGeneration) return;
    aiAccountVerificationKnown = true;
    currentAIAccountType = null;
    currentAIAccountIdentity = null;
    invalidateAIAdvice(currentSymbol);
    const typeEl = document.getElementById('ai-account-type');
    if (typeEl) { typeEl.innerText = 'HESAP DOĞRULANAMADI'; typeEl.className = 'text-amber-300'; }
    for (const id of ['ai-acc-balance','ai-acc-profit']) {
      const node = document.getElementById(id);
      if (node) node.innerText = '—';
    }
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
    console.debug("fetchAIStatus error:", err);
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

  const statusBadge = document.getElementById("ai-status-badge");
  const tradesCount = document.getElementById("ai-learned-trades-count");
  const personaStyle = document.getElementById("ai-persona-style");
  const personaRR = document.getElementById("ai-persona-rr");
  const lastDate = document.getElementById("ai-last-learned-date");
  const personaDesc = document.getElementById("ai-persona-desc");

  if (profilePending) {
    if (statusBadge) {
      statusBadge.innerText = selectedLanguage === "en" ? "English profile pending" : "Türkçe profil bekleniyor";
      statusBadge.className = "text-base font-bold text-amber-400";
    }
  } else if (memory.last_analyzed && (memory.analyzed_trades_count || 0) > 0) {
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
      ideasList.innerHTML = `<li class="text-gray-500 text-xs py-2">İşlem geçmişiniz analiz edildiğinde strateji önerileri burada gösterilecektir.</li>`;
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

    const settingsModal = document.getElementById('ai-autopilot-modal');
    const editing = typeof settingsModal?.classList?.contains === 'function' && !settingsModal.classList.contains('hidden');
    if (!editing && cfgMode) cfgMode.value = config.mode || "DISABLED";
    if (!editing && cfgLot) cfgLot.value = config.max_lot ?? 0.01;
    if (!editing && cfgConf) cfgConf.value = config.min_confidence ?? 75;
    if (!editing && cfgLoss) cfgLoss.value = config.daily_loss_limit ?? 50.0;
    if (!editing && cfgSyms) cfgSyms.value = (config.allowed_symbols || []).join(",");
  }

  if (memory.latest_recommendation && !currentAIAdvice) {
    renderAIAdvice(memory.latest_recommendation);
  }
}

function renderAIAdvice(advice) {
  if (!advice || advice.symbol !== currentSymbol) return;
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
    if (!aiExecutionBusy && aiAdviceExecutable(advice)) {
      execBtn.disabled = false;
      execBtn.innerHTML = `<i class="ph-bold ph-paper-plane-tilt text-lg"></i> <span>Bu Tavsiyeyi MT5'te Uygula (${sig} - ${advice.symbol || currentSymbol})</span>`;
    } else {
      execBtn.disabled = true;
      execBtn.innerHTML = `<i class="ph-bold ph-hand text-lg"></i> <span>Bekle Modunda (Emir Açılmaz)</span>`;
    }
  }
}

function changeAdviceSymbol(sym) {
  invalidateAIAdvice(sym);
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
      body: JSON.stringify({ days: 90, language: window.MT5I18n?.language?.() || "tr" })
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Başarılı! DeepSeek ${data.memory?.analyzed_trades_count || 0} işlemi analiz etti ve hafızasına kaydetti.`, "success");
      renderAIMemory(data.memory, null);
      if (data.cached) showToast("İşlem geçmişi değişmedi; kayıtlı analiz kullanıldı. Yeni token harcanmadı.", "info");
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
  const symbol = currentSymbol;
  if (btn?.disabled && aiAdviceRequestSymbol === symbol) return;
  aiAdviceRequestSymbol = symbol;
  invalidateAIAdvice(symbol);
  const activeGeneration = aiAdviceGeneration;
  aiAdviceRequestGeneration = activeGeneration;
  let originalHtml = "";
  if (btn) {
    if (aiAdviceButtonHtml === null) aiAdviceButtonHtml = btn.innerHTML;
    originalHtml = aiAdviceButtonHtml;
    btn.disabled = true;
    btn.innerHTML = `<i class="ph-bold ph-spinner animate-spin text-cyan-400"></i> <span>Piyasa Analiz Ediliyor...</span>`;
  }

  try {
    showToast(`⚡ ${currentSymbol} için DeepSeek canlı piyasa tavsiyesi hazırlanıyor...`, "info");
    const res = await fetch("/api/ai/advice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, timeframe: "M15", language: window.MT5I18n?.language?.() || "tr" })
    });

    const data = await res.json();
    if (activeGeneration !== aiAdviceGeneration || symbol !== currentSymbol) return;
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
    if (activeGeneration !== aiAdviceGeneration || symbol !== currentSymbol) return;
    showToast(`❌ Tavsiye Hatası: ${err.message}`, "error");
  } finally {
    if (btn && activeGeneration === aiAdviceRequestGeneration) {
      aiAdviceRequestSymbol = null;
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
}

async function executeCurrentAdvice() {
  if (aiExecutionBusy || document.getElementById('ai-execute-btn')?.disabled) return;
  const advice = currentAIAdvice;
  if (!aiAdviceExecutable(advice)) {
    showToast('Tavsiye bu sembol için geçerli değil, süresi doldu veya daha önce gönderildi. Güncel analiz alın.', 'error');
    return;
  }
  const symbol = currentSymbol, generation = aiAdviceGeneration;
  const snapshot = {...advice};
  const expectedAccount = typeof tradingAccountPayload === 'function' ? tradingAccountPayload() : {};
  aiExecutionBusy = true;
  const btn = document.getElementById('ai-execute-btn');
  if (btn) btn.disabled = true;
  try {
    const confirmed = await confirmAction({
      title:'AI emrini onayla', message:'Bu emir MT5 hesabınıza gönderilecek.',
      details:[['Sembol',symbol],['Yön',snapshot.action],['Lot','0.01'],['SL mesafesi (puan)',snapshot.sl_points],['TP mesafesi (puan)',snapshot.tp_points]],
      confirmLabel:'Emri gönder'
    });
    if (!confirmed) return;
    if (currentAIAdvice !== advice || currentSymbol !== symbol || aiAdviceGeneration !== generation || !aiAdviceExecutable(advice)
        || (typeof tradingAccountPayload === 'function' && JSON.stringify(expectedAccount) !== JSON.stringify(tradingAccountPayload()))) {
      showToast('Onay sırasında tavsiye, sembol veya hesap değişti. Emir gönderilmedi.', 'error');
      return;
    }
    if (btn) btn.innerText = "MT5'e gönderiliyor…";
    let data, res;
    try {
      ({response:res,data} = await tradeRequest('/api/ai/execute',
        {recommendation:{...snapshot,suggested_lot:0.01},...expectedAccount}));
    } catch (error) {
      handledAIAdvice.add(aiAdviceKey(snapshot));
      showToast('Emir sonucu belirsiz; MT5 açık pozisyonlarını ve geçmişi kontrol edin.', 'error');
      return;
    }
    if (data.success || data.uncertain || data.pending) handledAIAdvice.add(aiAdviceKey(snapshot));
    if (res.ok && data.success) {
      showToast(`${data.partial ? 'Emir kısmen gerçekleşti' : data.pending ? 'Emir kabul edildi; gerçekleşme bekleniyor' : 'Emir gerçekleşti'} · #${data.ticket}`,
        data.partial || data.pending ? 'warning' : 'success');
    } else {
      showToast(`${data.uncertain ? 'Emir sonucu belirsiz; MT5 durumunu kontrol edin' : 'Emir tamamlanamadı'}: ${data.detail || data.error || ''}`, 'error');
    }
    if (typeof fetchPositions === 'function') fetchPositions(true);
    fetchAccount(true);
  } finally {
    aiExecutionBusy = false;
    if (currentAIAdvice) renderAIAdvice(currentAIAdvice);
    else if (btn) { btn.disabled = true; btn.innerText = 'Yeni tavsiye bekleniyor'; }
    if (typeof refreshOrderRecovery === 'function') refreshOrderRecovery();
  }
}

function toggleAIAutopilotModal() {
  const modal = document.getElementById("ai-autopilot-modal");
  if (modal) modal.classList.toggle("hidden");
}

async function saveAIAutopilot() {
  if (aiSettingsBusy) return;
  const expectedAccount = tradingAccountPayload();
  const isCurrentAccount = () => JSON.stringify(expectedAccount) === JSON.stringify(tradingAccountPayload())
    && !(typeof accountSwitching !== 'undefined' && accountSwitching);
  const mode = document.getElementById("ai-cfg-mode")?.value || "DISABLED";
  const maxLot = parseFloat(document.getElementById("ai-cfg-max-lot")?.value || "0.01");
  const minConf = parseInt(document.getElementById("ai-cfg-min-conf")?.value || "75");
  const maxLoss = parseFloat(document.getElementById("ai-cfg-max-loss")?.value || "50.0");
  const symsRaw = document.getElementById("ai-cfg-symbols")?.value || "EURUSD,GBPUSD";
  const symbols = symsRaw.split(",").map(s => s.trim()).filter(s => s.length > 0);
  aiSettingsBusy = true;
  try {
    let confirmRealFullAuto = false;
    if (mode === "FULL_AUTO" && currentAIAccountType === "REAL") {
      confirmRealFullAuto = await confirmAction({
        title: "Gerçek hesapta otomatik işlem",
        message: "Otopilot bu gerçek hesapta sizden ayrıca emir onayı almadan işlem açabilir.",
        details: [["Mod", "Tam otomatik"], ["Azami lot", String(maxLot)], ["Semboller", symbols.join(", ")]],
        confirmLabel: "Gerçek hesapta etkinleştir"
      });
      if (!confirmRealFullAuto) return;
    }

    if (!isCurrentAccount()) {
      showToast('Onay sırasında hesap değişti; otopilot ayarları kaydedilmedi.', 'error');
      return;
    }
    const {response:res,data} = await tradeRequest('/api/ai/autopilot', {
        enabled: mode !== "DISABLED",
        mode: mode === "DISABLED" ? "ADVISORY" : mode,
        max_lot: maxLot,
        min_confidence: minConf,
        daily_loss_limit: maxLoss,
        allowed_symbols: symbols,
        confirm_real_full_auto: confirmRealFullAuto,
        ...expectedAccount
    });
    if (!isCurrentAccount()) return;
    if (res.ok && data.success) {
      showToast(`✅ Otopilot Ayarları Kaydedildi! (${mode})`, "success");
      toggleAIAutopilotModal();
      fetchAIStatus();
    } else {
      showToast(`❌ Kayıt Hatası: ${data.detail || data.error}`, "error");
    }
  } catch (err) {
    showToast(`❌ İstek Hatası: ${err.message}`, "error");
  } finally { aiSettingsBusy = false; }
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  let bg = "bg-[#181d2a] border-gray-700 text-white";
  if (type === "success") bg = "bg-emerald-950 border-emerald-600 text-emerald-200";
  if (type === "error") bg = "bg-rose-950 border-rose-600 text-rose-200";
  if (type === "warning") bg = "bg-amber-950 border-amber-600 text-amber-200";

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
  return Number(val).toLocaleString(window.MT5I18n?.language?.() === "en" ? "en-US" : "tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.innerText = text;
  return div.innerHTML;
}
