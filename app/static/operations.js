// Read-only broker risk and operations panels. No order is placed here.
let previewTimer;
let previewSide = 'BUY';
let previewGeneration = 0;
let latestStopGuidance = null;
const tradePreviewResults = new Map();
function tradePreviewIdentity(side = previewSide) {
  const symbol = typeof currentSymbol !== 'undefined' ? currentSymbol : window.currentSymbol;
  const volume = Number(document.getElementById('lot-input')?.value);
  const slValue = String(document.getElementById('sl-input')?.value ?? '').trim(), tpValue = String(document.getElementById('tp-input')?.value ?? '').trim();
  const sl_points = slValue ? Number(slValue) : NaN;
  const tp_points = tpValue ? Number(tpValue) : NaN;
  return {symbol, volume, sl_points, tp_points, side,
    key:JSON.stringify([symbol, side, volume, sl_points, tp_points,
      typeof accountSessionGeneration === 'undefined' ? null : accountSessionGeneration,
      window.MT5Markets?.version?.() ?? null])};
}
function tradePreviewIssue(side) {
  const saved = tradePreviewResults.get(tradePreviewIdentity(side).key);
  const en = window.MT5I18n?.language?.() === 'en';
  if (!saved || Date.now() - saved.at < 0 || Date.now() - saved.at > 15000)
    return en ? 'Verifying pre-trade risk.' : 'İşlem öncesi risk doğrulanıyor.';
  return saved.result.success === true ? '' : saved.result.detail || saved.result.error
    || (en ? 'Pre-trade risk could not be verified.' : 'İşlem öncesi risk doğrulanamadı.');
}
async function requestTradePreview(identity, account) {
  if (window.MT5Markets?.isSelectionVerified(identity.symbol, identity.side) === false)
    return {success:false,error:window.MT5Markets.selectionIssue(identity.symbol, identity.side)};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/api/trade/preview', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({symbol:identity.symbol,order_type:identity.side,volume:identity.volume,
        sl_points:identity.sl_points,tp_points:identity.tp_points,...account}),signal:controller.signal});
    const result = await response.json();
    return response.ok && result?.success === true ? result
      : {...result,success:false,error:result?.detail || result?.error || 'Risk verisi alınamadı.'};
  } catch (error) {
    return {success:false,error:error.name === 'AbortError' ? 'Risk kontrolü zamanında yanıt vermedi. Emir gönderilmedi.' : 'Risk kontrolü bağlantısı doğrulanamadı.'};
  } finally { clearTimeout(timer); }
}
window.MT5TradePreview = {
  issue:tradePreviewIssue,
  verify:async side => {
    previewSide = side;
    return await refreshTradePreview() === true && !tradePreviewIssue(side);
  }
};
function markTradePreview(state, text) {
  const label = document.getElementById('trade-preview');
  if (label?.dataset) label.dataset.state = state;
  if (label?.setAttribute) label.setAttribute('aria-busy', String(state === 'refreshing'));
  const status = document.getElementById('trade-preview-status');
  if (status) status.textContent = text;
  if (typeof updateOrderButtons === 'function') updateOrderButtons();
}
function renderTradePolicy(result) {
  const label = document.getElementById('trade-risk-policy');
  if (!label) return;
  const en = window.MT5I18n?.language?.() === 'en';
  const cap = result?.effective_trade_risk_pct ?? result?.max_trade_risk_pct;
  const parts = [];
  if (result?.stop_required) parts.push(en ? 'A stop loss is required on this real account.' : 'Bu gerçek hesapta Stop Loss zorunlu.');
  if (Number.isFinite(cap) && cap > 0) parts.push(en ? `Maximum risk per order: ${cap}% of equity.` : `Emir başına azami risk: varlığın %${cap}'i.`);
  label.textContent = parts.join(' ');
}
function renderStopGuidance(guidance, identity) {
  const label = document.getElementById('stop-distance-guidance');
  const button = document.getElementById('apply-stop-minimum');
  const valid = guidance && ['min_sl_points','min_tp_points','point','spread_points'].every(k => Number.isFinite(guidance[k])) && guidance.point > 0;
  latestStopGuidance = valid ? {guidance, key:identity.key, at:Date.now()} : null;
  if (label) label.textContent = valid
    ? `${identity.symbol}: 1 puan = ${guidance.point} fiyat birimi. Spread ${guidance.spread_points} puan. 1 puan payla SL en az ${guidance.min_sl_points}, TP en az ${guidance.min_tp_points} puan (0 = yok). SL genişlerse olası kayıp artar.`
    : '';
  if (button) button.hidden = !valid || !((identity.sl_points > 0 && identity.sl_points < guidance.min_sl_points) || (identity.tp_points > 0 && identity.tp_points < guidance.min_tp_points));
}
function applyStopMinimum() {
  const identity = tradePreviewIdentity(), saved = latestStopGuidance;
  if (!saved || saved.key !== identity.key || Date.now()-saved.at > 15000 || (typeof chartSelectionBlocked !== 'undefined' && chartSelectionBlocked)
      || window.MT5Markets?.isSelectionVerified(identity.symbol, identity.side) === false) {
    scheduleTradePreview();
    return;
  }
  for (const [id, minimum] of [['sl-input',saved.guidance.min_sl_points],['tp-input',saved.guidance.min_tp_points]]) {
    const input = document.getElementById(id), value = Number(input.value);
    if (value > 0 && value < minimum) input.value = String(minimum);
  }
  renderStopGuidance(null, identity);
  scheduleTradePreview(); // Recalculate the changed risk. Never send an order.
}
function scheduleTradePreview(side) {
  if (side) previewSide = side;
  clearTimeout(previewTimer);
  const generation = ++previewGeneration;
  const en = window.MT5I18n?.language?.() === 'en';
  markTradePreview('refreshing', en ? 'Recalculating the selected order…' : 'Seçili emir yeniden hesaplanıyor…');
  renderStopGuidance(null, tradePreviewIdentity());
  renderTradePolicy(null);
  previewTimer = setTimeout(() => refreshTradePreview(generation), 350);
}
async function refreshTradePreview(scheduledGeneration) {
  const label = document.getElementById('trade-preview');
  if (!label) return;
  const accountUnavailable = () => (typeof accountSwitching !== 'undefined' && accountSwitching)
    || (typeof accountVerificationKnown !== 'undefined' && accountVerificationKnown && !currentAccountIdentity);
  if (accountUnavailable()) {
    label.textContent = 'Hesap doğrulanamadı; risk tahmini kullanılamaz.';
    markTradePreview('unavailable', '');
    renderStopGuidance(null, tradePreviewIdentity());
    renderTradePolicy(null);
    return;
  }
  if (typeof chartSelectionBlocked !== 'undefined' && chartSelectionBlocked) {
    label.textContent = 'Grafik ürünü MT5 sembolüyle eşleşmiyor; emir girişi kapalı.';
    markTradePreview('unavailable', '');
    renderStopGuidance(null, tradePreviewIdentity());
    return;
  }
  const otherSide = previewSide === 'BUY' ? 'SELL' : 'BUY';
  if (window.MT5Markets?.isSelectionVerified(tradePreviewIdentity().symbol, previewSide) === false
      && window.MT5Markets?.isSelectionVerified(tradePreviewIdentity().symbol, otherSide) === true) previewSide = otherSide;
  if (window.MT5Markets?.isSelectionVerified(tradePreviewIdentity().symbol, previewSide) === false) {
    label.textContent = window.MT5Markets.selectionIssue(tradePreviewIdentity().symbol, previewSide);
    markTradePreview('unavailable', '');
    renderStopGuidance(null, tradePreviewIdentity());
    renderTradePolicy(null);
    return;
  }
  if (scheduledGeneration === undefined) clearTimeout(previewTimer);
  const generation = scheduledGeneration === undefined ? ++previewGeneration : scheduledGeneration;
  if (generation !== previewGeneration) return;
  const identity = tradePreviewIdentity();
  const {symbol, volume, sl_points, tp_points, side, key} = identity;
  if (!Number.isFinite(volume) || volume <= 0 || ![sl_points,tp_points].every(value=>Number.isInteger(value)&&value>=0)) {
    label.textContent = 'Geçerli lot ve SL/TP mesafesi girin.';
    markTradePreview('unavailable', '');
    renderStopGuidance(null, identity);
    return;
  }
  // Leave the last result visible while refreshing; replacing it with a short
  // loading message makes the order card jump whenever the pointer moves.
  const isCurrent = () => generation === previewGeneration && tradePreviewIdentity().key === key
    && !(typeof chartSelectionBlocked !== 'undefined' && chartSelectionBlocked) && !accountUnavailable()
    && window.MT5Markets?.isSelectionVerified(symbol, side) !== false;
  let receivedGuidance = false;
  const en = window.MT5I18n?.language?.() === 'en';
  markTradePreview('refreshing', en ? 'Recalculating the selected order…' : 'Seçili emir yeniden hesaplanıyor…');
  try {
    const identities = ['BUY','SELL'].map(value => tradePreviewIdentity(value));
    const account = typeof tradingAccountPayload === 'function' ? tradingAccountPayload() : {};
    const results = await Promise.all(identities.map(value => requestTradePreview(value, account)));
    if (!isCurrent()) return;
    tradePreviewResults.clear();
    identities.forEach((value, index) => tradePreviewResults.set(value.key, {at:Date.now(), result:results[index]}));
    const result = results[side === 'BUY' ? 0 : 1];
    renderStopGuidance(result.stop_guidance, identity);
    renderTradePolicy(result);
    receivedGuidance = !!result.stop_guidance;
    if (!result.success) throw new Error(result.detail || result.error || 'Risk verisi alınamadı.');
    const money = n => `${Number(n).toLocaleString(window.MT5I18n?.language() === 'en' ? 'en-US' : 'tr-TR', {maximumFractionDigits:2})} ${result.currency}`;
    const en = window.MT5I18n?.language() === 'en';
    let message = result.stop_risk == null
      ? (en ? 'No verified stop-loss risk. Set a stop before trading.' : 'Doğrulanmış stop riski yok. İşlemden önce stop belirleyin.')
      : (en ? `Stop risk ${money(result.stop_risk)} (${result.risk_pct_equity}% of equity)` : `Stop riski ${money(result.stop_risk)} (varlığın %${result.risk_pct_equity}'i)`);
    message += result.margin_required == null
      ? (en ? ' · Margin unavailable' : ' · Teminat hesaplanamadı')
      : (en ? ` · Required margin ${money(result.margin_required)}` : ` · Gerekli teminat ${money(result.margin_required)}`);
    message += en ? ` · Spread ${result.spread_points} points` : ` · Spread ${result.spread_points} puan`;
    message += en ? ` · Existing stop risk ${money(result.existing_stop_risk)} · Daily loss ${money(result.daily_loss)}`
      : ` · Açık stop riski ${money(result.existing_stop_risk)} · Günlük zarar ${money(result.daily_loss)}`;
    if (result.positions_without_stop) message += en
      ? ` · ${result.positions_without_stop} open positions without a stop`
      : ` · ${result.positions_without_stop} açık pozisyonda stop yok`;
    message += en ? ' · Estimate only: commissions, swaps and slippage excluded. Actual loss can exceed this figure.'
      : ' · Tahmin: komisyon, swap ve kayma hariç. Gerçek zarar bu tutarı aşabilir.';
    label.textContent = message;
    markTradePreview('verified', en ? `${symbol} · ${side} · ${volume} lot · Estimate updated`
      : `${symbol} · ${side} · ${volume} lot · Tahmin güncellendi`);
    return true;
  } catch (error) {
    if (isCurrent()) {
      if (!receivedGuidance) renderStopGuidance(null, identity);
      label.textContent = error.message || 'Risk verisi doğrulanamadı.';
      markTradePreview('unavailable', en ? 'Risk estimate could not be verified' : 'Risk tahmini doğrulanamadı');
    }
  }
}
document.addEventListener('DOMContentLoaded', () => {
  for (const id of ['lot-input','sl-input','tp-input']) document.getElementById(id)?.addEventListener('input', () => scheduleTradePreview());
  document.getElementById('order-buy-btn')?.addEventListener('mouseenter', () => { if (previewSide !== 'BUY') scheduleTradePreview('BUY'); });
  document.getElementById('order-sell-btn')?.addEventListener('mouseenter', () => { if (previewSide !== 'SELL') scheduleTradePreview('SELL'); });
  document.getElementById('order-buy-btn')?.addEventListener('focus', () => { if (previewSide !== 'BUY') scheduleTradePreview('BUY'); });
  document.getElementById('order-sell-btn')?.addEventListener('focus', () => { if (previewSide !== 'SELL') scheduleTradePreview('SELL'); });
  scheduleTradePreview('BUY');
  setInterval(() => { if (document.getElementById('trade-preview')) refreshTradePreview(); }, 10000);
  initializeSecurityControl();
  refreshOperationEvents();
  setInterval(refreshOperationEvents, 5000);
});

let lastOperationEventId = 0;
let operationEventsInitialized = false;
function operationEventText(event, en) {
  const label = {filled:en?'Filled':'Gerçekleşti',rejected:en?'Rejected':'Reddedildi',pending:en?'Pending':'Beklemede',
    partial:en?'Partial fill':'Kısmi gerçekleşme',uncertain:en?'Outcome uncertain':'Sonuç belirsiz',
    resolved:en?'Reconciled':'Doğrulandı','still uncertain':en?'Still uncertain':'Hâlâ belirsiz',
    'MT5 connection lost':en?'MT5 connection lost':'MT5 bağlantısı kesildi',
    'MT5 connection restored':en?'MT5 connection restored':'MT5 bağlantısı yeniden kuruldu',
    'Emergency close-all started':en?'Emergency close-all started':'Acil toplu kapatma başlatıldı',
    'New orders resumed':en?'New orders resumed':'Yeni emirlere devam edildi',
    'Broker account connected':en?'Broker account connected':'Broker hesabına bağlanıldı'}[event.message]
    || (event.message.startsWith('Daily loss ') && !en ? event.message.replace('Daily loss ', 'Günlük zarar ') : event.message);
  const detail = event.detail ? (window.MT5I18n?.translate?.(event.detail) || event.detail) : '';
  return `${event.symbol ? event.symbol + ' · ' : ''}${label}${detail ? ': ' + detail : ''}`;
}

let fetchingOperationEvents = false;
const operationNoticeTimes = new Map();
function operationNoticeKey(event) {
  return JSON.stringify([event.request_id,event.kind,event.level,event.message,event.detail,event.symbol]);
}
function operationEventNeedsNotice(event) {
  // These request failures are already explained in the order/preview panels;
  // keep the audit rows without duplicating their warning as a toast.
  return event.level !== 'info' && !(event.kind === 'operator' &&
    /POST \/api\/(?:trade\/preview|order\/(?:open|pending|close|cancel)|position\/(?:stops|partial-close)) HTTP 4\d\d$/.test(event.message));
}

async function refreshOperationEvents() {
  const root = document.getElementById('operations-events');
  if (!root || fetchingOperationEvents) return;
  fetchingOperationEvents = true;
  try {
    const response = await fetch('/api/operations/events');
    if (!response.ok) return;
    const events = await response.json();
    const en = window.MT5I18n?.language() === 'en';
    root.replaceChildren();
    for (const event of events.slice().reverse()) {
      const row = document.createElement('p');
      row.className = 'border-b border-gray-800 py-1 ' + (event.level === 'error' ? 'text-rose-300' : event.level === 'warning' ? 'text-amber-300' : '');
      const label = operationEventText(event, en);
      row.textContent = `${new Date(event.created*1000).toLocaleTimeString(en?'en-US':'tr-TR')} · ${label}${event.request_id?' · '+event.request_id.slice(0,8):''}`;
      root.append(row);
    }
    if (!events.length) root.textContent = en ? 'No events yet.' : 'Henüz olay yok.';
    const newest = events.at(-1)?.id || 0;
    if (operationEventsInitialized && newest > lastOperationEventId) {
      const important = events.filter(e => e.id > lastOperationEventId && operationEventNeedsNotice(e));
      const now = Date.now();
      for (const [key, shown] of operationNoticeTimes) if (now-shown >= 30000) operationNoticeTimes.delete(key);
      const fresh = important.filter(e => {
        const key = operationNoticeKey(e);
        return !operationNoticeTimes.has(key);
      });
      const selected = fresh.filter(e => e.level === 'error').at(-1) || fresh.at(-1);
      if (selected && typeof showToast === 'function') {
        const more = fresh.length > 1 ? (en ? ` · ${fresh.length-1} more events in the events panel`
          : ` · Olaylar panelinde ${fresh.length-1} ek kayıt`) : '';
        showToast(operationEventText(selected, en) + more, selected.level === 'error' ? 'error' : 'warning');
        for (const e of fresh) operationNoticeTimes.set(operationNoticeKey(e), now);
      }
      const badge = document.getElementById('event-count');
      if (badge) badge.textContent = important.length ? `(${important.length})` : '';
    }
    lastOperationEventId = Math.max(newest, lastOperationEventId);
    operationEventsInitialized = true;
  } catch (_) { root.textContent = window.MT5I18n?.language() === 'en' ? 'Events unavailable.' : 'Olaylar alınamadı.'; }
  finally { fetchingOperationEvents = false; }
}

async function runStrategyBacktest() {
  const target = document.getElementById('backtest-result');
  if (!target) return;
  const en = window.MT5I18n?.language() === 'en';
  const candles = Number(document.getElementById('backtest-candles')?.value);
  const commission_points = Number(document.getElementById('backtest-commission')?.value);
  const payload = {symbol:currentSymbol, candles, commission_points,
    timeframe_minutes:15,
    hma_period:Number(document.getElementById('cfg-hma-period')?.value || 14),
    second_ma_type:document.getElementById('cfg-ma2-type')?.value || 'EMA',
    second_ma_period:Number(document.getElementById('cfg-ma2-period')?.value || 34),
    sl_points:200, tp_points:0};
  target.textContent = en ? 'Running historical simulation…' : 'Geçmiş veri testi çalışıyor…';
  try {
    const response = await fetch('/api/backtest', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Test failed');
    const line = (label, part) => `${label}: ${part.count} ${en?'trades':'işlem'}, ${part.net_points} ${en?'net points':'net puan'}, ${part.win_rate_pct ?? '—'}% ${en?'wins':'kazanç'}, ${part.max_drawdown_points} ${en?'max drawdown points':'azami düşüş puanı'}`;
    target.textContent = line(en?'First 70%':'İlk %70',data.in_sample) + ' · ' + line(en?'Last 30%':'Son %30',data.out_of_sample);
  } catch (error) { target.textContent = error.message; }
}

async function checkBrokerCapabilities() {
  const target = document.getElementById('broker-diagnostics');
  if (!target) return;
  const en = window.MT5I18n?.language() === 'en';
  target.textContent = en ? 'Checking broker settings…' : 'Broker ayarları okunuyor…';
  try {
    const response = await fetch('/api/broker/diagnostics?symbol='+encodeURIComponent(currentSymbol));
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Broker data unavailable');
    const spec = data.symbol;
    target.textContent = `${data.account_type} · ${data.position_mode} · ${en?'Min lot':'Min. lot'} ${spec.volume_min} · ${en?'Lot step':'Lot adımı'} ${spec.volume_step} · ${en?'Stop distance':'Stop mesafesi'} ${spec.stops_level_points} ${en?'points':'puan'} · ${en?'Limit orders':'Limit emir'} ${data.limit_order_supported?'✓':'✗'} · ${en?'Stop orders':'Stop emir'} ${data.stop_order_supported?'✓':'✗'} · FOK ${data.fok_supported?'✓':'✗'} · IOC ${data.ioc_supported?'✓':'✗'}`;
    if (data.dry_run?.available) target.textContent += ' · OrderCheck: ' + data.dry_run.checks.map(c => `${c.name} ${c.accepted?'✓':'✗'}${c.accepted?'':` (${c.retcode ?? '—'})`}`).join(', ');
    else target.textContent += en ? ' · OrderCheck unavailable' : ' · OrderCheck kullanılamıyor';
  } catch (error) { target.textContent = error.message; }
}

async function initializeSecurityControl() {
  const selector = document.getElementById('language-select');
  if (!selector) return;
  const en = window.MT5I18n?.language() === 'en';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'px-2.5 py-1 rounded-lg bg-gray-800 border border-gray-700 text-xs text-amber-300';
  button.textContent = en ? 'Trading Security' : 'İşlem Güvenliği';
  selector.parentElement.append(button);
  const dialog = document.createElement('dialog');
  dialog.className = 'rounded-2xl border border-gray-700 bg-[#1e2430] p-5 text-gray-100 shadow-2xl backdrop:bg-black/70 w-[min(92vw,420px)]';
  dialog.innerHTML = `<h2 class="text-lg font-bold">${en?'Real-account verification':'Gerçek hesap doğrulaması'}</h2>
    <p class="mt-2 text-sm text-gray-300">${en?'Enter the six-digit code from your authenticator. Access lasts 15 minutes.':'Kimlik doğrulama uygulamanızdaki altı haneli kodu girin. Erişim 15 dakika sürer.'}</p>
    <p class="mt-1 text-xs text-gray-400">${en?'The setup key is available only on the server.':'Kurulum anahtarı yalnızca sunucuda bulunur.'}</p>
    <form method="dialog" class="mt-4"><input aria-label="${en?'Verification code':'Doğrulama kodu'}" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" class="w-full rounded-lg bg-gray-900 border border-gray-600 p-2 text-center tracking-widest" required>
      <p class="mt-2 text-xs text-rose-300" role="status"></p><div class="mt-4 flex justify-end gap-2"><button value="cancel" class="px-3 py-2 rounded-lg bg-gray-700">${en?'Cancel':'İptal'}</button><button value="unlock" class="px-3 py-2 rounded-lg bg-emerald-600">${en?'Unlock':'Kilidi Aç'}</button></div></form>`;
  document.body.append(dialog);
  button.addEventListener('click', async () => {
    try {
      const response = await fetch('/api/security/status');
      const status = await response.json();
      if (status.unlocked) {
        await fetch('/api/security/lock', {method:'POST'});
        button.textContent = en ? 'Trading Security' : 'İşlem Güvenliği';
      } else dialog.showModal();
    } catch (_) { dialog.showModal(); }
  });
  dialog.querySelector('form').addEventListener('submit', async event => {
    if (event.submitter?.value !== 'unlock') return;
    event.preventDefault();
    const code = dialog.querySelector('input').value;
    const message = dialog.querySelector('[role="status"]');
    try {
      const response = await fetch('/api/security/unlock', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
      if (!response.ok) throw new Error((await response.json()).detail || 'Verification failed');
      dialog.querySelector('input').value = '';
      dialog.close();
      button.textContent = en ? 'Trading Unlocked' : 'İşlem Kilidi Açık';
    } catch (error) { message.textContent = error.message; }
  });
  try {
    const status = await (await fetch('/api/security/status')).json();
    if (status.unlocked) button.textContent = en ? 'Trading Unlocked' : 'İşlem Kilidi Açık';
  } catch (_) { /* The action itself remains protected by the server. */ }
}
