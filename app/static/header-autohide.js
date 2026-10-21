// Hides the sticky top bar while the page scrolls down and shows it again on any upward scroll.
// It never hides near the top of the page or while a control inside the bar has focus.
(function () {
  const header = document.getElementById('hx-header');
  if (!header) return;
  const THRESHOLD = 8;
  let last = window.scrollY || 0, ticking = false;
  function update() {
    ticking = false;
    const y = Math.max(0, window.scrollY || 0);
    const delta = y - last;
    if (y <= header.offsetHeight) header.classList.remove('hx-header-hidden');
    else if (delta > THRESHOLD && !header.contains(document.activeElement)) header.classList.add('hx-header-hidden');
    else if (delta < -THRESHOLD) header.classList.remove('hx-header-hidden');
    if (Math.abs(delta) > THRESHOLD || y <= header.offsetHeight) last = y;
  }
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
  }, {passive:true});
})();
