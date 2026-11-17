/* FoodFlow front end: vanilla JS, no build step. Views register themselves on V (see ops.js for the rest). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const inr = p => (p < 0 ? '-' : '') + '₹' + (Math.abs(p) / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inrf = p => p == null ? '–' : inr(p);
const qty = m => (m / 1000).toLocaleString('en-IN', { maximumFractionDigits: 3 });
const pct = bp => bp == null ? '–' : (bp > 0 ? '+' : '') + (bp / 100).toFixed(2) + '%';
const today = () => new Date().toISOString().slice(0, 10);
const monthStart = () => today().slice(0, 8) + '01';
const when = s => (s || '').replace('T', ' ').slice(0, 16);
const chip = (txt, cls) => `<span class="chip ${esc(cls || txt)}">${esc(String(txt).replace('_', ' '))}</span>`;
const UNIT = { kg: 'kg', l: 'L', pc: 'pcs', pkt: 'packets' };

const S = { user: null, meta: null, offline: false };
const V = {}; // view registry: V.name = async (arg, el) => void

const ICONS = {
  grid: 'M4 4h7v7H4z M13 4h7v4h-7z M13 10h7v10h-7z M4 13h7v7H4z', home: 'M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z',
  orders: 'M9 4h6a1 1 0 0 1 1 1v1H8V5a1 1 0 0 1 1-1z M6 6h12v15H6z M9 11h6 M9 15h6', invoice: 'M7 3h8l4 4v14H7z M15 3v4h4 M10 12h6 M10 16h6',
  wallet: 'M4 7h14a2 2 0 0 1 2 2v9H6a2 2 0 0 1-2-2z M4 7l12-3v3 M16 13h2', factory: 'M3 21V10l6 4v-4l6 4V6h6v15z',
  box: 'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10', cart: 'M3 4h2l2 12h11l2-8H7 M9 20h.01 M17 20h.01',
  sliders: 'M4 6h10 M18 6h2 M4 12h4 M12 12h8 M4 18h12 M14 4v4 M10 10v4 M16 16v4', chart: 'M4 20V4 M4 20h16 M8 16v-5 M12 16V8 M16 16v-3',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4', plus: 'M12 5v14M5 12h14'
};
const icon = n => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[n]}"/></svg>`;

const NAV = {
  admin: [['dash', 'Overview', 'grid'], ['orders', 'Orders', 'orders'], ['invoices', 'Invoices', 'invoice'], ['receivables', 'Receivables', 'wallet'], ['production', 'Production', 'factory'],
    ['stock', 'Stock', 'box'], ['purchases', 'Purchases', 'cart'], ['masters', 'Masters', 'sliders'], ['reports', 'Reports', 'chart'], ['audit', 'Audit trail', 'shield']],
  salesman: [['home', 'Home', 'home'], ['neworder', 'New order', 'plus'], ['myorders', 'My orders', 'orders']],
  production: [['dash', 'Overview', 'grid'], ['production', 'Production', 'factory'], ['stock', 'Stock', 'box'], ['purchases', 'Purchases', 'cart'], ['masters', 'Recipes & items', 'sliders'], ['reports', 'Reports', 'chart']],
  accountant: [['reports', 'Reports', 'chart'], ['invoices', 'Invoices', 'invoice'], ['receivables', 'Receivables', 'wallet'], ['stock', 'Stock', 'box'], ['purchases', 'Purchases', 'cart'], ['audit', 'Audit trail', 'shield']],
  owner: [['dash', 'Overview', 'grid'], ['reports', 'Reports', 'chart'], ['orders', 'Orders', 'orders'], ['invoices', 'Invoices', 'invoice'], ['receivables', 'Receivables', 'wallet'], ['production', 'Production', 'factory'], ['stock', 'Stock', 'box'], ['audit', 'Audit trail', 'shield']]
};
const ROLE_NAME = { admin: 'Factory admin', salesman: 'Salesman', production: 'Production supervisor', accountant: 'Accountant / auditor', owner: 'Owner' };

/* ---------- network ---------- */
async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o); // a TypeError here means "no network"
  let data = {};
  try { data = await r.json(); } catch { /* non-JSON */ }
  if (r.status === 401 && S.user && !path.includes('/login')) { S.user = null; renderLogin(); throw new Error('Please sign in again'); }
  if (!r.ok) { const e = new Error(data.error || 'Something went wrong'); e.data = data; e.status = r.status; throw e; }
  return data;
}
const post = (p, body = {}) => api(p, { method: 'POST', body });
const put = (p, body = {}) => api(p, { method: 'PUT', body });

function toast(msg, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg; $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 6000 : 3200);
}
async function act(btn, fn, okMsg) { // run a mutation with a disabled button and a toast
  if (btn) btn.disabled = true;
  try { const r = await fn(); if (okMsg) toast(okMsg, 'ok'); return r; }
  catch (e) { toast(e.message, 'err'); throw e; }
  finally { if (btn) btn.disabled = false; }
}

/* ---------- modal ---------- */
function modal(html, cls = '') {
  const scrim = document.createElement('div'); scrim.className = 'scrim';
  scrim.innerHTML = `<div class="sheet ${cls}" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => { scrim.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(scrim);
  scrim.close = close;
  $$('[data-close]', scrim).forEach(b => b.onclick = close);
  const first = $('input,select,textarea', scrim); if (first && !first.readOnly) first.focus();
  return scrim;
}
const sheetHead = (title, sub = '') => `<div class="sheethead"><div><h2>${title}</h2>${sub ? `<div class="muted small">${sub}</div>` : ''}</div><button class="x" data-close aria-label="Close">×</button></div>`;
const table = (cols, rows, opts = {}) => rows.length ? `<div class="tablewrap"><table><thead><tr>${cols.map(c => `<th class="${c.cls || ''}">${c.h}</th>`).join('')}</tr></thead><tbody>${rows.map((r, i) =>
  `<tr class="${opts.click ? 'click' : ''}" data-i="${i}">${cols.map(c => `<td class="${c.cls || ''}">${c.f(r, i)}</td>`).join('')}</tr>`).join('')}</tbody>${opts.foot || ''}</table></div>` : `<div class="empty">${opts.empty || 'Nothing here yet.'}</div>`;

/* ---------- boot / auth ---------- */
const cached = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem(k)); localStorage.setItem(k, JSON.stringify(v)); } catch { return null; } };

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => { });
  window.addEventListener('hashchange', route);
  window.addEventListener('online', () => { S.offline = false; syncQueue(true); route(); });
  window.addEventListener('offline', () => { S.offline = true; route(); });
  try { S.user = (await api('/api/me')).user; cached('ff_user', S.user); }
  catch (e) {
    if (e instanceof TypeError) { const u = cached('ff_user'); if (u && u.role === 'salesman') { S.user = u; S.offline = true; } }
  }
  if (!S.user) return renderLogin();
  await startApp();
}

async function startApp() {
  await loadMeta();
  if (S.user.role === 'salesman') { syncQueue(); setInterval(() => syncQueue(), 30000); }
  if (!location.hash || location.hash === '#/') location.hash = '#/' + NAV[S.user.role][0][0];
  renderShell(); route();
}

async function loadMeta() {
  try { S.meta = await api('/api/meta'); cached('ff_meta', S.meta); }
  catch (e) { if (e instanceof TypeError && cached('ff_meta')) { S.meta = cached('ff_meta'); S.offline = true; } else throw e; }
}

function renderLogin() {
  const demo = [['admin', 'Factory admin', 'Approve, invoice, dispatch, collect'], ['salesman_a', 'Salesman A', 'Take orders on the phone'], ['salesman_b', 'Salesman B', 'Second field user'],
    ['production', 'Production', 'Recipes, runs, receipts'], ['accountant', 'Accountant', 'Read-only reports'], ['owner', 'Owner', 'Summary and alerts']];
  $('#app').innerHTML = `<div class="login"><div class="art"><div class="brand"><i>🥨</i>FoodFlow</div>
    <div><h1>Every packet traced, from the salesman's order to the auditor's report.</h1><p>Orders, GST invoices, recipes, stock and costing in one place, each entered once where it happens.</p></div>
    <div class="small" style="color:#CFE0D6">Kaveri Snacks &amp; Sweets · demo data</div></div>
    <div class="form"><form id="lf"><h1>Sign in</h1><p class="muted">Use your username and password.</p>
    <div class="field"><label for="u">Username</label><input id="u" autocomplete="username" autocapitalize="none" required></div>
    <div class="field"><label for="p">Password</label><input id="p" type="password" autocomplete="current-password" required></div>
    <button class="btn" style="width:100%">Sign in</button>
    <div class="demo">${demo.map(d => `<button type="button" data-u="${d[0]}"><b>${d[1]}</b><span>${d[2]}</span></button>`).join('')}</div>
    <p class="muted small">Demo password for every login: <b>demo123</b></p></form></div></div>`;
  $$('.demo button').forEach(b => b.onclick = () => { $('#u').value = b.dataset.u; $('#p').value = 'demo123'; $('#lf').requestSubmit(); });
  $('#lf').onsubmit = async e => {
    e.preventDefault();
    try { S.user = (await post('/api/login', { username: $('#u').value, password: $('#p').value })).user; cached('ff_user', S.user); location.hash = ''; await startApp(); }
    catch (er) { toast(er.message, 'err'); }
  };
}

function renderShell() {
  const nav = NAV[S.user.role];
  const link = (n, label, ic) => `<a href="#/${n}" data-n="${n}">${icon(ic)}<span>${label}</span></a>`;
  $('#app').innerHTML = `<div class="shell"><aside class="side"><div class="brand"><i>🥨</i>FoodFlow</div><nav class="nav">${nav.map(n => link(...n)).join('')}</nav>
    <div class="who"><b>${esc(S.user.name)}</b>${ROLE_NAME[S.user.role]}<br><button id="out">Sign out</button></div></aside>
    <div><div class="topbar"><div class="brand"><i>🥨</i>FoodFlow</div><button id="out2">Sign out</button></div><main class="main" id="main"></main></div></div>
    <nav class="bottomnav">${nav.slice(0, 6).map(n => link(...n)).join('')}</nav>`;
  $('#out').onclick = $('#out2').onclick = async () => { try { await post('/api/logout'); } catch { } S.user = null; cached('ff_user', null); location.hash = ''; renderLogin(); };
}

async function route() {
  if (!S.user || !$('#main')) return;
  const [, name, arg] = (location.hash || '#/').split('/');
  const allowed = NAV[S.user.role].map(n => n[0]);
  const page = allowed.includes(name) ? name : allowed[0];
  $$('[data-n]').forEach(a => a.classList.toggle('on', a.dataset.n === page));
  const el = $('#main');
  el.innerHTML = S.offline ? '' : '';
  const bar = S.offline ? `<div class="netbar"><span><b>You are offline.</b> Orders you take are saved on this phone and sent when the network returns.</span></div>` : '';
  el.innerHTML = bar + '<div id="view"><div class="empty">Loading…</div></div>';
  try { await V[page](arg, $('#view')); }
  catch (e) { if (e instanceof TypeError) { $('#view').innerHTML = `<div class="empty">No connection. This screen needs the network.</div>`; } else $('#view').innerHTML = `<div class="callout warn">${esc(e.message)}</div>`; }
}
const head = (title, sub, right = '') => `<div class="pagehead"><div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div><div>${right}</div></div>`;
const reload = () => route();

/* ---------- offline order queue ---------- */
const queue = () => cached('ff_queue') || [];
async function syncQueue(announce) {
  let q = queue(); if (!q.length || !navigator.onLine) return;
  let sent = 0;
  for (const o of [...q]) {
    try { await post('/api/orders', o); q = queue().filter(x => x.client_uuid !== o.client_uuid); cached('ff_queue', q); sent++; }
    catch (e) {
      if (e instanceof TypeError) break; // still offline
      q = queue().filter(x => x.client_uuid !== o.client_uuid); cached('ff_queue', q); toast(`An offline order was rejected: ${e.message}`, 'err');
    }
  }
  if (sent && (announce || true)) { toast(`${sent} offline order${sent > 1 ? 's' : ''} sent to the factory`, 'ok'); if (/home|myorders/.test(location.hash)) reload(); }
}
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : 'o-' + Date.now() + Math.random().toString(16).slice(2));

/* ---------- salesman ---------- */
V.home = async (_, el) => {
  const q = queue(); let sum = [], orders = [];
  if (!S.offline) { [sum, orders] = await Promise.all([api('/api/my/summary'), api('/api/orders')]); }
  el.innerHTML = head(`Hello, ${esc(S.user.name)}`, 'Your customers and orders at a glance.', `<a class="btn gold" href="#/neworder">${icon('plus').replace('<svg', '<svg width="18" height="18" style="stroke:currentColor;fill:none;stroke-width:2"')}New order</a>`) +
    (q.length ? `<div class="callout info"><b>${q.length} order${q.length > 1 ? 's' : ''} waiting to send.</b> ${S.offline ? 'They will go as soon as you have signal.' : 'Sending now…'} <button class="btn sm alt" id="syncnow">Send now</button></div>` : '') +
    `<div class="panel"><h2>Your sales by customer</h2>${table([{ h: 'Customer', f: r => `<b>${esc(r.name)}</b><div class="muted small">${esc(r.state)}</div>` }, { h: 'Orders', cls: 'num', f: r => r.orders },
      { h: 'Awaiting factory', cls: 'num', f: r => inr(r.pending_paise) }, { h: 'Invoiced', cls: 'num', f: r => inr(r.invoiced_paise) }], sum, { empty: S.offline ? 'Connect to see your sales.' : 'No orders yet. Tap New order to start.' })}</div>
    <div class="panel"><h2>Recent orders</h2>${ordersTable(orders.slice(0, 8))}</div>`;
  $('#syncnow') && ($('#syncnow').onclick = () => syncQueue(true));
  bindOrderRows(el, orders);
};

function ordersTable(rows) {
  return table([{ h: 'Order', f: r => `<b>${esc(r.order_no)}</b><div class="muted small">${when(r.created_at)}</div>` }, { h: 'Customer', f: r => esc(r.customer) + (S.user.role !== 'salesman' ? `<div class="muted small">by ${esc(r.salesman)}</div>` : '') },
    { h: 'Packets', cls: 'num', f: r => r.pkts }, { h: 'Value', cls: 'num', f: r => inr(r.total_paise) }, { h: 'Status', f: r => chip(r.status) }], rows, { click: true, empty: 'No orders yet.' });
}
function bindOrderRows(el, rows) { $$('tbody tr.click', el).forEach(tr => tr.onclick = () => openOrder(rows[+tr.dataset.i].id)); }

V.myorders = async (_, el) => {
  const rows = await api('/api/orders');
  el.innerHTML = head('My orders', 'Status updates as the factory approves, invoices and dispatches.') + `<div class="panel">${ordersTable(rows)}</div>`;
  bindOrderRows(el, rows);
};

V.neworder = async (_, el) => {
  const { customers, products, prices } = S.meta; const cart = {}; let unit = {};
  const priceFor = (cid, p) => { const o = prices.find(x => x.customer_id == cid && x.product_id == p.id); return o ? o.price_paise : p.price_paise; };
  el.innerHTML = head('New order', 'Pick a customer, set quantities, send. Works without signal.') + `<div class="panel"><div class="field"><label for="cust">Customer</label>
    <select id="cust"><option value="">Choose a customer…</option>${customers.map(c => `<option value="${c.id}">${esc(c.name)} · ${esc(c.state)}</option>`).join('')}</select></div>
    <div class="pick" id="lines"></div><div class="field" style="margin-top:12px"><label for="note">Note for the factory (optional)</label><input id="note" placeholder="e.g. deliver before Friday"></div>
    <div class="sticky-total"><div><div class="small" style="opacity:.8" id="cnt">No items</div><b id="tot">₹0.00</b></div><button class="btn gold" id="send">Send order</button></div></div>`;
  const draw = () => {
    const cid = $('#cust').value;
    $('#lines').innerHTML = products.map(p => {
      const u = unit[p.id] || 'pkt', q = cart[p.id] || 0;
      return `<div class="line"><div><b>${esc(p.name)}</b><div class="muted small">${cid ? inr(priceFor(cid, p)) : inr(p.price_paise)} per packet · ${p.packets_per_carton} per carton</div></div>
        <select data-u="${p.id}" aria-label="Unit"><option value="pkt" ${u === 'pkt' ? 'selected' : ''}>packets</option><option value="carton" ${u === 'carton' ? 'selected' : ''}>cartons</option></select>
        <div class="stepper"><button type="button" data-m="${p.id}" aria-label="Less">−</button><input inputmode="numeric" data-q="${p.id}" value="${q || ''}" placeholder="0" aria-label="Quantity"><button type="button" data-p="${p.id}" aria-label="More">+</button></div></div>`;
    }).join('');
    $$('[data-u]').forEach(s => s.onchange = () => { unit[s.dataset.u] = s.value; total(); });
    $$('[data-m]').forEach(b => b.onclick = () => { const id = b.dataset.m; cart[id] = Math.max(0, (cart[id] || 0) - ((unit[id] || 'pkt') === 'pkt' ? 10 : 1)); draw(); });
    $$('[data-p]').forEach(b => b.onclick = () => { const id = b.dataset.p; cart[id] = (cart[id] || 0) + ((unit[id] || 'pkt') === 'pkt' ? 10 : 1); draw(); });
    $$('[data-q]').forEach(i => i.oninput = () => { cart[i.dataset.q] = Math.max(0, parseInt(i.value) || 0); total(); });
    total();
  };
  const total = () => {
    const cid = $('#cust').value; let t = 0, n = 0;
    for (const p of products) { const q = cart[p.id] || 0, pk = q * ((unit[p.id] || 'pkt') === 'carton' ? p.packets_per_carton : 1); n += pk; t += pk * priceFor(cid, p); }
    $('#tot').textContent = inr(t); $('#cnt').textContent = n ? `${n} packets` : 'No items';
  };
  $('#cust').onchange = draw; draw();
  $('#send').onclick = async () => {
    const cid = $('#cust').value; if (!cid) return toast('Choose a customer first', 'err');
    const lines = products.filter(p => cart[p.id] > 0).map(p => ({ product_id: p.id, qty: cart[p.id], unit: unit[p.id] || 'pkt' }));
    if (!lines.length) return toast('Add at least one product', 'err');
    const payload = { customer_id: +cid, lines, note: $('#note').value, client_uuid: uuid(), created_at: new Date().toISOString().slice(0, 19).replace('T', ' ') };
    const b = $('#send'); b.disabled = true;
    try { const r = await post('/api/orders', payload); toast(`Order ${r.order.order_no} sent`, 'ok'); location.hash = '#/myorders'; }
    catch (e) {
      if (e instanceof TypeError) { cached('ff_queue', [...queue(), payload]); toast('No signal: order saved on this phone and will send automatically', 'ok'); location.hash = '#/home'; }
      else { toast(e.message, 'err'); b.disabled = false; }
    }
  };
};

/* ---------- orders (admin / read-only for owner & accountant) ---------- */
V.orders = async (_, el) => {
  let status = '';
  const draw = async () => {
    const rows = await api('/api/orders' + (status ? '?status=' + status : ''));
    $('#olist').innerHTML = ordersTable(rows); bindOrderRows($('#olist'), rows);
  };
  el.innerHTML = head('Orders', 'Verify price and stock, then approve and invoice.') + `<div class="panel"><div class="tabs" id="otabs">${[['', 'All'], ['submitted', 'To verify'], ['approved', 'Approved'], ['invoiced', 'Invoiced'], ['dispatched', 'Dispatched'], ['rejected', 'Rejected']]
    .map(([v, l]) => `<button data-s="${v}" class="${v === status ? 'on' : ''}">${l}</button>`).join('')}</div><div id="olist"></div></div>`;
  $$('#otabs button').forEach(b => b.onclick = () => { status = b.dataset.s; $$('#otabs button').forEach(x => x.classList.toggle('on', x === b)); draw(); });
  await draw();
};

async function openOrder(id) {
  const o = await api('/api/orders/' + id), isAdmin = S.user.role === 'admin', canEdit = isAdmin && ['submitted', 'approved'].includes(o.status);
  const m = modal('');
  const paint = o => {
    $('.sheet', m).innerHTML = sheetHead(`${esc(o.order_no)} ${chip(o.status)}`, `${esc(o.customer_name)} · ${esc(o.customer_state)} · by ${esc(o.salesman_name)} · ${when(o.created_at)}`) +
      (o.note ? `<p class="muted">“${esc(o.note)}”</p>` : '') + (o.reject_reason ? `<div class="callout warn">Rejected: ${esc(o.reject_reason)}</div>` : '') +
      (['submitted', 'approved'].includes(o.status) && o.short && isAdmin ? `<div class="callout warn"><b>Stock does not cover this order.</b> Short items are marked below. You can still approve; invoicing will ask you to reduce quantities to what is in stock.</div>` :
        ['submitted', 'approved'].includes(o.status) && isAdmin ? `<div class="callout ok">Finished-goods stock covers every line.</div>` : '') +
      table([{ h: 'Product', f: l => esc(l.name) }, { h: 'Qty (packets)', cls: 'num', f: l => canEdit ? `<input data-q="${l.id}" value="${l.qty_pkts}" style="width:84px;text-align:right">` : l.qty_pkts },
        { h: 'Price', cls: 'num', f: l => canEdit ? `<input data-p="${l.id}" value="${(l.price_paise / 100).toFixed(2)}" style="width:92px;text-align:right">` : inr(l.price_paise) },
        { h: 'In stock', cls: 'num', f: l => ['submitted', 'approved'].includes(o.status) && isAdmin ? (l.short_pkts ? `<span class="neg"><b>${l.stock_pkts}</b> (short ${l.short_pkts})</span>` : `<span class="pos">${l.stock_pkts}</span>`) : '–' },
        { h: 'Amount', cls: 'num', f: l => inr(l.amount_paise) }], o.lines, { foot: `<tfoot><tr><td colspan="4">Order value before GST</td><td class="num">${inr(o.total_paise)}</td></tr></tfoot>` }) +
      `<div class="actions">${canEdit ? `<button class="btn alt" id="save">Save changes</button>` : ''}${isAdmin && o.status === 'submitted' ? `<button class="btn" id="appr">Approve</button>` : ''}
      ${isAdmin && o.status === 'approved' ? `<button class="btn gold" id="inv">Create GST invoice</button>` : ''}${isAdmin && ['submitted', 'approved'].includes(o.status) ? `<button class="btn warn" id="rej">Reject</button>` : ''}
      ${o.invoice ? `<button class="btn alt" id="goinv">Open ${esc(o.invoice.invoice_no)}</button>` : ''}</div>`;
    $$('[data-close]', m).forEach(b => b.onclick = m.close);
    const bind = (id, fn) => $(id, m) && ($(id, m).onclick = fn);
    bind('#save', async e => paint(await act(e.target, () => put('/api/orders/' + id, { lines: o.lines.map(l => ({ product_id: l.product_id, qty: $(`[data-q="${l.id}"]`, m).value, price: $(`[data-p="${l.id}"]`, m).value })) }), 'Order updated')));
    bind('#appr', async e => paint(await act(e.target, () => post(`/api/orders/${id}/approve`), 'Order approved')));
    bind('#rej', () => { const r = prompt('Reason for rejecting this order?'); if (r) act(null, () => post(`/api/orders/${id}/reject`, { reason: r }), 'Order rejected').then(paint); });
    bind('#goinv', () => { m.close(); openInvoice(o.invoice.id); });
    bind('#inv', async e => {
      try { const r = await act(e.target, () => post(`/api/orders/${id}/invoice`, {}), 'Invoice created'); m.close(); reload(); openInvoice(r.id); }
      catch (er) {
        if (er.data && er.data.shortages) {
          const s = er.data.shortages.map(x => `${x.product}: ordered ${x.ordered}, in stock ${x.available} (short ${x.short})`).join('\n');
          if (confirm(`Not enough finished goods:\n\n${s}\n\nInvoice only what is in stock? The order lines will be reduced and the change is logged.`)) {
            const r = await act(e.target, () => post(`/api/orders/${id}/invoice`, { fit_to_stock: true }), 'Invoice created for available stock'); m.close(); reload(); openInvoice(r.id);
          }
        }
      }
    });
  };
  paint(o);
}

/* ---------- invoices ---------- */
V.invoices = async (_, el) => {
  const rows = await api('/api/invoices');
  el.innerHTML = head('Invoices', 'GST tax invoices numbered by financial year. Cancel with a credit note, never delete.') + `<div class="panel">${table([
    { h: 'Invoice', f: r => `<b>${esc(r.invoice_no)}</b><div class="muted small">${r.date}</div>` }, { h: 'Customer', f: r => esc(r.cust_name) },
    { h: 'Tax', f: r => r.supply_type === 'intra' ? 'CGST + SGST' : 'IGST' }, { h: 'Total', cls: 'num', f: r => inr(r.total_paise) },
    { h: 'Balance', cls: 'num', f: r => r.status === 'cancelled' ? '–' : inr(r.balance_paise) },
    { h: 'Status', f: r => chip(r.status) + (r.status === 'posted' ? ' ' + (r.dispatched_at ? chip('dispatched') : chip('awaiting dispatch', 'approved')) : '') }], rows, { click: true, empty: 'No invoices yet.' })}</div>`;
  $$('tbody tr.click', el).forEach(tr => tr.onclick = () => openInvoice(rows[+tr.dataset.i].id));
};

async function openInvoice(id) {
  const inv = await api('/api/invoices/' + id), isAdmin = S.user.role === 'admin', intra = inv.supply_type === 'intra';
  const m = modal(''), url = `${location.origin}/i/${inv.share_token}`;
  const text = encodeURIComponent(`Invoice ${inv.invoice_no} from Kaveri Snacks & Sweets for ${inr(inv.total_paise)}.\n${url}`);
  const phone = (inv.customer_phone || '').replace(/\D/g, '');
  $('.sheet', m).innerHTML = sheetHead(`${esc(inv.invoice_no)} ${chip(inv.status)}`, `${inv.date} · ${esc(inv.cust_name)} · ${esc(inv.cust_gstin || 'Unregistered')} · place of supply ${esc(inv.place_of_supply)} (${inv.place_code})`) +
    (inv.credit_note ? `<div class="callout warn">Cancelled by credit note <b>${esc(inv.credit_note.cn_no)}</b> on ${inv.credit_note.date}: ${esc(inv.credit_note.reason)}${inv.credit_note.stock_returned ? ' · goods returned to stock' : ''}</div>` : '') +
    table([{ h: 'Item', f: l => `${esc(l.description)}<div class="muted small">HSN ${esc(l.hsn)}</div>` }, { h: 'Qty', cls: 'num', f: l => l.qty_pkts }, { h: 'Rate', cls: 'num', f: l => inr(l.rate_paise) },
      { h: 'Taxable', cls: 'num', f: l => inr(l.taxable_paise) }, ...(intra ? [{ h: 'CGST', cls: 'num', f: l => inr(l.cgst_paise) }, { h: 'SGST', cls: 'num', f: l => inr(l.sgst_paise) }] : [{ h: 'IGST', cls: 'num', f: l => inr(l.igst_paise) }])], inv.lines,
      { foot: `<tfoot><tr><td colspan="3">Totals</td><td class="num">${inr(inv.taxable_paise)}</td>${intra ? `<td class="num">${inr(inv.cgst_paise)}</td><td class="num">${inr(inv.sgst_paise)}</td>` : `<td class="num">${inr(inv.igst_paise)}</td>`}</tr>
      <tr><td colspan="${intra ? 6 : 5}">Invoice total</td><td class="num">${inr(inv.total_paise)}</td></tr></tfoot>` }) +
    `<div class="grid g2" style="margin-top:14px"><div><h3>Payments</h3>${inv.payments.length ? inv.payments.map(p => `<div class="small">${p.paid_on} · ${esc(p.mode)} ${esc(p.ref || '')} · <b>${inr(p.amount_paise)}</b></div>`).join('') : '<div class="muted small">No payments yet.</div>'}</div>
    <div class="right"><div class="muted small">Balance due</div><b style="font-family:var(--display);font-size:26px" class="${inv.balance_paise > 0 && inv.status === 'posted' ? 'neg' : 'pos'}">${inv.status === 'cancelled' ? '–' : inr(inv.balance_paise)}</b></div></div>
    <div class="actions"><a class="btn alt" target="_blank" href="/api/invoices/${id}/pdf">Download PDF</a>
    <a class="btn alt" target="_blank" rel="noopener" href="https://wa.me/${phone ? '91' + phone.slice(-10) : ''}?text=${text}">Share on WhatsApp</a>
    <a class="btn alt" href="mailto:?subject=${encodeURIComponent('Invoice ' + inv.invoice_no)}&body=${text}">Email</a>
    ${isAdmin && inv.status === 'posted' && !inv.dispatched_at ? `<button class="btn" id="disp">Record dispatch</button>` : ''}
    ${isAdmin && inv.status === 'posted' && inv.balance_paise > 0 ? `<button class="btn gold" id="pay">Record payment</button>` : ''}
    ${isAdmin && inv.status === 'posted' ? `<button class="btn warn" id="cn">Cancel with credit note</button>` : ''}</div>
    ${inv.dispatched_at ? `<p class="muted small">Dispatched ${when(inv.dispatched_at)}. Finished-goods stock was reduced at that moment.</p>` : ''}`;
  $$('[data-close]', m).forEach(b => b.onclick = m.close);
  const bind = (s, f) => $(s, m) && ($(s, m).onclick = f);
  bind('#disp', async e => { await act(e.target, () => post(`/api/invoices/${id}/dispatch`), 'Dispatch recorded, stock reduced'); m.close(); reload(); openInvoice(id); });
  bind('#pay', () => paymentForm({ invoice_id: id, max: inv.balance_paise, title: `Payment against ${inv.invoice_no}`, done: () => { m.close(); reload(); openInvoice(id); } }));
  bind('#cn', async () => { const r = prompt('Reason for cancelling this invoice? A credit note will be issued.'); if (!r) return; const res = await act(null, () => post(`/api/invoices/${id}/cancel`, { reason: r }), 'Credit note issued'); m.close(); reload(); openInvoice(id); });
}

function paymentForm({ invoice_id, customer_id, max, title, done }) {
  const m = modal(sheetHead(title) + `<form id="pf"><div class="row"><div><label>Amount (₹)</label><input name="amount" inputmode="decimal" required ${max ? `value="${(max / 100).toFixed(2)}"` : ''}></div>
    <div><label>Date</label><input type="date" name="paid_on" value="${today()}"></div></div><div class="row" style="margin-top:12px"><div><label>Mode</label><select name="mode"><option>cash</option><option>upi</option><option>bank</option><option>cheque</option></select></div>
    <div><label>Reference</label><input name="ref" placeholder="UTR / cheque no."></div></div><div class="actions"><button class="btn">Save payment</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`, 'narrow');
  $('#pf', m).onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.invoice_id = invoice_id; f.customer_id = customer_id;
    await act($('button', e.target), () => post('/api/payments', f), 'Payment recorded'); m.close(); done && done();
  };
}

/* ---------- receivables ---------- */
V.receivables = async (_, el) => {
  const r = await api('/api/receivables'), cust = S.meta.customers;
  const asof = r.to;
  el.innerHTML = head('Receivables', `Outstanding by customer with ageing, as of ${asof}.`, S.user.role === 'admin' ? `<button class="btn gold" id="onacc">Record payment on account</button>` : '') +
    `<div class="panel">${table(r.columns.map((c, i) => ({ h: c.label, cls: c.type === 'money' ? 'num' : '', f: row => c.type === 'money' ? inr(row[c.key]) : esc(row[c.key]) })), r.rows,
      { empty: 'Nobody owes anything. All invoices are settled.', foot: `<tfoot><tr><td>Total</td><td></td>${r.columns.slice(2).map(c => `<td class="num">${inr(r.totals[c.key] || 0)}</td>`).join('')}</tr></tfoot>` })}</div>
    <p class="muted small">Receipts recorded without an invoice are applied to the oldest invoices first.</p>`;
  $('#onacc') && ($('#onacc').onclick = () => {
    const m = modal(sheetHead('Customer') + `<div class="field"><label>Customer</label><select id="cs">${cust.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></div><div class="actions"><button class="btn" id="go">Continue</button></div>`, 'narrow');
    $('#go', m).onclick = () => { const cid = +$('#cs', m).value; m.close(); paymentForm({ customer_id: cid, title: 'Payment on account', done: reload }); };
  });
};
