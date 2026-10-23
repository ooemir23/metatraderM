// Desktop main screen switch (top centre): Grafik = chart + order panel, İşlemler = positions / pending /
// history / report, Bot = signal scanner + HMA bot, MevO = the /ai page embedded. Phones show everything (section bar).
(function () {
  const KEY = 'hma.main.view.v1';
  const sw = document.getElementById('main-view-switch');
  if (!sw) return;
  const VIEWS = ['chart', 'trades', 'bot', 'mevo'];
  const norm = (v) => (v === 'work' ? 'trades' : VIEWS.includes(v) ? v : 'chart');   // 'work' = old combined screen
  const read = () => { try { return norm(localStorage.getItem(KEY)); } catch (_) { return 'chart'; } };
  let view = read();

  function set(next, {persist = true, scroll = true} = {}) {
    view = norm(next);
    document.body.dataset.mainView = view;
    // The positions card holds both the trade tabs and the bot pane; pick the one this screen is for.
    if (view === 'trades' && typeof window.setWorkspaceView === 'function') window.setWorkspaceView('positions', {persist: false, focus: false});
    if (view === 'bot' && typeof window.setWorkspaceView === 'function') window.setWorkspaceView('signals', {persist: false, focus: false});
    const mv = document.getElementById('mevo-view'), frame = document.getElementById('mevo-frame');
    if (mv) mv.hidden = view !== 'mevo';
    if (view === 'mevo' && frame && !frame.getAttribute('src')) frame.setAttribute('src', frame.dataset.src);
    sw.querySelectorAll('button[data-view]').forEach((b) => b.setAttribute('aria-selected', b.dataset.view === view ? 'true' : 'false'));
    if (persist) { try { localStorage.setItem(KEY, view); } catch (_) { /* session only */ } }
    // Charts and tables measure their containers when they become visible.
    window.dispatchEvent(new Event('resize'));
    if (scroll && window.scrollY > 0) window.scrollTo({top: 0});
  }

  sw.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-view]');
    if (b) set(b.dataset.view);
  });

  // Anything that opens a workspace tab (bot alerts, "fix" toasts, Strategies, bot sound links…) shows that screen.
  const wrap = () => {
    const original = window.hxTab;
    if (typeof original !== 'function' || original.__mainView) return false;
    const wrapped = function (name) {
      const want = name === 'signals' ? 'bot' : 'trades';
      if (view !== want) set(want, {scroll: false});
      return original.apply(this, arguments);
    };
    wrapped.__mainView = true;
    window.hxTab = wrapped;
    return true;
  };
  if (!wrap()) document.addEventListener('DOMContentLoaded', wrap);

  // Open-position count on the switch, so positions are not forgotten while the chart is shown.
  const badge = document.getElementById('mv-pos-count');
  const source = document.getElementById('pos-count-badge');
  const sync = () => {
    if (!badge || !source) return;
    const n = Number(String(source.textContent).trim());
    badge.hidden = !(Number.isFinite(n) && n > 0);
    badge.textContent = Number.isFinite(n) ? String(n) : '';
  };
  if (source && typeof MutationObserver === 'function') new MutationObserver(sync).observe(source, {childList: true, characterData: true, subtree: true});
  sync();

  set(view, {persist: false, scroll: false});
  window.MainView = {set, get: () => view};
})();
