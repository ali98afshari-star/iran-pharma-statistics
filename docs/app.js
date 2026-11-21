/* Dashboard for Iran pharma statistics (v2). Reads ./data.json (same folder).
   All numbers come from that file; nothing is hard-coded except labels. */
'use strict';

/* ---- AI worker config ----
   Set WORKER_URL to the Cloudflare Worker URL that answers questions
   (POST {question, context} -> {answer}). Empty = not connected yet:
   the ask box shows a "coming soon" state and never invents a URL. */
const WORKER_URL = "";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
}
function fmtDec(n, d) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
/* Rial -> billion Toman (1 Toman = 10 Rial; 1 billion Toman = 1e10 Rial) */
function bnToman(rial) { return rial / 1e10; }
function bnUnits(u) { return u / 1e9; }
function pct(x) { return fmtDec(x * 100, 1) + '٪'; }
function signedPct(x) { return (x >= 0 ? '+' : '−') + fmtDec(Math.abs(x * 100), 1) + '٪'; }
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

let DATA = null;
let topYear = '1404';
let topSort = 'value';
let atcYear = 1404;
let provMonth = 1;
let monthSel = '1405-6';
let distYear = 1404;
let distSort = 'value';
let manufYear = 1404;
let manufSort = 'value';
/* current row arrays per table, for re-sorting */
const CURRENT = {};
const SORT_STATE = {};

async function load() {
  try {
    const res = await fetch('./data.json');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const text = await res.text();
    /* data.json is generated from Python/pandas, which can emit bare NaN /
       Infinity tokens that are not valid strict JSON and would make
       response.json() throw. Sanitize them to null before parsing. */
    DATA = JSON.parse(text.replace(/:\s*(NaN|-?Infinity)\b/g, ': null'));
  } catch (e) {
    const el = $('#load-error');
    el.hidden = false;
    el.textContent = 'خطا در خواندن فایل داده (data.json): ' + e.message +
      ' — مطمئن شوید داشبورد از همین پوشه (docs) سرو می‌شود.';
    return;
  }
  renderAll();
}

function renderAll() {
  renderAiStatus();
  renderYearCards();
  renderTop();
  renderAtcFixNote();
  renderAtc();
  renderMonthlyChart();
  renderMonthSelect();
  renderMonthCards();
  renderMonthlyTable();
  renderBlankMonthNotes();
  renderProvinceWarning();
  renderProvinces();
  renderDistributors();
  renderManufacturers();
  renderCavAtc();
}

/* ================= AI SECTION ================= */
function buildAiContext() {
  const t04 = DATA.market_year_totals['1404'];
  const t05 = DATA.market_year_totals['1405_h1'];
  const top04 = topGenerics('1404', 'value', 5);
  const top05 = topGenerics('1405', 'value', 5);
  const fix = DATA.atc_fix;
  const lines = [
    'بازار داروی ایران از سالنامه‌های رسمی سازمان غذا و دارو (IFDA).',
    'سال ۱۴۰۴ کامل (۱۲ ماه): واحد فروش ' + fmt(t04.units) + ' عدد، ارزش مصرف‌کننده ' +
      fmtDec(bnToman(t04.value_consumer), 1) + ' میلیارد تومان، میانگین قیمت هر واحد ' +
      fmt(Math.round(t04.value_consumer / t04.units)) + ' ریال.',
    'نیمهٔ اول ۱۴۰۵ (فقط ۶ ماه — با کل ۱۴۰۴ مقایسه نشود): واحد فروش ' + fmt(t05.units) +
      ' عدد، ارزش مصرف‌کننده ' + fmtDec(bnToman(t05.value_consumer), 1) +
      ' میلیارد تومان، میانگین قیمت هر واحد ' +
      fmt(Math.round(t05.value_consumer / t05.units)) + ' ریال.',
    '۵ ژنریک پرفروش ۱۴۰۴ از نظر ارزش: ' + top04.map((r) => r.generic_name + ' (' + fmtDec(bnToman(r.value_consumer), 1) + ' میلیارد تومان)').join('؛ ') + '.',
    '۵ ژنریک پرفروش نیمهٔ اول ۱۴۰۵ از نظر ارزش: ' + top05.map((r) => r.generic_name + ' (' + fmtDec(bnToman(r.value_consumer), 1) + ' میلیارد تومان)').join('؛ ') + '.',
  ];
  if (fix) {
    lines.push('سهم ارزش بدون کد ATC پس از تکمیل: ۱۴۰۴ برابر ' +
      pct(fix.after['1404'].blank_value_pct) + ' و نیمهٔ اول ۱۴۰۵ برابر ' +
      pct(fix.after['1405'].blank_value_pct) + ' است؛ ' + fmt(fix.generics_still_blank_count) +
      ' ژنریک هنوز در هیچ ردیفی ATC ندارند.');
  }
  lines.push('واحد فروش یعنی تعداد واحد (قرص/آمپول)، نه بسته. همهٔ مبالغ به ریال‌اند؛ تومان = ریال تقسیم بر ۱۰.');
  return lines.join('\n');
}

function renderAiStatus() {
  const st = $('#ai-status');
  if (!WORKER_URL) {
    st.textContent = 'بخش پاسخ‌گو هنوز به ورکر وصل نشده است — به‌زودی وصل می‌شود. تا آن زمان، تب‌های زیر همهٔ آمار را نشان می‌دهند.';
  } else {
    st.textContent = '';
  }
}

async function askAi(question) {
  const ans = $('#ai-answer');
  const btn = $('#ai-send');
  ans.hidden = false;
  if (!WORKER_URL) {
    ans.innerHTML = 'بخش پاسخ‌گو هنوز وصل نشده است. ورکر پرسش‌وپاسخ به‌زودی به این داشبورد وصل می‌شود؛ ' +
      'تا آن زمان می‌توانید از تب‌های سالانه، ماهانه و استانی استفاده کنید.' +
      '<span class="ai-note">سؤال شما: ' + escapeHtml(question) + '</span>';
    return;
  }
  ans.textContent = 'در حال پرسیدن از داده‌ها…';
  btn.disabled = true;
  try {
    const res = await fetch(WORKER_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question, context: buildAiContext() }),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    ans.innerHTML = escapeHtml(data.answer || 'پاسخی دریافت نشد.') +
      '<span class="ai-note">پاسخ بر اساس سالنامه‌های رسمی سازمان غذا و دارو داده شده است؛ ' +
      'سال ۱۴۰۵ فقط نیمهٔ اول را پوشش می‌دهد.</span>';
  } catch (e) {
    ans.innerHTML = 'خطا در تماس با ورکر: ' + escapeHtml(e.message) +
      '<span class="ai-note">لطفاً دوباره تلاش کنید.</span>';
  } finally {
    btn.disabled = false;
  }
}

function submitAi() {
  const q = $('#ai-input').value.trim();
  if (!q) return;
  askAi(q);
}

/* ================= DETAIL MODAL ================= */
/* Details open as a modal popup (overlay + centered dialog), never inline.
   Closes via the × button, a click on the backdrop, or Escape. */
function openModal(html) {
  const overlay = $('#modal-overlay');
  $('#modal-body').innerHTML = html;
  overlay.hidden = false;
  document.body.classList.add('modal-open');
  $('#modal-close').focus();
}
function closeModal() {
  const overlay = $('#modal-overlay');
  if (overlay.hidden) return;
  overlay.hidden = true;
  document.body.classList.remove('modal-open');
  $('#modal-body').innerHTML = '';
}

/* ================= TABS ================= */
function switchTab(name) {
  $$('.main-tab').forEach((b) => {
    const on = b.getAttribute('data-tab') === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  $$('.tab-panel').forEach((p) => {
    p.hidden = p.id !== 'panel-' + name;
  });
}

/* ================= SORTABLE TABLES ================= */
function sortBy(rows, key, dir) {
  return [...rows].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
    return String(va).localeCompare(String(vb), 'fa') * dir;
  });
}

/* Wire th[data-sort] clicks on a table. getRows() returns the current row
   objects; rowHtml(r) renders one row. First click sorts descending (most
   useful for numbers), next clicks toggle. */
function makeSortable(table, getRows, rowHtml) {
  table.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.getAttribute('data-sort');
      const st = SORT_STATE[table.id] || (SORT_STATE[table.id] = { key: null, dir: -1 });
      st.dir = (st.key === key) ? -st.dir : -1;
      st.key = key;
      table.querySelectorAll('th').forEach((x) => x.classList.remove('sorted-asc', 'sorted-desc'));
      th.classList.add(st.dir === 1 ? 'sorted-asc' : 'sorted-desc');
      const rows = sortBy(getRows(), key, st.dir);
      table.querySelector('tbody').innerHTML = rows.map(rowHtml).join('');
    });
  });
}

/* ================= SVG HELPERS ================= */
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
}
function svgText(x, y, s, anchor, size) {
  const t = svgEl('text', { x: x, y: y, 'text-anchor': anchor || 'middle', 'font-size': size || 11, fill: '#555' });
  t.textContent = s;
  return t;
}
function svgTitle(text) {
  const t = svgEl('title');
  t.textContent = text;
  return t;
}

/* ================= ANNUAL TAB ================= */
function topGenerics(year, by, n) {
  const unitsKey = year === '1404' ? 'units_1404' : 'units_1405_h1';
  const valueKey = year === '1404' ? 'value_consumer_1404' : 'value_consumer_1405_h1';
  const rows = DATA.catalog.map((d) => ({
    generic_code: d.generic_code,
    generic_name: d.generic_name,
    atc: d.atc,
    units: d[unitsKey] || 0,
    value_consumer: d[valueKey] || 0,
  })).filter((r) => r.value_consumer > 0);
  rows.sort((a, b) => (by === 'units' ? b.units - a.units : b.value_consumer - a.value_consumer));
  return rows.slice(0, n || 20);
}

function yearCard(label, value, sub) {
  return '<div class="card"><div class="label">' + label + '</div><div class="value">' +
    value + '</div><div class="sub">' + sub + '</div></div>';
}

function renderYearCards() {
  const t04 = DATA.market_year_totals['1404'];
  const t05 = DATA.market_year_totals['1405_h1'];
  const avg04 = t04.value_consumer / t04.units;
  const avg05 = t05.value_consumer / t05.units;
  $('#cards-1404').innerHTML =
    yearCard('واحد فروش', fmt(t04.units) + ' عدد', fmtDec(bnUnits(t04.units), 2) + ' میلیارد عدد') +
    yearCard('ارزش مصرف‌کننده', fmtDec(bnToman(t04.value_consumer), 2) + ' میلیارد تومان', 'معادل ' + fmtDec(t04.value_consumer / 1e9, 0) + ' میلیارد ریال') +
    yearCard('میانگین قیمت هر واحد', fmt(Math.round(avg04)) + ' ریال', fmtDec(avg04 / 10, 0) + ' تومان به ازای هر واحد');
  $('#cards-1405').innerHTML =
    yearCard('واحد فروش', fmt(t05.units) + ' عدد', fmtDec(bnUnits(t05.units), 2) + ' میلیارد عدد') +
    yearCard('ارزش مصرف‌کننده', fmtDec(bnToman(t05.value_consumer), 2) + ' میلیارد تومان', 'بیش از کل ارزش سال ۱۴۰۴، با نیمی از واحدها') +
    yearCard('میانگین قیمت هر واحد', fmt(Math.round(avg05)) + ' ریال', fmtDec(avg05 / 10, 0) + ' تومان به ازای هر واحد');
  $('#price-note').textContent =
    'میانگین قیمت هر واحد (ارزش تقسیم بر واحد فروش): ۱۴۰۴ برابر ' + fmt(Math.round(avg04)) +
    ' ریال (' + fmtDec(avg04 / 10, 0) + ' تومان) و نیمهٔ اول ۱۴۰۵ برابر ' + fmt(Math.round(avg05)) +
    ' ریال (' + fmtDec(avg05 / 10, 0) + ' تومان) است — یعنی حدود ' + fmtDec(avg05 / avg04, 2) +
    ' برابر. ارزش نیمهٔ اول ۱۴۰۵ با وجود نیمی از واحدها، از کل ارزش سال ۱۴۰۴ بیشتر است. ' +
    'تعداد کد ژنریک یکتا در کاتالوگ: ' + fmt(DATA.catalog.length) + ' دارو.';
}

function topRowHtml(total) {
  return (r, i) =>
    '<tr><td class="num">' + (i + 1) + '</td><td class="cell-name">' + escapeHtml(r.generic_name) + '</td>' +
    '<td class="num">' + escapeHtml(String(r.generic_code)) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / total) + '</td></tr>';
}

function renderTop() {
  const totalKey = topYear === '1404' ? '1404' : '1405_h1';
  const total = DATA.market_year_totals[totalKey].value_consumer;
  const rows = topGenerics(topYear, topSort, 20).map((r, i) => Object.assign({ rank: i + 1, share: r.value_consumer / total }, r));
  CURRENT.top = rows;
  $('#top-table tbody').innerHTML = rows.map(topRowHtml(total)).join('');
  $('#top-note').textContent = topYear === '1404'
    ? 'سهم ارزشی نسبت به کل ارزش مصرف‌کنندهٔ سال ۱۴۰۴ محاسبه شده است. جدول بر اساس ' + (topSort === 'units' ? 'واحد فروش' : 'ارزش') + ' مرتب شده است.'
    : 'سهم ارزشی نسبت به کل ارزش مصرف‌کنندهٔ نیمهٔ اول ۱۴۰۵ محاسبه شده است (فقط ۶ ماه). جدول بر اساس ' + (topSort === 'units' ? 'واحد فروش' : 'ارزش') + ' مرتب شده است.';
}

function renderAtcFixNote() {
  const fix = DATA.atc_fix;
  const el = $('#atc-fix-note');
  if (!fix) {
    el.textContent = 'در فایل خام ۴۶٫۲٪ ردیف‌های ۱۴۰۴ و ۵۱٫۷٪ ردیف‌های ۱۴۰۵ کد ATC نداشتند.';
    return;
  }
  el.textContent = 'در فایل خام ۴۶٫۲٪ ردیف‌های ۱۴۰۴ و ۵۱٫۷٪ ردیف‌های ۱۴۰۵ کد ATC نداشتند. ' +
    'پس از چهار دور تکمیل (نگاشت کد ژنریک، جست‌وجوی نام مادهٔ مؤثره، رفع ابهام بر اساس شکل/مسیر و جست‌وجو در ChEMBL و KEGG، و جست‌وجو در شاخص ATC/DDD سازمان جهانی بهداشت) سهم ردیف‌های بدون ATC به ' +
    pct(fix.after['1404'].blank_rows_pct) + ' در ۱۴۰۴ و ' + pct(fix.after['1405'].blank_rows_pct) +
    ' در نیمهٔ اول ۱۴۰۵ رسید (سهم ارزشی: ' + pct(fix.after['1404'].blank_value_pct) + ' و ' +
    pct(fix.after['1405'].blank_value_pct) + '). ' + fmt(fix.rows_filled) +
    ' ردیف تکمیل شد؛ ' + fmt(fix.generics_still_blank_count) +
    ' ژنریک هنوز در هیچ ردیفی ATC ندارند.';
}

function renderAtc() {
  const rows = DATA.atc_year.filter((r) => r.year === atcYear)
    .sort((a, b) => b.value_consumer - a.value_consumer);
  const tot = rows.reduce((a, r) => a + r.value_consumer, 0);
  $('#atc-bars').innerHTML = rows.map((r) => {
    const share = r.value_consumer / tot;
    const isBlank = r.atc_group === '(blank)';
    return '<div class="atc-row"><span>' + (isBlank ? 'بدون ATC' : 'گروه ' + escapeHtml(r.atc_group)) +
      '</span><span class="track"><span class="fill' + (isBlank ? ' blank' : '') +
      '" style="display:block;width:' + fmtDec(share * 100, 1) + '%"></span></span>' +
      '<span class="pct">' + pct(share) + '</span></div>';
  }).join('');
  const tableRows = rows.map((r) => Object.assign({ share: r.value_consumer / tot }, r));
  CURRENT.atc = tableRows;
  $('#atc-table tbody').innerHTML = tableRows.map((r) =>
    '<tr><td>' + (r.atc_group === '(blank)' ? '(blank) — بدون کد ATC' : escapeHtml(r.atc_group)) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / tot) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td></tr>').join('');
}

/* ================= MONTHLY TAB ================= */
function drawBarLineChart(svg, rows, opts) {
  /* rows: [{label, sublabel, value (bn Toman), units (bn), title}] — bars
     = value, line = units. opts: {W, H, valueLabel, unitsLabel} */
  const W = (opts && opts.W) || 960, H = (opts && opts.H) || 380;
  const L = 64, R = 64, T = 28, B = 64;
  const iw = W - L - R, ih = H - T - B;
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.innerHTML = '';
  if (!rows.length) return;
  const maxV = Math.max.apply(null, rows.map((r) => r.value));
  const maxU = Math.max.apply(null, rows.map((r) => r.units));
  const slot = iw / rows.length;
  const bw = Math.min(34, slot * 0.55);
  for (let i = 0; i <= 4; i++) {
    const v = maxV * i / 4;
    const y = T + ih - (ih * i / 4);
    svg.appendChild(svgEl('line', { x1: L, y1: y, x2: W - R, y2: y, stroke: '#e5e5e0', 'stroke-width': 1 }));
    svg.appendChild(svgText(L - 6, y + 4, fmtDec(v / 1000, 0), 'end'));
    svg.appendChild(svgText(W - R + 6, y + 4, fmtDec(maxU * i / 4, 1), 'start'));
  }
  svg.appendChild(svgText(16, T + 6, (opts && opts.valueLabel) || 'ارزش (هزار میلیارد تومان)', 'start'));
  svg.appendChild(svgText(W - 16, T + 6, (opts && opts.unitsLabel) || 'واحد (میلیارد عدد)', 'end'));
  const pts = [];
  rows.forEach((r, i) => {
    const cx = L + slot * i + slot / 2;
    const vh = maxV ? ih * r.value / maxV : 0;
    const bar = svgEl('rect', {
      x: cx - bw / 2, y: T + ih - vh, width: bw, height: Math.max(vh, 0),
      fill: '#0b5e4b', rx: 2,
    });
    bar.appendChild(svgTitle(r.title));
    svg.appendChild(bar);
    const uy = T + ih - (maxU ? ih * r.units / maxU : 0);
    pts.push(cx + ',' + uy.toFixed(1));
    svg.appendChild(svgEl('circle', { cx: cx, cy: uy, r: 3.5, fill: '#b3541e' }));
    svg.appendChild(svgText(cx, H - B + 18, r.label, 'middle'));
    if (r.sublabel) svg.appendChild(svgText(cx, H - B + 34, r.sublabel, 'middle'));
  });
  svg.appendChild(svgEl('polyline', {
    points: pts.join(' '), fill: 'none', stroke: '#b3541e', 'stroke-width': 2,
  }));
}

function renderMonthlyChart() {
  const rows = DATA.market_monthly.filter((r) => r.month !== null)
    .sort((a, b) => (a.year - b.year) || (a.month - b.month));
  drawBarLineChart($('#monthly-chart'), rows.map((r) => ({
    label: r.month_name_fa,
    sublabel: String(r.year),
    value: bnToman(r.value_consumer),
    units: bnUnits(r.units),
    title: r.month_name_fa + ' ' + r.year + ': ارزش ' +
      fmtDec(bnToman(r.value_consumer), 2) + ' میلیارد تومان، واحد ' + fmt(r.units),
  })));
}

function monthKey(y, m) { return y + '-' + m; }

function renderMonthSelect() {
  const sel = $('#month-select');
  const rows = DATA.market_monthly.filter((r) => r.month !== null)
    .sort((a, b) => (a.year - b.year) || (a.month - b.month));
  sel.innerHTML = rows.map((r) =>
    '<option value="' + monthKey(r.year, r.month) + '">' + r.month_name_fa + ' ' + r.year + '</option>').join('');
  sel.value = monthSel;
}

function renderMonthCards() {
  const parts = monthSel.split('-');
  const y = Number(parts[0]), m = Number(parts[1]);
  const r = DATA.market_monthly.find((x) => x.year === y && x.month === m);
  if (!r) { $('#month-cards').innerHTML = ''; return; }
  const avg = r.units ? r.value_consumer / r.units : null;
  $('#month-cards').innerHTML =
    yearCard('واحد فروش', fmt(r.units) + ' عدد', fmtDec(bnUnits(r.units), 2) + ' میلیارد عدد') +
    yearCard('ارزش مصرف‌کننده', fmtDec(bnToman(r.value_consumer), 2) + ' میلیارد تومان', 'داروخانه: ' + fmtDec(bnToman(r.value_pharmacy), 2) + ' میلیارد تومان') +
    yearCard('ارزش توزیع‌کننده', fmtDec(bnToman(r.value_distributor), 2) + ' میلیارد تومان', 'کد ژنریک یکتا: ' + fmt(r.distinct_generic_codes) + ' — برند یکتا: ' + fmt(r.distinct_brands)) +
    yearCard('میانگین قیمت هر واحد', avg ? fmt(Math.round(avg)) + ' ریال' : '—', avg ? fmtDec(avg / 10, 0) + ' تومان به ازای هر واحد' : '');
}

function renderMonthlyTable() {
  const rows = DATA.market_monthly.filter((r) => r.month !== null)
    .sort((a, b) => (a.year - b.year) || (a.month - b.month))
    .map((r) => Object.assign({}, r));
  CURRENT.monthly = rows;
  $('#monthly-table tbody').innerHTML = rows.map((r) =>
    '<tr data-month="' + monthKey(r.year, r.month) + '" class="clickable"><td>' + r.year + '</td><td>' + r.month_name_fa + '</td>' +
    '<td class="num">' + fmt(r.packages) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_pharmacy), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_distributor), 2) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td>' +
    '<td class="num">' + fmt(r.distinct_brands) + '</td></tr>').join('');
}

function renderBlankMonthNotes() {
  const blank = DATA.market_monthly.find((r) => r.month === null);
  const t04 = DATA.market_year_totals['1404'];
  const text = blank
    ? 'ردیف بدون ماه در ۱۴۰۴: ' + fmt(blank.units) + ' واحد (' +
      pct(blank.units / t04.units) + ' واحدهای سال) و ارزش ' +
      fmtDec(bnToman(blank.value_consumer), 2) + ' میلیارد تومان (' +
      pct(blank.value_consumer / t04.value_consumer) +
      ' ارزش سالانه) که در هیچ‌یک از ماه‌های بالا نیست و فقط در مجموع سالانه لحاظ شده است؛ بنابراین مجموع ۱۲ ردیف ماهانه کمتر از مجموع سالانه است.'
    : '';
  $('#blank-month-note').textContent = text;
  const cav = $('#cav-blank-month');
  if (blank) {
    cav.textContent =
      'در فایل ماهانهٔ ۱۴۰۴، ' + fmt(DATA.stats.yearbook_1404_monthly.month_blank_rows) +
      ' ردیف بدون ماه وجود دارد که ارزش آن‌ها (' +
      fmtDec(bnToman(blank.value_consumer), 2) + ' میلیارد تومان، ' +
      pct(blank.value_consumer / t04.value_consumer) + ' ارزش سالانه و ' +
      pct(blank.units / t04.units) + ' واحدهای سال) در هیچ‌یک از ردیف‌های ماهانه نیست و فقط در مجموع سالانه آمده است.';
  }
}

function renderCavAtc() {
  const fix = DATA.atc_fix;
  const el = $('#cav-atc');
  if (!fix) {
    el.textContent = 'بخش بزرگی از ردیف‌ها کد ATC خالی دارند (۴۶٫۲٪ ردیف‌ها در ۱۴۰۴ و ۵۱٫۷٪ در نیمهٔ اول ۱۴۰۵)؛ تحلیل ATC ناقص است.';
    return;
  }
  el.textContent = 'در فایل خام ۴۶٫۲٪ ردیف‌های ۱۴۰۴ و ۵۱٫۷٪ ردیف‌های نیمهٔ اول ۱۴۰۵ کد ATC نداشتند؛ ' +
    'پس از چهار دور تکمیل سهم ردیف‌های بدون ATC به ' +
    pct(fix.after['1404'].blank_rows_pct) + ' (۱۴۰۴) و ' + pct(fix.after['1405'].blank_rows_pct) +
    ' (۱۴۰۵) رسید، ولی سهم ارزشی بدون ATC هنوز ' + pct(fix.after['1404'].blank_value_pct) +
    ' (۱۴۰۴) و ' + pct(fix.after['1405'].blank_value_pct) + ' (۱۴۰۵) است؛ تحلیل ATC همچنان ناقص است.';
}

/* ================= DRUG SEARCH + DETAIL ================= */
const FA_MONTHS = ['', 'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];

function searchRowHtml(d) {
  return '<tr class="clickable" data-code="' + escapeHtml(String(d.generic_code)) + '">' +
    '<td class="cell-name">' + escapeHtml(d.generic_name) + '</td>' +
    '<td class="num">' + escapeHtml(String(d.generic_code)) + '</td>' +
    '<td class="num">' + escapeHtml(d.atc || '—') + '</td>' +
    '<td class="num">' + fmt(d.units_1404) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(d.value_consumer_1404), 2) + '</td>' +
    '<td class="num">' + fmt(d.units_1405_h1) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(d.value_consumer_1405_h1), 2) + '</td></tr>';
}

function doSearch() {
  const q = $('#drug-query').value.trim().toLowerCase();
  const tb = $('#search-table tbody');
  closeModal();
  if (!q) {
    tb.innerHTML = '<tr><td colspan="7" class="muted">برای دیدن نتایج، عبارتی را جست‌وجو کنید.</td></tr>';
    $('#search-count').textContent = '';
    CURRENT.search = [];
    return;
  }
  const hits = DATA.catalog.filter((d) =>
    (d.generic_name || '').toLowerCase().includes(q) ||
    String(d.generic_code).includes(q) ||
    (d.atc || '').toLowerCase().includes(q));
  $('#search-count').textContent = fmt(hits.length) + ' نتیجه';
  CURRENT.search = hits;
  const shown = hits.slice(0, 50);
  tb.innerHTML = shown.map(searchRowHtml).join('') +
    (hits.length > 50 ? '<tr><td colspan="7" class="muted">فقط ۵۰ نتیجهٔ اول نمایش داده شد؛ جست‌وجو را دقیق‌تر کنید.</td></tr>' : '');
}

function showDrugDetail(code) {
  const d = DATA.catalog.find((x) => String(x.generic_code) === String(code));
  if (!d) return;
  const avg04 = d.units_1404 ? d.value_consumer_1404 / d.units_1404 : null;
  const avg05 = d.units_1405_h1 ? d.value_consumer_1405_h1 / d.units_1405_h1 : null;
  const series = (DATA.generic_series && DATA.generic_series[String(code)]) || [];
  const monthly = series.filter((r) => r[1] !== 0)
    .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  const blankRows = series.filter((r) => r[1] === 0);
  const blankUnits = blankRows.reduce((a, r) => a + r[2], 0);
  const blankValue = blankRows.reduce((a, r) => a + r[3], 0);
  const brands = (DATA.generic_brands && DATA.generic_brands[String(code)]) || {};

  function brandTable(yearKey, label) {
    const list = brands[yearKey] || [];
    if (!list.length) return '<p class="muted">' + label + ': برندی ثبت نشده است.</p>';
    return '<h4>' + label + '</h4><div class="table-wrap"><table><thead><tr>' +
      '<th>#</th><th>برند</th><th>واحد فروش (عدد)</th>' +
      '<th>ارزش مصرف‌کننده (میلیارد تومان)</th><th>میانگین قیمت هر واحد (ریال)</th></tr></thead><tbody>' +
      list.map((b, i) =>
        '<tr><td class="num">' + (i + 1) + '</td><td>' + escapeHtml(b[0]) + '</td>' +
        '<td class="num">' + fmt(b[1]) + '</td>' +
        '<td class="num">' + fmtDec(bnToman(b[2]), 2) + '</td>' +
        '<td class="num">' + (b[1] ? fmt(Math.round(b[2] / b[1])) : '—') + '</td></tr>').join('') +
      '</tbody></table></div>';
  }

  const html =
    '<h3>' + escapeHtml(d.generic_name) + '</h3>' +
    '<dl>' +
    '<dt>کد ژنریک:</dt><dd>' + escapeHtml(String(d.generic_code)) + '</dd>' +
    '<dt>ATC:</dt><dd>' + escapeHtml(d.atc || '— (این ژنریک در هیچ ردیفی کد ATC ندارد)') + '</dd>' +
    '<dt>واحد فروش ۱۴۰۴:</dt><dd>' + fmt(d.units_1404) + ' عدد</dd>' +
    '<dt>ارزش مصرف‌کننده ۱۴۰۴:</dt><dd>' + fmtDec(bnToman(d.value_consumer_1404), 2) + ' میلیارد تومان</dd>' +
    '<dt>میانگین قیمت هر واحد ۱۴۰۴:</dt><dd>' + (avg04 ? fmt(Math.round(avg04)) + ' ریال (' + fmtDec(avg04 / 10, 0) + ' تومان)' : '—') + '</dd>' +
    '<dt>واحد فروش نیمهٔ اول ۱۴۰۵:</dt><dd>' + fmt(d.units_1405_h1) + ' عدد</dd>' +
    '<dt>ارزش مصرف‌کننده نیمهٔ اول ۱۴۰۵:</dt><dd>' + fmtDec(bnToman(d.value_consumer_1405_h1), 2) + ' میلیارد تومان</dd>' +
    '<dt>میانگین قیمت هر واحد ۱۴۰۵ (نیمهٔ اول):</dt><dd>' + (avg05 ? fmt(Math.round(avg05)) + ' ریال (' + fmtDec(avg05 / 10, 0) + ' تومان)' : '—') + '</dd>' +
    '</dl>' +
    '<h4>روند ماهانه</h4>' +
    '<div class="chart-box mini-chart"><svg id="drug-chart" viewBox="0 0 960 300" role="img" aria-label="روند ماهانهٔ دارو"></svg></div>' +
    '<div class="table-wrap"><table><thead><tr><th>سال</th><th>ماه</th><th>واحد فروش (عدد)</th>' +
    '<th>ارزش مصرف‌کننده (میلیارد تومان)</th><th>میانگین قیمت هر واحد (ریال)</th></tr></thead><tbody>' +
    (monthly.length ? monthly.map((r) =>
      '<tr><td>' + r[0] + '</td><td>' + FA_MONTHS[r[1]] + '</td>' +
      '<td class="num">' + fmt(r[2]) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r[3]), 2) + '</td>' +
      '<td class="num">' + (r[2] ? fmt(Math.round(r[3] / r[2])) : '—') + '</td></tr>').join('')
      : '<tr><td colspan="5" class="muted">سری ماهانه‌ای برای این دارو ثبت نشده است.</td></tr>') +
    '</tbody></table></div>' +
    (blankRows.length
      ? '<p class="note">ردیف بدون ماه در ۱۴۰۴ برای این دارو: ' + fmt(blankUnits) + ' واحد و ارزش ' +
        fmtDec(bnToman(blankValue), 2) + ' میلیارد تومان که فقط در مجموع سالانه لحاظ شده است.</p>'
      : '') +
    '<p class="note">میانگین قیمت = ارزش تقسیم بر واحد فروش (موزون با ترکیب فروش). ماه بدون ردیف یعنی در آن ماه فروشی برای این دارو ثبت نشده است.</p>' +
    brandTable('1404', 'پرفروش‌ترین برندها — سال ۱۴۰۴') +
    brandTable('1405', 'پرفروش‌ترین برندها — نیمهٔ اول ۱۴۰۵');
  openModal(html);
  if (monthly.length) {
    drawBarLineChart($('#drug-chart'), monthly.map((r) => ({
      label: FA_MONTHS[r[1]],
      sublabel: String(r[0]),
      value: bnToman(r[3]),
      units: bnUnits(r[2]),
      title: FA_MONTHS[r[1]] + ' ' + r[0] + ': ارزش ' +
        fmtDec(bnToman(r[3]), 2) + ' میلیارد تومان، واحد ' + fmt(r[2]),
    })), { W: 960, H: 300 });
  }
}

/* ================= PROVINCE TAB ================= */
function renderProvinceWarning() {
  const c = DATA.checks;
  function diff(m) {
    const d = c['1404_' + m + '_units_diff_prov_minus_nat'];
    const n = c['1404_' + m + '_national_units'];
    return signedPct(d / n);
  }
  $('#prov-diff-m1').textContent = diff('m01') + ' واحد';
  $('#prov-diff-m6').textContent = diff('m06') + ' واحد';
  $('#prov-diff-m12').textContent = diff('m12') + ' واحد';
  function vdiff(m) {
    const d = c['1404_' + m + '_value_diff_prov_minus_nat'];
    const n = c['1404_' + m + '_national_value_consumer'];
    return signedPct(d / n);
  }
  $('#prov-note').textContent = 'اختلاف ارزش مصرف‌کنندهٔ مجموع استانی نسبت به مجموع ملی: فروردین ' +
    vdiff('m01') + '، شهریور ' + vdiff('m06') + '، اسفند ' + vdiff('m12') + '.';
}

/* Generic horizontal bar chart for named rows (value_consumer desc) as
   HTML bars (not SVG): text stays at real size on a phone instead of
   shrinking with a 960-wide viewBox. getName(r) returns the label; the
   full name + numbers are kept in the title attribute. */
function drawNameBarChart(container, rows, getName) {
  if (!rows.length) { container.innerHTML = ''; return; }
  const maxV = Math.max.apply(null, rows.map((r) => r.value_consumer));
  container.innerHTML = rows.map((r) => {
    const name = getName(r);
    const share = maxV ? r.value_consumer / maxV : 0;
    return '<div class="name-bar-row" title="' + escapeHtml(name + ': ارزش ' +
      fmtDec(bnToman(r.value_consumer), 2) + ' میلیارد تومان، واحد ' + fmt(r.units)) + '">' +
      '<span class="nb-name">' + escapeHtml(name) + '</span>' +
      '<span class="nb-val">' + fmtDec(bnToman(r.value_consumer), 1) + '</span>' +
      '<span class="nb-track"><span class="nb-fill" style="width:' + fmtDec(share * 100, 1) + '%"></span></span>' +
      '</div>';
  }).join('');
}

function drawProvChart(container, rows) {
  drawNameBarChart(container, rows, (r) => r.province);
}

function renderProvinces() {
  const rows = DATA.province_month
    .filter((r) => r.year === 1404 && r.month === provMonth)
    .sort((a, b) => b.value_consumer - a.value_consumer);
  const totUnits = rows.reduce((a, r) => a + r.units, 0);
  const totValue = rows.reduce((a, r) => a + r.value_consumer, 0);
  const nat = DATA.market_monthly.find((r) => r.year === 1404 && r.month === provMonth);
  const monthName = rows.length ? rows[0].month_name_fa : '';
  $('#prov-cards').innerHTML =
    yearCard('مجموع استانی — واحد فروش (' + monthName + ' ۱۴۰۴)', fmt(totUnits) + ' عدد', fmtDec(bnUnits(totUnits), 2) + ' میلیارد عدد') +
    yearCard('مجموع استانی — ارزش مصرف‌کننده', fmtDec(bnToman(totValue), 2) + ' میلیارد تومان', '۳۱ استان') +
    (nat
      ? yearCard('اختلاف با مجموع ملی همان ماه', signedPct((totUnits - nat.units) / nat.units) + ' واحد', 'ارزش: ' + signedPct((totValue - nat.value_consumer) / nat.value_consumer))
      : '');
  drawProvChart($('#prov-chart'), rows);
  const tableRows = rows.map((r) => Object.assign({ rank: 0, share: r.value_consumer / totValue }, r));
  CURRENT.prov = tableRows;
  $('#prov-table tbody').innerHTML = tableRows.map((r, i) =>
    '<tr><td class="num">' + (i + 1) + '</td><td>' + escapeHtml(r.province) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / totValue) + '</td></tr>').join('');
}

/* ================= DISTRIBUTOR + MANUFACTURER TABS ================= */
function yearLabelFa(year) {
  return year === 1404 ? 'سال ۱۴۰۴' : 'نیمهٔ اول ۱۴۰۵';
}
function marketTotal(year) {
  return DATA.market_year_totals[year === 1404 ? '1404' : '1405_h1'];
}

/* Manufacturer display name: Latin name, plus Persian name in
   parentheses when the yearbook gives one. */
function manufName(r) {
  return r.manufacturer_fa ? r.manufacturer + ' (' + r.manufacturer_fa + ')' : r.manufacturer;
}

/* Persian name for a manufacturer given its Latin name (looked up in the
   manufacturer_year rows); null when the yearbook gives none. */
let MANUF_FA = null;
function manufFa(latin) {
  if (!MANUF_FA) {
    MANUF_FA = new Map();
    DATA.manufacturer_year.forEach((r) => {
      if (r.manufacturer_fa && !MANUF_FA.has(r.manufacturer)) MANUF_FA.set(r.manufacturer, r.manufacturer_fa);
    });
  }
  return MANUF_FA.get(latin) || null;
}
function manufDisplay(latin) {
  const fa = manufFa(latin);
  return fa ? latin + ' (' + fa + ')' : latin;
}

/* Counterpart cell: top counterpart company by value + how many distinct
   counterpart companies the row works with ("از N شرکت"). */
function counterpartCell(name, count) {
  if (!name) return '<td class="cell-name">—</td>';
  return '<td class="cell-name">' + escapeHtml(name) +
    (count > 1 ? ' <span class="muted">(از ' + fmt(count) + ' شرکت)</span>' : '') + '</td>';
}

function distRowHtml(r, i) {
  return '<tr class="clickable" data-name="' + escapeHtml(r.distributor) + '">' +
    '<td class="num">' + (i + 1) + '</td><td class="cell-name">' + escapeHtml(r.distributor) + '</td>' +
    counterpartCell(manufDisplay(r.top_manufacturer), r.top_manufacturer_count) +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_distributor), 2) + '</td>' +
    '<td class="num">' + pct(r.share) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td>' +
    '<td class="num">' + fmt(r.distinct_brands) + '</td></tr>';
}

function manufRowHtml(r, i) {
  return '<tr class="clickable" data-name="' + escapeHtml(r.manufacturer) + '">' +
    '<td class="num">' + (i + 1) + '</td><td class="cell-name">' + escapeHtml(manufName(r)) + '</td>' +
    counterpartCell(r.top_distributor, r.top_distributor_count) +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_distributor), 2) + '</td>' +
    '<td class="num">' + pct(r.share) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td>' +
    '<td class="num">' + fmt(r.distinct_brands) + '</td></tr>';
}

function renderDistributors() {
  const yearRows = DATA.distributor_year.filter((r) => r.year === distYear)
    .sort((a, b) => b.value_consumer - a.value_consumer);
  const total = marketTotal(distYear).value_consumer;
  const totUnits = yearRows.reduce((a, r) => a + r.units, 0);
  const totValue = yearRows.reduce((a, r) => a + r.value_consumer, 0);
  const top1 = yearRows[0];
  $('#dist-cards').innerHTML =
    yearCard('مجموع واحد فروش — ' + yearLabelFa(distYear), fmt(totUnits) + ' عدد', fmtDec(bnUnits(totUnits), 2) + ' میلیارد عدد') +
    yearCard('مجموع ارزش مصرف‌کننده — ' + yearLabelFa(distYear), fmtDec(bnToman(totValue), 2) + ' میلیارد تومان', fmt(yearRows.length) + ' توزیع‌کننده') +
    (top1
      ? yearCard('توزیع‌کنندهٔ اول از نظر ارزش', escapeHtml(top1.distributor), 'سهم ارزشی: ' + pct(top1.value_consumer / total) + ' — ارزش ' + fmtDec(bnToman(top1.value_consumer), 1) + ' میلیارد تومان')
      : '');
  drawNameBarChart($('#dist-chart'), yearRows.slice(0, 20), (r) => r.distributor);
  const q = $('#dist-query').value.trim().toLowerCase();
  let rows = yearRows;
  if (q) rows = yearRows.filter((r) => r.distributor.toLowerCase().includes(q));
  const tableRows = rows.map((r) => Object.assign({ rank: 0, share: r.value_consumer / total }, r));
  /* re-apply the value/units sort toggle on the (possibly filtered) rows */
  tableRows.sort((a, b) => distSort === 'units' ? b.units - a.units : b.value_consumer - a.value_consumer);
  CURRENT.dist = tableRows;
  $('#dist-count').textContent = q ? fmt(rows.length) + ' نتیجه' : '';
  const shown = q ? tableRows.slice(0, 50) : tableRows;
  $('#dist-table tbody').innerHTML = shown.map(distRowHtml).join('') +
    (q && tableRows.length > 50
      ? '<tr><td colspan="9" class="muted">فقط ۵۰ نتیجهٔ اول نمایش داده شد؛ جست‌وجو را دقیق‌تر کنید.</td></tr>'
      : '');
  closeModal();
}

function renderManufacturers() {
  const yearRows = DATA.manufacturer_year.filter((r) => r.year === manufYear)
    .sort((a, b) => b.value_consumer - a.value_consumer);
  const total = marketTotal(manufYear).value_consumer;
  const totUnits = yearRows.reduce((a, r) => a + r.units, 0);
  const totValue = yearRows.reduce((a, r) => a + r.value_consumer, 0);
  const top1 = yearRows[0];
  $('#manuf-cards').innerHTML =
    yearCard('مجموع واحد فروش — ' + yearLabelFa(manufYear), fmt(totUnits) + ' عدد', fmtDec(bnUnits(totUnits), 2) + ' میلیارد عدد') +
    yearCard('مجموع ارزش مصرف‌کننده — ' + yearLabelFa(manufYear), fmtDec(bnToman(totValue), 2) + ' میلیارد تومان', fmt(yearRows.length) + ' تولیدکننده') +
    (top1
      ? yearCard('تولیدکنندهٔ اول از نظر ارزش', escapeHtml(manufName(top1)), 'سهم ارزشی: ' + pct(top1.value_consumer / total) + ' — ارزش ' + fmtDec(bnToman(top1.value_consumer), 1) + ' میلیارد تومان')
      : '');
  drawNameBarChart($('#manuf-chart'), yearRows.slice(0, 20), (r) => r.manufacturer);
  const q = $('#manuf-query').value.trim().toLowerCase();
  let rows = yearRows;
  if (q) {
    rows = yearRows.filter((r) =>
      r.manufacturer.toLowerCase().includes(q) ||
      (r.manufacturer_fa || '').toLowerCase().includes(q));
  }
  const tableRows = rows.map((r) => Object.assign({ rank: 0, share: r.value_consumer / total }, r));
  /* re-apply the value/units sort toggle on the (possibly filtered) rows */
  tableRows.sort((a, b) => manufSort === 'units' ? b.units - a.units : b.value_consumer - a.value_consumer);
  CURRENT.manuf = tableRows;
  $('#manuf-count').textContent = q ? fmt(rows.length) + ' نتیجه' : '';
  const shown = q ? tableRows.slice(0, 50) : tableRows;
  $('#manuf-table tbody').innerHTML = shown.map(manufRowHtml).join('') +
    (q && tableRows.length > 50
      ? '<tr><td colspan="9" class="muted">فقط ۵۰ نتیجهٔ اول نمایش داده شد؛ جست‌وجو را دقیق‌تر کنید.</td></tr>'
      : '');
  closeModal();
}

/* Small top-5 table for a distributor/manufacturer detail panel.
   list: [[name, units, value_consumer, top_counterpart, counterpart_count]]
   (older 3-element entries still render, with '—' for the counterpart).
   counterpartLabel: header for the counterpart-company column, e.g.
   'تولیدکننده' in a distributor detail, 'توزیع‌کننده' in a manufacturer
   detail; omit it for tables without a counterpart (drug brands).
   counterpartIsManuf: true when the counterpart is a manufacturer (so its
   Persian name is shown when the yearbook gives one). */
function topListTable(list, emptyMsg, counterpartLabel, counterpartIsManuf) {
  if (!list || !list.length) return '<p class="muted">' + emptyMsg + '</p>';
  return '<div class="table-wrap"><table><thead><tr>' +
    '<th>#</th><th>نام</th>' +
    (counterpartLabel ? '<th>' + counterpartLabel + '</th>' : '') +
    '<th>واحد فروش (عدد)</th>' +
    '<th>ارزش (میلیارد تومان)</th><th>میانگین قیمت هر واحد (ریال)</th></tr></thead><tbody>' +
    list.map((g, i) =>
      '<tr><td class="num">' + (i + 1) + '</td><td class="cell-name">' + escapeHtml(g[0]) + '</td>' +
      (counterpartLabel
        ? counterpartCell(g.length > 3 && g[3] ? (counterpartIsManuf ? manufDisplay(g[3]) : g[3]) : null, g[4])
        : '') +
      '<td class="num">' + fmt(g[1]) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(g[2]), 2) + '</td>' +
      '<td class="num">' + (g[1] ? fmt(Math.round(g[2] / g[1])) : '—') + '</td></tr>').join('') +
    '</tbody></table></div>';
}

function showNameDetail(kind, name, year) {
  const isDist = kind === 'dist';
  const rows = isDist ? DATA.distributor_year : DATA.manufacturer_year;
  const key = isDist ? 'distributor' : 'manufacturer';
  const r = rows.find((x) => x.year === year && x[key] === name);
  if (!r) return;
  const total = marketTotal(year).value_consumer;
  const avg = r.units ? r.value_consumer / r.units : null;
  const displayName = isDist ? r.distributor : manufName(r);
  const html =
    '<h3>' + escapeHtml(displayName) + '</h3>' +
    '<dl>' +
    '<dt>دوره:</dt><dd>' + yearLabelFa(year) + '</dd>' +
    '<dt>واحد فروش:</dt><dd>' + fmt(r.units) + ' عدد</dd>' +
    '<dt>ارزش مصرف‌کننده:</dt><dd>' + fmtDec(bnToman(r.value_consumer), 2) + ' میلیارد تومان</dd>' +
    '<dt>ارزش توزیع‌کننده:</dt><dd>' + fmtDec(bnToman(r.value_distributor), 2) + ' میلیارد تومان</dd>' +
    '<dt>ارزش داروخانه:</dt><dd>' + fmtDec(bnToman(r.value_pharmacy), 2) + ' میلیارد تومان</dd>' +
    '<dt>تعداد بسته:</dt><dd>' + fmt(r.packages) + '</dd>' +
    '<dt>کد ژنریک یکتا:</dt><dd>' + fmt(r.distinct_generic_codes) + '</dd>' +
    '<dt>برند یکتا:</dt><dd>' + fmt(r.distinct_brands) + '</dd>' +
    '<dt>میانگین قیمت هر واحد:</dt><dd>' + (avg ? fmt(Math.round(avg)) + ' ریال (' + fmtDec(avg / 10, 0) + ' تومان)' : '—') + '</dd>' +
    '<dt>سهم ارزشی از کل بازار:</dt><dd>' + pct(r.value_consumer / total) + '</dd>' +
    '</dl>' +
    '<h4>پرفروش‌ترین ژنریک‌ها</h4>' +
    topListTable(r.top_generics, 'ژنریکی ثبت نشده است.', isDist ? 'تولیدکننده' : 'توزیع‌کننده', isDist) +
    '<h4>پرفروش‌ترین برندها</h4>' +
    topListTable(r.top_brands, 'برندی ثبت نشده است.', isDist ? 'تولیدکننده' : 'توزیع‌کننده', isDist) +
    '<p class="note">میانگین قیمت = ارزش تقسیم بر واحد فروش (موزون با ترکیب فروش). سهم ارزشی نسبت به کل ارزش مصرف‌کنندهٔ ' + yearLabelFa(year) + ' محاسبه شده است. ستون «' + (isDist ? 'تولیدکننده' : 'توزیع‌کننده') + '» شرکت طرف مقابل با بیشترین ارزش برای آن قلم است؛ عدد داخل پرانتز تعداد شرکت‌های طرف مقابل آن قلم است.</p>';
  openModal(html);
}

function showDistDetail(name, year) { showNameDetail('dist', name, year); }
function showManufDetail(name, year) { showNameDetail('manuf', name, year); }

/* ================= INIT ================= */
document.addEventListener('DOMContentLoaded', () => {
  load();
  /* detail modal: close via the × button, a click on the backdrop, or Escape */
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });
  /* AI */
  $('#ai-send').addEventListener('click', submitAi);
  $('#ai-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitAi(); });
  $$('#ai-chips .chip').forEach((b) => b.addEventListener('click', () => {
    $('#ai-input').value = b.getAttribute('data-q');
    submitAi();
  }));
  /* main tabs */
  $$('.main-tab').forEach((b) => b.addEventListener('click', () => switchTab(b.getAttribute('data-tab'))));
  /* annual toggles */
  $$('[data-top-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-top-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    topYear = b.getAttribute('data-top-year');
    renderTop();
  }));
  $$('[data-top-sort]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-top-sort]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    topSort = b.getAttribute('data-top-sort');
    renderTop();
  }));
  $$('[data-atc-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-atc-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    atcYear = Number(b.getAttribute('data-atc-year'));
    renderAtc();
  }));
  /* monthly */
  $('#month-select').addEventListener('change', (e) => {
    monthSel = e.target.value;
    renderMonthCards();
    $$('#monthly-table tbody tr').forEach((tr) =>
      tr.classList.toggle('selected', tr.getAttribute('data-month') === monthSel));
  });
  $('#monthly-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-month]');
    if (!tr) return;
    monthSel = tr.getAttribute('data-month');
    $('#month-select').value = monthSel;
    renderMonthCards();
    $$('#monthly-table tbody tr').forEach((x) =>
      x.classList.toggle('selected', x === tr));
  });
  /* drug search */
  $('#drug-query').addEventListener('input', doSearch);
  $('#search-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr.clickable');
    if (tr) showDrugDetail(tr.getAttribute('data-code'));
  });
  /* provinces */
  $$('[data-prov-month]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-prov-month]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    provMonth = Number(b.getAttribute('data-prov-month'));
    renderProvinces();
  }));
  /* distributors */
  $$('[data-dist-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-dist-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    distYear = Number(b.getAttribute('data-dist-year'));
    renderDistributors();
  }));
  $$('[data-dist-sort]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-dist-sort]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    distSort = b.getAttribute('data-dist-sort');
    renderDistributors();
  }));
  $('#dist-query').addEventListener('input', renderDistributors);
  $('#dist-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr.clickable');
    if (tr) showDistDetail(tr.getAttribute('data-name'), distYear);
  });
  /* manufacturers */
  $$('[data-manuf-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-manuf-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    manufYear = Number(b.getAttribute('data-manuf-year'));
    renderManufacturers();
  }));
  $$('[data-manuf-sort]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-manuf-sort]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    manufSort = b.getAttribute('data-manuf-sort');
    renderManufacturers();
  }));
  $('#manuf-query').addEventListener('input', renderManufacturers);
  $('#manuf-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr.clickable');
    if (tr) showManufDetail(tr.getAttribute('data-name'), manufYear);
  });
  /* sortable tables */
  makeSortable($('#top-table'),
    () => CURRENT.top || [],
    (r, i) => topRowHtml(DATA.market_year_totals[topYear === '1404' ? '1404' : '1405_h1'].value_consumer)(Object.assign({}, r, { rank: i + 1 }), i));
  makeSortable($('#atc-table'),
    () => CURRENT.atc || [],
    (r) => '<tr><td>' + (r.atc_group === '(blank)' ? '(blank) — بدون کد ATC' : escapeHtml(r.atc_group)) + '</td>' +
      '<td class="num">' + fmt(r.units) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
      '<td class="num">' + pct(r.share) + '</td>' +
      '<td class="num">' + fmt(r.distinct_generic_codes) + '</td></tr>');
  makeSortable($('#monthly-table'),
    () => CURRENT.monthly || [],
    (r) => '<tr data-month="' + monthKey(r.year, r.month) + '" class="clickable' + (monthKey(r.year, r.month) === monthSel ? ' selected' : '') + '"><td>' + r.year + '</td><td>' + r.month_name_fa + '</td>' +
      '<td class="num">' + fmt(r.packages) + '</td>' +
      '<td class="num">' + fmt(r.units) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r.value_pharmacy), 2) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r.value_distributor), 2) + '</td>' +
      '<td class="num">' + fmt(r.distinct_generic_codes) + '</td>' +
      '<td class="num">' + fmt(r.distinct_brands) + '</td></tr>');
  makeSortable($('#search-table'),
    () => CURRENT.search || [],
    (r) => searchRowHtml(r));
  makeSortable($('#prov-table'),
    () => CURRENT.prov || [],
    (r, i) => '<tr><td class="num">' + (i + 1) + '</td><td>' + escapeHtml(r.province) + '</td>' +
      '<td class="num">' + fmt(r.units) + '</td>' +
      '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
      '<td class="num">' + pct(r.share) + '</td></tr>');
  makeSortable($('#dist-table'),
    () => CURRENT.dist || [],
    (r, i) => distRowHtml(r, i));
  makeSortable($('#manuf-table'),
    () => CURRENT.manuf || [],
    (r, i) => manufRowHtml(r, i));
});
