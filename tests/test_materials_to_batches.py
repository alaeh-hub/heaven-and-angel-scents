"""End to end: log a supplier and a material, restock it, see the totals on the
Materials page, then produce a formula batch that reads that material's stock
and (averaged) price."""
from decimal import Decimal

from factories import get_form_token, get_raw_material, login, make_user
from test_batch_formulas import _make_role_materials, _rows, _save_materials

BULK_BATCHES_URL = "/admin/bulk-batches"


def test_supplier_material_restock_totals_and_batch_use_them(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    # 1. log a supplier
    resp = client.post("/admin/materials/suppliers/add", data={
        "supplier_name": "Flow Test Supplier", "address": "Somewhere, Quezon City"})
    assert resp.status_code == 302
    supplier_id = _rows(sql, "SELECT supplier_id FROM suppliers WHERE supplier_name = 'Flow Test Supplier'")[0]["supplier_id"]

    # 2. log a material bought from them: 500 ml for PHP 2,000
    client.post("/admin/materials", data={
        "material_name": "Flow Test Oil", "unit": "Milliliter", "purchase_mode": "Package",
        "package_qty": "500", "package_cost": "2000", "supplier_id": str(supplier_id)})
    oil = _rows(sql, "SELECT * FROM raw_materials WHERE material_name = 'Flow Test Oil'")[0]
    assert (oil["stock_qty"], oil["package_cost"], oil["cost_per_unit"]) == (
        Decimal("500"), Decimal("2000.00"), Decimal("4.0000"))
    assert oil["supplier_id"] == supplier_id

    # 3. buy 500 ml more for PHP 2,200: stock adds, totals roll up, price averages
    client.post("/admin/materials/restock", data={
        "material_id": str(oil["material_id"]), "qty": "500", "cost": "2200"})
    oil = get_raw_material(sql, oil["material_id"])
    assert oil["stock_qty"] == Decimal("1000")
    assert (oil["package_qty"], oil["package_cost"]) == (Decimal("1000"), Decimal("4200.00"))
    assert oil["cost_per_unit"] == Decimal("4.2000")

    # 4. the totals show on the Materials page (per material and per supplier)
    page = client.get("/admin/materials").get_data(as_text=True)
    assert "Flow Test Oil" in page and "4,200.00" in page

    # 5. a batch reads that stock and that price
    m = _make_role_materials(sql)
    m["oil"] = oil["material_id"]
    _save_materials(client, m)
    token = get_form_token(client, BULK_BATCHES_URL)
    client.post("/admin/bulk-batches/produce", data={
        "form_token": token, "formula": "15%", "size": "500", "scent_name": "Flow Batch"})
    batch = _rows(sql, "SELECT * FROM bulk_batches WHERE scent_name = 'Flow Batch'")[0]
    line = _rows(sql, "SELECT * FROM bulk_batch_materials WHERE batch_id = %s AND material_id = %s",
                 (batch["batch_id"], oil["material_id"]))[0]
    assert line["qty_used"] == Decimal("500")
    assert line["cost_per_unit_snapshot"] == Decimal("4.2000")      # the averaged price, not 4.00
    assert line["line_cost"] == Decimal("2100.0000")
    assert get_raw_material(sql, oil["material_id"])["stock_qty"] == Decimal("500")   # 1000 - 500
    assert batch["total_cost"] > Decimal("2100")                    # oil + the other materials + packaging
