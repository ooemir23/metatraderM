let latestResearchReport = null;

function researchConfigLabel(c) {
  return `${c.mode.replace('_', ' + ')} · ${c.filtered ? 'filtreli' : 'ham kesişim'} · ${c.wait} mum · %${Math.round(c.distance_atr * 100)} ATR`;
}

function renderResearchReport(data) {
  const root = document.getElementById('research-result');
  root.replaceChildren();
  const addText = (text) => {
    const p = document.createElement('p');
    p.className = 'my-2 text-gray-300';
    p.textContent = text;
    root.appendChild(p);
  };
  const date = (time) => new Date(time * 1000).toISOString().slice(0, 16).replace('T', ' ');
  addText(`Gerçek veri: H1 ${data.data_quality.H1.count} mum · H4 ${data.data_quality.H4.count} mum. Ortak dönem: ${date(data.segments.train.start)} – ${date(data.segments.holdout.end_exclusive)}.`);
  addText(`Seans/eksik veri olabilecek uzun aralıklar: H1 ${data.data_quality.H1.long_intervals}, H4 ${data.data_quality.H4.long_intervals}.`);
  if (data.selected) {
    addText(`Eğitimde seçilen aday: ${researchConfigLabel(data.selected.config)}. Ayrılmış test sonucu parametre seçiminde kullanılmadı.`);
  } else {
    addText('Eğitimde pozitif sonuç ve yeterli işlem sağlayan filtreli aday bulunamadı.');
  }
  if (data.assessment) addText(data.assessment.message);
  const currency = data.settings.currency;
  const money = value => value == null ? '—' : `${Number(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} ${currency || ''}`;
  const selectedStats = data.selected_holdout ? data.selected_holdout.stats : data.baseline_holdout.stats;
  if (data.selected) {
    const total = data.selected.train.count + data.selected.validation.count + selectedStats.count;
    addText(`Seçilen aday tüm dönemlerde ${total} işlem: eğitim ${data.selected.train.count}, doğrulama ${data.selected.validation.count}, ayrılmış test ${selectedStats.count}. Üstteki özet yalnız son %20'lik ayrılmış testi gösterir. Araştırmada 10 işlem sınırı yoktur.`);
    const signals = data.selected_holdout.signals;
    if (signals) addText(`Ayrılmış test sinyalleri: ${signals.crossovers} kesişim, ${signals.confirmed} onay; ${signals.filtered_at_deadline} filtre nedeniyle iptal, ${signals.expired} süre aşımı, ${signals.reversed} ters kesişim nedeniyle iptal. Aynı anda tek araştırma pozisyonu tutulur.`);
  } else {
    addText('Araştırmada 10 işlem sınırı yoktur. İşlem sayısı tarih aralığına, kesişimlere ve onay koşullarına göre oluşur.');
  }
  const cards = document.createElement('div');
  cards.className = 'research-metrics';
  addText(data.selected ? 'Seçilen aday · ayrılmış test özeti' : 'Ham kesişim · ayrılmış test özeti');
  for (const [label, value] of [
    ['İşlem başına lot', data.settings.lot_size], ['Başlangıç sermayesi', money(selectedStats.initial_capital)],
    ['Toplam kâr', money(selectedStats.gross_profit)], ['Toplam zarar', money(selectedStats.gross_loss)],
    ['Net kazanç / kayıp', money(selectedStats.net_profit)], ['Son bakiye', money(selectedStats.final_capital)],
    ['Doğru (kârlı)', selectedStats.wins], ['Yanlış (zararlı)', selectedStats.losses],
    ['Başa baş', selectedStats.breakeven], ['Getiri', `${selectedStats.return_pct ?? '—'}%`],
    ['Azami düşüş', money(selectedStats.max_drawdown_money)]]) {
    const card = document.createElement('div');
    const title = document.createElement('span'); title.textContent = label;
    const valueNode = document.createElement('strong'); valueNode.textContent = String(value);
    card.append(title, valueNode); cards.appendChild(card);
  }
  root.appendChild(cards);
  addText('Doğru/yanlış, maliyet sonrası kârlı/zararlı işlem demektir. Eğitim, doğrulama ve ayrılmış test ayrı simülasyonlardır; her biri aynı başlangıç sermayesini kullanır.');
  const makeTable = titles => {
    const wrap = document.createElement('div'); wrap.className = 'research-table-scroll';
    const table = document.createElement('table');
    table.className = 'w-full text-left border-collapse whitespace-nowrap';
    const header = table.createTHead().insertRow();
    for (const title of titles) {
      const th = document.createElement('th'); th.className = 'border-b border-gray-600 px-2 py-2';
      th.textContent = title; header.appendChild(th);
    }
    const body = table.createTBody(); wrap.appendChild(table);
    return {wrap, body};
  };
  const addCells = (body, values) => {
    const row = body.insertRow();
    for (const value of values) {
      const cell = row.insertCell(); cell.className = 'border-b border-gray-800 px-2 py-2';
      cell.textContent = String(value ?? '—');
    }
  };
  const statTitles = ['Seçenek / dönem', 'Lot', 'Ana para', 'İşlem', 'Doğru', 'Yanlış', 'Başa baş', 'Toplam kâr', 'Toplam zarar', 'Net kazanç/kayıp', 'Son bakiye', 'Kazanç %', 'Azami düşüş', 'Net puan'];
  const statCells = (label, stats) => [label, data.settings.lot_size, money(stats.initial_capital), stats.count,
    stats.wins, stats.losses, stats.breakeven, money(stats.gross_profit), money(stats.gross_loss),
    money(stats.net_profit), money(stats.final_capital), stats.win_rate_pct ?? '—', money(stats.max_drawdown_money), stats.net_points];
  const summaryTable = makeTable(statTitles);
  if (data.selected) {
    addCells(summaryTable.body, statCells('Seçilen · eğitim', data.selected.train));
    addCells(summaryTable.body, statCells('Seçilen · doğrulama', data.selected.validation));
    addCells(summaryTable.body, statCells('Seçilen · ayrılmış test', data.selected_holdout.stats));
  }
  addCells(summaryTable.body, statCells('Ham kesişim · ayrılmış test', data.baseline_holdout.stats));
  root.appendChild(summaryTable.wrap);

  const tradeGroups = [];
  if (data.selected) {
    tradeGroups.push(['Seçilen · ayrılmış test', data.selected_holdout.trades],
      ['Seçilen · eğitim', data.selected_trades?.train || []],
      ['Seçilen · doğrulama', data.selected_trades?.validation || []]);
  }
  tradeGroups.push(['Ham kesişim · ayrılmış test', data.baseline_holdout.trades]);
  const toolbar = document.createElement('div'); toolbar.className = 'research-trade-toolbar';
  const label = document.createElement('label'); label.textContent = 'İşlem dökümü: ';
  const selector = document.createElement('select'); selector.id = 'research-trade-period';
  selector.className = 'rounded bg-gray-800 p-2'; selector.setAttribute('aria-label', 'İşlem dökümü dönemi');
  tradeGroups.forEach(([name], index) => {
    const option = document.createElement('option'); option.value = index; option.textContent = name; selector.appendChild(option);
  });
  label.appendChild(selector);
  const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = 'Önceki';
  const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Sonraki';
  for (const button of [previous, next]) button.className = 'rounded bg-gray-700 px-3 py-2 disabled:opacity-40';
  const pageInfo = document.createElement('span'); pageInfo.setAttribute('aria-live', 'polite');
  toolbar.append(label, previous, next, pageInfo); root.appendChild(toolbar);
  const tradeTable = makeTable(['#', 'Yön', 'Lot', 'Giriş (UTC)', 'Çıkış (UTC)', 'Giriş fiyatı', 'Çıkış fiyatı', 'Net kazanç/kayıp', 'İşlem sonrası bakiye', 'Sonuç', 'Çıkış nedeni']);
  root.appendChild(tradeTable.wrap);
  let page = 0;
  const reasons = {'stop': 'Stop', 'target': 'Hedef', 'opposite crossover': 'Ters kesişim', 'segment end': 'Dönem sonu'};
  const showTrades = () => {
    const trades = tradeGroups[Number(selector.value)][1];
    tradeTable.body.replaceChildren();
    const first = page * 50;
    trades.slice(first, first + 50).forEach((trade, i) => addCells(tradeTable.body, [first + i + 1,
      trade.side === 'BUY' ? 'Alış' : 'Satış', trade.lot_size, date(trade.entry_time), date(trade.exit_time),
      trade.entry_price?.toFixed(2), trade.exit_price?.toFixed(2), money(trade.net_profit), money(trade.balance_after),
      trade.net_points > 0 ? 'Kârlı' : trade.net_points < 0 ? 'Zararlı' : 'Başa baş', reasons[trade.reason] || trade.reason]));
    pageInfo.textContent = trades.length ? `${first + 1}–${Math.min(first + 50, trades.length)} / ${trades.length} işlem` : 'İşlem yok';
    previous.disabled = page === 0; next.disabled = first + 50 >= trades.length;
  };
  selector.addEventListener('change', () => { page = 0; showTrades(); });
  previous.addEventListener('click', () => { if (page > 0) { page--; showTrades(); } });
  next.addEventListener('click', () => { if (!next.disabled) { page++; showTrades(); } });
  showTrades();

  const comparisons = document.createElement('details');
  const comparisonTitle = document.createElement('summary'); comparisonTitle.className = 'my-3 cursor-pointer font-semibold';
  comparisonTitle.textContent = '51 seçeneğin eğitim sonuçları'; comparisons.appendChild(comparisonTitle);
  const trainingTable = makeTable(statTitles);
  for (const item of data.comparisons) addCells(trainingTable.body, statCells(researchConfigLabel(item.config), item.train));
  comparisons.appendChild(trainingTable.wrap); root.appendChild(comparisons);
  addText('OHLC simülasyonu: aynı mumda stop ve hedef görülürse stop sayılır. Gerçek tick sırası bilinmez. Bu rapor otomatik canlı işlem ayarı oluşturmaz.');
}

async function runResearchComparison() {
  const button = document.getElementById('research-run');
  if (button.disabled) return;
  const status = document.getElementById('research-status');
  const number = id => Number(document.getElementById(id).value);
  const start = Date.parse(document.getElementById('research-start').value + 'T00:00:00Z') / 1000;
  const end = Date.parse(document.getElementById('research-end').value + 'T00:00:00Z') / 1000;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    status.textContent = 'Başlangıç tarihi bitiş tarihinden önce olmalı.';
    return;
  }
  const payload = {symbol: 'XAUUSD', start, end,
    lot_size: number('research-lot'), initial_capital: number('research-capital'),
    hma_period: number('research-hma'), kama_period: number('research-kama'), atr_period: number('research-atr'),
    er_min: number('research-er'), stop_atr: number('research-stop'), target_atr: number('research-target'),
    historical_spread: true, commission_points: number('research-commission'), slippage_points: number('research-slippage'),
    swap_long_points_per_day: number('research-swap-long'), swap_short_points_per_day: number('research-swap-short')};
  saveResearchSettings();
  button.disabled = true;
  document.getElementById('research-reset').disabled = true;
  latestResearchReport = null;
  document.getElementById('research-download').disabled = true;
  document.getElementById('research-result').replaceChildren();
  status.textContent = 'Broker geçmişi alınıyor ve 51 seçenek karşılaştırılıyor…';
  try {
    const response = await fetch('/api/research/compare', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Araştırma isteği geçersiz.');
    latestResearchReport = data;
    renderResearchReport(data);
    document.getElementById('research-download').disabled = false;
    status.textContent = data.assessment ? data.assessment.message : (data.selected ? 'Karşılaştırma tamamlandı. Seçilen değerler araştırma adayıdır.' : 'Karşılaştırma tamamlandı; uygun aday yok.');
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; document.getElementById('research-reset').disabled = false; }
}

async function downloadResearchReport() {
  if (!latestResearchReport) return;
  const button = document.getElementById('research-download');
  if (button.disabled) return;
  const report = latestResearchReport;
  button.disabled = true;
  try {
    const response = await fetch('/api/research/export', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(report)});
    if (!response.ok) {
      const error = await response.json();
      throw new Error(typeof error.detail === 'string' ? error.detail : 'Excel raporu oluşturulamadı.');
    }
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement('a');
    a.href = url; a.download = 'XAUUSD-HMA-KAMA-ATR-report.xlsx'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { document.getElementById('research-status').textContent = error.message; }
  finally { button.disabled = !latestResearchReport; }
}

const researchSettingsKey = 'metatraderm.research.settings.v1';
const researchFieldIds = ['research-start', 'research-end', 'research-lot', 'research-capital',
  'research-hma', 'research-kama', 'research-atr', 'research-er', 'research-stop', 'research-target',
  'research-commission', 'research-slippage', 'research-swap-long', 'research-swap-short'];
function researchDefaultSettings() {
  const today = new Date();
  const earlier = new Date(today); earlier.setUTCFullYear(earlier.getUTCFullYear() - 3);
  for (const id of researchFieldIds) {
    const input = document.getElementById(id);
    if (input) input.value = input.defaultValue;
  }
  const end = document.getElementById('research-end'), start = document.getElementById('research-start');
  end.value = today.toISOString().slice(0, 10); start.value = earlier.toISOString().slice(0, 10);
  end.max = start.max = end.value;
}
function saveResearchSettings() {
  try {
    const values = {};
    for (const id of researchFieldIds) values[id] = document.getElementById(id).value;
    localStorage.setItem(researchSettingsKey, JSON.stringify(values));
  } catch (_) {
    document.getElementById('research-status').textContent = 'Ayarlar bu tarayıcıda kaydedilemedi; tarayıcı depolama iznini kontrol edin.';
  }
}
function resetResearchSettings() {
  if (document.getElementById('research-run').disabled) return;
  try { localStorage.removeItem(researchSettingsKey); }
  catch (_) {
    document.getElementById('research-status').textContent = 'Kayıtlı ayarlar silinemedi; tarayıcı depolama iznini kontrol edin.';
    return;
  }
  researchDefaultSettings();
  latestResearchReport = null;
  document.getElementById('research-result').replaceChildren();
  document.getElementById('research-download').disabled = true;
  document.getElementById('research-status').textContent = 'Ayarlar varsayılanlara sıfırlandı. Yeni rapor için karşılaştırmayı çalıştırın.';
}
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('research-form')) return;
  researchDefaultSettings();
  try {
    const saved = JSON.parse(localStorage.getItem(researchSettingsKey) || 'null');
    if (saved && typeof saved === 'object') {
      for (const id of researchFieldIds) {
        if (typeof saved[id] === 'string' && saved[id].length <= 100) document.getElementById(id).value = saved[id];
      }
    }
  } catch (_) {
    document.getElementById('research-status').textContent = 'Kayıtlı ayarlar okunamadı; varsayılan değerler gösteriliyor.';
  }
  for (const id of researchFieldIds) {
    const input = document.getElementById(id);
    input.addEventListener('input', saveResearchSettings);
    input.addEventListener('change', saveResearchSettings);
  }
});


let researchPreviousOverflow = null;
function updateResearchFullscreenButton() {
  const panel = document.getElementById('research-panel');
  const active = document.fullscreenElement === panel || panel.classList.contains('research-fullscreen-fallback');
  const button = document.getElementById('research-fullscreen');
  button.textContent = active ? '⛶ Tam ekrandan çık' : '⛶ Tam ekran';
  button.setAttribute('aria-pressed', String(active));
}
function exitResearchFallback() {
  const panel = document.getElementById('research-panel');
  if (!panel.classList.contains('research-fullscreen-fallback')) return;
  panel.classList.remove('research-fullscreen-fallback');
  document.body.style.overflow = researchPreviousOverflow ?? '';
  researchPreviousOverflow = null;
  updateResearchFullscreenButton();
}
async function toggleResearchFullscreen(event) {
  event.preventDefault(); event.stopPropagation();
  const panel = document.getElementById('research-panel');
  if (document.fullscreenElement === panel) {
    await document.exitFullscreen(); return;
  }
  if (panel.classList.contains('research-fullscreen-fallback')) {
    exitResearchFallback(); return;
  }
  panel.open = true;
  try {
    if (!panel.requestFullscreen) throw new Error('Fullscreen unavailable');
    await panel.requestFullscreen();
  } catch (_) {
    researchPreviousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.classList.add('research-fullscreen-fallback');
  }
  updateResearchFullscreenButton();
}
document.addEventListener('fullscreenchange', updateResearchFullscreenButton);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') exitResearchFallback();
});
