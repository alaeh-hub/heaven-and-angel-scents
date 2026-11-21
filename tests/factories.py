"""Tiny row-builders for tests: each inserts one row via the `sql` fixture.

Names are uuid-suffixed so tests never collide with each other or with the
seeded branches (1/2/3). Nothing is cleaned up: the whole test database is
dropped at session end, so tests must only look at their own rows.
"""
import re
import uuid

from werkzeug.security import generate_password_hash

_FORM_TOKEN_RE = re.compile(rb'name="form_token" value="([^"]*)"')


def get_form_token(client, endpoint):
    """GET `endpoint` and return the hidden form_token a single-use-token route renders.

    Needed before POSTing to such a route: the token is single-use and tied to the session.
    """
    resp = client.get(endpoint)
    match = _FORM_TOKEN_RE.search(resp.data)
    assert match, f"No form_token hidden field found on {endpoint}"
    return match.group(1).decode("utf-8")


def unique_suffix():
    return uuid.uuid4().hex[:8]


def make_branch(sql, name=None, is_hq=False):
    name = name or f"Test Branch {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        "INSERT INTO branches (branch_name, location, is_hq) VALUES (%s, %s, %s)",
        (name, "Nowhere", is_hq),
    )
    sql.commit()
    branch_id = cur.lastrowid
    cur.close()
    return branch_id


def make_product(sql, price="100.00", unit="50ML", variant="Male"):
    sku = f"TST-{unique_suffix().upper()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, unit, price)
           VALUES (%s, %s, %s, %s, %s)""",
        (sku, f"Test Product {sku}", variant, unit, price),
    )
    sql.commit()
    cur.close()
    return sku


def make_scent(sql, unit="50ML", variant="Male", price="50.00", name=None):
    """A bottled product with a proper '<code>-<unit>' SKU (make_product's TST-
    SKUs have no base code), so its scent can have bulk batches and bulk sales.
    Returns (sku, base_code)."""
    base = f"SC{unique_suffix().upper()}"
    sku = f"{base}-{unit}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, category, unit, price)
           VALUES (%s, %s, %s, 'Bottled', %s, %s)""",
        (sku, name or f"Scent {base}", variant, unit, price),
    )
    sql.commit()
    cur.close()
    return sku, base


def make_scent_batch(sql, base_code, remaining_ml, cost_per_ml="2.000000", created_at=None):
    """A bulk batch of a scent (bulk_batches.base_code) with remaining_ml left,
    inserted directly. created_at decides which batch is the OLDEST."""
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO bulk_batches
               (scent_name, base_code, input_qty, input_unit, total_volume_ml,
                total_cost, cost_per_ml, remaining_ml)
           VALUES (%s, %s, %s, 'Milliliter', %s, %s, %s, %s)""",
        (f"Batch {base_code} {unique_suffix()}", base_code, remaining_ml, remaining_ml,
         float(remaining_ml) * float(cost_per_ml), cost_per_ml, remaining_ml),
    )
    batch_id = cur.lastrowid
    if created_at:
        cur.execute("UPDATE bulk_batches SET created_at = %s WHERE batch_id = %s", (created_at, batch_id))
    sql.commit()
    cur.close()
    return batch_id


def get_batch_remaining(sql, batch_id):
    cur = sql.cursor()
    cur.execute("SELECT remaining_ml FROM bulk_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    cur.close()
    return float(row[0])


def make_bulk_sale(sql, branch_id, base_code, qty_ml, unit_price="1.00", customer_name=None):
    """A bulk sale row as record_sale() writes it: sold by the mL under the key
    '<code>-BULK' (not a product). Inserted directly, so no batch is touched."""
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, customer_name)
           VALUES (%s, %s, %s, %s, %s)""",
        (branch_id, f"{base_code}-BULK", qty_ml, unit_price, customer_name),
    )
    sql.commit()
    sale_id = cur.lastrowid
    cur.close()
    return sale_id


def make_partner(sql, partner_type="Distributor", name=None):
    name = name or f"Test Partner {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partners (partner_type, partner_name, contact_person, phone, email, address)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (partner_type, name, "Test Contact", "0917 000 0000", "test@example.com", "Test Address"),
    )
    sql.commit()
    partner_id = cur.lastrowid
    cur.close()
    return partner_id


def make_package(sql, name=None, discount_percent="10.00", partner_scope="Both"):
    name = name or f"Test Package {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO packages (package_name, description, partner_scope, discount_percent)
           VALUES (%s, %s, %s, %s)""",
        (name, "Test description", partner_scope, discount_percent),
    )
    sql.commit()
    package_id = cur.lastrowid
    cur.close()
    return package_id


def make_partner_inquiry(sql, partner_type="Distributor", company_name=None,
                         package_name_snapshot="Test Package", status="New"):
    company_name = company_name or f"Test Company {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partner_inquiries
               (partner_type, company_name, contact_person, phone, email,
                package_name_snapshot, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (partner_type, company_name, "Test Contact", "0917 000 0000",
         "test@example.com", package_name_snapshot, status),
    )
    sql.commit()
    inquiry_id = cur.lastrowid
    cur.close()
    return inquiry_id


def make_inventory(sql, branch_id, sku, stock_qty, reorder_level=10):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO branch_inventory (branch_id, sku, stock_qty, reorder_level)
           VALUES (%s, %s, %s, %s)""",
        (branch_id, sku, stock_qty, reorder_level),
    )
    sql.commit()
    cur.close()


def get_inventory_qty(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row["stock_qty"] if row else None


def make_user(sql, role, branch_id=None, password="Test-Passw0rd!",
              is_active=True, must_change_password=False):
    username = f"test_{role.lower()}_{unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO users (username, password_hash, role, branch_id, is_active, must_change_password)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (username, generate_password_hash(password), role, branch_id,
         is_active, must_change_password),
    )
    sql.commit()
    user_id = cur.lastrowid
    cur.close()
    return {"user_id": user_id, "username": username, "password": password, "role": role}


def make_raw_material(sql, unit="Gram", package_qty="100.000", package_cost="100.00"):
    """Insert a raw material like the Materials page: cost_per_unit from cost / qty, stock = package_qty.

    Only a bulk batch (see make_bulk_batch()/create_bulk_batch()) deducts stock_qty.
    """
    name = f"Test Material {unique_suffix()}"
    cost_per_unit = float(package_cost) / float(package_qty)
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO raw_materials
               (material_name, unit, purchase_mode, package_qty, package_cost, cost_per_unit, stock_qty)
           VALUES (%s, %s, 'Package', %s, %s, %s, %s)""",
        (name, unit, package_qty, package_cost, cost_per_unit, package_qty),
    )
    sql.commit()
    material_id = cur.lastrowid
    cur.close()
    return material_id


def get_raw_material(sql, material_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM raw_materials WHERE material_id = %s", (material_id,))
    row = cur.fetchone()
    cur.close()
    return row


PRODUCTION_URL = "/admin/production"


def log_production(client, sku, qty_produced, batch_code=None, bulk_batch_id=None,
                    rate_per_ml=None, **kwargs):
    """POST to the production form. Bottled needs bulk_batch_id; Bulk/Refill needs rate_per_ml."""
    token = get_form_token(client, PRODUCTION_URL)
    data = {"form_token": token, "sku": sku, "qty_produced": str(qty_produced)}
    if batch_code:
        data["batch_code"] = batch_code
    if bulk_batch_id is not None:
        data["bulk_batch_id"] = str(bulk_batch_id)
    if rate_per_ml is not None:
        data["rate_per_ml"] = str(rate_per_ml)
    return client.post(PRODUCTION_URL, data=data, **kwargs)


def make_bulk_batch(sql, items, input_qty="1000.000", input_unit="Milliliter", scent_name=None):
    """Insert a bulk batch directly, matching create_bulk_batch()'s cost math.

    `items` is a list of (material_id, qty_used, cost_per_unit). Unlike the real
    route it does NOT deduct raw_materials.stock_qty.
    """
    ml_per_unit = {"Milliliter": 1, "Liter": 1000, "Gallon": 3200}
    total_volume_ml = float(input_qty) * ml_per_unit[input_unit]
    total_cost = sum(float(qty) * float(cost) for _, qty, cost in items)
    cost_per_ml = total_cost / total_volume_ml if total_volume_ml else 0

    cur = sql.cursor()
    cur.execute(
        """INSERT INTO bulk_batches
               (scent_name, input_qty, input_unit, total_volume_ml, total_cost, cost_per_ml, remaining_ml)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (scent_name or f"Test Batch {unique_suffix()}", input_qty, input_unit,
         total_volume_ml, total_cost, cost_per_ml, total_volume_ml),
    )
    batch_id = cur.lastrowid
    for material_id, qty_used, cost_per_unit in items:
        # qty_used_unit (migration 39): tests pass amounts in the purchase unit.
        cur.execute("SELECT unit FROM raw_materials WHERE material_id = %s", (material_id,))
        qty_used_unit = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO bulk_batch_materials
                   (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit_snapshot, line_cost)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit,
             float(qty_used) * float(cost_per_unit)),
        )
    sql.commit()
    cur.close()
    return batch_id


def set_unit_cogs(sql, unit, base_cost_per_unit):
    """Set a Bottled size's flat base cost directly (what production() logs as cogs_per_unit)."""
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO unit_cogs_settings (unit, base_cost_per_unit) VALUES (%s, %s)
           ON DUPLICATE KEY UPDATE base_cost_per_unit = VALUES(base_cost_per_unit)""",
        (unit, base_cost_per_unit),
    )
    sql.commit()
    cur.close()


def get_bulk_batch(sql, batch_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM bulk_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    cur.close()
    return row


def get_cogs_logs(sql, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM cogs_logs WHERE sku = %s ORDER BY cogs_log_id", (sku,))
    rows = cur.fetchall()
    cur.close()
    return rows


def make_stock_request(sql, branch_id, items, status="Pending"):
    """Insert a delivery header and items directly, in any state a test needs.

    Each `items` dict needs sku/requested_qty; unit_price, dispatched_qty,
    received_qty and damaged_qty default like a fresh Pending line. An In Transit
    request with dispatched lines also gets a shipment fixture. Returns request_id.
    """
    cur = sql.cursor()
    cur.execute(
        "INSERT INTO stock_requests (branch_id, delivery_number, status) VALUES (%s, %s, %s)",
        (branch_id, f"DR-TEST-{unique_suffix()}", status),
    )
    request_id = cur.lastrowid
    shipment_lines = []
    for item in items:
        cur.execute(
            """INSERT INTO stock_request_items
               (request_id, sku, requested_qty, unit_price, dispatched_qty, received_qty, damaged_qty)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (request_id, item["sku"], item["requested_qty"],
             item.get("unit_price", "0.00"), item.get("dispatched_qty"),
             item.get("received_qty"), item.get("damaged_qty", 0)),
        )
        dispatched_qty = int(item.get("dispatched_qty") or 0)
        if dispatched_qty > 0:
            shipment_lines.append((cur.lastrowid, dispatched_qty))
    if status == "In Transit" and shipment_lines:
        cur.execute(
            "INSERT INTO stock_request_shipments (request_id, status) VALUES (%s, 'In Transit')",
            (request_id,),
        )
        shipment_id = cur.lastrowid
        cur.executemany(
            """INSERT INTO stock_request_shipment_items
                   (shipment_id, request_item_id, dispatched_qty)
               VALUES (%s, %s, %s)""",
            [(shipment_id, item_id, qty) for item_id, qty in shipment_lines],
        )
    sql.commit()
    cur.close()
    return request_id


def get_request_status(sql, request_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT status FROM stock_requests WHERE request_id = %s", (request_id,))
    row = cur.fetchone()
    cur.close()
    return row["status"] if row else None


def get_request_item(sql, request_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM stock_request_items WHERE request_id = %s AND sku = %s",
        (request_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row


def get_in_transit_shipment_items(sql, request_id):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        """SELECT ss.shipment_id, ssi.shipment_item_id, ssi.dispatched_qty,
                  sri.item_id, sri.sku
           FROM stock_request_shipments ss
           JOIN stock_request_shipment_items ssi ON ssi.shipment_id = ss.shipment_id
           JOIN stock_request_items sri ON sri.item_id = ssi.request_item_id
           WHERE ss.request_id = %s AND ss.status = 'In Transit'
           ORDER BY ssi.shipment_item_id""",
        (request_id,),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def count_sales(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT COUNT(*) AS c FROM sales WHERE branch_id = %s AND sku = %s",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row["c"]


def last_movement_log(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        """SELECT * FROM stock_movement_logs
           WHERE branch_id = %s AND sku = %s
           ORDER BY log_id DESC LIMIT 1""",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row


def set_account_active(sql, user_id, is_active):
    cur = sql.cursor()
    cur.execute("UPDATE users SET is_active = %s WHERE user_id = %s",
                (is_active, user_id))
    sql.commit()
    cur.close()


def login(client, username, password, login_type):
    """POST to /login as this test module's own client, following no
    redirects — callers assert on the redirect itself (302 + Location)
    or chain a follow-up request."""
    return client.post(
        "/login",
        data={"username": username, "password": password, "login_type": login_type},
    )
