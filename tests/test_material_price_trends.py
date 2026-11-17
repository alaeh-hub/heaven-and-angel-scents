from datetime import datetime

from heaven_angel.routes.admin.materials import build_material_price_history


def _material(**over):
    base = {
        "material_id": 1, "material_name": "Alcohol", "unit": "Milliliter",
        "package_qty": 200, "package_cost": 130, "cost_per_unit": 0.65,
        "created_at": datetime(2026, 1, 1), "supplier_name": "Acme",
    }
    base.update(over)
    return base


def test_first_purchase_and_restock_blend():
    # 100 for 50 first (0.50/unit), then 100 for 80 restock (0.80/unit).
    events = [{
        "material_id": 1, "qty": 100, "cost": 80,
        "purchased_at": datetime(2026, 2, 1), "supplier_name_snapshot": "Acme",
    }]
    [m] = build_material_price_history([_material()], events)
    assert [p["price"] for p in m["points"]] == [0.5, 0.8]
    assert [p["blended"] for p in m["points"]] == [0.5, 0.65]
    assert m["points"][0]["kind"] == "First purchase"
    assert m["latest_price"] == 0.8
    assert round(m["change_pct"], 1) == 60.0
    assert m["lowest_price"] == 0.5 and m["highest_price"] == 0.8


def test_material_without_restocks_has_single_point():
    [m] = build_material_price_history(
        [_material(package_qty=100, package_cost=50)], [])
    assert len(m["points"]) == 1
    assert m["change_pct"] == 0
    assert m["previous_price"] is None and m["last_change_pct"] is None
