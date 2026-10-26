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
  render();
})();
