"""FoodFlow: order-to-audit system for a small food manufacturer.

Run:  python app.py        (http://localhost:5000)
"""
import os
import secrets
import sqlite3
from functools import wraps

from flask import Flask, Response, abort, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash

import db as dbm
import reports as rep
import services as svc
from core import STATES, D, milli_to_str, rnd, rupees_to_paise, to_milli
from pdfgen import invoice_pdf
from services import Err

BASE = os.path.dirname(os.path.abspath(__file__))


def _secret():
    p = os.path.join(os.path.dirname(dbm.DB_PATH), "secret.key")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
    return open(p).read().strip()


app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="")
app.config.update(SECRET_KEY=os.environ.get("SECRET_KEY") or _secret(), SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14, JSON_SORT_KEYS=False)
app.teardown_appcontext(dbm.close_db)

READ_ALL = ("admin", "owner", "accountant")


# ------------------------------------------------------------------ plumbing
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    r = dbm.get_db().execute("SELECT id, username, name, role FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    return dict(r) if r else None


def need(*roles):
    """Admin may do everything; other roles only what is listed. need() = any signed-in user."""
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            u = current_user()
            if not u:
                return jsonify(error="Please sign in"), 401
            if roles and u["role"] != "admin" and u["role"] not in roles:
                return jsonify(error="Your role is not allowed to do this"), 403
            g.user = u
            return fn(*a, **kw)
        return wrap
    return deco


@app.errorhandler(Err)
def on_err(e):
    return jsonify(error=e.msg, **e.data), e.code


@app.errorhandler(sqlite3.IntegrityError)
def on_integrity(e):
    return jsonify(error=f"That conflicts with existing data ({e})"), 400


@app.errorhandler(ValueError)
def on_value(e):
    return jsonify(error=str(e)), 400


@app.after_request
def finish(resp):
    if "db" in g:
        (g.db.commit if resp.status_code < 400 else g.db.rollback)()
    resp.headers["Cache-Control"] = "no-store" if request.path.startswith("/api") else resp.headers.get("Cache-Control", "no-cache")
    return resp


def rows(cur):
    return [dict(r) for r in cur]


def body():
    return request.get_json(silent=True) or {}


# ------------------------------------------------------------------ pages / auth
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/login")
def login():
    d = body()
    u = dbm.get_db().execute("SELECT * FROM users WHERE username=? AND active=1", ((d.get("username") or "").strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], d.get("password") or ""):
        return jsonify(error="Wrong username or password"), 401
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    svc.audit(dbm.get_db(), dict(u), "user", u["id"], "login")
    return jsonify(user={"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = current_user()
    return jsonify(user=u) if u else (jsonify(error="Not signed in"), 401)


@app.get("/api/meta")
@need()
def meta():
    d, u = dbm.get_db(), g.user
    out = {"customers": rows(d.execute("SELECT * FROM customers WHERE active=1 ORDER BY name")),
           "products": rows(d.execute("SELECT * FROM products WHERE active=1 ORDER BY name")),
           "prices": rows(d.execute("SELECT * FROM customer_prices")),
           "states": [{"name": k, "code": v} for k, v in STATES.items()]}
    if u["role"] != "salesman":
        out["items"] = rows(d.execute("SELECT * FROM items WHERE active=1 ORDER BY category, name"))
        out["suppliers"] = rows(d.execute("SELECT * FROM suppliers ORDER BY name"))
        out["settings"] = {r["key"]: r["value"] for r in d.execute("SELECT * FROM settings")}
    return jsonify(out)


# ------------------------------------------------------------------ masters
def _gst_x100(v):
    return int(D(v if v not in (None, "") else 5) * 100)


@app.post("/api/products")
@need()
def product_create():
    if g.user["role"] not in ("admin",):
        raise Err("Only the admin can change masters", 403)
    d, db = body(), dbm.get_db()
    if not (d.get("name") and d.get("code") and d.get("hsn")):
        raise Err("Name, code and HSN are required")
    cur = db.execute("""INSERT INTO products(code,name,hsn,gst_rate_x100,pack_weight_g,packets_per_carton,price_paise,bom_yield_milli,shelf_life_days,
                        reorder_level_milli) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (d["code"].strip().upper(), d["name"].strip(), d["hsn"].strip(), _gst_x100(d.get("gst_rate")), d.get("pack_weight_g") or None,
                      int(d.get("packets_per_carton") or 20), rupees_to_paise(d.get("price") or 0), 0, int(d.get("shelf_life_days") or 90),
                      to_milli(d.get("reorder_level") or 0, "pkt", "pkt")))
    pid = cur.lastrowid
    src = d.get("copy_bom_from")
    if src:  # new pack size sharing a recipe: scale yield by weight ratio, packaging by yield ratio
        sp = db.execute("SELECT * FROM products WHERE id=?", (src,)).fetchone()
        if sp and sp["pack_weight_g"] and d.get("pack_weight_g"):
            ratio = D(sp["pack_weight_g"]) / D(d["pack_weight_g"])
            db.execute("UPDATE products SET bom_yield_milli=? WHERE id=?", (rnd(sp["bom_yield_milli"] * ratio), pid))
            for b in db.execute("SELECT b.*, i.category FROM bom_lines b JOIN items i ON i.id=b.item_id WHERE b.product_id=?", (sp["id"],)):
                q = b["qty_milli"] if b["category"] == "raw" else rnd(b["qty_milli"] * ratio)
                db.execute("INSERT INTO bom_lines(product_id,item_id,qty_milli) VALUES(?,?,?)", (pid, b["item_id"], q))
    svc.audit(db, g.user, "product", pid, "create", {"code": d["code"]})
    return jsonify(id=pid)


@app.put("/api/products/<int:pid>")
@need()
def product_update(pid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can change masters", 403)
    d, db = body(), dbm.get_db()
    old = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not old:
        raise Err("Product not found", 404)
    db.execute("""UPDATE products SET name=?, hsn=?, gst_rate_x100=?, pack_weight_g=?, packets_per_carton=?, price_paise=?, shelf_life_days=?,
                  reorder_level_milli=?, active=? WHERE id=?""",
               (d.get("name", old["name"]), d.get("hsn", old["hsn"]), _gst_x100(d["gst_rate"]) if "gst_rate" in d else old["gst_rate_x100"],
                d.get("pack_weight_g", old["pack_weight_g"]), int(d.get("packets_per_carton", old["packets_per_carton"])),
                rupees_to_paise(d["price"]) if "price" in d else old["price_paise"], int(d.get("shelf_life_days", old["shelf_life_days"])),
                to_milli(d["reorder_level"], "pkt", "pkt") if "reorder_level" in d else old["reorder_level_milli"],
                int(d.get("active", old["active"])), pid))
    svc.audit(db, g.user, "product", pid, "update", {"before": dict(old), "after": d})
    return jsonify(ok=True)


@app.get("/api/products/<int:pid>/bom")
@need("production", "accountant", "owner")
def bom_get(pid):
    db = dbm.get_db()
    p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        raise Err("Product not found", 404)
    lines = rows(db.execute("""SELECT b.item_id, b.qty_milli, i.name, i.unit, i.category FROM bom_lines b JOIN items i ON i.id=b.item_id
                               WHERE b.product_id=? ORDER BY i.category, i.name""", (pid,)))
    return jsonify(product=dict(p), lines=lines)


@app.put("/api/products/<int:pid>/bom")
@need("production")
def bom_put(pid):
    d, db = body(), dbm.get_db()
    p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        raise Err("Product not found", 404)
    yield_m = to_milli(d.get("yield_packets") or 0, "pkt", "pkt")
    if yield_m <= 0:
        raise Err("Expected yield (packets per batch) must be above zero")
    before = rows(db.execute("SELECT item_id, qty_milli FROM bom_lines WHERE product_id=?", (pid,)))
    db.execute("DELETE FROM bom_lines WHERE product_id=?", (pid,))
    for ln in d.get("lines") or []:
        it = db.execute("SELECT * FROM items WHERE id=?", (ln["item_id"],)).fetchone()
        q = to_milli(ln["qty"], ln.get("unit") or it["unit"], it["unit"])
        if q <= 0:
            raise Err(f"Quantity for {it['name']} must be above zero")
        db.execute("INSERT INTO bom_lines(product_id,item_id,qty_milli) VALUES(?,?,?)", (pid, it["id"], q))
    db.execute("UPDATE products SET bom_yield_milli=? WHERE id=?", (yield_m, pid))
    svc.audit(db, g.user, "bom", pid, "update", {"before": before, "yield_before": p["bom_yield_milli"], "yield_after": yield_m, "lines": d.get("lines")})
    return jsonify(ok=True)


@app.post("/api/items")
@need("production")
def item_create():
    d, db = body(), dbm.get_db()
    if not d.get("name") or d.get("unit") not in ("kg", "l", "pc") or d.get("category") not in ("raw", "packaging"):
        raise Err("Name, category (raw/packaging) and base unit (kg, l, pc) are required")
    code = (d.get("code") or d["name"][:6]).strip().upper()
    cur = db.execute("INSERT INTO items(code,name,category,unit,packaging_role,reorder_level_milli) VALUES(?,?,?,?,?,?)",
                     (code, d["name"].strip(), d["category"], d["unit"], d.get("packaging_role") or None,
                      to_milli(d.get("reorder_level") or 0, d["unit"], d["unit"])))
    iid = cur.lastrowid
    if D(d.get("opening_qty") or 0) > 0:
        val = rupees_to_paise(D(d["opening_qty"]) * D(d.get("opening_rate") or 0))
        svc.post(db, g.user, item_id=iid, qty_milli=to_milli(d["opening_qty"], d["unit"], d["unit"]), value_paise=val,
                 doc_type="opening", doc_id=iid, doc_ref="OPENING", note="Opening balance", ts="2000-01-01 00:00:00")
    svc.audit(db, g.user, "item", iid, "create", {"code": code})
    return jsonify(id=iid)


@app.put("/api/items/<int:iid>")
@need("production")
def item_update(iid):
    d, db = body(), dbm.get_db()
    old = db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if not old:
        raise Err("Item not found", 404)
    db.execute("UPDATE items SET name=?, reorder_level_milli=?, packaging_role=?, active=? WHERE id=?",
               (d.get("name", old["name"]), to_milli(d["reorder_level"], old["unit"], old["unit"]) if "reorder_level" in d else old["reorder_level_milli"],
                d.get("packaging_role", old["packaging_role"]) or None, int(d.get("active", old["active"])), iid))
    svc.audit(db, g.user, "item", iid, "update", d)
    return jsonify(ok=True)


@app.post("/api/customers")
@need()
def customer_create():
    if g.user["role"] != "admin":
        raise Err("Only the admin can change masters", 403)
    d, db = body(), dbm.get_db()
    if d.get("state") not in STATES or not d.get("name"):
        raise Err("Name and a valid state are required")
    gstin = (d.get("gstin") or "").strip().upper() or None
    if gstin and (len(gstin) != 15 or gstin[:2] != STATES[d["state"]]):
        raise Err("GSTIN must be 15 characters and start with the state code " + STATES[d["state"]])
    cur = db.execute("INSERT INTO customers(name,type,gstin,state,state_code,address,phone) VALUES(?,?,?,?,?,?,?)",
                     (d["name"].strip(), d.get("type") or "retailer", gstin, d["state"], STATES[d["state"]], d.get("address"), d.get("phone")))
    svc.audit(db, g.user, "customer", cur.lastrowid, "create", {"name": d["name"]})
    return jsonify(id=cur.lastrowid)


@app.put("/api/customers/<int:cid>")
@need()
def customer_update(cid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can change masters", 403)
    d, db = body(), dbm.get_db()
    old = db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not old:
        raise Err("Customer not found", 404)
    st = d.get("state", old["state"])
    db.execute("UPDATE customers SET name=?,type=?,gstin=?,state=?,state_code=?,address=?,phone=?,active=? WHERE id=?",
               (d.get("name", old["name"]), d.get("type", old["type"]), (d.get("gstin", old["gstin"]) or None), st, STATES.get(st, old["state_code"]),
                d.get("address", old["address"]), d.get("phone", old["phone"]), int(d.get("active", old["active"])), cid))
    svc.audit(db, g.user, "customer", cid, "update", d)
    return jsonify(ok=True)


@app.put("/api/customers/<int:cid>/prices")
@need()
def customer_prices(cid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can change masters", 403)
    db = dbm.get_db()
    for p in body().get("prices") or []:
        if p.get("price") in (None, ""):
            db.execute("DELETE FROM customer_prices WHERE customer_id=? AND product_id=?", (cid, p["product_id"]))
        else:
            db.execute("INSERT INTO customer_prices(customer_id,product_id,price_paise) VALUES(?,?,?) ON CONFLICT(customer_id,product_id) DO UPDATE SET price_paise=excluded.price_paise",
                       (cid, p["product_id"], rupees_to_paise(p["price"])))
    svc.audit(db, g.user, "customer", cid, "prices", body())
    return jsonify(ok=True)


@app.post("/api/suppliers")
@need("production")
def supplier_create():
    d, db = body(), dbm.get_db()
    if not d.get("name"):
        raise Err("Supplier name is required")
    cur = db.execute("INSERT INTO suppliers(name,gstin,phone) VALUES(?,?,?)", (d["name"].strip(), d.get("gstin"), d.get("phone")))
    return jsonify(id=cur.lastrowid)


@app.put("/api/settings")
@need()
def settings_put():
    if g.user["role"] != "admin":
        raise Err("Only the admin can change settings", 403)
    db = dbm.get_db()
    for k, v in body().items():
        if k in ("company_name", "company_gstin", "company_address", "company_phone", "tolerance_bp"):
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    svc.audit(db, g.user, "settings", 0, "update", body())
    return jsonify(ok=True)


# ------------------------------------------------------------------ purchases & production
@app.get("/api/purchases")
@need("production", "accountant", "owner")
def purchases_list():
    return jsonify(rows(dbm.get_db().execute(
        """SELECT p.*, s.name supplier FROM purchases p JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.id DESC LIMIT 100""")))


@app.post("/api/purchases")
@need("production")
def purchases_create():
    db = dbm.get_db()
    return jsonify(id=svc.create_purchase(db, g.user, body()))


@app.get("/api/runs")
@need("production", "accountant", "owner")
def runs_list():
    out = rows(dbm.get_db().execute("""SELECT r.id, r.run_no, r.batch_no, r.status, r.started_at, r.expected_output_milli, r.actual_output_milli,
                                       p.name product FROM production_runs r JOIN products p ON p.id=r.product_id ORDER BY r.id DESC LIMIT 100"""))
    return jsonify(out)


@app.post("/api/runs")
@need("production")
def runs_start():
    db = dbm.get_db()
    return jsonify(id=svc.start_run(db, g.user, body()))


@app.get("/api/runs/<int:rid>")
@need("production", "accountant", "owner")
def runs_get(rid):
    return jsonify(svc.run_detail(dbm.get_db(), rid))


@app.post("/api/runs/<int:rid>/complete")
@need("production")
def runs_complete(rid):
    db = dbm.get_db()
    svc.complete_run(db, g.user, rid, body())
    return jsonify(svc.run_detail(db, rid))


@app.post("/api/overheads")
@need("production")
def overhead_add():
    return jsonify(id=svc.add_overhead(dbm.get_db(), g.user, body()))


@app.post("/api/stock/adjust")
@need("production")
def stock_adjust():
    svc.adjust_stock(dbm.get_db(), g.user, body())
    return jsonify(ok=True)


# ------------------------------------------------------------------ orders
@app.get("/api/orders")
@need("salesman", "accountant", "owner")
def orders_list():
    db, u = dbm.get_db(), g.user
    sql = """SELECT o.id, o.order_no, o.status, o.created_at, c.name customer, c.state, u.name salesman,
             (SELECT SUM(qty_pkts*price_paise) FROM order_lines WHERE order_id=o.id) total_paise,
             (SELECT SUM(qty_pkts) FROM order_lines WHERE order_id=o.id) pkts
             FROM orders o JOIN customers c ON c.id=o.customer_id JOIN users u ON u.id=o.salesman_id WHERE 1=1"""
    args = []
    if u["role"] == "salesman":
        sql += " AND o.salesman_id=?"
        args.append(u["id"])
    if request.args.get("status"):
        sql += " AND o.status=?"
        args.append(request.args["status"])
    sql += " ORDER BY o.id DESC LIMIT 200"
    return jsonify(rows(db.execute(sql, args)))


@app.post("/api/orders")
@need("salesman")
def orders_create():
    db = dbm.get_db()
    oid, dup = svc.create_order(db, g.user, body())
    return jsonify(id=oid, duplicate=dup, order=svc.order_detail(db, oid))


@app.get("/api/orders/<int:oid>")
@need("salesman", "accountant", "owner")
def orders_get(oid):
    return jsonify(svc.order_detail(dbm.get_db(), oid, g.user))


@app.put("/api/orders/<int:oid>")
@need()
def orders_edit(oid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can edit orders", 403)
    db = dbm.get_db()
    svc.edit_order(db, g.user, oid, body())
    return jsonify(svc.order_detail(db, oid))


@app.post("/api/orders/<int:oid>/<action>")
@need()
def orders_action(oid, action):
    if g.user["role"] != "admin":
        raise Err("Only the admin can verify orders", 403)
    db = dbm.get_db()
    if action in ("approve", "reject"):
        svc.set_order_status(db, g.user, oid, action, body().get("reason"))
        return jsonify(svc.order_detail(db, oid))
    if action == "invoice":
        iid = svc.create_invoice(db, g.user, oid, body())
        return jsonify(id=iid, order=svc.order_detail(db, oid))
    abort(404)


@app.get("/api/my/summary")
@need("salesman")
def my_summary():
    db, u = dbm.get_db(), g.user
    out = rows(db.execute("""SELECT c.id, c.name, c.state, COUNT(DISTINCT o.id) orders,
        COALESCE(SUM(CASE WHEN o.status IN ('submitted','approved') THEN l.qty_pkts*l.price_paise END),0) pending_paise,
        COALESCE(SUM(CASE WHEN o.status IN ('invoiced','dispatched') THEN l.qty_pkts*l.price_paise END),0) invoiced_paise
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN order_lines l ON l.order_id=o.id
        WHERE o.salesman_id=? GROUP BY c.id ORDER BY invoiced_paise DESC, pending_paise DESC""", (u["id"],)))
    return jsonify(out)


# ------------------------------------------------------------------ invoices & payments
@app.get("/api/invoices")
@need("accountant", "owner")
def invoices_list():
    out = rows(dbm.get_db().execute("""SELECT i.id, i.invoice_no, i.date, i.status, i.cust_name, i.total_paise, i.supply_type, i.dispatched_at,
        i.total_paise - COALESCE((SELECT SUM(amount_paise) FROM payments WHERE invoice_id=i.id),0) balance_paise
        FROM invoices i ORDER BY i.id DESC LIMIT 200"""))
    return jsonify(out)


@app.get("/api/invoices/<int:iid>")
@need("accountant", "owner")
def invoices_get(iid):
    db = dbm.get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        raise Err("Invoice not found", 404)
    d = dict(inv)
    d["lines"] = rows(db.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (iid,)))
    d["payments"] = rows(db.execute("SELECT * FROM payments WHERE invoice_id=? ORDER BY id", (iid,)))
    d["balance_paise"] = svc.invoice_outstanding(db, iid)
    d["credit_note"] = (lambda r: dict(r) if r else None)(db.execute("SELECT * FROM credit_notes WHERE invoice_id=?", (iid,)).fetchone())
    d["customer_phone"] = (db.execute("SELECT phone FROM customers WHERE id=?", (inv["customer_id"],)).fetchone() or {"phone": None})["phone"]
    return jsonify(d)


@app.post("/api/invoices/<int:iid>/dispatch")
@need()
def invoices_dispatch(iid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can record dispatch", 403)
    svc.dispatch_invoice(dbm.get_db(), g.user, iid)
    return jsonify(ok=True)


@app.post("/api/invoices/<int:iid>/cancel")
@need()
def invoices_cancel(iid):
    if g.user["role"] != "admin":
        raise Err("Only the admin can cancel an invoice", 403)
    return jsonify(credit_note=svc.cancel_invoice(dbm.get_db(), g.user, iid, body().get("reason")))


@app.get("/api/invoices/<int:iid>/pdf")
@need("accountant", "owner")
def invoices_pdf(iid):
    db = dbm.get_db()
    inv = db.execute("SELECT invoice_no FROM invoices WHERE id=?", (iid,)).fetchone()
    if not inv:
        abort(404)
    return Response(invoice_pdf(db, iid), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"].replace("/", "-")}.pdf"'})


@app.get("/i/<token>")
def invoice_public(token):
    """Unguessable link used for the WhatsApp / e-mail share."""
    db = dbm.get_db()
    inv = db.execute("SELECT id, invoice_no FROM invoices WHERE share_token=?", (token,)).fetchone()
    if not inv:
        abort(404)
    return Response(invoice_pdf(db, inv["id"]), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"].replace("/", "-")}.pdf"'})


@app.post("/api/payments")
@need()
def payments_create():
    if g.user["role"] != "admin":
        raise Err("Only the admin can record payments", 403)
    return jsonify(id=svc.record_payment(dbm.get_db(), g.user, body()))


# ------------------------------------------------------------------ reports, dashboard, audit
@app.get("/api/dashboard")
@need("owner", "accountant", "production")
def dashboard():
    d = rep.dashboard(dbm.get_db())
    if g.user["role"] == "production":
        for k in ("sales_month", "collected_month", "outstanding_total", "cost_per_packet"):
            d.pop(k, None)
    return jsonify(d)


@app.get("/api/reports/<name>")
@need("accountant", "owner", "production")
def report(name):
    if name not in rep.REPORTS:
        abort(404)
    if g.user["role"] == "production" and name not in ("stock_summary", "stock_ledger", "production", "consumption", "purchase_register"):
        raise Err("Your role is not allowed to do this", 403)
    db = dbm.get_db()
    r = rep.REPORTS[name](db, request.args)
    fmt = request.args.get("format", "json")
    fname = f"{name}_{r.get('from') or ''}_{r.get('to') or ''}"
    if fmt == "csv":
        return Response(rep.to_csv(r), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    if fmt == "xlsx":
        return Response(rep.to_xlsx(r), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
    if fmt == "pdf":
        return Response(rep.to_pdf(r, svc.setting(db, "company_name", "")), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})
    return jsonify(r)


@app.get("/api/audit")
@need("owner", "accountant")
def audit_list():
    return jsonify(rows(dbm.get_db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300")))


@app.get("/api/receivables")
@need("accountant", "owner")
def receivables():
    return jsonify(rep.outstanding(dbm.get_db(), request.args))


@app.get("/api/overheads")
@need("production", "accountant", "owner")
def overheads_list():
    return jsonify(rows(dbm.get_db().execute(
        "SELECT o.*, r.run_no FROM overheads o LEFT JOIN production_runs r ON r.id=o.run_id ORDER BY o.id DESC LIMIT 100")))


@app.get("/api/ping")
def ping():
    return jsonify(ok=True)


def create_app_ready():
    conn = dbm.init_db()
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() and os.environ.get("FOODFLOW_SEED", "1") == "1":
        import seed
        seed.seed(conn)
    conn.close()


create_app_ready()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
