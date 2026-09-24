// Read-only broker risk and operations panels. No order is placed here.
let previewTimer;
let previewSide = 'BUY';
function scheduleTradePreview(side) {
  if (side) previewSide = side;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshTradePreview, 350);
}
async function refreshTradePreview() {
  const label = document.getElementById('trade-preview');
  if (!label) return;
  const symbol = window.currentSymbol || currentSymbol;
  const volume = Number(document.getElementById('lot-input')?.value);
  const sl_points = Number(document.getElementById('sl-input')?.value);
  if (!Number.isFinite(volume) || volume <= 0 || !Number.isInteger(sl_points) || sl_points < 0) {
    label.textContent = 'Geçerli lot ve stop mesafesi girin.';
    return;
  }
  const identity = `${symbol}:${previewSide}:${volume}:${sl_points}`;
  label.textContent = 'Risk hesaplanıyor…';
  try {
    const response = await fetch('/api/trade/preview', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({symbol,order_type:previewSide,volume,sl_points})});
    const result = await response.json();
    if (`${currentSymbol}:${previewSide}:${Number(document.getElementById('lot-input')?.value)}:${Number(document.getElementById('sl-input')?.value)}` !== identity) return;
    if (!response.ok || !result.success) throw new Error(result.detail || result.error || 'Risk verisi alınamadı.');
    const money = n => `${Number(n).toLocaleString(window.MT5I18n?.language() === 'en' ? 'en-US' : 'tr-TR', {maximumFractionDigits:2})} ${result.currency}`;
    const en = window.MT5I18n?.language() === 'en';
    label.textContent = result.stop_risk == null
      ? (en ? 'No verified stop-loss risk. Set a stop before trading.' : 'Doğrulanmış stop riski yok. İşlemden önce stop belirleyin.')
      : (en ? `Stop risk ${money(result.stop_risk)} (${result.risk_pct_equity}% of equity)` : `Stop riski ${money(result.stop_risk)} (varlığın %${result.risk_pct_equity}'i)`);
    label.textContent += result.margin_required == null
      ? (en ? ' · Margin unavailable' : ' · Teminat hesaplanamadı')
      : (en ? ` · Required margin ${money(result.margin_required)}` : ` · Gerekli teminat ${money(result.margin_required)}`);
    label.textContent += en ? ` · Spread ${result.spread_points} points` : ` · Spread ${result.spread_points} puan`;
    label.textContent += en ? ` · Existing stop risk ${money(result.existing_stop_risk)} · Daily loss ${money(result.daily_loss)}`
      : ` · Açık stop riski ${money(result.existing_stop_risk)} · Günlük zarar ${money(result.daily_loss)}`;
    if (result.positions_without_stop) label.textContent += en
      ? ` · ${result.positions_without_stop} open positions without a stop`
      : ` · ${result.positions_without_stop} açık pozisyonda stop yok`;
    label.textContent += en ? ' · Commission excluded' : ' · Komisyon hariç';
  } catch (error) {
    label.textContent = error.message || 'Risk verisi doğrulanamadı.';
  }
}
document.addEventListener('DOMContentLoaded', () => {
  for (const id of ['lot-input','sl-input']) document.getElementById(id)?.addEventListener('input', () => scheduleTradePreview());
  document.getElementById('order-buy-btn')?.addEventListener('mouseenter', () => scheduleTradePreview('BUY'));
  document.getElementById('order-sell-btn')?.addEventListener('mouseenter', () => scheduleTradePreview('SELL'));
  scheduleTradePreview('BUY');
  initializeSecurityControl();
  refreshOperationEvents();
  setInterval(refreshOperationEvents, 5000);
});

let lastOperationEventId = 0;
let operationEventsInitialized = false;
async function refreshOperationEvents() {
  const root = document.getElementById('operations-events');
  if (!root) return;
  try {
    const response = await fetch('/api/operations/events');
    if (!response.ok) return;
    const events = await response.json();
    const en = window.MT5I18n?.language() === 'en';
    root.replaceChildren();
    for (const event of events.slice().reverse()) {
      const row = document.createElement('p');
      row.className = 'border-b border-gray-800 py-1 ' + (event.level === 'error' ? 'text-rose-300' : event.level === 'warning' ? 'text-amber-300' : '');
      const label = {filled:en?'Filled':'Gerçekleşti',rejected:en?'Rejected':'Reddedildi',pending:en?'Pending':'Beklemede',
        partial:en?'Partial fill':'Kısmi gerçekleşme',uncertain:en?'Outcome uncertain':'Sonuç belirsiz',
        resolved:en?'Reconciled':'Doğrulandı','still uncertain':en?'Still uncertain':'Hâlâ belirsiz',
        'MT5 connection lost':en?'MT5 connection lost':'MT5 bağlantısı kesildi',
        'MT5 connection restored':en?'MT5 connection restored':'MT5 bağlantısı yeniden kuruldu',
        'Emergency close-all started':en?'Emergency close-all started':'Acil toplu kapatma başlatıldı',
        'New orders resumed':en?'New orders resumed':'Yeni emirlere devam edildi',
        'Broker account connected':en?'Broker account connected':'Broker hesabına bağlanıldı'}[event.message]
        || (event.message.startsWith('Daily loss ') && !en ? event.message.replace('Daily loss ', 'Günlük zarar ') : event.message);
      row.textContent = `${new Date(event.created*1000).toLocaleTimeString(en?'en-US':'tr-TR')} · ${label}${event.request_id?' · '+event.request_id.slice(0,8):''}`;
      root.append(row);
    }
    if (!events.length) root.textContent = en ? 'No events yet.' : 'Henüz olay yok.';
    const newest = events.at(-1)?.id || 0;
    if (operationEventsInitialized && newest > lastOperationEventId) {
      const important = events.filter(e => e.id > lastOperationEventId && e.level !== 'info');
      if (important.length && typeof showToast === 'function') showToast(en ? 'Trading events need attention. Open the events panel.' : 'İşlem olaylarını kontrol edin.', 'error');
      const badge = document.getElementById('event-count');
      if (badge) badge.textContent = important.length ? `(${important.length})` : '';
    }
    lastOperationEventId = Math.max(newest, lastOperationEventId);
    operationEventsInitialized = true;
  } catch (_) { root.textContent = window.MT5I18n?.language() === 'en' ? 'Events unavailable.' : 'Olaylar alınamadı.'; }
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
