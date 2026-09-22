// Additional trade actions share durable request IDs with the server journal.
const managedPositions = new Map();
const actionLocks = new Set();
let editorPosition = null;
let liveSource = null;
let lastLiveMessage = 0;
let displayedTickTime = 0;
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
function startLiveFeed() {
  if (liveSource) liveSource.close();
  lastLiveMessage=0;
  displayedTickTime=0;
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
      const indicator=document.getElementById('live-feed-status');
      if (indicator) indicator.textContent='Canlı akış';
    } catch (_) {markLiveStale();}
  };
  source.onerror=()=>{if(source===liveSource)markLiveStale();};
}
function markLiveStale() {
  lastLiveMessage=0;
  const indicator=document.getElementById('live-feed-status');
  if (indicator) indicator.textContent='Canlı veri bekleniyor';
  const badge=document.getElementById('pos-count-badge');
  if (badge) badge.textContent='Veri güncel değil';
  showAccountConnectionError('Canlı veri doğrulanamadı');
}
document.addEventListener('DOMContentLoaded',()=>{
  startLiveFeed();fetchPendingOrders();
  setInterval(()=>{
    if (!liveFeedHealthy()) fetchPendingOrders();
    const age=displayedTickTime ? Math.max(0,Math.floor(Date.now()/1000-displayedTickTime)) : null;
    const node=document.getElementById('tick-age');
    if (node) node.textContent=age===null?'Fiyat bekleniyor':`Fiyat yaşı: ${age} sn`;
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
