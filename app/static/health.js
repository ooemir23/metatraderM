// System health: (1) captures the page's own technical errors (JS errors, failed /api calls) into the notification bell,
// (2) asks /api/health every minute and shows new warnings/errors there, (3) "Sistem sağlığı" panel in Araçlar.
// Read-only: nothing here changes a setting or sends an order.
(function () {
  const en = () => window.MT5I18n?.language?.() === 'en';
  const seen = new Map();            // message -> last time reported (rate limit)
  const report = (message, type) => {
    const now = Date.now();
    if (seen.has(message) && now - seen.get(message) < 60000) return;
    seen.set(message, now);
    window.NotifyCenter?.add(message, type);
  };

  // ---- technical errors of this page
  window.addEventListener('error', (event) => {
    if (!event.message || /ResizeObserver/.test(event.message)) return;
    const where = event.filename ? ` (${String(event.filename).split('/').pop()}:${event.lineno})` : '';
    report(`Teknik hata: ${event.message}${where}`, 'error');
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason?.message || String(event.reason || '');
    if (reason && !/AbortError|aborted/i.test(reason)) report(`Teknik hata (işlenmeyen): ${reason.slice(0, 200)}`, 'error');
  });
  if (typeof window.fetch === 'function' && !window.fetch.__health) {
    const original = window.fetch;
    const wrapped = function (input, init) {
      const url = typeof input === 'string' ? input : input?.url || '';
      return original.apply(this, arguments).then((response) => {
        try {
          const path = new URL(url, location.href).pathname;
          // 503 = broker/MT5 temporarily unavailable (reported once by the health check), not a bug of the page.
          if (response.status >= 500 && response.status !== 503 && path.startsWith('/api/') && path !== '/api/health') report(`Sunucu hatası ${response.status}: ${path}`, 'error');
        } catch (_) { /* ignore */ }
        return response;
      }, (error) => {
        try {
          const path = new URL(url, location.href).pathname;
          if (path.startsWith('/api/') && path !== '/api/health' && error?.name !== 'AbortError') report(`Sunucuya ulaşılamadı: ${path}`, 'warning');
        } catch (_) { /* ignore */ }
        throw error;
      });
    };
    wrapped.__health = true;
    window.fetch = wrapped;
  }

  // ---- /api/health
  const TARGETS = {'bot-settings': 'Bot Ayarları', risk: 'İşlem Riski', connection: 'Hesap bağlantısı', positions: 'Pozisyonlar'};
  const ICON = {error: '⛔', warn: '⚠️', info: 'ℹ️'};
  const reported = new Set();
  let last = null;

  async function load() {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('health ' + res.status);
    return res.json();
  }
  function render(data) {
    const box = document.getElementById('health-body');
    if (!box) return;
    box.textContent = '';
    const head = document.createElement('p');
    head.className = 'health-head';
    head.dataset.level = data.level;
    const time = new Date(data.checked_at * 1000).toLocaleTimeString(en() ? 'en-GB' : 'tr-TR');
    head.textContent = (data.level === 'ok' ? '✅ Sorun bulunmadı' : data.level === 'warn' ? '⚠️ Dikkat edilmesi gereken şeyler var' : '⛔ Düzeltilmesi gereken sorun var') + ` · ${time}`;
    box.appendChild(head);
    data.findings.forEach((f) => {
      const row = document.createElement('div');
      row.className = 'health-item';
      row.dataset.level = f.level;
      const title = document.createElement('strong');
      title.textContent = `${ICON[f.level] || ''} ${f.title}`;
      row.appendChild(title);
      if (f.detail) { const d = document.createElement('div'); d.textContent = f.detail; row.appendChild(d); }
      if (f.hint || f.target) { const h = document.createElement('div'); h.className = 'health-hint'; h.textContent = [f.hint, TARGETS[f.target] ? `→ ${TARGETS[f.target]}` : ''].filter(Boolean).join(' '); row.appendChild(h); }
      box.appendChild(row);
    });
  }
  function toBell(data) {
    const current = new Set();
    data.findings.filter((f) => f.level !== 'info').forEach((f) => {
      current.add(f.id);
      if (!reported.has(f.id)) {
        reported.add(f.id);
        window.NotifyCenter?.add(`Sistem sağlığı · ${f.title}${f.detail ? ' — ' + f.detail : ''}`, f.level === 'error' ? 'error' : 'warning');
      }
    });
    [...reported].forEach((id) => { if (!current.has(id)) reported.delete(id); });   // fixed → can be reported again
  }
  async function check(manual) {
    const box = document.getElementById('health-body');
    if (manual && box) box.textContent = en() ? 'Checking…' : 'Kontrol ediliyor…';
    try {
      last = await load();
      toBell(last);
      render(last);
    } catch (_) {
      if (manual && box) box.textContent = en() ? 'Health check could not be read.' : 'Sağlık kontrolü okunamadı.';
    }
  }
  window.runHealthCheck = () => check(true);
  if (typeof setInterval === 'function') {
    setTimeout(() => check(false), 8000);
    setInterval(() => check(false), 60000);
  }
})();
