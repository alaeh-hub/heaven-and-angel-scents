
from flask import (
    render_template,
    request,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services import login_activity
from heaven_angel.services.reports import (DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- ledger
@bp.route("/movement-logs")
@admin_required
def movement_logs():
    """Production/Sale/Refill activity only.

    Stock-request movements (reference_type='STOCK_REQUEST') are excluded: a delivery's
    own page and receipt show them grouped.
    """
    branch_filter = request.args.get("branch_id", "all")
    sql = """SELECT sml.*, b.branch_name, p.item_name
              FROM stock_movement_logs sml
              JOIN branches b ON sml.branch_id = b.branch_id
              JOIN products p ON sml.sku = p.sku
              WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')"""
    params = []
    if branch_filter != "all":
        sql += " AND sml.branch_id = %s"
        params.append(branch_filter)
    sql += " ORDER BY sml.created_at DESC LIMIT 200"
    logs = query(sql, tuple(params))
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches ORDER BY branch_name")
    return render_template("admin/movement_logs.html", logs=logs, branch_list=branch_list, branch_filter=branch_filter)


# ---------------------------------------------------------------- discrepancies (fleet-wide)
@bp.route("/discrepancies")
@admin_required
def discrepancies():
    """Fleet-wide delivery discrepancies (DAMAGE and ADJUSTMENT rows) across every branch."""
    branch_filter = request.args.get("branch_id", "all")
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")

    # Per-branch rollup, always fleet-wide. Units come from delivery lines (the log rows
    # carry change_qty = 0); total = damaged + shortfall.
    summary = query(
        f"""SELECT b.branch_id, b.branch_name,
                  COUNT(DISTINCT sml.reference_id) AS deliveries_affected,
                  COALESCE(SUM(CASE WHEN sml.movement_type = 'DAMAGE'
                                    THEN {DISCREPANCY_UNITS_SQL} ELSE 0 END), 0) AS damaged_units,
                  COALESCE(SUM(CASE WHEN sml.movement_type = 'ADJUSTMENT'
                                    THEN {DISCREPANCY_UNITS_SQL} ELSE 0 END), 0) AS shortfall_units
           FROM branches b
           LEFT JOIN stock_movement_logs sml
             ON sml.branch_id = b.branch_id
            AND sml.reference_type = 'STOCK_REQUEST'
            AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
           {DISCREPANCY_ITEM_JOIN}
           WHERE b.is_hq = FALSE
           GROUP BY b.branch_id, b.branch_name"""
    )
    for s in summary:
        s["damaged_units"] = int(s["damaged_units"])
        s["shortfall_units"] = int(s["shortfall_units"])
        s["total_units"] = s["damaged_units"] + s["shortfall_units"]
    summary.sort(key=lambda s: (-s["total_units"], -s["damaged_units"], s["branch_name"]))

    # Detail log: like branch.py's discrepancies(), across branches, with per-branch tabs.
    sql = f"""SELECT sml.*, p.item_name, b.branch_name, sr.delivery_number,
                     {DISCREPANCY_UNITS_SQL} AS units_lost
              FROM stock_movement_logs sml
              JOIN products p ON sml.sku = p.sku
              JOIN branches b ON sml.branch_id = b.branch_id
              LEFT JOIN stock_requests sr
                ON sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = sr.request_id
              {DISCREPANCY_ITEM_JOIN}
              WHERE sml.reference_type = 'STOCK_REQUEST'
                AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')"""
    params = []
    if branch_filter != "all":
        sql += " AND sml.branch_id = %s"
        params.append(branch_filter)
    sql += " ORDER BY sml.created_at DESC LIMIT 300"
    logs = query(sql, tuple(params))

    totals = {
        "total_units": sum(s["total_units"] for s in summary),
        "damaged_units": sum(s["damaged_units"] for s in summary),
        "shortfall_units": sum(s["shortfall_units"] for s in summary),
    }

    return render_template(
        "admin/discrepancies.html",
        summary=summary, logs=logs, totals=totals,
        branch_list=branch_list, branch_filter=branch_filter,
    )


# ---------------------------------------------------------------- audit log
@bp.route("/audit-log")
@admin_required
def audit_log():
    """Non-inventory admin activity (account, product, branch changes); see audit.py."""
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = 300
    offset = (page - 1) * page_size
    # Fetch one extra row to know if another page exists (no COUNT query).
    rows = query(
        """SELECT action_id, actor_username, action, target, details, created_at
           FROM admin_actions
           ORDER BY created_at DESC LIMIT %s OFFSET %s""",
        (page_size + 1, offset),
    )
    has_next = len(rows) > page_size
    logs = rows[:page_size]
    return render_template(
        "admin/audit_log.html", logs=logs, page=page, has_next=has_next)


# ---------------------------------------------------------------- login activity
@bp.route("/login-activity")
@admin_required
def login_activity_log():
    """Every sign-in attempt, successful or not (see login_activity.py).

    username_attempted is what was typed, kept even if no account matched;
    current_username is the account's name today.
    """
    outcome_filter = request.args.get("outcome", "all")
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = 300
    offset = (page - 1) * page_size
    sql = """SELECT la.*, u.username AS current_username
             FROM login_activity la
             LEFT JOIN users u ON la.user_id = u.user_id"""
    if outcome_filter == "success":
        sql += " WHERE la.success = TRUE"
    elif outcome_filter == "failed":
        sql += " WHERE la.success = FALSE"
    # Fetch one extra row to know if another page exists (no COUNT query).
    sql += " ORDER BY la.created_at DESC LIMIT %s OFFSET %s"
    rows = query(sql, (page_size + 1, offset))
    has_next = len(rows) > page_size
    logs = rows[:page_size]

    totals = query(
        """SELECT COUNT(*) AS total_attempts,
                  COALESCE(SUM(CASE WHEN success THEN 0 ELSE 1 END), 0) AS failed_attempts,
                  COUNT(DISTINCT CASE WHEN NOT success THEN ip_address END) AS failed_ip_count
           FROM login_activity""",
        fetchone=True,
    )

    # Usernames with several failed attempts in the last 24 hours.
    recent_failures = query(
        """SELECT username_attempted, COUNT(*) AS attempt_count, MAX(created_at) AS last_attempt_at
           FROM login_activity
           WHERE success = FALSE AND created_at >= NOW() - INTERVAL 24 HOUR
           GROUP BY username_attempted
           HAVING attempt_count >= 3
           ORDER BY attempt_count DESC LIMIT 10"""
    )

    return render_template(
        "admin/login_activity.html", logs=logs, totals=totals,
        outcome_filter=outcome_filter, recent_failures=recent_failures,
        failure_reasons=login_activity.LOGIN_FAILURE_REASONS,
        page=page, has_next=has_next,
    )
