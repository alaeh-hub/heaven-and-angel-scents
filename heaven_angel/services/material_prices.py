"""Material purchase and price history: what each purchase cost per unit, and how the
blended cost per unit moved. Shared by the Materials page's price trends chart,
the Material Price Changes report and the AI assistant, so all three agree."""
from heaven_angel.db import query


def build_material_price_history(materials, events):
    """Per-material purchase history with price paid and running blended cost.

    raw_materials keeps lifetime package_qty/package_cost, and every restock
    also has its own dated event row. The first purchase has no event, so it
    is whatever is left of the lifetime totals after subtracting the events
    (dated at the material's created_at). Each purchase's price is
    cost / qty; `blended` is the cumulative cost / cumulative qty after that
    purchase, i.e. the same weighted average restock_material() stores.
    """
    events_by_material = {}
    for ev in events:
        events_by_material.setdefault(ev["material_id"], []).append(ev)

    result = []
    for mat in materials:
        evs = events_by_material.get(mat["material_id"], [])
        purchases = []
        base_qty = float(mat["package_qty"]) - sum(float(e["qty"]) for e in evs)
        base_cost = float(mat["package_cost"]) - sum(float(e["cost"]) for e in evs)
        if base_qty > 0:
            purchases.append({
                "at": mat["created_at"], "qty": base_qty, "cost": max(base_cost, 0.0),
                "supplier": mat["supplier_name"], "kind": "First purchase",
            })
        for e in evs:
            purchases.append({
                "at": e["purchased_at"], "qty": float(e["qty"]), "cost": float(e["cost"]),
                "supplier": e["supplier_name_snapshot"], "kind": "Restock",
            })
        purchases.sort(key=lambda p: p["at"])

        cum_qty = cum_cost = 0.0
        points = []
        for p in purchases:
            cum_qty += p["qty"]
            cum_cost += p["cost"]
            points.append({
                "date": p["at"].strftime("%Y-%m-%d"),
                "qty": round(p["qty"], 3),
                "cost": round(p["cost"], 2),
                "price": round(p["cost"] / p["qty"], 4),
                "blended": round(cum_cost / cum_qty, 4),
                "supplier": p["supplier"],
                "kind": p["kind"],
            })
        if not points:
            continue

        prices = [pt["price"] for pt in points]
        first, latest = prices[0], prices[-1]
        previous = prices[-2] if len(prices) > 1 else None
        result.append({
            "material_id": mat["material_id"],
            "name": mat["material_name"],
            "unit": mat["unit"].lower(),
            "current_cost_per_unit": float(mat["cost_per_unit"]),
            "points": points,
            "purchase_count": len(points),
            "first_price": first,
            "latest_price": latest,
            "previous_price": previous,
            "lowest_price": min(prices),
            "highest_price": max(prices),
            "change_pct": ((latest - first) / first * 100) if first else None,
            "last_change_pct": ((latest - previous) / previous * 100) if previous else None,
        })
    return result


def load_material_price_history(name=None):
    """build_material_price_history() over the live tables, optionally for the
    materials whose name contains `name`."""
    sql = (
        """SELECT rm.material_id, rm.material_name, rm.unit, rm.package_qty,
                  rm.package_cost, rm.cost_per_unit, rm.created_at, s.supplier_name
           FROM raw_materials rm
           LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id"""
    )
    params = []
    if name:
        sql += " WHERE rm.material_name LIKE %s"
        params.append(f"%{name}%")
    sql += " ORDER BY rm.material_name"
    materials = query(sql, tuple(params))
    events = query(
        """SELECT material_id, qty, cost, purchased_at, supplier_name_snapshot
           FROM material_restock_events ORDER BY purchased_at, restock_id"""
    )
    return build_material_price_history(materials, events)
