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
    if (persist) { try { localStorage.setItem(KEY, view); } catch (_) { /* session-only */ } }
    if (view === 'signals' && opening) {
      if (typeof refreshSignalScanner === 'function') refreshSignalScanner(true, true);
      if (typeof fetchBotStatus === 'function') fetchBotStatus();
      if (typeof fetchBotLiveSignal === 'function') fetchBotLiveSignal();
    }
    if (focus && opening) (view === 'signals' ? signals.querySelector('.signals-back') : $('signals-toggle'))?.focus({preventScroll: true});
    // Chart / table widths change after the transition.
    setTimeout(() => window.dispatchEvent(new Event('resize')), 320);
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
  // Pending bot signals waiting for approval light a dot on the toggle.
  function syncDot() {
    const box = $('bot-pending-signal-box'), dot = $('signals-toggle-dot');
    if (dot) dot.hidden = !box || box.classList.contains('hidden');
  }

  function init() {
    const watch = (id, fn, opts) => { const node = $(id); if (node && typeof MutationObserver === 'function') new MutationObserver(fn).observe(node, opts); };
    watch('pos-count-badge', syncStrip, {childList: true, characterData: true, subtree: true});
    watch('acc-profit', syncStrip, {childList: true, characterData: true, subtree: true, attributes: true});
    watch('bot-pending-signal-box', syncDot, {attributes: true, attributeFilter: ['class']});
    syncStrip(); syncDot();
    setWorkspaceView(storedView(), {persist: false, focus: false});
  }

  window.setWorkspaceView = setWorkspaceView;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
