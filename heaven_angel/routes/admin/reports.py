
from flask import (
    abort, flash, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services.reports import (REPORT_GROUPS, REPORT_TYPES, report_catalog, get_report,
                     parse_report_filters, render_report_excel, render_report_pdf)
from heaven_angel.utils import (
    SALE_UNITS, business_today,
)

from heaven_angel.routes.admin._common import bp
from heaven_angel.services import admin_report_data as report_data
from heaven_angel.services.trends import TREND_GRANULARITIES


# LOW_STOCK_WHERE (from reports.py): shared low-stock rule (HQ + branches, at/below own
# reorder level, never BULK). Used by the dashboard, Low Stock page, reports and AI tools.


# ---------------------------------------------------------------- reports
# Reports page: all-time sales, package and partner rankings.
@bp.route("/reports")
@admin_required
def reports():
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    catalog = report_catalog("Admin")
    # All-time, across every branch.
    top_sellers = query(
        """SELECT p.sku, p.item_name, p.variant,
                  SUM(s.qty_sold) AS total_units,
                  SUM(s.qty_sold * s.unit_price) AS total_revenue
           FROM sales s
           JOIN sale_catalog p ON p.sku = s.sku
           WHERE p.unit <> 'BULK'  -- bulk qty_sold is mL, not bottles
           GROUP BY p.sku, p.item_name, p.variant
           ORDER BY total_units DESC LIMIT 3"""
    )

    # Best-selling packages: only Closed counts as a sale. Joined through packages, so
    # deleted packages drop out of this breakdown (not the totals).
    top_packages = query(
        """SELECT pkg.package_id, pkg.package_name,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_revenue
           FROM partner_inquiries pinq
           JOIN packages pkg ON pkg.package_id = pinq.package_id
           WHERE pinq.status = 'Closed'
           GROUP BY pkg.package_id, pkg.package_name
           ORDER BY total_revenue DESC LIMIT 3"""
    )

    # Top partners by package sales (Closed only), grouped by company name
    # (case/whitespace-insensitive) so repeat submissions roll into one line.
    # MIN/MAX pick one representative id, name and type per company.
    top_partners = query(
        """SELECT MIN(p.partner_id) AS partner_id, MIN(p.partner_name) AS partner_name,
                  MAX(p.partner_type) AS partner_type,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_spent
           FROM partner_inquiries pinq
           JOIN partners p ON p.partner_id = pinq.partner_id
           WHERE pinq.status = 'Closed'
           GROUP BY LOWER(TRIM(p.partner_name))
           ORDER BY total_spent DESC LIMIT 3"""
    )

    return render_template(
        "admin/reports.html", branch_list=branch_list, report_catalog=catalog, report_groups=REPORT_GROUPS,
        unit_choices=SALE_UNITS,
        current_month=business_today().strftime("%Y-%m"),
        top_sellers=top_sellers, top_packages=top_packages, top_partners=top_partners,
    )


@bp.route("/reports/generate")
@admin_required
def generate_report():
    """Download a filtered report as PDF or Excel; admins can pull any branch or all."""
    report_type = request.args.get("type", "")
    fmt = request.args.get("format", "pdf")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["admin"] or fmt not in ("pdf", "xlsx"):
        abort(404)

    filters = parse_report_filters(request.args)
    report = get_report(report_type, filters, branch_scope=None,
                        actor_label=f"HQ Admin, {session.get('username')}")

    if report["row_count"] == 0:
        flash(
            f"No data matches the selected filters for {report['title']}.", "warning")
        return redirect(url_for("admin.reports"))

    stamp = business_today().isoformat()
    if fmt == "xlsx":
        buf = render_report_excel(report)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buf = render_report_pdf(report)
        mimetype = "application/pdf"
    return send_file(
        buf, mimetype=mimetype, as_attachment=True,
        download_name=f"{report_type}-{stamp}.{fmt}",
    )


@bp.route("/api/report-preview")
@admin_required
def report_preview():
    """Row count for the builder's current choices (warns before an empty download)."""
    report_type = request.args.get("type", "")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["admin"]:
        abort(404)
    report = get_report(report_type, parse_report_filters(request.args), branch_scope=None,
                        actor_label=f"HQ Admin, {session.get('username')}")
    return jsonify({"rows": report["row_count"], "truncated": report["truncated"],
                    "title": report["title"], "subtitle": report["subtitle"]})


# JSON: chart data for the Reports page.
@bp.route("/api/reports-data")
@admin_required
def reports_data():
    granularity = request.args.get("granularity", "daily")
    trend_bucket = TREND_GRANULARITIES.get(granularity, TREND_GRANULARITIES["daily"])

    by_variant, by_branch, stock_by_branch = report_data.sales_breakdowns()
    totals, package_totals = report_data.all_time_totals()
    financials, windowed_revenue, windowed_materials_cost = report_data.windowed_financials(trend_bucket)
    return jsonify(
        by_variant=by_variant, by_branch=by_branch,
        movement_trend=report_data.movement_trend(trend_bucket),
        stock_by_branch=stock_by_branch, totals=totals, financials=financials,
        kpi=report_data.kpi_tiles(granularity, trend_bucket, windowed_revenue, windowed_materials_cost),
        sales_by_channel=report_data.sales_by_channel(package_totals),
    )
