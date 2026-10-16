/* NEXUS front end. No frameworks, no CDN scripts.
   Security rule: every string that comes from the API (complaint, model text, record fields, errors)
   is rendered with textContent / createTextNode / setAttribute. There is no innerHTML anywhere,
   SVG is built with createElementNS, and only same-origin relative URLs are fetched. */
(() => {
'use strict';

// =================================================================== dom helpers
const $ = (s, r = document) => r.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';

function setProps(n, props) {
  if (!props) return n;
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.setAttribute('class', v);
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'style' || k === 'innerHTML' || k === 'outerHTML') throw new Error('forbidden prop: ' + k);
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  return n;
}
function addKids(n, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}
const el = (tag, props, ...kids) => addKids(setProps(document.createElement(tag), props), kids);
const svg = (tag, props, ...kids) => addKids(setProps(document.createElementNS(SVGNS, tag), props), kids);

const mqReduce = window.matchMedia('(prefers-reduced-motion: reduce)');
const reduced = () => mqReduce.matches;

// =================================================================== hand-drawn path generators
// (lifted from the doodle engine: jittered lines, sloppy boxes, circles, checks, sparks)
function prng(seed) {
  let s = (Math.abs(Math.floor(seed)) % 2147483646) + 1;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}
const n1 = v => Math.round(v * 10) / 10;
const jit = (r, a) => (r() - 0.5) * 2 * a;
const P = (x, y) => `${n1(x)} ${n1(y)}`;
function edge(r, x1, y1, x2, y2, bulge) {
  return `M${P(x1 + jit(r, 1.2), y1 + jit(r, 1.2))} Q${P((x1 + x2) / 2 + jit(r, bulge), (y1 + y2) / 2 + jit(r, bulge))} ${P(x2 + jit(r, 1.2), y2 + jit(r, 1.2))}`;
}
function boxPath(r, w, h, o = 4) {
  const a = () => o * r();
  return [edge(r, -a(), 0, w + a(), 0, 2), edge(r, w, -a(), w, h + a(), 2), edge(r, w + a(), h, -a(), h, 2), edge(r, 0, h + a(), 0, -a(), 2)].join(' ');
}
const underlinePath = (r, w) => `${edge(r, 2, 6, w - 2, 5, 3)} ${edge(r, w * 0.08, 11, w * 0.7, 10, 2)}`;
function circlePath(r, cx, cy, rad, turn = 1.08, a0 = -2.2) {
  const n = 36; let d = '';
  for (let i = 0; i <= n; i++) {
    const a = a0 + (i / n) * Math.PI * 2 * turn, rr = rad * (1 + jit(r, 0.02) + (i / n) * 0.04);
    d += (i ? ' L' : 'M') + P(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
  }
  return d;
}
const checkPath = (x, y, s) => `M${P(x, y + s * 0.5)} L${P(x + s * 0.35, y + s * 0.88)} L${P(x + s, y)}`;
const crossPath = (r, x, y, s) => `${edge(r, x, y, x + s, y + s, 2)} ${edge(r, x + s, y, x, y + s, 2)}`;
function sparkPath(r, x, y, s) {
  let d = '';
  for (let k = 0; k < 4; k++) {
    const a = k * Math.PI / 2 + 0.3 + jit(r, 0.2);
    d += ` M${P(x + Math.cos(a) * s * 0.3, y + Math.sin(a) * s * 0.3)} L${P(x + Math.cos(a) * s, y + Math.sin(a) * s)}`;
  }
  return d.trim();
}
function squigglePath(r, x, y, w, amp = 5, n = 5) {
  let d = `M${P(x, y)}`; const st = w / n;
  for (let i = 0; i < n; i++) d += ` Q${P(x + st * i + st / 2, y + (i % 2 ? amp : -amp) + jit(r, 1))} ${P(x + st * (i + 1), y + jit(r, 1))}`;
  return d;
}

// ink: a hand-drawn SVG layer on a host element, regenerated (same seed) when the host is resized
const inks = new Map();
let inkSeed = 11;
const inkRO = new ResizeObserver(entries => entries.forEach(e => redrawInk(e.target)));
function ink(host, kind = 'box') {
  if (inks.has(host)) return;
  const path = svg('path', { pathLength: 1 });
  const g = svg('g', { filter: kind === 'box' ? 'url(#boil)' : 'url(#boil-thin)' }, path);
  const s = svg('svg', { class: kind === 'box' ? 'card-ink' : 'ink-ul', 'aria-hidden': 'true', focusable: 'false' }, g);
  host.prepend(s);
  inks.set(host, { kind, path, svg: s, seed: (inkSeed += 7919) });
  inkRO.observe(host);
}
function redrawInk(host) {
  const i = inks.get(host);
  if (!i) return;
  if (!host.isConnected) { inkRO.unobserve(host); inks.delete(host); return; }
  const w = host.offsetWidth, h = host.offsetHeight;
  if (!w || !h) return;
  const r = prng(i.seed);
  const hh = i.kind === 'box' ? h : 14;
  i.svg.setAttribute('width', w); i.svg.setAttribute('height', hh); i.svg.setAttribute('viewBox', `0 0 ${w} ${hh}`);
  i.path.setAttribute('d', i.kind === 'box' ? boxPath(r, w, h) : underlinePath(r, w));
}
function inkAll(root) {
  root.querySelectorAll('.card').forEach(c => ink(c));
  let k = 0;
  root.querySelectorAll('.card, .sticky').forEach(c => { if (!c.style.getPropertyValue('--d')) c.style.setProperty('--d', (k++ * 0.07).toFixed(2) + 's'); });
}

// ink boil: step the turbulence seed at ~10 fps (decorative ink only, never text). Off for reduced motion / hidden tab.
let boilTimer = 0;
function boilSync() {
  clearInterval(boilTimer); boilTimer = 0;
  const t1 = $('#turb'), t2 = $('#turb2');
  if (reduced() || document.hidden || !t1) return;
  let i = 0;
  boilTimer = setInterval(() => {
    const seed = String(1 + (++i % 6));
    t1.setAttribute('seed', seed);
    if (t2) t2.setAttribute('seed', seed);
  }, 100);
}
mqReduce.addEventListener('change', boilSync);
document.addEventListener('visibilitychange', boilSync);

// =================================================================== formatting
const state = {
  token: '', health: null, policy: null,
  customers: [], custById: new Map(), scenarios: [], orders: new Map(),
  currentCase: null, idem: { sig: '', key: '' }, submitting: false,
  poll: { seq: 0, timer: 0 }, formSeq: 0,
  reviews: [], reviewOpenId: null, insightsRun: '', locked: false,
};

const nfCache = new Map();
function fmtMoney(v, cur) {
  if (v == null) return 'unavailable';
  if (typeof v !== 'number' || !Number.isInteger(v)) return `invalid (${String(v).slice(0, 20)})`;
  const code = typeof cur === 'string' ? cur : '';
  const exps = state.policy && state.policy.currency_exponent;
  const exp = exps && Number.isInteger(exps[code]) ? exps[code] : null;
  if (exp == null) return `${v} minor units${code ? ' ' + code.slice(0, 8) : ''}`;
  const key = code + ':' + exp;
  let f = nfCache.get(key);
  if (!f) {
    try { f = new Intl.NumberFormat('en-US', { style: 'currency', currency: code, minimumFractionDigits: exp, maximumFractionDigits: exp }); }
    catch (e) { f = { format: x => `${x.toFixed(exp)} ${code}` }; }
    nfCache.set(key, f);
  }
  return f.format(v / Math.pow(10, exp));
}
const missing = () => el('span', { class: 'miss' }, 'missing');
const unavailable = () => el('span', { class: 'na' }, 'unavailable');
const moneyNode = (v, cur) => (v == null ? unavailable() : document.createTextNode(fmtMoney(v, cur)));
// evidence rows: a null amount is a missing record field, not an unavailable computation
const recMoney = (v, cur) => (v == null ? missing() : document.createTextNode(fmtMoney(v, cur)));
function cell(v) { return v == null || v === '' ? missing() : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
function dateCell(v) {
  if (v == null || v === '') return missing();
  const s = String(v);
  return el('span', { title: s, class: 'nowrap' }, /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : s);
}
const whenText = v => (typeof v === 'string' ? v.replace('T', ' ').replace(/(:\d\d)(\.\d+)?Z?$/, '$1').slice(0, 16) + ' UTC' : '');
const orNA = v => (v == null || v === '' || v === 'unavailable' ? unavailable() : String(v));
function countUp(node, minor, cur, delay) {
  const final = fmtMoney(minor, cur);
  if (reduced() || !Number.isInteger(minor) || minor === 0) { node.textContent = final; return; }
  node.textContent = fmtMoney(0, cur);
  const t0 = performance.now() + delay, dur = 800;
  const step = now => {
    const p = Math.min(1, Math.max(0, (now - t0) / dur));
    node.textContent = p >= 1 || !node.isConnected ? final : fmtMoney(Math.round(minor * (1 - Math.pow(1 - p, 3))), cur);
    if (p < 1 && node.isConnected) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const live = $('#live');
function say(msg) { live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 40); }

// =================================================================== api client (same-origin only, bearer token)
const TOKEN_KEY = 'nexus.token';
const tokenStore = {
  get() { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } },
  set(v) { try { sessionStorage.setItem(TOKEN_KEY, v); } catch (e) { /* in-memory only */ } },
  del() { try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ } },
};

class ApiError extends Error {
  constructor(status, code, detail, retryAfter) {
    super(detail || code);
    this.status = status; this.code = code; this.detail = detail || ''; this.retryAfter = retryAfter || '';
  }
}
function detailText(d) {
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(x => (x && typeof x === 'object' ? [Array.isArray(x.loc) ? x.loc.slice(1).join('.') : '', x.msg].filter(Boolean).join(': ') : String(x))).join('; ').slice(0, 300);
  return '';
}
async function api(method, path, body) {
  if (typeof path !== 'string' || path[0] !== '/' || path[1] === '/') throw new Error('relative paths only');
  const headers = { Accept: 'application/json' };
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  const init = { method, headers, cache: 'no-store', credentials: 'same-origin' };
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, init); } catch (e) { throw new ApiError(0, 'network', 'Could not reach the server.'); }
  let data = null;
  try { const t = await res.text(); data = t ? JSON.parse(t) : null; } catch (e) { data = null; }
  if (res.status === 401) { onUnauthorized(); throw new ApiError(401, 'unauthorized', 'Access token missing or rejected.'); }
  if (!res.ok) {
    throw new ApiError(res.status, data && typeof data.error === 'string' ? data.error : 'error',
      detailText(data && data.detail) || res.statusText, res.headers.get('Retry-After'));
  }
  return { status: res.status, data };
}
const errText = e => (e instanceof ApiError ? (e.detail || e.code) : 'Something went wrong.');

// =================================================================== access gate
function showGate(msg, kind) {
  stopPolling();
  $('#tabs-nav').hidden = true; $('#app').hidden = true; $('#fatal').hidden = true;
  $('#gate').hidden = false;
  state.locked = true;
  const m = $('#gate-msg'); m.textContent = msg || ''; m.dataset.kind = msg ? (kind || 'error') : '';
  setBadges(null);
  setTimeout(() => { if (!$('#gate').hidden) $('#gate-token').focus(); }, 50);
  inkAll($('#gate'));
}
function onUnauthorized() {
  const had = !!state.token;
  state.token = ''; tokenStore.del();
  showGate(had ? 'That token was rejected or has expired. Enter the access token again.' : 'This demo needs an access token.', had ? 'error' : 'info');
}
function fatal(msg) {
  $('#gate').hidden = true; $('#tabs-nav').hidden = true; $('#app').hidden = true;
  const f = $('#fatal'); f.textContent = msg; f.hidden = false;
}

// =================================================================== header badges (honest provenance)
function badgeTexts(c) {
  if (!c) {
    const m = String(state.health && state.health.inference && state.health.inference.mode || '').toUpperCase();
    const run = state.health ? `Server mode: ${m || 'unknown'}` : state.locked ? 'Locked: token needed' : 'Server unreachable';
    const inf = m === 'LIVE' ? 'Live configured. No case yet.' : m === 'RECORDED' ? 'Recorded replay mode. No case yet.'
      : m === 'MOCK' ? 'Mock mode (no AMD call). No case yet.' : 'No case yet';
    return { run, inf, kind: 'idle' };
  }
  const run = `Run mode: ${c.run_mode || 'unknown'}`, s = c.inference_status;
  if (c.state === 'QUEUED' || c.state === 'RUNNING') return { run, inf: 'Waiting for triage...', kind: 'pending' };
  if (s === 'SKIPPED_POLICY') return { run, inf: 'Skipped by policy · 0 model calls', kind: 'skipped' };
  if (s === 'FAILED') return { run, inf: `FAILED · ${c.review_reason || 'model call failed'}`, kind: 'failed' };
  if (c.live_amd === true) return { run, inf: 'LIVE AMD', kind: 'live' };
  if (s === 'USED' && c.run_mode === 'RECORDED') return { run, inf: 'RECORDED replay', kind: 'recorded' };
  if (s === 'USED' && c.run_mode === 'MOCK') return { run, inf: 'MOCK (no AMD call)', kind: 'mock' };
  if (s === 'USED') return { run, inf: 'Live run · call not confirmed', kind: 'pending' };
  return { run, inf: c.state === 'FAILED' ? 'Not run · case failed' : 'Inference not started', kind: 'pending' };
}
function setBadges(c) {
  const t = badgeTexts(c);
  const upd = (id, text, kind) => {
    const b = $(id), v = $('.badge-v', b);
    if (v.textContent === text && b.dataset.kind === kind) return;
    v.textContent = text; b.dataset.kind = kind;
    if (!reduced()) { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }
  };
  upd('#badge-run', t.run, 'idle');
  upd('#badge-inf', t.inf, t.kind);
}

// =================================================================== tabs
const TABS = ['case', 'reviews', 'insights', 'policy'];
function selectTab(name, focus) {
  if (!TABS.includes(name)) name = 'case';
  for (const t of TABS) {
    const tab = $('#tab-' + t), on = t === name;
    tab.setAttribute('aria-selected', on ? 'true' : 'false'); tab.tabIndex = on ? 0 : -1;
    $('#panel-' + t).hidden = !on;
  }
  if (focus) $('#tab-' + name).focus();
  try { history.replaceState(null, '', '#' + name); } catch (e) { /* ignore */ }
  if (name === 'reviews') loadReviews();
  if (name === 'insights') loadInsights(state.insightsRun);
}
function initTabs() {
  const list = $('[role="tablist"]');
  list.addEventListener('click', e => { const t = e.target.closest('[role="tab"]'); if (t) selectTab(t.id.slice(4), false); });
  list.addEventListener('keydown', e => {
    const i = TABS.findIndex(t => $('#tab-' + t).tabIndex === 0);
    let n = -1;
    if (e.key === 'ArrowRight') n = (i + 1) % TABS.length;
    else if (e.key === 'ArrowLeft') n = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = TABS.length - 1;
    if (n >= 0) { e.preventDefault(); selectTab(TABS[n], true); }
  });
  window.addEventListener('hashchange', () => { if (!$('#app').hidden) selectTab(location.hash.slice(1), false); });
}

// =================================================================== case form
const opt = (value, text) => el('option', { value }, text);
const MAX_COMPLAINT = 2000, MAX_RELATED = 5;
const custName = id => { const c = state.custById.get(id); return c && c.display_name ? c.display_name : String(id == null ? 'unknown' : id); };

function formMsg(text, kind) { const m = $('#form-msg'); m.textContent = text || ''; m.dataset.kind = text ? (kind || 'error') : ''; }
function formFail(text, sel) { formMsg(text); const n = $(sel); if (n) n.focus(); }
function updateCount() { const n = $('#complaint').value.length, c = $('#count'); c.textContent = `${n} / ${MAX_COMPLAINT}`; c.classList.toggle('warn', n >= MAX_COMPLAINT * 0.9); }

function orderLabel(o) {
  const parts = [String(o.id)];
  if (o.description) parts.push(String(o.description));
  if (o.total_minor != null) parts.push(fmtMoney(o.total_minor, o.currency));
  if (typeof o.created_at === 'string') parts.push(o.created_at.slice(0, 10));
  if (o.payment_count != null) parts.push(`${o.payment_count} payment${o.payment_count === 1 ? '' : 's'}`);
  return parts.join(' · ');
}
function fillCustomers(list) {
  const sel = $('#customer');
  sel.replaceChildren(opt('', 'Choose a customer...'), ...list.map(c => opt(c.id, `${c.display_name || c.id}${c.account_status && c.account_status !== 'active' ? ' · ' + c.account_status : ''}`)));
}
function fillScenarios(list) {
  $('#scenario').replaceChildren(opt('', 'Pick a scenario to fill the form...'), ...list.map(s => opt(s.case_key, s.title || s.case_key)));
}
function renderRelated(preset) {
  const box = $('#related-list');
  const keep = preset || Array.from(box.querySelectorAll('input:checked')).map(i => i.value);
  const disputed = $('#order').value;
  const others = (state.orders.get($('#customer').value) || []).filter(o => o.id !== disputed);
  box.replaceChildren();
  if (!disputed) { box.append(el('p', { class: 'hint' }, 'Choose a disputed order first.')); return; }
  if (!others.length) { box.append(el('p', { class: 'hint' }, 'No other orders for this customer.')); return; }
  for (const o of others) {
    const cb = el('input', { type: 'checkbox', value: o.id });
    cb.checked = keep.includes(o.id);
    box.append(el('label', { class: 'check' }, cb, el('span', null, orderLabel(o))));
  }
  limitRelated();
}
function limitRelated() {
  const boxes = Array.from($('#related-list').querySelectorAll('input'));
  const n = boxes.filter(b => b.checked).length;
  boxes.forEach(b => { b.disabled = !b.checked && n >= MAX_RELATED; });
}
async function loadOrders(customerId) {
  const seq = ++state.formSeq, sel = $('#order');
  sel.replaceChildren(opt('', customerId ? 'Loading orders...' : 'Choose a customer first...'));
  sel.disabled = true; renderRelated([]);
  if (!customerId) return;
  let orders = state.orders.get(customerId);
  if (!orders) {
    try {
      const { data } = await api('GET', `/customers/${encodeURIComponent(customerId)}/orders`);
      orders = Array.isArray(data) ? data : [];
      state.orders.set(customerId, orders);
    } catch (e) {
      if (seq === state.formSeq) { sel.replaceChildren(opt('', 'Could not load orders')); formMsg(errText(e)); }
      return;
    }
  }
  if (seq !== state.formSeq) return;
  sel.replaceChildren(opt('', orders.length ? 'Choose the disputed order...' : 'No orders for this customer'), ...orders.map(o => opt(o.id, orderLabel(o))));
  sel.disabled = !orders.length;
}
async function applyScenario(key) {
  const sc = state.scenarios.find(s => s.case_key === key);
  if (!sc) return;
  const cs = $('#customer');
  cs.value = sc.customer_id;
  if (cs.value !== sc.customer_id) { formMsg('That scenario’s customer is not in the customer list.'); return; }
  await loadOrders(sc.customer_id);
  $('#order').value = sc.disputed_order_id;
  renderRelated(Array.isArray(sc.related_order_ids) ? sc.related_order_ids : []);
  $('#complaint').value = sc.complaint || ''; updateCount(); formMsg('');
  say('Scenario loaded into the form');
}
function readForm() {
  return {
    customer_id: $('#customer').value,
    disputed_order_id: $('#order').value,
    related_order_ids: Array.from($('#related-list').querySelectorAll('input:checked')).map(i => i.value).sort(),
    complaint: $('#complaint').value.trim(),
  };
}
function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  const b = new Uint8Array(16); crypto.getRandomValues(b);
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}
function setBusy(b) {
  state.submitting = b;
  const btn = $('#submit'); btn.disabled = b; btn.textContent = b ? 'Working...' : 'Triage this complaint';
  $('#case-form').setAttribute('aria-busy', String(b));
}
async function onSubmit(ev) {
  ev.preventDefault();
  if (state.submitting) return;
  const f = readForm();
  if (!f.customer_id) return formFail('Choose a customer.', '#customer');
  if (!f.disputed_order_id) return formFail('Choose the disputed order.', '#order');
  if (!f.complaint) return formFail('Write the customer complaint.', '#complaint');
  if (f.complaint.length > MAX_COMPLAINT) return formFail(`The complaint is over ${MAX_COMPLAINT} characters.`, '#complaint');
  // same payload (double click, retry) keeps its key; any change to the payload gets a fresh one
  const sig = JSON.stringify([f.customer_id, f.disputed_order_id, f.related_order_ids, f.complaint]);
  if (sig !== state.idem.sig) state.idem = { sig, key: newKey() };
  formMsg(''); setBusy(true); stopPolling();
  state.currentCase = null; $('#result').replaceChildren(); setBadges(null);
  $('#pipeline-card').hidden = false; $('#case-banner').textContent = 'Submitting...';
  setPipeline([{ s: 'active', sub: 'capturing evidence' }], false);
  try {
    const { status, data } = await api('POST', '/cases', Object.assign({}, f, { idempotency_key: state.idem.key }));
    if (status === 200) formMsg('This exact request was already submitted. Showing the existing case (idempotent replay).', 'info');
    say('Case submitted');
    startPolling(data.id);
  } catch (e) {
    setBusy(false); $('#pipeline-card').hidden = true;
    if (e.status === 401) return;
    if (e.status === 409) { state.idem = { sig: '', key: '' }; formMsg('That request conflicts with an earlier one. Submit again to retry with a fresh key.'); }
    else if (e.status === 429) formMsg(`Too many requests${e.retryAfter ? ` (retry after ${e.retryAfter} s)` : ''}. Try again shortly.`);
    else if (e.status === 404) formMsg('Customer or order not found.');
    else formMsg(errText(e));
    say('Submit failed');
  }
}
function initForm() {
  $('#case-form').addEventListener('submit', onSubmit);
  $('#scenario').addEventListener('change', e => { if (e.target.value) applyScenario(e.target.value); });
  $('#customer').addEventListener('change', e => { $('#scenario').value = ''; loadOrders(e.target.value); });
  $('#order').addEventListener('change', () => renderRelated());
  $('#related-list').addEventListener('change', limitRelated);
  $('#complaint').addEventListener('input', updateCount);
  updateCount();
}

// =================================================================== pipeline doodle
const STEPS = ['Snapshot', 'Policy', 'AMD model', 'Validate', 'Reply'];
const STATUS_WORDS = { pending: 'pending', active: 'in progress', done: 'done', skipped: 'skipped', failed: 'failed', na: 'not applicable' };
function buildPipeline() {
  const ol = $('#pipe'); ol.replaceChildren();
  STEPS.forEach((label, i) => {
    const r = prng(100 + i * 13);
    const node = svg('svg', { class: 'node', viewBox: '0 0 64 64', width: 56, height: 56, 'aria-hidden': 'true', focusable: 'false' },
      svg('circle', { class: 'fill', cx: 32, cy: 32, r: 24 }),
      svg('g', { class: 'ring-g' }, svg('g', { filter: 'url(#boil-thin)' }, svg('path', { class: 'ring', pathLength: 1, d: circlePath(r, 32, 32, 25) }))),
      svg('g', { filter: 'url(#boil-thin)' },
        svg('path', { class: 'mark check', pathLength: 1, d: checkPath(19, 20, 26) }),
        svg('path', { class: 'mark cross', pathLength: 1, d: crossPath(r, 21, 21, 22) }),
        svg('path', { class: 'mark dash', pathLength: 1, d: edge(r, 20, 33, 44, 31, 2) })));
    const link = i < STEPS.length - 1 ? el('div', { class: 'link', 'aria-hidden': 'true' },
      svg('svg', { class: 'link-line', viewBox: '0 0 100 12', preserveAspectRatio: 'none', focusable: 'false' }, svg('path', { d: `M1 6 Q28 ${n1(2 + r() * 3)} 52 6 T99 6` })),
      svg('svg', { class: 'link-head', viewBox: '0 0 12 14', focusable: 'false' }, svg('path', { d: 'M2 2 L10 7 L2 12' }))) : null;
    ol.append(el('li', { class: 'step', 'data-status': 'pending' }, node,
      el('span', { class: 'step-label' }, label), el('span', { class: 'sr-only step-sr' }, ': pending'),
      el('span', { class: 'step-sub' }), link));
  });
}
function setPipeline(items, stagger) {
  $('#pipe').querySelectorAll('.step').forEach((li, i) => {
    const it = items[i] || { s: 'pending', sub: '' };
    const finished = it.s === 'done' || it.s === 'skipped' || it.s === 'failed' || it.s === 'na';
    li.style.setProperty('--td', stagger && finished && !reduced() ? (i * 0.28).toFixed(2) + 's' : '0s');
    li.dataset.status = it.s;
    $('.step-sub', li).textContent = it.sub || '';
    $('.step-sr', li).textContent = ': ' + STATUS_WORDS[it.s];
  });
}
function pipelineFor(c) {
  const p = { s: 'pending', sub: '' };
  if (c.state === 'QUEUED') return [{ s: 'done', sub: 'evidence stored' }, { s: 'pending', sub: 'queued' }, p, p, p];
  if (c.state === 'RUNNING') return [{ s: 'done', sub: 'evidence stored' }, { s: 'active', sub: 'applying rules' }, p, p, p];
  const inf = c.inference_status, flags = Array.isArray(c.flags) ? c.flags.length : 0;
  const policy = c.state === 'FAILED' && !c.policy_hash ? { s: 'failed', sub: 'did not complete' }
    : { s: 'done', sub: flags ? `${flags} flag${flags > 1 ? 's' : ''} found` : 'no mandatory flags' };
  let amd, val;
  if (inf === 'SKIPPED_POLICY') { amd = { s: 'skipped', sub: 'skipped by policy' }; val = { s: 'na', sub: 'nothing to validate' }; }
  else if (inf === 'FAILED') { amd = { s: 'failed', sub: 'call failed' }; val = { s: 'na', sub: 'no output' }; }
  else if (inf === 'USED') {
    amd = { s: 'done', sub: c.live_amd === true ? 'live call' : c.run_mode === 'RECORDED' ? 'recorded replay' : c.run_mode === 'MOCK' ? 'mock, no AMD call' : 'call recorded' };
    const v = c.validation;
    val = v && v.valid === false ? { s: 'failed', sub: 'invalid output' } : { s: 'done', sub: v && v.downgraded ? 'downgraded' : 'checked' };
  } else { amd = { s: 'skipped', sub: 'not run' }; val = { s: 'na', sub: 'no output' }; }
  const hasReply = typeof c.reply_draft === 'string' && c.reply_draft !== '';
  const reply = hasReply ? { s: 'done', sub: 'draft ready' } : { s: 'na', sub: c.disposition === 'HUMAN_REVIEW_REQUIRED' ? 'held for operator' : 'no draft' };
  return [{ s: 'done', sub: 'evidence stored' }, policy, amd, val, reply];
}

// =================================================================== polling
function stopPolling() { state.poll.seq++; clearTimeout(state.poll.timer); }
function bannerText(c) {
  const live = c.state === 'QUEUED' || c.state === 'RUNNING';
  return `Case ${c.id} · ${c.state}${Number.isInteger(c.attempt_count) ? ` · attempt ${c.attempt_count}` : ''}${live ? ' · checking every 2 s' : ''}`;
}
function startPolling(id) {
  stopPolling();
  const seq = state.poll.seq;
  let fails = 0, n = 0, prev = '';
  const tick = async () => {
    if (seq !== state.poll.seq) return;
    try {
      const { data: c } = await api('GET', '/cases/' + encodeURIComponent(id));
      if (seq !== state.poll.seq) return;
      fails = 0;
      if (c.state === 'QUEUED' || c.state === 'RUNNING') {
        setBadges(c); $('#case-banner').textContent = bannerText(c); setPipeline(pipelineFor(c), false);
        if (c.state !== prev) say(c.state === 'QUEUED' ? 'Case queued' : 'Case running');
        prev = c.state;
        if (++n > 150) { setBusy(false); formMsg('Still working after 5 minutes. Check the case again later.', 'info'); return; }
        state.poll.timer = setTimeout(tick, 2000);
      } else {
        onCaseFinal(c);
      }
    } catch (e) {
      if (seq !== state.poll.seq || e.status === 401) return;
      const retry = e.status === 0 || e.status >= 500;
      if (!retry || ++fails >= 5) { setBusy(false); formMsg(retry ? `Lost contact with the server: ${errText(e)}` : errText(e)); return; }
      state.poll.timer = setTimeout(tick, 2000);
    }
  };
  tick();
}
function onCaseFinal(c) {
  state.currentCase = c;
  setBusy(false);
  setBadges(c);
  $('#case-banner').textContent = bannerText(c);
  setPipeline(pipelineFor(c), true);
  renderResult(c, true);
  if (c.state === 'NEEDS_HUMAN') loadReviews(); // keep the queue count current
  say(`Triage complete: ${DISP[c.disposition] || (c.state === 'FAILED' ? 'case failed' : 'no outcome')}. Run mode ${c.run_mode}. ${badgeTexts(c).inf}`);
}

// =================================================================== result rendering
const DISP = {
  REFUND_RECOMMENDED: 'Refund recommended', NO_REFUND_RECOMMENDED: 'No refund recommended',
  CLARIFICATION_REQUIRED: 'Clarification required', HUMAN_REVIEW_REQUIRED: 'Human review required',
};
const EXPLAIN = {
  AUTH_HOLD_NOT_CHARGE: 'authorization hold, not a charge', DUPLICATE_CAPTURE_REFUND: 'duplicate capture',
  PARTIAL_CORRECTION_REMAINDER: 'partly corrected, remainder owed', SPLIT_SHIPMENT_CAPTURES: 'split-shipment captures',
  PARTIAL_CAPTURES_MATCH_TOTAL: 'partial captures add up to the total', ALREADY_CORRECTED: 'already corrected',
  SEPARATE_ORDERS: 'separate orders', SUBSCRIPTION_RENEWAL: 'subscription renewal', SINGLE_CHARGE_ONLY: 'single charge only',
  NEEDS_DETAILS: 'needs more details', OUT_OF_DOMAIN: 'out of domain',
};
const FIELD_WORDS = { date: 'charge date', last_four: 'last four digits of the card', amount: 'amount' };
const flagCode = f => (typeof f === 'string' ? f : f && typeof f === 'object' ? String(f.code || f.reason || '') : '');
const safeClass = s => String(s == null ? '' : s).toLowerCase().replace(/[^a-z_]/g, '');
const td = (cls, ...kids) => el('td', cls ? { class: cls } : null, ...kids);
const small = t => (t == null ? null : el('small', null, String(t)));
const pill = (status, extra) => el('span', { class: `pill ${safeClass(status)}${extra ? ' ' + extra : ''}` }, cell(status));
const card = (title, ...kids) => el('section', { class: 'card pop-in' }, title ? el('h2', { class: 'card-title' }, title) : null, ...kids);

function dataTable(label, heads, rows, emptyText) {
  return el('div', { class: 'table-scroll', role: 'region', 'aria-label': label, tabindex: '0' },
    el('table', null,
      el('caption', { class: 'sr-only' }, label),
      el('thead', null, el('tr', null, heads.map(h => el('th', { scope: 'col' }, h)))),
      el('tbody', null, rows.length ? rows.map(r => el('tr', null, r))
        : el('tr', null, el('td', { class: 'empty', colspan: heads.length }, el('span', { class: 'na' }, emptyText))))));
}

// rule chip: hover / focus / tap shows the rule text
let tipSeq = 0;
function ruleChip(r) {
  const id = 'tip' + (++tipSeq);
  const btn = el('button', { type: 'button', class: 'rule-chip', 'aria-describedby': id, 'aria-expanded': 'false' }, String(r.id));
  const tip = el('span', { class: 'tip', role: 'tooltip', id }, el('strong', null, `${r.id}${r.title ? ' · ' + r.title : ''}`), r.text ? String(r.text) : '');
  const wrap = el('span', { class: 'rule' }, btn, tip);
  const place = () => {
    if (getComputedStyle(tip).position === 'fixed') return;
    tip.style.left = '0px';
    const over = tip.getBoundingClientRect().right - (document.documentElement.clientWidth - 12);
    if (over > 0) tip.style.left = -over + 'px';
  };
  wrap.addEventListener('mouseenter', place);
  btn.addEventListener('focus', place);
  btn.addEventListener('click', () => { const open = wrap.classList.toggle('open'); btn.setAttribute('aria-expanded', String(open)); if (open) place(); });
  return wrap;
}
function closeTips(except) {
  document.querySelectorAll('.rule.open').forEach(r => {
    if (except && r.contains(except)) return;
    r.classList.remove('open'); r.firstChild.setAttribute('aria-expanded', 'false');
  });
}

function openReviewFromCase(id) { selectTab('reviews', false); openReview(id); }
function reviewLine(c, noButton) {
  const r = c.review;
  if (!r) return null;
  if (r.status === 'PENDING') {
    return el('p', { class: 'review-line' },
      `Review queue: PENDING since ${whenText(r.queued_at)}${r.reason_code ? ' (' + r.reason_code + ')' : ''}. `,
      noButton ? null : el('button', { type: 'button', class: 'btn small', onclick: () => openReviewFromCase(c.id) }, 'Open in review queue'));
  }
  return el('p', { class: 'review-line' }, `Review COMPLETED by ${r.operator_id || 'unknown'}: ${r.operator_disposition || 'no decision'}. The original automated outcome is retained.`);
}

function dispositionCard(c, opts = {}) {
  const prim = c.primary_reason || c.review_reason;
  const flags = (Array.isArray(c.flags) ? c.flags : []).map(flagCode).filter(Boolean);
  const rules = Array.isArray(c.rules_cited) ? c.rules_cited : [];
  let why = null;
  if (prim) why = el('p', { class: 'reason' }, 'Primary reason: ', el('code', null, String(prim)));
  else if (c.explanation_code) why = el('p', { class: 'reason' }, 'Explanation: ', el('code', null, String(c.explanation_code)), EXPLAIN[c.explanation_code] ? ` (${EXPLAIN[c.explanation_code]})` : '');
  return card('Disposition',
    c.state === 'FAILED' ? el('p', { class: 'fail-note' }, 'The system could not complete a safe outcome for this case.') : null,
    el('div', { class: 'stamp-wrap' }, el('p', { class: 'stamp' + (opts.small ? ' small' : '') }, DISP[c.disposition] || 'No outcome')),
    why,
    el('div', { class: 'row-block' }, el('span', { class: 'label-k' }, 'Deterministic flags'),
      flags.length ? el('ul', { class: 'tags' }, flags.map(f => el('li', { class: 'tag' + (f === prim ? ' primary' : '') }, (f === prim ? '★ ' : '') + f)))
        : el('p', { class: 'hint' }, 'No mandatory flags. Policy found nothing that forces human review.')),
    el('div', { class: 'row-block' }, el('span', { class: 'label-k' }, 'Policy rules cited (hover or focus a rule)'),
      rules.length ? el('div', { class: 'tags' }, rules.map(ruleChip)) : el('p', { class: 'hint' }, 'No rules cited.')),
    reviewLine(c, opts.noButton));
}

function amountsCard(c) {
  const a = c.amounts || {}, cur = a.currency;
  const row = (k, v, cls) => el('li', { class: cls || null }, el('span', { class: 'k' }, k), el('span', { class: 'leader', 'aria-hidden': 'true' }), el('span', { class: 'v' }, moneyNode(v, cur)));
  const net = el('span', { class: 'v' });
  if (a.net_owed == null) net.append(unavailable()); else net.textContent = fmtMoney(a.net_owed, cur);
  if (a.net_owed != null && Number.isInteger(a.net_owed)) net.dataset.minor = String(a.net_owed);
  return card('Amounts',
    el('ul', { class: 'receipt' },
      row('Order total', a.order_total), row('Captured total', a.captured_total), row('Over-capture', a.over_capture),
      row('Already corrected', a.corrected_total),
      el('li', { class: 'key' }, el('span', { class: 'k' }, el('span', { class: 'hl' }, 'Net owed')), el('span', { class: 'leader', 'aria-hidden': 'true' }), net),
      row('Recommended refund', a.recommended_amount), row('Policy threshold', a.threshold)),
    el('p', { class: 'hint cur-note' }, `Currency: ${a.currency || 'unavailable'}. Amounts come from deterministic policy code, never from model text. Unavailable means outside the supported scope, not zero.`));
}

function evidenceCard(c) {
  const ev = c.evidence || {}, cu = ev.customer || {};
  const orders = Array.isArray(ev.orders) ? ev.orders : [], pays = Array.isArray(ev.payments) ? ev.payments : [], refs = Array.isArray(ev.refunds) ? ev.refunds : [];
  const sub = (text) => el('h3', { class: 'sub-title' }, text);
  const orderRows = orders.map(o => [
    td(null, el('span', { class: 'role' + (o.role === 'disputed' ? ' disputed' : '') }, cell(o.role))),
    td('mono', cell(o.id)), td(null, cell(o.description)), td(null, dateCell(o.created_at)),
    td('num', recMoney(o.total_minor, o.currency), small(o.currency)),
    td(null, cell(o.kind), small(o.shipment_count == null ? null : `${o.shipment_count} shipment${o.shipment_count === 1 ? '' : 's'}`)),
    td(null, cell(o.adjustment_status))]);
  const payRows = pays.map(p => [
    td('mono', cell(p.id), p.parent_authorization_id ? small('child of ' + p.parent_authorization_id) : null),
    td('mono', cell(p.order_id)), td(null, pill(p.status)),
    td(null, dateCell(p.captured_at || p.authorized_at), p.auth_expires_at ? small('expires ' + String(p.auth_expires_at).slice(0, 10)) : null),
    td('mono', p.card_brand == null && p.last_four == null ? missing() : `${p.card_brand == null ? '?' : p.card_brand} •••• ${p.last_four == null ? '(missing)' : p.last_four}`),
    td('num', recMoney(p.amount_minor, p.currency), small(p.currency)),
    td(null, cell(p.capture_type)),
    td(null, p.chargeback_status === 'open' ? pill('open') : cell(p.chargeback_status))]);
  const refRows = refs.map(r => [
    td('mono', cell(r.id)), td('mono', cell(r.payment_id)), td(null, pill(r.status)), td(null, dateCell(r.created_at)),
    td('num', recMoney(r.amount_minor, r.currency), small(r.currency)), td(null, cell(r.reason))]);
  return card('Evidence',
    el('p', { class: 'cust-line' }, 'Customer: ', el('strong', null, cell(cu.display_name)), ' ',
      el('span', { class: 'mono' }, `(${cu.id || c.customer_id || 'unknown'})`), ' · account ',
      el('span', { class: 'pill' + (cu.account_status === 'restricted' ? ' open' : '') }, cell(cu.account_status))),
    sub('Orders'), dataTable('Orders', ['Role', 'Order', 'Item', 'Date', 'Total', 'Kind', 'Adjustment'], orderRows, 'No orders in this snapshot'),
    sub('Payments'), dataTable('Payments', ['Payment', 'Order', 'Status', 'Date', 'Card', 'Amount', 'Capture', 'Chargeback'], payRows, 'No payments in this snapshot'),
    sub('Refunds'), dataTable('Refunds', ['Refund', 'Payment', 'Status', 'Date', 'Amount', 'Reason'], refRows, 'No refunds recorded'),
    el('p', { class: 'footnote' }, 'Related orders are cited for context and are never added to the disputed order’s arithmetic.'));
}

function replyCard(c) {
  if (c.disposition === 'HUMAN_REVIEW_REQUIRED') return card('Customer reply', el('p', { class: 'no-reply' }, 'No customer reply before operator review'));
  const draft = typeof c.reply_draft === 'string' ? c.reply_draft : '';
  if (!draft) return card('Customer reply', el('p', { class: 'hint' }, 'No reply draft was produced for this case.'));
  const mf = Array.isArray(c.missing_fields) ? c.missing_fields : [];
  const ta = el('textarea', { id: 'reply', rows: Math.min(18, Math.max(7, draft.split('\n').length + 1)) });
  ta.value = draft;
  const edited = el('p', { class: 'edited', role: 'status', hidden: true }, 'EDITED — operator review required; template guarantees no longer apply');
  const copyBtn = el('button', { type: 'button', class: 'btn' }, 'Copy reply');
  let was = false;
  ta.addEventListener('input', () => {
    const now = ta.value !== draft;
    edited.hidden = !now;
    if (now && !was) say('Reply edited. Operator review required; template guarantees no longer apply.');
    was = now;
  });
  copyBtn.addEventListener('click', async () => {
    let ok = true;
    try { await navigator.clipboard.writeText(ta.value); }
    catch (e) { try { ta.select(); ok = document.execCommand('copy'); } catch (e2) { ok = false; } }
    copyBtn.textContent = ok ? 'Copied' : 'Press Ctrl/Cmd+C';
    say(ok ? 'Reply copied to the clipboard' : 'Copy failed. Select the text and copy it manually.');
    setTimeout(() => { copyBtn.textContent = 'Copy reply'; }, 1800);
  });
  return card('Customer reply',
    mf.length ? el('div', { class: 'row-block' }, el('span', { class: 'label-k' }, 'Missing from the complaint'),
      el('ul', { class: 'tags' }, mf.map(f => el('li', { class: 'tag primary', title: String(f) }, FIELD_WORDS[f] || String(f))))) : null,
    el('p', { class: 'hint draft-note' }, 'Draft only. Nothing is sent. Amounts, IDs and timing in the template were rendered by code, not by the model.'),
    el('label', { class: 'reply-label', for: 'reply' }, 'Reply draft (editable)'), ta,
    el('div', { class: 'reply-actions' }, copyBtn), edited);
}

const tok = v => (v == null || v === 'unavailable' ? 'unavailable' : String(v));
function traceCard(c) {
  const calls = Array.isArray(c.calls) ? c.calls : [];
  const rows = calls.map(k => [
    td('mono', cell(k.mode)), td('mono', cell(k.model_id)), td('mono', orNA(k.provider_request_id)),
    td('num', k.latency_ms == null ? unavailable() : `${k.latency_ms} ms`),
    td('mono', tok(k.input_tokens) === 'unavailable' && tok(k.output_tokens) === 'unavailable' ? unavailable() : `${tok(k.input_tokens)} in / ${tok(k.output_tokens)} out`),
    td('mono', cell(k.status)), td('num', cell(k.attempt_number))]);
  const modes = new Set(calls.map(k => k.mode));
  return card('Call trace',
    dataTable('Model call trace', ['Mode', 'Model', 'Request ID', 'Latency', 'Tokens', 'Status', 'Attempt'], rows,
      c.inference_status === 'SKIPPED_POLICY' ? 'Skipped by policy · 0 model calls' : 'No model calls were made.'),
    modes.has('RECORDED') ? el('p', { class: 'footnote' }, 'Recorded replay: latency and time are this replay’s. The original live call is under Developer metrics.') : null,
    modes.has('MOCK') ? el('p', { class: 'footnote' }, 'Mock model: no AMD call was made.') : null);
}

function devCard(c) {
  const v = c.validation || {}, ev = c.evidence || {};
  const errs = Array.isArray(v.errors) ? v.errors : [], events = Array.isArray(c.events) ? c.events : [], calls = Array.isArray(c.calls) ? c.calls : [];
  const kv = (k, val) => [el('dt', null, k), el('dd', null, val)];
  const callBlocks = calls.map(k => el('div', null,
    el('h3', { class: 'sub-title' }, `Call ${k.id == null ? '?' : k.id} (attempt ${k.attempt_number == null ? '?' : k.attempt_number})`),
    el('dl', { class: 'kv' },
      kv('Role / mode', `${k.role || '?'} / ${k.mode || '?'}`), kv('Prompt version', cell(k.prompt_version)),
      kv('Endpoint host', cell(k.endpoint_host)), kv('Request time', cell(k.request_timestamp)),
      kv('Usage state', cell(k.usage_state)),
      k.source_metadata ? kv('Original live call (replay source)', el('pre', { class: 'pre' }, JSON.stringify(k.source_metadata, null, 2))) : null)));
  const evRows = events.map(e => [td('num', cell(e.seq)), td('mono', cell(e.type)), td('mono', cell(e.ts)), td(null, cell(e.reason))]);
  return el('div', { class: 'card pop-in' }, el('details', null,
    el('summary', null, 'Developer metrics'),
    el('dl', { class: 'kv' },
      kv('Validation', v.valid == null ? unavailable() : (v.valid ? 'valid' : 'INVALID') + (v.downgraded ? ' · downgraded to clarification' : '')),
      kv('Validation errors', errs.length ? el('ul', { class: 'errs' }, errs.map(e => el('li', null, String(e)))) : 'none'),
      kv('Snapshot hash', cell(ev.snapshot_hash)), kv('Policy version', cell(c.policy_version)), kv('Policy hash', cell(c.policy_hash)),
      kv('State', cell(c.state)), kv('Run mode', cell(c.run_mode)), kv('Inference status', cell(c.inference_status)),
      kv('live_amd', String(c.live_amd)), kv('Attempts', cell(c.attempt_count)), kv('Dataset run', cell(c.dataset_run_id)),
      kv('Explanation code', cell(c.explanation_code)), kv('Review reason', cell(c.review_reason)),
      kv('Created / updated', `${c.created_at || '?'} / ${c.updated_at || '?'}`)),
    callBlocks,
    el('h3', { class: 'sub-title' }, 'Events'),
    dataTable('Case events', ['Seq', 'Type', 'Time', 'Reason'], evRows, 'No events'),
    el('div', { class: 'internal' }, el('span', { class: 'label-k' }, 'internal model draft — not sent to customer'),
      el('p', null, c.operator_explanation ? String(c.operator_explanation) : unavailable()))));
}

function burst(host) {
  if (reduced() || !host.isConnected) return;
  const stamp = host.querySelector('.stamp');
  if (!stamp) return;
  const r = prng(Date.now() % 100000);
  const w = stamp.offsetWidth, h = stamp.offsetHeight, W = w + 180, H = h + 180, cx = W / 2, cy = H / 2, rx = w / 2 + 10, ry = h / 2 + 10;
  const g = svg('g', { class: 'fly' });
  g.style.transformOrigin = `${cx}px ${cy}px`;
  for (let k = 0; k < 14; k++) {
    const a = (k / 14) * Math.PI * 2 + jit(r, 0.12), len = 16 + r() * 18;
    const x0 = cx + Math.cos(a) * rx, y0 = cy + Math.sin(a) * ry;
    g.append(svg('path', { pathLength: 1, d: `M${P(x0, y0)} L${P(x0 + Math.cos(a) * len, y0 + Math.sin(a) * len)}` }));
  }
  [[-1, -1, 15], [1, -1, 13], [-1, 1, 12], [1, 1, 16]].forEach(([sx, sy, sz]) => g.append(svg('path', { pathLength: 1, d: sparkPath(r, cx + sx * (rx + 44), cy + sy * (ry + 34), sz) })));
  g.append(svg('path', { pathLength: 1, d: squigglePath(r, cx - rx - 70, cy, 44) }), svg('path', { pathLength: 1, d: squigglePath(r, cx + rx + 26, cy + 6, 44) }));
  const s = svg('svg', { class: 'burst', width: W, height: H, viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true', focusable: 'false' }, g);
  s.style.left = (stamp.offsetLeft + w / 2 - cx) + 'px';
  s.style.top = (stamp.offsetTop + h / 2 - cy) + 'px';
  host.append(s);
  setTimeout(() => s.remove(), 1200);
}

function renderResult(c, animate) {
  const root = $('#result');
  root.replaceChildren();
  root.classList.toggle('still', !animate);
  root.append(el('div', { class: 'res-top' }, dispositionCard(c), amountsCard(c)), evidenceCard(c), replyCard(c), traceCard(c), devCard(c));
  inkAll(root);
  if (!animate) return;
  setTimeout(() => {
    const wrap = $('.stamp-wrap', root); if (wrap) burst(wrap);
    const net = $('.receipt .key .v', root);
    if (net && net.dataset.minor) countUp(net, Number(net.dataset.minor), (c.amounts || {}).currency, 0);
  }, reduced() ? 0 : 800);
}

// =================================================================== review queue
const DECISIONS = [
  ['REFUND_RECOMMENDED', 'Recommend a refund'], ['NO_REFUND_RECOMMENDED', 'Recommend no refund'],
  ['CLARIFICATION_REQUIRED', 'Ask the customer for details'], ['CLOSED_NO_ACTION', 'Close, no action'],
];
const OPERATOR_KEY = 'nexus.operator';
const savedOperator = () => { try { return sessionStorage.getItem(OPERATOR_KEY) || ''; } catch (e) { return ''; } };
const saveOperator = v => { try { sessionStorage.setItem(OPERATOR_KEY, v); } catch (e) { /* ignore */ } };
const findItem = id => Array.from($('#rv-list').children).find(li => li.dataset && li.dataset.case === id);
const setRvCount = () => { $('#rv-count').textContent = state.reviews.length ? `(${state.reviews.length})` : ''; };

async function loadReviews() {
  const st = $('#rv-status');
  try {
    const { data } = await api('GET', '/reviews');
    state.reviews = Array.isArray(data) ? data : [];
    st.textContent = '';
    renderReviewList();
  } catch (e) { if (e.status !== 401) st.textContent = errText(e); }
  setRvCount();
}
function renderReviewList() {
  const ul = $('#rv-list');
  ul.replaceChildren();
  if (!state.reviews.length) ul.append(el('li', { class: 'rv-empty' }, 'Queue is empty. Nothing is waiting for an operator.'));
  for (const r of state.reviews) {
    const flags = (Array.isArray(r.flags) ? r.flags : []).map(flagCode).filter(f => f && f !== r.reason_code);
    const btn = el('button', { type: 'button', class: 'rv-open', 'aria-controls': 'rv-detail', 'aria-expanded': String(state.reviewOpenId === r.case_id) },
      el('span', { class: 'rv-meta' }, el('span', { class: 'tag primary' }, cell(r.reason_code)), el('strong', null, custName(r.customer_id)), el('span', { class: 'mono' }, cell(r.disputed_order_id))),
      flags.length ? el('span', { class: 'tags' }, flags.map(f => el('span', { class: 'tag soft' }, f))) : null,
      el('span', { class: 'rv-excerpt' }, r.complaint_excerpt ? String(r.complaint_excerpt) : ''),
      el('span', { class: 'rv-when' }, `queued ${whenText(r.queued_at)} · ${r.case_id}`));
    btn.addEventListener('click', () => openReview(r.case_id));
    const li = el('li', { class: 'rv-item' }, btn,
      el('div', { class: 'done-check', 'aria-hidden': 'true' }, svg('svg', { viewBox: '0 0 64 64', width: 56, height: 56, focusable: 'false' }, svg('path', { pathLength: 1, d: checkPath(13, 16, 38) }))));
    li.dataset.case = String(r.case_id);
    ul.append(li);
  }
}
function markOpen() {
  Array.from($('#rv-list').children).forEach(li => { const b = li.querySelector('.rv-open'); if (b) b.setAttribute('aria-expanded', String(li.dataset.case === state.reviewOpenId)); });
}
async function openReview(id) {
  const box = $('#rv-detail');
  state.reviewOpenId = id; markOpen();
  box.replaceChildren(el('p', { class: 'hint' }, 'Loading case...'));
  let c;
  try { c = (await api('GET', '/cases/' + encodeURIComponent(id))).data; }
  catch (e) { if (state.reviewOpenId === id && e.status !== 401) box.replaceChildren(el('p', { class: 'msg', 'data-kind': 'error' }, errText(e))); return; }
  if (state.reviewOpenId !== id) return;
  renderReviewDetail(c);
  box.focus();
}
const field = (label, control, extra) => el('div', { class: 'field' }, el('label', { for: control.id }, label), control, extra);

function renderReviewDetail(c) {
  const box = $('#rv-detail');
  const pending = c.review && c.review.status === 'PENDING' && c.state === 'NEEDS_HUMAN';
  box.replaceChildren(
    el('section', { class: 'card pop-in' },
      el('h3', { class: 'card-title' }, `Case ${c.id}`),
      el('p', { class: 'cust-line' }, el('strong', null, custName(c.customer_id)), ' · order ', el('span', { class: 'mono' }, cell(c.disputed_order_id))),
      el('span', { class: 'label-k' }, 'Customer complaint'),
      el('blockquote', { class: 'quote' }, c.complaint == null ? '' : String(c.complaint))),
    dispositionCard(c, { small: true, noButton: true }), amountsCard(c), evidenceCard(c),
    pending ? opForm(c) : completedCard(c));
  inkAll(box);
}
function completedCard(c) {
  const r = c.review;
  if (!r) return card('Operator disposition', el('p', { class: 'hint' }, 'This case has no review record. It is not waiting for an operator.'));
  const orig = r.original_outcome == null ? unavailable() : el('code', null, typeof r.original_outcome === 'string' ? r.original_outcome : JSON.stringify(r.original_outcome));
  const kv = (k, v) => [el('dt', null, k), el('dd', null, v)];
  return card(r.status === 'COMPLETED' ? 'Review completed' : 'Review status',
    el('dl', { class: 'kv' }, kv('Status', cell(r.status)), kv('Queue reason', cell(r.reason_code)), kv('Operator', cell(r.operator_id)),
      kv('Decision', cell(r.operator_disposition)), kv('Note', r.operator_note ? String(r.operator_note) : el('span', { class: 'na' }, 'none')),
      kv('Reviewed at', cell(r.reviewed_at)), kv('Original automated outcome', orig)),
    el('p', { class: 'hint cur-note' }, 'Recording a decision never executes a payment. The original automated outcome is retained.'));
}
function opForm(c) {
  const idIn = el('input', { type: 'text', id: 'op-id', maxlength: 64, autocomplete: 'off', spellcheck: 'false', required: true, 'aria-describedby': 'op-id-hint' });
  idIn.value = savedOperator();
  const sel = el('select', { id: 'op-decision', required: true }, opt('', 'Choose a decision...'), ...DECISIONS.map(([v, t]) => opt(v, `${t} (${v})`)));
  const note = el('textarea', { id: 'op-note', maxlength: 500, rows: 3, 'aria-describedby': 'op-count' });
  const cnt = el('p', { class: 'counter', id: 'op-count' }, '0 / 500');
  note.addEventListener('input', () => { cnt.textContent = `${note.value.length} / 500`; });
  const msg = el('p', { class: 'msg', role: 'alert' });
  const btn = el('button', { type: 'submit', class: 'btn primary' }, 'Record decision');
  const form = el('form', { class: 'op-form', novalidate: true },
    field('Operator ID', idIn, el('p', { class: 'hint', id: 'op-id-hint' }, 'An audit label, not a login. Letters, digits and . _ @ - (up to 64).')),
    field('Decision', sel), field('Note (optional)', note, cnt),
    el('p', { class: 'hint' }, 'Recording a decision never executes a payment. The original automated outcome is kept.'),
    el('div', { class: 'actions' }, btn, msg));
  form.addEventListener('submit', ev => submitDecision(ev, c, { idIn, sel, note, msg, btn }));
  return el('section', { class: 'card pop-in' }, el('h3', { class: 'card-title' }, 'Operator disposition'), form);
}
async function submitDecision(ev, c, f) {
  ev.preventDefault();
  const operator_id = f.idIn.value.trim(), decision = f.sel.value, note = f.note.value.trim();
  const fail = (m, n) => { f.msg.textContent = m; f.msg.dataset.kind = 'error'; if (n) n.focus(); };
  if (!/^[A-Za-z0-9._@-]{1,64}$/.test(operator_id)) return fail('Operator ID must be 1 to 64 characters: letters, digits and . _ @ - only.', f.idIn);
  if (!decision) return fail('Choose a decision.', f.sel);
  if (note.length > 500) return fail('The note is over 500 characters.', f.note);
  f.btn.disabled = true; f.msg.textContent = ''; f.msg.dataset.kind = '';
  try {
    const { data } = await api('POST', `/reviews/${encodeURIComponent(c.id)}/disposition`, { operator_id, decision, note });
    saveOperator(operator_id);
    completeItem(c.id);
    if (state.reviewOpenId === c.id) renderReviewDetail(data);
    if (state.currentCase && state.currentCase.id === data.id) { state.currentCase = data; renderResult(data, false); }
    say('Decision recorded. Review completed. No payment was executed.');
  } catch (e) {
    f.btn.disabled = false;
    if (e.status === 401) return;
    if (e.status === 409) { fail(`Already completed: this review was already decided, or the case is no longer waiting for review. Nothing was changed.${e.detail ? ' (' + e.detail + ')' : ''}`); loadReviews(); }
    else if (e.status === 404) fail('Case not found.');
    else fail(errText(e));
    say('Decision not recorded');
  }
}
function completeItem(id) {
  state.reviews = state.reviews.filter(r => r.case_id !== id);
  setRvCount();
  const li = findItem(id);
  if (!li) return;
  const finish = () => { li.remove(); if (!state.reviews.length) renderReviewList(); };
  if (reduced()) { finish(); return; }
  li.classList.add('done');
  setTimeout(() => { li.classList.add('leaving'); setTimeout(finish, 850); }, 650);
}

// =================================================================== insights
function pairs(x) {
  const num = v => (Number.isFinite(Number(v)) ? Number(v) : 0);
  let out = [];
  if (Array.isArray(x)) {
    out = x.map(it => Array.isArray(it) ? [String(it[0]), num(it[1])]
      : it && typeof it === 'object' ? [String(it.name ?? it.key ?? it.label ?? it.disposition ?? it.flag ?? it.code ?? it.reason ?? '?'), num(it.count ?? it.n ?? it.value)]
        : [String(it), 0]);
  } else if (x && typeof x === 'object') {
    out = Object.entries(x).map(([k, v]) => [k, num(v && typeof v === 'object' ? (v.count ?? v.n ?? v.value) : v)]);
  }
  return out.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}
function barSvg(idx) {
  const r = prng(300 + idx * 17);
  const d = `M0 ${n1(1 + jit(r, 1))} Q50 ${n1(jit(r, 1.5))} 100 ${n1(1 + jit(r, 1))} L${n1(99 + jit(r, 1))} 13 L100 ${n1(25 + jit(r, 1))} Q50 ${n1(26 + jit(r, 1.5))} 0 ${n1(25 + jit(r, 1))} Z`;
  return svg('svg', { viewBox: '0 0 100 26', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' }, svg('path', { class: 'fill', d }), svg('path', { class: 'out', d }));
}
function chartCard(title, data, denom, overlapNote) {
  const bars = [];
  const list = data.length ? el('ul', { class: 'bars' }, data.map(([name, n], i) => {
    const bar = el('div', { class: 'bar' });
    if (n > 0) {
      bar.style.width = (denom > 0 ? Math.min(100, Math.max(4, (n / denom) * 100)) : 4) + '%';
      bar.style.setProperty('--d', (i * 0.09).toFixed(2) + 's');
      bar.append(barSvg(i)); bars.push(bar);
    }
    return el('li', { class: 'bar-row' }, el('span', { class: 'bar-label' }, name),
      el('div', { class: 'bar-track' }, bar), el('span', { class: 'bar-n' }, String(n), el('small', null, denom ? ` / ${denom}` : '')));
  })) : el('p', { class: 'hint' }, 'Nothing to chart for this run.');
  const c = card(title, list, overlapNote ? el('p', { class: 'overlap' }, el('strong', null, 'Categories overlap: one case can carry several flags'), '. Bars need not add up to the denominator.') : null);
  c.bars = bars;
  return c;
}
async function loadInsights(runId) {
  const st = $('#in-status');
  st.textContent = 'Loading...';
  try {
    const { data } = await api('GET', '/insights' + (runId ? '?run_id=' + encodeURIComponent(runId) : ''));
    renderInsights(data || {});
    st.textContent = '';
  } catch (e) { if (e.status !== 401) st.textContent = errText(e); }
}
function renderInsights(d) {
  const runs = Array.isArray(d.runs) ? d.runs : [];
  const sel = d.selected_run && typeof d.selected_run === 'object' ? d.selected_run.run_id : d.selected_run;
  const rs = $('#run-select');
  rs.replaceChildren(...(runs.length ? runs.map(r => opt(r.run_id, `${r.run_id} · ${r.run_mode} · ${r.case_count} cases`)) : [opt('', 'No runs yet')]));
  rs.disabled = !runs.length;
  if (sel) rs.value = sel;
  state.insightsRun = sel ? String(sel) : '';
  const denom = Number.isFinite(Number(d.denominator)) ? Number(d.denominator) : 0;
  const hyps = (Array.isArray(d.hypotheses) ? d.hypotheses : []).map(h => typeof h === 'string' ? h : (h && (h.text || h.hypothesis || h.title)) || '').filter(Boolean);
  const infs = pairs(d.by_inference_status);
  const disp = chartCard('By final disposition', pairs(d.by_disposition), denom, false);
  const flag = chartCard('By validated flag or reason', pairs(d.by_flag), denom, d.flags_overlap === true);
  const root = $('#insights');
  root.replaceChildren(
    el('div', { class: 'denom' }, el('span', { class: 'big' }, String(denom)),
      el('span', { class: 'what' }, `unique cases in this run (the denominator)${d.run_mode ? ' · run mode ' + d.run_mode : ''}. Retries and replays are not extra cases. Runs and modes are never pooled.`)),
    infs.length ? el('div', { class: 'block' }, el('span', { class: 'label-k' }, 'Inference status of these cases'),
      el('ul', { class: 'chips' }, infs.map(([k, n]) => el('li', { class: 'tag soft' }, `${k}: ${n}`))),
      el('p', { class: 'hint' }, 'Policy-skipped cases stay in their run with zero model calls.')) : null,
    el('div', { class: 'charts' }, disp, flag),
    card('Hypotheses to investigate',
      el('p', { class: 'hint' }, 'Questions a support lead could check. They are not findings and not causes.'),
      hyps.length ? el('ul', { class: 'notes' }, hyps.map((h, i) => {
        const n = el('li', { class: 'sticky' }, h);
        n.style.setProperty('--r', [-2, 1.5, -1, 2, -1.8, 1][i % 6] + 'deg');
        return n;
      })) : el('p', { class: 'hint' }, 'No hypotheses for this run.')));
  inkAll(root);
  const on = () => { disp.bars.concat(flag.bars).forEach(b => b.classList.add('on')); };
  if (reduced()) on(); else requestAnimationFrame(() => requestAnimationFrame(on));
}

// =================================================================== policy
function renderPolicy(p) {
  const root = $('#policy');
  const hash = typeof p.hash === 'string' ? p.hash : '';
  const exps = p.currency_exponent || {};
  const kv = (k, v) => [el('dt', null, k), el('dd', null, v)];
  const th = Object.entries(p.thresholds_minor || {}).map(([cur, minor]) => [td('mono', cur), td('num', fmtMoney(minor, cur)), td('mono', exps[cur] == null ? missing() : String(exps[cur]))]);
  const rules = Array.isArray(p.rules) ? p.rules : [];
  root.replaceChildren(
    el('div', { class: 'dl-grid' },
      card('Active policy', el('dl', { class: 'kv' },
        kv('Version', cell(p.version)),
        kv('Hash (shortened)', hash ? el('code', { title: hash }, hash.slice(0, 12) + '…') : missing()),
        kv('Supported currencies', Array.isArray(p.supported_currencies) && p.supported_currencies.length ? el('ul', { class: 'tags' }, p.supported_currencies.map(c => el('li', { class: 'tag' }, String(c)))) : missing()),
        kv('Authorization expiry', p.auth_expiry_days == null ? missing() : `${p.auth_expiry_days} days`),
        kv('Refund settlement expectation', p.refund_settlement_days == null ? missing() : `${p.refund_settlement_days} business days (an expectation, never a guarantee)`))),
      card('Automatic-recommendation thresholds',
        dataTable('Thresholds by currency', ['Currency', 'Threshold', 'Minor-unit exponent'], th, 'No thresholds'),
        el('p', { class: 'footnote' }, 'Above the threshold the case goes to a person. Amounts are integer minor units, formatted for display only.'))),
    card('Rules', rules.length ? el('ol', { class: 'rules' }, rules.map(r => el('li', null,
      el('span', { class: 'rid' }, String(r.id)), el('div', null, el('h4', null, cell(r.title)), el('p', null, r.text ? String(r.text) : ''))))) : el('p', { class: 'hint' }, 'No rules loaded.')));
  inkAll(root);
}

// =================================================================== boot
async function init() {
  state.locked = false;
  $('#gate').hidden = true; $('#fatal').hidden = true; $('#tabs-nav').hidden = false; $('#app').hidden = false;
  setBadges(state.currentCase);
  const [pol, cus, sce] = await Promise.allSettled([api('GET', '/policy'), api('GET', '/customers'), api('GET', '/scenarios')]);
  if (!$('#gate').hidden) return; // a 401 arrived while loading
  if (pol.status === 'fulfilled') { state.policy = pol.value.data || {}; renderPolicy(state.policy); }
  else $('#policy').replaceChildren(el('p', { class: 'msg', 'data-kind': 'error' }, `Could not load the policy: ${errText(pol.reason)}`));
  if (cus.status === 'fulfilled') {
    state.customers = Array.isArray(cus.value.data) ? cus.value.data : [];
    state.custById = new Map(state.customers.map(c => [c.id, c]));
    fillCustomers(state.customers);
  } else formMsg(`Could not load customers: ${errText(cus.reason)}`);
  if (sce.status === 'fulfilled') { state.scenarios = Array.isArray(sce.value.data) ? sce.value.data : []; fillScenarios(state.scenarios); }
  inkAll(document);
  selectTab(location.hash.slice(1) || 'case', false);
  loadReviews();
}
async function boot() {
  state.token = state.token || tokenStore.get();
  let res;
  try { res = await api('GET', '/health'); }
  catch (e) {
    if (e.status === 401) return; // gate is showing
    state.health = null; setBadges(null);
    if (e.code === 'auth_not_configured') return fatal('This server has no access token configured (auth_not_configured), so it refuses every request.');
    return fatal(`Could not reach NEXUS: ${errText(e)}`);
  }
  state.health = res.data || {};
  if (state.health.auth_required && !state.token) { showGate('This demo needs an access token.', 'info'); return; }
  await init();
}
function start() {
  ink($('#wordmark'), 'underline');
  initTabs(); initForm(); buildPipeline();
  $('#rv-refresh').addEventListener('click', loadReviews);
  $('#run-select').addEventListener('change', e => loadInsights(e.target.value));
  $('#gate-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const v = $('#gate-token').value.trim();
    if (!v) { const m = $('#gate-msg'); m.textContent = 'Enter the access token.'; m.dataset.kind = 'error'; return; }
    state.token = v; tokenStore.set(v); $('#gate-token').value = '';
    await boot();
  });
  document.addEventListener('click', e => closeTips(e.target));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTips(); });
  boilSync();
  boot();
}
start();
})();
