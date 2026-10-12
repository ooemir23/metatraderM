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
let depthRequestRevision=0;
let displayedDepth=null;
async function refreshMarketDepth() {
  const revision=++depthRequestRevision,symbol=currentSymbol,generation=accountSessionGeneration,account=JSON.stringify(currentAccountIdentity);
  const status=document.getElementById('market-depth-status'),root=document.getElementById('market-depth-levels');
  if(!status||!root)return;
  displayedDepth=null;root.innerHTML='';status.textContent='Broker derinliği doğrulanıyor…';
  try {
    const response=await fetch('/api/market/depth?symbol='+encodeURIComponent(symbol));
    const data=await response.json();
    if(revision!==depthRequestRevision||symbol!==currentSymbol||generation!==accountSessionGeneration||account!==JSON.stringify(currentAccountIdentity)||accountSwitching)return;
    if(!response.ok||!data.available){status.textContent=data.message||data.detail||'Broker derinlik verisi sağlamıyor.';return;}
    if(!Array.isArray(data.levels)||data.symbol!==symbol||!currentAccountIdentity||JSON.stringify(data.account)!==JSON.stringify(currentAccountIdentity.slice(0,2)))throw new Error('Derinlik hesabı veya sembolü uyuşmuyor.');
    const levels=data.levels.filter(row=>['BUY','SELL'].includes(row.side)&&Number.isFinite(row.price)&&row.price>=0&&Number.isFinite(row.volume)&&row.volume>=0);
    displayedDepth={symbol,generation,account,levels};
    const digits=Number.isInteger(data.digits)&&data.digits>=0&&data.digits<=12?data.digits:5;
    root.innerHTML=levels.map((row,index)=>`<button type="button" ${row.market||!row.price?'disabled':''} onclick="stageDepthOrder(${index})" class="w-full flex justify-between px-2 py-1 rounded hover:bg-gray-800 ${row.side==='BUY'?'text-emerald-400':'text-rose-400'}"><span>${safeText(row.side)}${row.market?' · Market':''}</span><span>${Number(row.price).toFixed(digits)}</span><span>${Number(row.volume)}</span></button>`).join('');
    status.textContent=`${symbol} · ${levels.length} seviye · ${new Date(data.sampled_at*1000).toLocaleTimeString()}`;
  }catch(err){if(revision===depthRequestRevision){displayedDepth=null;root.innerHTML='';status.textContent=err.message;}}
}
function stageDepthOrder(index) {
  const snapshot=displayedDepth,row=snapshot?.levels[index];
  if(!row||row.market||!(row.price>0)||snapshot.symbol!==currentSymbol||snapshot.generation!==accountSessionGeneration||snapshot.account!==JSON.stringify(currentAccountIdentity)||accountSwitching)return showToast('Derinlik değişti; veriyi yenileyin.','error');
  const type=document.getElementById('pending-type'),price=document.getElementById('pending-price');
  type.value=row.side+'_LIMIT';price.value=String(row.price);changePendingOptions();
  const panel=type.closest?.('details');if(panel)panel.open=true;
  price.focus?.();
  showToast('Bekleyen emir formu dolduruldu; emir gönderilmedi.','info');
}
function rememberPositions(rows) {
  managedPositions.clear();
  rows.forEach(p => managedPositions.set(p.ticket, p));
}
function showPositionEditor(ticket) {
  const p = managedPositions.get(ticket);
  if (!p) return showToast('Pozisyon güncel listede bulunamadı.', 'error');
  editorPosition = {...p,account:JSON.stringify(currentAccountIdentity),generation:accountSessionGeneration};
  document.getElementById('position-editor-title').textContent = `${p.symbol} ${p.type} · #${p.ticket} · ${p.volume} lot`;
  document.getElementById('edit-sl').value = p.sl || 0;
  document.getElementById('edit-tp').value = p.tp || 0;
  document.getElementById('partial-volume').value = '';
  for (const [id,value] of [['close-deviation',document.getElementById('order-deviation')?.value || '20'],['close-filling','AUTO'],['trailing-distance','0']]) {
    const node=document.getElementById(id);if(node) node.value=value;
  }
  const closeBy=document.getElementById('close-by-button');if(closeBy) closeBy.disabled=true;
  document.getElementById('position-editor-status').textContent = '';
  document.getElementById('position-editor').showModal();
  loadPositionManagement(editorPosition);
}
function editorIsCurrent(p) {
  return p && editorPosition === p && p.account === JSON.stringify(currentAccountIdentity)
    && p.generation === accountSessionGeneration && !accountSwitching;
}
async function loadPositionManagement(p) {
  try {
    const [capResponse,trailResponse]=await Promise.all([fetch(`/api/position/${p.ticket}/capabilities`),fetch('/api/position/trailing')]);
    if(!capResponse.ok||!trailResponse.ok) throw new Error('Pozisyon kuralları doğrulanamadı.');
    const [cap,trails]=await Promise.all([capResponse.json(),trailResponse.json()]);
    if(!editorIsCurrent(p)) return;
    const select=document.getElementById('close-by-ticket'),button=document.getElementById('close-by-button');
    if(select) {select.innerHTML=(cap.opposites||[]).map(row=>`<option value="${Number(row.ticket)}">#${Number(row.ticket)} · ${Number(row.volume)} lot</option>`).join('');select.disabled=!cap.close_by||!cap.opposites?.length;}
    if(button)button.disabled=!cap.close_by||!cap.opposites?.length||window.MT5Permissions?.can('closeByPosition')===false;
    const note=document.getElementById('close-by-note');if(note)note.textContent=cap.close_by?'Broker iki pozisyonun küçük hacmini karşılıklı kapatır; fark açık kalır.':'Bu hesap veya sembol Close By desteklemiyor.';
    const volume=document.getElementById('partial-volume');if(volume){volume.step=String(cap.volume_step);volume.min=String(cap.volume_min);volume.max=String(p.volume-cap.volume_min);}
    const filling=document.getElementById('close-filling');if(filling)for(const option of Array.from(filling.options||[]))option.disabled=option.value!=='AUTO'&&!cap.fillings.includes({FOK:0,IOC:1,RETURN:2}[option.value]);
    const trail=trails.find(row=>row.ticket===p.ticket);
    const distance=document.getElementById('trailing-distance');if(distance)distance.value=String(trail?.points||0);
    const status=document.getElementById('trailing-status');if(status)status.textContent=trail?`${trail.points} puan · ${{armed:'Etkin',paused:'Doğrulama bekleniyor',uncertain:'Sonuç belirsiz; yeniden etkinleştirmeden önce MT5 durumunu kontrol edin',rejected:'Broker reddetti'}[trail.status]||trail.status}${trail.message?' · '+trail.message:''}`:'İz süren stop kapalı.';
  }catch(err){if(editorIsCurrent(p))document.getElementById('position-editor-status').textContent=err.message;}
}
function closeExecutionSettings() {
  const deviation_points=Number(document.getElementById('close-deviation')?.value??20);
  const filling_policy=document.getElementById('close-filling')?.value||'AUTO';
  if(!Number.isInteger(deviation_points)||deviation_points<0||deviation_points>100000||!['AUTO','FOK','IOC','RETURN'].includes(filling_policy))return null;
  return {deviation_points,filling_policy};
}
async function closeEditorPosition() {
  const p=editorPosition,settings=closeExecutionSettings();
  if(!editorIsCurrent(p)||!settings)return showToast('Hesabı ve kapatma ayarlarını doğrulayın.','error');
  const result=await guardedAction('close:'+p.ticket,'/api/order/close',{ticket:p.ticket,...settings});
  if(result?.success&&!result.pending&&!result.partial&&!result.uncertain&&editorIsCurrent(p))document.getElementById('position-editor').close();
}
async function closeByPosition() {
  const p=editorPosition,position_by=Number(document.getElementById('close-by-ticket')?.value);
  if(!editorIsCurrent(p)||!Number.isSafeInteger(position_by)||position_by<=0||position_by===p.ticket||document.getElementById('close-by-button')?.disabled)return showToast('Geçerli karşıt pozisyon seçin.','error');
  const result=await guardedAction('close-by:'+p.ticket,'/api/position/close-by',{ticket:p.ticket,position_by});
  if(result?.success&&!result.pending&&!result.uncertain&&editorIsCurrent(p))document.getElementById('position-editor').close();
}
async function saveTrailingStop() {
  const p=editorPosition,distance_points=Number(document.getElementById('trailing-distance')?.value);
  if(!editorIsCurrent(p)||!Number.isInteger(distance_points)||distance_points<0||distance_points>10000000)return showToast('Geçerli iz süren stop mesafesi girin.','error');
  await guardedAction('trailing:'+p.ticket,'/api/position/trailing',{ticket:p.ticket,distance_points});
  if(editorIsCurrent(p))await loadPositionManagement(p);
}
async function guardedAction(key, endpoint, payload) {
  if (actionLocks.has(key) || accountSwitching) return null;
  if (accountVerificationKnown && !currentAccountIdentity) return showToast('İşlemden önce broker hesap bağlantısını doğrulayın.', 'error');
  payload = {...payload,...tradingAccountPayload()};
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
    const {data} = await tradeRequest(endpoint, {...payload,request_id:intent.request_id});
    if (data.request_id && !data.uncertain && !data.pending) localStorage.removeItem(storeKey);
    const message = data.uncertain ? 'Sonuç belirsiz; MT5 durumunu kontrol edin.' : data.pending ? 'Talep kabul edildi; broker sonucu bekleniyor.' : data.partial ? 'Talep kısmen gerçekleşti.' : data.success ? 'İşlem tamamlandı.' : (data.detail || data.error || 'İşlem tamamlanamadı.');
    showToast(message, data.uncertain ? 'error' : data.pending || data.partial ? 'warning' : data.success ? 'success' : 'error');
    const status = payload.ticket && editorPosition?.ticket === payload.ticket ? document.getElementById('position-editor-status') : null;
    if (status) status.textContent = message;
    fetchPositions(true);fetchPendingOrders();fetchAccount(true);
    return data;
  } catch (err) {
    const message = err.message || 'Sonuç belirsiz; MT5 üzerinden kontrol edin.';
    showToast(message, 'error');
    const status = payload.ticket && editorPosition?.ticket === payload.ticket ? document.getElementById('position-editor-status') : null;
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
  if (p.account !== JSON.stringify(currentAccountIdentity) || p.generation !== accountSessionGeneration) return showToast('Hesap değişti; pozisyonu yeniden açın.', 'error');
  const slValue = String(document.getElementById('edit-sl').value ?? '').trim(), tpValue = String(document.getElementById('edit-tp').value ?? '').trim();
  const sl = slValue === '' ? NaN : Number(slValue), tp = tpValue === '' ? NaN : Number(tpValue);
  if (![sl,tp].every(v=>Number.isFinite(v)&&v>=0)) return showToast('Geçerli SL/TP fiyatları girin.', 'error');
  const result = await guardedAction('stops:'+p.ticket, '/api/position/stops', {ticket:p.ticket,sl,tp,expected_sl:p.sl,expected_tp:p.tp,manual_mode:manualOrderMode()});
  if (result?.success && !result.pending && !result.partial && !result.uncertain && editorPosition?.ticket === p.ticket) {
    editorPosition = {...p,sl,tp};
  }
}
async function partialClosePosition() {
  const p=editorPosition;
  if (!p) return;
  if (p.account !== JSON.stringify(currentAccountIdentity) || p.generation !== accountSessionGeneration) return showToast('Hesap değişti; pozisyonu yeniden açın.', 'error');
  const volume=Number(document.getElementById('partial-volume').value);
  const settings=closeExecutionSettings();
  if(!settings)return showToast('Geçerli kapatma ayarları girin.','error');
  if (!Number.isFinite(volume)||volume<=0||volume>=p.volume) return showToast('Açık lottan küçük, pozitif bir miktar girin.', 'error');
  const result=await guardedAction('partial:'+p.ticket,'/api/position/partial-close',{ticket:p.ticket,volume,...settings});
  if (result?.success && !result.pending && !result.partial && !result.uncertain && editorPosition?.ticket === p.ticket) document.getElementById('position-editor').close();
}
function pendingOrderOptions() {
  const kind = document.getElementById('pending-type').value;
  const value = document.getElementById('pending-time-type')?.value;
  const time_type = ['GTC','DAY','SPECIFIED','SPECIFIED_DAY'].includes(value) ? value : 'GTC';
  const filling=document.getElementById('pending-filling')?.value;
  const options = {time_type,expiration:0,filling_policy:filling==='BOC'?'BOC':'RETURN'};
  if (kind.endsWith('STOP_LIMIT')) options.stoplimit_price = Number(document.getElementById('pending-stoplimit')?.value);
  if (['SPECIFIED','SPECIFIED_DAY'].includes(time_type)) {
    const raw = document.getElementById('pending-expiration')?.value;
    options.expiration = Math.floor(new Date(raw || NaN).getTime()/1000);
  }
  return options;
}
function changePendingOptions() {
  const kind = document.getElementById('pending-type')?.value || '';
  const selectedTime = document.getElementById('pending-time-type')?.value || 'GTC';
  const limit = document.getElementById('pending-stoplimit-field');
  const expiry = document.getElementById('pending-expiration-field');
  if (limit) limit.hidden = !kind.endsWith('STOP_LIMIT');
  if (expiry) expiry.hidden = !['SPECIFIED','SPECIFIED_DAY'].includes(selectedTime);
  const expiryInput=document.getElementById('pending-expiration');
  if (expiryInput) expiryInput.type=selectedTime==='SPECIFIED_DAY'?'date':'datetime-local';
  const filling=document.getElementById('pending-filling'),spec=window.MT5Markets?.getSymbol(currentSymbol);
  if(filling?.options)for(const option of filling.options)option.disabled=option.value==='BOC'&&(!kind.endsWith('LIMIT')||!spec||(spec.filling_mode&4)===0);
}

const managedPendingOrders = new Map();
let editorPendingOrder = null;
function pendingState(order) {
  return [Number(order.ticket),order.symbol,{BUY_LIMIT:2,SELL_LIMIT:3,BUY_STOP:4,SELL_STOP:5,BUY_STOP_LIMIT:6,SELL_STOP_LIMIT:7}[order.type],
    Number(order.magic),Number(order.volume),Number(order.price),Number(order.sl),Number(order.tp),Number(order.stoplimit||0),Number(order.type_time||0),Number(order.expiration||0)];
}
function localDateInput(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch*1000);
  return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function showPendingOrderEditor(ticket) {
  const order=managedPendingOrders.get(ticket);
  if (!order || accountSwitching || !currentAccountIdentity) return showToast('Bekleyen emir veya hesap doğrulanamadı.', 'error');
  editorPendingOrder={...order,account:JSON.stringify(currentAccountIdentity),generation:accountSessionGeneration};
  document.getElementById('pending-editor-title').textContent=`${order.symbol} ${order.type} · #${order.ticket}`;
  for(const [id,value] of [['price',order.price],['sl',order.sl],['tp',order.tp],['stoplimit',order.stoplimit||0]]) document.getElementById('pending-edit-'+id).value=String(value);
  document.getElementById('pending-edit-limit-field').hidden=!order.type.endsWith('STOP_LIMIT');
  document.getElementById('pending-edit-time').value=['GTC','DAY','SPECIFIED','SPECIFIED_DAY'][order.type_time||0];
  changePendingEditorTime();
  document.getElementById('pending-edit-expiration').value=order.type_time===3 ? localDateInput(order.expiration).slice(0,10) : localDateInput(order.expiration);
  document.getElementById('pending-editor').showModal();
}
function changePendingEditorTime() {
  const type=document.getElementById('pending-edit-time').value;
  document.getElementById('pending-edit-expiration-field').hidden=!['SPECIFIED','SPECIFIED_DAY'].includes(type);
  document.getElementById('pending-edit-expiration').type=type==='SPECIFIED_DAY'?'date':'datetime-local';
}
async function savePendingOrder() {
  const order=editorPendingOrder;
  if(!order || accountSwitching || order.account!==JSON.stringify(currentAccountIdentity) || order.generation!==accountSessionGeneration)
    return showToast('Hesap değişti; bekleyen emri yeniden açın.', 'error');
  const value=id=>Number(document.getElementById('pending-edit-'+id).value);
  const time_type=document.getElementById('pending-edit-time').value;
  const expiration=['SPECIFIED','SPECIFIED_DAY'].includes(time_type) ? Math.floor(new Date(document.getElementById('pending-edit-expiration').value).getTime()/1000) : 0;
  const payload={ticket:order.ticket,entry_price:value('price'),sl_price:value('sl'),tp_price:value('tp'),time_type,expiration,
    manual_mode:manualOrderMode(),expected_state:pendingState(order)};
  if(order.type.endsWith('STOP_LIMIT')) payload.stoplimit_price=value('stoplimit');
  if(!Number.isFinite(payload.entry_price)||payload.entry_price<=0||![payload.sl_price,payload.tp_price].every(n=>Number.isFinite(n)&&n>=0)
      ||!Number.isInteger(expiration)||(payload.stoplimit_price!==undefined&&!(payload.stoplimit_price>0)))
    return showToast('Emir fiyatlarını ve son geçerlilik zamanını kontrol edin.', 'error');
  const result=await guardedAction('pending-modify:'+order.ticket,'/api/order/modify',payload);
  if(result?.success&&!result.uncertain&&!result.pending&&editorPendingOrder===order)document.getElementById('pending-editor').close();
}

let pendingOrderPreflight = false;
function pendingOrderTicket() {
  const pending_type = document.getElementById('pending-type').value;
  return {symbol:currentSymbol,pending_type,order_type:pending_type.startsWith('BUY')?'BUY':'SELL',
    volume:Number(document.getElementById('pending-volume').value),
    entry_price:Number(document.getElementById('pending-price').value), ...readOrderProtection(true), ...pendingOrderOptions()};
}
async function submitPendingOrder() {
  if (accountSwitching || pendingOrderPreflight) return;
  const protection = readOrderProtection(true), ticket = pendingOrderTicket();
  if (!protection || ![ticket.volume,ticket.entry_price].every(v=>Number.isFinite(v)&&v>0)
      || (ticket.stoplimit_price !== undefined && !(Number.isFinite(ticket.stoplimit_price)&&ticket.stoplimit_price>0))
      || !Number.isInteger(ticket.expiration) || ticket.expiration < 0)
    return showToast('Fiyat, lot ve SL/TP alanlarını kontrol edin.', 'error');
  const snapshot = () => JSON.stringify([pendingOrderTicket(),tradingAccountPayload(),accountSessionGeneration,window.MT5Markets?.version?.()]);
  const selected = snapshot();
  pendingOrderPreflight = true;
  try {
    if (!await verifyChartOrderSymbol(ticket.order_type) || selected !== snapshot()
        || window.MT5Markets?.isSelectionVerified(currentSymbol, ticket.order_type) === false)
      return showToast('Hesap, sembol veya emir bilgileri değişti ya da doğrulanamadı. Emir gönderilmedi.', 'error');
    if (protection.manual_mode !== 'MT5' && currentAccountType==='REAL' && !(protection.sl_price ?? protection.sl_points))
      return showToast('Uygulama kuralı: gerçek hesapta yeni bekleyen emir için Stop Loss zorunlu.', 'error');
    const quoteIssue = quoteOrderIssue();
    if (quoteIssue) return showToast(quoteIssue + ' Bekleyen emir gönderilmedi.', 'warning');
    await guardedAction('pending:'+ticket.symbol,'/api/order/pending', ticket);
  } finally { pendingOrderPreflight = false; }
}
async function cancelPendingOrder(ticket) {
  await guardedAction('cancel:'+ticket,'/api/order/cancel',{ticket});
}
function renderPendingOrders(orders) {
  const root=document.getElementById('pending-orders-list');
  if (!root) return;
  if (accountSwitching) return;
  if (!Array.isArray(orders)) throw new Error('Bekleyen emir listesi doğrulanamadı.');
  pendingRenderRevision++;
  managedPendingOrders.clear();
  orders.forEach(order=>managedPendingOrders.set(Number(order.ticket),{...order}));
  root.innerHTML=orders.length ? orders.map(o=>`<div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800 py-2"><span><strong>${safeText(o.symbol)}</strong> ${safeText(o.type)}<br><span class="text-gray-400">#${Number(o.ticket)} · ${Number(o.volume)} lot @ ${Number(o.price)} · SL ${Number(o.sl)||0} · TP ${Number(o.tp)||0}${o.stoplimit ? ` · Limit ${Number(o.stoplimit)}` : ''} · ${['GTC','DAY','SPECIFIED','SPECIFIED_DAY'][o.type_time||0]} · ${safeText(o.filling_policy||'RETURN')}${o.expiration ? ` · ${safeText(new Date(o.expiration*1000).toLocaleString())}` : ''}</span></span><button onclick="showPendingOrderEditor(${Number(o.ticket)})" class="px-2 py-1 rounded bg-cyan-500/10 text-cyan-300">Değiştir</button><button onclick="cancelPendingOrder(${Number(o.ticket)})" class="px-2 py-1 rounded bg-rose-500/10 text-rose-300">İptal et</button></div>`).join('') : '<p class="text-gray-500 py-2">Bekleyen emir yok.</p>';
}
let fetchingPending=false;
let pendingRenderRevision=0;
async function fetchPendingOrders() {
  if (fetchingPending) return;
  fetchingPending=true;
  const generation=accountSessionGeneration, revision=pendingRenderRevision;
  try {
    const response=await fetch('/api/orders');
    if (!response.ok) throw new Error('Veri alınamadı');
    const orders=await response.json();
    if (generation===accountSessionGeneration && revision===pendingRenderRevision) renderPendingOrders(orders);
  } catch (_) {
    if (generation!==accountSessionGeneration || revision!==pendingRenderRevision) return;
    const root=document.getElementById('pending-orders-list');
    if (root) root.textContent='Bekleyen emir bilgisi doğrulanamadı.';
  } finally {fetchingPending=false;}
}
function liveFeedHealthy() {return lastLiveMessage>0 && Date.now()-lastLiveMessage<2500;}
function updateQuoteStatus() {
  updateOrderButtons();
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
  const generation=accountSessionGeneration;
  const source=new EventSource('/api/live?symbol='+encodeURIComponent(symbol));
  liveSource=source;
  source.onmessage=event=>{
    if (source!==liveSource || generation!==accountSessionGeneration || accountSwitching) return;
    try {
      const data=JSON.parse(event.data);
      if (data.error) throw new Error(data.error);
      lastLiveMessage=Date.now();
      renderAccountData(data.account);
      // Account identity may change after a terminal reconnect. Reject this
      // snapshot and open a feed for the newly observed account generation.
      if (generation!==accountSessionGeneration) { startLiveFeed(); return; }
      renderPositionsData(data.positions);
      renderPendingOrders(data.orders);
      if (data.prices[symbol]) renderPriceData(data.prices[symbol]);
      else clearSymbolPrices();
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
    if (!(result.found || result.reconciled) || result.uncertain || result.pending || tradeActionBusy() || localStorage.getItem(key) !== stored) continue;
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
