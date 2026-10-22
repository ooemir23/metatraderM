// Buy/sell signal settings (scanner) + "how is this signal calculated" explanation, presets and history test.
// Settings and custom presets are a per-browser preference; the server validates every value again.
(function () {
  const KEY = 'hma.signal.settings.v1';
  const PRESET_KEY = 'hma.signal.presets.v1';
  const SESSIONS = {ASIA: 'Asya', LONDON: 'Londra', NEWYORK: 'New York'};
  const DEFAULTS = {hma_period: 14, ma_type: 'EMA', ema_period: 34, confirm_close: true,
    rsi_period: 14, rsi_filter: false, rsi_buy_max: 70, rsi_sell_min: 30,
    atr_period: 14, sl_atr: 2, tp_atr: 3, only_crosses: false,
    htf_filter: false, htf_minutes: 0, adx_filter: false, adx_period: 14, adx_min: 20,
    max_spread_points: 0, min_rr: 0, cooldown_bars: 0, volume_filter: false, volume_mult: 1,
    sessions: [], show_stats: true, stats_bars: 500};
  const LIMITS = {hma_period: [2, 200, true], ema_period: [2, 200, true], rsi_period: [2, 100, true], atr_period: [2, 100, true],
    rsi_buy_max: [50, 100, false], rsi_sell_min: [0, 50, false], sl_atr: [0, 20, false], tp_atr: [0, 50, false],
    adx_period: [2, 100, true], adx_min: [0, 100, false], max_spread_points: [0, 100000, true], min_rr: [0, 20, false],
    cooldown_bars: [0, 200, true], volume_mult: [0.1, 10, false], stats_bars: [100, 3000, true]};
  const TYPES = ['EMA', 'SMA', 'LWMA', 'HMA'];
  const HTF = [0, 15, 30, 60, 240, 1440];
  const BOOLS = ['confirm_close', 'rsi_filter', 'only_crosses', 'htf_filter', 'adx_filter', 'volume_filter', 'show_stats'];
  const TF_CODES = {15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', 1440: 'D1'};
  // Built-in presets only change the filters; the crossover periods stay as the user set them.
  const PRESETS = {
    cautious: {label: 'Temkinli', values: {confirm_close: true, rsi_filter: true, rsi_buy_max: 70, rsi_sell_min: 30,
      htf_filter: true, htf_minutes: 0, adx_filter: true, adx_min: 25, cooldown_bars: 5, min_rr: 1.5,
      volume_filter: true, volume_mult: 1.2, sessions: ['LONDON', 'NEWYORK'], only_crosses: true}},
    balanced: {label: 'Dengeli', values: {confirm_close: true, rsi_filter: true, rsi_buy_max: 75, rsi_sell_min: 25,
      htf_filter: true, htf_minutes: 0, adx_filter: true, adx_min: 20, cooldown_bars: 3, min_rr: 1.2,
      volume_filter: false, sessions: [], only_crosses: false}},
    aggressive: {label: 'Agresif', values: {confirm_close: true, rsi_filter: false, htf_filter: false, adx_filter: false,
      cooldown_bars: 0, min_rr: 0, volume_filter: false, max_spread_points: 0, sessions: [], only_crosses: false}},
  };
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function sanitize(raw) {
    const out = {...DEFAULTS, sessions: []};
    if (!raw || typeof raw !== 'object') return out;
    for (const [key, [min, max, integer]] of Object.entries(LIMITS)) {
      const n = Number(raw[key]);
      if (raw[key] !== '' && raw[key] != null && Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n))) out[key] = n;
    }
    if (TYPES.includes(raw.ma_type)) out.ma_type = raw.ma_type;
    if (HTF.includes(Number(raw.htf_minutes))) out.htf_minutes = Number(raw.htf_minutes);
    for (const key of BOOLS) if (typeof raw[key] === 'boolean') out[key] = raw[key];
    if (Array.isArray(raw.sessions)) out.sessions = Object.keys(SESSIONS).filter((k) => raw.sessions.includes(k));
    return out;
  }
  function load() {
    try { return sanitize(JSON.parse(localStorage.getItem(KEY) || 'null')); } catch (_) { return sanitize(null); }
  }
  let settings = load();

  function loadCustomPresets() {
    try {
      const raw = JSON.parse(localStorage.getItem(PRESET_KEY) || '{}');
      const out = {};
      if (raw && typeof raw === 'object') for (const [name, value] of Object.entries(raw).slice(0, 30)) {
        if (typeof name === 'string' && name.trim() && name.length <= 32) out[name] = sanitize(value);
      }
      return out;
    } catch (_) { return {}; }
  }
  let customPresets = loadCustomPresets();

  function query(s = settings, {stats = s.show_stats} = {}) {
    const p = new URLSearchParams();
    for (const key of ['hma_period', 'ema_period', 'ma_type', 'rsi_period', 'atr_period', 'sl_atr', 'tp_atr',
      'confirm_close', 'rsi_filter', 'rsi_buy_max', 'rsi_sell_min', 'htf_filter', 'htf_minutes', 'adx_filter',
      'adx_period', 'adx_min', 'max_spread_points', 'cooldown_bars', 'min_rr', 'volume_filter', 'volume_mult']) p.set(key, String(s[key]));
    if (s.sessions.length) p.set('sessions', s.sessions.join(','));
    if (stats) { p.set('stats', 'true'); p.set('stats_bars', String(s.stats_bars)); }
    return p.toString();
  }
  const fastLabel = (s = settings) => `HMA(${s.hma_period})`;
  const slowLabel = (s = settings) => `${s.ma_type}(${s.ema_period})`;
  const htfLabel = (s = settings) => s.htf_minutes ? TF_CODES[s.htf_minutes] : 'otomatik üst zaman dilimi';

  function filterLines(s = settings) {
    const lines = [];
    if (s.htf_filter) lines.push(`Trend filtresi: ${htfLabel(s)} üzerinde ${fastLabel(s)} × ${slowLabel(s)} aynı yönde değilse sinyal verilmez (yalnız kapanmış üst mumlar).`);
    if (s.adx_filter) lines.push(`ADX(${s.adx_period}) ${s.adx_min}'in altındaysa piyasa yatay sayılır, sinyal verilmez (MT5 iADX).`);
    if (s.max_spread_points > 0) lines.push(`Spread ${s.max_spread_points} puanın üstündeyse sinyal verilmez.`);
    if (s.min_rr > 0) lines.push(`Spread dahil risk/ödül 1:${s.min_rr}'in altındaysa sinyal verilmez.`);
    if (s.cooldown_bars > 0) lines.push(`Bir kesişimden sonra ${s.cooldown_bars} mum içinde gelen yeni kesişim yok sayılır.`);
    if (s.volume_filter) lines.push(`Kesişim mumunun tick hacmi son 20 mum ortalamasının ${s.volume_mult} katından azsa sinyal verilmez.`);
    if (s.sessions.length) lines.push(`Yalnız ${s.sessions.map((k) => SESSIONS[k]).join(', ')} seansında (UTC) sinyal verilir.`);
    return lines;
  }

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
      ${filterLines(s).map((line) => `<li>${line}</li>`).join('')}
      <li>${sl}; ${tp}. Önerilerdir, emir göndermez.</li>
      <li>Bir filtreye takılan sinyal <b>⛔ Filtrelendi</b> olarak gösterilir; nedeni satırın altında yazar.</li>
      ${s.only_crosses ? '<li>Yalnız yeni kesişimler listelenir; trend satırları gizlenir.</li>' : ''}
    </ul>`;
  }

  function activeFilterCount(s = settings) {
    return [s.rsi_filter, s.htf_filter, s.adx_filter, s.max_spread_points > 0, s.min_rr > 0, s.cooldown_bars > 0,
      s.volume_filter, s.sessions.length > 0].filter(Boolean).length;
  }

  function renderSummary() {
    const node = $('signal-settings-summary');
    const count = activeFilterCount();
    if (node) node.textContent = `${fastLabel()} × ${slowLabel()} · ${settings.confirm_close ? 'kapanmış mum' : 'canlı mum'}`
      + (count ? ` · ${count} filtre açık` : ' · filtre yok');
    const explain = $('signal-settings-explain');
    if (explain) explain.innerHTML = explanation();
  }

  function renderPresetOptions(selected = '') {
    const select = $('signal-preset-select');
    if (!select) return;
    const builtin = Object.entries(PRESETS).map(([key, p]) => `<option value="builtin:${key}">${p.label}</option>`).join('');
    const custom = Object.keys(customPresets).map((name) => `<option value="custom:${esc(name)}">${esc(name)}</option>`).join('');
    select.innerHTML = `<option value="">— Seçin —</option><optgroup label="Hazır">${builtin}</optgroup>`
      + (custom ? `<optgroup label="Kaydettiklerim">${custom}</optgroup>` : '');
    select.value = selected;
    const del = $('signal-preset-delete');
    if (del) del.disabled = !selected.startsWith('custom:');
  }

  function fillForm(s) {
    const form = $('signal-settings-form');
    if (!form) return;
    for (const [key, value] of Object.entries(s)) {
      if (key === 'sessions') continue;
      const input = form.elements.namedItem(key);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = !!value; else input.value = String(value);
    }
    for (const key of Object.keys(SESSIONS)) {
      const input = form.elements.namedItem('session_' + key);
      if (input) input.checked = s.sessions.includes(key);
    }
    toggleInputs();
    const preview = $('signal-settings-preview');
    if (preview) preview.innerHTML = explanation(s);
  }
  function readForm() {
    const form = $('signal-settings-form');
    const raw = {};
    for (const key of Object.keys(DEFAULTS)) {
      if (key === 'sessions') continue;
      const input = form?.elements.namedItem(key);
      if (!input) continue;
      raw[key] = input.type === 'checkbox' ? input.checked : input.tagName === 'SELECT' ? input.value : (input.value === '' ? NaN : Number(input.value));
    }
    raw.sessions = Object.keys(SESSIONS).filter((k) => form?.elements.namedItem('session_' + k)?.checked);
    if (raw.htf_minutes !== undefined) raw.htf_minutes = Number(raw.htf_minutes);
    return raw;
  }
  function invalidFields(raw) {
    return Object.entries(LIMITS).filter(([key, [min, max, integer]]) => {
      if (!(key in raw)) return false;
      const n = raw[key];
      return !(Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n)));
    }).map(([key]) => key);
  }
  function toggleInputs() {
    const form = $('signal-settings-form');
    const on = (name) => !!form?.elements.namedItem(name)?.checked;
    const set = (keys, enabled) => keys.forEach((key) => { const input = form?.elements.namedItem(key); if (input) input.disabled = !enabled; });
    set(['rsi_buy_max', 'rsi_sell_min'], on('rsi_filter'));
    set(['htf_minutes'], on('htf_filter'));
    set(['adx_period', 'adx_min'], on('adx_filter'));
    set(['volume_mult'], on('volume_filter'));
  }

  function open() {
    fillForm(settings);
    renderPresetOptions();
    const status = $('signal-settings-status');
    if (status) status.textContent = '';
    const result = $('signal-test-result');
    if (result) result.innerHTML = '';
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
    // Chart lines/markers use exactly the scanner rule: HMA × selected slow MA, closed candles if confirmed.
    window.MT5Chart?.setSignalPeriods?.(settings.hma_period, settings.ema_period, settings.ma_type, settings.confirm_close !== false);
    close();
    if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
    if (typeof showToast === 'function') showToast(`✅ Sinyal ayarları kaydedildi: ${fastLabel()} × ${slowLabel()}`, 'success');
    // The HMA bot uses the scanner's crossover (also while running, like an MT5 EA input change).
    if (typeof syncBotToSelection === 'function') syncBotToSelection({}, {announce: true});
    return true;
  }
  // Bot settings dialog edits the same crossover: keep scanner, chart and bot on one HMA × MA.
  function setPeriods({hma_period, ma_type, ema_period} = {}) {
    const next = sanitize({...settings, hma_period: hma_period ?? settings.hma_period,
      ma_type: ma_type ?? settings.ma_type, ema_period: ema_period ?? settings.ema_period});
    if (next.hma_period === settings.hma_period && next.ma_type === settings.ma_type && next.ema_period === settings.ema_period) return false;
    settings = next;
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (_) { /* session-only */ }
    renderSummary();
    window.MT5Chart?.setSignalPeriods?.(settings.hma_period, settings.ema_period, settings.ma_type, settings.confirm_close !== false);
    if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
    return true;
  }
  function reset() { fillForm(sanitize(null)); renderPresetOptions(); }

  function applyPreset(value) {
    const status = $('signal-settings-status');
    if (!value) { renderPresetOptions(''); return; }
    const [kind, ...rest] = String(value).split(':');
    const name = rest.join(':');
    const current = sanitize(readForm());
    let next;
    if (kind === 'builtin' && PRESETS[name]) next = sanitize({...current, ...PRESETS[name].values});
    else if (kind === 'custom' && customPresets[name]) next = sanitize(customPresets[name]);
    if (!next) return;
    fillForm(next);
    renderPresetOptions(value);
    if (status) status.textContent = `"${kind === 'builtin' ? PRESETS[name].label : name}" ayarları forma yüklendi. Uygulamak için "Kaydet ve tara"ya basın.`;
  }
  function savePreset() {
    const status = $('signal-settings-status');
    const name = String($('signal-preset-name')?.value || '').trim().slice(0, 32);
    const raw = readForm();
    const bad = invalidFields(raw);
    if (!name) { if (status) status.textContent = 'Ayar seti için bir ad yazın.'; return false; }
    if (bad.length) { if (status) status.textContent = 'Geçersiz değer: ' + bad.join(', ') + '.'; return false; }
    if (!(name in customPresets) && Object.keys(customPresets).length >= 30) { if (status) status.textContent = 'En fazla 30 ayar seti kaydedilebilir.'; return false; }
    customPresets = {...customPresets, [name]: sanitize(raw)};
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(customPresets)); } catch (_) { /* session-only */ }
    renderPresetOptions('custom:' + name);
    if (status) status.textContent = `"${name}" ayar seti kaydedildi (bu tarayıcıda).`;
    const input = $('signal-preset-name');
    if (input) input.value = '';
    return true;
  }
  function deletePreset() {
    const value = $('signal-preset-select')?.value || '';
    if (!value.startsWith('custom:')) return false;
    const name = value.slice(7);
    const {[name]: _removed, ...rest} = customPresets;
    customPresets = rest;
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(customPresets)); } catch (_) { /* session-only */ }
    renderPresetOptions('');
    const status = $('signal-settings-status');
    if (status) status.textContent = `"${name}" ayar seti silindi.`;
    return true;
  }

  const FILTER_NAMES = {rsi: 'RSI', htf: 'Üst zaman dilimi', adx: 'ADX', spread: 'Spread', session: 'Seans',
    cooldown: 'Bekleme', volume: 'Hacim', rr: 'Risk/ödül'};
  function statsHtml(st, {long = false} = {}) {
    if (!st) return '';
    if (!st.available) return esc(st.reason || 'Geçmiş testi yapılamadı.');
    const closed = st.wins + st.losses;
    const r = st.avg_r == null ? '—' : `${st.avg_r > 0 ? '+' : ''}${st.avg_r}R`;
    const tone = st.avg_r == null ? '' : st.avg_r > 0 ? 'good' : 'bad';
    let text = `Son ${st.bars} mum: ${st.passed} sinyal`;
    text += closed ? ` · <span class="${tone}">%${st.win_rate_pct} kazanma, ortalama ${r}</span> (TP ${st.wins} · SL ${st.losses}${st.open ? ` · açık ${st.open}` : ''})` : ' · sonuçlanan işlem yok';
    if (st.filtered) {
      const parts = Object.entries(st.filter_counts || {}).map(([k, v]) => `${esc(FILTER_NAMES[k] || k)} ${Number(v)}`).join(', ');
      text += ` · ${st.crosses} kesişimden ${st.filtered}'i filtrelendi${long && parts ? ` (${parts})` : ''}`;
    }
    if (st.small_sample) text += ' · <i>az örnek, sonuç güvenilir değil</i>';
    if (long) text += '<br><span class="text-gray-400">Giriş sinyal mumunun kapanışı (+ güncel spread), çıkış ATR SL/TP; aynı mum ikisine de değerse zarar sayılır. Geçmiş sonuç gelecek garantisi değildir.</span>';
    return text;
  }

  async function testHistory() {
    const result = $('signal-test-result');
    const raw = readForm();
    const bad = invalidFields(raw);
    if (bad.length) { if (result) result.textContent = 'Geçersiz değer: ' + bad.join(', ') + '.'; return null; }
    const s = sanitize(raw);
    const symbol = (typeof currentSymbol !== 'undefined' && currentSymbol) || 'EURUSD';
    const tf = Number($('signal-scan-tf')?.value || 15) || 15;
    if (result) result.textContent = `${symbol} için son ${s.stats_bars} mum test ediliyor…`;
    try {
      const res = await fetch(`/api/signals/scan?symbols=${encodeURIComponent(symbol)}&timeframe_minutes=${tf}&${query(s, {stats: true})}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Geçmiş testi yapılamadı.');
      const item = (data.signals || []).find((x) => x.symbol === symbol);
      if (!item) throw new Error(`${symbol} için yeterli MT5 mum verisi yok.`);
      if (result) result.innerHTML = `<b>${esc(symbol)} · ${TF_CODES[tf] || tf + ' dk'}</b> — ${statsHtml(item.stats, {long: true})}`;
      return item.stats;
    } catch (err) {
      if (result) result.textContent = '❌ ' + err.message;
      return null;
    }
  }

  // The bot always uses the saved scanner crossover, so "Bota uygula" saves the form (and the bot follows).
  function applyToBot() { return save(); }

  function init() {
    const form = $('signal-settings-form');
    if (form) {
      form.addEventListener('submit', save);
      form.addEventListener('input', () => {
        toggleInputs();
        const preview = $('signal-settings-preview');
        if (preview && !invalidFields(readForm()).length) preview.innerHTML = explanation(sanitize(readForm()));
      });
    }
    renderSummary();
    // Chart AL/SAT markers follow the same HMA × slow MA rule as the scanner.
    window.MT5Chart?.setSignalPeriods?.(settings.hma_period, settings.ema_period, settings.ma_type, settings.confirm_close !== false);
  }

  window.HMASignalSettings = {get: () => ({...settings, sessions: [...settings.sessions]}), query, open, close, save, reset, applyToBot, setPeriods,
    applyPreset, savePreset, deletePreset, testHistory, statsHtml, filterNames: {...FILTER_NAMES},
    presets: () => ({builtin: Object.keys(PRESETS), custom: Object.keys(customPresets)}),
    fastLabel, slowLabel, explanation, defaults: sanitize(null)};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
