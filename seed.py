"""Loads the section 5 benchmark master data.

    python seed.py            # seed an empty database
    python seed.py --reset    # delete the database and seed again
"""
import os
import sys

from werkzeug.security import generate_password_hash

import db as dbm
from core import rupees_to_paise, to_milli

DEMO_PASSWORD = "demo123"
OPENING_TS = "2000-01-01 00:00:00"


def seed(conn):
    q = conn.execute
    pw = generate_password_hash(DEMO_PASSWORD)
    for u, n, r in [("admin", "Factory Admin", "admin"), ("salesman_a", "Salesman A", "salesman"), ("salesman_b", "Salesman B", "salesman"),
                    ("production", "Production Supervisor", "production"), ("accountant", "Accountant / Auditor", "accountant"),
                    ("owner", "Owner", "owner")]:
        q("INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)", (u, pw, n, r))
    for k, v in {"company_name": "Kaveri Snacks & Sweets", "company_gstin": "33ABCDE1234F1Z5", "company_state": "Tamil Nadu",
                 "company_state_code": "33", "company_phone": "0431-2000000",
                 "company_address": "12 Industrial Estate, Thuvakudi, Tiruchirappalli, Tamil Nadu 620015", "tolerance_bp": "300"}.items():
        q("INSERT INTO settings(key,value) VALUES(?,?)", (k, v))

    q("INSERT INTO customers(name,type,gstin,state,state_code,address,phone) VALUES(?,?,?,?,?,?,?)",
      ("Retailer A", "retailer", "33AAACR1111A1Z2", "Tamil Nadu", "33", "45 Big Bazaar Street, Tiruchirappalli, Tamil Nadu", "9876500001"))
    q("INSERT INTO customers(name,type,gstin,state,state_code,address,phone) VALUES(?,?,?,?,?,?,?)",
      ("Distributor B", "distributor", "29AAACD2222B1Z8", "Karnataka", "29", "8 Market Road, Bengaluru, Karnataka", "9876500002"))
    q("INSERT INTO suppliers(name,gstin,phone) VALUES(?,?,?)", ("Sri Venkateswara Flour Mills", "33AAACS3333C1Z4", "9876500003"))

    items = [("FLOUR", "Flour", "raw", "kg", None, 50, 100, "42"), ("OIL", "Cooking oil", "raw", "l", None, 10, 30, "140"),
             ("SPICE", "Salt and spice mix", "raw", "kg", None, 1, 5, "200"), ("POUCH200", "200 g pouch", "packaging", "pc", "pouch", 100, 500, "2.50"),
             ("CARTON20", "Carton (holds 20 packets)", "packaging", "pc", "carton", 10, 30, "15")]
    ids = {}
    for code, name, cat, unit, role, rl, oq, rate in items:
        cur = q("INSERT INTO items(code,name,category,unit,packaging_role,reorder_level_milli) VALUES(?,?,?,?,?,?)",
                (code, name, cat, unit, role, to_milli(rl, unit, unit)))
        ids[code] = cur.lastrowid
        q("""INSERT INTO stock_ledger(ts,item_id,qty_milli,value_paise,doc_type,doc_id,doc_ref,note) VALUES(?,?,?,?,'opening',?,?,?)""",
          (OPENING_TS, cur.lastrowid, to_milli(oq, unit, unit), rupees_to_paise(oq * __import__("decimal").Decimal(rate)), cur.lastrowid, "OPENING", "Opening balance"))

    cur = q("""INSERT INTO products(code,name,hsn,gst_rate_x100,pack_weight_g,packets_per_carton,price_paise,bom_yield_milli,shelf_life_days,reorder_level_milli)
               VALUES('MUR200','Murukku 200 g','21069099',500,200,20,5000,?,90,?)""", (200000, 40000))
    pid = cur.lastrowid
    for code, qty in [("FLOUR", 40), ("OIL", 10), ("SPICE", "1.2"), ("POUCH200", 200), ("CARTON20", 10)]:
        unit = [i for i in items if i[0] == code][0][3]
        q("INSERT INTO bom_lines(product_id,item_id,qty_milli) VALUES(?,?,?)", (pid, ids[code], to_milli(qty, unit, unit)))
    # opening finished goods: 50 packets. The brief gives no cost, so we assume Rs 30.00 per packet (documented in README).
    q("""INSERT INTO stock_ledger(ts,product_id,qty_milli,value_paise,doc_type,doc_id,doc_ref,note) VALUES(?,?,?,?,'opening',?,?,?)""",
      (OPENING_TS, pid, 50000, 50 * 3000, pid, "OPENING", "Opening balance (valued at assumed Rs 30.00/packet)"))
    conn.commit()


if __name__ == "__main__":
    if "--reset" in sys.argv and os.path.exists(dbm.DB_PATH):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(dbm.DB_PATH + ext):
                os.remove(dbm.DB_PATH + ext)
    conn = dbm.init_db()
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        print("Database already has data. Use --reset to start over.")
    else:
        seed(conn)
        print(f"Seeded {dbm.DB_PATH}. Logins: admin, salesman_a, salesman_b, production, accountant, owner (password {DEMO_PASSWORD})")
