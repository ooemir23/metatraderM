// MevO: yalnız danışman. Emir açma/otopilot yoktur; /api/mevo/* uçlarını kullanır.
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Modern, ortalanmış onay penceresi (tarayıcının confirm() yerine). Promise<boolean> döner.
function askConfirm({title, icon = 'ph-question', rows = [], warnings = [], text = '', okText = 'Tamam', cancelText = 'İptal', tone = 'warn'}) {
  return new Promise(resolve => {
    const dlg = $('mevo-modal'), body = $('mm-body');
    const iconTone = tone === 'buy' ? 'text-emerald-400' : tone === 'sell' ? 'text-rose-400' : 'text-violet-300';
    body.innerHTML = `<div class="flex items-center gap-3 mb-3"><i class="ph-bold ${esc(icon)} text-2xl ${iconTone}"></i><h3 id="mm-title" class="text-base font-bold text-white">${esc(title)}</h3></div>
      ${text ? `<p class="text-xs text-gray-300 leading-relaxed whitespace-pre-line mb-2">${esc(text)}</p>` : ''}
      ${rows.length ? `<div>${rows.map(([k, v]) => `<div class="mm-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('')}</div>` : ''}
      ${warnings.length ? `<div class="mm-warn"><b><i class="ph-bold ph-warning"></i> Uyarılar</b>${warnings.map(w => `<p>${esc(w)}</p>`).join('')}</div>` : ''}
      <div class="flex justify-end gap-2 mt-5"><button type="button" class="mm-btn" id="mm-cancel">${esc(cancelText)}</button><button type="button" class="mm-btn mm-ok" data-tone="${esc(tone)}" id="mm-ok">${esc(okText)}</button></div>`;
    let done = false;
    const finish = v => { if (done) return; done = true; dlg.close(); resolve(v); };
    $('mm-cancel').onclick = () => finish(false);
    $('mm-ok').onclick = () => finish(true);
    dlg.oncancel = e => { e.preventDefault(); finish(false); };
    dlg.onclick = e => { if (e.target === dlg) finish(false); };
    dlg.showModal();
    $('mm-cancel').focus();
  });
}

function mevoAlert(text, tone) {
  const el = $('mevo-alert');
  if (!text) { el.classList.add('hidden'); return; }
  el.className = 'text-xs rounded-lg border px-3 py-2 ' + (tone === 'ok'
    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-rose-500/40 bg-rose-500/10 text-rose-300');
  el.textContent = text;
}

async function mevoApi(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'İstek başarısız (' + res.status + ')');
  return data;
}

function list(title, items) {
  items = (items || []).filter(Boolean);
  if (!items.length) return '';
  return `<div><div class="font-semibold text-white mb-1">${esc(title)}</div><ul class="list-disc pl-5 space-y-0.5">${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul></div>`;
}

function causeTable(c) {
  const rows = (c || []).map(x => `<tr><td class="pr-3">${esc(x.label)}</td><td class="pr-3">${x.count} zarar</td><td class="text-rose-400">${esc(x.loss)}</td></tr>`).join('');
  return rows ? `<table class="text-[11px] text-gray-300 mt-1">${rows}</table>` : '<p class="text-gray-500">Zarar yok ya da sebep bulunamadı.</p>';
}

function renderBotComparison(c, a) {
  if (!c) return '<p class="text-gray-500">Zarar analizi yapılamadı.</p>';
  const m = c.manual || {};
  return `<div class="border border-rose-500/30 rounded-xl p-3 bg-rose-500/5 space-y-3">
    <div class="font-semibold text-white">Zarar analizi: hangi durumda zarar edilmiş?</div>
    <p class="text-gray-400">Sebepler, kârlı manuel işlemlerinin (saat, yön, lot, süre, SL) referansıyla karşılaştırılarak koddan hesaplandı. Bir zarar birden fazla sebebe sayılabilir; bunlar bağlantıdır, kesin neden kanıtı değildir.</p>
    <div><b class="text-white">Manuel (orijinal MT5):</b> ${m.count || 0} işlem · ${m.wins || 0} kârlı / ${m.losses || 0} zararlı · net ${esc(m.net)} · zarar toplamı ${esc(m.loss_total)}
      ${causeTable(m.causes)}${list('MevO yorumu', a && a.manual_loss_reasons)}</div>
    <div><b class="text-white">Bot:</b> ${c.bot_count} işlem · ${c.bot_wins} kârlı / ${c.bot_losses} zararlı · net ${esc(c.bot_net)} · zarar toplamı ${esc(c.bot_loss_total)}
      ${c.bot_count ? causeTable(c.causes) : '<p class="text-gray-500">Bot geçmişi yok.</p>'}${list('MevO yorumu', a && a.bot_loss_reasons)}</div></div>`;
}

let mevoProfile = null, lastOpps = null;
function renderProfile(p) {
  mevoProfile = p;
  const box = $('mevo-profile');
  const hint = $('mevo-profile-hint');
  if (!p) { box.innerHTML = '<p class="text-gray-500">Henüz öğrenilmedi.</p>'; if (hint) hint.textContent = 'Henüz öğrenilmedi'; return; }
  if (hint) hint.textContent = `${p.stats.count} işlem · %${p.stats.win_rate} kazanç`;
  const a = p.analysis || {}, s = p.stats || {};
  const syms = Object.entries(s.symbols || {}).map(([k, v]) =>
    `<tr><td class="pr-3 font-semibold text-white">${esc(k)}</td><td class="pr-3">${v.count} işlem</td><td class="pr-3">%${v.win_rate} kazanç</td><td class="pr-3">${v.buy} AL / ${v.sell} SAT</td><td class="pr-3">lot ~${v.median_lot}</td><td>${v.median_hold_min} dk</td></tr>`).join('');
  box.innerHTML = `
    <p class="text-gray-500">Son güncelleme: ${esc(p.learned_at)} · ${s.count} elle açılmış kapalı işlem · net ${esc(s.net)} · %${esc(s.win_rate)} kazanç oranı</p>
    <p>${esc(a.summary)}</p>
    ${syms ? `<div class="overflow-x-auto"><table class="text-[11px] text-gray-300">${syms}</table></div>` : ''}
    ${list('Mantık / kurallar', a.rules)}${list('Risk alışkanlıkları', a.risk_habits)}${list('Verimli saatler (broker saati)', a.best_hours)}
    ${renderBotComparison(p.comparison, a)}
    ${list('Zayıf noktalar', a.weak_points)}${list('Dikkat', a.caveats)}`;
  const names = Object.keys(s.symbols || {});
  if (names.length && !symbolsTouched) { symbols = names.slice(0, MAX_SYMBOLS); renderChips(); }
}

function defaultLot(sym) {
  const v = mevoProfile && mevoProfile.stats && mevoProfile.stats.symbols && mevoProfile.stats.symbols[sym];
  return v ? v.median_lot : 0.01;
}

function openForm(i, n) {
  const field = (id, label, value, step) => `<label class="block text-[11px] text-gray-400">${label}<input id="${id}-${n}" type="number" step="${step}" min="0" value="${esc(value)}" class="mt-0.5 w-full bg-[#0b0e14] border border-gray-700 rounded-lg px-2 py-1 text-xs text-white font-mono"></label>`;
  return `<div class="mt-3"><button type="button" id="opp-toggle-${n}" onclick="$('opp-form-${n}').classList.toggle('hidden')" class="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-white border border-gray-700 font-semibold text-xs">Pozisyon olarak aç…</button>
    <div id="opp-form-${n}" class="hidden mt-2 p-2 rounded-lg border border-gray-700 bg-[#0b0e14]">
      <p class="text-[11px] text-gray-400 mb-2">Düzenleyip açabilirsin. ${i.action === 'BUY' ? 'AL (Ask)' : 'SAT (Bid)'} piyasa emri; SL/TP fiyat olarak gönderilir (0 = yok). Lot varsayılanı manuel işlemlerinin ortancasıdır.</p>
      <div class="grid grid-cols-3 gap-2">${field('opp-lot', 'Lot', defaultLot(i.symbol), '0.01')}${field('opp-sl', 'SL', i.sl, 'any')}${field('opp-tp', 'TP', i.tp, 'any')}</div>
      <button type="button" id="opp-open-${n}" onclick="openOpportunity(${n})" class="mt-2 px-3 py-1.5 rounded-lg ${i.action === 'BUY' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-rose-600 hover:bg-rose-500'} text-white font-bold text-xs">${i.action === 'BUY' ? 'AL' : 'SAT'} pozisyonu aç</button>
      <p id="opp-msg-${n}" class="mt-2 text-[11px]"></p>
    </div></div>`;
}

function goToOpenPositions(event) {
  event.preventDefault();
  if (window.parent !== window && window.parent.MainView) { window.parent.MainView.set('trades'); return; }   // gömülüyken terminalin İşlemler ekranı
  try { localStorage.setItem('hma.main.view.v1', 'trades'); } catch (_) {}   // terminalde "İşlemler" ekranı açılsın
  window.location.href = '/';
}

function newRequestId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return 'mevo-' + Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
}

function limitRaise(d, lot) {
  if (d.reason_code === 'order_lot_limit' && d.max_order_lots > 0) {
    return {body: {max_order_lots: Math.ceil(lot * 100) / 100}, text: `Tek emir sınırı: ${d.max_order_lots} → ${Math.ceil(lot * 100) / 100} lot`};
  }
  if (d.reason_code === 'exposure_limit' && d.max_total_lots > 0 && d.max_open_orders > 0) {
    const body = {}, parts = [];
    const needLots = Math.ceil((d.committed_lots + lot) * 100) / 100, needCount = d.open_count + 1;
    if (needLots > d.max_total_lots) { body.max_total_lots = needLots; parts.push(`Toplam lot sınırı: ${d.max_total_lots} → ${needLots}`); }
    if (needCount > d.max_open_orders) { body.max_open_orders = needCount; parts.push(`Açık işlem sınırı: ${d.max_open_orders} → ${needCount}`); }
    if (Object.keys(body).length) return {body, text: parts.join('\n')};
  }
  return null;
}

async function openOpportunity(n) {
  const item = lastOpps && lastOpps.items[n];
  const msg = $('opp-msg-' + n), btn = $('opp-open-' + n);
  const say = (t, ok) => { msg.textContent = t; msg.className = 'mt-2 text-[11px] ' + (ok ? 'text-emerald-300' : 'text-rose-300'); };
  if (!item || item.action === 'WAIT') return;
  const lot = Number($('opp-lot-' + n).value), sl = Number($('opp-sl-' + n).value), tp = Number($('opp-tp-' + n).value);
  if (!(lot > 0) || !(sl >= 0) || !(tp >= 0)) return say('Lot, SL ve TP geçerli sayı olmalı.', false);
  const identity = window.currentAccountIdentity;
  if (!identity) return say('Hesap doğrulanamadı; terminalde hesabı kontrol et.', false);
  const side = item.action === 'BUY' ? 'AL' : 'SAT';
  const ok = await askConfirm({title: `${item.symbol} ${side} piyasa emri`, icon: item.action === 'BUY' ? 'ph-trend-up' : 'ph-trend-down',
    tone: item.action === 'BUY' ? 'buy' : 'sell', okText: `${side} pozisyonu aç`, cancelText: 'Vazgeç',
    rows: [['Yön', side], ['Lot', String(lot)], ['SL', sl ? String(sl) : 'yok'], ['TP', tp ? String(tp) : 'yok'], ['Hesap', `${identity[2]} #${identity[0]}`]],
    warnings: item.warnings || []});
  if (!ok) return;
  btn.disabled = true; say('Emir gönderiliyor…', true);
  try {
    // Uygulama sınırı (lot/işlem) doluysa: yeni sınırı göstererek onay iste; onaylanırsa sınırı artırıp emri yeniden gönder.
    for (let attempt = 0; attempt < 4; attempt++) {
      const res = await fetch('/api/order/open', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        request_id: newRequestId(), symbol: item.symbol, order_type: item.action, volume: lot, sl_price: sl, tp_price: tp,
        comment: 'MevO', expected_account: identity})});
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        msg.innerHTML = `<a href="/" class="underline decoration-dotted text-emerald-300" onclick="goToOpenPositions(event)">Açıldı: #${esc(data.ticket)} @ ${esc(data.price)} · açık işlemleri gör →</a>`;
        msg.className = 'mt-2 text-[11px]';
        return;
      }
      if (data.uncertain || data.pending) return say('Emir sonucu belirsiz. Tekrar basma; terminalde pozisyonları kontrol et.', false);
      const raise = limitRaise(data, lot);
      if (!raise) return say(typeof data.detail === 'string' ? data.detail : (data.error || 'Emir açılamadı.'), false);
      if (!await askConfirm({title: 'Sınırı artırıp işlemi açayım mı?', icon: 'ph-shield-warning', okText: 'Sınırı artır ve aç', cancelText: 'Vazgeç',
        text: data.error + '\n\nBu, uygulamanın kayıtlı sınırlarını kalıcı olarak değiştirir (Risk ayarlarından geri alabilirsin).',
        rows: raise.text.split('\n').map(l => { const i = l.indexOf(':'); return [l.slice(0, i), l.slice(i + 1).trim()]; })})) {
        return say('Sınır artırılmadı; emir açılmadı.', false);
      }
      say('Sınır artırılıyor…', true);
      const lr = await fetch('/api/risk/lot-limits', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(raise.body)});
      const ld = await lr.json().catch(() => ({}));
      if (!lr.ok) return say('Sınır artırılamadı: ' + (typeof ld.detail === 'string' ? ld.detail : 'yetki ya da doğrulama gerekli.'), false);
      say('Sınır artırıldı; emir yeniden gönderiliyor…', true);
    }
    say('Sınır birkaç kez artırıldı ama emir hâlâ açılamadı; terminalde Risk ayarlarını kontrol et.', false);
  } catch (err) {
    say('İstek sonucu bilinmiyor; tekrar basma, terminalde pozisyonları kontrol et.', false);
  } finally { btn.disabled = false; }
}

function renderOpps(o) {
  lastOpps = o;
  const box = $('mevo-opps');
  if (!o || !o.items || !o.items.length) { box.innerHTML = '<p class="text-xs text-gray-500 sm:col-span-2">Henüz fırsat getirilmedi.</p>'; return; }
  box.innerHTML = o.items.map((i, n) => {
    const buy = i.action === 'BUY', wait = i.action === 'WAIT';
    const tone = buy ? 'border-emerald-500/40' : wait ? 'border-gray-700' : 'border-rose-500/40';
    const badge = buy ? 'bg-emerald-500/15 text-emerald-300' : wait ? 'bg-gray-700/50 text-gray-300' : 'bg-rose-500/15 text-rose-300';
    const label = buy ? 'AL' : wait ? 'BEKLE' : 'SAT';
    const lv = wait ? '' : `<div class="font-mono text-[11px] grid grid-cols-4 gap-1 mt-3"><span class="text-gray-500">Giriş<br><b class="text-white">${esc(i.entry)}</b></span><span class="text-gray-500">SL<br><b class="text-rose-400">${esc(i.sl)}</b></span><span class="text-gray-500">TP<br><b class="text-emerald-400">${esc(i.tp)}</b></span><span class="text-gray-500">R:R<br><b class="text-white">${esc(i.rr)}</b></span></div>`;
    const warns = (i.warnings || []);
    const warnBox = warns.length ? `<details class="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-300"><summary class="cursor-pointer"><i class="ph-bold ph-warning"></i> ${warns.length} uyarı</summary>${warns.map(w => `<p class="mt-1">${esc(w)}</p>`).join('')}</details>` : '';
    return `<div class="bg-[#0f1218] border ${tone} rounded-xl p-3 text-xs"><div class="flex justify-between items-center"><b class="text-white text-sm">${esc(i.symbol)}</b><span class="px-2 py-0.5 rounded-md font-bold ${badge}">${label}</span></div>${wait ? '' : `<div class="text-gray-500 mt-1">model güveni %${esc(i.confidence)}</div>`}${lv}<p class="mt-3 text-gray-300">${esc(i.reason)}</p>${warnBox}${wait ? '' : openForm(i, n)}</div>`;
  }).join('') + `<p class="sm:col-span-2 text-[11px] text-gray-500">${esc(o.created_at)} · ${esc(o.timeframe)} · ${o.used_model === false ? 'önbellekten, token harcanmadı · ' : ''}kapanmış mumlara göre. Yatırım tavsiyesi değildir; emri sen onaylarsın.</p>`;
}

// ---- MevO'nun kendi işlemlerinin sonucu: neden kazandı / kaybetti + alınan önlemler ----
function money(v) { const n = Number(v); return (n > 0 ? '+' : '') + n.toFixed(2); }
function renderReview(r) {
  const box = $('mevo-review');
  if (!r || !r.count) {
    box.innerHTML = '<p class="text-gray-500">MevO\'dan açılmış kapalı işlem yok. Fırsat kartından “Pozisyon olarak aç” ile ya da botta “MevO\'yu kullan” ile açılan işlemler kapanınca burada sebepleriyle analiz edilir; art arda zarar eden sembol/yönde MevO yeni fırsatta uyarır ve bot girmez.</p>';
    return;
  }
  const tile = (k, v, cls = 'text-white') => `<div class="rounded-xl bg-[#0f1218] border border-gray-800 px-3 py-2"><div class="text-[11px] text-gray-500">${k}</div><div class="text-base font-bold ${cls}">${v}</div></div>`;
  const blocks = (r.blocks || []).map(b => `<p class="rounded-lg border border-rose-500/40 bg-rose-500/10 text-rose-200 px-3 py-2"><i class="ph-bold ph-shield-warning"></i> <b>Önlem:</b> ${esc(b.text)} Bot bu durumda girmez.</p>`).join('');
  const rows = (r.trades || []).map(t => {
    const win = t.net > 0;
    return `<div class="rounded-lg border ${win ? 'border-emerald-500/25' : 'border-rose-500/25'} bg-[#0f1218] px-3 py-2">
      <div class="flex flex-wrap items-center justify-between gap-2"><span><b class="text-white">${esc(t.symbol)}</b> <span class="text-gray-400">${t.side === 'BUY' ? 'AL' : 'SAT'} · ${esc(t.volume)} lot · ${esc(t.hold_min)} dk${t.rr ? ' · R:R ' + esc(t.rr) : ''}</span></span>
        <b class="${win ? 'text-emerald-400' : 'text-rose-400'}">${money(t.net)}</b></div>
      <p class="mt-1 text-gray-400">${win ? 'Neden kazandı' : 'Neden kaybetti'}: ${(t.why || []).map(esc).join(' · ')}</p></div>`;
  }).join('');
  const groups = (r.groups || []).map(g => `<span class="inline-block mr-3">${esc(g.symbol)} ${g.side === 'BUY' ? 'AL' : 'SAT'}: ${g.wins}/${g.count} · <b class="${g.net >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${money(g.net)}</b></span>`).join('');
  box.innerHTML = `<div class="grid grid-cols-2 sm:grid-cols-4 gap-2">${tile('İşlem', r.count)}${tile('Kazanç oranı', '%' + esc(r.win_rate))}${tile('Net', money(r.net), r.net >= 0 ? 'text-emerald-400' : 'text-rose-400')}${tile('Zarar toplamı', esc(r.loss_total), 'text-rose-400')}</div>
    ${blocks}
    ${groups ? `<div class="text-gray-400">${groups}</div>` : ''}
    ${(r.causes || []).length ? `<div><div class="font-semibold text-white mb-1">Zararların ortak sebepleri</div>${causeTable(r.causes)}</div>` : ''}
    <div class="space-y-2">${rows}</div>
    <p class="text-[11px] text-gray-500">Sebepler kârlı manuel işlemlerinin saat/yön/lot/süre/SL referansıyla ve kapanış nedeniyle (SL/TP/elle) koddan karşılaştırılır; bağlantıdır, kesin neden değildir.</p>`;
}

async function loadReview() {
  try { renderReview(await mevoApi('/api/mevo/review')); }
  catch (e) { $('mevo-review').innerHTML = `<p class="text-gray-500">Sonuçlar okunamadı: ${esc(e.message)}</p>`; }
}
function mevoReview() { return busy('mevo-review-btn', loadReview); }

function renderStatus(st) {
  renderProfile(st.profile); renderOpps(st.opportunities);
  window.mevoLastUsedModel = st.opportunities ? st.opportunities.used_model : undefined;
  $('mevo-usage').textContent = `Bugün ${st.usage_today.calls}/${st.daily_call_limit} istek · ${st.usage_today.tokens} token`;
  $('mevo-strip-key').innerHTML = st.configured ? '<span class="text-emerald-400">●</span> DeepSeek bağlı' : '<span class="text-rose-400">●</span> API anahtarı yok';
  $('mevo-strip-learn').textContent = st.profile ? 'Öğrenildi: ' + st.profile.learned_at.replace('T', ' ') : 'Henüz öğrenilmedi';
  const src = {panel: 'panelden kaydedilmiş anahtar kullanılıyor', env: 'sunucu ortam değişkenindeki anahtar kullanılıyor', explicit: 'anahtar tanımlı'}[st.key_source];
  $('mevo-key-state').textContent = src ? 'Durum: ' + src + '. Yeni anahtar girersen onun yerine geçer.' : 'Anahtar tanımlı değil; buraya gir.';
  if (!st.configured) mevoAlert('DeepSeek API anahtarı tanımlı değil: sağ üstteki çark simgesinden gir.', 'err');
}

async function busy(btnId, fn) {
  const b = $(btnId); b.disabled = true; b.classList.add('opacity-60');
  mevoAlert('');
  try { await fn(); } catch (e) { mevoAlert(e.message, 'err'); }
  finally { b.disabled = false; b.classList.remove('opacity-60'); }
}

function openKeySettings() {
  const d = $('mevo-key-modal');
  if (!d.open) d.showModal();
  $('mevo-key').focus();
}

function mevoSaveKey() {
  return busy('mevo-key-btn', async () => {
    const key = $('mevo-key').value.trim();
    if (!key) throw new Error('Anahtarı yaz.');
    const st = await mevoApi('/api/mevo/key', {api_key: key});
    $('mevo-key').value = '';
    renderStatus(st); $('mevo-key-modal').close(); mevoAlert('Anahtar doğrulandı ve kaydedildi.', 'ok');
  });
}

function mevoLearn() {
  return busy('mevo-learn-btn', async () => { await mevoApi('/api/mevo/learn', {}); renderStatus(await mevoApi('/api/mevo/status')); mevoAlert('Manuel işlemlerin incelendi.', 'ok'); });
}

function mevoOpportunities() {
  return busy('mevo-opp-btn', async () => {
    if (!symbols.length) throw new Error('Önce en az bir sembol ekle.');
    if (!mevoProfile) await mevoApi('/api/mevo/learn', {});      // tek düğme: gerekirse önce manuel işlemlerini öğrenir
    await mevoApi('/api/mevo/opportunities', {symbols, timeframe: $('mevo-tf').value});
    renderStatus(await mevoApi('/api/mevo/status'));
    loadReview();
  });
}

// ---- semboller: uygulamadaki ürün seçiciyle aynı (MT5Markets) ----
const MAX_SYMBOLS = 6;
let symbols = [];
try { symbols = JSON.parse(localStorage.getItem('mevoSymbols') || '[]').filter(x => typeof x === 'string').slice(0, MAX_SYMBOLS); } catch (_) {}
let symbolsTouched = symbols.length > 0;
function saveSymbols() { try { localStorage.setItem('mevoSymbols', JSON.stringify(symbols)); } catch (_) {} }
function renderChips() {
  $('mevo-chips').innerHTML = symbols.map(sym => `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-[#0f1218] border border-gray-700 text-xs font-mono text-white">${esc(sym)}<button type="button" aria-label="${esc(sym)} kaldır" onclick="removeSymbol('${esc(sym)}')" class="text-gray-400 hover:text-rose-400">×</button></span>`).join('')
    || '<span class="text-[11px] text-gray-500">Sembol seçilmedi.</span>';
  $('market-open').disabled = symbols.length >= MAX_SYMBOLS || $('market-open').dataset.noAccount === '1';
}
function addSymbol(name) {
  symbolsTouched = true;
  if (!symbols.includes(name) && symbols.length < MAX_SYMBOLS) symbols.push(name);
  saveSymbols(); renderChips();
}
function removeSymbol(name) { symbols = symbols.filter(s => s !== name); symbolsTouched = true; saveSymbols(); renderChips(); }
async function syncAccount() {
  try {
    const res = await fetch('/api/account');
    const d = await res.json();
    const type = d.connected && !d.account_mismatch && ['DEMO', 'REAL'].includes(d.account_type) ? d.account_type : null;
    window.currentAccountIdentity = type && Number.isSafeInteger(d.login) && typeof d.server === 'string' ? [d.login, d.server, type] : null;
  } catch (_) { window.currentAccountIdentity = null; }
  $('market-open').dataset.noAccount = window.currentAccountIdentity ? '0' : '1';
  window.MT5Markets?.setAccount(window.currentAccountIdentity, 1);
  renderChips();
}
window.MT5Markets?.init({onSelect: addSymbol});
renderChips();
syncAccount(); setInterval(syncAccount, 15000);
mevoApi('/api/mevo/status').then(renderStatus).catch(e => mevoAlert(e.message, 'err'));
loadReview();
