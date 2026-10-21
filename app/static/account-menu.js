// "Hesap" button in the top bar: opens a small menu with the broker connection status and account type.
(function () {
  const toggle = document.getElementById('hx-account-toggle');
  const menu = document.getElementById('hx-account-menu');
  const wrap = document.getElementById('hx-account');
  if (!toggle || !menu || !wrap) return;
  function setOpen(open) {
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  toggle.addEventListener('click', event => { event.stopPropagation(); setOpen(menu.hidden); });
  menu.addEventListener('click', event => { if (event.target.closest('button')) setOpen(false); });
  document.addEventListener('click', event => { if (!menu.hidden && !wrap.contains(event.target)) setOpen(false); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) { setOpen(false); toggle.focus(); }
  });
})();
