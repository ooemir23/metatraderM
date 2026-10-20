// Error/warning toasts that can be fixed somewhere in the panel become clickable: a click opens the
// place where the problem is solved (bot settings, order panel field, risk lock, security, account…).
// Only navigation: nothing is changed or sent by the click itself.
(() => {
  let origin = 'order';       // where the user last acted: 'bot' (bot card / signal scanner) or 'order'
  document.addEventListener('pointerdown', event => {
    const el = event.target?.closest?.('#bot-card, #signals-pane, #signal-scanner-card, #bot-settings-modal, #order-card, #mobile-trade-bar, .mt5-chart-menu');
    if (!el) return;
    origin = el.matches('#order-card, #mobile-trade-bar, .mt5-chart-menu') ? 'order' : 'bot';
  }, true);

  const focus = id => {
    const node = document.getElementById(id);
    if (!node) return false;
    node.scrollIntoView?.({behavior: 'smooth', block: 'center'});
    setTimeout(() => { node.focus?.(); node.select?.(); node.classList?.add('fix-highlight'); setTimeout(() => node.classList?.remove('fix-highlight'), 2400); }, 250);
    return true;
  };
  async function botSettings(field) {
    const modal = document.getElementById('bot-settings-modal');
    if (modal?.classList.contains('hidden') && typeof window.toggleBotSettingsModal === 'function') await window.toggleBotSettingsModal();
    if (field) setTimeout(() => focus(field), 150);
  }
  function orderPanel(field) {
    if (typeof window.setOrderExecutionType === 'function' && ['lot-input', 'sl-input', 'tp-input'].includes(field)
        && document.getElementById('order-execution-type')?.value !== 'PENDING') window.setOrderExecutionType('MARKET');
    focus(field || 'lot-input');
  }

  // Ordered rules: first match wins. `bot` / `order` give the target for each origin.
  const RULES = [
    {test: /stop riski|parasal risk|risk sınırı|trade_risk/i,
     bot: {label: 'Bot ayarları · lot ve stop', run: () => botSettings('cfg-lot')},
     order: {label: 'Emir paneli · hacim', run: () => orderPanel('lot-input')}},
    {test: /stop loss zorunlu|geçerli stop|sl .*(yanlış|geçersiz)|stop seviyesi|stops?_level|invalid stops/i,
     bot: {label: 'Bot ayarları · stop', run: () => botSettings('cfg-use-sl')},
     order: {label: 'Emir paneli · Zarar durdur (SL)', run: () => orderPanel('sl-input')}},
    {test: /take profit|kâr al|\btp\b.*(yanlış|geçersiz)/i,
     bot: {label: 'Bot ayarları · hedef', run: () => botSettings('cfg-use-tp')},
     order: {label: 'Emir paneli · Kâr al (TP)', run: () => orderPanel('tp-input')}},
    {test: /tek emir sınırı|toplam lot|en fazla açık|lot sınırı|açabilir; bu lotla/i,
     bot: {label: 'Bot ayarları · lot sınırları', run: () => botSettings('cfg-max-order-lots')},
     order: {label: 'Günlük risk ve emir sınırları', run: () => window.openTradingRiskDialog?.()}},
    {test: /\blot\b|hacim|volume/i,
     bot: {label: 'Bot ayarları · lot', run: () => botSettings('cfg-lot')},
     order: {label: 'Emir paneli · hacim', run: () => orderPanel('lot-input')}},
    {test: /günlük zarar|emir kilidi|kilitli|kilit|işlem riski|yeni emirler(e)? (kapalı|durdu)|halt/i,
     any: {label: 'Günlük risk ve emir kilidi', run: () => window.openTradingRiskDialog?.()}},
    {test: /totp|doğrulama kodu|işlem güvenliği|gerçek hesap doğrulaması/i,
     any: {label: 'İşlem Güvenliği (TOTP)', run: () => document.getElementById('security-button')?.click()}},
    {test: /hedging/i,
     any: {label: 'Broker hesabı', run: () => window.toggleLoginModal?.()}},
    {test: /hesap doğrulanamadı|mt5 bağlı değil|broker bağlantı|hesap (değişti|uyuşmuyor)|account/i,
     any: {label: 'Broker hesabı bağlantısı', run: () => window.toggleLoginModal?.()}},
    {test: /bot parametre|bot ayar|bot zaman dilimi|bot sembol|sayı sınırlarını/i,
     any: {label: 'Bot ayarları', run: () => botSettings()}},
    {test: /sinyal ayar|filtre/i,
     any: {label: 'Sinyal ayarları', run: () => window.HMASignalSettings?.open?.()}},
  ];

  function resolve(message, from = origin) {
    const text = String(message || '');
    for (const rule of RULES) if (rule.test.test(text)) return rule.any || rule[from] || rule.order || null;
    return null;
  }

  // Called by showToast for error / warning toasts.
  function attach(toast, message, type) {
    if (!toast || !['error', 'warning'].includes(type)) return false;
    const fix = resolve(message);
    if (!fix) return false;
    toast.classList.add('toast-fix');
    toast.setAttribute('role', 'button');
    toast.tabIndex = 0;
    toast.title = `Çözmek için tıklayın: ${fix.label}`;
    const hint = document.createElement('span');
    hint.className = 'toast-fix-hint';
    hint.textContent = `${window.MT5I18n?.language?.() === 'en' ? 'Click to fix' : 'Çözmek için tıkla'} → ${fix.label}`;
    toast.appendChild(hint);
    const run = () => { toast.remove(); try { fix.run(); } catch (_) {} };
    toast.addEventListener('click', run);
    toast.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); run(); } });
    return true;
  }

  window.HMAToastFix = {resolve, attach, origin: () => origin};
})();
