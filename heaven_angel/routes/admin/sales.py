
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, send_file, session, url_for,
)

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.services.receipts import build_sale_receipt_pdf
from heaven_angel.services.sales_import import build_sales_import_template, import_sales_rows, parse_sales_import_workbook
from heaven_angel.services.sale_stock import void_sale
from heaven_angel.sockets import notify_admin, notify_admin_and_branch
from heaven_angel.utils import (
    ValidationError,
    consume_form_token, issue_form_token,
)

from heaven_angel.services import sale_entry
from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


# ---------------------------------------------------------------- record sale (HQ direct)
@bp.route("/record-sale/<int:sale_id>/receipt")
@admin_required
def sale_receipt(sale_id):
    """Downloadable PDF receipt for one sale/refill (receipts.py); admins see every branch."""
    pdf_buffer, sale = build_sale_receipt_pdf(sale_id)
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"SR-{sale.get('order_number', sale_id):06d}.pdf",
    )


# Flash text for a recorded sale (one product, or several on one receipt).
def _recorded_message(sales):
    if len(sales) == 1:
        return f"{sales[0].sale_type} recorded."
    return f"{sales[0].sale_type} recorded: {len(sales)} products on one receipt."


@bp.route("/record-sale", methods=["GET", "POST"])
@admin_required
def record_sale():
    """HQ sells straight from the warehouse (branch_id=1): walk-in, refill or Credit sale.

    Mirrors branch.record_sale(), but HQ can only sell/refill existing stock. The HQ price
    is a suggestion; the amount charged is typed on every sale.
    """
    if request.method == "POST":
        if not consume_form_token("admin_record_sale"):
            flash(
                "Already saved, or the form expired: check the sales list before retrying.", "error")
            return redirect(url_for("admin.record_sale"))

        try:
            sales = sale_entry.parse_sale_order_form(
                request.form, qty_label="Quantity",
                sale_type_prompt="Select whether this is a sale, a refill, or a freebie.",
                product_prompt="Select a product.")
        except sale_entry.SaleFormError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.record_sale"))

        try:
            sale_entry.record_sale_order(
                sales, branch_id=HQ_BRANCH_ID, user_id=session.get("user_id"), note_suffix=" (HQ)")
            notify_admin(["inventory", "sales", "movement_logs", "production"])
            flash(_recorded_message(sales), "success")
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "admin record_sale failed for sku=%s", sales[0].sku)
            flash("Couldn't record that — please try again.", "error")
        return redirect(url_for("admin.record_sale"))

    # Bulk is the mL left in a scent's bulk batches (bulk_batches.base_code). bulk_ml on a
    # bottle row is what a Refill can pour; '<code>-BULK' rows feed the sold-by-mL panel.
    batch_ml = """(SELECT base_code, SUM(remaining_ml) AS ml FROM bulk_batches
                   WHERE base_code IS NOT NULL AND remaining_ml > 0 GROUP BY base_code)"""
    inventory = query(
        f"""SELECT p.sku, p.item_name, p.variant, p.category, p.unit, p.price, bi.stock_qty,
                   CAST(FLOOR(COALESCE(bm.ml, 0)) AS SIGNED) AS bulk_ml
            FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
            LEFT JOIN {batch_ml} bm
              ON bm.base_code = LEFT(p.sku, CHAR_LENGTH(p.sku) - CHAR_LENGTH(p.unit) - 1)
            WHERE bi.branch_id = %s AND (bi.stock_qty > 0 OR bm.ml > 0)
            UNION ALL
            SELECT c.sku, c.item_name, c.variant, c.category, c.unit, c.price,
                   CAST(FLOOR(bm.ml) AS SIGNED), CAST(FLOOR(bm.ml) AS SIGNED)
            FROM sale_catalog c
            JOIN {batch_ml} bm ON CONCAT(bm.base_code, '-BULK') = c.sku
            WHERE c.unit = 'BULK' AND bm.ml >= 1
            ORDER BY item_name""",
        (HQ_BRANCH_ID,),
    )
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    # s.* already carries sales.sku; used for the Item column.
    recent_sales = query(
        """SELECT s.*, p.item_name, p.unit, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN sale_catalog p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (HQ_BRANCH_ID,),
    )
    sale_entry.annotate_receipts(recent_sales)
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    # Every customer name logged at HQ with the address of their first sale (form autofill).
    customers = query(
        """SELECT s.customer_name AS name, s.customer_address AS address
           FROM sales s
           JOIN (
               SELECT customer_name, MIN(sold_at) AS first_sold_at
               FROM sales
               WHERE branch_id = %s AND customer_name IS NOT NULL AND customer_name <> ''
               GROUP BY customer_name
           ) first_seen
             ON first_seen.customer_name = s.customer_name AND first_seen.first_sold_at = s.sold_at
           WHERE s.branch_id = %s
           ORDER BY s.customer_name""",
        (HQ_BRANCH_ID, HQ_BRANCH_ID),
    )
    _seen = set()
    customers = [c for c in customers if not (c["name"] in _seen or _seen.add(c["name"]))]
    return render_template(
        "admin/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
        customers=customers, bulk_rate=bulk_rate, form_token=issue_form_token(
            "admin_record_sale"),
        import_form_token=issue_form_token("admin_import_sales"),
    )


# ---------------------------------------------------------------- bulk import (Excel)
@bp.route("/record-sale/import-template")
@admin_required
def sales_import_template():
    """Blank workbook for the Import from Excel button (sales_import)."""
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    buf = build_sales_import_template(inventory)
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="sales-import-template.xlsx",
    )


@bp.route("/record-sale/import", methods=["POST"])
@admin_required
def import_sales():
    """Bulk-insert a filled template in one all-or-nothing transaction on the HQ warehouse."""
    if not consume_form_token("admin_import_sales"):
        flash(
            "This import already went through, or the form expired — try uploading again.", "error")
        return redirect(url_for("admin.record_sale"))

    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose an Excel file to import.", "error")
        return redirect(url_for("admin.record_sale"))

    try:
        raw_rows = parse_sales_import_workbook(upload)
        inserted, receipts = import_sales_rows(
            HQ_BRANCH_ID, raw_rows, session.get("user_id"))
        notify_admin(["inventory", "sales", "movement_logs", "production"])
        flash(
            f"Imported {inserted} sale{'s' if inserted != 1 else ''} from the file"
            + (f", on {receipts} receipt{'s' if receipts != 1 else ''}." if receipts != inserted else "."), "success")
    except (ValidationError, TransactionAborted) as err:
        for line in str(err).split("\n"):
            if line.strip():
                flash(line, "error")
    except Exception:
        current_app.logger.exception("admin sales import failed")
        flash("Couldn't import that file — please check the format and try again.", "error")
    return redirect(url_for("admin.record_sale"))


# ---------------------------------------------------------------- customer directory (company-wide)
@bp.route("/customers")
@admin_required
def customers():
    """Company-wide repeat-customer directory (admin counterpart of branch.customers()).

    Built from sales.customer_name/customer_address grouped by exact name; customers from
    several branches fold into one row. Address is the earliest sale's; walk-ins are excluded.
    """
    rows = query(
        """SELECT s.customer_name AS name, s.customer_address AS address,
                  agg.branch_count, agg.branches, agg.purchase_count,
                  agg.total_units, agg.total_bulk_ml, agg.total_spent, agg.last_purchase_at,
                  last_b.branch_name AS last_branch_name
           FROM (
               SELECT sa.customer_name,
                      COUNT(DISTINCT sa.branch_id) AS branch_count,
                      GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches,
                      COUNT(*) AS purchase_count,
                      -- Bulk sales store qty_sold in mL, so they're totalled
                      -- separately rather than added to the bottle count.
                      COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                      COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                      COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                      MIN(sa.sold_at) AS first_sold_at,
                      MAX(sa.sold_at) AS last_purchase_at
               FROM sales sa
               JOIN branches b ON sa.branch_id = b.branch_id
               JOIN sale_catalog p ON p.sku = sa.sku
               WHERE sa.customer_name IS NOT NULL AND sa.customer_name <> ''
               GROUP BY sa.customer_name
           ) agg
           JOIN sales s
             ON s.customer_name = agg.customer_name AND s.sold_at = agg.first_sold_at
           JOIN sales last_sale
             ON last_sale.customer_name = agg.customer_name AND last_sale.sold_at = agg.last_purchase_at
           JOIN branches last_b ON last_sale.branch_id = last_b.branch_id
           ORDER BY agg.total_spent DESC"""
    )
    # Several lines from one receipt share a sold_at, so the timestamp joins can
    # repeat a customer; the aggregates are per customer, so keep the first row.
    _seen_names = set()
    rows = [r for r in rows if not (r["name"] in _seen_names or _seen_names.add(r["name"]))]

    totals = query(
        """SELECT COUNT(DISTINCT customer_name) AS customer_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_named_revenue
           FROM sales WHERE customer_name IS NOT NULL AND customer_name <> ''""", fetchone=True,
    )
    multi_branch_count = sum(1 for r in rows if r["branch_count"] > 1)

    return render_template(
        "admin/customers.html", rows=rows, totals=totals,
        multi_branch_count=multi_branch_count,
    )


# ---------------------------------------------------------------- credit purchases
@bp.route("/credit-purchases")
@admin_required
def credit_purchases():
    """Outstanding Credit across every branch, grouped by branch + buyer, filterable by branch.

    Settled credit (sale_stock.settle_credit) drops off once cleared.
    """
    branch_filter = request.args.get("branch_id", "all")
    branches = query("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY is_hq DESC, branch_name")

    sql = """SELECT s.branch_id, b.branch_name,
                    COALESCE(s.buyer_name, bu.username) AS buyer_name,
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_units,
                    COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                    COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS total_amount,
                    MAX(s.sold_at) AS last_taken_at
             FROM sales s
             JOIN sale_catalog p ON p.sku = s.sku
             JOIN branches b ON b.branch_id = s.branch_id
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
             WHERE s.payment_method = 'Credit' AND s.credit_settled_at IS NULL"""
    params = []
    if branch_filter.isdigit():
        sql += " AND s.branch_id = %s"
        params.append(int(branch_filter))
    sql += """ GROUP BY s.branch_id, b.branch_name, COALESCE(s.buyer_name, bu.username)
               ORDER BY total_amount DESC"""
    rows = query(sql, tuple(params))

    totals_sql = """SELECT COUNT(*) AS transaction_count,
                            COALESCE(SUM(qty_sold * unit_price), 0) AS total_amount
                     FROM sales WHERE payment_method = 'Credit' AND credit_settled_at IS NULL"""
    totals_params = ()
    if branch_filter.isdigit():
        totals_sql += " AND branch_id = %s"
        totals_params = (int(branch_filter),)
    totals = query(totals_sql, totals_params, fetchone=True)

    return render_template(
        "admin/credit_purchases.html", rows=rows, totals=totals,
        branches=branches, branch_filter=branch_filter,
    )


# ---------------------------------------------------------------- sales history / void
@bp.route("/sales-history")
@admin_required
def sales_history():
    """Every location's sales, newest first, where HQ voids one; recent voids listed below."""
    branch_filter = request.args.get("branch_id", "all")
    branches = query("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY is_hq DESC, branch_name")
    sql = """SELECT s.*, b.branch_name, b.is_hq, p.item_name, p.variant, p.unit,
                    COALESCE(s.buyer_name, bu.username) AS buyer_username
             FROM sales s
             JOIN branches b ON b.branch_id = s.branch_id
             JOIN sale_catalog p ON p.sku = s.sku
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id"""
    params = ()
    if branch_filter.isdigit():
        sql += " WHERE s.branch_id = %s"
        params = (int(branch_filter),)
    sql += " ORDER BY s.sold_at DESC, s.sale_id DESC LIMIT 300"
    sales = sale_entry.annotate_receipts(query(sql, params))
    voids = query(
        """SELECT v.*, b.branch_name FROM sale_voids v JOIN branches b ON b.branch_id = v.branch_id
           ORDER BY v.voided_at DESC LIMIT 50"""
    )
    return render_template("admin/sales_history.html", sales=sales, voids=voids,
                           branches=branches, branch_filter=branch_filter)


@bp.route("/sales/<int:sale_id>/void", methods=["POST"])
@admin_required
def void_sale_route(sale_id):
    """HQ can void any location's sale — see sale_stock.void_sale()."""
    # Only ever back to an admin page (never an arbitrary URL from the form).
    next_url = request.form.get("next") or ""
    if not (next_url.startswith("/admin/") and not next_url.startswith("//")):
        next_url = url_for("admin.sales_history")
    back = redirect(next_url)
    reason = (request.form.get("reason") or "").strip()[:255]
    if not reason:
        flash("Give a reason for voiding this sale.", "error")
        return back
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = void_sale(cur, sale_id=sale_id, reason=reason,
                             user_id=session.get("user_id"), username=session.get("username"))
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return back
    except Exception:
        current_app.logger.exception("void sale %s failed", sale_id)
        flash("Couldn't void that sale — please try again.", "error")
        return back
    notify_admin_and_branch(sale["branch_id"], ["inventory", "sales", "movement_logs"])
    log_action("void_sale", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"{sale['sale_type']} × {sale['qty_sold']} · {reason}"[:255])
    flash(f"Voided the {sale['sale_type'].lower()} of {sale['item_name']} — its stock was returned.", "success")
    return back
