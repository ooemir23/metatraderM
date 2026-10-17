// Interface size control (A− / % / A+), shared by the terminal and the MevO page.
// The whole page is scaled with CSS zoom so px, rem and component styles stay in proportion.
// The choice is a per-browser convenience; storage failures fall back to the default size.
(function () {
  const KEY = 'hma.ui.scale.v1';
  const MIN = 0.7, MAX = 1.5, STEP = 0.1, DEFAULT = 1;
  const clamp = (value) => Math.min(MAX, Math.max(MIN, Math.round(value * 10) / 10));

  function read() {
    try {
      const value = Number(localStorage.getItem(KEY));
      return Number.isFinite(value) && value > 0 ? clamp(value) : DEFAULT;
    } catch (_) { return DEFAULT; }
  }
  function save(value) {
    try { localStorage.setItem(KEY, String(value)); } catch (_) { /* session-only */ }
  }

  let scale = read();

  function apply(value, persist) {
    scale = clamp(value);
    document.documentElement.style.zoom = scale === 1 ? '' : String(scale);
    // Keep the same on-screen page width: zooming out shows more content instead of wider margins.
    document.documentElement.style.setProperty('--ui-scale', String(scale));
    if (persist) save(scale);
    const label = document.getElementById('ui-scale-value');
    if (label) label.textContent = Math.round(scale * 100) + '%';
    const down = document.getElementById('ui-scale-down');
    const up = document.getElementById('ui-scale-up');
    if (down) down.disabled = scale <= MIN;
    if (up) up.disabled = scale >= MAX;
    // Charts and resizable panels measure their containers; let them re-layout.
    window.dispatchEvent(new Event('resize'));
  }

  // Apply before first paint to avoid a size jump.
  apply(scale, false);

  function build() {
    const slot = document.querySelector('[data-ui-scale-slot]');
    if (!slot || slot.dataset.ready) return;
    slot.dataset.ready = '1';
    const en = () => window.MT5I18n?.language?.() === 'en';
    slot.innerHTML =
      '<button id="ui-scale-down" type="button" class="ui-scale-btn" aria-label="Küçült">A−</button>' +
      '<button id="ui-scale-value" type="button" class="ui-scale-value" title="Varsayılan boyuta dön">100%</button>' +
      '<button id="ui-scale-up" type="button" class="ui-scale-btn" aria-label="Büyüt">A+</button>';
    slot.setAttribute('role', 'group');
    slot.setAttribute('aria-label', en() ? 'Interface size' : 'Arayüz boyutu');
    slot.querySelector('#ui-scale-down').addEventListener('click', () => apply(scale - STEP, true));
    slot.querySelector('#ui-scale-up').addEventListener('click', () => apply(scale + STEP, true));
    slot.querySelector('#ui-scale-value').addEventListener('click', () => apply(DEFAULT, true));
    apply(scale, false);
  }

  // Ctrl/Cmd + Alt + (+ / − / 0) as keyboard shortcuts; plain Ctrl +/− stays the browser zoom.
  document.addEventListener('keydown', (event) => {
    if (!(event.altKey && (event.ctrlKey || event.metaKey))) return;
    if (event.key === '+' || event.key === '=') { event.preventDefault(); apply(scale + STEP, true); }
    else if (event.key === '-' || event.key === '_') { event.preventDefault(); apply(scale - STEP, true); }
    else if (event.key === '0') { event.preventDefault(); apply(DEFAULT, true); }
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();

  window.HMAUiScale = {get: () => scale, set: (value) => apply(value, true)};
})();
