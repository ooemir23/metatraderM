// Desktop main screen switch (top centre): "Grafik" = chart + order panel, "İşlemler & Bot" = positions,
// pending, history, report, signals and bot on the full page. Phones show both (they have the section bar).
(function () {
  const KEY = 'hma.main.view.v1';
  const sw = document.getElementById('main-view-switch');
  if (!sw) return;
  const read = () => { try { return localStorage.getItem(KEY) === 'work' ? 'work' : 'chart'; } catch (_) { return 'chart'; } };
  let view = read();

  function set(next, {persist = true, scroll = true} = {}) {
    view = next === 'work' ? 'work' : 'chart';
    document.body.dataset.mainView = view;
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
      if (view !== 'work') set('work', {scroll: false});
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
