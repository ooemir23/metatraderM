// Account figures (balance, equity, P/L, margin) live in the top bar: hover opens them below, a click pins
// them open, a click anywhere else (or Escape) closes. The ids acc-* stay unchanged for the live updaters.
(function () {
  const wrap = document.getElementById('hx-balance');
  const toggle = document.getElementById('hx-balance-toggle');
  const menu = document.getElementById('hx-balance-menu');
  if (!wrap || !toggle || !menu) return;
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
  // Equity stays visible on the button itself.
  const src = document.getElementById('acc-equity'), dst = document.getElementById('hx-balance-equity');
  const sync = () => { if (src && dst) dst.textContent = src.textContent.trim() || '—'; };
  if (src && typeof MutationObserver === 'function') new MutationObserver(sync).observe(src, {childList: true, characterData: true, subtree: true});
  sync();
})();
