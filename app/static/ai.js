// MevO: yalnız danışman. Emir açma/otopilot yoktur; /api/mevo/* uçlarını kullanır.
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

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

function renderProfile(p) {
  const box = $('mevo-profile');
  if (!p) { box.innerHTML = '<p class="text-gray-500">Henüz öğrenilmedi.</p>'; return; }
  const a = p.analysis || {}, s = p.stats || {};
  const syms = Object.entries(s.symbols || {}).map(([k, v]) =>
    `<tr><td class="pr-3 font-semibold text-white">${esc(k)}</td><td class="pr-3">${v.count} işlem</td><td class="pr-3">%${v.win_rate} kazanç</td><td class="pr-3">${v.buy} AL / ${v.sell} SAT</td><td class="pr-3">lot ~${v.median_lot}</td><td>${v.median_hold_min} dk</td></tr>`).join('');
  box.innerHTML = `
    <p class="text-gray-500">Son güncelleme: ${esc(p.learned_at)} · ${s.count} elle açılmış kapalı işlem · net ${esc(s.net)} · %${esc(s.win_rate)} kazanç oranı</p>
    <p>${esc(a.summary)}</p>
    ${syms ? `<div class="overflow-x-auto"><table class="text-[11px] text-gray-300">${syms}</table></div>` : ''}
    ${list('Mantık / kurallar', a.rules)}${list('Risk alışkanlıkları', a.risk_habits)}${list('Verimli saatler (broker saati)', a.best_hours)}
    ${list('Zayıf noktalar', a.weak_points)}${list('Dikkat', a.caveats)}`;
  const names = Object.keys(s.symbols || {});
  if (names.length && !$('mevo-symbols').dataset.touched) $('mevo-symbols').value = names.slice(0, 6).join(',');
}

function renderOpps(o) {
  const box = $('mevo-opps');
  if (!o || !o.items || !o.items.length) { box.innerHTML = '<p class="text-xs text-gray-500">Henüz fırsat getirilmedi.</p>'; return; }
  box.innerHTML = o.items.map(i => {
    const tone = i.action === 'BUY' ? 'border-emerald-500/40' : i.action === 'SELL' ? 'border-rose-500/40' : 'border-gray-700';
    const label = i.action === 'BUY' ? 'AL' : i.action === 'SELL' ? 'SAT' : 'BEKLE';
    const lv = i.action === 'WAIT' ? '' : `<div class="font-mono text-[11px] grid grid-cols-4 gap-1 mt-2"><span>Giriş<br><b class="text-white">${esc(i.entry)}</b></span><span>SL<br><b class="text-rose-400">${esc(i.sl)}</b></span><span>TP<br><b class="text-emerald-400">${esc(i.tp)}</b></span><span>R:R<br><b class="text-white">${esc(i.rr)}</b></span></div>`;
    return `<div class="bg-[#0f1218] border ${tone} rounded-xl p-3 text-xs"><div class="flex justify-between items-center"><b class="text-white">${esc(i.symbol)} · ${label}</b><span class="text-gray-400">model güveni %${esc(i.confidence)}</span></div>${lv}<p class="mt-2 text-gray-300">${esc(i.reason)}</p>${i.matched_rule ? `<p class="mt-1 text-gray-500">Kural: ${esc(i.matched_rule)}</p>` : ''}</div>`;
  }).join('') + `<p class="sm:col-span-2 text-[11px] text-gray-500">${esc(o.created_at)} · ${esc(o.timeframe)} · oluşan mum hariç, kapanmış mumlara göre. Yatırım tavsiyesi değildir; emir açılmadı.</p>`;
}

function renderStatus(st) {
  renderProfile(st.profile); renderOpps(st.opportunities);
  $('mevo-usage').textContent = `Bugün ${st.usage_today.calls}/${st.daily_call_limit} istek · ${st.usage_today.tokens} token · model ${st.model}`;
  if (!st.configured) mevoAlert('DeepSeek API anahtarı tanımlı değil: sunucuda DEEPSEEK_API_KEY ortam değişkenini ayarla.', 'err');
}

async function busy(btnId, fn) {
  const b = $(btnId); b.disabled = true; b.classList.add('opacity-60');
  mevoAlert('');
  try { await fn(); } catch (e) { mevoAlert(e.message, 'err'); }
  finally { b.disabled = false; b.classList.remove('opacity-60'); }
}

function mevoLearn() {
  return busy('mevo-learn-btn', async () => { await mevoApi('/api/mevo/learn', {}); renderStatus(await mevoApi('/api/mevo/status')); mevoAlert('Manuel işlemlerin incelendi.', 'ok'); });
}

function mevoOpportunities() {
  return busy('mevo-opp-btn', async () => {
    const symbols = $('mevo-symbols').value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
    await mevoApi('/api/mevo/opportunities', {symbols, timeframe: $('mevo-tf').value});
    renderStatus(await mevoApi('/api/mevo/status'));
  });
}

$('mevo-symbols').addEventListener('input', e => { e.target.dataset.touched = '1'; });
mevoApi('/api/mevo/status').then(renderStatus).catch(e => mevoAlert(e.message, 'err'));
