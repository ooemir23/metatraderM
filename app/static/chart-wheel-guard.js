// Mouse-wheel zoom on charts only after a click on the chart (like embedded maps):
// before that the wheel scrolls the page. Leaving the chart or clicking elsewhere locks it again.
(function () {
  const HINT = 'Yakınlaştırmak için önce grafiğe tıklayın';

  function attach(area, {onArm, onDisarm, armTarget = area, leaveByOutsideMove = false} = {}) {
    if (!area) return () => {};
    let armed = false;
    let hintTimer = 0;
    const hint = document.createElement('div');
    hint.className = 'chart-wheel-hint';
    hint.setAttribute('role', 'status');
    hint.textContent = window.MT5I18n?.translate?.(HINT) || HINT;
    hint.hidden = true;
    if (getComputedStyle(area).position === 'static') area.style.position = 'relative';
    area.appendChild(hint);

    const set = (value) => {
      if (armed === value) return;
      armed = value;
      area.classList.toggle('chart-wheel-armed', value);
      hint.hidden = true;
      (value ? onArm : onDisarm)?.();
    };
    const onPointerDown = (event) => { if (event.button === 0 || event.pointerType === 'touch') set(true); };
    const onLeave = () => set(false);
    const onDocDown = (event) => { if (!area.contains(event.target)) set(false); };
    // Entering an iframe looks like "leaving" to the page, so iframe charts lock again when the
    // pointer moves over anything outside the chart instead.
    const onDocMove = (event) => { if (armed && !area.contains(event.target)) set(false); };
    const onWheel = () => {
      if (armed) return;
      hint.hidden = false;
      clearTimeout(hintTimer);
      hintTimer = setTimeout(() => { hint.hidden = true; }, 1400);
    };
    armTarget.addEventListener('pointerdown', onPointerDown);
    if (leaveByOutsideMove) document.addEventListener('pointermove', onDocMove, true);
    else area.addEventListener('pointerleave', onLeave);
    // Capture phase: the chart library may stop wheel propagation inside its canvas.
    area.addEventListener('wheel', onWheel, {passive: true, capture: true});
    document.addEventListener('pointerdown', onDocDown, true);
    onDisarm?.();
    return () => {
      clearTimeout(hintTimer);
      armTarget.removeEventListener('pointerdown', onPointerDown);
      area.removeEventListener('pointerleave', onLeave);
      document.removeEventListener('pointermove', onDocMove, true);
      area.removeEventListener('wheel', onWheel, {capture: true});
      document.removeEventListener('pointerdown', onDocDown, true);
      hint.remove();
    };
  }

  // TradingView embed is an iframe that always takes the wheel: cover it until the first click.
  function guardFrame(container) {
    if (!container) return () => {};
    container.querySelector(':scope > .chart-wheel-shield')?.remove();
    const shield = document.createElement('div');
    shield.className = 'chart-wheel-shield';
    shield.title = HINT;
    container.appendChild(shield);
    const release = attach(container, {
      armTarget: shield,
      leaveByOutsideMove: true,
      onArm: () => { shield.hidden = true; },
      onDisarm: () => { shield.hidden = false; },
    });
    return () => { release(); shield.remove(); };
  }

  window.HMAChartWheel = {attach, guardFrame};
})();
