
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, send_file, session, url_for,
)

from heaven_angel.db import TransactionAborted, execute, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.receipts import build_receipt_pdf
from heaven_angel.sockets import notify_admin_and_branch, notify_bell
from heaven_angel.utils import (
    ValidationError,
    parse_non_negative_int,
)

from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


# A stock request is a delivery: one branch cart with any number of SKUs. The list shows
# one row per delivery; line items appear on its review page and receipt PDF.
# Requests page: one row per delivery, filterable by status.
@bp.route("/requests")
@admin_required
def requests_list():
    status_filter = request.args.get("status", "all")
    sql = """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at, sr.branch_id,
                    b.branch_name,
                    COUNT(sri.item_id) AS item_count,
                    COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                    COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value,
                    COALESCE(SUM(sri.received_qty), 0) AS total_received,
                    COALESCE(SUM(sri.damaged_qty), 0) AS total_damaged
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id"""
    params = ()
    if status_filter != "all":
        sql += " WHERE sr.status = %s"
        params = (status_filter,)
    sql += " GROUP BY sr.request_id ORDER BY FIELD(sr.status,'Pending','Partially Fulfilled','In Transit','Fulfilled','Rejected'), sr.requested_at DESC"
    stock_requests = query(sql, params)
    return render_template("admin/requests.html", stock_requests=stock_requests, status_filter=status_filter)


@bp.route("/requests/<int:request_id>")
@admin_required
def review_request(request_id):
    """Line items of one delivery; Pending ones get an editable dispatch qty, others are read-only."""
    req = query(
        """SELECT sr.*, b.branch_name
           FROM stock_requests sr JOIN branches b ON sr.branch_id = b.branch_id
           WHERE sr.request_id = %s""",
        (request_id,), fetchone=True,
    )
    if not req:
        abort(404)
    items = query(
        """SELECT sri.*, p.item_name, p.variant, p.unit,
                  GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0) AS remaining_qty
           FROM stock_request_items sri JOIN products p ON sri.sku = p.sku
           WHERE sri.request_id = %s ORDER BY p.item_name""",
        (request_id,),
    )
    total_value = sum(
        (item["requested_qty"] * item["unit_price"]) for item in items)
    shipments = query(
        """SELECT ss.shipment_id, ss.status, ss.dispatched_at, ss.received_at,
                  COALESCE(SUM(ssi.dispatched_qty), 0) AS total_dispatched
           FROM stock_request_shipments ss
           LEFT JOIN stock_request_shipment_items ssi ON ssi.shipment_id = ss.shipment_id
           WHERE ss.request_id = %s
           GROUP BY ss.shipment_id ORDER BY ss.shipment_id DESC""",
        (request_id,),
    )
    return render_template("admin/request_detail.html", req=req, items=items, total_value=total_value, shipments=shipments)


@bp.route("/requests/<int:request_id>/dispatch", methods=["POST"])
@admin_required
def dispatch_request(request_id):
    """Create a shipment for some or all outstanding items.

    Reads item_id[] / dispatched_qty[] pairs from request_detail.html; zero skips a line.
    """
    item_ids = request.form.getlist("item_id[]")
    raw_qtys = request.form.getlist("dispatched_qty[]")

    if not item_ids or len(item_ids) != len(raw_qtys) or len(set(item_ids)) != len(item_ids):
        flash("Nothing to dispatch.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    try:
        requested_dispatch = {}
        for raw_id, raw_qty in zip(item_ids, raw_qtys):
            requested_dispatch[int(raw_id)] = parse_non_negative_int(
                raw_qty, "Dispatched quantity")
    except (TypeError, ValueError, ValidationError) as err:
        flash(str(err) if isinstance(err, ValidationError)
              else "Invalid item in dispatch request.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    if all(qty == 0 for qty in requested_dispatch.values()):
        flash("Dispatch at least one item.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    req = None
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            # Lock the request, its items and the HQ stock rows so concurrent dispatches
            # can't over-dispatch HQ stock below zero.
            cur.execute(
                "SELECT * FROM stock_requests WHERE request_id = %s FOR UPDATE", (request_id,))
            req = cur.fetchone()
            if not req or req["status"] not in ("Pending", "Partially Fulfilled"):
                # Plain return is safe here: first check after locking, before any writes. It also
                # redirects to the list, unlike the shared TransactionAborted handler.
                cur.close()
                flash("This request can no longer be dispatched.", "error")
                return redirect(url_for("admin.requests_list"))

            cur.execute(
                "SELECT * FROM stock_request_items WHERE request_id = %s FOR UPDATE",
                (request_id,),
            )
            items_by_id = {row["item_id"]: row for row in cur.fetchall()}

            # Pure validation, no writes yet — safe as plain returns, but
            # raised for consistency with the write loop below.
            for item_id, qty in requested_dispatch.items():
                item = items_by_id.get(item_id)
                if item is None:
                    cur.close()
                    raise TransactionAborted(
                        "Invalid item in dispatch request.")
                remaining = item["requested_qty"] - (item["received_qty"] or 0)
                if qty > remaining:
                    cur.close()
                    raise TransactionAborted(
                        "Dispatched quantity can't exceed the remaining quantity.")

            cur.execute(
                "INSERT INTO stock_request_shipments (request_id, dispatched_by_user_id) VALUES (%s, %s)",
                (request_id, session.get("user_id")),
            )
            shipment_id = cur.lastrowid

            dispatched_any = False
            # This loop writes as it goes, so aborts must raise TransactionAborted (a plain return
            # would let transaction() commit earlier writes; see db.py).
            # SKU order, so two overlapping deliveries lock stock rows in the same order.
            for item_id, qty in sorted(requested_dispatch.items(), key=lambda kv: items_by_id[kv[0]]["sku"]):
                item = items_by_id[item_id]
                if qty == 0:
                    continue

                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                    (HQ_BRANCH_ID, item["sku"]),
                )
                hq_row = cur.fetchone()
                if not hq_row or hq_row["stock_qty"] < qty:
                    cur.close()
                    raise TransactionAborted(
                        f"Not enough HQ warehouse stock to dispatch {item['sku']}.")

                before_qty = hq_row["stock_qty"]
                after_qty = before_qty - qty
                cur.execute(
                    "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                    (after_qty, HQ_BRANCH_ID, item["sku"]),
                )
                cur.execute(
                    "UPDATE stock_request_items SET dispatched_qty = COALESCE(dispatched_qty, 0) + %s WHERE item_id = %s",
                    (qty, item_id),
                )
                cur.execute(
                    "INSERT INTO stock_request_shipment_items (shipment_id, request_item_id, dispatched_qty) VALUES (%s, %s, %s)",
                    (shipment_id, item_id, qty),
                )
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                       VALUES (%s, %s, %s, 'DISPATCH', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                     (HQ_BRANCH_ID, item["sku"], -qty, f"Dispatched on {req['delivery_number']} shipment #{shipment_id}",
                     session.get("user_id"), request_id, before_qty, after_qty),
                )
                dispatched_any = True

            if not dispatched_any:
                cur.close()
                raise TransactionAborted("Dispatch at least one item.")

            cur.execute(
                "UPDATE stock_requests SET status = 'In Transit' WHERE request_id = %s",
                (request_id,),
            )
            cur.close()
        notify_admin_and_branch(
            req["branch_id"], ["requests", "inventory", "movement_logs"])
        notify_bell(f"Shipment #{shipment_id} for {req['delivery_number']} is on its way from HQ.",
                    room=f"branch:{req['branch_id']}", level="success")
        flash(
            f"{req['delivery_number']} shipment #{shipment_id} dispatched and is now in transit.", "success")
    except TransactionAborted as err:
        flash(str(err), "error")
        return redirect(url_for("admin.review_request", request_id=request_id))
    except Exception:
        current_app.logger.exception(
            "dispatch_request failed for request_id=%s", request_id)
        flash("Couldn't dispatch this request — please try again.", "error")
    return redirect(url_for("admin.requests_list"))


@bp.route("/requests/<int:request_id>/receipt")
@admin_required
def request_receipt(request_id):
    """Downloadable PDF for any branch's Fulfilled request (receipts.py)."""
    pdf_buffer, req = build_receipt_pdf(request_id)
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"GR-{request_id:06d}.pdf",
    )


# POST: reject a Pending request.
@bp.route("/requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_request(request_id):
    req = query("SELECT branch_id, delivery_number FROM stock_requests WHERE request_id = %s",
                (request_id,), fetchone=True)
    if not req:
        flash("That request no longer exists.", "error")
        return redirect(url_for("admin.requests_list"))

    # rowcount tells a real reject (only Pending qualifies) from a double-submit or race.
    _, rowcount = execute(
        "UPDATE stock_requests SET status = 'Rejected' WHERE request_id = %s AND status = 'Pending'",
        (request_id,),
    )
    if rowcount == 0:
        flash(
            f"{req['delivery_number']} can't be rejected: it's no longer Pending.", "error",
        )
        return redirect(url_for("admin.requests_list"))

    notify_admin_and_branch(req["branch_id"], "requests")
    notify_bell(f"Delivery {req['delivery_number']} was rejected by HQ.",
                room=f"branch:{req['branch_id']}", level="warning")
    flash(f"{req['delivery_number']} rejected.", "success")
    return redirect(url_for("admin.requests_list"))
