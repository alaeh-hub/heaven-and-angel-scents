
from flask import (
    render_template,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services.finance import (
    PACKAGE_DATE_SQL, SALE_COST_EXPR, SALES_WITH_COST_SQL, UNIT_COST_SQL, cogs_sold,
)
from heaven_angel.services.reports import (LOW_STOCK_WHERE)
from heaven_angel.utils import (
    business_today, percent_change,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "sku_count": query("SELECT COUNT(*) c FROM products", fetchone=True)["c"],
        "branch_count": query("SELECT COUNT(*) c FROM branches WHERE is_hq = FALSE", fetchone=True)["c"],
        "pending_requests": query(
            "SELECT COUNT(*) c FROM stock_requests WHERE status IN ('Pending', 'Partially Fulfilled')", fetchone=True
        )["c"],
        # HQ's warehouse plus every branch — see LOW_STOCK_WHERE.
        "low_stock_count": query(
            f"""SELECT COUNT(*) c FROM branch_inventory bi
               JOIN branches b ON bi.branch_id = b.branch_id
               JOIN products p ON bi.sku = p.sku
               WHERE {LOW_STOCK_WHERE}""",
            fetchone=True,
        )["c"],
    }

    low_stock = query(
        f"""SELECT b.branch_name, b.is_hq, p.item_name, p.sku, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi
           JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE {LOW_STOCK_WHERE}
           ORDER BY bi.stock_qty ASC LIMIT 8"""
    )

    recent_requests = query(
        """SELECT sr.request_id, sr.delivery_number, b.branch_name, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           GROUP BY sr.request_id
           ORDER BY sr.requested_at DESC LIMIT 6"""
    )

    # Same exclusion as movement_logs(): STOCK_REQUEST rows belong on the delivery page.
    recent_activity = query(
        """SELECT sml.created_at, b.branch_name, p.item_name, sml.change_qty, sml.movement_type
           FROM stock_movement_logs sml
           JOIN branches b ON sml.branch_id = b.branch_id
           JOIN products p ON sml.sku = p.sku
           WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')
           ORDER BY sml.created_at DESC LIMIT 8"""
    )

    # Operational snapshot: bottles on hand per branch (sales rankings are on Reports).
    stock_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS total_stock
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON p.sku = bi.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY total_stock DESC"""
    )

    # Financials: revenue = branch sales + closed package orders (order_amount).
    # Capital = cost of goods produced (SUM total_cogs in cogs_logs). Raw materials bought
    # (SUM package_cost) is a comparison figure only; it does not feed capital or profit.
    total_cogs = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs", fetchone=True
    )["v"]
    # Oil bought per scent (scent_oil_restocks) is raw material spend too.
    raw_materials_purchased = query(
        """SELECT (SELECT COALESCE(SUM(package_cost), 0) FROM raw_materials)
                + (SELECT COALESCE(SUM(cost), 0) FROM scent_oil_restocks) AS v""", fetchone=True
    )["v"]
    branch_sales_revenue = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales", fetchone=True
    )["v"]
    package_sales_revenue = query(
        "SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries WHERE status = 'Closed'",
        fetchone=True,
    )["v"]
    total_revenue = branch_sales_revenue + package_sales_revenue

    # Revenue trend: month-to-date vs the same stretch of last month (MySQL clamps
    # - INTERVAL 1 MONTH to month end). Closed orders count in closed_at (else created_at).
    month_revenue = query(
        """SELECT
               COALESCE(SUM(CASE WHEN t.at >= CURDATE() - INTERVAL (DAY(CURDATE()) - 1) DAY
                                 THEN t.amount END), 0) AS current_period,
               COALESCE(SUM(CASE WHEN t.at < NOW() - INTERVAL 1 MONTH
                                 THEN t.amount END), 0) AS previous_period
           FROM (
               SELECT sold_at AS at, qty_sold * unit_price AS amount FROM sales
               UNION ALL
               SELECT COALESCE(closed_at, created_at), order_amount FROM partner_inquiries WHERE status = 'Closed'
           ) t
           WHERE t.at >= CURDATE() - INTERVAL (DAY(CURDATE()) - 1) DAY - INTERVAL 1 MONTH
             AND t.at < NOW()""",
        fetchone=True,
    )

    # Profit = revenue - cost of goods SOLD (finance.py); production cost shows as capital.
    cogs_total = cogs_sold()
    receivables = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales "
        "WHERE payment_method = 'Credit' AND credit_settled_at IS NULL", fetchone=True)["v"]

    # Last 14 days of revenue and profit, zero-filled, for the tile sparklines.
    from datetime import timedelta
    spark_days = [business_today() - timedelta(days=i) for i in range(13, -1, -1)]
    rev_by_day = {}
    for r in query(
        """SELECT d AS day, SUM(amount) AS amount FROM (
               SELECT DATE(sold_at) AS d, qty_sold * unit_price AS amount FROM sales
                 WHERE sold_at >= CURDATE() - INTERVAL 14 DAY
               UNION ALL
               SELECT DATE(COALESCE(closed_at, created_at)), order_amount FROM partner_inquiries
                 WHERE status = 'Closed' AND COALESCE(closed_at, created_at) >= CURDATE() - INTERVAL 14 DAY
           ) t GROUP BY d"""):
        rev_by_day[r["day"]] = float(r["amount"] or 0)
    production_by_day = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(created_at) AS day, SUM(total_cogs) AS amount FROM cogs_logs
               WHERE created_at >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(created_at)""")
    }
    cogs_by_day = {}
    for r in query(
        f"""SELECT DATE(s.sold_at) AS day, SUM({SALE_COST_EXPR}) AS amount
            FROM {SALES_WITH_COST_SQL}
            WHERE s.sold_at >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(s.sold_at)"""):
        cogs_by_day[r["day"]] = cogs_by_day.get(r["day"], 0.0) + float(r["amount"] or 0)
    for r in query(
        f"""SELECT DATE({PACKAGE_DATE_SQL}) AS day, SUM(pii.qty * COALESCE(uc.unit_cost, 0)) AS amount
            FROM partner_inquiry_items pii
            JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
            LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= CURDATE() - INTERVAL 14 DAY
            GROUP BY DATE({PACKAGE_DATE_SQL})"""):
        cogs_by_day[r["day"]] = cogs_by_day.get(r["day"], 0.0) + float(r["amount"] or 0)
    raw_by_day = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(d) AS day, SUM(amt) AS amount FROM (
                   SELECT created_at AS d, package_cost AS amt FROM raw_materials
                   UNION ALL
                   SELECT purchased_at, cost FROM scent_oil_restocks
               ) purchases
               WHERE d >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(d)""")
    }
    spark_cost = [production_by_day.get(d, 0.0) for d in spark_days]
    spark_raw = [raw_by_day.get(d, 0.0) for d in spark_days]
    spark_revenue = [rev_by_day.get(d, 0.0) for d in spark_days]
    spark_profit = [rev_by_day.get(d, 0.0) - cogs_by_day.get(d, 0.0) for d in spark_days]

    financials = {
        "spark_revenue": spark_revenue,
        "spark_profit": spark_profit,
        "spark_cost": spark_cost,
        "spark_raw": spark_raw,
        "profit_margin": ((float(total_revenue) - cogs_total) / float(total_revenue) * 100) if total_revenue else None,
        "receivables": receivables,
        "capital": total_cogs,
        "revenue": total_revenue,
        "branch_sales_revenue": branch_sales_revenue,
        "package_sales_revenue": package_sales_revenue,
        "materials_cost": cogs_total,
        "raw_materials_purchased": raw_materials_purchased,
        "profit": float(total_revenue) - cogs_total,
        "revenue_trend": percent_change(
            month_revenue["current_period"], month_revenue["previous_period"]),
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
        stock_by_branch=stock_by_branch, financials=financials,
    )
