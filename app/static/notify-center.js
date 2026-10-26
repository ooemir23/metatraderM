// Notification bell: every toast is also kept in a history (newest first) so a message that vanished after a few
// seconds can be read again. The history lives in this browser only (localStorage, newest 100).
(function () {
  const KEY = 'hma.notifications.v1', MAX = 100;
  const bell = document.getElementById('nc-bell'), panel = document.getElementById('nc-panel'), list = document.getElementById('nc-list');
  const badge = document.getElementById('nc-badge'), wrap = document.getElementById('nc-wrap');
  if (!bell || !panel || !list || !badge || !wrap) return;
  const load = () => { try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v : []; } catch (_) { return []; } };
  let items = load(), unread = items.filter(i => !i.read).length;
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(items.slice(0, MAX))); } catch (_) { /* session only */ } };
  const en = () => window.MT5I18n?.language?.() === 'en';
  const clock = (t) => new Date(t).toLocaleTimeString(en() ? 'en-GB' : 'tr-TR', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const day = (t) => new Date(t).toLocaleDateString(en() ? 'en-GB' : 'tr-TR', {day: '2-digit', month: '2-digit'});

  // Where a notification belongs: the screen/tab/row it talks about (null = informational only).
  function route(message) {
    const text = String(message || '');
    const ticket = (text.match(/#(\d{6,})/) || [])[1];
    if (/Sistem sağlığı/.test(text)) return {label: en() ? 'System health' : 'Sistem sağlığı', go: () => { window.setToolsDrawer?.(true); const d = document.getElementById('health-panel'); if (d) { d.open = true; d.scrollIntoView?.({block: 'nearest'}); } }};
    if (/Teknik hata|Sunucu hatası|Sunucuya ulaşılamadı/.test(text)) return {label: en() ? 'Tools · logs' : 'Araçlar · günlük', go: () => window.setToolsDrawer?.(true)};
    if (ticket) {
      const open = document.querySelector(`[data-close-ticket="${ticket}"]`);
      return {label: open ? (en() ? 'Positions' : 'Pozisyonlar') : (en() ? 'History' : 'Geçmiş'), go: () => {
        window.MainView?.set('trades', {persist: false, scroll: false});
        window.hxTab?.(open ? 'positions' : 'history');
        const row = open ? document.querySelector(`[data-close-ticket="${ticket}"]`)?.closest('tr') : null;
        if (row) { row.scrollIntoView?.({block: 'center', behavior: 'smooth'}); row.classList.add('nc-flash'); setTimeout(() => row.classList.remove('nc-flash'), 2400); }
      }};
    }
    if (/MevO/.test(text)) return {label: 'MevO', go: () => window.MainView?.set('mevo', {persist: false, scroll: false})};
    if (/\bBot\b|HMA Algoritmik|Manuel kopya|Başlangıç uyarısı|Sinyal|sinyal/.test(text)) return {label: 'Bot', go: () => { window.MainView?.set('bot', {persist: false, scroll: false}); window.hxTab?.('signals'); }};
    if (/[Ee]mir|Piyasa kapalı|lot/.test(text)) return {label: en() ? 'Order' : 'Emir', go: () => { window.MainView?.set('chart', {persist: false, scroll: false}); document.getElementById('order-card')?.scrollIntoView?.({block: 'start', behavior: 'smooth'}); }};
    return null;
  }

  function render() {
    unread = items.filter(i => !i.read).length;
    badge.hidden = !unread;
    badge.textContent = unread > 99 ? '99+' : String(unread);
    document.body.classList.toggle('nc-has-unread', unread > 0);
    list.textContent = '';
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'nc-empty';
      empty.textContent = en() ? 'No notifications yet.' : 'Henüz bildirim yok.';
      list.appendChild(empty);
      return;
    }
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'nc-item';
      row.dataset.type = item.type || 'info';
      if (!item.read) row.classList.add('is-new');
      const time = document.createElement('time');
      time.textContent = `${day(item.t)} ${clock(item.t)}`;
      const text = document.createElement('span');
      text.textContent = item.message;
      row.append(time, text);
      const target = route(item.message);
      if (target) {
        row.classList.add('is-link');
        row.tabIndex = 0;
        row.setAttribute('role', 'button');
        const hint = document.createElement('em');
        hint.textContent = '→ ' + target.label;
        row.appendChild(hint);
        const open = () => { setOpen(false); try { target.go(); } catch (_) { /* never break the bell */ } };
        row.addEventListener('click', open);
        row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
      }
      list.appendChild(row);
    });
  }
  function setOpen(open) {
    panel.hidden = !open;
    bell.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { render(); } else if (items.some(i => !i.read)) { items.forEach(i => { i.read = true; }); save(); render(); }
  }
  bell.addEventListener('click', event => { event.stopPropagation(); setOpen(panel.hidden); });
  document.getElementById('nc-clear')?.addEventListener('click', () => { items = []; save(); render(); });
  document.addEventListener('click', event => { if (!panel.hidden && !wrap.contains(event.target)) setOpen(false); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { setOpen(false); bell.focus(); } });

  function record(message, type) {
    const text = String(message ?? '').trim();
    if (!text) return;
    // The same message repeated within a few seconds (polling loops) is one entry.
    const last = items[0];
    if (last && last.message === text && Date.now() - last.t < 5000) { last.t = Date.now(); }
    else items.unshift({t: Date.now(), message: text, type: type || 'info', read: !panel.hidden});
    items.length = Math.min(items.length, MAX);
    save();
    render();
  }
  const original = window.showToast;
  if (typeof original === 'function' && !original.__nc) {
    const wrapped = function (message, type) { try { record(message, type); } catch (_) { /* never block the toast */ } return original.apply(this, arguments); };
    wrapped.__nc = true;
    window.showToast = wrapped;
  }
  window.NotifyCenter = {add: (message, type) => { try { record(message, type); } catch (_) { /* ignore */ } }};
  render();
})();
