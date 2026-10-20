// Period report (Rapor sekmesi): day / week / month / year table from /api/reports?period=&limit=.
// Days are broker server days, weeks start on Monday (computed on the server). No numbers are made up:
// an empty period comes back from the broker report as zero.
(() => {
  const LIMITS = {day: 30, week: 12, month: 12, year: 5};
  const MIN_INTERVAL = 60000;          // the report reads every closed position; do not poll it every tick
  let period = 'day', last = null, loadedAt = 0, loading = null, generation = 0;
  try { const saved = localStorage.getItem('hma.report-period'); if (LIMITS[saved]) period = saved; } catch (_) {}

  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  const money = (v, cur, signed = true) => {
    if (v === null || v === undefined) return '—';
    const n = num(v), text = Math.abs(n).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return `${n < 0 ? '-' : (signed && n > 0 ? '+' : '')}${cur} ${text}`;
  };
  const tone = v => num(v) > 0 ? 'hx-profit' : num(v) < 0 ? 'hx-loss' : '';

  function syncSegment() {
    document.querySelectorAll('#period-report [data-period]').forEach(btn => btn.setAttribute('aria-pressed', String(btn.dataset.period === period)));
  }

  function chart(rows, cur) {
    const data = rows.slice().reverse();               // oldest -> newest, left to right
    const max = Math.max(1e-9, ...data.map(r => Math.abs(num(r.net))));
    const w = 100 / Math.max(1, data.length);
    const bars = data.map((r, i) => {
      const h = Math.abs(num(r.net)) / max * 46;
      const y = num(r.net) >= 0 ? 50 - h : 50;
      return `<rect x="${(i * w + w * .15).toFixed(3)}" y="${y.toFixed(2)}" width="${(w * .7).toFixed(3)}" height="${Math.max(h, num(r.net) ? .6 : 0).toFixed(2)}" class="${num(r.net) >= 0 ? 'pr-up' : 'pr-down'}"><title>${esc(r.label)} · ${esc(money(r.net, cur))}</title></rect>`;
    }).join('');
    return `<svg class="pr-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Dönem net kâr/zarar grafiği">
      <line x1="0" x2="100" y1="50" y2="50" class="pr-axis"/>${bars}</svg>`;
  }

  function render(data) {
    const box = $('period-report-body'); if (!box) return;
    const cur = data.currency || window.accountCurrency || '';
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const s = data.summary || {};
    box.innerHTML = `
      <div class="pr-kpis">
        <div class="pr-kpi"><span>Bu dönem</span><strong class="${tone(s.current)}">${esc(money(s.current, cur))}</strong></div>
        <div class="pr-kpi"><span>Önceki dönem</span><strong class="${tone(s.previous)}">${esc(money(s.previous, cur))}</strong></div>
        <div class="pr-kpi"><span>Dönem ortalaması</span><strong class="${tone(s.average)}">${esc(money(s.average, cur))}</strong></div>
        <div class="pr-kpi"><span>Kârlı dönem oranı</span><strong>%${num(s.profitable_ratio).toLocaleString('tr-TR')}</strong><em>${num(s.profitable_periods)} kârlı · toplam ${esc(money(s.total, cur))}</em></div>
      </div>
      ${chart(rows, cur)}
      <div class="pr-table-wrap"><table class="pr-table">
        <thead><tr><th>Dönem</th><th>İşlem</th><th>K / Z</th><th>Kazanma</th><th>Brüt kâr</th><th>Brüt zarar</th><th>Komisyon</th><th>Swap</th><th>Net</th><th>PF</th><th>En iyi</th><th>En kötü</th><th>Kümülatif</th></tr></thead>
        <tbody>${rows.map(r => `<tr>
          <td data-label="Dönem">${esc(r.label)}</td>
          <td data-label="İşlem">${num(r.trades)}</td>
          <td data-label="K / Z">${num(r.wins)} / ${num(r.losses)}</td>
          <td data-label="Kazanma">${r.trades ? '%' + num(r.win_rate).toLocaleString('tr-TR') : '—'}</td>
          <td data-label="Brüt kâr" class="${tone(r.gross_profit)}">${esc(money(r.gross_profit, cur, false))}</td>
          <td data-label="Brüt zarar" class="${num(r.gross_loss) ? 'hx-loss' : ''}">${esc(money(r.gross_loss, cur))}</td>
          <td data-label="Komisyon">${esc(money(r.commission, cur))}</td>
          <td data-label="Swap">${esc(money(r.swap, cur))}</td>
          <td data-label="Net" class="${tone(r.net)} pr-strong">${esc(money(r.net, cur))}</td>
          <td data-label="PF">${r.profit_factor === null ? '∞' : num(r.profit_factor).toFixed(2)}</td>
          <td data-label="En iyi">${esc(money(r.best, cur))}</td>
          <td data-label="En kötü">${esc(money(r.worst, cur))}</td>
          <td data-label="Kümülatif" class="${tone(r.cumulative)}">${esc(money(r.cumulative, cur))}</td>
        </tr>`).join('')}</tbody>
      </table></div>
      <p class="pr-note">${esc(data.basis || '')}${data.incomplete_position_ids?.length ? ` ${data.incomplete_position_ids.length} pozisyonun geçmişi eksik; kazanma istatistiğine katılmadı.` : ''}</p>`;
    const csv = $('period-report-csv'); if (csv) csv.disabled = !rows.length;
  }

  function showError(message) {
    const box = $('period-report-body');
    if (box) box.innerHTML = `<p class="pr-note pr-error" role="status">${esc(message)}</p>`;
    const csv = $('period-report-csv'); if (csv) csv.disabled = true;
  }

  async function load(force = false) {
    if (!$('period-report-body')) return;
    if (!force && last?.period === period && Date.now() - loadedAt < MIN_INTERVAL) return;
    if (loading && !force) return loading;
    const request = ++generation, wanted = period;
    loading = (async () => {
      try {
        const res = await fetch(`/api/reports?period=${wanted}&limit=${LIMITS[wanted]}`);
        const data = await res.json().catch(() => ({}));
        if (request !== generation) return;
        if (!res.ok || !Array.isArray(data.rows)) throw new Error(typeof data.detail === 'string' ? data.detail : 'Dönem raporu okunamadı.');
        last = data; loadedAt = Date.now();
        render(data);
      } catch (err) {
        if (request === generation) showError(`${err.message || 'Dönem raporu okunamadı.'} Tekrar denenecek.`);
      } finally { if (request === generation) loading = null; }
    })();
    return loading;
  }

  function setPeriod(next) {
    if (!LIMITS[next]) return;
    period = next;
    try { localStorage.setItem('hma.report-period', next); } catch (_) {}
    syncSegment();
    return load(true);
  }

  function exportCsv() {
    if (!last?.rows?.length) return;
    const head = ['period', 'label', 'start_broker_day', 'trades', 'wins', 'losses', 'win_rate', 'gross_profit', 'gross_loss', 'commission', 'swap', 'fee', 'net', 'profit_factor', 'best', 'worst', 'cumulative', 'currency'];
    const lines = last.rows.map(r => [last.period, `"${r.label}"`, r.start, r.trades, r.wins, r.losses, r.win_rate, r.gross_profit, r.gross_loss, r.commission, r.swap, r.fee, r.net, r.profit_factor ?? '', r.best ?? '', r.worst ?? '', r.cumulative, last.currency].join(','));
    const blob = new Blob(['﻿' + [head.join(','), ...lines].join('\r\n')], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = `rapor-${last.period}.csv`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function reset() { generation++; last = null; loadedAt = 0; loading = null; }

  document.addEventListener('DOMContentLoaded', syncSegment);
  window.HMAPeriodReport = {load, setPeriod, exportCsv, reset, render, period: () => period, LIMITS};
})();
