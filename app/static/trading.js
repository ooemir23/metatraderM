// Additional trade actions share durable request IDs with the server journal.
const managedPositions = new Map();
const actionLocks = new Set();
let editorPosition = null;
let liveSource = null;
let lastLiveMessage = 0;
let displayedTickTime = 0;
let displayedQuoteStatus = null;
let displayedQuoteReceivedAt = 0;
let quoteDiagnosticFailed = false;
function safeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function rememberPositions(rows) {
  managedPositions.clear();
  rows.forEach(p => managedPositions.set(p.ticket, p));
}
function showPositionEditor(ticket) {
  const p = managedPositions.get(ticket);
  if (!p) return showToast('Pozisyon güncel listede bulunamadı.', 'error');
  editorPosition = {...p};
  document.getElementById('position-editor-title').textContent = `${p.symbol} ${p.type} · #${p.ticket} · ${p.volume} lot`;
  document.getElementById('edit-sl').value = p.sl || 0;
  document.getElementById('edit-tp').value = p.tp || 0;
  document.getElementById('partial-volume').value = '';
  document.getElementById('position-editor-status').textContent = '';
  document.getElementById('position-editor').showModal();
}
async function guardedAction(key, endpoint, payload) {
  if (actionLocks.has(key)) return null;
  actionLocks.add(key);
  try {
    const storeKey = 'managed-intent:' + key;
    const previous = localStorage.getItem(storeKey);
    let intent = previous ? JSON.parse(previous) : null;
    if (intent && JSON.stringify(intent.payload) !== JSON.stringify(payload)) {
      throw new Error('Önceki talebin sonucu belirsiz. MT5 durumunu kontrol edip belirsiz emirleri temizleyin.');
    }
    if (!intent) {
      intent = {request_id:newOrderId(),payload};
      localStorage.setItem(storeKey, JSON.stringify(intent));
    }
    const res = await fetch(endpoint, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...payload,request_id:intent.request_id})});
    const data = await res.json();
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(storeKey);
    const message = data.uncertain ? 'Sonuç belirsiz; MT5 durumunu kontrol edin.' : data.pending ? 'Talep kabul edildi; broker sonucu bekleniyor.' : data.partial ? 'Talep kısmen gerçekleşti.' : data.success ? 'İşlem tamamlandı.' : (data.detail || data.error || 'İşlem tamamlanamadı.');
    showToast(message, data.success && !data.partial ? 'success' : 'error');
    const status = document.getElementById('position-editor-status');
    if (status) status.textContent = message;
    fetchPositions(true);fetchPendingOrders();fetchAccount(true);
    return data;
  } catch (err) {
    const message = err.message || 'Sonuç belirsiz; MT5 üzerinden kontrol edin.';
    showToast(message, 'error');
    const status = document.getElementById('position-editor-status');
    if (status) status.textContent = message;
    return null;
  } finally {
    actionLocks.delete(key);
    if (typeof refreshOrderRecovery === "function") refreshOrderRecovery();
  }
}
async function savePositionStops() {
  const p = editorPosition;
  if (!p) return;
  const sl = Number(document.getElementById('edit-sl').value), tp = Number(document.getElementById('edit-tp').value);
  if (![sl,tp].every(v=>Number.isFinite(v)&&v>=0)) return showToast('Geçerli SL/TP fiyatları girin.', 'error');
  const result = await guardedAction('stops:'+p.ticket, '/api/position/stops', {ticket:p.ticket,sl,tp,expected_sl:p.sl,expected_tp:p.tp});
  if (result?.success && !result.pending && !result.partial) {
    editorPosition = {...p,sl,tp};
  }
}
async function partialClosePosition() {
  const p=editorPosition;
  if (!p) return;
  const volume=Number(document.getElementById('partial-volume').value);
  if (!Number.isFinite(volume)||volume<=0||volume>=p.volume) return showToast('Açık lottan küçük, pozitif bir miktar girin.', 'error');
  const result=await guardedAction('partial:'+p.ticket,'/api/position/partial-close',{ticket:p.ticket,volume});
  if (result?.success && !result.pending) document.getElementById('position-editor').close();
}
async function submitPendingOrder() {
  const selectedSymbol = currentSymbol;
  if (!await verifyChartOrderSymbol() || currentSymbol !== selectedSymbol) return showToast('Grafik ve emir sembolü doğrulanamadı veya değişti. Emir gönderilmedi.', 'error');
  const pending_type=document.getElementById('pending-type').value;
  const volume=Number(document.getElementById('pending-volume').value);
  const entry_price=Number(document.getElementById('pending-price').value);
  const sl_points=Number(document.getElementById('pending-sl').value), tp_points=Number(document.getElementById('pending-tp').value);
  if (![volume,entry_price].every(v=>Number.isFinite(v)&&v>0)||![sl_points,tp_points].every(v=>Number.isInteger(v)&&v>=0)) return showToast('Fiyat, lot ve puan alanlarını kontrol edin.', 'error');
  const symbol=currentSymbol;
  await guardedAction('pending:'+symbol,'/api/order/pending', {symbol,pending_type,order_type:pending_type.startsWith('BUY')?'BUY':'SELL',volume,entry_price,sl_points,tp_points});
}
async function cancelPendingOrder(ticket) {
  await guardedAction('cancel:'+ticket,'/api/order/cancel',{ticket});
}
function renderPendingOrders(orders) {
  const root=document.getElementById('pending-orders-list');
  if (!root) return;
  root.innerHTML=orders.length ? orders.map(o=>`<div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800 py-2"><span><strong>${safeText(o.symbol)}</strong> ${safeText(o.type)}<br><span class="text-gray-400">#${Number(o.ticket)} · ${Number(o.volume)} lot @ ${Number(o.price)}</span></span><button onclick="cancelPendingOrder(${Number(o.ticket)})" class="px-2 py-1 rounded bg-rose-500/10 text-rose-300">İptal et</button></div>`).join('') : '<p class="text-gray-500 py-2">Bekleyen emir yok.</p>';
}
let fetchingPending=false;
async function fetchPendingOrders() {
  if (fetchingPending) return;
  fetchingPending=true;
  try {
    const response=await fetch('/api/orders');
    if (!response.ok) throw new Error('Veri alınamadı');
    renderPendingOrders(await response.json());
  } catch (_) {
    const root=document.getElementById('pending-orders-list');
    if (root) root.textContent='Bekleyen emir bilgisi doğrulanamadı.';
  } finally {fetchingPending=false;}
}
function liveFeedHealthy() {return Date.now()-lastLiveMessage<2500;}
function updateQuoteStatus() {
  const en = window.MT5I18n?.language?.() === 'en';
  const indicator = document.getElementById('live-feed-status');
  if (indicator) indicator.textContent = liveFeedHealthy()
    ? (en ? 'Server connection active' : 'Sunucu bağlantısı açık')
    : (en ? 'Waiting for server connection' : 'Sunucu bağlantısı bekleniyor');
  const node = document.getElementById('tick-age');
  const brokerNode = document.getElementById('mt5-feed-status');
  const diagnosticCurrent = !quoteDiagnosticFailed && displayedQuoteStatus && Date.now()-displayedQuoteReceivedAt < 5000;
  if (brokerNode) brokerNode.textContent = !diagnosticCurrent
    ? (en ? 'MT5 broker connection not verified' : 'MT5 broker bağlantısı doğrulanamadı')
    : displayedQuoteStatus.broker_connected === true
      ? (en ? 'MT5 broker connected' : 'MT5 broker bağlantısı açık')
      : displayedQuoteStatus.broker_connected === false
        ? (en ? 'MT5 broker disconnected' : 'MT5 broker bağlantısı kesik')
        : (en ? 'MT5 broker connection not verified' : 'MT5 broker bağlantısı doğrulanamadı');
  if (!node) return;
  // Server-measured age avoids interpreting the user's computer clock as broker time.
  const age = displayedQuoteStatus?.age_seconds != null
    ? displayedQuoteStatus.age_seconds + (Date.now()-displayedQuoteReceivedAt)/1000
    : Date.now()/1000 - displayedTickTime;
  if (!Number.isFinite(displayedTickTime) || displayedTickTime <= 0) {
    node.textContent = displayedQuoteStatus?.state === 'invalid'
      ? (en ? 'Broker quote invalid' : 'Broker fiyatı geçersiz')
      : (en ? 'MT5 has not provided a quote for this symbol' : 'MT5 bu sembol için fiyat bildirmedi');
  } else if (age < 0) {
    node.textContent = en ? 'Price time is in the future; check clock settings' : 'Fiyat zamanı ileride; saat ayarını kontrol edin';
  } else if (age > 10) {
    const seconds = Math.floor(age);
    const elapsed = `${Math.floor(seconds/3600)}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
    node.textContent = en ? `Price outdated · Last quote ${elapsed} ago`
      : `Fiyat güncel değil · Son fiyat ${elapsed} önce`;
  } else {
    node.textContent = en ? `Price updated ${Math.floor(age)} s ago` : `Fiyat ${Math.floor(age)} sn önce güncellendi`;
  }
}
function startLiveFeed() {
  if (liveSource) liveSource.close();
  lastLiveMessage=0;
  displayedTickTime=0;
  displayedQuoteStatus=null;
  quoteDiagnosticFailed=false;
  updateQuoteStatus();
  if (typeof EventSource==='undefined') return;
  const symbol=currentSymbol;
  const source=new EventSource('/api/live?symbol='+encodeURIComponent(symbol));
  liveSource=source;
  source.onmessage=event=>{
    if (source!==liveSource) return;
    try {
      const data=JSON.parse(event.data);
      if (data.error) throw new Error(data.error);
      lastLiveMessage=Date.now();
      renderAccountData(data.account);
      renderPositionsData(data.positions);
      renderPendingOrders(data.orders);
      if (data.prices[symbol]) renderPriceData(data.prices[symbol]);
      else {displayedTickTime=0; displayedQuoteStatus=null;}
      updateQuoteStatus();
    } catch (_) {markLiveStale();}
  };
  source.onerror=()=>{if(source===liveSource)markLiveStale();};
}
function markLiveStale() {
  lastLiveMessage=0;
  quoteDiagnosticFailed=true;
  updateQuoteStatus();
  const badge=document.getElementById('pos-count-badge');
  if (badge) badge.textContent='Veri güncel değil';
  showAccountConnectionError('Canlı veri doğrulanamadı');
}
document.addEventListener('DOMContentLoaded',()=>{
  startLiveFeed();fetchPendingOrders();
  setInterval(()=>{
    if (!liveFeedHealthy()) fetchPendingOrders();
    updateQuoteStatus();
  },2000);
});
window.addEventListener('beforeunload',()=>{if(liveSource)liveSource.close();});

async function fetchExecutionHealth() {
  const label = document.getElementById('execution-health');
  try {
    const response = await fetch('/api/operations/latency');
    if (!response.ok) throw new Error('Ölçüm alınamadı.');
    const data = await response.json(), run = data.execution_ms, queue = data.queue_ms;
    const format = value => value == null ? '—' : `${value} ms`;
    label.textContent = `İşleme (${run.count}): ortanca ${format(run.p50)} · %95 ${format(run.p95)} · en yüksek ${format(run.max)}. Kuyruk (${queue.count}): %95 ${format(queue.p95)}.`;
  } catch (_) { label.textContent = 'Gecikme bilgisi şu anda alınamıyor.'; }
}
let checkingOrderResults = false;
async function checkReconciledOrders() {
  if (checkingOrderResults || tradeActionBusy()) return;
  checkingOrderResults = true;
  const symbol = currentSymbol;
  try {
   for (const key of ['order-intent:' + symbol, 'managed-intent:pending:' + symbol]) {
    const stored = localStorage.getItem(key);
    if (!stored) continue;
    const intent = JSON.parse(stored);
    const response = await fetch('/api/operations/status?request_id=' + encodeURIComponent(intent.request_id));
    if (!response.ok) continue;
    const result = await response.json();
    if (!result.reconciled || result.uncertain || result.pending || tradeActionBusy() || localStorage.getItem(key) !== stored) continue;
    localStorage.removeItem(key);
    setOrderNotice(symbol, result.success ? 'Emir brokerdan doğrulandı' : 'Emir tamamlanmadı',
      result.success ? `Bilet #${result.ticket}${result.partial ? ' · Kısmi gerçekleşme' : ''}. Yeni emir gönderilmedi.` : result.error,
      result.success ? 'success' : 'error');
    fetchPositions(true); fetchAccount(true); fetchPendingOrders();
   }
  } catch (_) { /* Unknown remains protected; never resend. */ }
  finally { checkingOrderResults = false; }
}
setInterval(checkReconciledOrders, 15000);
