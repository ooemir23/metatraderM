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
  const table = document.createElement('table');
  table.className = 'w-full text-left border-collapse whitespace-nowrap';
  const header = table.createTHead().insertRow();
  for (const title of ['Seçenek / dönem', 'İşlem', 'Net puan', 'Puan/işlem', 'Kazanç %', 'Azami düşüş']) {
    const th = document.createElement('th');
    th.className = 'border-b border-gray-600 px-2 py-2';
    th.textContent = title;
    header.appendChild(th);
  }
  const body = table.createTBody();
  const addRow = (label, stats) => {
    const row = body.insertRow();
    for (const value of [label, stats.count, stats.net_points, stats.mean_points ?? '—', stats.win_rate_pct ?? '—', stats.max_drawdown_points]) {
      const cell = row.insertCell();
      cell.className = 'border-b border-gray-800 px-2 py-2';
      cell.textContent = String(value);
    }
  };
  if (data.selected) {
    addRow('Seçilen · eğitim', data.selected.train);
    addRow('Seçilen · doğrulama', data.selected.validation);
    addRow('Seçilen · ayrılmış test', data.selected_holdout.stats);
  }
  addRow('Ham kesişim · ayrılmış test', data.baseline_holdout.stats);
  // Display all training comparisons without sorting by held-out performance.
  for (const item of data.comparisons) addRow(researchConfigLabel(item.config) + ' · eğitim', item.train);
  root.appendChild(table);
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
    hma_period: number('research-hma'), kama_period: number('research-kama'), atr_period: number('research-atr'),
    er_min: number('research-er'), stop_atr: number('research-stop'), target_atr: number('research-target'),
    historical_spread: true, commission_points: number('research-commission'), slippage_points: number('research-slippage'),
    swap_long_points_per_day: number('research-swap-long'), swap_short_points_per_day: number('research-swap-short')};
  button.disabled = true;
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
    status.textContent = data.selected ? 'Karşılaştırma tamamlandı. Seçilen değerler araştırma adayıdır.' : 'Karşılaştırma tamamlandı; uygun aday yok.';
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}

function downloadResearchReport() {
  if (!latestResearchReport) return;
  const blob = new Blob([JSON.stringify(latestResearchReport, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'XAUUSD-HMA-KAMA-ATR-report.json';
  a.click();
  URL.revokeObjectURL(url);
}

document.addEventListener('DOMContentLoaded', () => {
  const end = document.getElementById('research-end');
  const start = document.getElementById('research-start');
  if (!end || !start) return;
  const today = new Date();
  end.value = today.toISOString().slice(0, 10);
  end.max = end.value;
  const earlier = new Date(today);
  earlier.setUTCFullYear(earlier.getUTCFullYear() - 3);
  start.value = earlier.toISOString().slice(0, 10);
  start.max = end.value;
});
