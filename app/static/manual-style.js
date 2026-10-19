// "Manuel Stil" card: signals learned from positions opened by hand in the original MT5 terminal.
// Data: GET /api/manual-style (closed candles only, broker times). This card never places orders.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (v) => (typeof escapeHtml === 'function' ? escapeHtml(v)
    : String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
  const TF = {1:'M1', 5:'M5', 15:'M15', 30:'M30', 60:'H1', 240:'H4'};
  const ALLOWED = Object.keys(TF).map(Number);
  const KEY_EXP = 'hma.manualStyle.experimentalSound.v1';
  const announced = new Set();
  let busy = false, last = null, seq = 0;

  function brokerTime(t) {
    // Broker server epoch: show it as-is (MT5 History shows server time).
    return new Date(Number(t) * 1000).toISOString().slice(0, 16).replace('T', ' ');
  }
  const pct = (x) => (x == null ? '—' : `%${Math.round(x * 100)}`);
  const num = (x, d = 2) => (x == null || !Number.isFinite(Number(x)) ? '—' : Number(x).toFixed(d));
  const money = (x) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${Number(x).toLocaleString('tr-TR', {maximumFractionDigits: 2})}`);

  function experimentalSound() {
    try { return localStorage.getItem(KEY_EXP) === '1'; } catch (_) { return false; }
  }
  function setExperimentalSound(on) {
    try { localStorage.setItem(KEY_EXP, on ? '1' : '0'); } catch (_) { /* session only */ }
  }

  function params() {
    const symbol = (typeof currentSymbol === 'string' && currentSymbol) || 'XAUUSD';
    let tf = Number($('signal-scan-tf')?.value || 15) || 15;
    if (!ALLOWED.includes(tf)) tf = 15;
    return {symbol, tf};
  }

  function sideLabel(side) {
    return side === 'BUY' ? 'AL' : side === 'SELL' ? 'SAT' : 'BEKLE';
  }

  function maybeAlert(data) {
    const sig = data?.signal;
    if (!sig || (sig.side !== 'BUY' && sig.side !== 'SELL')) return;
    const key = `${data.symbol}|${data.timeframe_minutes}|${sig.candle_time}|${sig.side}`;
    if (announced.has(key)) return;
    const first = announced.size === 0 && last === null;
    announced.add(key);
    if (first) return;  // page just opened: do not ring for a signal that was already there
    const soundOn = typeof signalAutoAlertEnabled === 'undefined' || signalAutoAlertEnabled;
    if (!soundOn || (sig.experimental && !experimentalSound())) return;
    if (typeof playSignalBeep === 'function') playSignalBeep(sig.side === 'BUY');
    if (typeof showToast === 'function') {
      showToast(`🖥️ Manuel stil: ${data.symbol} ${TF[data.timeframe_minutes] || ''} ${sideLabel(sig.side)}`
        + (sig.experimental ? ' (deneysel)' : ''), 'info');
    }
  }

  function render(data) {
    const body = $('manual-style-body');
    const status = $('manual-style-status');
    if (!body) return;
    const tfName = TF[data.timeframe_minutes] || `${data.timeframe_minutes} dk`;
    if (status) {
      status.textContent = `${data.symbol} · ${tfName} · son ${data.days} gün · ${data.positions} pozisyon → `
        + `${data.decisions} karar (${data.wins} kazanan) · net ${money(data.net)}`;
    }
    if (data.status !== 'OK') {
      const others = Object.entries(data.manual_symbols || {}).filter(([s]) => s !== data.symbol)
        .map(([s, n]) => `${esc(s)} (${n})`).join(', ');
      body.innerHTML = `<p class="ms-note">${esc(data.message)}</p>`
        + (others ? `<p class="ms-note">Manuel işlem bulunan diğer semboller: ${others}</p>` : '');
      return;
    }
    const v = data.verdict || {};
    const sig = data.signal;
    const ht = data.history_test || {};
    const verdict = v.reliable
      ? `<span class="ms-badge is-ok">Geçmiş testi geçti</span>`
      : `<span class="ms-badge is-warn">Deneysel</span>`;
    let live = '<p class="ms-note">Canlı sinyal için yeterli veri yok.</p>';
    if (sig) {
      const wins = sig.neighbours.filter(n => n.win).length;
      live = `<div class="ms-live" data-side="${esc(sig.side)}">
          <span class="ms-side">${sideLabel(sig.side)}</span>
          <div class="ms-live-text">
            <div>${sig.side === 'HOLD'
              ? `Son kapanmış mum (${esc(brokerTime(sig.candle_time))}) senin girişlerine ${sig.similar ? 'benziyor ama benzer girişlerin çoğu kazanmadı' : 'benzemiyor'}.`
              : `Son kapanmış mum (${esc(brokerTime(sig.candle_time))}) senin ${sideLabel(sig.side)} girişlerine benziyor.`}</div>
            <div class="ms-sub">En benzer ${sig.neighbours.length} geçmiş girişin ${wins}'i kazandı · skor ${num(sig.score)} · fiyat ${esc(sig.price)}</div>
          </div>
        </div>`;
    }
    const rows = (data.profile?.features || []).map(f => `<tr><td>${esc(f.label)}</td>`
      + `<td>${f.kind === 'share' ? pct(f.wins) + ' lehte' : num(f.wins)}</td>`
      + `<td>${f.kind === 'share' ? pct(f.losses) + ' lehte' : num(f.losses)}</td></tr>`).join('');
    const s = ht.signals || {}, b = ht.baseline || {};
    const recent = (ht.recent || []).slice(-6).reverse().map(x => `<li><span>${esc(brokerTime(x.candle_time))}</span>`
      + `<b class="ms-${x.side === 'BUY' ? 'buy' : 'sell'}">${sideLabel(x.side)}</b>`
      + `<span>${x.after ? `${num(x.after.move)} ATR` : 'sonuç bekleniyor'}</span></li>`).join('');
    body.innerHTML = `
      <div class="ms-verdict">${verdict}<span>${esc(v.reason || '')}</span></div>
      ${live}
      <details class="ms-details">
        <summary>Ne öğrendi, nasıl test edildi?</summary>
        <p class="ms-note">Her girişin öncesindeki son kapanmış ${esc(tfName)} mumu MT5 göstergeleriyle okunur
          (HMA14, EMA34, RSI14, ATR14, ADX14, üst zaman dilimi trendi, seans). Değerler işlem yönüne göre çevrilir:
          "lehte" o yön için uygun demek.</p>
        <table class="ms-table"><thead><tr><th></th><th>Kazanan girişler</th><th>Kaybeden girişler</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="ms-note">Girişlerin ilk ${ht.horizon_bars} mumu: isabet ${pct(data.entry_quality?.hit_rate)},
          ortalama ${num(data.entry_quality?.avg_move)} ATR.</p>
        <p class="ms-note">Geçmiş test (her mum yalnız o ana kadar kapanmış işlemlerden öğrenir, ${ht.horizon_bars} mum sonrası):
          ${s.count || 0} bağımsız sinyal (toplam ${s.all_signals ?? s.count ?? 0}), isabet ${pct(s.hit_rate)}, ortalama ${num(s.avg_move)} ATR ·
          her mumda girmek: isabet ${pct(b.hit_rate)}, ortalama ${num(b.avg_move)} ATR.</p>
        ${recent ? `<ul class="ms-recent">${recent}</ul>` : ''}
      </details>`;
  }

  async function refresh(manual = false) {
    if (busy && !manual) return;
    if (!manual && document.visibilityState === 'hidden') return;
    const {symbol, tf} = params();
    const my = ++seq;
    busy = true;
    const status = $('manual-style-status');
    if (manual && status) status.textContent = `${symbol} manuel işlemleri inceleniyor…`;
    try {
      const res = await fetch(`/api/manual-style?symbol=${encodeURIComponent(symbol)}&timeframe_minutes=${tf}`
        + (manual ? '&refresh=true' : ''));
      const data = await res.json().catch(() => ({}));
      if (my !== seq) return;
      if (!res.ok) throw new Error(data.detail || 'Manuel stil analizi alınamadı.');
      maybeAlert(data);
      render(data);
      last = data;
    } catch (err) {
      if (my === seq && status) status.textContent = err.message || 'Manuel stil analizi alınamadı.';
    } finally {
      if (my === seq) busy = false;
    }
  }

  function init() {
    const exp = $('manual-style-exp-sound');
    if (exp) {
      exp.checked = experimentalSound();
      exp.addEventListener('change', () => setExperimentalSound(exp.checked));
    }
    $('manual-style-refresh')?.addEventListener('click', () => refresh(true));
    $('signal-scan-tf')?.addEventListener('change', () => refresh(true));
    refresh(false);
    let lastAt = Date.now();
    setInterval(() => {
      const {symbol, tf} = params();
      if (last && (last.symbol !== symbol || last.timeframe_minutes !== tf)) { lastAt = Date.now(); refresh(true); }
      else if (Date.now() - lastAt >= 60000) { lastAt = Date.now(); refresh(false); }
    }, 5000);
  }

  window.HMAManualStyle = {refresh, render, _maybeAlert: maybeAlert, _announced: announced};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
