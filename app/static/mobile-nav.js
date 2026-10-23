// Mobile section bar: Chart · Order · Trades · Bot. Opens the right workspace tab and scrolls there,
// so the bot is one tap away instead of below the chart and the whole order form.
(function () {
  const nav = document.getElementById('mx-nav');
  if (!nav) return;
  const target = {
    chart: () => document.getElementById('chart-card'),
    order: () => document.getElementById('order-card'),
    positions: () => document.getElementById('positions-card'),
    bot: () => document.getElementById('positions-card'),
  };
  const mobile = () => window.matchMedia?.('(max-width: 759px)').matches;

  function go(name) {
    if (name === 'positions' && typeof window.hxTab === 'function') window.hxTab('positions');
    if (name === 'bot' && typeof window.hxTab === 'function') window.hxTab('signals');
    const el = name === 'bot' ? (document.getElementById('bot-card') || target.bot()) : target[name]?.();
    if (!el) return;
    const offset = (nav.getBoundingClientRect().height || 0) + 8;
    window.scrollTo({top: el.getBoundingClientRect().top + window.scrollY - offset, behavior: 'smooth'});
    mark(name);
  }
  function mark(name) {
    nav.querySelectorAll('button').forEach((b) => b.setAttribute('aria-current', b.dataset.mx === name ? 'true' : 'false'));
  }
  function current() {
    if (!mobile()) return;
    const line = (nav.getBoundingClientRect().bottom || 0) + 40;
    let name = 'chart';
    for (const key of ['chart', 'order', 'positions']) {
      const el = target[key]();
      if (el && el.getBoundingClientRect().top <= line) name = key;
    }
    if (name === 'positions' && document.getElementById('positions-card')?.dataset?.hxTab === 'signals') name = 'bot';
    mark(name);
  }
  nav.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-mx]');
    if (b) go(b.dataset.mx);
  });
  window.addEventListener('scroll', () => requestAnimationFrame(current), {passive: true});
  current();
  window.MobileNav = {go, current};
})();
