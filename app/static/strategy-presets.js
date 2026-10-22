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

  function isCurrent(p) {
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
    if (typeof syncBotToSelection === 'function') ok = await syncBotToSelection({}, {announce: false});
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
    load(false);
  }
  function close() { $('strategy-presets-dialog')?.close?.(); }

  window.StrategyPresets = {open, close, load, apply, render};
})();
