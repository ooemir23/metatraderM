// Positions <-> Signal scanner + HMA bot accordion in the lower-left card.
// "positions": positions full width. "signals": positions fold into a strip on the
// left and the scanner and the bot are shown side by side in the same space.
(function () {
  const KEY = 'hma.workspace.view.v1';
  const $ = (id) => document.getElementById(id);

  function storedView() {
    try { return localStorage.getItem(KEY) === 'signals' ? 'signals' : 'positions'; } catch (_) { return 'positions'; }
  }

  function setWorkspaceView(view, {persist = true, focus = true} = {}) {
    const split = $('workspace-split'), positions = $('positions-pane'), signals = $('signals-pane');
    if (!split || !positions || !signals) return;
    view = view === 'signals' ? 'signals' : 'positions';
    const opening = split.dataset.view !== view;
    split.dataset.view = view;
    $('positions-card')?.classList.toggle('signals-open', view === 'signals');
    // inert keeps the folded pane out of tab order and screen readers.
    positions.toggleAttribute('inert', view === 'signals');
    signals.toggleAttribute('inert', view !== 'signals');
    $('signals-toggle')?.setAttribute('aria-expanded', String(view === 'signals'));
    $('positions-strip')?.setAttribute('aria-expanded', String(view === 'positions'));
    (document.querySelectorAll ? document.querySelectorAll('#workspace-switch .ws-tab') : []).forEach(tab => {
      const active = tab.dataset.view === view;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    if (persist) { try { localStorage.setItem(KEY, view); } catch (_) { /* session-only */ } }
    if (view === 'signals' && opening) {
      if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
      if (typeof fetchBotStatus === 'function') fetchBotStatus();
      if (typeof fetchBotLiveSignal === 'function') fetchBotLiveSignal();
    }
    if (focus && opening) (view === 'signals' ? signals.querySelector('.signals-back') : $('signals-toggle'))?.focus({preventScroll: true});
    // Chart / table widths change after the transition.
    setTimeout(() => window.dispatchEvent(new Event('resize')), 320);
    hxSyncTabs();
  }

  // ---- Terminal design: one tab bar (Pozisyonlar · Bekleyen emirler · Geçmiş · Rapor · Sinyal & Bot) ----
  const HX_TABS = ['positions', 'pending', 'history', 'reports', 'signals'];
  function hxActive() {
    if ($('workspace-split')?.dataset.view === 'signals') return 'signals';
    const tab = typeof currentPositionTab === 'string' ? currentPositionTab : 'open';
    if (tab === 'closed') return 'history';
    if (tab === 'reports') return 'reports';
    return $('positions-card')?.dataset?.pendingMode === '1' ? 'pending' : 'positions';
  }
  function hxSyncTabs() {
    const active = hxActive();
    const card = $('positions-card');
    if (card?.dataset) card.dataset.hxTab = active;
    (document.querySelectorAll ? document.querySelectorAll('#workspace-switch .hx-tab') : []).forEach(tab => {
      const on = tab.dataset.hx === active;
      tab.classList?.toggle('is-active', on);
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    });
  }
  function hxTab(name) {
    if (!HX_TABS.includes(name)) return;
    const card = $('positions-card');
    if (card?.dataset) card.dataset.pendingMode = name === 'pending' ? '1' : '';
    if (name === 'signals') {
      setWorkspaceView('signals', {focus: false});
    } else {
      setWorkspaceView('positions', {focus: false});
      if (typeof switchPositionTab === 'function') switchPositionTab(name === 'history' ? 'closed' : name === 'reports' ? 'reports' : 'open');
      if (name === 'pending' && typeof fetchPendingOrders === 'function') fetchPendingOrders();
    }
    hxSyncTabs();
  }

  // Mirror position count and floating P&L into the folded strip.
  function syncStrip() {
    const count = $('positions-strip-count'), pnl = $('positions-strip-pnl');
    const badge = $('pos-count-badge'), profit = $('acc-profit');
    if (count && badge) count.textContent = /^\d+$/.test(badge.textContent.trim()) ? badge.textContent.trim() : '–';
    if (pnl && profit) {
      pnl.textContent = profit.textContent.trim();
      pnl.dataset.tone = profit.className.includes('rose') ? 'loss' : profit.className.includes('emerald') ? 'profit' : '';
    }
  }
  // Toggle badges: green = directional scanner signals (pulses on fresh crossovers),
  // amber = bot signals waiting for approval.
  function setBadge(id, count, title) {
    const node = $(id);
    if (!node) return;
    node.hidden = !(count > 0);
    node.textContent = count > 99 ? '99+' : String(count);
    if (title) node.title = title;
  }
  function onScanner(event) {
    const {signals = 0, crosses = 0} = event.detail || {};
    setBadge('signals-count', signals, `${signals} sinyal · ${crosses} yeni kesişim`);
    $('signals-count')?.classList.toggle('has-cross', crosses > 0);
    // The tab only counts what needs attention: new crossovers (a trend direction is always present).
    setBadge('ws-tab-signals-count', crosses, `${crosses} yeni kesişim`);
    const cross = $('ws-tab-signals-count');
    if (cross && crosses > 0) cross.textContent = `${crosses > 99 ? '99+' : crosses} yeni`;
  }
  function onPending(event) {
    const count = Number(event.detail?.count) || 0;
    setBadge('signals-pending', count, `${count} bot sinyali onay bekliyor`);
    setBadge('ws-tab-pending', count, `${count} bot sinyali onay bekliyor`);
  }

  // ---- Tools drawer (strategy test, research, market depth, system log) ----
  const seenErrors = new Set();
  function errorRows() {
    return [...($('system-logs')?.querySelectorAll('.text-rose-400') || [])].map(row => row.textContent.trim());
  }
  function syncErrorBadge() {
    const open = $('tools-drawer')?.classList.contains('open');
    const rows = errorRows();
    if (open) rows.forEach(row => seenErrors.add(row));
    setBadge('tools-error-badge', open ? 0 : rows.filter(row => !seenErrors.has(row)).length);
  }
  let toolsReturnFocus = null;
  function setToolsDrawer(open) {
    const drawer = $('tools-drawer'), backdrop = $('tools-backdrop');
    if (!drawer) return;
    if (open) toolsReturnFocus = document.activeElement;
    // Closing the drawer also disconnects the native MT5 screen so the panel's order buttons unlock;
    // the terminal itself keeps running on the server.
    const terminal = $('native-terminal-panel');
    if (!open && terminal?.open) terminal.open = false;
    drawer.classList.toggle('open', open);
    drawer.toggleAttribute('inert', !open);
    drawer.setAttribute('aria-hidden', String(!open));
    if (backdrop) backdrop.hidden = !open;
    $('tools-toggle')?.setAttribute('aria-expanded', String(open));
    syncErrorBadge();
    if (open) drawer.querySelector('.tools-drawer-close')?.focus({preventScroll: true});
    else toolsReturnFocus?.focus?.({preventScroll: true});
  }

  function init() {
    const watch = (id, fn, opts) => { const node = $(id); if (node && typeof MutationObserver === 'function') new MutationObserver(fn).observe(node, opts); };
    watch('pos-count-badge', syncStrip, {childList: true, characterData: true, subtree: true});
    watch('acc-profit', syncStrip, {childList: true, characterData: true, subtree: true, attributes: true});
    watch('system-logs', syncErrorBadge, {childList: true, subtree: true});
    window.addEventListener('hma:scanner', onScanner);
    window.addEventListener('hma:bot-pending', onPending);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && $('tools-drawer')?.classList.contains('open')
          && !document.fullscreenElement && !document.querySelector('dialog[open]')) setToolsDrawer(false);
    });
    // Arrow keys move between the tabs (WAI-ARIA tabs pattern).
    $('workspace-switch')?.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const i = HX_TABS.indexOf(hxActive());
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? HX_TABS.length - 1
        : (i + (event.key === 'ArrowRight' ? 1 : HX_TABS.length - 1)) % HX_TABS.length;
      hxTab(HX_TABS[next]);
      document.querySelector?.(`#workspace-switch [data-hx="${HX_TABS[next]}"]`)?.focus();
    });
    syncStrip(); errorRows().forEach(row => seenErrors.add(row));
    setWorkspaceView(storedView(), {persist: false, focus: false});
    // The main screen (Grafik / İşlemler / Bot / MevO) decides which pane of this card is shown.
    if (window.MainView) window.MainView.set(window.MainView.get(), {persist: false, scroll: false});
  }

  window.setWorkspaceView = setWorkspaceView;
  window.hxTab = hxTab;
  window.hxSyncTabs = hxSyncTabs;
  window.setToolsDrawer = setToolsDrawer;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
