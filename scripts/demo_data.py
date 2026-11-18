"""Full demo data: fills every part of the system so it can be tested end to end.

    python scripts/demo_data.py              # fill an empty (freshly applied) database
    python scripts/demo_data.py --sales 900  # same, with more sales (default 380)

Start from a clean database, then run this:

    1. drop the database and apply schema.sql again (it seeds the 8 materials,
       the 4 suppliers, the 31 products and HQ + Balayan Branch);
    2. python scripts/demo_data.py

The script refuses to run when business records already exist, and when
APP_ENV=production. It never invents materials or products: everything comes from
schema.sql. Everything that moves stock goes through the same functions the app
itself uses (bulk batches, production runs, delivery receipts, Record Sale, void),
so costs, stock levels and ledger rows are exactly what the pages would have made;
only the dates are moved back in time.

What it creates (all back-dated over about 80 days):
  - Logins (admin + branch accounts, one inactive), two more branches.
  - Supplier details, the purchase history of every material (restocks at rising,
    falling and flat prices, with receipt numbers), unit costs and formulas, bulk rate.
  - Oil stocked per scent (its own price and stock, with a dated purchase), then bulk
    batches for most scents (15% / 14% / 13% formulas, 500 and 250 mL) that use it,
    bottled production runs, and three scents deliberately left with no oil or stock.
  - Deliveries HQ -> branches: Fulfilled (incl. one with 28 lines, one with damage and
    a missing unit finished by a second shipment), Partially Fulfilled, In Transit,
    Pending and Rejected, so every receipt and status shows up.
  - Sales at every branch: bottles, bulk by the mL, refills, freebies, cash and credit
    (some credit settled), named customers, voids, plus a handful of receipt stress
    cases (very long names / addresses) dated today.
  - Partners, their investments, packages, inquiries in every status (some fulfilled),
    AI stock drafts, login activity, the admin log and backup settings.

The login passwords are printed at the end (development only).
"""
import argparse
import decimal
import math
import os
import random
import sys
import uuid
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):      # the Windows console defaults to cp1252, which has no peso sign
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from werkzeug.security import generate_password_hash  # noqa: E402

from heaven_angel.app import create_app  # noqa: E402
from heaven_angel.db import TransactionAborted, get_db, query, transaction  # noqa: E402
from heaven_angel.routes.admin.batch_formulas import (  # noqa: E402
    formula_for_scent, load_catalog, load_formula_config, prepare_scent_oils,
)
from heaven_angel.routes.admin.production import (  # noqa: E402
    _BatchPlan, _ProductionRun, _create_bulk_batch_records, _record_production_run,
)
from heaven_angel.routes.branch.stock_requests import _confirm_shipment_receipt  # noqa: E402
from heaven_angel.services import sale_entry  # noqa: E402
from heaven_angel.services.sale_stock import void_sale  # noqa: E402
from heaven_angel.utils import bottle_size_ml, business_now, convert_material_qty  # noqa: E402

D = decimal.Decimal
rnd = random.Random(20261117)
HQ = 1
PASSWORD = "Demo@12345678"

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
NOW = None          # set in main(): the business "now"


def run(sql, params=()):
    cur = get_db().cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    get_db().commit()
    cur.close()
    return last


def ago(days, hour=None, minute=None):
    """A business time `days` ago; a random working hour unless one is given."""
    h = rnd.randint(9, 20) if hour is None else hour
    m = rnd.randint(0, 59) if minute is None else minute
    return (NOW - timedelta(days=days)).replace(hour=h, minute=m, second=rnd.randint(0, 59), microsecond=0)


def max_id(table, col):
    row = query(f"SELECT COALESCE(MAX({col}), 0) AS m FROM {table}", fetchone=True)
    return row["m"]


def retime_logs(after_log_id, when):
    run("UPDATE stock_movement_logs SET created_at = %s WHERE log_id > %s", (when, after_log_id))


ADMIN_ID = {}       # username -> user_id
ACTIONS = []        # (when, actor, action, target, details)


def audit(when, actor, action, target=None, details=None):
    ACTIONS.append((when, actor, action, target, details))


def section(title):
    print(f"\n== {title}")


# ---------------------------------------------------------------------------
# 1. branches and logins
# ---------------------------------------------------------------------------
def add_branches_and_users():
    section("Branches and logins")
    for name, loc in (("Lipa Branch", "Lipa City, Batangas"), ("Tanauan Branch", "Tanauan City, Batangas")):
        bid = run("INSERT INTO branches (branch_name, location) VALUES (%s, %s)", (name, loc))
        run("INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) SELECT %s, sku, 0 FROM products", (bid,))
        audit(ago(84), "admin", "add_branch", name, loc)
    run("UPDATE branches SET location = 'Poblacion, Balayan, Batangas' WHERE branch_id = 2")
    branches = {b["branch_name"]: b["branch_id"] for b in query("SELECT branch_id, branch_name FROM branches")}

    pw = generate_password_hash(PASSWORD)
    users = [
        ("admin", "Admin", None, True), ("maria.admin", "Admin", None, True),
        ("balayan", "Branch", branches["Balayan Branch"], True),
        ("lipa", "Branch", branches["Lipa Branch"], True),
        ("tanauan", "Branch", branches["Tanauan Branch"], True),
        ("balayan.ana", "Branch", branches["Balayan Branch"], True),
        ("former.staff", "Branch", branches["Lipa Branch"], False),
    ]
    for username, role, bid, active in users:
        uid = run(
            """INSERT INTO users (username, password_hash, role, branch_id, is_active, must_change_password, created_at)
               VALUES (%s, %s, %s, %s, %s, FALSE, %s)""",
            (username, pw, role, bid, active, ago(90, 9, 0)))
        ADMIN_ID[username] = uid
        audit(ago(88), "admin", "create_user", username, f"{role} account")
    audit(ago(40), "admin", "deactivate_user", "former.staff", "left the company")
    return branches


# ---------------------------------------------------------------------------
# 2. suppliers, settings, formulas
# ---------------------------------------------------------------------------
def add_suppliers_and_settings():
    section("Suppliers, unit costs and formulas")
    run("""UPDATE suppliers SET contact_person = 'Mang Rey Bautista', phone = '0917 555 0142',
           email = 'orders@bubblemix.example', notes = 'Oils and fixatives. Cash on delivery, 3 days lead time.'
           WHERE supplier_name LIKE 'BubbleMix%'""")
    run("""UPDATE suppliers SET contact_person = 'Ms. Liza Ocampo', phone = '0922 555 0188',
           email = 'sales@centralpack.example', notes = 'Bottles and alcohol. Free delivery from 5,000 pesos.'
           WHERE supplier_name = 'Centralpack'""")
    for name, person, phone, email, addr, notes in (
        ("LabelWorks Printing", "Jun Mendoza", "0933 555 0121", "jun@labelworks.example",
         "21 P. Tuazon Blvd., Cubao, Quezon City", "Sticker printing. Not used yet: quote only."),
        ("Pure Aqua Refilling Station", None, "0945 555 0107", None, "Poblacion, Balayan, Batangas", None),
    ):
        run("""INSERT INTO suppliers (supplier_name, contact_person, phone, email, address, notes)
               VALUES (%s, %s, %s, %s, %s, %s)""", (name, person, phone, email, addr, notes))

    run("UPDATE bulk_rate_settings SET rate_per_ml = 7.0000 WHERE id = 1")
    for unit, cost in (("85ML", 58.00), ("50ML", 36.00), ("10ML", 11.50), ("3ML", 4.25)):
        run("UPDATE unit_cogs_settings SET base_cost_per_unit = %s WHERE unit = %s", (cost, unit))

    mats = {m["material_name"]: m for m in query("SELECT material_id, material_name, cost_per_unit FROM raw_materials")}
    formulas = {
        "85ML": {"Oil Scent": 12.75, "Fixative": 0.5, "Perfume Alcohol": 60, "Moisturizer": 0.8,
                 "Frosted 85 ml Bottle": 1, "Sticker": 1, "Plastic Seal": 1},
        "50ML": {"Oil Scent": 7.5, "Perfume Alcohol": 35, "Sticker": 1, "Plastic Seal": 1},
        "10ML": {"Oil Scent": 1.5, "Perfume Alcohol": 7, "Sticker": 1},
        "3ML": {"Oil Scent": 0.45, "Perfume Alcohol": 2, "Sticker": 1},
    }
    for unit, lines in formulas.items():
        for mat, qty in lines.items():
            m = mats[mat]
            run("""INSERT INTO unit_formula_items (unit, material_id, qty_per_unit, line_cost)
                   VALUES (%s, %s, %s, %s)""", (unit, m["material_id"], qty, D(str(qty)) * m["cost_per_unit"]))
    audit(ago(86), "admin", "save_bulk_rate", "Bulk rate", "7.00 per mL")
    audit(ago(86), "admin", "save_unit_cogs", "Unit costs", "85ML 58.00, 50ML 36.00, 10ML 11.50, 3ML 4.25")


# ---------------------------------------------------------------------------
# 3. bulk batches: plan, buy the materials, make them
# ---------------------------------------------------------------------------
# material -> (stock left over as a share of what the batches need, price story)
# a price story is [(days ago, share of the total bought, price multiplier)]
STOCK_MARGIN = {"Oil Scent": 1.03, "Fixative": 1.25, "Moisturizer": 1.5, "Perfume Alcohol": 1.2,
                "Frosted 85 ml Bottle": 1.12, "Distilled Water": 1.3, "Sticker": 1.25, "Plastic Seal": 1.04}
PRICE_STORY = {
    "Oil Scent": [(200, 0.16, 1.00), (165, .08, 1.03), (130, .16, 1.08), (95, .08, 1.05), (60, .16, 1.15), (30, .08, 1.22), (8, .28, 1.18)],
    "Fixative": [(200, .15, 1.00), (150, .15, .97), (110, .15, .94), (75, .20, .90), (40, .15, .95), (12, .20, 1.00)],
    "Moisturizer": [(200, .12, 1.00), (160, .12, 1.00), (120, .12, 1.06), (85, .12, 1.06), (50, .30, .95), (20, .22, .92)],
    "Perfume Alcohol": [(200, .08, 1.00), (170, .08, 1.02), (140, .16, 1.10), (105, .08, 1.18), (70, .16, 1.12), (45, .12, 1.25), (15, .32, 1.30)],
    "Frosted 85 ml Bottle": [(200, .06, 1.00), (155, .12, .93), (120, .12, .87), (80, .24, .80), (40, .12, .85), (10, .34, .80)],
    "Distilled Water": [(200, .15, 1.00), (150, .15, 1.01), (110, .15, .99), (70, .15, 1.02), (30, .20, 1.00), (5, .20, 1.03)],
    "Sticker": [(200, .10, 1.00), (140, .20, 1.10), (100, .20, 1.10), (60, .30, .80), (25, .20, .90)],
    "Plastic Seal": [(200, .08, 1.00), (130, .16, 1.00), (90, .16, 1.20), (55, .16, 1.20), (20, .44, 1.40)],
}
RECEIPT_PREFIX = {"Oil Scent": "BM", "Fixative": "BM", "Moisturizer": "BM", "Perfume Alcohol": "CP",
                  "Frosted 85 ml Bottle": "CP", "Distilled Water": "", "Sticker": "", "Plastic Seal": ""}


def _materials():
    return query("SELECT material_id, material_name, unit, stock_qty, cost_per_unit FROM raw_materials")


def plan_batches(config):
    """Which scents get a bulk batch, at what size, and when."""
    products = query("SELECT sku, item_name, unit FROM products WHERE unit = %s ORDER BY item_name", (config["bottle_unit"],))
    scents = [{"sku": p["sku"], "name": p["item_name"], "code": p["sku"][:-(len(p["unit"]) + 1)]} for p in products]
    rnd.shuffle(scents)                         # which scents are popular is random but repeatable
    for i, s in enumerate(scents):
        s["rank"] = i
    big, small = scents[:26], scents[26:28]     # the last 3 are never made: zero stock
    plan = []
    for i, s in enumerate(big + small):
        size = 500 if s in big else 250
        plan.append({"scent": s, "formula": formula_for_scent(config, s["code"]), "size": size,
                     "code": f"HA-B{i + 1:03d}", "days": 82 - int(i * 24 / 28)})
    return scents, plan


def buy_materials(plan, catalog):
    section("Material purchases")
    mats = {m["material_name"]: m for m in _materials()}
    by_id = {m["material_id"]: m for m in mats.values()}
    need = {m["material_id"]: D(0) for m in mats.values()}
    for b in plan:
        calc = catalog[b["formula"]][b["size"]]
        for ln in calc["lines"]:
            need[ln["material_id"]] += convert_material_qty(ln["qty"], ln["qty_unit"], by_id[ln["material_id"]]["unit"])
        for p in calc["packaging"]:
            need[p["material_id"]] += D(p["per_bottle"]) * calc["bottles"]

    for name, m in mats.items():
        total = need[m["material_id"]] * D(str(STOCK_MARGIN[name]))
        step = 1 if m["unit"] == "Piece" else (5 if m["unit"] in ("Gram", "Milliliter") else 1)
        total = D(math.ceil(total / step) * step)
        story = PRICE_STORY[name]
        qtys = [D(math.floor(total * D(str(w)) / step) * step) for _d, w, _p in story]
        qtys[-1] += total - sum(qtys)
        base = m["cost_per_unit"]
        first_days, _w, first_mult = story[0]
        total_qty = qtys[0]
        total_cost = (qtys[0] * base * D(str(first_mult))).quantize(D("0.01"))
        for (days, _w2, mult), qty in zip(story[1:], qtys[1:]):
            cost = (qty * base * D(str(mult))).quantize(D("0.01"))
            sup = query("SELECT s.supplier_id, s.supplier_name FROM raw_materials rm LEFT JOIN suppliers s "
                        "ON s.supplier_id = rm.supplier_id WHERE rm.material_id = %s", (m["material_id"],), fetchone=True)
            run("""INSERT INTO material_restock_events
                       (material_id, material_name_snapshot, unit_snapshot, qty, cost, supplier_id,
                        supplier_name_snapshot, purchased_at, created_by_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (m["material_id"], name, m["unit"], qty, cost, sup["supplier_id"], sup["supplier_name"],
                 ago(days, 10), ADMIN_ID["admin"]))
            total_qty += qty
            total_cost += cost
            audit(ago(days, 10), "admin", "restock_material", name, f"{qty:g} {m['unit'].lower()} for {cost:,.2f}")
        prefix = RECEIPT_PREFIX[name]
        receipt = f"{prefix}-{rnd.randint(10000, 99999)}" if prefix else None
        run("""UPDATE raw_materials SET package_qty = %s, package_cost = %s, cost_per_unit = %s,
               stock_qty = %s, receipt_number = %s, created_at = %s WHERE material_id = %s""",
            (total_qty, total_cost, (total_cost / total_qty).quantize(D("0.0001")), total_qty, receipt,
             ago(first_days, 10), m["material_id"]))
        print(f"  {name:<22} {len(story)} purchases, bought {total_qty:>9g} {m['unit'].lower():<10} "
              f"blended ₱{total_cost / total_qty:,.4f}/{m['unit'].lower()}")


def stock_scent_oils(plan, config):
    """Each scent that gets a batch has its own oil: its own price and enough stock for the batch,
    bought a few days before it. The three scents with no batch stay at 0."""
    section("Oil per scent")
    prepare_scent_oils(_materials(), config)
    oil = next(m for m in _materials() if m["material_name"] == "Oil Scent")
    for b in plan:
        code = b["scent"]["code"]
        price = (oil["cost_per_unit"] * D(str(round(rnd.uniform(0.92, 1.18), 3)))).quantize(D("0.0001"))
        qty = D(math.ceil(D(b["size"]) * D("1.15") / 5) * 5)
        cost = (qty * price).quantize(D("0.01"))
        run("UPDATE scent_oils SET cost_per_unit = %s, stock_qty = %s WHERE base_code = %s", (price, qty, code))
        run("""INSERT INTO scent_oil_restocks (base_code, qty, cost, purchased_at, created_by_user_id)
               VALUES (%s, %s, %s, %s, %s)""", (code, qty, cost, ago(b["days"] + 6, 10).date(), ADMIN_ID["admin"]))
        audit(ago(b["days"] + 6, 10), "admin", "restock_scent_oil", b["scent"]["name"], f"+{qty:g} for {cost:,.2f}")
    print(f"  {len(plan)} scents stocked with their own oil")


def make_batches(plan, catalog, config, oil_material_id):
    section("Bulk batches")
    for b in plan:
        calc = catalog[b["formula"]][b["size"]]
        total_ml = calc["total_ml"].quantize(D("0.001"))
        scent = b["scent"]
        bp = _BatchPlan(
            scent["name"], b["code"], total_ml, "Milliliter", None, config["bottle_unit"], calc["bottles"], total_ml,
            [(ln["material_id"], ln["qty"], ln["qty_unit"]) for ln in calc["lines"]], calc["label"],
            [(p["material_id"], p["per_bottle"]) for p in calc["packaging"]], scent["code"],
            (scent["code"], oil_material_id))      # costed at this scent's own oil, taken from its own stock
        total_cost, _ = _create_bulk_batch_records(bp, ADMIN_ID["admin"])
        when = ago(b["days"], 9)
        run("UPDATE bulk_batches SET created_at = %s WHERE batch_code = %s", (when, b["code"]))
        b["batch_id"] = query("SELECT batch_id FROM bulk_batches WHERE batch_code = %s", (b["code"],), fetchone=True)["batch_id"]
        b["bottles"] = calc["bottles"]
        audit(when, "admin", "create_bulk_batch", b["code"], f"{scent['name']} {calc['label']}")
        print(f"  {b['code']}  {scent['name']:<34} {calc['label']:<24} {calc['bottles']:>3} bottles  ₱{total_cost:>10,.2f}")


def produce_bottles(plan):
    section("Production runs (bottling)")
    total = 0
    for b in plan:
        qty = int(b["bottles"] * (0.85 if b["size"] == 500 else 0.8))
        sku = b["scent"]["sku"]
        cogs = query("SELECT base_cost_per_unit FROM unit_cogs_settings WHERE unit = '85ML'", fetchone=True)["base_cost_per_unit"]
        run_ = _ProductionRun(sku, b["code"], qty, b["batch_id"], "85ML", bottle_size_ml("85ML"), cogs,
                              (cogs * qty).quantize(D("0.01")))
        before = max_id("stock_movement_logs", "log_id")
        _record_production_run(run_, ADMIN_ID["admin"])
        when = ago(b["days"] - 2, 11)
        run("UPDATE production_logs SET produced_at = %s WHERE batch_code = %s", (when, b["code"]))
        run("UPDATE cogs_logs SET created_at = %s WHERE production_log_id = (SELECT log_id FROM production_logs WHERE batch_code = %s)",
            (when, b["code"]))
        retime_logs(before, when)
        b["produced"] = qty
        total += qty
        audit(when, "admin", "log_production", b["scent"]["name"], f"{qty} bottles from {b['code']}")
    print(f"  {len(plan)} runs, {total} bottles now in the HQ warehouse")


# ---------------------------------------------------------------------------
# 4. deliveries HQ -> branches
# ---------------------------------------------------------------------------
def new_request(branch_id, lines, when):
    """A branch's delivery request: lines = [(sku, qty)]."""
    rid = run("INSERT INTO stock_requests (branch_id, delivery_number, requested_at) VALUES (%s, %s, %s)",
              (branch_id, uuid.uuid4().hex[:20], when))
    run("UPDATE stock_requests SET delivery_number = %s WHERE request_id = %s", (f"DR-{rid:06d}", rid))
    for sku, qty in lines:
        price = query("SELECT price FROM products WHERE sku = %s", (sku,), fetchone=True)["price"]
        run("INSERT INTO stock_request_items (request_id, sku, requested_qty, unit_price) VALUES (%s, %s, %s, %s)",
            (rid, sku, qty, price))
    return rid


def dispatch(request_id, qty_by_sku, when):
    """HQ ships (part of) a request; the same writes as Dispatch on the admin page."""
    req = query("SELECT delivery_number FROM stock_requests WHERE request_id = %s", (request_id,), fetchone=True)
    items = {i["sku"]: i for i in query("SELECT * FROM stock_request_items WHERE request_id = %s", (request_id,))}
    before = max_id("stock_movement_logs", "log_id")
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO stock_request_shipments (request_id, dispatched_by_user_id, dispatched_at) VALUES (%s, %s, %s)",
                    (request_id, ADMIN_ID["admin"], when))
        shipment_id = cur.lastrowid
        for sku, qty in qty_by_sku.items():
            if qty <= 0:
                continue
            cur.execute("SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE", (HQ, sku))
            have = cur.fetchone()["stock_qty"]
            if have < qty:
                raise TransactionAborted(f"HQ has only {have} of {sku}")
            cur.execute("UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s", (have - qty, HQ, sku))
            cur.execute("UPDATE stock_request_items SET dispatched_qty = COALESCE(dispatched_qty, 0) + %s WHERE item_id = %s",
                        (qty, items[sku]["item_id"]))
            cur.execute("INSERT INTO stock_request_shipment_items (shipment_id, request_item_id, dispatched_qty) VALUES (%s, %s, %s)",
                        (shipment_id, items[sku]["item_id"], qty))
            cur.execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_by_user_id,
                       reference_type, reference_id, before_qty, after_qty)
                   VALUES (%s, %s, %s, 'DISPATCH', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                (HQ, sku, -qty, f"Dispatched on {req['delivery_number']} shipment #{shipment_id}",
                 ADMIN_ID["admin"], request_id, have, have - qty))
        cur.execute("UPDATE stock_requests SET status = 'In Transit' WHERE request_id = %s", (request_id,))
        cur.close()
    retime_logs(before, when)
    run("UPDATE stock_requests SET updated_at = %s WHERE request_id = %s", (when, request_id))
    audit(when, "admin", "dispatch_request", req["delivery_number"], f"shipment #{shipment_id}")
    return shipment_id


def receive(shipment_id, branch_id, user, when, damaged=None, missing=None):
    """The branch confirms a shipment. damaged / missing = {sku: units}."""
    damaged, missing = damaged or {}, missing or {}
    rows = query("""SELECT ssi.shipment_item_id, ssi.dispatched_qty, sri.sku, sr.delivery_number, sr.request_id
                    FROM stock_request_shipment_items ssi
                    JOIN stock_request_items sri ON sri.item_id = ssi.request_item_id
                    JOIN stock_requests sr ON sr.request_id = sri.request_id
                    WHERE ssi.shipment_id = %s""", (shipment_id,))
    ids, got, dmg = [], {}, {}
    for r in rows:
        k = str(r["shipment_item_id"])
        ids.append(k)
        dmg[k] = min(damaged.get(r["sku"], 0), r["dispatched_qty"])
        got[k] = max(0, r["dispatched_qty"] - dmg[k] - missing.get(r["sku"], 0))
    before = max_id("stock_movement_logs", "log_id")
    number, shortfall = _confirm_shipment_receipt(branch_id, ADMIN_ID[user], shipment_id, ids, got, dmg)
    retime_logs(before, when)
    run("UPDATE stock_request_shipments SET received_at = %s WHERE shipment_id = %s", (when, shipment_id))
    run("UPDATE stock_requests SET updated_at = %s WHERE request_id = %s", (when, rows[0]["request_id"]))
    audit(when, user, "receive_shipment", number, f"shortfall {shortfall}" if shortfall else "all accounted for")
    return shortfall


def hq_stock():
    return {r["sku"]: r["stock_qty"] for r in query("SELECT sku, stock_qty FROM branch_inventory WHERE branch_id = %s", (HQ,))}


def deliveries(branches, plan):
    section("Deliveries HQ -> branches")
    produced = {b["scent"]["sku"]: b["produced"] for b in plan}
    # (branch, user, wave-1 share, wave-2 share, day of wave 1, day of wave 2)
    setups = [
        ("Balayan Branch", "balayan", 0.18, 0.13, 55, 24),
        ("Lipa Branch", "lipa", 0.15, 0.10, 52, 21),
        ("Tanauan Branch", "tanauan", 0.11, 0.08, 49, 18),
    ]
    for branch, user, s1, s2, d1, d2 in setups:
        bid = branches[branch]
        for wave, share, day in ((1, s1, d1), (2, s2, d2)):
            qty = {sku: max(1, int(p * share)) for sku, p in produced.items()}
            lines = sorted(qty.items())
            # every branch asks for exactly what it is sent, except one delivery below
            over = branch == "Tanauan Branch" and wave == 2      # this one asks for more than HQ can send: stays Partially Fulfilled
            requested = [(sku, q + (rnd.randint(1, 3) if over and rnd.random() < 0.25 else 0)) for sku, q in lines]
            when = ago(day, 10)
            rid = new_request(bid, requested, when)
            audit(when, user, "request_stock", f"DR-{rid:06d}", f"{len(requested)} products")
            ship = {sku: q for sku, q in lines}
            sid = dispatch(rid, ship, ago(day - 1, 14))
            if branch == "Balayan Branch" and wave == 1:
                # damage on two lines and one unit never arrived; a second shipment finishes it
                skus = [sku for sku, _ in lines]
                dmg = {skus[2]: 2, skus[9]: 1}
                miss = {skus[5]: 1}
                receive(sid, bid, user, ago(day - 2, 11), damaged=dmg, missing=miss)
                remaining = {}
                for it in query("SELECT sku, requested_qty, COALESCE(received_qty, 0) AS got FROM stock_request_items WHERE request_id = %s", (rid,)):
                    if it["requested_qty"] > it["got"]:
                        remaining[it["sku"]] = it["requested_qty"] - it["got"]
                sid2 = dispatch(rid, {k: min(v, hq_stock()[k]) for k, v in remaining.items()}, ago(day - 4, 15))
                receive(sid2, bid, user, ago(day - 5, 12))
            elif branch == "Lipa Branch" and wave == 2:
                receive(sid, bid, user, ago(day - 2, 11), damaged={lines[0][0]: 1}, missing={lines[4][0]: 2})   # stays Partially Fulfilled
            else:
                receive(sid, bid, user, ago(day - 2, 11))
            print(f"  {branch:<16} wave {wave}: {len(lines)} lines, {sum(q for _s, q in lines)} bottles")

    # one in transit, one pending, one rejected, so those statuses all exist
    stocks = hq_stock()
    sellable = sorted((s for s, q in stocks.items() if q >= 6), key=lambda s: -stocks[s])
    rid = new_request(branches["Lipa Branch"], [(s, 6) for s in sellable[:5]], ago(2, 9))
    dispatch(rid, {s: 6 for s in sellable[:5]}, ago(1, 15))
    audit(ago(2, 9), "lipa", "request_stock", f"DR-{rid:06d}", "5 products")
    rid = new_request(branches["Balayan Branch"], [(s, rnd.randint(3, 8)) for s in sellable[5:13]], ago(0, 8, 30))
    audit(ago(0, 8, 30), "balayan", "request_stock", f"DR-{rid:06d}", "8 products")
    rid = new_request(branches["Tanauan Branch"], [(s, 10) for s in sellable[:3]], ago(0, 9, 10))
    audit(ago(0, 9, 10), "tanauan", "request_stock", f"DR-{rid:06d}", "3 products")
    rid = new_request(branches["Tanauan Branch"], [(s, 40) for s in sellable[:4]], ago(10, 10))
    run("UPDATE stock_requests SET status = 'Rejected', updated_at = %s WHERE request_id = %s", (ago(9, 10), rid))
    audit(ago(9, 10), "admin", "reject_request", f"DR-{rid:06d}", "quantity too large for current stock")
    print("  + 1 In Transit, 2 Pending, 1 Rejected")


# ---------------------------------------------------------------------------
# 5. partners, packages, inquiries
# ---------------------------------------------------------------------------
PARTNERS = [
    ("Distributor", "Calabarzon Scents Trading", "Roberto Magsaysay", "0917 301 4455", "roberto@calabarzonscents.example", "Brgy. Sambat, Tanauan City, Batangas"),
    ("Distributor", "Southern Luzon Fragrance Distribution Corporation", "Atty. Imelda Fernandez-Aquino", "0918 220 9087", "imelda@slfdc.example", "Km 52 National Highway, Sto. Tomas, Batangas"),
    ("Distributor", "Bicol Aroma Wholesale", "Joey Alcantara", "0927 118 3342", None, "Magsaysay Ave., Naga City, Camarines Sur"),
    ("Reseller", "Ate Joy's Perfume Corner", "Joy Dela Cruz", "0915 889 2210", "joy.perfumes@example.com", "Public Market Stall 14, Lipa City"),
    ("Reseller", "Kuya Mark Online Scents", "Mark Villanueva", "0939 662 1175", "markscents@example.com", "Blk 4 Lot 9 Mabuhay Homes, Calamba, Laguna"),
    ("Reseller", "Fragrance Fix PH", "Camille Reyes", "0906 774 5521", "hello@fragrancefix.example", "Unit 3B, Ayala Malls Feliz, Pasig City"),
    ("Reseller", "Mercado Gift Shop", "Nena Mercado", "0928 445 7788", None, "Rizal Ave., Balayan, Batangas"),
    ("Reseller", "Tindahan ni Aling Baby", "Baby Lopez", "0916 530 4412", None, "Brgy. Gulod, Balayan, Batangas"),
]

PACKAGES = [  # name, description, scope, discount, active, n skus, qty each
    ("Starter Reseller Set", "Five best-selling scents, four bottles each. A first order for a new reseller.", "Reseller", 10, True, 5, 4),
    ("Distributor Case", "Eight scents, six bottles each, for stocking a shop.", "Distributor", 18, True, 8, 6),
    ("Gift Mix", "Six scents, two bottles each. Good for gift shops.", "Both", 8, True, 6, 2),
    ("Best Sellers Bundle", "Ten popular scents, three bottles each.", "Both", 12, True, 10, 3),
    ("Holiday Promo 2025 (retired)", "Last year's holiday bundle, no longer offered.", "Both", 5, False, 4, 5),
]


def partners_and_packages(plan):
    section("Partners, packages and inquiries")
    stocks = hq_stock()
    ranked = [b["scent"]["sku"] for b in sorted(plan, key=lambda b: b["scent"]["rank"])]
    by_stock = sorted(ranked, key=lambda s: -stocks.get(s, 0))
    prices = {p["sku"]: p for p in query("SELECT sku, item_name, unit, price FROM products")}

    partner_ids = []
    for i, (ptype, name, person, phone, email, addr) in enumerate(PARTNERS):
        pid = run("""INSERT INTO partners (partner_type, partner_name, contact_person, phone, email, address, notes, created_at)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                  (ptype, name, person, phone, email, addr,
                   "Came in through the partner portal." if i % 2 == 0 else "Referred by an existing reseller.", ago(70 - i * 6)))
        partner_ids.append(pid)
    invest = [(0, 50000, "Initial buy-in", 66), (0, 25000, "Top-up for the December stock", 30),
              (1, 120000, "First pallet order", 58), (2, 35000, "Initial buy-in", 40),
              (3, 8000, "Starter set", 44), (3, 8500, "Second starter set", 12), (4, 12000, "Online store opening stock", 25),
              (5, 15000, "Pop-up booth stock", 18), (6, -1500, "Correction: duplicate entry reversed", 9), (6, 6000, "Gift shop restock", 20)]
    for idx, amount, note, days in invest:
        run("INSERT INTO partner_investments (partner_id, amount, note, logged_by_user_id, created_at) VALUES (%s, %s, %s, %s, %s)",
            (partner_ids[idx], amount, note, ADMIN_ID["admin"], ago(days)))

    pkg_ids = []
    start = 0
    for name, desc, scope, disc, active, n, qty in PACKAGES:
        pid = run("INSERT INTO packages (package_name, description, partner_scope, discount_percent, is_active, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                  (name, desc, scope, disc, active, ago(60)))
        skus = by_stock[start:start + n] if len(pkg_ids) < 4 else ranked[-n:]
        for sku in skus:
            run("INSERT INTO package_items (package_id, sku, qty) VALUES (%s, %s, %s)", (pid, sku, qty))
        pkg_ids.append((pid, name, disc, [(s, qty) for s in skus], scope))
        audit(ago(60), "admin", "create_package", name, f"{disc}% off")
        start = (start + 2) % 10
    audit(ago(35), "admin", "deactivate_package", PACKAGES[4][0], "no longer offered")

    # (partner idx, package idx, status, days ago, fulfil?)
    inquiries = [
        (3, 0, "Closed", 52, True), (4, 0, "Closed", 33, True), (0, 1, "Closed", 47, False), (1, 3, "Closed", 28, True),
        (5, 2, "Closed", 14, False), (6, 2, "Contacted", 11, False), (2, 1, "Follow-up", 16, False),
        (7, 0, "On Hold", 21, False), (3, 3, "Follow-up", 6, False), (4, 2, "New", 3, False),
        (5, 0, "New", 1, False), (0, 3, "New", 0, False), (6, 0, "Declined", 38, False), (7, 2, "Declined", 26, False),
    ]
    fulfilled = 0
    for p_idx, k_idx, status, days, fulfil in inquiries:
        pid, pname, disc, items, scope = pkg_ids[k_idx]
        ptype, company, person, phone, email, addr = PARTNERS[p_idx]
        if scope not in ("Both", ptype):
            ptype = scope            # keep the partner type consistent with the package's audience
        reference = sum(prices[s]["price"] * q for s, q in items)
        amount = (reference * (D(100) - D(disc)) / D(100)).quantize(D("0.01"))
        when = ago(days, 14)
        pref = (None, "Call", "SMS", "Viber", "Email")[p_idx % 5]
        iid = run(
            """INSERT INTO partner_inquiries (package_id, partner_id, partner_type, company_name, contact_person, phone, email, address,
                   message, preferred_contact, remarks, package_name_snapshot, order_amount, status, email_sent, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (pid, partner_ids[p_idx], ptype, company, person, phone, email, addr,
             "Hi, interested in this package. Can you deliver to our area?" if p_idx % 2 == 0 else "Do you have a bigger set?",
             pref, "Called twice, will confirm by Friday." if status in ("Follow-up", "On Hold") else
             ("Declined: price too high for now." if status == "Declined" else None),
             pname, amount, status, bool(email), when))
        for s, q in items:
            run("INSERT INTO partner_inquiry_items (inquiry_id, sku, item_name, unit, qty, unit_price) VALUES (%s, %s, %s, %s, %s, %s)",
                (iid, s, prices[s]["item_name"], prices[s]["unit"], q, prices[s]["price"]))
        if status != "New":
            audit(ago(days - 1 if days else 0, 10), "admin", "update_inquiry_status", company, status)
        if fulfil:
            stocks = hq_stock()
            if all(stocks.get(s, 0) >= q for s, q in items):
                before = max_id("stock_movement_logs", "log_id")
                with transaction() as conn:
                    cur = conn.cursor(dictionary=True)
                    for s, q in items:
                        cur.execute("SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE", (HQ, s))
                        have = cur.fetchone()["stock_qty"]
                        cur.execute("UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s", (have - q, HQ, s))
                        cur.execute(
                            """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_by_user_id,
                                   reference_type, reference_id, before_qty, after_qty)
                               VALUES (%s, %s, %s, 'PACKAGE_ORDER', %s, %s, 'PARTNER_INQUIRY', %s, %s, %s)""",
                            (HQ, s, -q, f"Package order for {company}", ADMIN_ID["admin"], iid, have, have - q))
                    cur.close()
                when_f = ago(max(days - 3, 0), 16)
                retime_logs(before, when_f)
                run("UPDATE partner_inquiries SET fulfilled_at = %s, fulfilled_by_user_id = %s WHERE inquiry_id = %s",
                    (when_f, ADMIN_ID["admin"], iid))
                audit(when_f, "admin", "fulfill_inquiry", company, pname)
                fulfilled += 1
    # the partner list's rollup columns, as the portal keeps them
    run("""UPDATE partners p SET inquiry_count = (SELECT COUNT(*) FROM partner_inquiries i WHERE i.partner_id = p.partner_id),
           last_inquiry_at = (SELECT MAX(created_at) FROM partner_inquiries i WHERE i.partner_id = p.partner_id)""")
    print(f"  {len(PARTNERS)} partners, {len(invest)} investments, {len(PACKAGES)} packages, "
          f"{len(inquiries)} inquiries ({fulfilled} fulfilled)")


# ---------------------------------------------------------------------------
# 6. sales
# ---------------------------------------------------------------------------
CUSTOMERS = [
    ("Maria Santos", "Brgy. Poblacion, Balayan, Batangas"),
    ("Jose Reyes", "12 Mabini St., Lipa City, Batangas"),
    ("Ana Dela Cruz", "Blk 3 Lot 5 Sampaguita Homes, Tanauan City"),
    ("Carlo Mendoza", None),
    ("Liza Bautista", "Brgy. Gulod, Balayan, Batangas"),
    ("Rowena Garcia", "45 Rizal Ave., Batangas City"),
    ("Miguel Aquino", "Unit 8, Lakeview Condominium, Calamba, Laguna"),
    ("Grace Villanueva", "Brgy. Sambat, Tanauan City"),
    ("Noel Castillo", None),
    ("Jenny Ramos", "San Juan, Batangas"),
    ("Aling Nena's Sari-sari Store", "Brgy. Caloocan, Balayan, Batangas"),
    ("Dr. Patricia Lim", "Lim Dental Clinic, P. Burgos St., Lipa City"),
    ("Kuya Boy", None),
    ("Cecilia Torres", "88 Gen. Luna St., Nasugbu, Batangas"),
    ("Engr. Ramil Navarro", "Navarro Construction, Brgy. Darasa, Tanauan City"),
    ("Teresita Flores", "Purok 5, Brgy. Navotas, Balayan, Batangas"),
    ("Bea Salazar", "Rosario, Batangas"),
    ("Dennis Pascual", None),
    ("Mylene Cruz", "Blk 12 Lot 2 Villa Rosa, Lipa City"),
    ("Joel Domingo", "Calaca, Batangas"),
    ("Sheila Marquez", "Brgy. San Piro, Balayan, Batangas"),
    ("Paolo Espiritu", "Lemery, Batangas"),
    ("Hannah Gonzales", "Taal, Batangas"),
    ("Benjie Alvarez", None),
    ("Fe Concepcion", "Sto. Tomas, Batangas"),
    ("Rhea Mangubat", "Brgy. Wawa, Lian, Batangas"),
    ("Tomas Evangelista", "Alitagtag, Batangas"),
    ("Marites Soriano", "Brgy. Banay-banay, Lipa City"),
]
STAFF = ["Ate Joy (staff)", "Kuya Mark (staff)", "Ana (staff)", "Boss Rey"]
VOID_REASONS = ["Wrong scent picked", "Customer changed mind", "Entered twice by mistake",
                "Wrong quantity typed", "Customer returned it right away"]


def sales(branches, plan, total_sales):
    section(f"Sales (about {total_sales})")
    branch_users = {branches["Balayan Branch"]: "balayan", branches["Lipa Branch"]: "lipa",
                    branches["Tanauan Branch"]: "tanauan", HQ: "admin"}
    weights = {branches["Balayan Branch"]: 0.36, branches["Lipa Branch"]: 0.24,
               branches["Tanauan Branch"]: 0.15, HQ: 0.25}
    ranked_skus = [b["scent"]["sku"] for b in sorted(plan, key=lambda b: b["scent"]["rank"])]
    pop = [1 / (i + 4) for i in range(len(ranked_skus))]
    bulk_codes = [b["scent"]["code"] for b in plan]
    first_day = {b: d for b, d in ((branches["Balayan Branch"], 46), (branches["Lipa Branch"], 44),
                                   (branches["Tanauan Branch"], 41), (HQ, 54))}

    events = []
    for bid, share in weights.items():
        for _ in range(int(total_sales * share)):
            days = min(first_day[bid], int(rnd.triangular(0, first_day[bid] + 1, 8)))
            events.append((days, rnd.randint(9, 20), rnd.randint(0, 59), bid))
    events.sort(key=lambda e: (-e[0], e[1], e[2]))

    stats = {"orders": 0, "done": 0, "skipped": 0, "bottle": 0, "bulk": 0, "refill": 0, "freebie": 0, "credit": 0}
    credit_ids = []
    for days, hour, minute, bid in events:
        user = branch_users[bid]
        for _attempt in range(6):
            roll = rnd.random()
            if bid == HQ:
                kind = "bulk" if roll < 0.14 else "refill" if roll < 0.29 else "freebie" if roll < 0.36 else "sale"
            else:
                kind = "freebie" if roll < 0.06 else "sale"
            code_pick = rnd.choices(ranked_skus, weights=pop)[0]
            if kind == "bulk":
                sku, qty, price = f"{rnd.choice(bulk_codes)}-BULK", rnd.choice([30, 50, 60, 100, 100, 150, 200, 250, 300, 500]), D(rnd.choice(["7.00", "7.00", "7.50", "6.50"]))
            elif kind == "refill":
                sku, qty, price = code_pick, rnd.choice([1, 1, 1, 2, 3]), D(rnd.choice(["60", "60", "65", "55"]))
            elif kind == "freebie":
                sku, qty, price = code_pick, 1, D(0)
            else:
                sku = code_pick
                qty = rnd.choices([1, 2, 3, 4, 6, 10], weights=[60, 24, 9, 4, 2, 1])[0]
                price = D(rnd.choices(["85", "80", "75", "90", "100"], weights=[70, 12, 6, 8, 4])[0])
            credit = kind in ("sale", "refill") and rnd.random() < 0.15
            cust = rnd.choice(CUSTOMERS) if rnd.random() < 0.45 else (None, None)
            buyer = (cust[0] if cust[0] and rnd.random() < 0.6 else rnd.choice(STAFF)) if credit else None
            when = ago(days, hour, minute)
            form = sale_entry.SaleInput(sku, qty, price, {"sale": "Sale", "bulk": "Sale", "refill": "Refill", "freebie": "Freebie"}[kind],
                                        "Credit" if credit else "Cash", buyer, cust[0], cust[1], when)
            forms = [form]
            if kind == "sale" and rnd.random() < 0.14:        # a customer buying several scents at once
                for other in rnd.sample([k for k in ranked_skus if k != sku], rnd.randint(1, 5)):
                    forms.append(sale_entry.SaleInput(other, rnd.choice([1, 1, 2, 3]), price, "Sale", form.payment_method,
                                                      buyer, cust[0], cust[1], when))
            before = max_id("stock_movement_logs", "log_id")
            try:
                sale_id = sale_entry.record_sale_order(forms, branch_id=bid, user_id=ADMIN_ID[user])
            except TransactionAborted:
                continue
            run("UPDATE sales SET sold_at = %s, recorded_at = %s WHERE sale_id = %s OR order_id = %s",
                (when, when, sale_id, sale_id))
            retime_logs(before, when)
            stats["orders"] += len(forms) > 1
            stats["done"] += 1
            stats[kind if kind != "sale" else "bottle"] += 1
            if credit:
                stats["credit"] += 1
                credit_ids.append((sale_id, bid, when))
            break
        else:
            stats["skipped"] += 1

    # settle about half of the credit sales, a few days after
    settled = 0
    for sale_id, bid, when in credit_ids:
        if rnd.random() < 0.55:
            run("UPDATE sales SET credit_settled_at = %s, credit_settled_by = %s WHERE sale_id = %s",
                (min(when + timedelta(days=rnd.randint(2, 10)), NOW), branch_users[bid], sale_id))
            settled += 1
    print(f"  {stats['done']} recorded ({stats['bottle']} bottle sales, {stats['bulk']} bulk, {stats['refill']} refills, "
          f"{stats['freebie']} freebies), {stats['orders']} of them several scents on one receipt; {stats['credit']} on credit, {settled} settled; {stats['skipped']} skipped (out of stock)")
    return stats


def void_some(branches):
    section("Voided sales")
    rows = query("""SELECT s.sale_id, s.branch_id, s.sold_at FROM sales s JOIN products p ON p.sku = s.sku
                    WHERE s.sale_type = 'Sale' AND s.credit_settled_at IS NULL ORDER BY RAND(7) LIMIT 14""")
    names = {1: "admin", branches["Balayan Branch"]: "balayan", branches["Lipa Branch"]: "lipa", branches["Tanauan Branch"]: "tanauan"}
    for r in rows:
        who = names[r["branch_id"]]
        before = max_id("stock_movement_logs", "log_id")
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            void_sale(cur, sale_id=r["sale_id"], reason=rnd.choice(VOID_REASONS), user_id=ADMIN_ID[who], username=who)
            cur.close()
        when = r["sold_at"] + timedelta(hours=rnd.randint(1, 5))
        when = min(when, NOW)
        run("UPDATE sale_voids SET voided_at = %s WHERE sale_id = %s", (when, r["sale_id"]))
        retime_logs(before, when)
        audit(when, who, "void_sale", f"Sale #{r['sale_id']}", "voided")
    print(f"  {len(rows)} sales voided")


def receipt_stress_sales(branches):
    """Real sales dated today that make the receipt slip work hard: very long names and addresses."""
    section("Receipt stress sales (today)")
    long_name = "Maria Cristina Dela Cruz-Villanueva Ramos y Fernandez de Los Santos"
    long_addr = ("Unit 1204, Tower 2, The Residences at Greenbelt Heights Condominium Complex, "
                 "Corner of Ayala Avenue and Makati Avenue, Brgy. San Lorenzo, Makati City, Metro Manila, 1223, Philippines")
    cases = []
    balayan = branches["Balayan Branch"]
    stock = {r["sku"]: r["stock_qty"] for r in query("SELECT sku, stock_qty FROM branch_inventory WHERE branch_id = %s", (balayan,))}
    names = {p["sku"]: p["item_name"] for p in query("SELECT sku, item_name FROM products")}
    in_stock = {s: q for s, q in stock.items() if q >= 3}
    if in_stock:
        longest = max(in_stock, key=lambda s: len(names[s]))
        cases.append((balayan, "balayan", longest, min(12, in_stock[longest] - 2), D("85"), "Sale", "Cash", None, long_name, long_addr))
        cases.append((balayan, "balayan", longest, 1, D("85"), "Sale", "Credit", "Engr. Bartolome Evangelista-Magsaysay Jr.", "Walk-in", None))
        cases.append((balayan, "balayan", longest, 1, D(0), "Freebie", "Cash", None, "Dr. Patricia Lim", "Lim Dental Clinic, P. Burgos St., Lipa City"))
    hq_bulk = query("SELECT base_code FROM bulk_batches WHERE remaining_ml >= 2000 AND base_code IS NOT NULL ORDER BY remaining_ml DESC LIMIT 1", fetchone=True)
    if hq_bulk:
        cases.append((HQ, "admin", f"{hq_bulk['base_code']}-BULK", 1800, D("7.50"), "Sale", "Cash", None, long_name, long_addr))
    hq_left = [s for s, q in hq_stock().items() if q >= 1]
    if hq_left:
        hq_sku = max(hq_left, key=lambda s: len(names[s]))
        cases.append((HQ, "admin", hq_sku, 2, D("60"), "Refill", "Credit", "Boss Rey", "Kuya Boy", None))
    # Orders of several scents: 10 products (one full slip), 11 (a second slip with one line)
    # and 23 (three slips), the last with a long name and address.
    def stocked(loc, minimum=1):
        have = {r["sku"]: r["stock_qty"] for r in query(
            "SELECT sku, stock_qty FROM branch_inventory WHERE branch_id = %s", (loc,))}
        return [k for k, q in sorted(have.items(), key=lambda kv: -kv[1]) if q >= minimum]
    for loc, user, count, cname, caddr, pay, buyer in (
            (HQ, "admin", 10, "Dr. Patricia Lim", "Lim Dental Clinic, P. Burgos St., Lipa City", "Cash", None),
            (HQ, "admin", 11, "Aling Nena's Sari-sari Store", "Brgy. Caloocan, Balayan, Batangas", "Cash", None),
            (HQ, "admin", 23, long_name, long_addr, "Cash", None)):
        skus = stocked(loc)[:count]
        if len(skus) < 2:
            continue
        when = ago(0, rnd.randint(8, 9), rnd.randint(0, 59))
        forms = [sale_entry.SaleInput(k, 1, D("85"), "Sale", pay, buyer,
                                      None if pay == "Credit" else cname, None if pay == "Credit" else caddr, when) for k in skus]
        before = max_id("stock_movement_logs", "log_id")
        try:
            sid = sale_entry.record_sale_order(forms, branch_id=loc, user_id=ADMIN_ID[user])
        except TransactionAborted as err:
            print("  skipped a big order:", err)
            continue
        run("UPDATE sales SET sold_at = %s, recorded_at = %s WHERE sale_id = %s OR order_id = %s", (when, when, sid, sid))
        retime_logs(before, when)
        print(f"  SR-{sid:06d}  ORDER of {len(skus)} products ({-(-len(skus) // 10)} slip(s))")
    for bid, user, sku, qty, price, stype, pay, buyer, cname, caddr in cases:
        when = ago(0, rnd.randint(8, 9), rnd.randint(0, 59))
        cname_final = None if cname == "Walk-in" else cname
        form = sale_entry.SaleInput(sku, qty, price, stype, pay, buyer, cname_final, caddr, when)
        before = max_id("stock_movement_logs", "log_id")
        try:
            sale_entry.record_sale(form, branch_id=bid, user_id=ADMIN_ID[user])
        except TransactionAborted as err:
            print("  skipped a stress case:", err)
            continue
        sid = max_id("sales", "sale_id")
        run("UPDATE sales SET sold_at = %s, recorded_at = %s WHERE sale_id = %s", (when, when, sid))
        retime_logs(before, when)
        print(f"  SR-{sid:06d}  {stype:<8} {names.get(sku, sku)[:34]:<34} x{qty}")


# ---------------------------------------------------------------------------
# 7. AI drafts, login activity, admin log, settings
# ---------------------------------------------------------------------------
def ai_drafts(branches, plan):
    section("AI stock drafts, login activity, admin log, settings")
    skus = [b["scent"]["sku"] for b in plan]
    req_id = query("SELECT request_id FROM stock_requests WHERE branch_id = %s ORDER BY request_id LIMIT 1",
                   (branches["Balayan Branch"],), fetchone=True)["request_id"]
    drafts = [
        (branches["Balayan Branch"], "balayan", "Pending Review", "Restock the fast movers", "Sold 18 bottles of these in the last 7 days; stock is under the reorder level.", None, None),
        (branches["Lipa Branch"], "lipa", "Approved", "Weekend restock", "Weekend sales run about 40% above weekdays.", req_id, "admin"),
        (branches["Tanauan Branch"], "tanauan", "Rejected", "Big restock before payday", "HQ is low on these scents right now.", None, "admin"),
    ]
    for bid, user, status, note, reason, rid, reviewer in drafts:
        did = run("""INSERT INTO ai_stock_drafts (branch_id, created_by_user_id, created_by_username, status, note, reasoning,
                         resulting_request_id, reviewed_by_user_id, reviewed_at, created_at)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                  (bid, ADMIN_ID[user], user, status, note, reason, rid, ADMIN_ID[reviewer] if reviewer else None,
                   ago(3) if reviewer else None, ago(4)))
        for sku in rnd.sample(skus, 5):
            run("INSERT INTO ai_stock_draft_items (draft_id, sku, suggested_qty) VALUES (%s, %s, %s)", (did, sku, rnd.choice([6, 8, 10, 12])))

    agents = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36",
              "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1",
              "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"]
    rows = []
    for d in range(45, -1, -1):
        for user in ("admin", "balayan", "lipa", "tanauan"):
            if rnd.random() < 0.8:
                rows.append((ADMIN_ID[user], user, "Admin" if user == "admin" else "Branch", True, None, ago(d, rnd.randint(7, 9))))
    for _ in range(10):
        rows.append((None, rnd.choice(["admin", "balayan", "root", "test", "lipa"]), rnd.choice(["Admin", "Branch"]), False,
                     rnd.choice(["bad_credentials", "bad_credentials", "wrong_role_tab", "missing_fields"]), ago(rnd.randint(0, 30))))
    rows.append((ADMIN_ID["former.staff"], "former.staff", "Branch", False, "inactive_account", ago(30)))
    for _ in range(5):
        rows.append((None, "admin", "Admin", False, "bad_credentials", ago(2, 3, rnd.randint(0, 59))))
    rows.append((None, "admin", "Admin", False, "locked_out", ago(2, 3, 59)))
    for uid, uname, role, ok, reason, when in rows:
        run("""INSERT INTO login_activity (user_id, username_attempted, role_attempted, success, failure_reason, ip_address, user_agent, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (uid, uname, role, ok, reason, f"192.168.1.{rnd.randint(20, 90)}", rnd.choice(agents), when))

    for when, actor, action, target, details in sorted(ACTIONS, key=lambda a: a[0]):
        run("""INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (ADMIN_ID.get(actor), actor, action, target, details, when))
    for key, value in (("backup_days", "15"), ("backup_auto", "0"), ("backup_hour", "2")):
        run("INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
            (key, value))
    print(f"  3 drafts, {len(rows)} login attempts, {len(ACTIONS)} admin log rows, backup settings")


# ---------------------------------------------------------------------------
def summary():
    section("What is in the database now")
    tables = ["users", "branches", "suppliers", "raw_materials", "material_restock_events", "scent_oils",
              "scent_oil_restocks", "products", "bulk_batches",
              "production_logs", "cogs_logs", "stock_requests", "stock_request_shipments", "stock_movement_logs",
              "sales", "sale_voids", "partners", "partner_investments", "packages", "partner_inquiries",
              "ai_stock_drafts", "login_activity", "admin_actions"]
    for t in tables:
        print(f"  {t:<26}{query(f'SELECT COUNT(*) AS c FROM {t}', fetchone=True)['c']:>7}")
    print("\nLogins (all use the same password):")
    for u in query("SELECT username, role, is_active FROM users ORDER BY user_id"):
        print(f"  {u['username']:<14}{u['role']:<8}{'' if u['is_active'] else '(inactive)'}")
    print(f"  password: {PASSWORD}")


def main():
    global NOW
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sales", type=int, default=380, help="how many sales to attempt (default 380)")
    args = parser.parse_args()

    if os.environ.get("APP_ENV", "development").lower() == "production":
        sys.exit("Refusing to run: APP_ENV=production.")
    app = create_app()
    with app.app_context():
        if query("SELECT 1 FROM users LIMIT 1") or query("SELECT 1 FROM sales LIMIT 1") or query("SELECT 1 FROM bulk_batches LIMIT 1"):
            sys.exit("The database already has records. Drop it and apply schema.sql again first, then re-run this.")
        if not query("SELECT 1 FROM raw_materials WHERE material_name = 'Oil Scent'", fetchone=True):
            sys.exit("The seeded materials are missing. Apply schema.sql first.")
        NOW = business_now()
        try:
            branches = add_branches_and_users()
            add_suppliers_and_settings()
            config = load_formula_config()
            _scents, plan = plan_batches(config)
            resolved, catalog, missing = load_catalog(_materials(), config)
            if missing:
                sys.exit("Formula materials aren't set up: " + ", ".join(missing))
            buy_materials(plan, catalog)
            stock_scent_oils(plan, config)
            make_batches(plan, catalog, config, resolved["oil"]["material_id"])
            produce_bottles(plan)
            deliveries(branches, plan)
            partners_and_packages(plan)
            sales(branches, plan, args.sales)
            void_some(branches)
            receipt_stress_sales(branches)
            ai_drafts(branches, plan)
            summary()
        except Exception:
            get_db().rollback()
            raise


if __name__ == "__main__":
    main()
