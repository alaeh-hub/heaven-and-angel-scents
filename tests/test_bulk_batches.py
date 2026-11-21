"""Bulk batches: mixing raw materials into mL inventory, then bottling from it.

Bottling draws down a batch's remaining mL, but the cost logged is the flat
base price per size (unit_cogs_settings), not the batch's own cost/mL.
Also covers restock_material(): real raw-material stock, deducted by a batch.
"""
import json
import re
from decimal import Decimal

from factories import (get_bulk_batch, get_cogs_logs, get_form_token,
                        get_inventory_qty, get_raw_material, log_production,
                        login, make_bulk_batch, make_product,
                        make_raw_material, make_user, set_unit_cogs)


BULK_BATCHES_URL = "/admin/bulk-batches"
CREATE_BULK_BATCH_URL = "/admin/bulk-batches/create"
RESTOCK_URL = "/admin/materials/restock"
HQ_BRANCH_ID = 1


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _bulk_batches_by_scent(sql, scent_name):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM bulk_batches WHERE scent_name = %s ORDER BY batch_id", (scent_name,))
    rows = cur.fetchall()
    cur.close()
    return rows


def test_bulk_batches_page_renders(client, sql):
    """Bulk batches page renders."""
    _signed_in_admin(client, sql)
    resp = client.get(BULK_BATCHES_URL)
    assert resp.status_code == 200

    mat_a = make_raw_material(sql)
    make_bulk_batch(sql, [(mat_a, "10", "1.00")], scent_name="Renders Fine")
    resp = client.get(BULK_BATCHES_URL)
    assert resp.status_code == 200
    assert b"Renders Fine" in resp.data


def test_create_bulk_batch_deducts_stock_and_computes_cost(client, sql):
    """Create bulk batch deducts stock and computes cost."""
    _signed_in_admin(client, sql)
    # ₱2.00/gram, 100g in stock
    mat_a = make_raw_material(
        sql, unit="Gram", package_qty="100.000", package_cost="200.00")
    # ₱1.00/mL, 500mL in stock
    mat_b = make_raw_material(
        sql, unit="Milliliter", package_qty="500.000", package_cost="500.00")

    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(CREATE_BULK_BATCH_URL, data={
        "form_token": token,
        "scent_name": "Rose Garden",
        "input_qty": "1",
        "input_unit": "Liter",
        "material_id[]": [str(mat_a), str(mat_b)],
        "qty_used[]": ["10", "20"],
        "qty_unit[]": ["Gram", "Milliliter"],
    })
    assert resp.status_code == 302

    batches = _bulk_batches_by_scent(sql, "Rose Garden")
    assert len(batches) == 1
    batch = batches[0]
    # 10g x ₱2.00 + 20mL x ₱1.00 = ₱40.00, over a 1 Liter (1000 mL) batch
    assert Decimal(batch["total_cost"]) == Decimal("40.00")
    assert Decimal(batch["total_volume_ml"]) == Decimal("1000.000")
    assert Decimal(batch["remaining_ml"]) == Decimal("1000.000")
    assert Decimal(batch["cost_per_ml"]) == Decimal("40.00") / Decimal("1000.000")

    # Stock is genuinely deducted by how much each material was used.
    mat_a_row = get_raw_material(sql, mat_a)
    mat_b_row = get_raw_material(sql, mat_b)
    assert Decimal(mat_a_row["stock_qty"]) == Decimal("90.000")
    assert Decimal(mat_b_row["stock_qty"]) == Decimal("480.000")


def test_create_bulk_batch_supports_material_logged_in_a_different_unit(client, sql):
    """A material bought by the Gallon but used in mL per batch.

    qty_unit[] lets each line say its unit; before, '0.31' meant 0.31 gallons
    and wildly overcharged the batch.
    """
    _signed_in_admin(client, sql)
    # cost_per_unit = 3200 pesos/gallon == exactly 1.00 peso/mL (a gallon is 3,200 mL)
    mat_a = make_raw_material(
        sql, unit="Gallon", package_qty="1.000", package_cost="3200")

    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(CREATE_BULK_BATCH_URL, data={
        "form_token": token,
        "scent_name": "Tiny Dose",
        "input_qty": "1",
        "input_unit": "Liter",
        "material_id[]": [str(mat_a)],
        "qty_used[]": ["0.31"],
        "qty_unit[]": ["Milliliter"],
    })
    assert resp.status_code == 302

    batches = _bulk_batches_by_scent(sql, "Tiny Dose")
    assert len(batches) == 1
    # 0.31 mL x ~1.00 peso/mL = ~0.31 pesos (not 0.31 gallons x 3200 = 992).
    assert Decimal(batches[0]["total_cost"]) == Decimal("0.31")

    # Stock is deducted in the material's own unit (gallons), converted.
    mat_a_row = get_raw_material(sql, mat_a)
    expected_gallons_used = Decimal("0.31") / Decimal("3200")
    expected_stock = (Decimal("1.000000") -
                       expected_gallons_used).quantize(Decimal("0.000001"))
    assert Decimal(mat_a_row["stock_qty"]) == expected_stock


def test_create_bulk_batch_rejects_insufficient_stock(client, sql):
    """Create bulk batch rejects insufficient stock."""
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(
        sql, unit="Gram", package_qty="5.000", package_cost="50.00")

    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(CREATE_BULK_BATCH_URL, data={
        "form_token": token,
        "scent_name": "Not Enough Stock",
        "input_qty": "1",
        "input_unit": "Liter",
        "material_id[]": [str(mat_a)],
        "qty_used[]": ["60"],  # only 5g in stock
        "qty_unit[]": ["Gram"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"not enough" in resp.data.lower()

    # Nothing was created, and stock is untouched.
    assert _bulk_batches_by_scent(sql, "Not Enough Stock") == []
    mat_a_row = get_raw_material(sql, mat_a)
    assert Decimal(mat_a_row["stock_qty"]) == Decimal("5.000")


def test_restock_material_adds_stock_and_averages_cost(client, sql):
    """Restock material adds stock and averages cost."""
    _signed_in_admin(client, sql)
    # 100g for ₱100 -> ₱1.00/gram, 100g in stock
    mat_a = make_raw_material(
        sql, unit="Gram", package_qty="100.000", package_cost="100.00")

    resp = client.post(RESTOCK_URL, data={
        "material_id": str(mat_a),
        "qty": "100",
        "cost": "300.00",  # this purchase alone: ₱3.00/gram
    })
    assert resp.status_code == 302

    mat_a_row = get_raw_material(sql, mat_a)
    assert Decimal(mat_a_row["stock_qty"]) == Decimal("200.000")
    # Weighted average across both purchases: (100 + 300) / (100 + 100) = ₱2.00/gram
    assert Decimal(mat_a_row["cost_per_unit"]) == Decimal("2.0000")
    assert Decimal(mat_a_row["package_qty"]) == Decimal("200.000")
    assert Decimal(mat_a_row["package_cost"]) == Decimal("400.00")


def test_production_run_against_batch_deducts_remaining_ml_and_computes_cogs(client, sql):
    """cogs_per_unit comes from the flat base price set on the Formulas
    page (see unit_cogs_settings) — the batch only decides how much mL
    gets drawn down and deducted, not the cost logged."""
    _signed_in_admin(client, sql)
    sku = make_product(sql, unit="85ML")
    set_unit_cogs(sql, "85ML", "8.50")
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "1000", "0.10")],
                                input_qty="1000", input_unit="Milliliter")

    resp = log_production(client, sku, 4, bulk_batch_id=batch_id)
    assert resp.status_code == 302

    logs = get_cogs_logs(sql, sku)
    assert len(logs) == 1
    log = logs[0]
    assert Decimal(log["cogs_per_unit"]) == Decimal("8.5000")
    assert Decimal(log["total_cogs"]) == Decimal("34.00")
    assert log["bulk_batch_id"] == batch_id

    batch = get_bulk_batch(sql, batch_id)
    # 4 x 85 mL = 340 mL drawn from 1000 mL; cost_per_ml is informational only.
    assert Decimal(batch["remaining_ml"]) == Decimal("660.000")
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 4


def test_production_shares_base_cost_across_products_of_the_same_unit(client, sql):
    """Every 85ML product gets the same cost per unit regardless of
    scent or which batch it's filled from — the cost lives on the
    packaging size (unit_cogs_settings), not the batch or the product."""
    _signed_in_admin(client, sql)
    set_unit_cogs(sql, "85ML", "17.00")
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "1000", "0.20")],
                                input_qty="1000", input_unit="Milliliter")

    sku_1 = make_product(sql, unit="85ML")
    sku_2 = make_product(sql, unit="85ML")

    log_production(client, sku_1, 1, bulk_batch_id=batch_id)
    log_production(client, sku_2, 1, bulk_batch_id=batch_id)

    logs_1 = get_cogs_logs(sql, sku_1)
    logs_2 = get_cogs_logs(sql, sku_2)
    assert Decimal(logs_1[0]["cogs_per_unit"]) == Decimal("17.0000")
    assert Decimal(logs_2[0]["cogs_per_unit"]) == Decimal("17.0000")

    batch = get_bulk_batch(sql, batch_id)
    assert Decimal(batch["remaining_ml"]) == Decimal("830.000")


def test_production_run_requires_a_bulk_batch_for_bottled_sizes(client, sql):
    """Unlike Bulk/Refill (its own rate per mL), a Bottled size has
    nothing to fall back on — no batch, no run. This is what actually
    enforces materials going into a batch before they're bottled."""
    _signed_in_admin(client, sql)
    sku = make_product(sql, unit="85ML")

    resp = log_production(client, sku, 1, follow_redirects=True)
    assert resp.status_code == 200
    assert get_cogs_logs(sql, sku) == []
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) in (None, 0)


def test_production_run_rejects_batch_with_insufficient_remaining_ml(client, sql):
    """Production run rejects batch with insufficient remaining ml."""
    _signed_in_admin(client, sql)
    sku = make_product(sql, unit="85ML")
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "50", "1.00")],
                                input_qty="50", input_unit="Milliliter")

    resp = log_production(client, sku, 1, bulk_batch_id=batch_id, follow_redirects=True)
    assert resp.status_code == 200
    assert b"only has" in resp.data.lower()
    assert get_cogs_logs(sql, sku) == []

    batch = get_bulk_batch(sql, batch_id)
    assert Decimal(batch["remaining_ml"]) == Decimal("50.000")


def test_dashboard_capital_is_cogs_not_raw_material_purchases(client, sql):
    """Assert on deltas only: fleet-wide sums include rows from other tests sharing the DB."""
    _signed_in_admin(client, sql)
    before = client.get("/admin/api/reports-data").get_json()["financials"]

    sku = make_product(sql, unit="10ML")
    mat_a = make_raw_material(
        sql, package_qty="100.000", package_cost="1000.00")

    # Buying material moves "raw materials bought", not capital; a production run does.
    after_purchase = client.get(
        "/admin/api/reports-data").get_json()["financials"]
    assert Decimal(str(after_purchase["capital"])) == Decimal(
        str(before["capital"]))
    assert Decimal(str(after_purchase["raw_materials_purchased"])) - Decimal(
        str(before["raw_materials_purchased"])) == Decimal("1000.00")

    # Flat base cost of 10.00 for a 10ML bottle (unit_cogs_settings), not the batch cost/mL.
    set_unit_cogs(sql, "10ML", "10.00")
    batch_id = make_bulk_batch(sql, [(mat_a, "100", "1.00")],
                                input_qty="100", input_unit="Milliliter")
    resp = log_production(client, sku, 3, bulk_batch_id=batch_id)
    assert resp.status_code == 302

    after_batch = client.get("/admin/api/reports-data").get_json()["financials"]
    assert Decimal(str(after_batch["capital"])) - Decimal(
        str(before["capital"])) == Decimal("30.00")
    # Raw materials bought is unchanged by a production run.
    assert Decimal(str(after_batch["raw_materials_purchased"])) == Decimal(
        str(after_purchase["raw_materials_purchased"]))


def test_bulk_batch_yields_show_a_liquid_only_reference_cost(client, sql):
    """Yield cost per bottle is the batch's liquid cost/mL x the size's mL, reference only.

    production() still charges the flat base cost; packaging is not included here.
    """
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(sql)
    # ₱0.10/mL over 1000 mL -> cost_per_ml = 0.10
    make_bulk_batch(sql, [(mat_a, "1000", "0.10")],
                    input_qty="1000", input_unit="Milliliter",
                    scent_name="Yield Reference")

    resp = client.get(BULK_BATCHES_URL)
    assert resp.status_code == 200

    tag_match = re.search(
        rb'<button[^>]*data-scent-name="Yield Reference"[^>]*>', resp.data)
    assert tag_match, "No Details button rendered for this batch"
    yields_match = re.search(rb"data-yields='([^']*)'", tag_match.group(0))
    assert yields_match, "No data-yields attribute rendered on the batch's Details button"
    yields = json.loads(yields_match.group(1))

    expected = {"85ML": "8.50", "50ML": "5.00", "10ML": "1.00", "3ML": "0.30"}
    for y in yields:
        assert Decimal(str(y["total_cost_per_bottle"])) == Decimal(expected[y["unit"]])
        assert Decimal(str(y["packaging_cost"])) == Decimal("0")


# ---------------------------------------------------------------- write-off of leftover mL
def _leave_ml(sql, batch_id, remaining_ml):
    cur = sql.cursor()
    cur.execute("UPDATE bulk_batches SET remaining_ml = %s WHERE batch_id = %s",
                (remaining_ml, batch_id))
    sql.commit()
    cur.close()


def _write_off_url(batch_id):
    return f"/admin/bulk-batches/{batch_id}/write-off"


def test_write_off_zeroes_remaining_but_keeps_cost_and_logs_it(client, sql):
    """Write off zeroes remaining but keeps cost and logs it."""
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "100", "0.50")], scent_name="Leftover Scent",
                               input_qty="1000", input_unit="Milliliter")
    _leave_ml(sql, batch_id, "50.000")
    before = get_bulk_batch(sql, batch_id)

    resp = client.post(_write_off_url(batch_id),
                       data={"reason": "Too little to use", "note": "end of the jar"})
    assert resp.status_code == 302

    after = get_bulk_batch(sql, batch_id)
    assert Decimal(after["remaining_ml"]) == Decimal("0")
    # Cost and size are untouched; nothing is written to cost-of-goods.
    assert after["total_cost"] == before["total_cost"]
    assert after["cost_per_ml"] == before["cost_per_ml"]
    assert after["total_volume_ml"] == before["total_volume_ml"]

    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT details FROM admin_actions WHERE action = 'write_off_bulk_batch' "
                "AND target = 'Leftover Scent'")
    rows = cur.fetchall()
    cur.close()
    assert len(rows) == 1
    assert "50" in rows[0]["details"] and "Too little to use" in rows[0]["details"]
    assert "end of the jar" in rows[0]["details"]


def test_write_off_rejects_unknown_reason_and_leaves_batch_alone(client, sql):
    """Write off rejects unknown reason and leaves batch alone."""
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "100", "0.50")], input_qty="1000",
                               input_unit="Milliliter")
    _leave_ml(sql, batch_id, "50.000")

    client.post(_write_off_url(batch_id), data={"reason": "because"})
    assert Decimal(get_bulk_batch(sql, batch_id)["remaining_ml"]) == Decimal("50.000")
    client.post(_write_off_url(batch_id), data={})
    assert Decimal(get_bulk_batch(sql, batch_id)["remaining_ml"]) == Decimal("50.000")


def test_write_off_of_an_empty_or_missing_batch_is_refused(client, sql):
    """Write off of an empty or missing batch is refused."""
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(sql)
    batch_id = make_bulk_batch(sql, [(mat_a, "100", "0.50")], scent_name="Already Empty",
                               input_qty="1000", input_unit="Milliliter")
    _leave_ml(sql, batch_id, "0")

    assert client.post(_write_off_url(batch_id), data={"reason": "Other"}).status_code == 302
    assert client.post(_write_off_url(batch_id + 9999), data={"reason": "Other"}).status_code == 302

    cur = sql.cursor()
    cur.execute("SELECT COUNT(*) FROM admin_actions WHERE action = 'write_off_bulk_batch' "
                "AND target = 'Already Empty'")
    assert cur.fetchone()[0] == 0
    cur.close()


def test_bulk_batches_page_offers_write_off_form(client, sql):
    """Bulk batches page offers write off form."""
    _signed_in_admin(client, sql)
    resp = client.get(BULK_BATCHES_URL)
    assert resp.status_code == 200
    assert b"bdWriteOffForm" in resp.data
