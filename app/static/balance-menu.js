// Account figures (balance, equity, P/L, margin) live in the top bar: hover opens them below, a click pins
// them open, a click anywhere else (or Escape) closes. Starred figures are shown on the top-bar button itself and
// an eye button blurs all amounts. The ids acc-* stay unchanged for the live updaters; values are only mirrored.
(function () {
  const wrap = document.getElementById('hx-balance');
  const toggle = document.getElementById('hx-balance-toggle');
  const menu = document.getElementById('hx-balance-menu');
  if (!wrap || !toggle || !menu) return;
  const FAV_KEY = 'hma.balance.favs.v1', HIDE_KEY = 'hma.balance.hidden.v1';
  const SOURCES = {balance: 'acc-balance', equity: 'acc-equity', profit: 'acc-profit', margin: 'acc-margin', free: 'acc-margin-free', level: 'acc-margin-level'};
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* session only */ } },
  };
  let favs = (() => {
    try { const v = JSON.parse(store.get(FAV_KEY)); if (Array.isArray(v)) return v.filter(k => SOURCES[k]); } catch (_) { /* default */ }
    return ['equity'];
  })();
  let hidden = store.get(HIDE_KEY) === '1';

  // ---- open / close ----
  let pinned = false, hoverTimer = 0;
  function setOpen(open) {
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open) pinned = false;
  }
  const canHover = () => !window.matchMedia || window.matchMedia('(hover: hover)').matches;
  wrap.addEventListener('mouseenter', () => { if (!canHover()) return; clearTimeout(hoverTimer); setOpen(true); });
  wrap.addEventListener('mouseleave', () => {
    if (pinned) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => { if (!pinned) setOpen(false); }, 150);
  });
  toggle.addEventListener('click', (event) => {
    event.stopPropagation();
    if (pinned) { setOpen(false); return; }
    pinned = true; setOpen(true);
  });
  document.addEventListener('click', (event) => { if (!menu.hidden && !wrap.contains(event.target)) setOpen(false); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !menu.hidden) { setOpen(false); toggle.focus(); }
  });
  // Clicking inside the panel (stars, eye) keeps it open and pins it so it does not vanish while choosing.
  menu.addEventListener('click', () => { pinned = true; });

  // ---- stars: one per figure ----
  const label = (key) => document.querySelector(`.hx-metric[data-fav="${key}"] > span:first-child`)?.textContent.trim() || key;
  menu.querySelectorAll('.hx-metric[data-fav]').forEach((tile) => {
    const key = tile.dataset.fav;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'hx-star'; b.dataset.key = key;
    b.title = 'Favori: üst çubukta göster'; b.setAttribute('aria-label', 'Favori: üst çubukta göster');
    b.innerHTML = '<i class="ph-bold ph-star"></i>';
    b.addEventListener('click', (event) => {
      event.stopPropagation(); pinned = true;
      favs = favs.includes(key) ? favs.filter(k => k !== key) : [...favs, key];
      store.set(FAV_KEY, JSON.stringify(favs));
      render();
    });
    tile.appendChild(b);
  });

  // ---- render favourites on the top-bar button ----
  const favBox = document.getElementById('hx-balance-favs');
  const text = (key) => (document.getElementById(SOURCES[key])?.textContent || '').trim() || '—';
  function render() {
    menu.querySelectorAll('.hx-star').forEach((b) => {
      const on = favs.includes(b.dataset.key);
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      const icon = b.querySelector('i'); if (icon) icon.className = on ? 'ph-fill ph-star' : 'ph-bold ph-star';
    });
    if (favBox) {
      favBox.innerHTML = '';
      const order = Object.keys(SOURCES).filter(k => favs.includes(k));
      if (!order.length) { favBox.textContent = 'Hesap'; }
      order.forEach((key) => {
        const item = document.createElement('span');
        item.className = 'hx-fav-item';
        const name = document.createElement('em'); name.textContent = label(key);
        const value = document.createElement('strong'); value.className = 'hx-fav-value'; value.textContent = text(key);
        if (key === 'profit') value.dataset.tone = /^[-−]|-\s?\d/.test(value.textContent) ? 'loss' : 'profit';
        item.append(name, value); favBox.appendChild(item);
      });
    }
  }

  // ---- hide amounts (blur, so the live updaters keep working) ----
  const eye = document.getElementById('hx-balance-hide');
  function applyHidden() {
    document.body.dataset.hideAmounts = hidden ? '1' : '';
    if (eye) {
      eye.setAttribute('aria-pressed', hidden ? 'true' : 'false');
      const icon = eye.querySelector('i'); if (icon) icon.className = hidden ? 'ph-bold ph-eye-slash' : 'ph-bold ph-eye';
    }
  }
  eye?.addEventListener('click', (event) => {
    event.stopPropagation(); pinned = true;
    hidden = !hidden; store.set(HIDE_KEY, hidden ? '1' : '0'); applyHidden();
  });

  // Keep the mirrored values in sync with the live ones.
  if (typeof MutationObserver === 'function') {
    const observer = new MutationObserver(render);
    Object.values(SOURCES).forEach((id) => { const n = document.getElementById(id); if (n) observer.observe(n, {childList: true, characterData: true, subtree: true}); });
  }
  applyHidden(); render();
})();
