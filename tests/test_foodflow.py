import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FOODFLOW_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import app as appmod  # noqa: E402
import benchmark  # noqa: E402
from core import beyond_tolerance, compute_tax, fy_of, pct_bp, rupees_in_words, to_milli  # noqa: E402


class MathTests(unittest.TestCase):
    def test_unit_conversion(self):
        self.assertEqual(to_milli(500, "g", "kg"), 500)  # 0.5 kg
        self.assertEqual(to_milli("10.5", "litre", "l"), 10500)
        self.assertEqual(to_milli(250, "ml", "l"), 250)
        self.assertEqual(to_milli(2, "kg", "kg"), 2000)
        with self.assertRaises(ValueError):
            to_milli(1, "kg", "l")

    def test_gst_intra_and_inter(self):
        self.assertEqual(compute_tax(750000, 500, True), {"cgst": 18750, "sgst": 18750, "igst": 0})
        self.assertEqual(compute_tax(600000, 500, False), {"cgst": 0, "sgst": 0, "igst": 30000})

    def test_gst_rounding_half_up(self):
        # 15150 paise at 2.5% = 378.75 paise -> 379 each side
        self.assertEqual(compute_tax(15150, 500, True)["cgst"], 379)

    def test_tolerance_is_exact(self):
        self.assertFalse(beyond_tolerance(194, 200, 300))  # exactly -3% is not beyond 3%
        self.assertTrue(beyond_tolerance(188, 200, 300))
        self.assertEqual(pct_bp(10500, 10000), 500)

    def test_fy_and_words(self):
        self.assertEqual(fy_of("2026-03-31"), "2025-26")
        self.assertEqual(fy_of("2026-04-01"), "2026-27")
        self.assertEqual(rupees_in_words(787500), "Rupees Seven Thousand Eight Hundred Seventy Five Only")


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = benchmark.run(appmod.app)

    def test_stock_balances(self):
        o = self.out
        self.assertEqual((o["flour"], o["oil"], o["pouches"], o["cartons"]), (120, 19.5, 306, 21))
        self.assertEqual((o["fg_after_run"], o["fg_after_dispatch"]), (238, 88))

    def test_variances(self):
        r = self.out["run_final"]
        self.assertEqual(r["yield_variance_bp"], -600)
        self.assertTrue(r["yield_flagged"])
        oil = [l for l in r["lines"] if l["name"] == "Cooking oil"][0]
        self.assertEqual((oil["variance_bp"], oil["flagged"]), (500, True))
        pouch = [l for l in r["lines"] if l["name"] == "200 g pouch"][0]
        self.assertFalse(pouch["flagged"])

    def test_invoice_and_outstanding(self):
        i = self.out["invoice_a"]
        self.assertEqual((i["taxable_paise"], i["cgst_paise"], i["sgst_paise"], i["igst_paise"], i["total_paise"]),
                         (750000, 18750, 18750, 0, 787500))
        self.assertTrue(i["invoice_no"].startswith("INV/"))
        self.assertEqual(self.out["invoice_a_after_payment"]["balance_paise"], 387500)

    def test_shortage_blocks_invoice(self):
        code, j = self.out["order_b_invoice_blocked"]
        self.assertEqual(code, 409)
        self.assertEqual(j["shortages"][0]["short"], 32)

    def test_cost_per_packet(self):
        c = self.out["run_final"]["costs"]
        self.assertEqual(c["material_paise"], 405500)  # flour@WA 1725 + oil 1470 + spice 240 + pouches 485 + cartons 135
        self.assertEqual(c["overhead_paise"], 180000)
        self.assertAlmostEqual(c["cost_per_packet_paise"], 3114.36, places=2)

    def test_ledger_reconciles_with_summary(self):
        a = appmod.app.test_client()
        a.post("/api/login", json={"username": "accountant", "password": "demo123"})
        rep = a.get("/api/reports/stock_summary?from=2000-01-01&to=2100-01-01").json
        for r in rep["rows"]:
            self.assertEqual(r["opening"] + r["receipts"] + r["issues"] + r["adj"], r["closing"], r["name"])

    def test_every_ledger_row_has_source(self):
        from db import connect
        c = connect(os.environ["FOODFLOW_DB"])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM stock_ledger WHERE doc_type IS NULL OR doc_ref IS NULL").fetchone()[0], 0)


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        benchmark.run(appmod.app) if not hasattr(BenchmarkTests, "out") else None

    def client(self, who):
        c = appmod.app.test_client()
        self.assertEqual(c.post("/api/login", json={"username": who, "password": "demo123"}).status_code, 200)
        return c

    def test_roles_are_enforced(self):
        s = self.client("salesman_a")
        self.assertEqual(s.get("/api/invoices").status_code, 403)
        self.assertEqual(s.get("/api/reports/sales_register").status_code, 403)
        self.assertEqual(s.post("/api/payments", json={}).status_code, 403)
        acc = self.client("accountant")
        self.assertEqual(acc.post("/api/purchases", json={}).status_code, 403)
        self.assertEqual(acc.get("/api/reports/sales_register").status_code, 200)
        self.assertEqual(appmod.app.test_client().get("/api/meta").status_code, 401)

    def test_offline_order_retry_is_idempotent(self):
        s, meta = self.client("salesman_b"), self.client("admin").get("/api/meta").json
        payload = {"customer_id": meta["customers"][0]["id"], "client_uuid": "dup-1",
                   "lines": [{"product_id": meta["products"][0]["id"], "qty": 2, "unit": "carton"}]}
        a, b = s.post("/api/orders", json=payload).json, s.post("/api/orders", json=payload).json
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(a["order"]["lines"][0]["qty_pkts"], 40)  # 2 cartons x 20

    def test_cannot_issue_more_than_stock(self):
        p, meta = self.client("production"), self.client("admin").get("/api/meta").json
        item = {i["code"]: i["id"] for i in meta["items"]}
        r = p.post("/api/runs", json={"product_id": meta["products"][0]["id"], "issues": [{"item_id": item["OIL"], "qty": 9999, "unit": "l"}]})
        self.assertEqual(r.status_code, 409)

    def test_distributor_b_igst(self):
        admin = self.client("admin")
        o = [x for x in admin.get("/api/orders?status=approved").json if x["customer"] == "Distributor B"][0]
        r = admin.post(f"/api/orders/{o['id']}/invoice", json={"fit_to_stock": True})
        self.assertEqual(r.status_code, 200)
        inv = self.client("accountant").get(f"/api/invoices/{r.json['id']}").json
        self.assertEqual((inv["supply_type"], inv["taxable_paise"], inv["igst_paise"], inv["cgst_paise"], inv["total_paise"]), ("inter", 440000, 22000, 0, 462000))

    def test_zz_credit_note_returns_stock(self):
        admin = self.client("admin")
        inv = [i for i in self.client("accountant").get("/api/invoices").json if i["status"] == "posted" and i["dispatched_at"]][0]
        stock = lambda: {s["name"]: s["qty_milli"] for s in self.client("owner").get("/api/dashboard").json["stock"]}["Murukku 200 g"]
        before = stock()
        self.assertEqual(admin.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "wrong rate"}).status_code, 200)
        self.assertEqual(stock() - before, 150000)
        self.assertEqual(admin.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "again"}).status_code, 400)
        self.assertTrue(any(a["action"] == "cancel" for a in self.client("owner").get("/api/audit").json))

    def test_exports(self):
        a = self.client("accountant")
        for name in ("sales_register", "gst_summary", "stock_summary", "production", "consumption", "cost", "outstanding",
                     "purchase_register", "payments", "gstr1", "customer_sales", "product_sales"):
            for fmt, sig in (("csv", b""), ("xlsx", b"PK"), ("pdf", b"%PDF")):
                r = a.get(f"/api/reports/{name}?format={fmt}")
                self.assertEqual(r.status_code, 200, (name, fmt))
                self.assertTrue(r.data.startswith(sig))
        self.assertTrue(a.get("/api/invoices/1/pdf").data.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
