"""Making bottles from a bulk batch takes the packaging (bottle, sticker, seal)
out of stock, unless the batch already had its packaging deducted."""
from decimal import Decimal

from factories import (get_batch_remaining, get_inventory_qty, get_raw_material, log_production,
                       login, make_bulk_batch, make_product, make_raw_material, make_user, set_unit_cogs)

HQ = 1


def _admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def _rows(sql, query, params=()):
    cur = sql.cursor(dictionary=True)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _packaging_materials(sql, stock):
    """The materials the formulas use as bottle / sticker / seal, set to `stock`."""
    rows = _rows(sql, "SELECT role, material_id FROM batch_formula_materials "
                      "WHERE role IN ('bottle', 'sticker', 'seal')")
    cur = sql.cursor()
    for r in rows:
        cur.execute("UPDATE raw_materials SET stock_qty = %s WHERE material_id = %s", (stock, r["material_id"]))
    sql.commit()
    cur.close()
    return [r["material_id"] for r in rows]


def test_a_run_from_a_batch_without_packaging_deducts_it(client, sql):
    _admin(client, sql)
    packaging = _packaging_materials(sql, 10)
    assert len(packaging) == 3
    sku = make_product(sql, unit="85ML")
    set_unit_cogs(sql, "85ML", "95.00")
    batch_id = make_bulk_batch(sql, [(make_raw_material(sql), "1000", "0.82")], input_qty="1000")

    log_production(client, sku, 3, bulk_batch_id=batch_id)

    assert get_inventory_qty(sql, HQ, sku) == 3
    assert get_batch_remaining(sql, batch_id) == Decimal("1000") - Decimal("85") * 3
    for material_id in packaging:
        assert get_raw_material(sql, material_id)["stock_qty"] == Decimal("7")
    assert _rows(sql, "SELECT total_cogs FROM cogs_logs WHERE sku = %s", (sku,))[0]["total_cogs"] == Decimal("285.00")


def test_a_run_is_refused_when_packaging_is_short(client, sql):
    _admin(client, sql)
    packaging = _packaging_materials(sql, 2)
    sku = make_product(sql, unit="85ML")
    batch_id = make_bulk_batch(sql, [(make_raw_material(sql), "1000", "0.82")], input_qty="1000")

    resp = log_production(client, sku, 3, bulk_batch_id=batch_id, follow_redirects=True)

    assert b"Not enough" in resp.data
    assert get_inventory_qty(sql, HQ, sku) in (None, 0)
    assert get_batch_remaining(sql, batch_id) == Decimal("1000")
    for material_id in packaging:
        assert get_raw_material(sql, material_id)["stock_qty"] == Decimal("2")


def test_a_batch_that_already_had_its_packaging_is_not_deducted_twice(client, sql):
    _admin(client, sql)
    packaging = _packaging_materials(sql, 10)
    sku = make_product(sql, unit="85ML")
    batch_id = make_bulk_batch(sql, [(make_raw_material(sql), "1000", "0.82")], input_qty="1000")
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO bulk_batch_materials
               (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit_snapshot, line_cost, is_packaging)
           VALUES (%s, %s, 5, 'Piece', 15, 75, TRUE)""", (batch_id, packaging[0]))
    sql.commit()
    cur.close()

    log_production(client, sku, 3, bulk_batch_id=batch_id)

    assert get_inventory_qty(sql, HQ, sku) == 3
    for material_id in packaging:
        assert get_raw_material(sql, material_id)["stock_qty"] == Decimal("10")


def test_a_batch_of_another_scent_or_size_is_refused(client, sql):
    _admin(client, sql)
    packaging = _packaging_materials(sql, 10)
    sku = make_product(sql, unit="85ML")
    set_unit_cogs(sql, "85ML", "95.00")
    batch_id = make_bulk_batch(sql, [(make_raw_material(sql), "1000", "0.82")], input_qty="1000")
    cur = sql.cursor()
    cur.execute("UPDATE bulk_batches SET base_code = 'ZZ9' WHERE batch_id = %s", (batch_id,))
    sql.commit()

    resp = log_production(client, sku, 3, bulk_batch_id=batch_id, follow_redirects=True)
    assert b"bottled as" in resp.data

    cur.execute("UPDATE bulk_batches SET base_code = NULL, target_unit = '50ML' WHERE batch_id = %s", (batch_id,))
    sql.commit()
    cur.close()
    resp = log_production(client, sku, 3, bulk_batch_id=batch_id, follow_redirects=True)
    assert b"was made for 50ML bottles" in resp.data

    assert get_inventory_qty(sql, HQ, sku) in (None, 0)
    assert get_batch_remaining(sql, batch_id) == Decimal("1000")
    for material_id in packaging:
        assert get_raw_material(sql, material_id)["stock_qty"] == Decimal("10")

