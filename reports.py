"""Auditor reports.  Each report returns {title, columns, rows, totals}.
Column types: text | money (paise) | moneyf (fractional paise) | qty (milli) | int | pct (basis points) | date."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from core import milli_to_str
from services import balance, tolerance_bp, setting, customer_outstanding, today


def col(key, label, typ="text", total=False):
    return {"key": key, "label": label, "type": typ, "total": total}


def month_range(args):
    t = date.today()
    f = args.get("from") or t.replace(day=1).isoformat()
    to = args.get("to") or t.isoformat()
    return f, to


def _result(title, columns, rows, f=None, to=None, note=None):
    totals = {c["key"]: sum((r.get(c["key"]) or 0) for r in rows) for c in columns if c["total"]}
    return {"title": title, "columns": columns, "rows": rows, "totals": totals, "from": f, "to": to, "note": note}


# ---------------------------------------------------------------- sales / GST
def sales_register(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT i.*, COALESCE((SELECT SUM(amount_paise) FROM payments WHERE invoice_id=i.id),0) paid
                           FROM invoices i WHERE i.date BETWEEN ? AND ? ORDER BY i.date, i.id""", (f, to)):
        rows.append({"date": r["date"], "doc": r["invoice_no"], "kind": "Invoice" + (" (cancelled)" if r["status"] == "cancelled" else ""),
                     "customer": r["cust_name"], "gstin": r["cust_gstin"], "pos": r["place_of_supply"],
                     "taxable": r["taxable_paise"], "cgst": r["cgst_paise"], "sgst": r["sgst_paise"], "igst": r["igst_paise"],
                     "total": r["total_paise"], "paid": r["paid"]})
    for r in db.execute("""SELECT c.*, i.cust_name, i.cust_gstin, i.place_of_supply, i.invoice_no, i.taxable_paise, i.cgst_paise,
                           i.sgst_paise, i.igst_paise FROM credit_notes c JOIN invoices i ON i.id=c.invoice_id
                           WHERE c.date BETWEEN ? AND ? ORDER BY c.date, c.id""", (f, to)):
        rows.append({"date": r["date"], "doc": r["cn_no"], "kind": f"Credit note vs {r['invoice_no']}", "customer": r["cust_name"],
                     "gstin": r["cust_gstin"], "pos": r["place_of_supply"], "taxable": -r["taxable_paise"], "cgst": -r["cgst_paise"],
                     "sgst": -r["sgst_paise"], "igst": -r["igst_paise"], "total": -r["amount_paise"], "paid": 0})
    rows.sort(key=lambda x: (x["date"], x["doc"]))
    cols = [col("date", "Date", "date"), col("doc", "Document"), col("kind", "Type"), col("customer", "Customer"), col("gstin", "GSTIN"),
            col("pos", "Place of supply"), col("taxable", "Taxable", "money", True), col("cgst", "CGST", "money", True),
            col("sgst", "SGST", "money", True), col("igst", "IGST", "money", True), col("total", "Invoice value", "money", True),
            col("paid", "Received", "money", True)]
    return _result("Sales register", cols, rows, f, to, "Credit notes are shown as negative rows, so totals are net of cancellations.")


def customer_sales(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT c.name, c.type, c.state, COUNT(i.id) n, COALESCE(SUM(i.taxable_paise),0) taxable,
                           COALESCE(SUM(i.total_paise),0) total FROM customers c
                           LEFT JOIN invoices i ON i.customer_id=c.id AND i.status='posted' AND i.date BETWEEN ? AND ?
                           GROUP BY c.id HAVING n>0 ORDER BY total DESC""", (f, to)):
        rows.append({"customer": r["name"], "type": r["type"], "state": r["state"], "n": r["n"], "taxable": r["taxable"], "total": r["total"]})
    cols = [col("customer", "Customer"), col("type", "Type"), col("state", "State"), col("n", "Invoices", "int", True),
            col("taxable", "Taxable sales", "money", True), col("total", "Invoice value", "money", True)]
    return _result("Customer-wise sales", cols, rows, f, to)


def product_sales(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT l.description, SUM(l.qty_pkts) q, SUM(l.taxable_paise) taxable, SUM(l.cogs_paise) cogs
                           FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id
                           WHERE i.status='posted' AND i.date BETWEEN ? AND ? GROUP BY l.product_id ORDER BY taxable DESC""", (f, to)):
        rows.append({"product": r["description"], "qty": r["q"], "taxable": r["taxable"], "cogs": r["cogs"], "gm": r["taxable"] - r["cogs"]})
    cols = [col("product", "Product"), col("qty", "Packets sold", "int", True), col("taxable", "Taxable sales", "money", True),
            col("cogs", "Cost of goods (dispatched)", "money", True), col("gm", "Gross margin", "money", True)]
    return _result("Product-wise sales", cols, rows, f, to)


def gst_summary(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT l.gst_rate_x100 rate, i.supply_type, SUM(l.taxable_paise) t, SUM(l.cgst_paise) c, SUM(l.sgst_paise) s,
                           SUM(l.igst_paise) g FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id
                           WHERE i.date BETWEEN ? AND ? GROUP BY l.gst_rate_x100, i.supply_type""", (f, to)):
        rows.append({"table": "3.1(a) Outward taxable supplies", "supply": "Intra-state" if r["supply_type"] == "intra" else "Inter-state",
                     "rate": r["rate"], "taxable": r["t"], "cgst": r["c"], "sgst": r["s"], "igst": r["g"]})
    for r in db.execute("""SELECT l.gst_rate_x100 rate, i.supply_type, SUM(l.taxable_paise) t, SUM(l.cgst_paise) c, SUM(l.sgst_paise) s,
                           SUM(l.igst_paise) g FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id JOIN invoice_lines l ON l.invoice_id=i.id
                           WHERE n.date BETWEEN ? AND ? GROUP BY l.gst_rate_x100, i.supply_type""", (f, to)):
        rows.append({"table": "GSTR-1 9B Credit notes (reduces liability)", "supply": "Intra-state" if r["supply_type"] == "intra" else "Inter-state",
                     "rate": r["rate"], "taxable": -r["t"], "cgst": -r["c"], "sgst": -r["s"], "igst": -r["g"]})
    for r in rows:
        r["tax"] = r["cgst"] + r["sgst"] + r["igst"]
    cols = [col("table", "Return table"), col("supply", "Supply"), col("rate", "GST rate", "pct"), col("taxable", "Taxable value", "money", True),
            col("cgst", "CGST", "money", True), col("sgst", "SGST", "money", True), col("igst", "IGST", "money", True),
            col("tax", "Total tax", "money", True)]
    return _result("GST summary (outward supplies, GSTR-3B 3.1 shape)", cols, rows, f, to)


def gstr1(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT i.*, l.gst_rate_x100 rate, SUM(l.taxable_paise) t, SUM(l.cgst_paise) c, SUM(l.sgst_paise) s, SUM(l.igst_paise) g
                           FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id WHERE i.date BETWEEN ? AND ? AND i.status='posted'
                           GROUP BY i.id, l.gst_rate_x100 ORDER BY i.date, i.id""", (f, to)):
        rows.append({"gstin": r["cust_gstin"] or "(unregistered)", "customer": r["cust_name"], "doc": r["invoice_no"], "date": r["date"],
                     "value": r["total_paise"], "pos": f"{r['place_code']}-{r['place_of_supply']}", "rate": r["rate"], "taxable": r["t"],
                     "igst": r["g"], "cgst": r["c"], "sgst": r["s"]})
    cols = [col("gstin", "GSTIN/UIN of recipient"), col("customer", "Receiver name"), col("doc", "Invoice no."), col("date", "Invoice date", "date"),
            col("value", "Invoice value", "money"), col("pos", "Place of supply"), col("rate", "Rate", "pct"),
            col("taxable", "Taxable value", "money", True), col("igst", "IGST", "money", True), col("cgst", "CGST", "money", True),
            col("sgst", "SGST", "money", True)]
    return _result("GSTR-1 B2B/B2C invoice detail", cols, rows, f, to)


def purchase_register(db, a):
    f, to = month_range(a)
    rows = []
    for r in db.execute("""SELECT p.bill_date, p.purchase_no, p.bill_no, s.name sup, s.gstin, i.name item, i.unit, l.*
                           FROM purchase_lines l JOIN purchases p ON p.id=l.purchase_id JOIN suppliers s ON s.id=p.supplier_id
                           JOIN items i ON i.id=l.item_id WHERE p.bill_date BETWEEN ? AND ? ORDER BY p.bill_date, p.id, l.id""", (f, to)):
        rows.append({"date": r["bill_date"], "doc": r["purchase_no"], "bill": r["bill_no"], "supplier": r["sup"], "gstin": r["gstin"],
                     "item": r["item"], "qty": r["qty_milli"], "unit": r["unit"], "rate": f"{r['rate']}/{r['entered_unit']}",
                     "taxable": r["amount_paise"], "gst": r["gst_rate_x100"], "tax": r["tax_paise"], "total": r["amount_paise"] + r["tax_paise"]})
    cols = [col("date", "Bill date", "date"), col("doc", "Purchase no."), col("bill", "Supplier bill"), col("supplier", "Supplier"), col("gstin", "GSTIN"),
            col("item", "Item"), col("qty", "Qty", "qty"), col("unit", "Unit"), col("rate", "Rate"), col("taxable", "Taxable", "money", True),
            col("gst", "GST %", "pct"), col("tax", "Input tax", "money", True), col("total", "Total", "money", True)]
    return _result("Purchase register", cols, rows, f, to)


# ---------------------------------------------------------------- stock
def stock_summary(db, a):
    f, to = month_range(a)
    kind = a.get("kind") or "all"
    rows = []
    q = """SELECT {id} id, {name} name, {unit} unit, {cat} cat, {rl} reorder,
             COALESCE(SUM(CASE WHEN date(l.ts)<:f THEN l.qty_milli END),0) opening,
             COALESCE(SUM(CASE WHEN date(l.ts) BETWEEN :f AND :t AND l.qty_milli>0 AND l.doc_type<>'adjustment' THEN l.qty_milli END),0) receipts,
             COALESCE(SUM(CASE WHEN date(l.ts) BETWEEN :f AND :t AND l.qty_milli<0 AND l.doc_type<>'adjustment' THEN l.qty_milli END),0) issues,
             COALESCE(SUM(CASE WHEN date(l.ts) BETWEEN :f AND :t AND l.doc_type='adjustment' THEN l.qty_milli END),0) adj,
             COALESCE(SUM(CASE WHEN date(l.ts)<=:t THEN l.qty_milli END),0) closing,
             COALESCE(SUM(CASE WHEN date(l.ts)<=:t THEN l.value_paise END),0) value
           FROM {tbl} x LEFT JOIN stock_ledger l ON l.{fk}=x.id GROUP BY x.id ORDER BY {order}"""
    if kind in ("all", "raw", "packaging"):
        sql = q.format(id="x.id", name="x.name", unit="x.unit", cat="CASE x.category WHEN 'raw' THEN 'Raw material' ELSE 'Packaging' END",
                       rl="x.reorder_level_milli", tbl="items", fk="item_id", order="x.category, x.name")
        for r in db.execute(sql, {"f": f, "t": to}):
            if kind != "all" and not r["cat"].lower().startswith(kind[:3]):
                continue
            rows.append(dict(r))
    if kind in ("all", "fg"):
        sql = q.format(id="x.id", name="x.name", unit="'pkt'", cat="'Finished goods'", rl="x.reorder_level_milli", tbl="products",
                       fk="product_id", order="x.name")
        rows += [dict(r) for r in db.execute(sql, {"f": f, "t": to})]
    for r in rows:
        r["rate"] = (r["value"] * 1000 / r["closing"]) if r["closing"] else 0
        r["status"] = "Low stock" if r["reorder"] and r["closing"] <= r["reorder"] else "OK"
    cols = [col("cat", "Category"), col("name", "Item"), col("unit", "Unit"), col("opening", "Opening", "qty"), col("receipts", "Receipts", "qty"),
            col("issues", "Issues / sales", "qty"), col("adj", "Adjustments", "qty"), col("closing", "Closing", "qty"),
            col("rate", "Avg rate", "moneyf"), col("value", "Stock value", "money", True), col("status", "Status")]
    return _result("Stock summary", cols, rows, f, to, "Opening + receipts + issues + adjustments = closing, valued at weighted-average cost.")


def stock_ledger(db, a):
    f, to = month_range(a)
    key = a.get("item") or ""
    if key[:1] not in ("i", "p") or not key[1:].isdigit():
        return _result("Stock ledger", [col("msg", "Choose an item")], [], f, to)
    fk, kid = ("item_id", int(key[1:])) if key[0] == "i" else ("product_id", int(key[1:]))
    name = (db.execute("SELECT name FROM items WHERE id=?", (kid,)) if key[0] == "i" else db.execute("SELECT name FROM products WHERE id=?", (kid,))).fetchone()
    run = db.execute(f"SELECT COALESCE(SUM(qty_milli),0) FROM stock_ledger WHERE {fk}=? AND date(ts)<?", (kid, f)).fetchone()[0]
    rows = [{"ts": f"{f} (opening)", "doc_type": "Brought forward", "doc_ref": "", "note": "", "inq": None, "outq": None, "bal": run, "value": 0}]
    for r in db.execute(f"SELECT * FROM stock_ledger WHERE {fk}=? AND date(ts) BETWEEN ? AND ? ORDER BY ts, id", (kid, f, to)):
        run += r["qty_milli"]
        rows.append({"ts": r["ts"], "doc_type": r["doc_type"].replace("_", " "), "doc_ref": r["doc_ref"], "note": r["note"] or "",
                     "inq": r["qty_milli"] if r["qty_milli"] > 0 else None, "outq": -r["qty_milli"] if r["qty_milli"] < 0 else None,
                     "bal": run, "value": r["value_paise"]})
    cols = [col("ts", "Date / time"), col("doc_type", "Source"), col("doc_ref", "Document"), col("note", "Note"), col("inq", "In", "qty"),
            col("outq", "Out", "qty"), col("bal", "Balance", "qty"), col("value", "Value moved", "money")]
    return _result(f"Stock ledger: {name['name'] if name else ''}", cols, rows, f, to)


# ---------------------------------------------------------------- production & costing
def production_report(db, a):
    f, to = month_range(a)
    tol = tolerance_bp(db)
    rows = []
    for r in db.execute("""SELECT r.*, p.name pname FROM production_runs r JOIN products p ON p.id=r.product_id
                           WHERE date(r.started_at) BETWEEN ? AND ? ORDER BY r.id""", (f, to)):
        out = r["actual_output_milli"]
        var = None if out is None else round((out - r["expected_output_milli"]) * 10000 / r["expected_output_milli"])
        rows.append({"run": r["run_no"], "batch": r["batch_no"], "product": r["pname"], "status": r["status"].replace("_", " "),
                     "expected": r["expected_output_milli"], "actual": out, "var": var,
                     "flag": "" if var is None else ("FLAGGED" if abs(out - r["expected_output_milli"]) * 10000 > tol * r["expected_output_milli"] else "ok"),
                     "damaged": r["damaged_pouches"], "scrap": r["scrap_packets"]})
    cols = [col("run", "Run"), col("batch", "Batch"), col("product", "Product"), col("status", "Status"),
            col("expected", "Expected packets", "qty", True), col("actual", "Actual packets", "qty", True),
            col("var", "Yield variance", "pct"), col("flag", f"Flag (> {tol/100:g}%)"),
            col("damaged", "Damaged pouches", "int", True), col("scrap", "Scrapped packets", "int", True)]
    return _result("Production report: yield and wastage", cols, rows, f, to)


def consumption_report(db, a):
    f, to = month_range(a)
    tol = tolerance_bp(db)
    rows = []
    for r in db.execute("""SELECT r.run_no, r.batch_no, i.name, i.unit, l.* FROM run_lines l JOIN production_runs r ON r.id=l.run_id
                           JOIN items i ON i.id=l.item_id WHERE date(r.started_at) BETWEEN ? AND ? ORDER BY r.id, i.category, i.name""", (f, to)):
        var = None if not r["planned_milli"] else round((r["actual_milli"] - r["planned_milli"]) * 10000 / r["planned_milli"])
        flag = bool(r["actual_milli"]) and abs(r["actual_milli"] - r["planned_milli"]) * 10000 > tol * r["planned_milli"]
        rows.append({"run": r["run_no"], "batch": r["batch_no"], "item": r["name"], "unit": r["unit"], "planned": r["planned_milli"],
                     "actual": r["actual_milli"], "diff": r["actual_milli"] - r["planned_milli"], "var": var, "flag": "FLAGGED" if flag else "ok",
                     "value": r["value_paise"]})
    cols = [col("run", "Run"), col("batch", "Batch"), col("item", "Material"), col("unit", "Unit"), col("planned", "Planned", "qty"),
            col("actual", "Actual", "qty"), col("diff", "Difference", "qty"), col("var", "Variance", "pct"),
            col("flag", f"Flag (> {tol/100:g}%)"), col("value", "Cost issued", "money", True)]
    return _result("Planned vs actual consumption", cols, rows, f, to)


def cost_report(db, a):
    f, to = month_range(a)
    runs = db.execute("""SELECT r.*, p.name pname, p.price_paise FROM production_runs r JOIN products p ON p.id=r.product_id
                         WHERE r.status='completed' AND date(r.started_at) BETWEEN ? AND ? ORDER BY r.id""", (f, to)).fetchall()
    month_packets, shared = {}, {}
    for r in db.execute("SELECT strftime('%Y-%m', started_at) m, SUM(actual_output_milli) q FROM production_runs WHERE status='completed' GROUP BY m"):
        month_packets[r["m"]] = r["q"]
    for r in db.execute("SELECT month, SUM(amount_paise) s FROM overheads WHERE run_id IS NULL GROUP BY month"):
        shared[r["month"]] = r["s"]
    rows = []
    for r in runs:
        mat = db.execute("SELECT COALESCE(SUM(value_paise),0) FROM run_lines WHERE run_id=?", (r["id"],)).fetchone()[0]
        oh = db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM overheads WHERE run_id=?", (r["id"],)).fetchone()[0]
        m = r["started_at"][:7]
        alloc = 0
        if shared.get(m) and month_packets.get(m):
            alloc = int(round(Decimal(shared[m]) * r["actual_output_milli"] / month_packets[m]))
        total = mat + oh + alloc
        good = r["actual_output_milli"]
        cpp = float(Decimal(total) * 1000 / good) if good else None
        rows.append({"run": r["run_no"], "product": r["pname"], "good": good, "materials": mat, "direct": oh, "shared": alloc, "total": total,
                     "cpp": cpp, "price": r["price_paise"], "margin": None if cpp is None else r["price_paise"] - cpp,
                     "mpct": None if cpp is None or not r["price_paise"] else round((r["price_paise"] - cpp) * 10000 / r["price_paise"])})
    cols = [col("run", "Run"), col("product", "Product"), col("good", "Good packets", "qty", True), col("materials", "Materials + packaging", "money", True),
            col("direct", "Labour / power / gas (run)", "money", True), col("shared", "Shared overhead allocated", "money", True),
            col("total", "Total cost", "money", True), col("cpp", "Cost per packet", "moneyf"), col("price", "Selling price", "money"),
            col("margin", "Margin per packet", "moneyf"), col("mpct", "Margin %", "pct")]
    return _result("Cost per packet", cols, rows, f, to,
                   "Materials at weighted-average cost; run overheads charged to the run; monthly shared overheads allocated by good packets.")


# ---------------------------------------------------------------- money in
def payments_report(db, a):
    f, to = month_range(a)
    rows = [{"date": r["paid_on"], "customer": r["cname"], "invoice": r["invoice_no"] or "On account", "mode": r["mode"], "ref": r["ref"] or "",
             "amount": r["amount_paise"]} for r in db.execute(
        """SELECT p.*, c.name cname, i.invoice_no FROM payments p JOIN customers c ON c.id=p.customer_id
           LEFT JOIN invoices i ON i.id=p.invoice_id WHERE p.paid_on BETWEEN ? AND ? ORDER BY p.paid_on, p.id""", (f, to))]
    cols = [col("date", "Date", "date"), col("customer", "Customer"), col("invoice", "Against"), col("mode", "Mode"), col("ref", "Reference"),
            col("amount", "Amount", "money", True)]
    return _result("Payments and collections", cols, rows, f, to)


def outstanding(db, a):
    _, to = month_range(a)
    asof = datetime.strptime(to, "%Y-%m-%d").date()
    rows = []
    for c in db.execute("SELECT * FROM customers ORDER BY name"):
        invs = db.execute("""SELECT i.id, i.date, i.total_paise - COALESCE((SELECT SUM(amount_paise) FROM payments WHERE invoice_id=i.id),0) bal
                             FROM invoices i WHERE i.customer_id=? AND i.status='posted' AND i.date<=? ORDER BY i.date, i.id""", (c["id"], to)).fetchall()
        # unallocated receipts (on account, or against a since-cancelled invoice) are applied to the oldest invoices first
        credit = db.execute("""SELECT COALESCE(SUM(p.amount_paise),0) FROM payments p LEFT JOIN invoices i ON i.id=p.invoice_id
                               WHERE p.customer_id=? AND (p.invoice_id IS NULL OR i.status='cancelled')""", (c["id"],)).fetchone()[0]
        b = {"b0": 0, "b1": 0, "b2": 0, "b3": 0}
        for inv in invs:
            bal = inv["bal"]
            use = min(bal, credit)
            bal, credit = bal - use, credit - use
            age = (asof - datetime.strptime(inv["date"], "%Y-%m-%d").date()).days
            b["b0" if age <= 30 else "b1" if age <= 60 else "b2" if age <= 90 else "b3"] += bal
        total = sum(b.values()) - credit
        if total or any(b.values()):
            rows.append({"customer": c["name"], "phone": c["phone"] or "", **b, "advance": credit, "total": total})
    cols = [col("customer", "Customer"), col("phone", "Phone"), col("b0", "0-30 days", "money", True), col("b1", "31-60 days", "money", True),
            col("b2", "61-90 days", "money", True), col("b3", "Over 90 days", "money", True), col("advance", "Unapplied advance", "money", True),
            col("total", "Outstanding", "money", True)]
    return _result("Outstanding receivables with ageing", cols, rows, None, to, "Age counted from invoice date to the 'to' date.")


REPORTS = {
    "sales_register": sales_register, "customer_sales": customer_sales, "product_sales": product_sales, "gst_summary": gst_summary,
    "gstr1": gstr1, "purchase_register": purchase_register, "stock_summary": stock_summary, "stock_ledger": stock_ledger,
    "production": production_report, "consumption": consumption_report, "cost": cost_report, "payments": payments_report,
    "outstanding": outstanding,
}


# ---------------------------------------------------------------- dashboard
def dashboard(db):
    t, month = today(), today()[:7]
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]
    d = {
        "orders_today": one("SELECT COUNT(*) FROM orders WHERE date(created_at)=?", t),
        "orders_pending": one("SELECT COUNT(*) FROM orders WHERE status IN ('submitted','approved')"),
        "sales_month": one("SELECT COALESCE(SUM(taxable_paise),0) FROM invoices WHERE status='posted' AND substr(date,1,7)=?", month),
        "collected_month": one("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE substr(paid_on,1,7)=?", month),
        "tolerance_bp": tolerance_bp(db),
    }
    d["outstanding_total"] = sum(customer_outstanding(db, c["id"]) for c in db.execute("SELECT id FROM customers"))
    stock = []
    for r in db.execute("SELECT * FROM items WHERE active=1 ORDER BY category, name"):
        q, v = balance(db, item_id=r["id"])
        stock.append({"kind": r["category"], "name": r["name"], "unit": r["unit"], "qty_milli": q, "value_paise": v,
                      "low": bool(r["reorder_level_milli"]) and q <= r["reorder_level_milli"], "reorder_milli": r["reorder_level_milli"]})
    for r in db.execute("SELECT * FROM products WHERE active=1 ORDER BY name"):
        q, v = balance(db, product_id=r["id"])
        stock.append({"kind": "fg", "name": r["name"], "unit": "pkt", "qty_milli": q, "value_paise": v,
                      "low": bool(r["reorder_level_milli"]) and q <= r["reorder_level_milli"], "reorder_milli": r["reorder_level_milli"]})
    d["stock"] = stock
    d["stock_value_paise"] = sum(s["value_paise"] for s in stock)
    alerts = []
    tol = d["tolerance_bp"]
    for r in db.execute("SELECT * FROM production_runs WHERE status='completed' ORDER BY id DESC LIMIT 10"):
        if abs(r["actual_output_milli"] - r["expected_output_milli"]) * 10000 > tol * r["expected_output_milli"]:
            alerts.append({"run_id": r["id"], "run": r["run_no"], "what": "Yield",
                           "bp": round((r["actual_output_milli"] - r["expected_output_milli"]) * 10000 / r["expected_output_milli"])})
        for l in db.execute("SELECT l.*, i.name FROM run_lines l JOIN items i ON i.id=l.item_id WHERE l.run_id=?", (r["id"],)):
            if l["planned_milli"] and l["actual_milli"] and abs(l["actual_milli"] - l["planned_milli"]) * 10000 > tol * l["planned_milli"]:
                alerts.append({"run_id": r["id"], "run": r["run_no"], "what": l["name"],
                               "bp": round((l["actual_milli"] - l["planned_milli"]) * 10000 / l["planned_milli"])})
    d["variance_alerts"] = alerts
    from services import run_costs
    cost = []
    for r in db.execute("""SELECT r.* FROM production_runs r WHERE r.status='completed' AND r.id=(SELECT MAX(id) FROM production_runs
                           WHERE product_id=r.product_id AND status='completed')"""):
        c = run_costs(db, r)
        p = db.execute("SELECT name FROM products WHERE id=?", (r["product_id"],)).fetchone()
        cost.append({"product": p["name"], "run": r["run_no"], **c})
    d["cost_per_packet"] = cost
    return d


# ---------------------------------------------------------------- export
def cell(c, v):
    """Plain python value for CSV/XLSX/PDF."""
    if v is None or v == "":
        return ""
    t = c["type"]
    if t == "money":
        return Decimal(v) / 100
    if t == "moneyf":
        return round(Decimal(str(v)) / 100, 2)
    if t == "qty":
        return Decimal(v) / 1000
    if t == "pct":
        return Decimal(v) / 100
    return v


def _pretty(c, v):
    v = cell(c, v)
    if v == "":
        return ""
    t = c["type"]
    if t in ("money", "moneyf"):
        return f"{v:,.2f}"
    if t == "qty":
        return milli_to_str(int(v * 1000))
    if t == "pct":
        return f"{v:,.2f}%"
    return str(v)


def to_csv(rep):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([rep["title"] + (f"  [{rep['from']} to {rep['to']}]" if rep.get("to") else "")])
    w.writerow([c["label"] for c in rep["columns"]])
    for r in rep["rows"]:
        w.writerow([cell(c, r.get(c["key"])) for c in rep["columns"]])
    if rep["totals"]:
        w.writerow(["Total"] + [cell(c, rep["totals"].get(c["key"], "")) if c["key"] in rep["totals"] else "" for c in rep["columns"]][1:])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(rep):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "".join(ch for ch in rep["title"] if ch not in "[]:*?/\\")[:31]
    ws.append([rep["title"] + (f"  ({rep['from']} to {rep['to']})" if rep.get("to") else "")])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([c["label"] for c in rep["columns"]])
    for cc in ws[2]:
        cc.font = Font(bold=True, color="FFFFFF")
        cc.fill = PatternFill("solid", fgColor="1F4D3A")
        cc.alignment = Alignment(wrap_text=True, vertical="center")
    fmt = {"money": "#,##0.00", "moneyf": "#,##0.00", "qty": "#,##0.###", "pct": '0.00"%"'}
    for r in rep["rows"]:
        ws.append([float(x) if isinstance(x, Decimal) else x for x in (cell(c, r.get(c["key"])) for c in rep["columns"])])
    if rep["totals"]:
        ws.append(["Total"] + [float(cell(c, rep["totals"][c["key"]])) if c["key"] in rep["totals"] else "" for c in rep["columns"]][1:])
        for cc in ws[ws.max_row]:
            cc.font = Font(bold=True)
    for i, c in enumerate(rep["columns"], 1):
        ws.column_dimensions[chr(64 + i) if i < 27 else "Z"].width = max(12, min(34, len(c["label"]) + 4))
        if c["type"] in fmt:
            for row in ws.iter_rows(min_row=3, min_col=i, max_col=i):
                row[0].number_format = fmt[c["type"]]
    ws.freeze_panes = "A3"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_pdf(rep, company=""):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=rep["title"])
    ss = getSampleStyleSheet()
    cellst = ParagraphStyle("c", parent=ss["BodyText"], fontSize=7, leading=8.5)
    head = ParagraphStyle("h", parent=cellst, textColor=colors.white, fontName="Helvetica-Bold")
    story = [Paragraph(f"<b>{company}</b>", ss["Normal"]), Paragraph(rep["title"], ss["Title"])]
    if rep.get("to"):
        story.append(Paragraph(f"Period: {rep.get('from') or 'start'} to {rep['to']}", ss["Normal"]))
    story.append(Spacer(1, 4 * mm))
    cols = rep["columns"]
    data = [[Paragraph(c["label"], head) for c in cols]]
    right = [i for i, c in enumerate(cols) if c["type"] in ("money", "moneyf", "qty", "pct", "int")]
    for r in rep["rows"]:
        data.append([Paragraph(str(_pretty(c, r.get(c["key"])) or "").replace("&", "&amp;"), cellst) for c in cols])
    if rep["totals"]:
        data.append([Paragraph("<b>Total</b>" if i == 0 else (f"<b>{_pretty(c, rep['totals'][c['key']])}</b>" if c["key"] in rep["totals"] else ""), cellst)
                     for i, c in enumerate(cols)])
    t = Table(data, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4D3A")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C2BC")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F4")])]
    for i in right:
        style.append(("ALIGN", (i, 1), (i, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    story.append(t)
    if rep.get("note"):
        story += [Spacer(1, 3 * mm), Paragraph(rep["note"], cellst)]
    doc.build(story)
    return out.getvalue()
