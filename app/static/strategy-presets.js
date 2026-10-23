// "Stratejiler" (globe) button: widely used MA crossovers tested on this broker's candles with the bot's rules.
// Choosing one sets the scanner, the chart and the bot (the bot follows the scanner's crossover).
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const TF = {1: 'M1', 5: 'M5', 15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', 1440: 'D1'};
  let last = null;
  let controller = null;

  const context = () => {
    const symbol = (typeof currentSymbol === 'string' && currentSymbol) || 'XAUUSD';
    const tf = Number(window.MT5Chart?.getTimeframe?.() || document.getElementById('signal-scan-tf')?.value || 15) || 15;
    return {symbol, tf};
  };
  const pts = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toLocaleString('tr-TR', {maximumFractionDigits: 1})} p`);
  const date = (t) => new Date(Number(t) * 1000).toISOString().slice(0, 16).replace('T', ' ');

  async function setEntryMode(mode) {
    const bot = (typeof latestBotSettings !== 'undefined' && latestBotSettings) || {};
    if (bot.entry_mode === mode) return true;
    const res = await fetch('/api/bot/follow', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entry_mode: mode})});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Bot giriş kuralı değiştirilemedi.');
    if (data && data.symbol && typeof latestBotSettings !== 'undefined') latestBotSettings = data;
    return true;
  }
  const botMode = () => ((typeof latestBotSettings !== 'undefined' && latestBotSettings) || {}).entry_mode || 'CROSSOVER';

  // Top card: the bot enters on the signal learned from the hand-opened MT5 trades (Manuel Stil Sinyali).
  let manual = null;
  async function loadManual(force = false) {
    const node = $('sp-manual');
    if (!node) return;
    const {symbol, tf} = context();
    if (![1, 5, 15, 30, 60, 240].includes(tf)) {
      node.innerHTML = manualCard(null, `Manuel işlem stili ${esc(TF[tf] || tf)} zaman diliminde çalışmaz; M1–H4 seçin.`);
      return;
    }
    if (!force && manual && manual.symbol === symbol && manual.timeframe_minutes === tf) { node.innerHTML = manualCard(manual); return; }
    node.innerHTML = manualCard(null, 'Manuel işlemlerin inceleniyor…');
    try {
      const res = await fetch(`/api/manual-style?${new URLSearchParams({symbol, timeframe_minutes: String(tf)})}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Manuel işlemler okunamadı.');
      manual = data;
      node.innerHTML = manualCard(data);
    } catch (err) {
      node.innerHTML = manualCard(null, '❌ ' + esc(err.message));
    }
  }
  function manualCard(data, message = '') {
    const bot = (typeof latestBotSettings !== 'undefined' && latestBotSettings) || {};
    const current = botMode() === 'MANUAL_STYLE';
    const copy = bot.copy_manual_trades && bot.manual_copy?.available ? bot.manual_copy : null;
    let body = message ? `<div class="sp-note">${message}</div>` : '';
    let usable = false;
    if (data) {
      const ht = data.history_test || {};
      const sig = ht.signals || {}, base = ht.baseline || {};
      const pct = (v) => (v == null ? '—' : `%${Math.round(v * 100)}`);
      usable = data.status === 'OK';
      body = `<div class="sp-note">${esc(data.symbol)} ${esc(TF[data.timeframe_minutes] || '')}: ${data.decisions} manuel karar (${data.wins} kârlı) öğrenildi.
        Bot, senin tarzına benzeyen kurulum çıktığında girer; lot ve SL/TP ${copy ? `kârlı manuel işlemlerinden (${copy.applied_lot ?? copy.lot} lot)` : 'bot ayarından'} gelir.</div>
        <div class="sp-stats">
          ${usable ? `<span>Geçmiş test: ${sig.count ?? 0} sinyal · isabet ${pct(sig.hit_rate)} (her mumda girmek ${pct(base.hit_rate)})</span>` : ''}
          <span class="${data.verdict?.reliable ? 'good' : 'sp-warn'}">${esc(data.verdict?.reason || data.message || '')}</span>
          ${data.signal ? `<span>Şu an: ${data.signal.side === 'HOLD' ? 'bekle' : data.signal.side === 'BUY' ? 'AL' : 'SAT'}</span>` : ''}
        </div>`;
    }
    return `<div class="sp-row sp-manual" data-tone="${current ? 'good' : 'idle'}">
      <div class="sp-rank">🧠</div>
      <div class="sp-main">
        <div class="sp-name">Manuel işlemlerim (orijinal MT5)${current ? ' <span class="sp-current">şu an kullanılıyor</span>' : ''}
          ${data && !data.verdict?.reliable ? ' <span class="sp-warn">deneysel</span>' : ''}</div>
        ${body}
      </div>
      <button type="button" class="signal-btn-primary" ${current || !data || !usable ? 'disabled' : ''} onclick="StrategyPresets.applyManual()">${current ? 'Seçili' : 'Bunu kullan'}</button>
    </div>`;
  }
  async function applyManual() {
    try {
      await setEntryMode('MANUAL_STYLE');
      if (typeof showToast === 'function') showToast('🧠 Bot artık manuel işlemlerinden öğrenilen sinyalle giriyor; lot ve SL/TP kârlı manuel işlemlerinden.' +
        (latestBotSettings?.is_running ? ' Bot çalışırken geçti; açık pozisyonlar kalır.' : ''), 'success');
    } catch (err) {
      if (typeof showToast === 'function') showToast('❌ ' + err.message, 'error');
    }
    if (typeof fetchBotStatus === 'function') fetchBotStatus();
    loadManual(false);
    if (last) render(last);
  }

  function isCurrent(p) {
    if (botMode() !== 'CROSSOVER') return false;
    const s = window.HMASignalSettings?.get?.() || {};
    return (s.fast_type || 'HMA') === p.fast_type && Number(s.hma_period) === p.fast && s.ma_type === p.slow_type && Number(s.ema_period) === p.slow;
  }

  function render(data) {
    const list = $('sp-list');
    const status = $('sp-status');
    if (!list) return;
    const exits = data.exit_source === 'manual_copy' ? 'manuel kopya SL/TP' : 'botun SL/TP ayarı';
    if (status) status.innerHTML = `${esc(data.symbol)} ${esc(TF[data.timeframe_minutes] || data.timeframe_minutes)} · ${data.candles} kapanmış mum (${esc(date(data.from_time))} – ${esc(date(data.to_time))}, sunucu saati) · `
      + `çıkış: ${esc(exits)} (SL ${data.sl_points || 'yok'} p, TP ${data.tp_points || 'yok'} p) · spread ${data.spread_points} p · komisyon ${data.commission_points} p. `
      + `<i>${esc(data.disclaimer)}</i>`;
    list.innerHTML = data.presets.map((p, i) => {
      if (!p.available) return `<div class="sp-row" data-tone="idle"><div class="sp-name">${esc(p.name)}</div><div class="sp-note">${esc(p.reason)}</div></div>`;
      const tone = p.net_points > 0 ? 'good' : 'bad';
      const oos = p.out_of_sample || {};
      const current = isCurrent(p);
      return `<div class="sp-row" data-tone="${tone}">
        <div class="sp-rank">${i + 1}</div>
        <div class="sp-main">
          <div class="sp-name">${esc(p.name)}${current ? ' <span class="sp-current">şu an kullanılıyor</span>' : ''}</div>
          <div class="sp-note">${esc(p.note)}</div>
          <div class="sp-stats">
            <span class="${tone}">Net ${pts(p.net_points)}</span>
            <span>${p.trades} işlem · %${p.win_rate_pct ?? '—'} kazanan · ortalama ${pts(p.avg_points)}</span>
            <span>En büyük düşüş ${pts(-p.max_drawdown_points)}</span>
            <span title="Testin son %30'u: ayarın daha önce görmediği dönem">Son dönem ${pts(oos.net_points)} (${oos.count || 0} işlem)</span>
            ${p.small_sample ? '<span class="sp-warn">az işlem, sonuç güvenilir değil</span>' : ''}
          </div>
        </div>
        <button type="button" class="signal-btn-primary" ${current ? 'disabled' : ''} onclick="StrategyPresets.apply('${esc(p.id)}')">${current ? 'Seçili' : 'Bunu kullan'}</button>
      </div>`;
    }).join('');
  }

  async function load(force = false) {
    const {symbol, tf} = context();
    const candles = Number($('sp-candles')?.value || 2000);
    const commission = Math.max(0, Number($('sp-commission')?.value || 0)) || 0;
    if ($('sp-context')) $('sp-context').textContent = `${symbol} · ${TF[tf] || tf + ' dk'}`;
    if (!force && last && last.symbol === symbol && last.timeframe_minutes === tf && last.candles_req === candles && last.commission_points === commission) { render(last); return last; }
    controller?.abort?.();
    controller = typeof AbortController === 'function' ? new AbortController() : null;
    if ($('sp-status')) $('sp-status').textContent = `${symbol} için ${candles} mumda ${8} strateji test ediliyor…`;
    if ($('sp-list')) $('sp-list').innerHTML = '';
    try {
      const q = new URLSearchParams({symbol, timeframe_minutes: String(tf), candles: String(candles), commission_points: String(commission)});
      if (force) loadManual(true);
      const res = await fetch(`/api/strategy-presets?${q}`, controller ? {signal: controller.signal} : undefined);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Stratejiler test edilemedi.');
      last = {...data, candles_req: candles};
      render(last);
      return last;
    } catch (err) {
      if (err?.name === 'AbortError') return null;
      if ($('sp-status')) $('sp-status').textContent = '❌ ' + err.message;
      return null;
    }
  }

  async function apply(id) {
    const p = last?.presets?.find((x) => x.id === id);
    if (!p) return false;
    const changed = window.HMASignalSettings?.setPeriods?.({fast_type: p.fast_type, hma_period: p.fast, ma_type: p.slow_type, ema_period: p.slow});
    let ok = true;
    try { await setEntryMode('CROSSOVER'); } catch (err) { ok = false; if (typeof showToast === 'function') showToast('❌ ' + err.message, 'error'); }
    if (ok && typeof syncBotToSelection === 'function') ok = await syncBotToSelection({}, {announce: false});
    if (typeof showToast === 'function') {
      showToast(ok ? `🌐 ${p.name} seçildi: tarayıcı, grafik ve bot bu kesişimi kullanıyor.${latestBotSettings?.is_running ? ' Bot çalışırken geçti; açık pozisyonlar kalır.' : ''}`
        : `⚠️ ${p.name} tarayıcıya uygulandı ama bot geçemedi; sebebi bot kartında.`, ok ? 'success' : 'warning');
    }
    if (typeof fetchBotStatus === 'function') fetchBotStatus();
    render(last);
    return changed !== undefined ? ok : ok;
  }

  function open() {
    const dialog = $('strategy-presets-dialog');
    if (!dialog) return;
    if (!dialog.open) dialog.showModal?.();
    loadManual(false);
    load(false);
  }
  function close() { $('strategy-presets-dialog')?.close?.(); }

  window.StrategyPresets = {open, close, load, apply, render, applyManual, loadManual};
})();
