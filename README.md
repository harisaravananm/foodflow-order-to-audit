<div align="center">

# 🥨 FoodFlow: Order-to-Audit System

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Ledger%20DB-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![Render](https://img.shields.io/badge/Deployed%20on-Render-46E3B7?logo=render&logoColor=white)
![PWA](https://img.shields.io/badge/PWA-Offline%20Orders-5A0FC8?logo=pwa&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-18%20Passing-2E7D4F)
![Benchmark](https://img.shields.io/badge/Section%205%20Benchmark-Verified-E9A82B)

**From a salesman's order to the auditor's report: GST invoices, BOM production with variance, stock ledger, costing and reports for a small food manufacturer.**

🔗 **[Live Demo]https://foodflow-order-to-audit.onrender.com/**  ·  📐 **[Design Note](DESIGN.md)**

</div>

---

## 🔑 Test Logins (password: `demo123`)

| Role | Username | What they can do |
| --- | --- | --- |
| 🛠️ Factory admin | `admin` | Approve orders, GST invoices, dispatch, payments |
| 📱 Salesman | `salesman_a`, `salesman_b` | Take orders on mobile, see own sales |
| 🏭 Production | `production` | Purchases, recipes, production runs, wastage |
| 📊 Accountant | `accountant` | Read-only reports and exports |
| 👤 Owner | `owner` | Dashboard, alerts, cost per packet, dues |

> ⏳ The free Render instance sleeps when idle, so the first load can take about a minute. Data resets on restart and reseeds with the benchmark data.

## 📖 Overview

- **Project:** Internship shortlisting task, NIT Tiruchirappalli (Centre for Digital Infrastructure)
- **Core idea:** every transaction is entered once and flows into stock, production, costing and accounts
- **Maintainer:** Hari Saravanan M ([@harisaravananm](https://github.com/harisaravananm))

---
## Run it

```
./run.sh                       # creates a venv, installs, seeds, serves http://localhost:5000
# or
pip install -r requirements.txt && python app.py
# or
docker compose up --build      # http://localhost:5000 (container listens on 7860)
```

The database (SQLite, `data/foodflow.db`) is created and seeded with the section 5 master data on first start.
`python seed.py --reset` wipes and reseeds it.

**Test logins** (password `demo123` for all): `admin`, `salesman_a`, `salesman_b`, `production`, `accountant`, `owner`.
The sign-in screen has one-tap buttons for each.

**Check the benchmark yourself**

```
python benchmark.py                      # runs section 5 end to end through the API on a throw-away DB and prints expected vs actual
python -m unittest discover -s tests -t .   # 18 tests: unit conversion, GST maths, tolerance, ledger reconciliation, roles, exports
```

## Stack and why

Python 3 + Flask + SQLite, with a dependency-free JavaScript single-page app (installable PWA).
SQLite gives a real relational database with transactions and constraints and needs no server, so the whole system starts with one command.
Flask keeps the business rules in plain Python (`services.py`) where they are easy to read and test. The UI has no build step, so there is nothing to compile before a demo.
Money is integer paise and quantities are integer thousandths of the base unit; `Decimal` is used for every intermediate step. No floats touch money or stock.

## What is built, against the section 6 tiers

| Tier | Item | Status |
| --- | --- | --- |
| Must | Login with roles (salesman, admin, production, plus accountant and owner), enforced on the server | Done |
| Must | Product, customer, raw-material, packaging and BOM masters | Done (Masters screen) |
| Must | Mobile order entry and admin approval | Done |
| Must | GST invoice as PDF (GSTIN, HSN, CGST+SGST or IGST by place of supply, FY numbering) | Done |
| Must | Production run updates raw and finished stock automatically | Done |
| Must | Planned vs actual consumption and yield with variance flag | Done |
| Must | Stock summary and stock ledger | Done |
| Must | Sales register and stock report exportable to CSV/Excel (and PDF) | Done |
| Should | Purchases and suppliers; payments, outstanding and ageing; cost per packet with overheads; GST summary; variance alerts; owner dashboard | Done |
| Stretch | Offline order capture with sync (PWA, queue in the phone, idempotent retry) | Built; server side tested, see known gaps |
| Stretch | Batch number and expiry on every production run | Done (recorded per run and shown in the ledger) |
| Stretch | Reorder levels and low-stock alerts | Done |
| Stretch | New pack size sharing a recipe (the interview example) | Done: "Add a product" with *Copy recipe from* scales yield and packaging by pack weight |
| Stretch | GSTR-1 shaped invoice detail report | Done |

Also: audit trail of every edit (user, time, before/after), credit notes instead of deleting invoices (stock returns at the cost it left at), customer-specific prices, WhatsApp and e-mail share links for invoices (unguessable public PDF link).

## Benchmark (section 5)

Run `python benchmark.py` to reproduce this table.

| Check | Expected | This system |
| --- | --- | --- |
| Flour closing balance | 120 kg | 120 kg |
| Oil closing balance | 19.5 L | 19.5 L |
| Pouches closing balance | 306 | 306 |
| Finished goods after run | 238 | 238 |
| Finished goods after Retailer A dispatch | 88 | 88 |
| Yield variance | 188 vs 200, -6%, flagged | -6.00%, flagged |
| Oil variance | +5%, flagged | +5.00%, flagged |
| Retailer A invoice | 7,500 + 187.50 + 187.50 = 7,875 | identical |
| Retailer A outstanding | 3,875 | 3,875 |
| Distributor B | shortage of 32, IGST if invoiced | shortage of 32 shown on the order; invoicing is blocked with the shortage list; "invoice what is in stock" gives 88 packets, Rs 4,400 + Rs 220 IGST |
| Cost per packet | our figure | **Rs 31.14** (margin Rs 18.86, 37.7%) |

Notes on differences and judgement calls:

* **Cartons are flagged at -10%.** The recipe plans 10 cartons per 200 packets; 188 packets needed 9 (188 / 20 rounded up). Pouches at 194 vs 200 are exactly -3.00%, which is *not* beyond a 3% tolerance, so they are not flagged. The tolerance test is exact integer arithmetic, not rounded percentages. Tolerance is editable under Masters > Settings.
* **Distributor B.** After Retailer A's dispatch only 88 packets remain, so 120 is 32 short. The system shows the shortage when the admin opens the order, lets the admin approve it, and refuses to invoice until the order is reduced; the "Invoice only what is in stock" prompt does that and logs it. Distributor B is in Karnataka, so that invoice is IGST: 88 packets give Rs 4,400 taxable + Rs 220 IGST = Rs 4,620 (covered by `test_distributor_b_igst`).
* **Costing method: weighted average.** Chosen because flour was bought at two rates (42 and 45) and one issue cannot sensibly be attributed to just one lot of a commodity that is mixed in a bin. The purchase is recorded before the run, so flour averages (100 x 42 + 60 x 45) / 160 = Rs 43.125/kg and 40 kg costs Rs 1,725. FIFO would give Rs 1,680 and last price Rs 1,800; cost per packet would be Rs 30.90 and Rs 31.54 respectively.
* **Material cost of the run** = flour 1,725 + oil 1,470 + spice mix 240 + pouches 485 (194 x 2.50, including the 6 damaged ones, because they were paid for) + cartons 135 = Rs 4,055. Add labour 1,200 and gas/power 600 = Rs 5,855, divided by 188 good packets = Rs 31.14.
* **Overhead rule.** Labour, gas and power entered against a run are charged wholly to that run. Overheads entered without a run (a monthly figure) are allocated to the month's runs by good packets produced (see the Cost per packet report). Overheads added after a run is complete also move the finished-goods valuation.
* **Opening finished goods (50 packets)** have no cost in the brief. I assumed Rs 30.00 per packet so stock valuation is not zero. It does not affect any benchmark number except total stock value.
* **GST on purchases** is recorded as input tax per line (default 0%) and kept out of stock cost.

## Assumptions

* Admin can do everything the other roles can, for demo convenience. Salesmen see only their own orders; production cannot see sales, invoices or money; accountant and owner are read-only. Rules are enforced in the API, not just hidden in the UI (`tests/` checks this).
* An invoice reduces nothing; **dispatch** reduces finished goods. Orders can be approved when stock is short, but cannot be invoiced.
* Order quantities are whole packets; a line can also be entered in cartons (20 packets each), converted at entry.
* Invoice date is the day it is created. Financial year runs 1 April to 31 March; numbers are `INV/2026-27/0001`.
* One GST rate per product (5% in the seed). Rounding: CGST and SGST are each rounded half-up to the paisa per line.
* Packaging is issued when the run is closed, because pouch and carton counts are only known then. Raw material is issued when the run starts.

## Known gaps (honest list)

* **Offline mode** is implemented (service worker caches the app, orders queue in `localStorage` with a client UUID so a retry can never duplicate). The server-side idempotency is tested; I did not test airplane-mode behaviour on a physical phone, and the sign-in itself needs the network the first time.
* Batch and expiry are recorded on each production run and printed in the ledger note, but stock is not tracked per lot, so dispatch is not first-expiry-first-out.
* No partial dispatch, sales returns other than full credit notes, work-in-progress stage, e-invoice/e-way bill, or Tally export.
* No user management or password-change screen (users are seeded). Sessions use a signed cookie; behind HTTPS set `SECRET_KEY`.
* SQLite is single-writer; fine for 5 to 15 salesmen, not for a large fleet.
* Fonts load from Google Fonts when online and fall back to system fonts otherwise.

## Project layout

```
app.py        Flask routes, role checks, exports          services.py   business rules (ledger, production, orders, invoices)
core.py       money, units, GST, number-to-words           reports.py    all reports + dashboard + CSV/XLSX/PDF
db.py         schema                                       pdfgen.py     GST invoice PDF
seed.py       section 5 master data                        benchmark.py  section 5 scenario via the API
static/       index.html, app.js, ops.js, style.css, sw.js, manifest.json     tests/   unit + integration tests
DESIGN.md     design note (data model, stock and BOM maths, costing)
```

## Deploying (Docker)

* **Hugging Face Spaces:** create a Space with SDK *Docker*, push this repo to it, and replace the Space's `README.md` with `huggingface/README.md` (it holds the `sdk: docker` / `app_port: 7860` header the Space needs).
* **Render / Railway / Fly:** point at the repo; the Dockerfile honours `$PORT`. Set `SECRET_KEY`.
* Free tiers have no permanent disk: the database reseeds with the benchmark data on restart. Mount a disk at `/home/user/app/data` to keep data.
