import decimal

from flask import (
    flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    FORMULA_UNITS, ValidationError,
    parse_non_negative_decimal,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- unit formulas (COGS)
@bp.route("/formulas")
@admin_required
def formulas():
    """Cost-of-goods settings: Bulk/Refill rate per mL and flat base cost per Bottled size.

    Formula items per size are reference only; production() charges the base cost.
    References come from active batches that targeted that size (none if untargeted).
    """
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    # Bulk/Refill reference: average all-in cost per mL across active batches (same
    # figure as the Bulk Batches page). Informational; Save rate is manual.
    active_batches_for_rate = query(
        "SELECT total_cost, total_volume_ml FROM bulk_batches WHERE remaining_ml > 0")
    reference_rate_per_ml = None
    reference_rate_batch_count = len(active_batches_for_rate)
    if active_batches_for_rate:
        all_in_rates = [
            b["total_cost"] / b["total_volume_ml"]
            for b in active_batches_for_rate if b["total_volume_ml"]
        ]
        if all_in_rates:
            reference_rate_per_ml = (
                sum(all_in_rates, decimal.Decimal("0")) / len(all_in_rates)
            ).quantize(decimal.Decimal("0.0001"))
    cogs_rows = query(
        "SELECT unit, base_cost_per_unit FROM unit_cogs_settings")
    cogs_by_unit = {r["unit"]: r["base_cost_per_unit"] for r in cogs_rows}
    # Per-size reference: averaged cost_per_new/refill_bottle of active batches that
    # targeted this size; none if no batch did.
    target_batches = query(
        """SELECT batch_id, target_unit, target_bottle_qty, total_cost
           FROM bulk_batches
           WHERE remaining_ml > 0 AND target_unit IS NOT NULL AND target_bottle_qty > 0"""
    )
    liquid_subtotal_by_batch = {}
    if target_batches:
        placeholders = ",".join(["%s"] * len(target_batches))
        liquid_rows = query(
            f"""SELECT batch_id, SUM(line_cost) AS liquid_subtotal
                FROM bulk_batch_materials
                WHERE is_packaging = FALSE AND batch_id IN ({placeholders})
                GROUP BY batch_id""",
            tuple(b["batch_id"] for b in target_batches),
        )
        liquid_subtotal_by_batch = {
            r["batch_id"]: r["liquid_subtotal"] for r in liquid_rows}

    refs_by_unit = {}
    for b in target_batches:
        liquid_subtotal = liquid_subtotal_by_batch.get(
            b["batch_id"], decimal.Decimal("0"))
        cost_per_new = (b["total_cost"] / b["target_bottle_qty"]).quantize(
            decimal.Decimal("0.01"))
        cost_per_refill = (liquid_subtotal / b["target_bottle_qty"]).quantize(
            decimal.Decimal("0.01"))
        refs_by_unit.setdefault(b["target_unit"], []).append(
            (cost_per_new, cost_per_refill))

    # FORMULA_UNITS is PRODUCT_UNITS minus BULK; every size gets a row (default 0).
    units = []
    for u in FORMULA_UNITS:
        refs = refs_by_unit.get(u)
        reference_cost_per_new = None
        reference_cost_per_refill = None
        reference_batch_count = len(refs) if refs else 0
        if refs:
            reference_cost_per_new = (
                sum((r[0] for r in refs), decimal.Decimal("0")) / len(refs)
            ).quantize(decimal.Decimal("0.01"))
            reference_cost_per_refill = (
                sum((r[1] for r in refs), decimal.Decimal("0")) / len(refs)
            ).quantize(decimal.Decimal("0.01"))
        units.append({
            "unit": u,
            "base_cost_per_unit": cogs_by_unit.get(u, decimal.Decimal("0")),
            "reference_cost_per_new": reference_cost_per_new,
            "reference_cost_per_refill": reference_cost_per_refill,
            "reference_batch_count": reference_batch_count,
        })
    return render_template(
        "admin/formulas.html",
        units=units,
        bulk_rate=bulk_rate,
        reference_rate_per_ml=reference_rate_per_ml,
        reference_rate_batch_count=reference_rate_batch_count,
    )


@bp.route("/formulas/save-bulk-rate", methods=["POST"])
@admin_required
def save_bulk_rate():
    """Update the single shared ₱-per-mL rate every Bulk/Refill product is
    produced/sold at (see bulk_rate_settings in schema.sql). Production
    still lets this be overridden per batch (raw material costs
    fluctuate); Record Sale always uses this rate as-is.
    """
    try:
        rate = parse_non_negative_decimal(
            request.form.get("rate_per_ml"), "Rate per mL")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    execute("UPDATE bulk_rate_settings SET rate_per_ml = %s WHERE id = 1", (rate,))
    notify_all(["materials"])
    log_action("save_bulk_rate", details=f"₱{rate:.4f}/mL")
    flash(f"Bulk/Refill rate set to ₱{rate:.4f} per mL.", "success")
    return redirect(url_for("admin.formulas"))


@bp.route("/formulas/save-unit-cogs", methods=["POST"])
@admin_required
def save_unit_cogs():
    """Set the flat base cost of goods for one Bottled packaging size
    (see unit_cogs_settings in schema.sql) — this is what production()
    logs as cogs_per_unit for that size, independent of whichever bulk
    batch a run happens to draw from (a batch's own real cost/mL stays
    informational, shown on the Bulk Batches page only).
    """
    unit = request.form.get("unit")
    if unit not in FORMULA_UNITS:
        flash("Select a valid packaging size.", "error")
        return redirect(url_for("admin.formulas"))

    try:
        base_cost = parse_non_negative_decimal(
            request.form.get("base_cost_per_unit"), "Base cost per unit")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    execute(
        """INSERT INTO unit_cogs_settings (unit, base_cost_per_unit) VALUES (%s, %s)
           ON DUPLICATE KEY UPDATE base_cost_per_unit = VALUES(base_cost_per_unit)""",
        (unit, base_cost),
    )
    notify_all(["materials"])
    log_action("save_unit_cogs", target=unit,
               details=f"₱{base_cost:,.4f}/unit")
    flash(f"{unit} base cost of goods set to ₱{base_cost:,.4f} per unit.", "success")
    return redirect(url_for("admin.formulas"))
