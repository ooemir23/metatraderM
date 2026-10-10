// Broker catalogue and account-scoped favourites. Selection never submits a trade.
(() => {
  const STORAGE_PREFIX = 'mt5.market-watch.favorites.v1:';
  const CATEGORIES = ['forex', 'metals', 'crypto', 'indices', 'stocks', 'commodities', 'other'];
  let account = null, scope = null, session = null, generation = 0, controller = null;
  let symbols = new Map(), favorites = [], active = '', state = 'unverified';
  let initialized = false, returnFocus = null, onSelect = null, onChange = null;
  let persistenceWarning = false;
  const node = id => document.getElementById(id);
  const t = (tr, en) => window.MT5I18n?.language?.() === 'en' ? en : tr;
  const sameAccount = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const supportedName = name => /^[A-Za-z0-9_.#-]{1,32}$/.test(name);
  const validAccount = value => Array.isArray(value) && value.length === 3
    && Number.isSafeInteger(value[0]) && value[0] > 0 && typeof value[1] === 'string' && !!value[1]
    && ['DEMO', 'REAL'].includes(value[2]);
  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function categoryLabel(category) {
    return ({forex:t('Döviz', 'Forex'), metals:t('Metaller', 'Metals'), crypto:t('Kripto', 'Crypto'),
      indices:t('Endeksler', 'Indices'), stocks:t('Hisseler', 'Stocks'),
      commodities:t('Emtialar', 'Commodities'), other:t('Diğer', 'Other')})[category] || t('Diğer', 'Other');
  }
  function getSymbol(name) { return state === 'ready' ? symbols.get(name) || null : null; }
  function isSelectionVerified(name, side) {
    const item = getSymbol(name);
    if (!account || !item || !supportedName(name) || item.selectable !== true || ![1, 2, 4].includes(item.trade_mode)) return false;
    return !(side === 'BUY' && item.trade_mode === 2) && !(side === 'SELL' && item.trade_mode === 1);
  }
  function selectionIssue(name, side) {
    if (!account) return t('Ürün seçmek için broker hesabını doğrulayın.', 'Verify the broker account before selecting an instrument.');
    if (state === 'loading') return t('Broker ürünleri yükleniyor…', 'Loading broker instruments…');
    if (state !== 'ready') return t('Broker ürünleri alınamadı. Yeniden deneyin.', 'Broker instruments are unavailable. Try again.');
    const item = symbols.get(name);
    if (!item) return t('Bu ürün bağlı broker kataloğunda yok. Ürün seçin.', 'This instrument is not in the connected broker catalogue. Select an instrument.');
    if (!supportedName(name)) return t('Bu ad web terminalinde desteklenmiyor.', 'This name is not supported in the web terminal.');
    if (item.trade_mode === 0) return t('Broker bu üründe yeni işlemleri kapatmış.', 'The broker has disabled new trades in this instrument.');
    if (item.trade_mode === 3) return t('Bu ürün yalnız mevcut pozisyonları kapatmaya açık.', 'This instrument only allows closing existing positions.');
    if (item.selectable !== true) return t('Bu broker ürünü terminalde işlem için desteklenmiyor.', 'This broker instrument is not supported for trading in this terminal.');
    if (item.trade_mode === 1 && side === 'SELL') return t('Bu ürün yalnız alış işlemlerine açık.', 'This instrument only allows buy orders.');
    if (item.trade_mode === 2 && side === 'BUY') return t('Bu ürün yalnız satış işlemlerine açık.', 'This instrument only allows sell orders.');
    return '';
  }
  function tradeLabel(item) {
    if (!supportedName(item.name)) return t('Bu ad desteklenmiyor', 'Name unsupported');
    const restricted = ({0:t('İşlem kapalı', 'Trading disabled'), 1:t('Yalnız alış', 'Buy only'),
      2:t('Yalnız satış', 'Sell only'), 3:t('Yalnız kapatma', 'Close only')})[item.trade_mode];
    return restricted || (item.selectable !== true ? t('İşlem desteklenmiyor', 'Trading unsupported') : '');
  }
  function readFavorites() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_PREFIX + scope) || '[]');
      return Array.isArray(value) ? [...new Set(value.filter(name => typeof name === 'string' && name.length > 0 && name.length <= 128))] : [];
    } catch (_) { return []; }
  }
  function saveFavorites() {
    try { localStorage.setItem(STORAGE_PREFIX + scope, JSON.stringify(favorites)); persistenceWarning = false; }
    catch (_) { persistenceWarning = true; }
  }
  function favoriteButton(name) {
    const saved = favorites.includes(name);
    const button = element('button', 'market-star' + (saved ? ' is-favorite' : ''), saved ? '★' : '☆');
    button.type = 'button';
    button.setAttribute('aria-label', saved ? t(`${name} favorilerden çıkar`, `Remove ${name} from favourites`)
      : t(`${name} favorilere ekle`, `Add ${name} to favourites`));
    button.setAttribute('aria-pressed', String(saved));
    button.addEventListener('click', () => {
      if (!account || state !== 'ready' || (!symbols.has(name) && !favorites.includes(name))) return;
      favorites = favorites.includes(name) ? favorites.filter(value => value !== name) : [...favorites, name];
      saveFavorites();
      renderFavorites();
      // Preserve keyboard focus when the result list is rebuilt.
      const focusIndex = [...node('market-results').querySelectorAll('.market-star')].indexOf(button);
      renderResults();
      const stars = node('market-results').querySelectorAll('.market-star');
      const nextFocus = stars[Math.min(focusIndex, stars.length - 1)] || node('market-search');
      nextFocus?.focus();
    });
    return button;
  }
  function choose(name) {
    const item = getSymbol(name);
    if (!account || !item || !supportedName(name)) return;
    close();
    if (onSelect) onSelect(item.name);
  }
  function renderFavorites() {
    const target = node('market-favorites');
    if (!target) return;
    target.replaceChildren();
    const available = state === 'ready' ? favorites.filter(name => symbols.has(name)) : [];
    if (!available.length) {
      const message = state === 'unverified' ? t('Hesap doğrulandığında ürünler gösterilir.', 'Instruments appear after account verification.')
        : state === 'loading' ? t('Broker ürünleri yükleniyor…', 'Loading broker instruments…')
        : state !== 'ready' ? t('Ürünler alınamadı · Yeniden deneyin', 'Instruments unavailable · Try again')
        : favorites.length ? t('Kaydedilen favoriler bu katalogda bulunamadı.', 'Saved favourites are not in this catalogue.')
        : t('Henüz favori yok · Ürün ara ve yıldızla ekle', 'No favourites yet · Search and add with a star');
      target.append(element('span', 'market-empty', message));
      return;
    }
    for (const name of available) {
      const item = symbols.get(name);
      const chip = element('button', 'market-chip' + (name === active ? ' is-active' : ''), name);
      chip.type = 'button';
      chip.setAttribute('data-no-translate', '');
      chip.setAttribute('aria-pressed', String(name === active));
      chip.title = [item.description, tradeLabel(item)].filter(Boolean).join(' · ');
      chip.disabled = !supportedName(name);
      chip.addEventListener('click', () => choose(name));
      target.append(chip);
    }
  }
  function renderResults() {
    const target = node('market-results'), status = node('market-status'), count = node('market-result-count');
    if (!target || !status) return;
    target.replaceChildren();
    const retry = node('market-retry');
    if (retry) { retry.hidden = !account; retry.disabled = state === 'loading'; }
    if (count) count.textContent = '';
    if (state !== 'ready') { status.textContent = selectionIssue(active); return; }
    const query = (node('market-search')?.value || '').trim().toLowerCase();
    const category = node('market-category')?.value || 'all';
    const missingFavorites = favorites.filter(name => !symbols.has(name));
    const catalogItems = [...symbols.values(), ...(category === 'favorites' ? missingFavorites.map(name => ({name, description:'',path:'',category:'other',missing:true})) : [])];
    const matches = catalogItems.filter(item => (category === 'all' || item.category === category || (category === 'favorites' && favorites.includes(item.name)))
      && (!query || [item.name, item.description, item.path].some(value => value.toLowerCase().includes(query))));
    status.textContent = persistenceWarning ? t('Favoriler bu oturumda kullanılabilir; tarayıcı kaydına izin verilmedi.', 'Favourites are available for this session; browser storage is blocked.')
      : !symbols.size ? t('Bu hesap için broker ürünü bulunamadı.', 'No broker instruments were found for this account.')
      : !matches.length ? t('Aramanıza uygun ürün bulunamadı.', 'No instruments match your search.')
      : category === 'favorites' && missingFavorites.length ? t('Katalogda bulunmayan favorileri yıldızla kaldırabilirsiniz.', 'Remove favourites missing from the catalogue with the star button.')
      : matches.length > 150 ? t('İlk 150 ürün gösteriliyor. Arama veya kategoriyle daraltın.', 'Showing the first 150 instruments. Refine by search or category.') : '';
    if (count) count.textContent = t(`${matches.length} / ${symbols.size} ürün`, `${matches.length} / ${symbols.size} instruments`);
    for (const item of matches.slice(0, 150)) {
      const row = element('div', 'market-row' + (item.name === active ? ' is-active' : ''));
      row.setAttribute('role', 'listitem');
      const button = element('button', 'market-select');
      button.type = 'button';
      button.disabled = item.missing || !supportedName(item.name);
      button.setAttribute('aria-label', t(`${item.name} ürününü seç`, `Select ${item.name}`));
      const name = element('strong', 'market-name', item.name);
      name.setAttribute('data-no-translate', '');
      const description = element('span', 'market-description', item.missing ? t('Bu katalogda bulunamadı', 'Not found in this catalogue')
        : item.description || item.path || categoryLabel(item.category));
      description.setAttribute('data-no-translate', '');
      button.append(name, description);
      button.addEventListener('click', () => choose(item.name));
      const badge = element('span', 'market-category-tag', item.missing ? t('Kullanılamıyor', 'Unavailable') : tradeLabel(item) || categoryLabel(item.category));
      if (item.missing || tradeLabel(item)) badge.classList.add('market-restricted');
      row.append(button, badge, favoriteButton(item.name));
      target.append(row);
    }
  }
  function renderActive() {
    const name = node('order-symbol-name');
    if (name) name.textContent = active || '—';
    const issue = node('market-selection-note');
    if (issue) issue.textContent = selectionIssue(active);
    const picker = node('order-symbol-select');
    if (picker) picker.disabled = !account;
  }
  function render() { renderFavorites(); renderResults(); renderActive(); }
  function changed() { render(); if (onChange) onChange(); }
  async function refresh(force = false) {
    if (!account) return;
    const request = ++generation, requestedAccount = [...account], requestedSession = session;
    controller?.abort();
    controller = new AbortController();
    const requestController = controller;
    const timer = setTimeout(() => requestController.abort(), 8000);
    state = 'loading';
    changed();
    const isCurrent = () => request === generation && session === requestedSession && sameAccount(account, requestedAccount);
    try {
      const response = await fetch('/api/symbols' + (force === true ? '?refresh=true' : ''), {signal:requestController.signal});
      const data = await response.json();
      if (!isCurrent()) return;
      if (!response.ok || !sameAccount(data.account, requestedAccount) || !Array.isArray(data.symbols)) throw new Error('Catalogue unavailable');
      const catalogue = new Map();
      for (const item of data.symbols) {
        if (!item || typeof item.name !== 'string' || !item.name || catalogue.has(item.name)) continue;
        catalogue.set(item.name, {...item, description:typeof item.description === 'string' ? item.description : '',
          path:typeof item.path === 'string' ? item.path : '', category:CATEGORIES.includes(item.category) ? item.category : 'other'});
      }
      symbols = catalogue;
      state = 'ready';
      changed();
    } catch (_) {
      if (!isCurrent()) return;
      symbols.clear();
      state = 'error';
      changed();
    } finally { clearTimeout(timer); }
  }
  function setAccount(value, nextSession = 0) {
    const next = validAccount(value) ? [...value] : null;
    if (sameAccount(account, next) && session === nextSession) return;
    generation++;
    controller?.abort();
    account = next;
    session = nextSession;
    symbols = new Map();
    const nextScope = next ? JSON.stringify(next) : null;
    if (nextScope !== scope) {
      scope = nextScope;
      favorites = scope ? readFavorites() : [];
      persistenceWarning = false;
      if (node('market-search')) node('market-search').value = '';
      if (node('market-category')) node('market-category').value = 'all';
    }
    state = next ? 'loading' : 'unverified';
    changed();
    if (next) return refresh();
  }
  function setActive(name) { active = typeof name === 'string' ? name : ''; render(); }
  function close() {
    const dialog = node('market-dialog');
    if (!dialog?.open) return;
    dialog.close();
    returnFocus?.focus();
  }
  function open(trigger) {
    const dialog = node('market-dialog');
    if (!dialog) return;
    returnFocus = trigger || document.activeElement;
    if (!dialog.open) dialog.showModal();
    renderResults();
    node('market-search')?.focus();
  }
  function init(options = {}) {
    onSelect = options.onSelect || onSelect;
    onChange = options.onChange || onChange;
    if (initialized || !node('market-dialog')) return;
    initialized = true;
    node('market-open')?.addEventListener('click', event => open(event.currentTarget));
    node('order-symbol-select')?.addEventListener('click', event => open(event.currentTarget));
    node('market-close')?.addEventListener('click', close);
    node('market-retry')?.addEventListener('click', () => refresh(true));
    node('market-manage-favorites')?.addEventListener('click', () => {
      node('market-search').value = '';
      node('market-category').value = 'favorites';
      renderResults();
      node('market-category').focus();
    });
    node('market-search')?.addEventListener('input', renderResults);
    node('market-category')?.addEventListener('change', renderResults);
    node('market-dialog').addEventListener('cancel', event => { event.preventDefault(); close(); });
    node('market-search')?.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); node('market-results').querySelector('.market-select')?.focus(); }
    });
    node('market-results')?.addEventListener('keydown', event => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      const buttons = [...node('market-results').querySelectorAll('.market-select')];
      const index = buttons.indexOf(event.target);
      if (index < 0) return;
      event.preventDefault();
      if (event.key === 'ArrowUp' && index === 0) node('market-search').focus();
      else buttons[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
    });
    render();
  }
  window.MT5Markets = {init, setAccount, setActive, open, close, refresh, getSymbol, isSelectionVerified, selectionIssue, version:() => generation};
})();
