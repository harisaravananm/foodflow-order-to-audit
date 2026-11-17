"""Runs the section 5 scenario end to end through the HTTP API and prints the checks.

    python benchmark.py        # uses a throw-away database, never touches data/foodflow.db
"""
import os
import sys
import tempfile


def make_client(role_user, app):
    c = app.test_client()
    r = c.post("/api/login", json={"username": role_user, "password": "demo123"})
    assert r.status_code == 200, r.json
    return c


def run(app):
    A = {n: make_client(n, app) for n in ("admin", "salesman_a", "salesman_b", "production", "accountant", "owner")}
    out = {}
    meta = A["admin"].get("/api/meta").json
    cust = {c["name"]: c["id"] for c in meta["customers"]}
    item = {i["code"]: i["id"] for i in meta["items"]}
    prod = meta["products"][0]["id"]
    sup = meta["suppliers"][0]["id"]
    bal = lambda: {s["name"]: s for s in A["owner"].get("/api/dashboard").json["stock"]}

    # 1 purchase 60 kg flour @ 45
    r = A["production"].post("/api/purchases", json={"supplier_id": sup, "bill_no": "SV-101", "lines": [{"item_id": item["FLOUR"], "qty": 60, "unit": "kg", "rate": 45}]})
    assert r.status_code == 200, r.json
    # 2 start run: 40 kg flour, 10.5 L oil, 1.2 kg spice
    r = A["production"].post("/api/runs", json={"product_id": prod, "batches": 1, "issues": [
        {"item_id": item["FLOUR"], "qty": 40, "unit": "kg"}, {"item_id": item["OIL"], "qty": 10.5, "unit": "l"}, {"item_id": item["SPICE"], "qty": 1.2, "unit": "kg"}]})
    assert r.status_code == 200, r.json
    run_id = r.json["id"]
    # 3 complete: 188 good, 6 pouches damaged, 9 cartons
    r = A["production"].post(f"/api/runs/{run_id}/complete", json={"good_output": 188, "damaged_pouches": 6, "packaging": [
        {"item_id": item["POUCH200"], "qty": 194}, {"item_id": item["CARTON20"], "qty": 9}]})
    assert r.status_code == 200, r.json
    out["run"] = r.json
    b = bal()
    out["flour"], out["oil"], out["pouches"], out["cartons"] = (b["Flour"]["qty_milli"] / 1000, b["Cooking oil"]["qty_milli"] / 1000,
                                                              b["200 g pouch"]["qty_milli"] / 1000, b["Carton (holds 20 packets)"]["qty_milli"] / 1000)
    out["fg_after_run"] = b["Murukku 200 g"]["qty_milli"] / 1000
    # 4 Salesman A order 150 for Retailer A
    r = A["salesman_a"].post("/api/orders", json={"customer_id": cust["Retailer A"], "client_uuid": "bench-a", "lines": [{"product_id": prod, "qty": 150, "unit": "pkt"}]})
    assert r.status_code == 200, r.json
    oa = r.json["id"]
    # 5 admin approves, invoices, dispatches
    assert A["admin"].post(f"/api/orders/{oa}/approve").status_code == 200
    r = A["admin"].post(f"/api/orders/{oa}/invoice", json={})
    assert r.status_code == 200, r.json
    inv_a = r.json["id"]
    assert A["admin"].post(f"/api/invoices/{inv_a}/dispatch").status_code == 200
    out["invoice_a"] = A["accountant"].get(f"/api/invoices/{inv_a}").json
    out["fg_after_dispatch"] = bal()["Murukku 200 g"]["qty_milli"] / 1000
    # 6 Salesman B order 120 for Distributor B -> shortage
    r = A["salesman_b"].post("/api/orders", json={"customer_id": cust["Distributor B"], "lines": [{"product_id": prod, "qty": 120, "unit": "pkt"}]})
    ob = r.json["id"]
    out["order_b_check"] = A["admin"].get(f"/api/orders/{ob}").json
    A["admin"].post(f"/api/orders/{ob}/approve")
    r = A["admin"].post(f"/api/orders/{ob}/invoice", json={})
    out["order_b_invoice_blocked"] = (r.status_code, r.json)
    # 7 payment 4000 from Retailer A
    r = A["admin"].post("/api/payments", json={"invoice_id": inv_a, "amount": 4000, "mode": "upi", "ref": "UPI-1"})
    assert r.status_code == 200, r.json
    out["invoice_a_after_payment"] = A["accountant"].get(f"/api/invoices/{inv_a}").json
    # 8 overheads
    for kind, amt in (("labour", 1200), ("gas/power", 600)):
        r = A["production"].post("/api/overheads", json={"run_id": run_id, "kind": kind, "amount": amt})
        assert r.status_code == 200, r.json
    out["run_final"] = A["production"].get(f"/api/runs/{run_id}").json
    out["inv_b_preview_igst"] = None
    return out


def main():
    d = tempfile.mkdtemp()
    os.environ["FOODFLOW_DB"] = os.path.join(d, "bench.db")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as appmod
    out = run(appmod.app)
    run_ = out["run_final"]
    oil = [l for l in run_["lines"] if l["name"] == "Cooking oil"][0]
    inv = out["invoice_a"]
    rows = [
        ("Flour closing balance (kg)", "120", out["flour"]),
        ("Oil closing balance (L)", "19.5", out["oil"]),
        ("Pouches closing balance", "306", out["pouches"]),
        ("Finished goods after run", "238", out["fg_after_run"]),
        ("Finished goods after Retailer A dispatch", "88", out["fg_after_dispatch"]),
        ("Yield variance %", "-6.00 (flagged)", f"{run_['yield_variance_bp']/100:.2f} (flagged={run_['yield_flagged']})"),
        ("Oil variance %", "+5.00 (flagged)", f"{oil['variance_bp']/100:+.2f} (flagged={oil['flagged']})"),
        ("Retailer A taxable", "7500.00", f"{inv['taxable_paise']/100:.2f}"),
        ("CGST / SGST", "187.50 / 187.50", f"{inv['cgst_paise']/100:.2f} / {inv['sgst_paise']/100:.2f}"),
        ("Invoice total", "7875.00", f"{inv['total_paise']/100:.2f}"),
        ("Retailer A outstanding", "3875.00", f"{out['invoice_a_after_payment']['balance_paise']/100:.2f}"),
        ("Distributor B shortage (packets)", "32", out["order_b_invoice_blocked"][1].get("shortages", [{}])[0].get("short")),
        ("Cost per packet (Rs)", "your figure", f"{run_['costs']['cost_per_packet_paise']/100:.2f}"),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"{'Check':<{w}}  {'Expected':<18} Actual")
    for a, e, v in rows:
        print(f"{a:<{w}}  {e:<18} {v}")


if __name__ == "__main__":
    main()
