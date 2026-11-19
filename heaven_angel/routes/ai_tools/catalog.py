
from heaven_angel.db import query
from heaven_angel.services.material_prices import load_material_price_history
from heaven_angel.utils import BOTTLE_UNITS, bottle_size_ml

from heaven_angel.routes.ai_tools._common import MAX_ROWS
from heaven_angel.routes.ai_tools.sales import PERIOD_NAMES, PERIOD_TMPL, period_sql


def get_raw_materials(args, ctx):
    name = (args.get("name") or "").strip()
    sql = (
        "SELECT rm.material_name, rm.unit, rm.stock_qty, rm.cost_per_unit, rm.purchase_mode, "
        "s.supplier_name FROM raw_materials rm "
        "LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id"
    )
    params = []
    if name:
        sql += " WHERE rm.material_name LIKE %s"
        params.append(f"%{name}%")
    sql += " ORDER BY rm.stock_qty ASC, rm.material_name LIMIT %s"
    params.append(MAX_ROWS)
    rows = query(sql, tuple(params))
    # What each purchase actually cost, so "cost_per_unit" (a blended average) is not mistaken for the latest price.
    history = {m["name"]: m for m in load_material_price_history(name or None)}
    results = []
    for r in rows:
        h = history.get(r["material_name"])
        results.append({
            "material": r["material_name"], "unit": r["unit"], "stock_qty": float(r["stock_qty"] or 0),
            "cost_per_unit": float(r["cost_per_unit"] or 0), "purchase_mode": r["purchase_mode"],
            "supplier": r["supplier_name"],
            "purchases": h["purchase_count"] if h else None,
            "latest_price_paid_per_unit": h["latest_price"] if h else None,
            "last_purchase": h["points"][-1]["date"] if h else None,
            "price_change_since_first_pct": None if not h or h["change_pct"] is None or h["purchase_count"] < 2 else round(h["change_pct"], 1),
        })
    oil = query("SELECT COUNT(*) AS scents, COALESCE(SUM(stock_qty), 0) AS stock FROM scent_oils", fetchone=True)
    out = {
        "results": results,
        "note": ("Sorted lowest stock first. cost_per_unit is the blended average across every purchase; "
                 "latest_price_paid_per_unit is what the most recent purchase cost. "
                 "Use get_material_price_history for the full price story. "
                 "Oil Scent is stocked and priced per scent: its stock_qty here is only the shared pool, used "
                 "for a batch of a scent that is not in the catalog. Each scent's own oil is in get_scent_oils."),
    }
    if oil and oil["scents"]:
        out["scent_oils"] = {"scents": oil["scents"], "total_stock": float(oil["stock"])}
    return out


def get_scent_oils(args, ctx):
    """The oil stocked for each scent (scent_oils): stock, price per unit and what was bought. A bulk batch
    of a catalog scent is costed at that scent's price and takes its oil from that scent's stock."""
    name = (args.get("name") or "").strip()
    only_in_stock = bool(args.get("only_in_stock"))
    where, params = "1=1", []
    if name:
        where += " AND (so.scent_name LIKE %s OR so.base_code LIKE %s)"
        params += [f"%{name}%", f"%{name}%"]
    if only_in_stock:
        where += " AND so.stock_qty > 0"
    rows = query(
        "SELECT so.base_code, so.scent_name, so.cost_per_unit, so.stock_qty, "
        "COALESCE(SUM(r.qty), 0) AS bought_qty, COALESCE(SUM(r.cost), 0) AS bought_cost, "
        "MAX(r.purchased_at) AS last_purchase "
        "FROM scent_oils so LEFT JOIN scent_oil_restocks r ON r.base_code = so.base_code "
        "WHERE " + where + " GROUP BY so.base_code, so.scent_name, so.cost_per_unit, so.stock_qty "
        "ORDER BY so.stock_qty ASC, so.scent_name LIMIT %s",
        tuple(params) + (MAX_ROWS,),
    )
    totals = query(
        "SELECT COUNT(*) AS scents, COALESCE(SUM(stock_qty), 0) AS stock, "
        "COALESCE(SUM(stock_qty * cost_per_unit), 0) AS value, COALESCE(SUM(stock_qty <= 0), 0) AS out_of_stock "
        "FROM scent_oils", fetchone=True)
    return {
        "results": [
            {"code": r["base_code"], "scent": r["scent_name"], "stock_qty": float(r["stock_qty"]),
             "price_per_unit": float(r["cost_per_unit"]),
             "stock_value": round(float(r["stock_qty"]) * float(r["cost_per_unit"]), 2),
             "bought_qty": float(r["bought_qty"]), "bought_cost": float(r["bought_cost"]),
             "last_purchase": r["last_purchase"].isoformat() if r["last_purchase"] else None}
            for r in rows
        ],
        "all_scents": {"scents": totals["scents"], "total_stock": float(totals["stock"]),
                       "stock_value": round(float(totals["value"]), 2), "out_of_stock": int(totals["out_of_stock"])},
        "note": ("Sorted lowest stock first. Oil is in mL. A scent at 0 stock cannot be used for a new bulk batch "
                 "until its oil is restocked (Materials page, Scent oils)."),
    }


def get_suppliers(args, ctx):
    name = (args.get("name") or "").strip()
    sql = (
        "SELECT s.supplier_name, s.contact_person, s.phone, s.email, "
        "COUNT(rm.material_id) AS material_count, "
        "GROUP_CONCAT(rm.material_name ORDER BY rm.material_name SEPARATOR ', ') AS materials "
        "FROM suppliers s LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id"
    )
    params = []
    if name:
        sql += " WHERE s.supplier_name LIKE %s"
        params.append(f"%{name}%")
    sql += " GROUP BY s.supplier_id ORDER BY s.supplier_name LIMIT %s"
    params.append(MAX_ROWS)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"supplier": r["supplier_name"], "contact_person": r["contact_person"], "phone": r["phone"],
             "email": r["email"], "material_count": r["material_count"], "materials": r["materials"]}
            for r in rows
        ]
    }


def get_formulas(args, ctx):
    """Cost-of-goods settings (the Formulas page): a flat base cost per
    bottle size, charged per bottle produced, plus the Bulk/Refill rate
    per mL. Same tables production() and the Formulas report read
    (unit_cogs_settings / bulk_rate_settings); the old per-material
    recipe table (unit_formula_items) is no longer used."""
    unit = (args.get("unit") or "").strip().upper()
    sql = "SELECT unit, base_cost_per_unit FROM unit_cogs_settings"
    params = []
    if unit:
        sql += " WHERE unit = %s"
        params.append(unit)
    rows = query(sql, tuple(params))
    by_unit = {r["unit"]: float(r["base_cost_per_unit"] or 0) for r in rows}
    rate = query("SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True)
    result = {
        "bottle_sizes": [
            {"unit": u, "base_cost_per_unit": by_unit.get(u, 0.0)}
            for u in BOTTLE_UNITS if not unit or u == unit
        ],
    }
    if not unit or unit == "BULK":
        result["bulk_rate_per_ml"] = float(rate["rate_per_ml"]) if rate else 0.0
    return result


def get_bulk_batches(args, ctx):
    from heaven_angel.routes.admin.batch_formulas import ensure_cost_columns
    ensure_cost_columns()
    active_only = args.get("active_only", True)
    sql = (
        "SELECT bb.batch_code, bb.base_code, bb.scent_name, bb.total_volume_ml, bb.remaining_ml, bb.cost_per_ml, "
        "bb.total_cost, bb.chemist_cost, bb.labor_cost, bb.created_at, bb.formula_label, bb.notes, "
        "EXISTS(SELECT 1 FROM bulk_batch_materials m WHERE m.batch_id = bb.batch_id AND m.is_packaging) AS has_packaging "
        "FROM bulk_batches bb"
    )
    if active_only:
        sql += " WHERE bb.remaining_ml > 0"
    sql += " ORDER BY bb.created_at DESC LIMIT %s"
    rows = query(sql, (MAX_ROWS,))
    results = []
    for r in rows:
        remaining = float(r["remaining_ml"] or 0)
        results.append({
            "batch_code": r["batch_code"], "scent_code": r["base_code"], "scent": r["scent_name"],
            "formula": r["formula_label"], "notes": r["notes"],
            # A batch with its packaging recorded had the bottles, stickers and seals taken out of stock when it was
            # made; for any other batch they come out of stock as bottles are produced from it.
            "packaging_recorded": bool(r["has_packaging"]),
            "total_volume_ml": float(r["total_volume_ml"] or 0), "remaining_ml": remaining,
            "cost_per_ml": float(r["cost_per_ml"] or 0), "total_cost": float(r["total_cost"] or 0),
            # A formula batch's total_cost includes these two flat charges (a set amount per bottle it yields).
            "chemist_cost": float(r["chemist_cost"] or 0), "labor_cost": float(r["labor_cost"] or 0),
            # Same yield figure the Bulk Batches page shows: how many full
            # bottles of each size the remaining mL could still fill.
            "bottles_possible": {u: int(remaining // float(bottle_size_ml(u))) for u in BOTTLE_UNITS},
            "created_at": r["created_at"].isoformat(),
        })
    return {
        "results": results,
        "note": ("total_cost = materials + chemist_cost + labor_cost; cost_per_ml is liquid only. "
                 "bottles_possible counts liquid only. Making bottles from a batch whose packaging is not recorded "
                 "(packaging_recorded false, e.g. an opening balance) also takes one bottle, sticker and seal per "
                 "bottle out of materials stock."),
    }


def get_production_costs(args, ctx):
    """COGS logged for bottles produced (cogs_logs). Bulk is not produced as a product any more (it is sold by the
    mL from bulk batches), so every row here is bottles. Opening-balance records, made when existing stock was loaded
    into the system, have no production run behind them and are totalled apart."""
    period = (args.get("period") or "this_month").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    where = period_sql(period, "c.created_at")
    totals = query(
        "SELECT COALESCE(SUM(c.qty_produced), 0) AS units, COALESCE(SUM(c.total_cogs), 0) AS cogs, "
        "COALESCE(SUM(CASE WHEN c.production_log_id IS NULL THEN c.total_cogs ELSE 0 END), 0) AS opening_cogs, "
        "COALESCE(SUM(CASE WHEN c.production_log_id IS NULL THEN c.qty_produced ELSE 0 END), 0) AS opening_units "
        "FROM cogs_logs c WHERE " + where, fetchone=True,
    )
    rows = query(
        "SELECT p.item_name, p.unit, SUM(c.qty_produced) AS qty, SUM(c.total_cogs) AS cogs "
        "FROM cogs_logs c JOIN products p ON c.sku = p.sku WHERE " + where +
        " GROUP BY p.sku, p.item_name, p.unit ORDER BY cogs DESC LIMIT %s",
        (MAX_ROWS,),
    )
    by_product = []
    for r in rows:
        qty = float(r["qty"] or 0)
        cogs = float(r["cogs"] or 0)
        by_product.append({"item_name": r["item_name"], "unit": r["unit"], "units": qty, "cogs": cogs,
                           "cogs_per_unit": round(cogs / qty, 2) if qty else None})
    return {
        "period": period,
        "units_produced": float(totals["units"]),
        "total_cogs": float(totals["cogs"]),
        "of_which_opening_balance": {"units": float(totals["opening_units"]), "cogs": float(totals["opening_cogs"])},
        "by_product": by_product,
        "note": "The cost of goods produced is shown as capital. Opening balance is stock that already existed when it was loaded.",
    }


def get_packages(args, ctx):
    include_inactive = bool(args.get("include_inactive"))
    sql = (
        "SELECT pkg.package_name, pkg.partner_scope, pkg.discount_percent, pkg.is_active, "
        "COUNT(pi.package_item_id) AS item_count, COALESCE(SUM(pi.qty * p.price), 0) AS reference_total "
        "FROM packages pkg LEFT JOIN package_items pi ON pi.package_id = pkg.package_id "
        "LEFT JOIN products p ON p.sku = pi.sku"
    )
    if not include_inactive:
        sql += " WHERE pkg.is_active = TRUE"
    sql += " GROUP BY pkg.package_id ORDER BY pkg.created_at DESC LIMIT %s"
    rows = query(sql, (MAX_ROWS,))
    results = []
    for r in rows:
        # Same math as finance.package_value().
        reference = float(r["reference_total"])
        discount = float(r["discount_percent"])
        results.append({
            "package": r["package_name"], "for": r["partner_scope"], "active": bool(r["is_active"]),
            "item_count": r["item_count"], "discount_percent": discount,
            "reference_total": reference, "discounted_total": round(reference * (1 - discount / 100), 2),
        })
    return {"results": results}


_INQUIRY_STATUSES = {"New", "Contacted", "Follow-up", "On Hold", "Closed", "Declined"}


def get_partner_inquiries(args, ctx):
    status = (args.get("status") or "").strip()
    if status and status not in _INQUIRY_STATUSES:
        return {"error": f"status must be one of {sorted(_INQUIRY_STATUSES)}."}
    counts = query("SELECT status, COUNT(*) AS n FROM partner_inquiries GROUP BY status")
    sql = (
        "SELECT company_name, contact_person, partner_type, package_name_snapshot, order_amount, "
        "status, fulfilled_at, created_at FROM partner_inquiries"
    )
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(15)
    rows = query(sql, tuple(params))
    return {
        "counts_by_status": {r["status"]: r["n"] for r in counts},
        "results": [
            {"company": r["company_name"], "contact_person": r["contact_person"],
             "partner_type": r["partner_type"], "package": r["package_name_snapshot"],
             "order_amount": float(r["order_amount"]) if r["order_amount"] is not None else None,
             "counts_as_sale": r["status"] == "Closed" and r["order_amount"] is not None,
             "status": r["status"],
             "fulfilled_at": r["fulfilled_at"].isoformat() if r["fulfilled_at"] else None, "received_at": r["created_at"].isoformat()}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_recent_sales  (both roles, branch-scoped)
# ---------------------------------------------------------------------------
