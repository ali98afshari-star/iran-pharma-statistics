/* Dashboard for Iran pharma statistics. Reads ./data.json (same folder).
   All numbers come from that file; nothing is hard-coded except labels. */
'use strict';

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

let DATA = null;
let topYear = '1404';
let atcYear = 1404;
let provMonth = 1;

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
  renderKeyCards();
  renderMonthly();
  renderTop();
  renderAtcWarning();
  renderAtc();
  renderProvinceWarning();
  renderProvinces();
  renderBlankMonthCaveat();
}

function renderKeyCards() {
  const t04 = DATA.market_year_totals['1404'];
  const t05 = DATA.market_year_totals['1405_h1'];
  const avg04 = t04.value_consumer / t04.units;   /* Rial per unit */
  const avg05 = t05.value_consumer / t05.units;   /* Rial per unit */
  const ratio = avg05 / avg04;
  const cards = [
    { label: 'واحد فروش — سال ۱۴۰۴ (۱۲ ماه)', value: fmt(t04.units) + ' عدد', sub: fmtDec(bnUnits(t04.units), 2) + ' میلیارد عدد' },
    { label: 'ارزش مصرف‌کننده — سال ۱۴۰۴', value: fmtDec(bnToman(t04.value_consumer), 2) + ' میلیارد تومان', sub: 'معادل ' + fmtDec(t04.value_consumer / 1e9, 0) + ' میلیارد ریال' },
    { label: 'واحد فروش — نیمهٔ اول ۱۴۰۵ (۶ ماه)', value: fmt(t05.units) + ' عدد', sub: fmtDec(bnUnits(t05.units), 2) + ' میلیارد عدد' },
    { label: 'ارزش مصرف‌کننده — نیمهٔ اول ۱۴۰۵', value: fmtDec(bnToman(t05.value_consumer), 2) + ' میلیارد تومان', sub: 'بیش از کل ارزش سال ۱۴۰۴، با نیمی از واحدها' },
    { label: 'کد ژنریک یکتا در کاتالوگ', value: fmt(DATA.catalog.length), sub: 'داروهای دارای فروش در ۱۴۰۴ یا نیمهٔ اول ۱۴۰۵' },
  ];
  $('#key-cards').innerHTML = cards.map((c) =>
    '<div class="card"><div class="label">' + c.label + '</div><div class="value">' + c.value +
    '</div><div class="sub">' + c.sub + '</div></div>').join('');
  $('#price-note').textContent =
    'میانگین قیمت هر واحد (ارزش تقسیم بر واحد فروش): ۱۴۰۴ برابر ' + fmt(Math.round(avg04)) +
    ' ریال (' + fmtDec(avg04 / 10, 0) + ' تومان) و نیمهٔ اول ۱۴۰۵ برابر ' + fmt(Math.round(avg05)) +
    ' ریال (' + fmtDec(avg05 / 10, 0) + ' تومان) است — یعنی حدود ' + fmtDec(ratio, 2) +
    ' برابر. ارزش نیمهٔ اول ۱۴۰۵ با وجود نیمی از واحدها، از کل ارزش سال ۱۴۰۴ بیشتر است.';
}

function renderMonthly() {
  const rows = DATA.market_monthly.filter((r) => r.month !== null)
    .sort((a, b) => (a.year - b.year) || (a.month - b.month));
  const blank = DATA.market_monthly.find((r) => r.month === null);
  const t04 = DATA.market_year_totals['1404'];

  /* ---- SVG chart: bars = value (thousand bn Toman), line = units (bn) ---- */
  const svg = $('#monthly-chart');
  const NS = 'http://www.w3.org/2000/svg';
  svg.innerHTML = '';
  const W = 960, H = 380, L = 64, R = 64, T = 24, B = 64;
  const iw = W - L - R, ih = H - T - B;
  const maxV = Math.max.apply(null, rows.map((r) => bnToman(r.value_consumer)));
  const maxU = Math.max.apply(null, rows.map((r) => bnUnits(r.units)));
  const slot = iw / rows.length;
  const bw = Math.min(34, slot * 0.55);
  function el(tag, attrs) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }
  function text(x, y, s, anchor, cls) {
    const t = el('text', { x: x, y: y, 'text-anchor': anchor || 'middle', 'font-size': 11, fill: '#555' });
    if (cls) t.setAttribute('class', cls);
    t.textContent = s;
    return t;
  }
  /* gridlines + y labels (value axis, thousand bn Toman) */
  for (let i = 0; i <= 4; i++) {
    const v = maxV * i / 4;
    const y = T + ih - (ih * i / 4);
    svg.appendChild(el('line', { x1: L, y1: y, x2: W - R, y2: y, stroke: '#e5e5e0', 'stroke-width': 1 }));
    svg.appendChild(text(L - 6, y + 4, fmtDec(v / 1000, 0), 'end'));
    svg.appendChild(text(W - R + 6, y + 4, fmtDec(maxU * i / 4, 1), 'start'));
  }
  svg.appendChild(text(16, T + 8, 'ارزش (هزار میلیارد تومان)', 'start'));
  svg.appendChild(text(W - 16, T + 8, 'واحد (میلیارد عدد)', 'end'));
  /* bars + line */
  const pts = [];
  rows.forEach((r, i) => {
    const cx = L + slot * i + slot / 2;
    const vh = ih * bnToman(r.value_consumer) / maxV;
    const bar = el('rect', {
      x: cx - bw / 2, y: T + ih - vh, width: bw, height: vh,
      fill: '#0b5e4b', rx: 2,
    });
    const title = el('title');
    title.textContent = r.month_name_fa + ' ' + r.year + ': ارزش ' +
      fmtDec(bnToman(r.value_consumer), 2) + ' میلیارد تومان، واحد ' + fmt(r.units);
    bar.appendChild(title);
    svg.appendChild(bar);
    const uy = T + ih - ih * bnUnits(r.units) / maxU;
    pts.push(cx + ',' + uy.toFixed(1));
    svg.appendChild(el('circle', { cx: cx, cy: uy, r: 3.5, fill: '#b3541e' }));
    svg.appendChild(text(cx, H - B + 18, r.month_name_fa, 'middle'));
    svg.appendChild(text(cx, H - B + 34, String(r.year), 'middle'));
  });
  svg.appendChild(el('polyline', {
    points: pts.join(' '), fill: 'none', stroke: '#b3541e', 'stroke-width': 2,
  }));

  /* ---- table ---- */
  const tb = $('#monthly-table tbody');
  tb.innerHTML = rows.map((r) =>
    '<tr><td>' + r.year + '</td><td>' + r.month_name_fa + '</td>' +
    '<td class="num">' + fmt(r.packages) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_pharmacy), 2) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_distributor), 2) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td>' +
    '<td class="num">' + fmt(r.distinct_brands) + '</td></tr>').join('');

  if (blank) {
    $('#blank-month-note').textContent =
      'ردیف بدون ماه در ۱۴۰۴: ' + fmt(blank.units) + ' واحد (' +
      pct(blank.units / t04.units) + ' واحدهای سال) و ارزش ' +
      fmtDec(bnToman(blank.value_consumer), 2) + ' میلیارد تومان (' +
      pct(blank.value_consumer / t04.value_consumer) +
      ' ارزش سالانه) که در هیچ‌یک از ماه‌های بالا نیست و فقط در مجموع سالانه لحاظ شده است؛ بنابراین مجموع ۱۲ ردیف ماهانه کمتر از مجموع سالانه است.';
  }
}

function renderTop() {
  const key = topYear === '1404' ? 'top_generics_1404_by_value' : 'top_generics_1405_h1_by_value';
  const totalKey = topYear === '1404' ? '1404' : '1405_h1';
  const total = DATA.market_year_totals[totalKey].value_consumer;
  const rows = DATA[key];
  $('#top-table tbody').innerHTML = rows.map((r, i) =>
    '<tr><td class="num">' + (i + 1) + '</td><td>' + escapeHtml(r.generic_name) + '</td>' +
    '<td class="num">' + escapeHtml(String(r.generic_code)) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / total) + '</td></tr>').join('');
  $('#top-note').textContent = topYear === '1404'
    ? 'سهم ارزشی نسبت به کل ارزش مصرف‌کنندهٔ سال ۱۴۰۴ محاسبه شده است.'
    : 'سهم ارزشی نسبت به کل ارزش مصرف‌کنندهٔ نیمهٔ اول ۱۴۰۵ محاسبه شده است (فقط ۶ ماه).';
}

function renderAtcWarning() {
  const s04 = DATA.stats.yearbook_1404_monthly;
  const s05 = DATA.stats.yearbook_1405_monthly;
  $('#atc-blank-1404').textContent = pct(s04.blank_atc_share);
  $('#atc-blank-1405').textContent = pct(s05.blank_atc_share);
  for (const y of [1404, 1405]) {
    const rows = DATA.atc_year.filter((r) => r.year === y);
    const tot = rows.reduce((a, r) => a + r.value_consumer, 0);
    const blank = rows.find((r) => r.atc_group === '(blank)');
    $('#atc-blank-value-' + y).textContent = pct(blank.value_consumer / tot);
  }
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
  $('#atc-table tbody').innerHTML = rows.map((r) =>
    '<tr><td>' + (r.atc_group === '(blank)' ? '(blank) — بدون کد ATC' : escapeHtml(r.atc_group)) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / tot) + '</td>' +
    '<td class="num">' + fmt(r.distinct_generic_codes) + '</td></tr>').join('');
}

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

function renderProvinces() {
  const rows = DATA.province_month
    .filter((r) => r.year === 1404 && r.month === provMonth)
    .sort((a, b) => b.value_consumer - a.value_consumer);
  const tot = rows.reduce((a, r) => a + r.value_consumer, 0);
  $('#prov-table tbody').innerHTML = rows.map((r, i) =>
    '<tr><td class="num">' + (i + 1) + '</td><td>' + escapeHtml(r.province) + '</td>' +
    '<td class="num">' + fmt(r.units) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(r.value_consumer), 2) + '</td>' +
    '<td class="num">' + pct(r.value_consumer / tot) + '</td></tr>').join('');
}

function renderBlankMonthCaveat() {
  const blank = DATA.market_monthly.find((r) => r.month === null);
  const t04 = DATA.market_year_totals['1404'];
  if (!blank) return;
  $('#cav-blank-month').textContent =
    'در فایل ماهانهٔ ۱۴۰۴، ' + fmt(DATA.stats.yearbook_1404_monthly.month_blank_rows) +
    ' ردیف بدون ماه وجود دارد که ارزش آن‌ها (' +
    fmtDec(bnToman(blank.value_consumer), 2) + ' میلیارد تومان، ' +
    pct(blank.value_consumer / t04.value_consumer) + ' ارزش سالانه و ' +
    pct(blank.units / t04.units) + ' واحدهای سال) در هیچ‌یک از ردیف‌های ماهانه نیست و فقط در مجموع سالانه آمده است.';
}

/* ---- drug search ---- */
function doSearch() {
  const q = $('#drug-query').value.trim().toLowerCase();
  const tb = $('#search-table tbody');
  $('#drug-detail').hidden = true;
  if (!q) {
    tb.innerHTML = '<tr><td colspan="7" class="muted">برای دیدن نتایج، عبارتی را جست‌وجو کنید.</td></tr>';
    $('#search-count').textContent = '';
    return;
  }
  const hits = DATA.catalog.filter((d) =>
    d.generic_name.toLowerCase().includes(q) ||
    String(d.generic_code).includes(q) ||
    (d.atc || '').toLowerCase().includes(q));
  $('#search-count').textContent = fmt(hits.length) + ' نتیجه';
  const shown = hits.slice(0, 50);
  tb.innerHTML = shown.map((d) =>
    '<tr class="clickable" data-code="' + escapeHtml(String(d.generic_code)) + '">' +
    '<td>' + escapeHtml(d.generic_name) + '</td>' +
    '<td class="num">' + escapeHtml(String(d.generic_code)) + '</td>' +
    '<td class="num">' + escapeHtml(d.atc || '—') + '</td>' +
    '<td class="num">' + fmt(d.units_1404) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(d.value_consumer_1404), 2) + '</td>' +
    '<td class="num">' + fmt(d.units_1405_h1) + '</td>' +
    '<td class="num">' + fmtDec(bnToman(d.value_consumer_1405_h1), 2) + '</td></tr>').join('') +
    (hits.length > 50 ? '<tr><td colspan="7" class="muted">فقط ۵۰ نتیجهٔ اول نمایش داده شد؛ جست‌وجو را دقیق‌تر کنید.</td></tr>' : '');
}

function showDrugDetail(code) {
  const d = DATA.catalog.find((x) => String(x.generic_code) === String(code));
  if (!d) return;
  const avg04 = d.units_1404 ? d.value_consumer_1404 / d.units_1404 : null;
  const avg05 = d.units_1405_h1 ? d.value_consumer_1405_h1 / d.units_1405_h1 : null;
  const panel = $('#drug-detail');
  panel.innerHTML =
    '<h3>' + escapeHtml(d.generic_name) + '</h3>' +
    '<dl>' +
    '<dt>کد ژنریک:</dt><dd>' + escapeHtml(String(d.generic_code)) + '</dd>' +
    '<dt>ATC:</dt><dd>' + escapeHtml(d.atc || '—') + '</dd>' +
    '<dt>واحد فروش ۱۴۰۴:</dt><dd>' + fmt(d.units_1404) + ' عدد</dd>' +
    '<dt>ارزش مصرف‌کننده ۱۴۰۴:</dt><dd>' + fmtDec(bnToman(d.value_consumer_1404), 2) + ' میلیارد تومان</dd>' +
    '<dt>میانگین قیمت هر واحد ۱۴۰۴:</dt><dd>' + (avg04 ? fmt(Math.round(avg04)) + ' ریال (' + fmtDec(avg04 / 10, 0) + ' تومان)' : '—') + '</dd>' +
    '<dt>واحد فروش نیمهٔ اول ۱۴۰۵:</dt><dd>' + fmt(d.units_1405_h1) + ' عدد</dd>' +
    '<dt>ارزش مصرف‌کننده نیمهٔ اول ۱۴۰۵:</dt><dd>' + fmtDec(bnToman(d.value_consumer_1405_h1), 2) + ' میلیارد تومان</dd>' +
    '<dt>میانگین قیمت هر واحد ۱۴۰۵ (نیمهٔ اول):</dt><dd>' + (avg05 ? fmt(Math.round(avg05)) + ' ریال (' + fmtDec(avg05 / 10, 0) + ' تومان)' : '—') + '</dd>' +
    '</dl>' +
    '<p class="note">سری ماهانهٔ این دارو در این داشبورد نیست؛ در مخزن، در فایل <code>data/aggregates/generic_monthly.parquet</code> موجود است. میانگین قیمت = ارزش تقسیم بر واحد فروش (موزون با ترکیب فروش).</p>';
  panel.hidden = false;
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

document.addEventListener('DOMContentLoaded', () => {
  load();
  $('#drug-query').addEventListener('input', doSearch);
  $('#search-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr.clickable');
    if (tr) showDrugDetail(tr.getAttribute('data-code'));
  });
  $$('[data-top-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-top-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    topYear = b.getAttribute('data-top-year');
    renderTop();
  }));
  $$('[data-atc-year]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-atc-year]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    atcYear = Number(b.getAttribute('data-atc-year'));
    renderAtc();
  }));
  $$('[data-prov-month]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-prov-month]').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    provMonth = Number(b.getAttribute('data-prov-month'));
    renderProvinces();
  }));
});
