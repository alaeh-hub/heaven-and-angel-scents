
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    PARTNER_TYPES,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- partners (distributors & resellers)
@bp.route("/partners", methods=["GET", "POST"])
@admin_required
def partners():
    """Partner directory (distributors/resellers HQ sells to in bulk).

    "Total package sales" = sum of order_amount over a partner's Closed inquiries
    (New/Contacted are leads). partner_investments is no longer read.
    """
    if request.method == "POST":
        return_type = request.form.get("return_type", "all")
        partner_type = request.form.get("partner_type", "").strip()
        name = request.form.get("partner_name", "").strip()
        contact_person = request.form.get("contact_person", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if partner_type not in PARTNER_TYPES:
            flash("Select whether this is a Distributor or a Reseller.", "error")
        elif not name:
            flash("Partner name is required.", "error")
        elif len(name) > 150 or any(len(v or "") > 255 for v in (contact_person, phone, email, address)):
            flash("One of the fields is too long.", "error")
        else:
            try:
                execute(
                    """INSERT INTO partners
                           (partner_type, partner_name, contact_person, phone, email, address, notes)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (partner_type, name, contact_person, phone, email, address, notes),
                )
            except Exception:
                current_app.logger.exception("add partner failed for %s", name)
                flash("Couldn't save that partner - check the field lengths.", "error")
                return redirect(url_for("admin.partners", type=return_type))
            notify_all(["partners"])
            log_action("add_partner", target=name, details=partner_type)
            flash(f"{name} added as a {partner_type.lower()}.", "success")
        return redirect(url_for("admin.partners", type=return_type))

    type_filter = request.args.get("type", "all")
    where_sql = ""
    params = ()
    if type_filter in PARTNER_TYPES:
        where_sql = "WHERE p.partner_type = %s"
        params = (type_filter,)

    # order_amount counts only when Closed; the AND is in the LEFT JOIN so partners with
    # no sales still appear.
    partner_list = query(
        f"""SELECT p.*,
                   COALESCE(SUM(pinq.order_amount), 0) AS total_invested,
                   COUNT(pinq.inquiry_id) AS investment_count,
                   MAX(pinq.created_at) AS last_investment_at
            FROM partners p
            LEFT JOIN partner_inquiries pinq
                ON pinq.partner_id = p.partner_id AND pinq.status = 'Closed'
            {where_sql}
            GROUP BY p.partner_id
            ORDER BY p.partner_name""",
        params,
    )

    totals = query(
        """SELECT
               COALESCE(SUM(pinq.order_amount), 0) AS total_all,
               COALESCE(SUM(CASE WHEN p.partner_type = 'Distributor' THEN pinq.order_amount ELSE 0 END), 0)
                   AS total_distributor,
               COALESCE(SUM(CASE WHEN p.partner_type = 'Reseller' THEN pinq.order_amount ELSE 0 END), 0)
                   AS total_reseller
           FROM partner_inquiries pinq
           JOIN partners p ON pinq.partner_id = p.partner_id
           WHERE pinq.status = 'Closed'""",
        fetchone=True,
    )
    counts = query(
        """SELECT
               COALESCE(SUM(CASE WHEN partner_type = 'Distributor' THEN 1 ELSE 0 END), 0)
                   AS distributor_count,
               COALESCE(SUM(CASE WHEN partner_type = 'Reseller' THEN 1 ELSE 0 END), 0)
                   AS reseller_count
           FROM partners""",
        fetchone=True,
    )

    portal_link = url_for(
        "portal.packages", slug=current_app.config.get("PARTNER_PORTAL_SLUG", ""), _external=True,
        _scheme="https",
    )

    return render_template(
        "admin/partners.html",
        partner_list=partner_list, type_filter=type_filter,
        totals=totals, counts=counts, partner_types=PARTNER_TYPES,
        portal_link=portal_link,
    )


@bp.route("/partners/<int:partner_id>")
@admin_required
def partner_detail(partner_id):
    """One partner's full inquiry history, exactly as submitted (the permanent record)."""
    partner = query("SELECT * FROM partners WHERE partner_id = %s",
                    (partner_id,), fetchone=True)
    if not partner:
        abort(404)

    inquiries = query(
        """SELECT * FROM partner_inquiries
           WHERE partner_id = %s ORDER BY created_at DESC""",
        (partner_id,),
    )

    return render_template(
        "admin/partner_detail.html", partner=partner, inquiries=inquiries,
    )

# Partners no longer "invest": money comes from Closed package orders (see partners()).
# partner_investments is kept in place but nothing reads or writes it.
