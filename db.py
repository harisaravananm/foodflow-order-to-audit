"""SQLite schema and connection helpers."""
import os
import sqlite3
from flask import g

DB_PATH = os.environ.get("FOODFLOW_DB", os.path.join(os.path.dirname(__file__), "data", "foodflow.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('salesman','admin','production','accountant','owner')),
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY, value INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'retailer',
  gstin TEXT, state TEXT NOT NULL, state_code TEXT NOT NULL, address TEXT, phone TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, hsn TEXT NOT NULL,
  gst_rate_x100 INTEGER NOT NULL DEFAULT 500, pack_weight_g INTEGER, packets_per_carton INTEGER NOT NULL DEFAULT 20,
  price_paise INTEGER NOT NULL, bom_yield_milli INTEGER NOT NULL DEFAULT 0, shelf_life_days INTEGER NOT NULL DEFAULT 90,
  reorder_level_milli INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS customer_prices(
  customer_id INTEGER NOT NULL REFERENCES customers(id), product_id INTEGER NOT NULL REFERENCES products(id),
  price_paise INTEGER NOT NULL, PRIMARY KEY(customer_id, product_id));
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN ('raw','packaging')), unit TEXT NOT NULL CHECK(unit IN ('kg','l','pc')),
  packaging_role TEXT CHECK(packaging_role IN ('pouch','carton') OR packaging_role IS NULL),
  reorder_level_milli INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS bom_lines(
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
  item_id INTEGER NOT NULL REFERENCES items(id), qty_milli INTEGER NOT NULL, UNIQUE(product_id,item_id));
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, gstin TEXT, phone TEXT);

CREATE TABLE IF NOT EXISTS purchases(
  id INTEGER PRIMARY KEY, purchase_no TEXT UNIQUE NOT NULL, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  bill_no TEXT, bill_date TEXT NOT NULL, total_paise INTEGER NOT NULL, tax_paise INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_lines(
  id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL REFERENCES purchases(id), item_id INTEGER NOT NULL REFERENCES items(id),
  qty_milli INTEGER NOT NULL, entered_qty TEXT NOT NULL, entered_unit TEXT NOT NULL, rate TEXT NOT NULL,
  amount_paise INTEGER NOT NULL, gst_rate_x100 INTEGER NOT NULL DEFAULT 0, tax_paise INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS production_runs(
  id INTEGER PRIMARY KEY, run_no TEXT UNIQUE NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
  batch_no TEXT NOT NULL, expiry_date TEXT, batches_milli INTEGER NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, completed_at TEXT, expected_output_milli INTEGER NOT NULL,
  actual_output_milli INTEGER, damaged_pouches INTEGER NOT NULL DEFAULT 0, scrap_packets INTEGER NOT NULL DEFAULT 0,
  note TEXT, created_by INTEGER REFERENCES users(id), completed_by INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS run_lines(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES production_runs(id), item_id INTEGER NOT NULL REFERENCES items(id),
  planned_milli INTEGER NOT NULL, actual_milli INTEGER NOT NULL DEFAULT 0, value_paise INTEGER NOT NULL DEFAULT 0,
  UNIQUE(run_id,item_id));
CREATE TABLE IF NOT EXISTS overheads(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES production_runs(id), month TEXT NOT NULL, kind TEXT NOT NULL,
  amount_paise INTEGER NOT NULL, note TEXT, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS stock_ledger(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, item_id INTEGER REFERENCES items(id), product_id INTEGER REFERENCES products(id),
  qty_milli INTEGER NOT NULL, value_paise INTEGER NOT NULL, doc_type TEXT NOT NULL, doc_id INTEGER, doc_ref TEXT,
  note TEXT, user_id INTEGER REFERENCES users(id),
  CHECK((item_id IS NULL) <> (product_id IS NULL)));
CREATE INDEX IF NOT EXISTS ix_ledger_item ON stock_ledger(item_id);
CREATE INDEX IF NOT EXISTS ix_ledger_prod ON stock_ledger(product_id);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, client_uuid TEXT UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id),
  salesman_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL, note TEXT, reject_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_lines(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER NOT NULL REFERENCES products(id),
  qty_pkts INTEGER NOT NULL, price_paise INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY, invoice_no TEXT UNIQUE NOT NULL, fy TEXT NOT NULL, order_id INTEGER REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id), date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'posted',
  supply_type TEXT NOT NULL, place_of_supply TEXT NOT NULL, place_code TEXT NOT NULL,
  cust_name TEXT NOT NULL, cust_gstin TEXT, cust_address TEXT,
  taxable_paise INTEGER NOT NULL, cgst_paise INTEGER NOT NULL, sgst_paise INTEGER NOT NULL, igst_paise INTEGER NOT NULL,
  total_paise INTEGER NOT NULL, share_token TEXT UNIQUE, dispatched_at TEXT, dispatched_by INTEGER REFERENCES users(id),
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoice_lines(
  id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), product_id INTEGER NOT NULL REFERENCES products(id),
  description TEXT NOT NULL, hsn TEXT NOT NULL, qty_pkts INTEGER NOT NULL, rate_paise INTEGER NOT NULL, taxable_paise INTEGER NOT NULL,
  gst_rate_x100 INTEGER NOT NULL, cgst_paise INTEGER NOT NULL, sgst_paise INTEGER NOT NULL, igst_paise INTEGER NOT NULL,
  cogs_paise INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS credit_notes(
  id INTEGER PRIMARY KEY, cn_no TEXT UNIQUE NOT NULL, invoice_id INTEGER NOT NULL REFERENCES invoices(id), date TEXT NOT NULL,
  reason TEXT NOT NULL, amount_paise INTEGER NOT NULL, stock_returned INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), invoice_id INTEGER REFERENCES invoices(id),
  amount_paise INTEGER NOT NULL CHECK(amount_paise>0), paid_on TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'cash', ref TEXT,
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, user_id INTEGER, username TEXT, entity TEXT NOT NULL, entity_id INTEGER,
  action TEXT NOT NULL, detail TEXT);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path=None):
    os.makedirs(os.path.dirname(path or DB_PATH), exist_ok=True)
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def next_number(db, key: str) -> int:
    db.execute("INSERT INTO counters(key,value) VALUES(?,0) ON CONFLICT(key) DO NOTHING", (key,))
    db.execute("UPDATE counters SET value=value+1 WHERE key=?", (key,))
    return db.execute("SELECT value FROM counters WHERE key=?", (key,)).fetchone()[0]
