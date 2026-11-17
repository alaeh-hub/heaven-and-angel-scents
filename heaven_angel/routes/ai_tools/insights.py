"""AI tools for the newer parts of the system: how material prices moved, voided sales,
the inventory (stock movement) log, and pointing people at the right report."""
from flask import url_for

from heaven_angel.db import query
from heaven_angel.services.material_prices import load_material_price_history
from heaven_angel.services.reports import REPORT_TYPES, report_catalog
from heaven_angel.services.reports.constants import MOVEMENT_TYPE_CHOICES

from heaven_angel.routes.ai_tools._common import MAX_ROWS, _branch_filter
from heaven_angel.routes.ai_tools.sales import PERIOD_NAMES, PERIOD_TMPL, period_sql


# ---------------------------------------------------------------------------
# Tool: get_material_price_history  (admin only)
# ---------------------------------------------------------------------------


def _price_summary(m, recent=6):
    """One material's price story, in the units the Materials page shows."""
    return {
        "material": m["name"],
        "unit": m["unit"],
        "purchases": m["purchase_count"],
        "first_price_per_unit": m["first_price"],
        "latest_price_per_unit": m["latest_price"],
        "previous_price_per_unit": m["previous_price"],
        "lowest_price_per_unit": m["lowest_price"],
        "highest_price_per_unit": m["highest_price"],
        "change_since_first_pct": None if m["change_pct"] is None else round(m["change_pct"], 1),
        "change_since_previous_pct": None if m["last_change_pct"] is None else round(m["last_change_pct"], 1),
        "blended_cost_per_unit_now": round(m["current_cost_per_unit"], 4),
        "recent_purchases": [
            {"date": p["date"], "kind": p["kind"], "qty": p["qty"], "cost": p["cost"],
             "price_per_unit": p["price"], "blended_after": p["blended"], "supplier": p["supplier"]}
            for p in m["points"][-recent:]
        ],
    }


def get_material_price_history(args, ctx):
    """How each raw material's price per unit has moved across purchases, and the
    blended cost per unit after each one (the Materials page's price trends)."""
    name = (args.get("name") or "").strip()
    history = load_material_price_history(name or None)
    if not history:
        return {"results": [], "note": f"No materials matching '{name}'." if name else "No materials with purchases yet."}
    # Biggest movers first, so "what got more expensive" reads straight off the top.
    history.sort(key=lambda m: -(abs(m["change_pct"]) if m["change_pct"] is not None and m["purchase_count"] > 1 else -1))
    return {
        "results": [_price_summary(m) for m in history[:MAX_ROWS]],
        "note": ("price_per_unit is what one purchase cost per unit (cost / quantity); blended cost is the running "
                 "average across every purchase so far. A positive change_pct means the material got more expensive. "
                 "Sorted biggest price movement first."),
    }


# ---------------------------------------------------------------------------
# Tool: get_voided_sales  (both roles, branch-scoped)
# ---------------------------------------------------------------------------


def get_voided_sales(args, ctx):
    """Sales that were voided (removed, stock returned): how many, how much they were
    worth, and the reasons. Same data as the Voided Sales report."""
    period = (args.get("period") or "all_time").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "sv.branch_id")
    if err:
        return {"error": err}
    base = (
        "FROM sale_voids sv JOIN branches b ON sv.branch_id = b.branch_id "
        "WHERE " + period_sql(period, "sv.voided_at") + frag
    )
    total = query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(sv.qty_sold * sv.unit_price), 0) AS value " + base,
        tuple(params), fetchone=True,
    )
    rows = query(
        "SELECT sv.voided_at, sv.sold_at, b.branch_name, sv.item_name, sv.sku, sv.unit, sv.qty_sold, sv.unit_price, "
        "sv.sale_type, sv.payment_method, COALESCE(sv.customer_name, sv.buyer_name) AS customer, sv.reason, "
        "sv.voided_by_username " + base + " ORDER BY sv.voided_at DESC LIMIT %s",
        tuple(params + [MAX_ROWS]),
    )
    return {
        "period": period,
        "voided_sales": total["n"],
        "voided_value": float(total["value"]),
        "results": [
            {"voided_at": r["voided_at"].isoformat(), "sold_at": r["sold_at"].isoformat() if r["sold_at"] else None,
             "branch": r["branch_name"], "item_name": r["item_name"], "sku": r["sku"],
             "qty": r["qty_sold"], "qty_unit": "mL" if r["unit"] == "BULK" else "bottles",
             "total": float(r["qty_sold"] * r["unit_price"]), "sale_type": r["sale_type"],
             "payment_method": r["payment_method"], "customer": r["customer"] or "Walk-in",
             "reason": r["reason"], "voided_by": r["voided_by_username"]}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_inventory_log  (both roles, branch-scoped)
# ---------------------------------------------------------------------------


def get_inventory_log(args, ctx):
    """The stock movement ledger (Inventory Log): production, dispatch, receipts, sales,
    refills, freebies, adjustments, damage, package orders and voids."""
    period = (args.get("period") or "this_week").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    movement = (args.get("movement_type") or "").strip().upper()
    if movement and movement not in MOVEMENT_TYPE_CHOICES:
        return {"error": f"movement_type must be one of {', '.join(MOVEMENT_TYPE_CHOICES)}."}
    item = (args.get("item_name") or "").strip()
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "sml.branch_id")
    if err:
        return {"error": err}
    where = "WHERE " + period_sql(period, "sml.created_at") + frag
    if movement:
        where += " AND sml.movement_type = %s"
        params.append(movement)
    if item:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        params += [f"%{item}%", f"%{item}%"]
    base = (
        "FROM stock_movement_logs sml JOIN branches b ON sml.branch_id = b.branch_id "
        "JOIN products p ON sml.sku = p.sku " + where
    )
    totals = query(
        "SELECT sml.movement_type, COUNT(*) AS entries, COALESCE(SUM(sml.change_qty), 0) AS net_units " + base +
        " GROUP BY sml.movement_type ORDER BY entries DESC",
        tuple(params),
    )
    try:
        limit = max(1, min(int(args.get("limit") or 15), MAX_ROWS))
    except (TypeError, ValueError):
        limit = 15
    rows = query(
        "SELECT sml.created_at, b.branch_name, p.item_name, p.sku, p.unit, sml.movement_type, sml.change_qty, "
        "sml.before_qty, sml.after_qty, sml.notes " + base + " ORDER BY sml.created_at DESC, sml.log_id DESC LIMIT %s",
        tuple(params + [limit]),
    )
    return {
        "period": period,
        "totals_by_type": {r["movement_type"]: {"entries": r["entries"], "net_units": int(r["net_units"])} for r in totals},
        "results": [
            {"when": r["created_at"].isoformat(), "branch": r["branch_name"], "item_name": r["item_name"],
             "sku": r["sku"], "type": r["movement_type"], "change": r["change_qty"],
             "unit": "mL" if r["unit"] == "BULK" else "bottles",
             "before": r["before_qty"], "after": r["after_qty"], "notes": r["notes"]}
            for r in rows
        ],
        "note": "net_units mixes bottles and bulk mL only if you did not filter; check the unit on each row.",
    }


# ---------------------------------------------------------------------------
# Tool: find_report  (both roles)
# ---------------------------------------------------------------------------


def find_report(args, ctx):
    """Which Reports page report answers a question, with a link that opens it ready to download."""
    topic = (args.get("topic") or "").strip().lower()
    role = ctx.get("role")
    endpoint = "admin.reports" if role == "Admin" else "branch.reports"
    catalog = report_catalog(role)
    # Plain words only: drop filler, and trim a trailing "s" so "prices" finds "Price Changes".
    stop = {"the", "and", "for", "report", "reports", "show", "shows", "which", "what", "went", "how", "are", "can",
            "give", "get", "who", "all", "list", "export", "download", "pdf", "excel", "need", "want", "see", "about", "with"}
    raw = [w.strip("?.!'\"") for w in topic.replace(",", " ").split()]
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in raw if len(w) > 2 and w not in stop]
    # Everyday words that name a report without appearing in its title.
    aliases = {"logged": "login", "signed": "login", "sign": "login", "password": "account", "user": "account",
               "profit": "financial", "revenue": "financial", "damaged": "discrepanc", "short": "discrepanc",
               "refund": "voided", "cancelled": "voided", "canceled": "voided", "owed": "credit", "debt": "credit"}
    words = [aliases.get(w, w) for w in words]

    def score(entry):
        label = entry["label"].lower()
        haystack = f"{label} {entry['description']} {entry['group']}".lower()
        return sum(haystack.count(w) + (3 if w in label else 0) + (2 if label.startswith(w) else 0) for w in words)

    ranked = sorted(catalog, key=score, reverse=True)
    matches = [e for e in ranked if score(e) > 0][:5] if words else ranked[:MAX_ROWS]
    if not matches:
        matches = ranked[:5]
        note = "Nothing matched that exactly; these are the closest. "
    else:
        note = ""
    return {
        "results": [
            {"report": e["label"], "group": e["group"], "description": e["description"],
             "has_date_filter": REPORT_TYPES[e["key"]]["windowed"],
             "open_at": url_for(endpoint, type=e["key"])}
            for e in matches
        ],
        "note": note + "On the Reports page you can narrow a report by date and options, then download it as PDF or Excel. "
                       "You cannot generate the file yourself; tell the user which report to pick.",
    }
