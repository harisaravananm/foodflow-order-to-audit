/* Operations views: dashboard, production, stock, purchases, masters, reports, audit. */

/* ---------- dashboard ---------- */
V.dash = async (_, el) => {
  const d = await api('/api/dashboard'), full = d.sales_month !== undefined;
  const low = d.stock.filter(s => s.low);
  el.innerHTML = head('Overview', `Today, ${new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}`) +
    `<div class="kpis"><div class="kpi"><span>Orders today</span><b>${d.orders_today}</b></div><div class="kpi ${d.orders_pending ? 'warn' : ''}"><span>Waiting to be invoiced</span><b>${d.orders_pending}</b></div>
    ${full ? `<div class="kpi"><span>Sales this month</span><b>${inr(d.sales_month)}</b></div><div class="kpi"><span>Collected this month</span><b>${inr(d.collected_month)}</b></div>
    <div class="kpi ${d.outstanding_total > 0 ? 'warn' : ''}"><span>Outstanding dues</span><b>${inr(d.outstanding_total)}</b></div>` : ''}
    <div class="kpi"><span>Stock value</span><b>${inr(d.stock_value_paise)}</b></div></div>
    <div class="grid split"><div class="panel"><h2>Stock on hand</h2>${table([{ h: 'Item', f: s => esc(s.name) + `<div class="muted small">${{ raw: 'Raw material', packaging: 'Packaging', fg: 'Finished goods' }[s.kind]}</div>` },
      { h: 'Quantity', cls: 'num', f: s => `${qty(s.qty_milli)} ${UNIT[s.unit]}` }, { h: 'Value', cls: 'num', f: s => inr(s.value_paise) }, { h: '', f: s => s.low ? chip('Low stock', 'Low') : '' }], d.stock)}</div>
    <div>${full && d.cost_per_packet.length ? costPanel(d.cost_per_packet) : ''}
    <div class="panel"><h2>Variance alerts</h2>${d.variance_alerts.length ? d.variance_alerts.map(a => `<div style="display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid var(--line)" class="${S.user.role !== 'accountant' ? '' : ''}">
      <span><b>${esc(a.what)}</b> <span class="muted small">${esc(a.run)}</span></span><span class="${a.bp > 0 ? 'neg' : 'neg'}"><b>${pct(a.bp)}</b></span></div>`).join('') : '<div class="empty">No variance beyond tolerance.</div>'}
      <p class="muted small" style="margin-bottom:0">Tolerance is ±${d.tolerance_bp / 100}% of plan.</p></div>
    ${low.length ? `<div class="panel"><h2>Reorder soon</h2>${low.map(s => `<div class="small" style="padding:4px 0"><b>${esc(s.name)}</b>: ${qty(s.qty_milli)} ${UNIT[s.unit]} left (reorder at ${qty(s.reorder_milli)})</div>`).join('')}</div>` : ''}</div></div>`;
};

function costPanel(list) {
  return `<div class="panel"><h2>Cost per packet</h2>${list.map(c => {
    if (!c.cost_per_packet_paise) return '';
    const cpp = c.cost_per_packet_paise, mat = cpp * c.material_paise / c.total_paise, oh = cpp - mat, price = c.price_paise, marg = Math.max(0, price - cpp);
    const w = v => (v / Math.max(price, cpp) * 100).toFixed(1) + '%';
    return `<div style="margin-bottom:14px"><div style="display:flex;justify-content:space-between;align-items:baseline"><b>${esc(c.product)}</b><span class="muted small">${esc(c.run)}</span></div>
      <div style="font-family:var(--display);font-size:30px;font-weight:700">${inr(cpp)} <span class="muted small" style="font-family:var(--font);font-weight:400">to make, sells at ${inr(price)}</span></div>
      <div class="costbar"><span style="width:${w(mat)};background:var(--leaf)"></span><span style="width:${w(oh)};background:var(--turmeric)"></span><span style="width:${w(marg)};background:var(--leaf-3)"></span></div>
      <div class="legend"><span><i style="background:var(--leaf)"></i>Materials ${inr(mat)}</span><span><i style="background:var(--turmeric)"></i>Labour, gas, power ${inr(oh)}</span><span><i style="background:var(--leaf-3);border:1px solid var(--line)"></i>Margin ${inr(price - cpp)} (${c.margin_pct}%)</span></div></div>`;
  }).join('')}</div>`;
}

/* ---------- production ---------- */
V.production = async (_, el) => {
  const rows = await api('/api/runs'), can = ['admin', 'production'].includes(S.user.role);
  el.innerHTML = head('Production', 'Each run issues raw material against the recipe, then books finished packets and shows where the plan was missed.', can ? `<button class="btn gold" id="newrun">Start a production run</button>` : '') +
    `<div class="panel">${table([{ h: 'Run', f: r => `<b>${esc(r.run_no)}</b><div class="muted small">${when(r.started_at)}</div>` }, { h: 'Product', f: r => `${esc(r.product)}<div class="muted small">Batch ${esc(r.batch_no)}</div>` },
      { h: 'Expected', cls: 'num', f: r => qty(r.expected_output_milli) }, { h: 'Actual', cls: 'num', f: r => r.actual_output_milli == null ? '–' : qty(r.actual_output_milli) },
      { h: 'Yield', cls: 'num', f: r => { if (r.actual_output_milli == null) return '–'; const bp = Math.round((r.actual_output_milli - r.expected_output_milli) * 10000 / r.expected_output_milli); return `<span class="${Math.abs(bp) > 300 ? 'neg' : 'pos'}">${pct(bp)}</span>`; } },
      { h: 'Status', f: r => chip(r.status) }], rows, { click: true, empty: 'No production runs yet.' })}</div>`;
  $$('tbody tr.click', el).forEach(tr => tr.onclick = () => openRun(rows[+tr.dataset.i].id));
  $('#newrun') && ($('#newrun').onclick = startRunForm);
};

const unitOptions = base => ({ kg: ['kg', 'g'], l: ['l', 'ml'], pc: ['pc'] }[base] || [base]).map(u => `<option value="${u}">${UNIT[u] || u}</option>`).join('');

async function startRunForm() {
  const products = S.meta.products;
  const m = modal(sheetHead('Start a production run', 'Raw materials are issued now. Packaging is booked when you record the output.') + `<div class="row"><div><label>Product</label><select id="rp">${products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></div>
    <div><label>Batches</label><input id="rb" value="1" inputmode="decimal"></div></div><div id="rl" style="margin-top:12px"></div><div class="actions"><button class="btn" id="go">Issue materials and start</button><button class="btn alt" data-close>Cancel</button></div>`);
  let bom = null;
  const draw = async () => {
    bom = await api(`/api/products/${$('#rp', m).value}/bom`); const b = parseFloat($('#rb', m).value) || 0;
    const raw = bom.lines.filter(l => l.category === 'raw');
    $('#rl', m).innerHTML = `<div class="callout info">Expected output: <b>${qty(bom.product.bom_yield_milli * b)} packets</b>. Adjust the quantities if the floor issued something different.</div>` +
      table([{ h: 'Raw material', f: l => esc(l.name) }, { h: 'Standard', cls: 'num', f: l => `${qty(l.qty_milli * b)} ${UNIT[l.unit]}` },
        { h: 'Issue now', cls: 'num', f: l => `<div style="display:flex;gap:6px;justify-content:flex-end"><input data-i="${l.item_id}" value="${qty(l.qty_milli * b).replace(/,/g, '')}" inputmode="decimal" style="width:90px;text-align:right"><select data-iu="${l.item_id}" style="width:74px">${unitOptions(l.unit)}</select></div>` }], raw);
  };
  $('#rp', m).onchange = draw; $('#rb', m).oninput = draw; await draw();
  $('#go', m).onclick = async e => {
    const issues = $$('[data-i]', m).map(i => ({ item_id: +i.dataset.i, qty: i.value, unit: $(`[data-iu="${i.dataset.i}"]`, m).value }));
    const r = await act(e.target, () => post('/api/runs', { product_id: +$('#rp', m).value, batches: $('#rb', m).value, issues }), 'Run started, raw material issued');
    m.close(); await route(); openRun(r.id);
  };
}

async function openRun(id) {
  const r = await api('/api/runs/' + id), can = ['admin', 'production'].includes(S.user.role), m = modal('');
  const done = r.status === 'completed';
  const lineRow = l => { const pl = l.planned_milli, ac = l.actual_milli, w = pl ? Math.min(100, ac / pl * 50) : 0;
    return `<tr><td>${esc(l.name)}<div class="muted small">${l.category}</div></td><td class="num">${qty(pl)} ${UNIT[l.unit]}</td><td class="num">${ac || !done && l.category === 'raw' ? qty(ac) + ' ' + UNIT[l.unit] : '–'}</td>
      <td style="width:150px"><div class="vbar ${l.flagged ? 'flag' : ''}" title="Black tick marks the plan"><i style="width:${w}%"></i><u style="left:50%"></u></div></td>
      <td class="num">${ac ? `<span class="${l.flagged ? 'neg' : ''}">${pct(l.variance_bp)}</span>${l.flagged ? ' ' + chip('flag', 'flag') : ''}` : '–'}</td><td class="num">${ac ? inr(l.value_paise) : '–'}</td></tr>`; };
  const c = r.costs;
  $('.sheet', m).innerHTML = sheetHead(`${esc(r.run_no)} ${chip(r.status)}`, `${esc(r.product_name)} · batch ${esc(r.batch_no)} · expires ${r.expiry_date} · ${qty(r.batches_milli)} batch${r.batches_milli === 1000 ? '' : 'es'}`) +
    `<div class="tablewrap"><table><thead><tr><th>Material</th><th class="num">Planned</th><th class="num">Actual</th><th>Actual against plan</th><th class="num">Variance</th><th class="num">Cost</th></tr></thead><tbody>${r.lines.map(lineRow).join('')}
    <tr><td><b>Finished packets</b></td><td class="num">${qty(r.expected_output_milli)}</td><td class="num">${done ? qty(r.actual_output_milli) : '–'}</td><td></td>
    <td class="num">${done ? `<span class="${r.yield_flagged ? 'neg' : ''}">${pct(r.yield_variance_bp)}</span>${r.yield_flagged ? ' ' + chip('flag', 'flag') : ''}` : '–'}</td><td></td></tr></tbody></table></div>` +
    (done ? `<p class="muted small">Wastage: ${qty(r.yield_loss_milli)} packets below expected yield · ${r.damaged_pouches} damaged pouches · ${r.scrap_packets} scrapped packets. Flags appear when a line is more than ±${r.tolerance_bp / 100}% off plan.</p>` : '') +
    (done ? `<div class="grid g2" style="margin-top:12px"><div class="panel"><h3>Cost of this run</h3><table><tr><td>Materials and packaging</td><td class="num">${inr(c.material_paise)}</td></tr>
      ${r.overheads.map(o => `<tr><td>${esc(o.kind)}${o.note ? ' · ' + esc(o.note) : ''}</td><td class="num">${inr(o.amount_paise)}</td></tr>`).join('')}<tr><td><b>Total</b></td><td class="num"><b>${inr(c.total_paise)}</b></td></tr></table>
      <div style="margin-top:10px" class="muted small">Cost per packet</div><b style="font-family:var(--display);font-size:30px">${inr(c.cost_per_packet_paise)}</b>
      <div class="small">Sells at ${inr(c.price_paise)} · margin ${inr(c.margin_paise)} (${c.margin_pct}%)</div></div>
      <div class="panel"><h3>Add labour, gas or power</h3>${can ? `<form id="ohf"><div class="field"><label>Type</label><select name="kind"><option>labour</option><option>gas</option><option>power</option><option>other</option></select></div>
      <div class="field"><label>Amount (₹)</label><input name="amount" inputmode="decimal" required></div><button class="btn sm">Add to this run</button></form>` : '<div class="muted small">Only production staff add overheads.</div>'}</div></div>` : '') +
    (!done && can ? `<h3 style="margin-top:16px">Record the output</h3><form id="cf"><div class="row"><div><label>Good packets made</label><input name="good_output" inputmode="numeric" required></div>
      <div><label>Damaged pouches</label><input name="damaged_pouches" inputmode="numeric" value="0"></div><div><label>Scrapped packets</label><input name="scrap_packets" inputmode="numeric" value="0"></div></div>
      <div class="row" style="margin-top:12px">${r.lines.filter(l => l.category === 'packaging').map(l => `<div><label>${esc(l.name)} used (${UNIT[l.unit]})</label><input data-pk="${l.item_id}" data-role="${l.role || ''}" inputmode="numeric"></div>`).join('')}</div>
      <p class="muted small">Pouches default to good plus damaged; cartons default to good packets ÷ ${S.meta.products.find(p => p.id === r.product_id)?.packets_per_carton || 20}, rounded up. Change them if the floor counted differently.</p>
      <div class="actions"><button class="btn gold">Save output and update stock</button></div></form>` : '');
  $$('[data-close]', m).forEach(b => b.onclick = m.close);
  const cf = $('#cf', m);
  if (cf) {
    const touched = new Set(), ppc = S.meta.products.find(p => p.id === r.product_id)?.packets_per_carton || 20;
    const calc = () => { const g = parseInt(cf.good_output.value) || 0, d = parseInt(cf.damaged_pouches.value) || 0, s = parseInt(cf.scrap_packets.value) || 0;
      $$('[data-pk]', cf).forEach(i => { if (touched.has(i.dataset.pk)) return; i.value = i.dataset.role === 'pouch' ? g + d + s : i.dataset.role === 'carton' ? Math.ceil(g / ppc) : r.lines.find(l => l.item_id == i.dataset.pk).planned_milli / 1000; }); };
    ['good_output', 'damaged_pouches', 'scrap_packets'].forEach(n => cf[n].oninput = calc);
    $$('[data-pk]', cf).forEach(i => i.addEventListener('input', e => { if (e.isTrusted) touched.add(i.dataset.pk); }));
    cf.onsubmit = async e => { e.preventDefault();
      const body = { good_output: cf.good_output.value, damaged_pouches: cf.damaged_pouches.value, scrap_packets: cf.scrap_packets.value, packaging: $$('[data-pk]', cf).map(i => ({ item_id: +i.dataset.pk, qty: i.value || 0 })) };
      await act($('button', cf), () => post(`/api/runs/${id}/complete`, body), 'Output recorded, stock updated'); m.close(); await route(); openRun(id); };
  }
  const ohf = $('#ohf', m);
  if (ohf) ohf.onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(ohf)); f.run_id = id; await act($('button', ohf), () => post('/api/overheads', f), 'Added'); m.close(); openRun(id); };
}

/* ---------- stock ---------- */
V.stock = async (_, el) => {
  let kind = 'all', from = monthStart(), to = today();
  const can = ['admin', 'production'].includes(S.user.role);
  el.innerHTML = head('Stock', 'Balances are the sum of the ledger. Click any item to see every movement and the document behind it.', can ? `<button class="btn alt" id="adj">Adjust stock</button>` : '') +
    `<div class="panel"><div class="tabs" id="kt">${[['all', 'Everything'], ['raw', 'Raw material'], ['packaging', 'Packaging'], ['fg', 'Finished goods']].map(([v, l]) => `<button data-k="${v}" class="${v === kind ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div class="row" style="margin-bottom:12px"><div><label>From</label><input type="date" id="f" value="${from}"></div><div><label>To</label><input type="date" id="t" value="${to}"></div></div><div id="sl"></div></div>`;
  const draw = async () => {
    const r = await api(`/api/reports/stock_summary?kind=${kind}&from=${from}&to=${to}`);
    $('#sl').innerHTML = reportTable(r, { click: true }); $$('#sl tbody tr.click').forEach(tr => tr.onclick = () => openLedger(r.rows[+tr.dataset.i], from, to));
  };
  $$('#kt button').forEach(b => b.onclick = () => { kind = b.dataset.k; $$('#kt button').forEach(x => x.classList.toggle('on', x === b)); draw(); });
  $('#f').onchange = e => { from = e.target.value; draw(); }; $('#t').onchange = e => { to = e.target.value; draw(); };
  $('#adj') && ($('#adj').onclick = adjustForm); await draw();
};

async function openLedger(row, from, to) {
  const key = (row.cat === 'Finished goods' ? 'p' : 'i') + row.id, r = await api(`/api/reports/stock_ledger?item=${key}&from=${from}&to=${to}`);
  const m = modal(sheetHead(esc(r.title), `${from} to ${to}`) + reportTable(r)); $$('[data-close]', m).forEach(b => b.onclick = m.close);
}

function adjustForm() {
  const { items, products } = S.meta;
  const m = modal(sheetHead('Adjust stock', 'For counts that differ from the books. A reason is required and the change is logged.') + `<form id="af"><div class="field"><label>Item</label><select name="k">${items.map(i => `<option value="i${i.id}" data-u="${i.unit}">${esc(i.name)}</option>`).join('')}${products.map(p => `<option value="p${p.id}" data-u="pkt">${esc(p.name)} (finished)</option>`).join('')}</select></div>
    <div class="row"><div><label>Quantity (use − to reduce)</label><input name="qty" inputmode="decimal" required></div><div class="fix"><label>Unit</label><select name="unit" id="au"></select></div></div>
    <div class="field" style="margin-top:12px"><label>Reason</label><input name="reason" required placeholder="e.g. physical count, spillage"></div><div class="actions"><button class="btn">Post adjustment</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`, 'narrow');
  const fillUnit = () => { const o = $('select[name=k] option:checked', m); $('#au', m).innerHTML = unitOptions(o.dataset.u === 'pkt' ? 'pkt' : o.dataset.u); }; fillUnit(); $('select[name=k]', m).onchange = fillUnit;
  $('#af', m).onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)), body = { qty: f.qty, unit: f.unit, reason: f.reason };
    f.k[0] === 'i' ? body.item_id = +f.k.slice(1) : body.product_id = +f.k.slice(1);
    await act($('button', e.target), () => post('/api/stock/adjust', body), 'Adjustment posted'); m.close(); reload(); };
}

/* ---------- purchases ---------- */
V.purchases = async (_, el) => {
  const rows = await api('/api/purchases'), can = ['admin', 'production'].includes(S.user.role);
  el.innerHTML = head('Purchases', 'Raw material and packaging receipts. Each one adds to stock at its purchase rate.', can ? `<button class="btn gold" id="np">Record a purchase</button>` : '') +
    `<div class="panel">${table([{ h: 'Purchase', f: r => `<b>${esc(r.purchase_no)}</b><div class="muted small">${r.bill_date}</div>` }, { h: 'Supplier', f: r => esc(r.supplier) }, { h: 'Supplier bill', f: r => esc(r.bill_no || '–') },
      { h: 'Input tax', cls: 'num', f: r => inr(r.tax_paise) }, { h: 'Total', cls: 'num', f: r => inr(r.total_paise) }], rows, { empty: 'No purchases recorded.' })}</div>`;
  $('#np') && ($('#np').onclick = purchaseForm);
};

function purchaseForm() {
  const { items, suppliers } = S.meta; let n = 0;
  const line = () => `<div class="row pl" style="margin-bottom:8px"><div style="flex:2 1 170px"><label>Item</label><select data-f="item_id">${items.map(i => `<option value="${i.id}" data-u="${i.unit}">${esc(i.name)}</option>`).join('')}</select></div>
    <div><label>Quantity</label><input data-f="qty" inputmode="decimal"></div><div class="fix" style="width:84px"><label>Unit</label><select data-f="unit"></select></div><div><label>Rate per unit (₹)</label><input data-f="rate" inputmode="decimal"></div><div class="fix" style="width:74px"><label>GST %</label><input data-f="gst_rate" value="0"></div></div>`;
  const m = modal(sheetHead('Record a purchase') + `<form id="pf2"><div class="row"><div><label>Supplier</label><select name="supplier_id">${suppliers.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div>
    <div><label>Supplier bill no.</label><input name="bill_no"></div><div><label>Bill date</label><input type="date" name="bill_date" value="${today()}"></div></div><div id="pls" style="margin-top:14px"></div>
    <button type="button" class="btn sm alt" id="addl">Add another item</button><div class="actions"><button class="btn">Save purchase</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`);
  const add = () => { const d = document.createElement('div'); d.innerHTML = line(); const row = d.firstChild; $('#pls', m).append(row);
    const s = $('[data-f=item_id]', row), fill = () => { $('[data-f=unit]', row).innerHTML = unitOptions(s.selectedOptions[0].dataset.u); }; s.onchange = fill; fill(); };
  add(); $('#addl', m).onclick = add;
  $('#pf2', m).onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    f.lines = $$('.pl', m).map(r => Object.fromEntries($$('[data-f]', r).map(i => [i.dataset.f, i.value]))).filter(l => l.qty);
    await act($('button', e.target), () => post('/api/purchases', f), 'Purchase recorded and stock updated'); m.close(); reload(); };
}

/* ---------- masters ---------- */
V.masters = async (_, el) => {
  const isAdmin = S.user.role === 'admin', tabs = isAdmin ? [['products', 'Products'], ['bom', 'Recipes (BOM)'], ['items', 'Raw & packaging'], ['customers', 'Customers'], ['suppliers', 'Suppliers'], ['settings', 'Settings']] : [['bom', 'Recipes (BOM)'], ['items', 'Raw & packaging']];
  let tab = tabs[0][0];
  el.innerHTML = head(isAdmin ? 'Masters' : 'Recipes and items', 'The reference data every transaction points to.') + `<div class="panel"><div class="tabs" id="mt">${tabs.map(([v, l]) => `<button data-t="${v}">${l}</button>`).join('')}</div><div id="mb"></div></div>`;
  const draw = async () => { $$('#mt button').forEach(b => b.classList.toggle('on', b.dataset.t === tab)); await M[tab]($('#mb')); };
  $$('#mt button').forEach(b => b.onclick = () => { tab = b.dataset.t; draw(); });
  await draw();
};
const refresh = async () => { await loadMeta(); reload(); };
const M = {};
M.products = async el => {
  const { products } = S.meta;
  el.innerHTML = `<div class="actions" style="margin:0 0 12px"><button class="btn sm" id="addp">Add a product or pack size</button></div>` + table([{ h: 'Product', f: p => `<b>${esc(p.name)}</b><div class="muted small">${esc(p.code)} · HSN ${esc(p.hsn)}</div>` }, { h: 'Pack', cls: 'num', f: p => `${p.pack_weight_g || '–'} g · ${p.packets_per_carton}/carton` },
    { h: 'Price', cls: 'num', f: p => inr(p.price_paise) }, { h: 'GST', cls: 'num', f: p => p.gst_rate_x100 / 100 + '%' }, { h: 'Yield / batch', cls: 'num', f: p => qty(p.bom_yield_milli) }], products, { click: true });
  $$('tbody tr.click', el).forEach(tr => tr.onclick = () => productForm(products[+tr.dataset.i])); $('#addp').onclick = () => productForm();
};
function productForm(p) {
  const f = p || { code: '', name: '', hsn: '21069099', gst_rate_x100: 500, pack_weight_g: 200, packets_per_carton: 20, price_paise: 0, shelf_life_days: 90, reorder_level_milli: 0 };
  const m = modal(sheetHead(p ? `Edit ${esc(p.name)}` : 'New product or pack size') + `<form id="pf3"><div class="row"><div><label>Code</label><input name="code" value="${esc(f.code)}" ${p ? 'readonly' : 'required'}></div><div style="flex:2 1 220px"><label>Name</label><input name="name" value="${esc(f.name)}" required></div></div>
    <div class="row" style="margin-top:12px"><div><label>HSN</label><input name="hsn" value="${esc(f.hsn)}"></div><div><label>GST %</label><input name="gst_rate" value="${f.gst_rate_x100 / 100}"></div><div><label>Selling price (₹, before tax)</label><input name="price" value="${(f.price_paise / 100).toFixed(2)}"></div></div>
    <div class="row" style="margin-top:12px"><div><label>Pack weight (g)</label><input name="pack_weight_g" value="${f.pack_weight_g || ''}"></div><div><label>Packets per carton</label><input name="packets_per_carton" value="${f.packets_per_carton}"></div>
    <div><label>Shelf life (days)</label><input name="shelf_life_days" value="${f.shelf_life_days}"></div><div><label>Reorder at (packets)</label><input name="reorder_level" value="${f.reorder_level_milli / 1000}"></div></div>
    ${p ? '' : `<div class="field" style="margin-top:12px"><label>Copy recipe from</label><select name="copy_bom_from"><option value="">Start with an empty recipe</option>${S.meta.products.map(x => `<option value="${x.id}">${esc(x.name)} (scales yield and packaging by pack weight)</option>`).join('')}</select></div>`}
    <div class="actions"><button class="btn">Save</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`);
  $('#pf3', m).onsubmit = async e => { e.preventDefault(); const b = Object.fromEntries(new FormData(e.target)); if (!b.copy_bom_from) delete b.copy_bom_from;
    await act($('button', e.target), () => p ? put('/api/products/' + p.id, b) : post('/api/products', b), 'Saved'); m.close(); refresh(); };
}
M.bom = async el => {
  const { products } = S.meta; el.innerHTML = `<div class="field" style="max-width:340px"><label>Product</label><select id="bp">${products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></div><div id="be"></div>`;
  const draw = async () => {
    const r = await api(`/api/products/${$('#bp').value}/bom`), items = S.meta.items, canEdit = ['admin', 'production'].includes(S.user.role);
    $('#be').innerHTML = `<form id="bf"><div class="row" style="margin-bottom:12px"><div style="max-width:260px"><label>Expected yield, packets per batch</label><input name="y" value="${qty(r.product.bom_yield_milli).replace(/,/g, '')}" ${canEdit ? '' : 'readonly'}></div></div>
      <div id="bl">${r.lines.map(l => bomRow(items, l)).join('')}</div>${canEdit ? `<div class="actions"><button type="button" class="btn sm alt" id="addb">Add material</button><button class="btn sm">Save recipe</button></div>` : ''}</form>`;
    if (!canEdit) return;
    $$('#bl select[data-f=item_id]').forEach(s => s.onchange = () => { $('[data-f=unit]', s.closest('.row')).innerHTML = unitOptions(s.selectedOptions[0].dataset.u); });
    $('#addb').onclick = () => { const d = document.createElement('div'); d.innerHTML = bomRow(items, { item_id: items[0].id, qty_milli: 0, unit: items[0].unit }); const row = d.firstChild; $('#bl').append(row);
      $('select[data-f=item_id]', row).onchange = e => { $('[data-f=unit]', row).innerHTML = unitOptions(e.target.selectedOptions[0].dataset.u); }; };
    $('#bf').onsubmit = async e => { e.preventDefault(); const lines = $$('#bl .row').map(row => ({ item_id: +$('[data-f=item_id]', row).value, qty: $('[data-f=qty]', row).value, unit: $('[data-f=unit]', row).value })).filter(l => parseFloat(l.qty) > 0);
      await act($('button:last-child', e.target), () => put(`/api/products/${$('#bp').value}/bom`, { yield_packets: e.target.y.value, lines }), 'Recipe saved'); await loadMeta(); draw(); };
  };
  $('#bp').onchange = draw; await draw();
};
const bomRow = (items, l) => `<div class="row" style="margin-bottom:8px"><div style="flex:2 1 200px"><select data-f="item_id">${items.map(i => `<option value="${i.id}" data-u="${i.unit}" ${i.id === l.item_id ? 'selected' : ''}>${esc(i.name)} (${i.category})</option>`).join('')}</select></div>
  <div><input data-f="qty" value="${l.qty_milli ? qty(l.qty_milli).replace(/,/g, '') : ''}" inputmode="decimal" placeholder="quantity per batch"></div><div class="fix" style="width:84px"><select data-f="unit">${unitOptions(items.find(i => i.id === l.item_id)?.unit || 'kg')}</select></div></div>`;

M.items = async el => {
  const { items } = S.meta;
  el.innerHTML = `<div class="actions" style="margin:0 0 12px"><button class="btn sm" id="addi">Add an item</button></div>` + table([{ h: 'Item', f: i => `<b>${esc(i.name)}</b><div class="muted small">${esc(i.code)}</div>` }, { h: 'Type', f: i => i.category + (i.packaging_role ? ` · ${i.packaging_role}` : '') },
    { h: 'Base unit', f: i => UNIT[i.unit] }, { h: 'Reorder at', cls: 'num', f: i => i.reorder_level_milli ? qty(i.reorder_level_milli) : '–' }], items);
  $('#addi').onclick = () => { const m = modal(sheetHead('Add an item') + `<form id="if"><div class="row"><div style="flex:2 1 200px"><label>Name</label><input name="name" required></div><div><label>Code</label><input name="code"></div></div>
    <div class="row" style="margin-top:12px"><div><label>Type</label><select name="category"><option value="raw">Raw material</option><option value="packaging">Packaging</option></select></div><div><label>Base unit</label><select name="unit"><option value="kg">kg</option><option value="l">litre</option><option value="pc">pieces</option></select></div>
    <div><label>Packaging role</label><select name="packaging_role"><option value="">None</option><option value="pouch">Pouch (one per packet)</option><option value="carton">Carton</option></select></div></div>
    <div class="row" style="margin-top:12px"><div><label>Opening quantity</label><input name="opening_qty" value="0"></div><div><label>Opening rate (₹ per base unit)</label><input name="opening_rate" value="0"></div><div><label>Reorder at</label><input name="reorder_level" value="0"></div></div>
    <div class="actions"><button class="btn">Add item</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`);
    $('#if', m).onsubmit = async e => { e.preventDefault(); await act($('button', e.target), () => post('/api/items', Object.fromEntries(new FormData(e.target))), 'Item added'); m.close(); refresh(); }; };
};
M.customers = async el => {
  const { customers, products, prices } = S.meta;
  el.innerHTML = `<div class="actions" style="margin:0 0 12px"><button class="btn sm" id="addc">Add a customer</button></div>` + table([{ h: 'Customer', f: c => `<b>${esc(c.name)}</b><div class="muted small">${esc(c.type)} · ${esc(c.phone || '')}</div>` },
    { h: 'GSTIN', f: c => esc(c.gstin || 'Unregistered') }, { h: 'State', f: c => `${esc(c.state)} (${c.state_code})` }, { h: 'Special prices', f: c => prices.filter(p => p.customer_id === c.id).map(p => `${esc(products.find(x => x.id === p.product_id)?.code)} ${inr(p.price_paise)}`).join(', ') || '–' }], customers, { click: true });
  const form = c => { const f = c || { name: '', type: 'retailer', gstin: '', state: 'Tamil Nadu', address: '', phone: '' };
    const m = modal(sheetHead(c ? `Edit ${esc(c.name)}` : 'Add a customer') + `<form id="cf2"><div class="row"><div style="flex:2 1 220px"><label>Name</label><input name="name" value="${esc(f.name)}" required></div><div><label>Type</label><select name="type"><option ${f.type === 'retailer' ? 'selected' : ''}>retailer</option><option ${f.type === 'distributor' ? 'selected' : ''}>distributor</option></select></div></div>
      <div class="row" style="margin-top:12px"><div><label>State (decides CGST+SGST or IGST)</label><select name="state">${S.meta.states.map(s => `<option ${s.name === f.state ? 'selected' : ''}>${s.name}</option>`).join('')}</select></div><div><label>GSTIN</label><input name="gstin" value="${esc(f.gstin || '')}" maxlength="15"></div><div><label>Phone</label><input name="phone" value="${esc(f.phone || '')}"></div></div>
      <div class="field" style="margin-top:12px"><label>Address</label><input name="address" value="${esc(f.address || '')}"></div>
      ${c ? `<h3>Customer-specific prices (₹, leave empty for list price)</h3><div class="row">${products.map(p => `<div><label>${esc(p.name)}</label><input data-pp="${p.id}" value="${((prices.find(x => x.customer_id === c.id && x.product_id === p.id) || {}).price_paise / 100) || ''}" placeholder="${(p.price_paise / 100).toFixed(2)}"></div>`).join('')}</div>` : ''}
      <div class="actions"><button class="btn">Save</button><button type="button" class="btn alt" data-close>Cancel</button></div></form>`);
    $('#cf2', m).onsubmit = async e => { e.preventDefault(); const b = Object.fromEntries(new FormData(e.target));
      await act($('button', e.target), async () => { const r = c ? await put('/api/customers/' + c.id, b) : await post('/api/customers', b);
        if (c) await put(`/api/customers/${c.id}/prices`, { prices: $$('[data-pp]', m).map(i => ({ product_id: +i.dataset.pp, price: i.value })) }); return r; }, 'Saved'); m.close(); refresh(); }; };
  $$('tbody tr.click', el).forEach(tr => tr.onclick = () => form(customers[+tr.dataset.i])); $('#addc').onclick = () => form();
};
M.suppliers = async el => {
  const { suppliers } = S.meta;
  el.innerHTML = `<form id="sf" class="row" style="margin-bottom:14px"><div><label>Name</label><input name="name" required></div><div><label>GSTIN</label><input name="gstin"></div><div><label>Phone</label><input name="phone"></div><div class="fix"><button class="btn">Add supplier</button></div></form>` +
    table([{ h: 'Supplier', f: s => `<b>${esc(s.name)}</b>` }, { h: 'GSTIN', f: s => esc(s.gstin || '–') }, { h: 'Phone', f: s => esc(s.phone || '–') }], suppliers);
  $('#sf').onsubmit = async e => { e.preventDefault(); await act($('button', e.target), () => post('/api/suppliers', Object.fromEntries(new FormData(e.target))), 'Supplier added'); refresh(); };
};
M.settings = async el => {
  const s = S.meta.settings;
  el.innerHTML = `<form id="st"><div class="row"><div style="flex:2 1 240px"><label>Company name (printed on invoices)</label><input name="company_name" value="${esc(s.company_name)}"></div><div><label>GSTIN</label><input name="company_gstin" value="${esc(s.company_gstin)}"></div></div>
    <div class="field" style="margin-top:12px"><label>Address</label><input name="company_address" value="${esc(s.company_address)}"></div><div class="row"><div><label>Phone</label><input name="company_phone" value="${esc(s.company_phone)}"></div>
    <div><label>Variance tolerance (%)</label><input name="tol" value="${s.tolerance_bp / 100}"></div></div><p class="muted small">Factory state is ${esc(s.company_state)} (${esc(s.company_state_code)}); sales to other states are invoiced with IGST.</p><div class="actions"><button class="btn">Save settings</button></div></form>`;
  $('#st').onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.tolerance_bp = Math.round(parseFloat(f.tol) * 100); delete f.tol;
    await act($('button', e.target), () => put('/api/settings', f), 'Settings saved'); refresh(); };
};

/* ---------- reports ---------- */
const REPORT_LIST = [['sales_register', 'Sales register'], ['customer_sales', 'Customer-wise sales'], ['product_sales', 'Product-wise sales'], ['gst_summary', 'GST summary (GSTR-3B shape)'], ['gstr1', 'GSTR-1 invoice detail'],
  ['purchase_register', 'Purchase register'], ['stock_summary', 'Stock summary'], ['stock_ledger', 'Stock ledger'], ['production', 'Production: yield and wastage'], ['consumption', 'Planned vs actual consumption'],
  ['cost', 'Cost per packet'], ['payments', 'Payments and collections'], ['outstanding', 'Outstanding with ageing']];
const PROD_REPORTS = ['stock_summary', 'stock_ledger', 'production', 'consumption', 'purchase_register'];

function fmtCell(c, v) {
  if (v == null || v === '') return '';
  return { money: inr, moneyf: inr, qty, pct }[c.type] ? { money: inr, moneyf: inr, qty, pct }[c.type](v) : esc(v);
}
function reportTable(r, opts = {}) {
  const cols = r.columns, num = c => ['money', 'moneyf', 'qty', 'pct', 'int'].includes(c.type);
  const flagged = (c, v) => (c.label.startsWith('Flag') && v === 'FLAGGED') || (c.key === 'status' && v === 'Low stock');
  return table(cols.map(c => ({ h: c.label, cls: num(c) ? 'num' : '', f: row => flagged(c, row[c.key]) ? chip(row[c.key], 'flag') : (c.key === 'status' && row[c.key] === 'OK' ? chip('OK') : fmtCell(c, row[c.key])) })), r.rows,
    { click: opts.click, empty: 'No records in this period.', foot: Object.keys(r.totals).length ? `<tfoot><tr>${cols.map((c, i) => i === 0 ? '<td>Total</td>' : `<td class="${num(c) ? 'num' : ''}">${c.key in r.totals ? fmtCell(c, r.totals[c.key]) : ''}</td>`).join('')}</tr></tfoot>` : '' }) + (r.note ? `<p class="muted small">${esc(r.note)}</p>` : '');
}

V.reports = async (arg, el) => {
  const list = S.user.role === 'production' ? REPORT_LIST.filter(r => PROD_REPORTS.includes(r[0])) : REPORT_LIST;
  let name = list.find(r => r[0] === arg) ? arg : list[0][0], from = monthStart(), to = today(), item = '', kind = 'all';
  const { items, products } = S.meta;
  el.innerHTML = head('Reports', 'Filter by date, read here, or export for the auditor.') + `<div class="panel"><div class="row"><div style="flex:2 1 240px"><label>Report</label><select id="rn">${list.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></div>
    <div id="ff"><label>From</label><input type="date" id="rf" value="${from}"></div><div><label>To</label><input type="date" id="rt" value="${to}"></div><div id="rx"></div>
    <div class="fix" id="ex"></div></div></div><div class="panel" id="rb"><div class="empty">Loading…</div></div>`;
  $('#rn').value = name;
  const qs = fmt => `from=${from}&to=${to}${item ? '&item=' + item : ''}${name === 'stock_summary' ? '&kind=' + kind : ''}${fmt ? '&format=' + fmt : ''}`;
  const draw = async () => {
    $('#ff').style.display = name === 'outstanding' ? 'none' : ''; $('#rx').innerHTML = '';
    if (name === 'stock_ledger') { $('#rx').innerHTML = `<label>Item</label><select id="ri"><option value="">Choose…</option>${items.map(i => `<option value="i${i.id}">${esc(i.name)}</option>`).join('')}${products.map(p => `<option value="p${p.id}">${esc(p.name)} (finished)</option>`).join('')}</select>`; $('#ri').value = item; $('#ri').onchange = e => { item = e.target.value; draw(); }; }
    if (name === 'stock_summary') { $('#rx').innerHTML = `<label>Category</label><select id="rk"><option value="all">All</option><option value="raw">Raw material</option><option value="packaging">Packaging</option><option value="fg">Finished goods</option></select>`; $('#rk').value = kind; $('#rk').onchange = e => { kind = e.target.value; draw(); }; }
    $('#ex').innerHTML = ['csv', 'xlsx', 'pdf'].map(f => `<a class="btn alt sm" href="/api/reports/${name}?${qs(f)}">${f === 'xlsx' ? 'Excel' : f.toUpperCase()}</a>`).join(' ');
    const r = await api(`/api/reports/${name}?${qs()}`);
    $('#rb').innerHTML = `<h2>${esc(r.title)}</h2><div class="muted small" style="margin-bottom:10px">${r.from ? r.from + ' to ' : 'As of '}${r.to || ''}</div>` + reportTable(r);
  };
  $('#rn').onchange = e => { name = e.target.value; location.hash = '#/reports/' + name; };
  $('#rf').onchange = e => { from = e.target.value; draw(); }; $('#rt').onchange = e => { to = e.target.value; draw(); };
  await draw();
};

/* ---------- audit ---------- */
V.audit = async (_, el) => {
  const rows = await api('/api/audit');
  el.innerHTML = head('Audit trail', 'Who changed what and when. Posted invoices are never deleted; they are cancelled with a credit note.') + `<div class="panel"><div class="field" style="max-width:320px"><input id="q" placeholder="Filter by user, document or action"></div><div id="al"></div></div>`;
  const draw = () => { const q = $('#q').value.toLowerCase(), f = rows.filter(r => !q || JSON.stringify(r).toLowerCase().includes(q));
    $('#al').innerHTML = table([{ h: 'When', f: r => `<span class="nowrap">${r.ts}</span>` }, { h: 'User', f: r => esc(r.username || '–') }, { h: 'Record', f: r => `${esc(r.entity)} #${r.entity_id ?? ''}` }, { h: 'Action', f: r => chip(r.action, r.action === 'cancel' ? 'flag' : '') },
      { h: 'Detail', f: r => `<span class="muted small">${esc((r.detail || '').slice(0, 140))}</span>` }], f); };
  $('#q').oninput = draw; draw();
};
