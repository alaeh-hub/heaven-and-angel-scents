from urllib.parse import urlencode

from flask import render_template, url_for

from heaven_angel.db import query
from heaven_angel.decorators import branch_required
from heaven_angel.utils import (
    BOTTLE_UNITS, base_code_from_sku, business_today, percent_change,
)

from heaven_angel.routes.branch._common import _branch_id, bp


# ---------------------------------------------------------------- dashboard
# Branch home: low stock, deliveries and today's sales.
@bp.route("/")
@branch_required
def dashboard():
    bid = _branch_id()
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    # BULK left out: branches can't request it, so a reorder flag is noise.
    low_stock = [row for row in inventory
                 if row["unit"] in BOTTLE_UNITS and row["stock_qty"] <= row["reorder_level"]]
    # SKUs carried count by base code (A1-85ML, A1-BULK... are one SKU).
    sku_count = len({base_code_from_sku(row["sku"], row["unit"]) for row in inventory})

    # Suggested reorder qty: top up to reorder_level * 3, min 1; editable in the cart.
    for row in low_stock:
        row["suggested_qty"] = max(
            (row["reorder_level"] * 3) - row["stock_qty"], 1)
        # Only bottle sizes can be requested from HQ (see request_stock()).
        row["requestable"] = row["unit"] in BOTTLE_UNITS

    # Querystring for "Reorder all low stock": repeated sku/qty keys seed the cart.
    reorder_all_url = None
    reorderable = [row for row in low_stock if row["requestable"]]
    if reorderable:
        pairs = [("sku", row["sku"]) for row in reorderable] + \
            [("qty", row["suggested_qty"]) for row in reorderable]
        reorder_all_url = url_for(
            "branch.request_stock") + "?" + urlencode(pairs)

    # A request is a multi-item delivery; this widget needs the header and totals only.
    pending_requests = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0)), 0) AS total_remaining
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
            WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'Partially Fulfilled', 'In Transit')
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC""",
        (bid,),
    )

    today_sales = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN sale_catalog p ON p.sku = s.sku
           WHERE s.branch_id = %s AND DATE(s.sold_at) = CURDATE()""",
        (bid,), fetchone=True,
    )

    # Last 14 days of daily revenue, zero-filled, for the hero tile's sparkline.
    from datetime import timedelta
    spark_rows = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(sold_at) AS day, SUM(qty_sold * unit_price) AS amount
               FROM sales WHERE branch_id = %s AND sold_at >= CURDATE() - INTERVAL 14 DAY
               GROUP BY DATE(sold_at)""", (bid,))
    }
    spark_days = [business_today() - timedelta(days=i) for i in range(13, -1, -1)]
    spark_revenue = [spark_rows.get(d, 0.0) for d in spark_days]
    unit_rows = {
        r["day"]: int(r["units"] or 0) for r in query(
            """SELECT DATE(s.sold_at) AS day, SUM(s.qty_sold) AS units
               FROM sales s JOIN sale_catalog p ON p.sku = s.sku
               WHERE s.branch_id = %s AND p.unit <> 'BULK' AND s.sold_at >= CURDATE() - INTERVAL 14 DAY
               GROUP BY DATE(s.sold_at)""", (bid,))
    }
    spark_units = [unit_rows.get(d, 0) for d in spark_days]
    week_revenue = sum(spark_revenue[7:])
    prev_week_revenue = sum(spark_revenue[:7])
    week_trend = percent_change(week_revenue, prev_week_revenue)

    # Trend arrow: today vs yesterday up to the same time of day.
    yesterday_revenue = query(
        """SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s
             AND sold_at >= CURDATE() - INTERVAL 1 DAY AND sold_at < NOW() - INTERVAL 1 DAY""",
        (bid,), fetchone=True,
    )["revenue"]
    today_sales["trend"] = percent_change(today_sales["revenue"], yesterday_revenue)

    return render_template(
        "branch/dashboard.html",
        inventory=inventory, low_stock=low_stock,
        pending_requests=pending_requests, today_sales=today_sales,
        reorder_all_url=reorder_all_url, sku_count=sku_count,
        spark_revenue=spark_revenue, spark_units=spark_units,
        week_revenue=week_revenue, week_trend=week_trend,
    )
