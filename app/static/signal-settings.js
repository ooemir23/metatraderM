// Buy/sell signal settings (scanner) + "how is this signal calculated" explanation.
// Settings are a per-browser preference; the server validates every value again.
(function () {
  const KEY = 'hma.signal.settings.v1';
  const DEFAULTS = {hma_period: 14, ma_type: 'EMA', ema_period: 34, confirm_close: true,
    rsi_period: 14, rsi_filter: false, rsi_buy_max: 70, rsi_sell_min: 30,
    atr_period: 14, sl_atr: 2, tp_atr: 3, only_crosses: false};
  const LIMITS = {hma_period: [2, 200, true], ema_period: [2, 200, true], rsi_period: [2, 100, true], atr_period: [2, 100, true],
    rsi_buy_max: [50, 100, false], rsi_sell_min: [0, 50, false], sl_atr: [0, 20, false], tp_atr: [0, 50, false]};
  const TYPES = ['EMA', 'SMA', 'LWMA', 'HMA'];
  const $ = (id) => document.getElementById(id);

  function sanitize(raw) {
    const out = {...DEFAULTS};
    if (!raw || typeof raw !== 'object') return out;
    for (const [key, [min, max, integer]] of Object.entries(LIMITS)) {
      const n = Number(raw[key]);
      if (Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n))) out[key] = n;
    }
    if (TYPES.includes(raw.ma_type)) out.ma_type = raw.ma_type;
    for (const key of ['confirm_close', 'rsi_filter', 'only_crosses']) if (typeof raw[key] === 'boolean') out[key] = raw[key];
    return out;
  }
  function load() {
    try { return sanitize(JSON.parse(localStorage.getItem(KEY) || 'null')); } catch (_) { return {...DEFAULTS}; }
  }
  let settings = load();

  function query() {
    const p = new URLSearchParams();
    for (const key of ['hma_period', 'ema_period', 'ma_type', 'rsi_period', 'atr_period', 'sl_atr', 'tp_atr',
      'confirm_close', 'rsi_filter', 'rsi_buy_max', 'rsi_sell_min']) p.set(key, String(settings[key]));
    return p.toString();
  }
  const fastLabel = (s = settings) => `HMA(${s.hma_period})`;
  const slowLabel = (s = settings) => `${s.ma_type}(${s.ema_period})`;

  function explanation(s = settings) {
    const candle = s.confirm_close
      ? 'Sinyal yalnız <b>kapanmış mumda</b> oluşur; oluşan mum sinyali değiştiremez (bot ve MT5 EA ile aynı).'
      : '<b>Oluşan (kapanmamış) mum</b> da kontrol edilir; sinyal mum kapanana kadar kaybolabilir.';
    const rsi = s.rsi_filter
      ? `RSI(${s.rsi_period}) filtresi açık: RSI ${s.rsi_buy_max}'in üstündeyken AL, ${s.rsi_sell_min}'un altındayken SAT verilmez.`
      : `RSI(${s.rsi_period}) yalnız bilgi amaçlı gösterilir; sinyali engellemez.`;
    const sl = s.sl_atr > 0 ? `Önerilen SL = fiyat ∓ ${s.sl_atr} × ATR(${s.atr_period})` : 'Önerilen SL kapalı';
    const tp = s.tp_atr > 0 ? `Önerilen TP = fiyat ± ${s.tp_atr} × ATR(${s.atr_period})` : 'önerilen TP kapalı';
    return `<ul class="signal-explain">
      <li><b class="text-emerald-300">▲ YENİ AL</b>: ${fastLabel(s)}, ${slowLabel(s)}'ü aşağıdan yukarı keser.</li>
      <li><b class="text-rose-300">▼ YENİ SAT</b>: ${fastLabel(s)}, ${slowLabel(s)}'ü yukarıdan aşağı keser.</li>
      <li><b>AL / SAT Trend</b>: kesişim yok; ${fastLabel(s)} ${slowLabel(s)}'ün üstünde (AL) veya altında (SAT). "N mum" son kesişimden bu yana geçen mumdur.</li>
      <li>${candle}</li>
      <li>${rsi}</li>
      <li>${sl}; ${tp}. Önerilerdir, emir göndermez.</li>
      ${s.only_crosses ? '<li>Yalnız yeni kesişimler listelenir; trend satırları gizlenir.</li>' : ''}
    </ul>`;
  }

  function renderSummary() {
    const node = $('signal-settings-summary');
    if (node) node.textContent = `${fastLabel()} × ${slowLabel()} · ${settings.confirm_close ? 'kapanmış mum' : 'canlı mum'}`
      + `${settings.rsi_filter ? ` · RSI filtresi ${settings.rsi_sell_min}–${settings.rsi_buy_max}` : ''}`;
    const explain = $('signal-settings-explain');
    if (explain) explain.innerHTML = explanation();
  }

  function fillForm(s) {
    const form = $('signal-settings-form');
    if (!form) return;
    for (const [key, value] of Object.entries(s)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = !!value; else input.value = String(value);
    }
    toggleRsiInputs();
    const preview = $('signal-settings-preview');
    if (preview) preview.innerHTML = explanation(s);
  }
  function readForm() {
    const form = $('signal-settings-form');
    const raw = {};
    for (const key of Object.keys(DEFAULTS)) {
      const input = form?.elements.namedItem(key);
      if (!input) continue;
      raw[key] = input.type === 'checkbox' ? input.checked : input.tagName === 'SELECT' ? input.value : Number(input.value);
    }
    return raw;
  }
  function invalidFields(raw) {
    return Object.entries(LIMITS).filter(([key, [min, max, integer]]) => {
      const n = raw[key];
      return !(Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n)));
    }).map(([key]) => key);
  }
  function toggleRsiInputs() {
    const form = $('signal-settings-form');
    const on = form?.elements.namedItem('rsi_filter')?.checked;
    for (const key of ['rsi_buy_max', 'rsi_sell_min']) { const input = form?.elements.namedItem(key); if (input) input.disabled = !on; }
  }

  function open() {
    fillForm(settings);
    const status = $('signal-settings-status');
    if (status) status.textContent = '';
    const dialog = $('signal-settings-dialog');
    if (dialog && !dialog.open) dialog.showModal?.();
  }
  function close() { $('signal-settings-dialog')?.close?.(); }

  function save(event) {
    event?.preventDefault?.();
    const raw = readForm();
    const bad = invalidFields(raw);
    const status = $('signal-settings-status');
    if (bad.length) {
      if (status) status.textContent = 'Geçersiz değer: ' + bad.join(', ') + '. Alan sınırlarını kontrol edin.';
      return false;
    }
    settings = sanitize(raw);
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (_) { /* session-only */ }
    renderSummary();
    // Chart lines/markers draw HMA × EMA; keep their periods in step (other MA types keep EMA on the chart).
    if (settings.ma_type === 'EMA') window.MT5Chart?.setSignalPeriods?.(settings.hma_period, settings.ema_period);
    else window.MT5Chart?.setSignalPeriods?.(settings.hma_period, null);
    close();
    if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
    if (typeof showToast === 'function') showToast(`✅ Sinyal ayarları kaydedildi: ${fastLabel()} × ${slowLabel()}`, 'success');
    return true;
  }
  function reset() { fillForm({...DEFAULTS}); }

  // Copy HMA / MA settings to the HMA bot (bot must be stopped; server enforces it).
  async function applyToBot() {
    const raw = sanitize(readForm());
    const status = $('signal-settings-status');
    try {
      const res = await fetch('/api/bot/config', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hma_period: raw.hma_period, second_ma_type: raw.ma_type, second_ma_period: raw.ema_period})});
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Bot ayarları kaydedilemedi.');
      if (status) status.textContent = `Bot ayarları güncellendi: ${fastLabel(raw)} × ${slowLabel(raw)}.`;
      if (typeof fetchBotStatus === 'function') fetchBotStatus();
    } catch (err) {
      if (status) status.textContent = '❌ ' + err.message;
    }
  }

  function init() {
    const form = $('signal-settings-form');
    if (form) {
      form.addEventListener('submit', save);
      form.addEventListener('input', () => {
        toggleRsiInputs();
        const preview = $('signal-settings-preview');
        if (preview && !invalidFields(readForm()).length) preview.innerHTML = explanation(sanitize(readForm()));
      });
    }
    renderSummary();
    // Chart AL/SAT markers follow the same HMA × EMA periods as the scanner.
    window.MT5Chart?.setSignalPeriods?.(settings.hma_period, settings.ma_type === 'EMA' ? settings.ema_period : null);
  }

  window.HMASignalSettings = {get: () => ({...settings}), query, open, close, save, reset, applyToBot, fastLabel, slowLabel, explanation, defaults: {...DEFAULTS}};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
