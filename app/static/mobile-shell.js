// Mobile shell (<760px): bottom tab bar drives the same screens as the desktop switch (MainView),
// header menu sheet, and the order form's "advanced settings" fold.
(function () {
  const tabs = document.getElementById('mx-tabs');
  const body = document.body;
  if (tabs) {
    tabs.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-view]');
      if (b && window.MainView) window.MainView.set(b.dataset.view);
      closeMenu();
    });
    const markTab = () => {
      const v = body.dataset.mainView || 'chart';
      tabs.querySelectorAll('button[data-view]').forEach((b) => b.setAttribute('aria-selected', b.dataset.view === v ? 'true' : 'false'));
    };
    if (typeof MutationObserver === 'function') new MutationObserver(markTab).observe(body, {attributes: true, attributeFilter: ['data-main-view']});
    markTab();
    const badge = document.getElementById('mx-pos-count'), source = document.getElementById('pos-count-badge');
    const sync = () => {
      if (!badge || !source) return;
      const n = Number(String(source.textContent).trim());
      badge.hidden = !(Number.isFinite(n) && n > 0);
      badge.textContent = Number.isFinite(n) ? String(n) : '';
    };
    if (source && typeof MutationObserver === 'function') new MutationObserver(sync).observe(source, {childList: true, characterData: true, subtree: true});
    sync();
  }

  const more = document.getElementById('mx-more');
  const scrim = document.getElementById('mx-scrim');
  function closeMenu() {
    body.classList.remove('mx-menu-open');
    if (more) more.setAttribute('aria-expanded', 'false');
  }
  if (more) more.addEventListener('click', () => {
    const open = !body.classList.contains('mx-menu-open');
    body.classList.toggle('mx-menu-open', open);
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  if (scrim) scrim.addEventListener('click', closeMenu);
  // Choosing something in the sheet (tools, MevO link) closes it; account/language controls stay usable.
  document.getElementById('hx-actions')?.addEventListener('click', (e) => {
    if (e.target.closest('#tools-toggle, a[href="/ai"]')) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

  const adv = document.getElementById('mx-adv-btn'), card = document.getElementById('order-card');
  if (adv && card) adv.addEventListener('click', () => {
    const open = card.classList.toggle('mx-adv');
    adv.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // Bot screen: the signal scanner and the manual-style card fold away behind one-tap headers.
  document.querySelectorAll('.mx-fold-btn[data-fold]').forEach((btn) => btn.addEventListener('click', () => {
    btn.setAttribute('aria-expanded', btn.getAttribute('aria-expanded') === 'true' ? 'false' : 'true');
  }));
})();
