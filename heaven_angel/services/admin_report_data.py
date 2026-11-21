"""Data for the admin Reports charts, one function per chart group.

Granularity SQL comes only from the fixed allow-list in services/trends.py.
"""
from heaven_angel.db import query
from heaven_angel.services.finance import (
    PACKAGE_DATE_SQL, SALE_COST_EXPR, SALES_WITH_COST_SQL, UNIT_COST_SQL, cogs_sold,
)
from heaven_angel.services.trends import trend_bucket_dates
from heaven_angel.utils import business_today


def sales_breakdowns():
    """Units sold by variant, and sales/stock per branch (HQ warehouse excluded)."""
    # Bottles only: bulk qty_sold is mL and would swamp unit counts (bulk_ml is separate).
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p LEFT JOIN sales s ON p.sku = s.sku
           WHERE p.unit <> 'BULK'
           GROUP BY p.variant"""
    )

    # Revenue = SUM(qty_sold * unit_price); unit_price was captured at sale time.
    by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           LEFT JOIN sale_catalog p ON p.sku = s.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    stock_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS total_stock,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS bulk_ml
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON p.sku = bi.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    return by_variant, by_branch, stock_by_branch


def movement_trend(trend_bucket):
    """Stock movement per bucket and type, over the chosen window."""
    window_sql = trend_bucket["window"]
    # Bottles and bulk mL are different units, so each movement type gives two series.
    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, sml.movement_type,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS units,
                   COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS bulk_ml
            FROM stock_movement_logs sml
            JOIN products p ON p.sku = sml.sku
            WHERE sml.created_at >= NOW() - {window_sql}
            GROUP BY {trend_bucket['trunc']}, sml.movement_type ORDER BY day"""
    )
    for row in movement_trend:
        row["day"] = row["day"].isoformat()
    return movement_trend


def all_time_totals():
    """Returns (totals, package_totals) — both all-time."""
    # All-time totals across branches (not windowed); revenue includes closed package orders.
    branch_totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN sale_catalog p ON p.sku = s.sku""",
        fetchone=True,
    )
    package_totals = query(
        """SELECT COUNT(*) AS order_count, COALESCE(SUM(order_amount), 0) AS revenue
           FROM partner_inquiries WHERE status = 'Closed'""",
        fetchone=True,
    )
    totals = {
        "units": branch_totals["units"],
        "bulk_ml": branch_totals["bulk_ml"],
        "revenue": branch_totals["revenue"] + package_totals["revenue"],
        "branch_revenue": branch_totals["revenue"],
        "package_revenue": package_totals["revenue"],
        "package_order_count": package_totals["order_count"],
    }
    return totals, package_totals


def windowed_financials(trend_bucket):
    """Returns (financials, windowed_revenue, windowed_materials_cost) for the chosen window."""
    window_sql = trend_bucket["window"]
    # Financials: capital = SUM(total_cogs) produced; profit = revenue - cost of goods sold, windowed.
    capital_total = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs", fetchone=True
    )["v"]
    # Oil bought per scent (scent_oil_restocks) is raw material spend too.
    raw_materials_purchased = query(
        """SELECT (SELECT COALESCE(SUM(package_cost), 0) FROM raw_materials)
                + (SELECT COALESCE(SUM(cost), 0) FROM scent_oil_restocks) AS v""", fetchone=True
    )["v"]
    windowed_branch_revenue = query(
        f"SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE sold_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    windowed_package_revenue = query(
        f"""SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries pi
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}""",
        fetchone=True,
    )["v"]
    windowed_revenue = windowed_branch_revenue + windowed_package_revenue
    # Cost of goods SOLD in the window (finance.py); production batches are capital, not expense.
    windowed_materials_cost = cogs_sold(
        f"s.sold_at >= NOW() - {window_sql}", f"{PACKAGE_DATE_SQL} >= NOW() - {window_sql}")
    financials = {
        "capital": capital_total,
        "raw_materials_purchased": raw_materials_purchased,
        "revenue_windowed": windowed_revenue,
        "branch_revenue_windowed": windowed_branch_revenue,
        "package_revenue_windowed": windowed_package_revenue,
        "materials_cost": windowed_materials_cost,
        "profit": float(windowed_revenue) - float(windowed_materials_cost),
    }
    return financials, windowed_revenue, windowed_materials_cost


def kpi_tiles(granularity, trend_bucket, windowed_revenue, windowed_materials_cost):
    """KPI tiles + the Revenue/Cost/Profit series, with the preceding window for the up/down chips."""
    window_sql = trend_bucket["window"]
    # KPI tiles and Revenue/Cost/Profit series: one point per bucket, plus the previous window.
    def _bucket(col):
        return trend_bucket["trunc"].replace("sml.created_at", col)

    def _series(sql_by_day):
        return {r["day"].isoformat(): r for r in sql_by_day}

    sales_series = _series(query(
        f"""SELECT {_bucket('s.sold_at')} AS day,
                   COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units
            FROM sales s JOIN sale_catalog p ON p.sku = s.sku
            WHERE s.sold_at >= NOW() - {window_sql}
            GROUP BY {_bucket('s.sold_at')}"""))
    package_series = _series(query(
        f"""SELECT {_bucket(PACKAGE_DATE_SQL)} AS day, COALESCE(SUM(order_amount), 0) AS revenue
            FROM partner_inquiries pi
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}
            GROUP BY {_bucket(PACKAGE_DATE_SQL)}"""))
    # Cost of goods sold per bucket: register sales + closed package orders.
    cost_series = _series(query(
        f"""SELECT {_bucket('s.sold_at')} AS day,
                   COALESCE(SUM({SALE_COST_EXPR}), 0) AS cost
            FROM {SALES_WITH_COST_SQL}
            WHERE s.sold_at >= NOW() - {window_sql}
            GROUP BY {_bucket('s.sold_at')}"""))
    package_cost_series = _series(query(
        f"""SELECT {_bucket(PACKAGE_DATE_SQL)} AS day,
                   COALESCE(SUM(pii.qty * COALESCE(uc.unit_cost, 0)), 0) AS cost
            FROM partner_inquiry_items pii
            JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
            LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}
            GROUP BY {_bucket(PACKAGE_DATE_SQL)}"""))
    # Cost of goods PRODUCED per bucket — capital, drives the Capital sparkline.
    produced_series = _series(query(
        f"""SELECT {_bucket('created_at')} AS day, COALESCE(SUM(total_cogs), 0) AS cost
            FROM cogs_logs WHERE created_at >= NOW() - {window_sql}
            GROUP BY {_bucket('created_at')}"""))

    raw_series = _series(query(
        f"""SELECT {_bucket('d')} AS day, COALESCE(SUM(amt), 0) AS raw
            FROM (SELECT created_at AS d, package_cost AS amt FROM raw_materials
                  UNION ALL
                  SELECT purchased_at, cost FROM scent_oil_restocks) purchases
            WHERE d >= NOW() - {window_sql}
            GROUP BY {_bucket('d')}"""))

    bucket_days = {d.isoformat() for d in trend_bucket_dates(granularity, business_today())}
    bucket_days |= (set(sales_series) | set(package_series) | set(cost_series) | set(package_cost_series)
                    | set(produced_series) | set(raw_series))
    kpi_series = []
    for day in sorted(bucket_days):
        sale_row = sales_series.get(day)
        pkg_row = package_series.get(day)
        cost_row = cost_series.get(day)
        pkg_cost_row = package_cost_series.get(day)
        produced_row = produced_series.get(day)
        kpi_series.append({
            "day": day,
            "revenue": float((sale_row["revenue"] if sale_row else 0) + (pkg_row["revenue"] if pkg_row else 0)),
            "cost": float((cost_row["cost"] if cost_row else 0) + (pkg_cost_row["cost"] if pkg_cost_row else 0)),
            "produced": float(produced_row["cost"]) if produced_row else 0.0,
            "raw": float(raw_series[day]["raw"]) if day in raw_series else 0.0,
            "units": int(sale_row["units"]) if sale_row else 0,
        })

    window2_sql = trend_bucket["window2"]
    prev_revenue = query(
        f"""SELECT (SELECT COALESCE(SUM(qty_sold * unit_price), 0) FROM sales
                    WHERE sold_at >= NOW() - {window2_sql} AND sold_at < NOW() - {window_sql})
                 + (SELECT COALESCE(SUM(order_amount), 0) FROM partner_inquiries pi
                    WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window2_sql}
                      AND {PACKAGE_DATE_SQL} < NOW() - {window_sql}) AS v""",
        fetchone=True)["v"]
    prev_cost = cogs_sold(
        f"s.sold_at >= NOW() - {window2_sql} AND s.sold_at < NOW() - {window_sql}",
        f"{PACKAGE_DATE_SQL} >= NOW() - {window2_sql} AND {PACKAGE_DATE_SQL} < NOW() - {window_sql}")
    prev_units = query(
        f"""SELECT COALESCE(SUM(s.qty_sold), 0) AS v FROM sales s JOIN sale_catalog p ON p.sku = s.sku
            WHERE p.unit <> 'BULK' AND s.sold_at >= NOW() - {window2_sql} AND s.sold_at < NOW() - {window_sql}""",
        fetchone=True)["v"]
    windowed_units = query(
        f"""SELECT COALESCE(SUM(s.qty_sold), 0) AS v FROM sales s JOIN sale_catalog p ON p.sku = s.sku
            WHERE p.unit <> 'BULK' AND s.sold_at >= NOW() - {window_sql}""",
        fetchone=True)["v"]
    kpi = {
        "revenue": float(windowed_revenue), "cost": float(windowed_materials_cost),
        "units": int(windowed_units),
        "prev_revenue": float(prev_revenue), "prev_cost": float(prev_cost),
        "prev_units": int(prev_units),
        "series": kpi_series,
    }
    return kpi


def sales_by_channel(package_totals):
    """Share of all-time sales per channel, largest first."""
    # Share of all-time sales by channel (each branch incl. the HQ
    # warehouse, plus closed package orders) for the donut's legend.
    channel_rows = query(
        """SELECT b.branch_name AS name, COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON s.branch_id = b.branch_id
           GROUP BY b.branch_id, b.branch_name, b.is_hq
           ORDER BY b.is_hq DESC, b.branch_name""")
    sales_by_channel = [{"name": r["name"], "revenue": float(r["revenue"])} for r in channel_rows]
    if package_totals["revenue"]:
        sales_by_channel.append({"name": "Package orders", "revenue": float(package_totals["revenue"])})
    sales_by_channel = sorted((c for c in sales_by_channel if c["revenue"] > 0),
                              key=lambda c: c["revenue"], reverse=True)
    return sales_by_channel
