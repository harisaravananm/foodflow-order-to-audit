"""Business rules.  Every function takes an open sqlite connection and a `user`
dict ({id, username, role}); callers commit.  Stock only ever changes by posting
rows to `stock_ledger`; balances are always SUM(ledger)."""
import json
import math
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from core import (D, rnd, to_milli, norm_unit, pct_bp, beyond_tolerance, compute_tax, fy_of,
                  rupees_to_paise)
from db import next_number


class Err(Exception):
    def __init__(self, msg, code=400, **data):
        super().__init__(msg)
        self.msg, self.code, self.data = msg, code, data


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().isoformat()


def setting(db, key, default=None):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def audit(db, user, entity, entity_id, action, detail=None):
    db.execute("INSERT INTO audit_log(ts,user_id,username,entity,entity_id,action,detail) VALUES(?,?,?,?,?,?,?)",
               (now(), user and user["id"], user and user["username"], entity, entity_id, action,
                json.dumps(detail, default=str) if detail is not None else None))


# --------------------------------------------------------------------------- ledger
def balance(db, item_id=None, product_id=None):
    col, key = ("item_id", item_id) if item_id else ("product_id", product_id)
    r = db.execute(f"SELECT COALESCE(SUM(qty_milli),0) q, COALESCE(SUM(value_paise),0) v FROM stock_ledger WHERE {col}=?",
                   (key,)).fetchone()
    return r["q"], r["v"]


def issue_value(db, qty_milli, item_id=None, product_id=None):
    """Weighted-average value of `qty_milli` leaving stock (all remaining stock -> exact remaining value)."""
    q, v = balance(db, item_id, product_id)
    if q <= 0 or v <= 0:
        return 0
    if qty_milli >= q:
        return v
    return rnd(Decimal(v) * qty_milli / q)


def post(db, user, *, qty_milli, value_paise, doc_type, doc_id, doc_ref, item_id=None, product_id=None, note="", ts=None):
    cur = db.execute(
        "INSERT INTO stock_ledger(ts,item_id,product_id,qty_milli,value_paise,doc_type,doc_id,doc_ref,note,user_id)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (ts or now(), item_id, product_id, qty_milli, value_paise, doc_type, doc_id, doc_ref, note, user and user["id"]))
    return cur.lastrowid


def post_issue(db, user, qty_milli, doc_type, doc_id, doc_ref, item_id=None, product_id=None, note=""):
    """Stock out at weighted-average cost; refuses to go negative."""
    q, _ = balance(db, item_id, product_id)
    if qty_milli > q:
        name = (db.execute("SELECT name FROM items WHERE id=?", (item_id,)) if item_id
                else db.execute("SELECT name FROM products WHERE id=?", (product_id,))).fetchone()["name"]
        raise Err(f"Insufficient stock of {name}: need {qty_milli/1000:g}, have {q/1000:g}", 409)
    val = issue_value(db, qty_milli, item_id, product_id)
    post(db, user, qty_milli=-qty_milli, value_paise=-val, doc_type=doc_type, doc_id=doc_id, doc_ref=doc_ref,
         item_id=item_id, product_id=product_id, note=note)
    return val


# --------------------------------------------------------------------------- purchases
def create_purchase(db, user, data):
    sup = db.execute("SELECT * FROM suppliers WHERE id=?", (data.get("supplier_id"),)).fetchone()
    if not sup:
        raise Err("Choose a supplier")
    lines = data.get("lines") or []
    if not lines:
        raise Err("Add at least one item")
    bill_date = data.get("bill_date") or today()
    no = f"PUR/{fy_of(bill_date)}/{next_number(db, 'pur-' + fy_of(bill_date)):04d}"
    cur = db.execute("INSERT INTO purchases(purchase_no,supplier_id,bill_no,bill_date,total_paise,tax_paise,created_by,created_at)"
                     " VALUES(?,?,?,?,0,0,?,?)", (no, sup["id"], data.get("bill_no"), bill_date, user["id"], now()))
    pid, total, tax_total = cur.lastrowid, 0, 0
    for ln in lines:
        item = db.execute("SELECT * FROM items WHERE id=?", (ln.get("item_id"),)).fetchone()
        if not item:
            raise Err("Unknown item in purchase")
        qty, rate = D(ln["qty"]), D(ln["rate"])
        if qty <= 0 or rate < 0:
            raise Err("Quantity must be positive and rate cannot be negative")
        try:
            qm = to_milli(qty, ln.get("unit") or item["unit"], item["unit"])
        except ValueError as e:
            raise Err(str(e))
        amount = rnd(qty * rate * 100)
        gst = int(D(ln.get("gst_rate") or 0) * 100)
        tax = rnd(Decimal(amount) * gst / 10000)
        db.execute("INSERT INTO purchase_lines(purchase_id,item_id,qty_milli,entered_qty,entered_unit,rate,amount_paise,gst_rate_x100,tax_paise)"
                   " VALUES(?,?,?,?,?,?,?,?,?)",
                   (pid, item["id"], qm, str(qty), norm_unit(ln.get("unit") or item["unit"]), str(rate), amount, gst, tax))
        post(db, user, item_id=item["id"], qty_milli=qm, value_paise=amount, doc_type="purchase", doc_id=pid, doc_ref=no,
             note=f"{sup['name']} bill {data.get('bill_no') or '-'}")
        total += amount + tax
        tax_total += tax
    db.execute("UPDATE purchases SET total_paise=?, tax_paise=? WHERE id=?", (total, tax_total, pid))
    audit(db, user, "purchase", pid, "create", {"no": no, "total": total})
    return pid


# --------------------------------------------------------------------------- production
def tolerance_bp(db):
    return int(setting(db, "tolerance_bp", "300"))


def _line_view(db, ln, tol):
    return {"item_id": ln["item_id"], "name": ln["name"], "unit": ln["unit"], "category": ln["category"],
            "role": ln["packaging_role"], "planned_milli": ln["planned_milli"], "actual_milli": ln["actual_milli"],
            "value_paise": ln["value_paise"], "variance_bp": pct_bp(ln["actual_milli"], ln["planned_milli"]),
            "flagged": bool(ln["actual_milli"]) and beyond_tolerance(ln["actual_milli"], ln["planned_milli"], tol)}


def run_costs(db, run):
    """Materials + direct overheads, cost per good packet (Decimal paise) and margin."""
    mat = db.execute("SELECT COALESCE(SUM(value_paise),0) FROM run_lines WHERE run_id=?", (run["id"],)).fetchone()[0]
    oh = db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM overheads WHERE run_id=?", (run["id"],)).fetchone()[0]
    good = run["actual_output_milli"] or 0
    cpp = None
    if good:
        cpp = (Decimal(mat + oh) * 1000 / good)
    price = db.execute("SELECT price_paise FROM products WHERE id=?", (run["product_id"],)).fetchone()[0]
    margin = None if cpp is None else (Decimal(price) - cpp)
    return {"material_paise": mat, "overhead_paise": oh, "total_paise": mat + oh,
            "cost_per_packet_paise": None if cpp is None else float(round(cpp, 2)),
            "price_paise": price,
            "margin_paise": None if margin is None else float(round(margin, 2)),
            "margin_pct": None if margin is None or not price else float(round(margin * 100 / price, 2))}


def run_detail(db, run_id):
    run = db.execute("""SELECT r.*, p.name product_name, p.code product_code, p.packets_per_carton, p.price_paise
                        FROM production_runs r JOIN products p ON p.id=r.product_id WHERE r.id=?""", (run_id,)).fetchone()
    if not run:
        raise Err("Run not found", 404)
    tol = tolerance_bp(db)
    lines = db.execute("""SELECT l.*, i.name, i.unit, i.category, i.packaging_role FROM run_lines l
                          JOIN items i ON i.id=l.item_id WHERE l.run_id=? ORDER BY i.category, i.name""", (run_id,)).fetchall()
    d = dict(run)
    d["lines"] = [_line_view(db, ln, tol) for ln in lines]
    d["tolerance_bp"] = tol
    d["overheads"] = [dict(r) for r in db.execute("SELECT * FROM overheads WHERE run_id=? ORDER BY id", (run_id,))]
    if run["status"] == "completed":
        out = run["actual_output_milli"]
        d["yield_variance_bp"] = pct_bp(out, run["expected_output_milli"])
        d["yield_flagged"] = beyond_tolerance(out, run["expected_output_milli"], tol)
        d["yield_loss_milli"] = run["expected_output_milli"] - out
    d["costs"] = run_costs(db, run)
    return d


def start_run(db, user, data):
    prod = db.execute("SELECT * FROM products WHERE id=?", (data.get("product_id"),)).fetchone()
    if not prod:
        raise Err("Choose a product")
    bom = db.execute("""SELECT b.*, i.name, i.unit, i.category FROM bom_lines b JOIN items i ON i.id=b.item_id
                        WHERE b.product_id=?""", (prod["id"],)).fetchall()
    if not bom or not prod["bom_yield_milli"]:
        raise Err("This product has no BOM / expected yield yet. Define it under Masters.")
    batches = D(data.get("batches") or 1)
    if batches <= 0:
        raise Err("Batches must be positive")
    bm = rnd(batches * 1000)
    seq = next_number(db, "run")
    run_no = f"PR/{fy_of(today())}/{seq:04d}"
    bseq = next_number(db, f"batch-{prod['code']}-{today()}")
    batch_no = f"{prod['code']}-{today().replace('-', '')}-{bseq:02d}"
    expiry = (date.today() + timedelta(days=prod["shelf_life_days"])).isoformat()
    expected = rnd(Decimal(prod["bom_yield_milli"]) * bm / 1000)
    cur = db.execute("""INSERT INTO production_runs(run_no,product_id,batch_no,expiry_date,batches_milli,status,started_at,
                        expected_output_milli,note,created_by) VALUES(?,?,?,?,?,'in_progress',?,?,?,?)""",
                     (run_no, prod["id"], batch_no, expiry, bm, now(), expected, data.get("note"), user["id"]))
    rid = cur.lastrowid
    issues = {int(i["item_id"]): i for i in (data.get("issues") or [])}
    for b in bom:
        planned = rnd(Decimal(b["qty_milli"]) * bm / 1000)
        db.execute("INSERT INTO run_lines(run_id,item_id,planned_milli) VALUES(?,?,?)", (rid, b["item_id"], planned))
        if b["category"] != "raw":
            continue  # packaging is issued when the run is closed
        i = issues.get(b["item_id"])
        qm = planned if i is None else _qty(i, b["unit"])
        if qm:
            val = post_issue(db, user, qm, "production_issue", rid, run_no, item_id=b["item_id"], note=f"Batch {batch_no}")
            db.execute("UPDATE run_lines SET actual_milli=?, value_paise=? WHERE run_id=? AND item_id=?",
                       (qm, val, rid, b["item_id"]))
    audit(db, user, "production_run", rid, "start", {"run_no": run_no, "batch_no": batch_no})
    return rid


def _qty(i, base_unit):
    try:
        qm = to_milli(i["qty"], i.get("unit") or base_unit, base_unit)
    except ValueError as e:
        raise Err(str(e))
    if qm < 0:
        raise Err("Quantity cannot be negative")
    return qm


def complete_run(db, user, run_id, data):
    run = db.execute("SELECT * FROM production_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise Err("Run not found", 404)
    if run["status"] != "in_progress":
        raise Err("This run is already completed")
    prod = db.execute("SELECT * FROM products WHERE id=?", (run["product_id"],)).fetchone()
    try:
        good, damaged, scrap = int(D(data.get("good_output"))), int(D(data.get("damaged_pouches") or 0)), int(D(data.get("scrap_packets") or 0))
    except ValueError:
        raise Err("Enter whole numbers for output and wastage")
    if min(good, damaged, scrap) < 0:
        raise Err("Output and wastage cannot be negative")
    lines = db.execute("""SELECT l.*, i.unit, i.category, i.packaging_role, i.name FROM run_lines l
                          JOIN items i ON i.id=l.item_id WHERE l.run_id=?""", (run_id,)).fetchall()
    given = {int(p["item_id"]): p for p in (data.get("packaging") or [])}
    for ln in lines:
        if ln["category"] != "packaging":
            continue
        if ln["item_id"] in given:
            qm = _qty(given[ln["item_id"]], ln["unit"])
        elif ln["packaging_role"] == "pouch":
            qm = (good + damaged + scrap) * 1000
        elif ln["packaging_role"] == "carton":
            qm = math.ceil(good / prod["packets_per_carton"]) * 1000
        else:
            qm = ln["planned_milli"]
        if qm:
            val = post_issue(db, user, qm, "production_issue", run_id, run["run_no"], item_id=ln["item_id"],
                             note=f"Packaging for batch {run['batch_no']}")
            db.execute("UPDATE run_lines SET actual_milli=?, value_paise=? WHERE id=?", (qm, val, ln["id"]))
    db.execute("""UPDATE production_runs SET status='completed', completed_at=?, actual_output_milli=?, damaged_pouches=?,
                  scrap_packets=?, completed_by=? WHERE id=?""", (now(), good * 1000, damaged, scrap, user["id"], run_id))
    mat = db.execute("SELECT COALESCE(SUM(value_paise),0) FROM run_lines WHERE run_id=?", (run_id,)).fetchone()[0]
    oh = db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM overheads WHERE run_id=?", (run_id,)).fetchone()[0]
    post(db, user, product_id=prod["id"], qty_milli=good * 1000, value_paise=mat + oh, doc_type="production_output",
         doc_id=run_id, doc_ref=run["run_no"], note=f"Batch {run['batch_no']} (exp {run['expiry_date']})")
    audit(db, user, "production_run", run_id, "complete", {"good": good, "damaged_pouches": damaged, "scrap": scrap})
    return run_id


def add_overhead(db, user, data):
    kind, amount = (data.get("kind") or "other").strip(), rupees_to_paise(data.get("amount") or 0)
    if amount <= 0:
        raise Err("Amount must be positive")
    run = None
    if data.get("run_id"):
        run = db.execute("SELECT * FROM production_runs WHERE id=?", (data["run_id"],)).fetchone()
        if not run:
            raise Err("Run not found", 404)
    month = (data.get("month") or (run["started_at"][:7] if run else today()[:7]))
    cur = db.execute("INSERT INTO overheads(run_id,month,kind,amount_paise,note,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                     (run and run["id"], month, kind, amount, data.get("note"), user["id"], now()))
    if run and run["status"] == "completed":  # keep finished-goods valuation in step with cost
        post(db, user, product_id=run["product_id"], qty_milli=0, value_paise=amount, doc_type="overhead",
             doc_id=run["id"], doc_ref=run["run_no"], note=f"{kind} added to batch {run['batch_no']}")
    audit(db, user, "overhead", cur.lastrowid, "create", {"run": run and run["run_no"], "kind": kind, "amount": amount})
    return cur.lastrowid


def adjust_stock(db, user, data):
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise Err("A reason is required for stock adjustments")
    item_id, product_id = data.get("item_id"), data.get("product_id")
    if bool(item_id) == bool(product_id):
        raise Err("Choose an item or a product")
    if item_id:
        it = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        qm = to_milli(data["qty"], data.get("unit") or it["unit"], it["unit"])
    else:
        qm = to_milli(data["qty"], "pkt", "pkt")
    if qm == 0:
        raise Err("Quantity cannot be zero")
    if qm < 0:
        val = post_issue(db, user, -qm, "adjustment", 0, "ADJ", item_id=item_id, product_id=product_id, note=reason)
    else:
        q, v = balance(db, item_id, product_id)
        val = rnd(Decimal(v) * qm / q) if q > 0 and v > 0 else 0
        post(db, user, item_id=item_id, product_id=product_id, qty_milli=qm, value_paise=val, doc_type="adjustment",
             doc_id=0, doc_ref="ADJ", note=reason)
    audit(db, user, "stock", item_id or product_id, "adjust", {"qty_milli": qm, "reason": reason})


# --------------------------------------------------------------------------- orders
def _price_for(db, customer_id, product):
    r = db.execute("SELECT price_paise FROM customer_prices WHERE customer_id=? AND product_id=?",
                   (customer_id, product["id"])).fetchone()
    return r["price_paise"] if r else product["price_paise"]


def _pkts(db, ln, product):
    qty = D(ln.get("qty") or 0)
    if qty != qty.to_integral_value() or qty <= 0:
        raise Err(f"Quantity for {product['name']} must be a whole number above zero")
    unit = (ln.get("unit") or "pkt").lower()
    return int(qty) * (product["packets_per_carton"] if unit == "carton" else 1)


def create_order(db, user, data):
    cust = db.execute("SELECT * FROM customers WHERE id=? AND active=1", (data.get("customer_id"),)).fetchone()
    if not cust:
        raise Err("Choose a customer")
    cu = data.get("client_uuid")
    if cu:
        ex = db.execute("SELECT id FROM orders WHERE client_uuid=?", (cu,)).fetchone()
        if ex:  # offline queue retry -> idempotent
            return ex["id"], True
    lines = data.get("lines") or []
    if not lines:
        raise Err("Add at least one product")
    no = f"SO/{fy_of(today())}/{next_number(db, 'so-' + fy_of(today())):04d}"
    cur = db.execute("INSERT INTO orders(order_no,client_uuid,customer_id,salesman_id,status,note,created_at,updated_at)"
                     " VALUES(?,?,?,?,'submitted',?,?,?)",
                     (no, cu, cust["id"], user["id"], data.get("note"), data.get("created_at") or now(), now()))
    oid = cur.lastrowid
    merged = {}
    for ln in lines:
        p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (ln.get("product_id"),)).fetchone()
        if not p:
            raise Err("Unknown product in order")
        merged[p["id"]] = merged.get(p["id"], 0) + _pkts(db, ln, p)
    for pid, q in merged.items():
        p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        db.execute("INSERT INTO order_lines(order_id,product_id,qty_pkts,price_paise) VALUES(?,?,?,?)",
                   (oid, pid, q, _price_for(db, cust["id"], p)))
    audit(db, user, "order", oid, "create", {"order_no": no, "customer": cust["name"]})
    return oid, False


def order_detail(db, oid, user=None):
    o = db.execute("""SELECT o.*, c.name customer_name, c.state customer_state, c.gstin customer_gstin, c.type customer_type,
                      u.name salesman_name FROM orders o JOIN customers c ON c.id=o.customer_id JOIN users u ON u.id=o.salesman_id
                      WHERE o.id=?""", (oid,)).fetchone()
    if not o or (user and user["role"] == "salesman" and o["salesman_id"] != user["id"]):
        raise Err("Order not found", 404)
    d = dict(o)
    d["lines"], d["total_paise"], d["short"] = [], 0, False
    for ln in db.execute("""SELECT l.*, p.name, p.code, p.packets_per_carton FROM order_lines l JOIN products p ON p.id=l.product_id
                            WHERE l.order_id=? ORDER BY l.id""", (oid,)):
        stock = balance(db, product_id=ln["product_id"])[0] // 1000
        short = max(0, ln["qty_pkts"] - stock) if o["status"] in ("submitted", "approved") else 0
        d["lines"].append({**dict(ln), "stock_pkts": stock, "short_pkts": short, "amount_paise": ln["qty_pkts"] * ln["price_paise"]})
        d["total_paise"] += ln["qty_pkts"] * ln["price_paise"]
        d["short"] = d["short"] or short > 0
    inv = db.execute("SELECT id, invoice_no, status, dispatched_at FROM invoices WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
    d["invoice"] = dict(inv) if inv else None
    return d


def edit_order(db, user, oid, data):
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise Err("Order not found", 404)
    if o["status"] not in ("submitted", "approved"):
        raise Err("Only submitted or approved orders can be edited")
    before = [dict(r) for r in db.execute("SELECT product_id, qty_pkts, price_paise FROM order_lines WHERE order_id=?", (oid,))]
    db.execute("DELETE FROM order_lines WHERE order_id=?", (oid,))
    for ln in data.get("lines") or []:
        p = db.execute("SELECT * FROM products WHERE id=?", (ln["product_id"],)).fetchone()
        q = int(D(ln["qty"]))
        if q <= 0:
            continue
        db.execute("INSERT INTO order_lines(order_id,product_id,qty_pkts,price_paise) VALUES(?,?,?,?)",
                   (oid, p["id"], q, rupees_to_paise(ln["price"]) if ln.get("price") not in (None, "") else p["price_paise"]))
    if not db.execute("SELECT 1 FROM order_lines WHERE order_id=?", (oid,)).fetchone():
        raise Err("An order needs at least one line")
    after = [dict(r) for r in db.execute("SELECT product_id, qty_pkts, price_paise FROM order_lines WHERE order_id=?", (oid,))]
    db.execute("UPDATE orders SET updated_at=? WHERE id=?", (now(), oid))
    audit(db, user, "order", oid, "edit", {"before": before, "after": after})


def set_order_status(db, user, oid, action, reason=None):
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise Err("Order not found", 404)
    if action == "approve" and o["status"] == "submitted":
        new = "approved"
    elif action == "reject" and o["status"] in ("submitted", "approved"):
        if not (reason or "").strip():
            raise Err("Give a reason for rejecting")
        new = "rejected"
    else:
        raise Err(f"Cannot {action} an order that is {o['status']}")
    db.execute("UPDATE orders SET status=?, reject_reason=?, updated_at=? WHERE id=?", (new, reason, now(), oid))
    audit(db, user, "order", oid, action, {"reason": reason})


# --------------------------------------------------------------------------- invoices
def create_invoice(db, user, oid, data=None):
    data = data or {}
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise Err("Order not found", 404)
    if o["status"] != "approved":
        raise Err("Approve the order before invoicing")
    cust = db.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
    lines = [dict(r) for r in db.execute("""SELECT l.*, p.name, p.hsn, p.gst_rate_x100 FROM order_lines l
                                            JOIN products p ON p.id=l.product_id WHERE l.order_id=?""", (oid,))]
    shortages = []
    for ln in lines:
        stock = balance(db, product_id=ln["product_id"])[0] // 1000
        if ln["qty_pkts"] > stock:
            shortages.append({"product": ln["name"], "ordered": ln["qty_pkts"], "available": stock, "short": ln["qty_pkts"] - stock})
    if shortages and data.get("fit_to_stock"):
        for ln in lines:
            avail = balance(db, product_id=ln["product_id"])[0] // 1000
            if ln["qty_pkts"] > avail:
                ln["qty_pkts"] = avail
                db.execute("UPDATE order_lines SET qty_pkts=? WHERE id=?", (avail, ln["id"]))
        lines = [ln for ln in lines if ln["qty_pkts"] > 0]
        audit(db, user, "order", oid, "fit_to_stock", {"shortages": shortages})
        shortages = []
    if shortages:
        raise Err("Finished-goods stock does not cover this order", 409, shortages=shortages)
    if not lines:
        raise Err("Nothing in stock to invoice", 409)
    intra = cust["state_code"] == setting(db, "company_state_code", "33")
    d = today()
    fy = fy_of(d)
    seq = next_number(db, f"inv-{fy}")
    inv_no = f"INV/{fy}/{seq:04d}"
    cur = db.execute("""INSERT INTO invoices(invoice_no,fy,order_id,customer_id,date,supply_type,place_of_supply,place_code,cust_name,
                        cust_gstin,cust_address,taxable_paise,cgst_paise,sgst_paise,igst_paise,total_paise,share_token,created_by,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,0,0,0,0,0,?,?,?)""",
                     (inv_no, fy, oid, cust["id"], d, "intra" if intra else "inter", cust["state"], cust["state_code"],
                      cust["name"], cust["gstin"], cust["address"], secrets.token_urlsafe(16), user["id"], now()))
    iid, tot = cur.lastrowid, dict(taxable=0, cgst=0, sgst=0, igst=0)
    for ln in lines:
        taxable = ln["qty_pkts"] * ln["price_paise"]
        t = compute_tax(taxable, ln["gst_rate_x100"], intra)
        db.execute("""INSERT INTO invoice_lines(invoice_id,product_id,description,hsn,qty_pkts,rate_paise,taxable_paise,gst_rate_x100,
                      cgst_paise,sgst_paise,igst_paise) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                   (iid, ln["product_id"], ln["name"], ln["hsn"], ln["qty_pkts"], ln["price_paise"], taxable, ln["gst_rate_x100"],
                    t["cgst"], t["sgst"], t["igst"]))
        tot["taxable"] += taxable
        for k in ("cgst", "sgst", "igst"):
            tot[k] += t[k]
    total = sum(tot.values())
    db.execute("UPDATE invoices SET taxable_paise=?,cgst_paise=?,sgst_paise=?,igst_paise=?,total_paise=? WHERE id=?",
               (tot["taxable"], tot["cgst"], tot["sgst"], tot["igst"], total, iid))
    db.execute("UPDATE orders SET status='invoiced', updated_at=? WHERE id=?", (now(), oid))
    audit(db, user, "invoice", iid, "create", {"invoice_no": inv_no, "total": total, "supply": "intra" if intra else "inter"})
    return iid


def dispatch_invoice(db, user, iid):
    inv = db.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        raise Err("Invoice not found", 404)
    if inv["status"] != "posted":
        raise Err("A cancelled invoice cannot be dispatched")
    if inv["dispatched_at"]:
        raise Err("Already dispatched")
    for ln in db.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (iid,)).fetchall():
        val = post_issue(db, user, ln["qty_pkts"] * 1000, "sales_dispatch", iid, inv["invoice_no"], product_id=ln["product_id"],
                         note=inv["cust_name"])
        db.execute("UPDATE invoice_lines SET cogs_paise=? WHERE id=?", (val, ln["id"]))
    db.execute("UPDATE invoices SET dispatched_at=?, dispatched_by=? WHERE id=?", (now(), user["id"], iid))
    if inv["order_id"]:
        db.execute("UPDATE orders SET status='dispatched', updated_at=? WHERE id=?", (now(), inv["order_id"]))
    audit(db, user, "invoice", iid, "dispatch", {"invoice_no": inv["invoice_no"]})


def cancel_invoice(db, user, iid, reason):
    inv = db.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        raise Err("Invoice not found", 404)
    if inv["status"] != "posted":
        raise Err("Invoice is already cancelled")
    if not (reason or "").strip():
        raise Err("Give a reason for the credit note")
    fy = inv["fy"]
    cn = f"CN/{fy}/{next_number(db, 'cn-' + fy):04d}"
    returned = 0
    if inv["dispatched_at"]:  # goods come back at the cost they left at
        for ln in db.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (iid,)).fetchall():
            post(db, user, product_id=ln["product_id"], qty_milli=ln["qty_pkts"] * 1000, value_paise=ln["cogs_paise"],
                 doc_type="credit_note", doc_id=iid, doc_ref=cn, note=f"Return of {inv['invoice_no']}")
        returned = 1
    db.execute("INSERT INTO credit_notes(cn_no,invoice_id,date,reason,amount_paise,stock_returned,created_by) VALUES(?,?,?,?,?,?,?)",
               (cn, iid, today(), reason, inv["total_paise"], returned, user["id"]))
    db.execute("UPDATE invoices SET status='cancelled' WHERE id=?", (iid,))
    if inv["order_id"]:
        db.execute("UPDATE orders SET status='cancelled', updated_at=? WHERE id=?", (now(), inv["order_id"]))
    audit(db, user, "invoice", iid, "cancel", {"credit_note": cn, "reason": reason})
    return cn


def record_payment(db, user, data):
    amount = rupees_to_paise(data.get("amount") or 0)
    if amount <= 0:
        raise Err("Amount must be positive")
    inv = None
    if data.get("invoice_id"):
        inv = db.execute("SELECT * FROM invoices WHERE id=?", (data["invoice_id"],)).fetchone()
        if not inv or inv["status"] != "posted":
            raise Err("Choose a posted invoice")
        due = invoice_outstanding(db, inv["id"])
        if amount > due:
            raise Err(f"Payment exceeds the invoice balance of {due/100:.2f}")
    cust_id = inv["customer_id"] if inv else data.get("customer_id")
    if not db.execute("SELECT 1 FROM customers WHERE id=?", (cust_id,)).fetchone():
        raise Err("Choose a customer")
    cur = db.execute("INSERT INTO payments(customer_id,invoice_id,amount_paise,paid_on,mode,ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (cust_id, inv and inv["id"], amount, data.get("paid_on") or today(), data.get("mode") or "cash",
                      data.get("ref"), user["id"], now()))
    audit(db, user, "payment", cur.lastrowid, "create", {"amount": amount, "invoice": inv and inv["invoice_no"]})
    return cur.lastrowid


def invoice_outstanding(db, iid):
    r = db.execute("""SELECT i.total_paise - COALESCE((SELECT SUM(amount_paise) FROM payments WHERE invoice_id=i.id),0)
                      FROM invoices i WHERE i.id=?""", (iid,)).fetchone()
    return r[0]


def customer_outstanding(db, customer_id):
    r = db.execute("""SELECT COALESCE((SELECT SUM(total_paise) FROM invoices WHERE customer_id=? AND status='posted'),0)
                      - COALESCE((SELECT SUM(amount_paise) FROM payments WHERE customer_id=?),0)""", (customer_id, customer_id)).fetchone()
    return r[0]
