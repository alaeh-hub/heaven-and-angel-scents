import decimal
from dataclasses import dataclass

from flask import (
    current_app, flash, redirect, render_template,
    request, session, url_for,
)

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_admin, notify_all
from heaven_angel.utils import (
    BATCH_VOLUME_UNITS, BOTTLE_UNITS, ML_PER_BATCH_UNIT, MATERIAL_UNITS, ValidationError,
    base_code_from_sku, bottle_size_ml, compatible_material_units, consume_form_token, convert_material_qty,
    issue_form_token,
    parse_optional_id,
    parse_optional_text, parse_positive_decimal, parse_positive_int,
)

from heaven_angel.routes.admin.batch_formulas import (
    ROLES, calc_to_json, ensure_cost_columns, formula_for_scent, load_catalog, load_formula_config,
    load_scent_choices, prepare_scent_oils,
)
from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


# A validated Log Production run, ready to be recorded.
@dataclass(frozen=True)
class _ProductionRun:
    sku: str
    batch_code: str | None
    qty: int
    bulk_batch_id: int
    unit: str                   # the bottle size, e.g. 85ML
    bottle_ml: object           # mL of bulk one bottle takes
    cogs_per_unit: decimal.Decimal
    total_cogs: decimal.Decimal


def _plan_production_run(form):
    """Validate the Log Production form and work out the run's cost.

    Raises ValidationError with a message to flash as-is. Checks run in a
    fixed order: units, product, then the bulk batch.
    """
    sku = form.get("sku")
    batch_code = form.get("batch_code", "").strip() or None
    qty = parse_positive_int(form.get("qty_produced"), "Units produced")

    product = query(
        "SELECT sku, item_name, unit FROM products WHERE sku = %s",
        (sku,), fetchone=True,
    )
    if not product:
        raise ValidationError("Select a valid product.")

    # A run must pick a bulk batch (materials go into a batch before bottling; it is what
    # gets deducted). The cost logged is the flat base price per size
    # (unit_cogs_settings), not the batch cost/ml: the batch already booked its real cost.
    # Bulk is not produced here; it is sold by the mL straight from its batch.
    bulk_batch_id = parse_positive_int(form.get("bulk_batch_id"), "Bulk batch")
    bottle_ml = bottle_size_ml(product["unit"])
    cogs_setting = query(
        "SELECT base_cost_per_unit FROM unit_cogs_settings WHERE unit = %s",
        (product["unit"],), fetchone=True,
    )
    if not cogs_setting:
        # Not a blocker (the run is still real), but the cost of goods will be 0.
        flash(f"No cost is set for {product['unit']} bottles, so this run is logged at 0 cost of goods. "
              "Set it under Settings so profit isn't overstated.", "warning")
    cogs_per_unit = (
        cogs_setting["base_cost_per_unit"] if cogs_setting else decimal.Decimal("0")
    ).quantize(decimal.Decimal("0.0001"))
    total_cogs = (cogs_per_unit * qty).quantize(decimal.Decimal("0.01"))
    return _ProductionRun(sku, batch_code, qty, bulk_batch_id, product["unit"], bottle_ml, cogs_per_unit, total_cogs)


def _deduct_packaging(cur, run):
    """Take the bottles' packaging (bottle, sticker, seal...) out of stock.

    Formula batches already had packaging deducted at creation, so only a batch with none
    on record (e.g. opening balance) is handled. Uses the size's own packaging list, else
    the bulk formulas' packaging for their bottle size. Cost of goods stays the flat cost
    per size. Returns the count deducted; raises TransactionAborted.
    """
    cur.execute("SELECT 1 FROM bulk_batch_materials WHERE batch_id = %s AND is_packaging = TRUE LIMIT 1",
                (run.bulk_batch_id,))
    if cur.fetchone():
        return 0
    cur.execute("SELECT material_id, qty_per_unit AS per_bottle FROM unit_formula_items WHERE unit = %s",
                (run.unit,))
    items = cur.fetchall()
    if not items:
        cur.execute("SELECT bottle_unit FROM bulk_formula_settings WHERE id = 1")
        settings = cur.fetchone()
        if settings and settings["bottle_unit"] == run.unit:
            cur.execute(
                """SELECT bfm.material_id, bfp.qty_per_bottle AS per_bottle
                   FROM bulk_formula_packaging bfp
                   JOIN batch_formula_materials bfm ON bfm.role = bfp.role""")
            items = cur.fetchall()
    items = [i for i in items if i["per_bottle"] > 0]
    if not items:
        return 0
    cur.execute(
        f"""SELECT material_id, material_name, unit, stock_qty FROM raw_materials
            WHERE material_id IN ({','.join(['%s'] * len(items))}) FOR UPDATE""",
        tuple(i["material_id"] for i in items))
    stock = {m["material_id"]: m for m in cur.fetchall()}
    for item in items:
        material = stock[item["material_id"]]
        needed = item["per_bottle"] * run.qty
        if material["stock_qty"] < needed:
            raise TransactionAborted(
                f"Not enough {material['material_name']} in stock to package {run.qty} bottle(s): "
                f"{material['stock_qty']:g} {material['unit'].lower()} left, {needed:g} needed.")
    for item in items:
        cur.execute("UPDATE raw_materials SET stock_qty = stock_qty - %s WHERE material_id = %s",
                    (item["per_bottle"] * run.qty, item["material_id"]))
    return len(items)


def _record_production_run(run, user_id):
    """Write the run in one transaction: deduct the bulk batch, take packaging out of
    stock, log the run, add HQ stock, and log the movement and cost of goods.
    Raises TransactionAborted with a message to flash."""
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)

        # Lock and check inside the transaction so concurrent runs can't pass a stale
        # remaining_ml check.
        cur.execute(
            "SELECT batch_id, remaining_ml, base_code, target_unit, scent_name "
            "FROM bulk_batches WHERE batch_id = %s FOR UPDATE",
            (run.bulk_batch_id,),
        )
        batch = cur.fetchone()
        if not batch:
            raise TransactionAborted("Select a valid bulk batch.")
        # A batch with a scent code / bottle size must match the product, or the run would
        # book one scent as another and mis-take packaging. Older batches without them pass.
        if batch["base_code"] and batch["base_code"] != base_code_from_sku(run.sku, run.unit):
            raise TransactionAborted(
                f"That batch is {batch['scent_name']} ({batch['base_code']}) and can't be "
                f"bottled as {run.sku}.")
        if batch["target_unit"] and batch["target_unit"] != run.unit:
            raise TransactionAborted(
                f"That batch was made for {batch['target_unit']} bottles, not {run.unit}.")
        needed_ml = run.bottle_ml * run.qty
        if batch["remaining_ml"] < needed_ml:
            raise TransactionAborted(
                f"That batch only has {batch['remaining_ml']:.3f} mL left — "
                f"this run needs {needed_ml:.3f} mL."
            )
        cur.execute(
            "UPDATE bulk_batches SET remaining_ml = remaining_ml - %s WHERE batch_id = %s",
            (needed_ml, run.bulk_batch_id),
        )
        packaged = _deduct_packaging(cur, run)

        cur.execute(
            "INSERT INTO production_logs (sku, batch_code, qty_produced) VALUES (%s, %s, %s)",
            (run.sku, run.batch_code, run.qty),
        )
        log_id = cur.lastrowid
        cur.execute(
            """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
            (HQ_BRANCH_ID, run.sku, run.qty),
        )
        cur.execute(
            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
            (HQ_BRANCH_ID, run.sku),
        )
        row = cur.fetchone()
        after_qty = row["stock_qty"] if row else None
        before_qty = (after_qty - run.qty) if after_qty is not None else None
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, before_qty, after_qty)
               VALUES (%s, %s, %s, 'PRODUCTION', %s, %s, 'PRODUCTION_LOG', %s, %s)""",
            (HQ_BRANCH_ID, run.sku, run.qty,
             f"Batch {run.batch_code}" if run.batch_code else "Production run",
             user_id, before_qty, after_qty),
        )
        cur.execute(
            """INSERT INTO cogs_logs
               (sku, production_log_id, bulk_batch_id, qty_produced, cogs_per_unit, total_cogs,
                created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (run.sku, log_id, run.bulk_batch_id, run.qty, run.cogs_per_unit, run.total_cogs, user_id),
        )
        cur.close()
    return packaged


# Data for the Log Production page: products, batches, recent runs.
def _production_page_data():
    # Each Bottled product carries its size's flat base cost so the form can preview the
    # cost of goods.
    products_list = query(
        """SELECT p.sku, p.item_name, p.unit, p.category,
                  COALESCE(ucs.base_cost_per_unit, 0) AS cogs_per_unit
           FROM products p
           LEFT JOIN unit_cogs_settings ucs ON ucs.unit = p.unit
           ORDER BY p.item_name"""
    )
    # Bottled runs must pick a batch with mL left; cost of goods comes from
    # unit_cogs_settings, not the batch cost_per_ml.
    active_batches = query(
        """SELECT batch_id, batch_code, scent_name, remaining_ml
           FROM bulk_batches WHERE remaining_ml > 0
           ORDER BY scent_name, created_at DESC"""
    )
    # Grouped/summed in case an older run used more than one batch.
    logs = query(
        """SELECT pl.*, p.item_name, p.unit, p.category,
                  cogs.cogs_per_unit, cogs.total_cogs
           FROM production_logs pl
           JOIN products p ON pl.sku = p.sku
           LEFT JOIN (
               SELECT production_log_id,
                      SUM(total_cogs) AS total_cogs,
                      AVG(cogs_per_unit) AS cogs_per_unit
               FROM cogs_logs
               WHERE production_log_id IS NOT NULL
               GROUP BY production_log_id
           ) cogs ON cogs.production_log_id = pl.log_id
           ORDER BY pl.produced_at DESC LIMIT 40"""
    )
    hq_stock = query(
        """SELECT p.sku, p.item_name, p.variant, p.category, p.unit, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    # Counted by base code, same as the Products page.
    for row in hq_stock:
        row["base_code"] = base_code_from_sku(row["sku"], row["unit"])
    return {"products": products_list, "logs": logs, "hq_stock": hq_stock,
            "active_batches": active_batches}


@bp.route("/production", methods=["GET", "POST"])
@admin_required
def production():
    """Log a production run and its cost of goods in one step (see cogs_logs).

    Bulk/Refill: no fixed size, costed at a hand-typed rate per mL. Bottled sizes pick a
    bulk batch whose remaining_ml is deducted (qty x size mL); the cost logged is the flat
    base price per size (unit_cogs_settings), not the batch cost_per_ml.
    """
    if request.method == "POST":
        if not consume_form_token("log_production"):
            flash(
                "This run was already logged, or the form expired — check the log below before resending.", "error")
            return redirect(url_for("admin.production"))

        try:
            run = _plan_production_run(request.form)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.production"))

        try:
            packaged = _record_production_run(run, session.get("user_id"))
            notify_admin(["production", "inventory", "movement_logs"])
            notify_all(["materials"])
            unit_label = "units"
            flash(
                f"Logged {run.qty} {unit_label} produced — ₱{run.total_cogs:,.2f} in cost of goods — and added to HQ warehouse stock."
                + (" Packaging stock deducted." if packaged else ""),
                "success",
            )
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "production logging failed for sku=%s", run.sku)
            flash("Couldn't log this production run — please try again.", "error")
        return redirect(url_for("admin.production"))

    return render_template(
        "admin/production.html", **_production_page_data(),
        form_token=issue_form_token("log_production"),
    )


# ---------------------------------------------------------------- bulk batches
@bp.route("/bulk-batches")
@admin_required
def bulk_batches():
    """Bulk batches: mix raw materials into one batch, then bottle out of it later.

    Shows each batch's real cost, remaining mL and how many bottles of each size it can
    still fill. Cost/mL and the all-in cost per bottle are informational only; production()
    charges the flat base price per size.
    """
    ensure_cost_columns()
    materials_list = query(
        """SELECT material_id, material_name, unit, stock_qty, cost_per_unit
           FROM raw_materials ORDER BY material_name"""
    )
    batch_rows = query(
        """SELECT batch_id, batch_code, scent_name, input_qty, input_unit,
                  total_volume_ml, total_cost, cost_per_ml, remaining_ml,
                  notes, target_unit, target_bottle_qty, formula_label, created_at,
                  chemist_cost, labor_cost
           FROM bulk_batches ORDER BY created_at DESC"""
    )
    ingredient_rows = query(
        """SELECT bbm.batch_id, bbm.qty_used, bbm.qty_used_unit, bbm.cost_per_unit_snapshot,
                  bbm.line_cost, bbm.is_packaging,
                  rm.material_name, rm.unit AS material_unit
           FROM bulk_batch_materials bbm
           JOIN raw_materials rm ON rm.material_id = bbm.material_id
           ORDER BY bbm.is_packaging, rm.material_name"""
    )
    ingredients_by_batch = {}
    for row in ingredient_rows:
        ingredients_by_batch.setdefault(row["batch_id"], []).append(row)

    batches = []
    for b in batch_rows:
        # A batch with a target size already had that size's packaging deducted at creation,
        # and leftover mL only refills that size (no new bottle): show that one size,
        # liquid-only (matches cost_per_refill_bottle).
        if b["target_unit"]:
            unit = b["target_unit"]
            size_ml = bottle_size_ml(unit)
            liquid_cost = (b["cost_per_ml"] * size_ml).quantize(
                decimal.Decimal("0.0001"))
            yields = [{
                "unit": unit,
                "bottle_count": int(b["remaining_ml"] // size_ml),
                "liquid_cost": liquid_cost,
                "packaging_cost": decimal.Decimal("0"),
                "total_cost_per_bottle": liquid_cost.quantize(decimal.Decimal("0.01")),
                "refill_only": True,
            }]
        else:
            yields = []
            for unit in BOTTLE_UNITS:
                size_ml = bottle_size_ml(unit)
                liquid_cost = (b["cost_per_ml"] * size_ml).quantize(
                    decimal.Decimal("0.0001"))
                packaging_cost = decimal.Decimal("0")
                yields.append({
                    "unit": unit,
                    "bottle_count": int(b["remaining_ml"] // size_ml),
                    "liquid_cost": liquid_cost,
                    "packaging_cost": packaging_cost,
                    "total_cost_per_bottle": (liquid_cost + packaging_cost).quantize(
                        decimal.Decimal("0.01")),
                    "refill_only": False,
                })
        b["yields"] = yields
        b["depleted"] = b["remaining_ml"] <= 0
        ingredients = ingredients_by_batch.get(b["batch_id"], [])
        b["ingredients"] = ingredients

        # All-in cost per mL = total_cost (liquid + packaging) / total_volume_ml, the figure
        # shown to users. Kept separate from the stored cost_per_ml, which stays liquid-only
        # (yield/refill math and formulas() averages depend on that).
        b["all_in_cost_per_ml"] = (
            b["total_cost"] / b["total_volume_ml"]).quantize(decimal.Decimal("0.000001")) \
            if b["total_volume_ml"] else decimal.Decimal("0")

        # This batch's own plan (when a target size was picked at creation): liquid and
        # packaging subtotals from its own materials. Cost per NEW bottle = total_cost / yield
        # (liquid + packaging + chemist + labor); per REFILL = liquid only / yield.
        if b["target_unit"] and b["target_bottle_qty"]:
            liquid_subtotal = sum(
                (i["line_cost"] for i in ingredients if not i["is_packaging"]),
                decimal.Decimal("0"))
            packaging_subtotal = sum(
                (i["line_cost"] for i in ingredients if i["is_packaging"]),
                decimal.Decimal("0"))
            b["liquid_subtotal"] = liquid_subtotal
            b["packaging_subtotal"] = packaging_subtotal
            b["cost_per_new_bottle"] = (
                b["total_cost"] / b["target_bottle_qty"]).quantize(decimal.Decimal("0.01"))
            b["cost_per_refill_bottle"] = (
                liquid_subtotal / b["target_bottle_qty"]).quantize(decimal.Decimal("0.01"))
        batches.append(b)

    config = load_formula_config()
    resolved, catalog, missing_roles = load_catalog(materials_list, config)
    scent_oils = prepare_scent_oils(materials_list, config)
    return render_template(
        "admin/bulk_batches.html",
        materials=materials_list,
        # Built-in formulas x sizes, costed live (batch_formulas.py); produce_batch_formula()
        # recomputes server-side.
        formula_specs={f: {"scents": spec["scents"], "sizes": list(spec["sizes"])}
                       for f, spec in config["formulas"].items()},
        formula_catalog_json={
            f: {str(size): calc_to_json(calc) for size, calc in sizes.items()}
            for f, sizes in catalog.items()
        },
        scent_choices=load_scent_choices(config),
        scent_oils_json={code: {"rate": float(r["cost_per_unit"]), "stock": float(r["stock_qty"])}
                         for code, r in scent_oils.items()},
        formula_roles=[
            {"role": role, "label": label,
             "material_id": resolved[role]["material_id"] if role in resolved else None}
            for role, (label, _u, _k) in ROLES.items()
        ],
        missing_roles=missing_roles,
        batches=batches,
        batch_units=BATCH_VOLUME_UNITS,
        # JSON for the create-batch form's live yield preview (same math as above, in the browser).
        bottle_ml_map={u: float(bottle_size_ml(u)) for u in BOTTLE_UNITS},
        ml_per_batch_unit={u: float(v) for u, v in ML_PER_BATCH_UNIT.items()},
        # Units the "Qty used" field accepts per material (see utils.compatible_material_units).
        material_unit_options={
            u: compatible_material_units(u) for u in MATERIAL_UNITS},
        form_token=issue_form_token("create_bulk_batch"),
        write_off_reasons=WRITE_OFF_REASONS,
    )


# A validated Create Batch form, ready to be recorded.
@dataclass(frozen=True)
class _BatchPlan:
    scent_name: str
    batch_code: str | None
    input_qty: decimal.Decimal
    input_unit: str
    notes: str | None
    target_unit: str | None
    target_bottle_qty: int | None
    total_volume_ml: decimal.Decimal
    items: list                     # (material_id, qty_used, qty_unit) per liquid ingredient
    formula_label: str | None = None   # set when produced from a built-in formula
    packaging_items: list | None = None  # (material_id, qty per bottle); None = the target size's unit_formula_items
    base_code: str | None = None        # the product code (A1) the batch is of; a batch without one can be bottled but not sold by the mL
    scent_oil: tuple | None = None      # (base_code, oil material id): take that material's oil from the scent's own stock and price


def _valid_base_code(raw):
    """The product code (A1) a batch is of, or None; unknown codes are refused."""
    code = (raw or "").strip()
    if not code:
        return None
    known = query("SELECT 1 AS ok FROM sale_catalog WHERE sku = %s",
                  (f"{code}-BULK",), fetchone=True)
    if not known:
        raise ValidationError(f"'{code}' isn't a product code in the catalog.")
    return code


def _plan_bulk_batch(form):
    """Validate the Create Batch form. Raises ValidationError with a message to flash as-is."""
    scent_name = form.get("scent_name", "").strip()
    batch_code = form.get("batch_code", "").strip() or None
    base_code = _valid_base_code(form.get("base_code"))
    input_unit = form.get("input_unit")
    target_unit = form.get("target_unit") or None

    if not scent_name:
        raise ValidationError("Scent / batch name is required.")
    if input_unit not in BATCH_VOLUME_UNITS:
        raise ValidationError("Select a valid batch size unit.")
    if target_unit is not None and target_unit not in BOTTLE_UNITS:
        raise ValidationError("Select a valid target bottle size.")
    input_qty = parse_positive_decimal(form.get("input_qty"), "Batch size")
    notes = parse_optional_text(form.get("notes"), "Notes", max_length=255)

    items = []
    seen_material_ids = set()
    for raw_id, raw_qty, qty_unit in zip(form.getlist("material_id[]"), form.getlist("qty_used[]"),
                                         form.getlist("qty_unit[]")):
        material_id = parse_optional_id(raw_id, "Material")
        if material_id is None:
            continue
        qty_used = parse_positive_decimal(raw_qty, "Quantity used")
        if qty_unit not in MATERIAL_UNITS:
            raise ValidationError("Select a valid unit for the quantity used.")
        if material_id in seen_material_ids:
            raise ValidationError("Each material can only appear once in a batch.")
        seen_material_ids.add(material_id)
        items.append((material_id, qty_used, qty_unit))
    if not items:
        raise ValidationError("Add at least one material used in this batch.")

    total_volume_ml = (input_qty * ML_PER_BATCH_UNIT[input_unit]).quantize(decimal.Decimal("0.001"))

    # Target bottle count = whole bottles the batch's volume can fill
    # (total_volume_ml // bottle_size_ml).
    target_bottle_qty = None
    if target_unit:
        target_bottle_qty = int(total_volume_ml // bottle_size_ml(target_unit))
        if target_bottle_qty < 1:
            raise ValidationError(
                f"Too small to fill one {target_unit} bottle: enlarge the batch or clear the size.")
    return _BatchPlan(scent_name, batch_code, input_qty, input_unit, notes, target_unit,
                      target_bottle_qty, total_volume_ml, items, base_code=base_code)


def _price_liquid_lines(cur, items, scent_oil=None):
    """Lock and stock-check each liquid ingredient.

    Returns (materials_by_id, line_rows, liquid_subtotal); raises TransactionAborted.
    Each line row ends with the scent code whose own oil stock it uses (None if ordinary).
    """
    placeholders = ",".join(["%s"] * len(items))
    cur.execute(
        f"""SELECT material_id, material_name, unit, cost_per_unit, stock_qty
            FROM raw_materials WHERE material_id IN ({placeholders}) FOR UPDATE""",
        tuple(i[0] for i in items),
    )
    materials_by_id = {row["material_id"]: row for row in cur.fetchall()}

    # A scent's own oil replaces the pooled Oil Scent's price and stock.
    oil_code = None
    if scent_oil and scent_oil[1] in materials_by_id:
        cur.execute("SELECT cost_per_unit, stock_qty FROM scent_oils WHERE base_code = %s FOR UPDATE",
                    (scent_oil[0],))
        own = cur.fetchone()
        if own:
            oil_code = scent_oil[0]
            materials_by_id[scent_oil[1]] = {
                **materials_by_id[scent_oil[1]],
                "cost_per_unit": own["cost_per_unit"], "stock_qty": own["stock_qty"]}

    liquid_subtotal = decimal.Decimal("0")
    line_rows = []
    for material_id, qty_used, qty_unit in items:
        material = materials_by_id.get(material_id)
        if not material:
            raise TransactionAborted("One of the selected materials no longer exists.")
        if qty_unit not in compatible_material_units(material["unit"]):
            raise TransactionAborted(
                f"{qty_unit} isn't a valid quantity unit for {material['material_name']} "
                f"({material['unit']})."
            )
        # qty_used/cost_per_unit_snapshot are kept in qty_unit (the unit typed, maybe finer
        # than the material's purchase unit); line_cost is still qty x snapshot (migration 39).
        cost_per_qty_unit = material["cost_per_unit"] * convert_material_qty(
            decimal.Decimal("1"), qty_unit, material["unit"])
        native_qty_used = convert_material_qty(qty_used, qty_unit, material["unit"])
        if material["stock_qty"] < native_qty_used:
            label = material["material_name"] + (f" for {oil_code}" if oil_code and material_id == scent_oil[1] else "")
            raise TransactionAborted(
                f"Not enough {label} in stock — "
                f"{material['stock_qty']:g} {material['unit'].lower()} left, "
                f"{qty_used:g} {qty_unit.lower()} needed."
            )
        line_cost = (qty_used * cost_per_qty_unit).quantize(decimal.Decimal("0.0001"))
        liquid_subtotal += line_cost
        line_rows.append(
            (material_id, qty_used, qty_unit, cost_per_qty_unit, line_cost, native_qty_used, False,
             oil_code if oil_code and material_id == scent_oil[1] else None))
    return materials_by_id, line_rows, liquid_subtotal


def _price_packaging_lines(cur, plan, materials_by_id):
    """Fold the target size's packaging formula into the same batch (migration 43).

    Locked and stock-checked like the liquids, using target_bottle_qty.
    Returns (line_rows, packaging_subtotal).
    """
    packaging_subtotal = decimal.Decimal("0")
    line_rows = []
    if not (plan.target_unit and plan.target_bottle_qty):
        return line_rows, packaging_subtotal
    if plan.packaging_items is not None:
        # A built-in formula brings its own packaging (bottle, sticker, seal).
        per_bottle = dict(plan.packaging_items)
        cur.execute(
            f"""SELECT material_id, material_name, unit, cost_per_unit, stock_qty
                FROM raw_materials WHERE material_id IN ({','.join(['%s'] * len(per_bottle))}) FOR UPDATE""",
            tuple(per_bottle),
        )
        packaging_materials = cur.fetchall()
        for material in packaging_materials:
            material["qty_per_unit"] = per_bottle[material["material_id"]]
    else:
        cur.execute(
            """SELECT rm.material_id, rm.material_name, rm.unit, rm.cost_per_unit, rm.stock_qty,
                      ufi.qty_per_unit
               FROM unit_formula_items ufi
               JOIN raw_materials rm ON rm.material_id = ufi.material_id
               WHERE ufi.unit = %s FOR UPDATE""",
            (plan.target_unit,),
        )
        packaging_materials = cur.fetchall()
    overlap_ids = {
        material["material_id"] for material in packaging_materials
        if material["material_id"] in materials_by_id
    }
    if overlap_ids:
        names = ", ".join(sorted(materials_by_id[mid]["material_name"] for mid in overlap_ids))
        raise TransactionAborted(
            f"{names} can't be both a batch ingredient and packaging for "
            f"{plan.target_unit}. Remove it from one of the two."
        )

    for material in packaging_materials:
        qty_used = (material["qty_per_unit"] * plan.target_bottle_qty).quantize(decimal.Decimal("0.001"))
        if material["stock_qty"] < qty_used:
            raise TransactionAborted(
                f"Not enough {material['material_name']} in stock to package "
                f"{plan.target_bottle_qty} {plan.target_unit} bottle(s) — "
                f"{material['stock_qty']:g} {material['unit'].lower()} left, "
                f"{qty_used:g} needed."
            )
        line_cost = (qty_used * material["cost_per_unit"]).quantize(decimal.Decimal("0.0001"))
        packaging_subtotal += line_cost
        line_rows.append(
            (material["material_id"], qty_used, material["unit"],
             material["cost_per_unit"], line_cost, qty_used, True, None))
    return line_rows, packaging_subtotal


def _create_bulk_batch_records(plan, user_id):
    """Write the batch in one transaction: totals, material lines and stock deducted.
    Returns (total_cost, cost_per_ml); raises TransactionAborted."""
    ensure_cost_columns()
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        materials_by_id, line_rows, liquid_subtotal = _price_liquid_lines(cur, plan.items, plan.scent_oil)
        packaging_rows, packaging_subtotal = _price_packaging_lines(cur, plan, materials_by_id)
        line_rows += packaging_rows

        liquid_subtotal = liquid_subtotal.quantize(decimal.Decimal("0.01"))
        packaging_subtotal = packaging_subtotal.quantize(decimal.Decimal("0.01"))
        # Formula batches add chemist + labor: a flat amount per bottle yielded, part of the
        # batch cost. Not materials, so no stock moves.
        chemist_cost = labor_cost = decimal.Decimal("0.00")
        if plan.formula_label and plan.target_bottle_qty:
            cur.execute("SELECT chemist_per_bottle, labor_per_bottle FROM bulk_formula_settings WHERE id = 1")
            rates = cur.fetchone()
            if rates:
                cents = decimal.Decimal("0.01")
                chemist_cost = (rates["chemist_per_bottle"] * plan.target_bottle_qty).quantize(cents)
                labor_cost = (rates["labor_per_bottle"] * plan.target_bottle_qty).quantize(cents)
        total_cost = liquid_subtotal + packaging_subtotal + chemist_cost + labor_cost
        # Cost per mL stays LIQUID-only: packaging is piece-counted, so mixing it in would
        # break the preview and yield grid.
        cost_per_ml = (liquid_subtotal / plan.total_volume_ml).quantize(decimal.Decimal("0.000001"))

        cur.execute(
            """INSERT INTO bulk_batches
               (batch_code, base_code, scent_name, input_qty, input_unit, total_volume_ml,
                total_cost, cost_per_ml, remaining_ml, notes, target_unit,
                target_bottle_qty, created_by_user_id, formula_label, chemist_cost, labor_cost)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (plan.batch_code, plan.base_code, plan.scent_name, plan.input_qty, plan.input_unit,
             plan.total_volume_ml, total_cost, cost_per_ml, plan.total_volume_ml, plan.notes,
             plan.target_unit, plan.target_bottle_qty, user_id, plan.formula_label,
             chemist_cost, labor_cost),
        )
        batch_id = cur.lastrowid

        for (material_id, qty_used, qty_unit, cost_per_unit_snapshot, line_cost,
             native_qty_used, is_packaging, from_scent_oil) in line_rows:
            cur.execute(
                """INSERT INTO bulk_batch_materials
                   (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit_snapshot,
                    line_cost, is_packaging)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (batch_id, material_id, qty_used, qty_unit,
                 cost_per_unit_snapshot, line_cost, is_packaging),
            )
            if from_scent_oil:
                cur.execute(
                    "UPDATE scent_oils SET stock_qty = stock_qty - %s WHERE base_code = %s",
                    (native_qty_used, from_scent_oil),
                )
            else:
                cur.execute(
                    "UPDATE raw_materials SET stock_qty = stock_qty - %s WHERE material_id = %s",
                    (native_qty_used, material_id),
                )
        cur.close()
    return total_cost, cost_per_ml


WRITE_OFF_REASONS = (
    "Too little to use", "Spoiled / expired", "Spilled / lost", "Used as tester", "Other",
)


@bp.route("/bulk-batches/<int:batch_id>/write-off", methods=["POST"])
@admin_required
def write_off_bulk_batch(batch_id):
    """Close out a batch's leftover mL (too little to bottle, spoiled, ...).

    Sets remaining_ml to 0 (Depleted). total_cost / cost_per_ml stay; no cost-of-goods row
    is written, only the audit log.
    """
    reason = request.form.get("reason", "")
    if reason not in WRITE_OFF_REASONS:
        flash("Pick a reason for the write-off.", "error")
        return redirect(url_for("admin.bulk_batches"))
    try:
        note = parse_optional_text(request.form.get("note"), "Note", max_length=150)
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))

    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            # Locked so a production run can't draw from the batch meanwhile.
            cur.execute(
                """SELECT scent_name, batch_code, remaining_ml
                   FROM bulk_batches WHERE batch_id = %s FOR UPDATE""",
                (batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise TransactionAborted("That batch no longer exists.")
            written_off_ml = batch["remaining_ml"]
            if written_off_ml <= 0:
                raise TransactionAborted("This batch has no mL left to write off.")
            cur.execute(
                "UPDATE bulk_batches SET remaining_ml = 0 WHERE batch_id = %s", (batch_id,))
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))
    except Exception:
        current_app.logger.exception("write off bulk batch failed for batch_id=%s", batch_id)
        flash("Couldn't write off this batch — please try again.", "error")
        return redirect(url_for("admin.bulk_batches"))

    notify_admin(["production"])
    log_action(
        "write_off_bulk_batch", target=batch["scent_name"],
        details=f"{written_off_ml:g} mL — {reason}"
        + (f" ({note})" if note else "")
        + (f" — batch {batch['batch_code']}" if batch["batch_code"] else ""))
    flash(f"Wrote off {written_off_ml:g} mL from {batch['scent_name']}.", "success")
    return redirect(url_for("admin.bulk_batches"))


@bp.route("/bulk-batches/produce", methods=["POST"])
@admin_required
def produce_batch_formula():
    """Produce one batch from a built-in formula (15% / 14% / 13%) at one timpla size.

    The formula supplies every material and amount; inputs are formula, size and scent
    name/code/notes. Runs the same transaction as any batch (_create_bulk_batch_records),
    incl. 85ML packaging and the flat chemist + labor charge per bottle.
    """
    if not consume_form_token("create_bulk_batch"):
        flash("This batch was already logged, or the form expired — check the list below before resending.", "error")
        return redirect(url_for("admin.bulk_batches"))

    config = load_formula_config()
    formula = request.form.get("formula", "")
    try:
        size = int(request.form.get("size", ""))
    except ValueError:
        size = None
    if formula not in config["formulas"] or size not in config["formulas"][formula]["sizes"]:
        flash("Choose a scent and a size.", "error")
        return redirect(url_for("admin.bulk_batches"))
    # A catalog scent has one formula (bulk_formula_scents); a mismatch means a stale or
    # tampered form.
    scent_code = request.form.get("scent_code", "").strip()
    if scent_code and formula_for_scent(config, scent_code) != formula:
        flash(f"{scent_code} uses Formula {formula_for_scent(config, scent_code)}, not Formula {formula}.", "error")
        return redirect(url_for("admin.bulk_batches"))
    try:
        scent_name = request.form.get("scent_name", "").strip()
        if not scent_name:
            raise ValidationError("Scent / batch name is required.")
        # A catalog scent code allows selling by the mL (and refills); "other" scents have none.
        base_code = _valid_base_code(scent_code)
        batch_code = request.form.get("batch_code", "").strip() or None
        notes = parse_optional_text(request.form.get("notes"), "Notes", max_length=255)
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))

    materials_list = query(
        "SELECT material_id, material_name, unit, stock_qty, cost_per_unit FROM raw_materials")
    # A catalog scent is costed at its own oil's price and takes its own oil stock.
    own_oil = prepare_scent_oils(materials_list, config).get(base_code) if base_code else None
    resolved, catalog, missing_roles = load_catalog(materials_list, config, oil=own_oil)
    calc = catalog[formula][size]
    if calc is None:
        flash("Set up the formula materials first (still missing: " + ", ".join(missing_roles) + ").", "error")
        return redirect(url_for("admin.bulk_batches"))

    if not calc["enough_stock"]:
        flash("Not enough materials to produce " + calc["label"] + ". Short: "
              + ", ".join(f"{x['name']} ({x['short_by']:g} {x['unit'].lower()})"
                          for x in calc["shortages"]) + ".", "error")
        return redirect(url_for("admin.bulk_batches"))

    total_ml = calc["total_ml"].quantize(decimal.Decimal("0.001"))
    plan = _BatchPlan(
        scent_name, batch_code, total_ml, "Milliliter", notes, config["bottle_unit"],
        calc["bottles"], total_ml,
        [(ln["material_id"], ln["qty"], ln["qty_unit"]) for ln in calc["lines"]],
        calc["label"],
        [(p["material_id"], p["per_bottle"]) for p in calc["packaging"]],
        base_code,
        (base_code, resolved["oil"]["material_id"]) if own_oil else None)
    return _save_batch(plan)


@bp.route("/bulk-batches/create", methods=["POST"])
@admin_required
def create_bulk_batch():
    """Mix specific raw materials into one new bulk batch.

    Deducts stock from each material and computes the real liquid cost / cost per mL.
    An optional target bottle size folds that size's packaging into the same batch
    (qty = target_bottle_qty x qty_per_unit, tagged is_packaging); blank is liquid-only.
    """
    if not consume_form_token("create_bulk_batch"):
        flash("This batch was already logged, or the form expired — check the list below before resending.", "error")
        return redirect(url_for("admin.bulk_batches"))

    try:
        plan = _plan_bulk_batch(request.form)
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))

    return _save_batch(plan)


def _save_batch(plan):
    """Write a validated plan, then notify/log/flash. Always redirects to the list."""
    try:
        total_cost, cost_per_ml = _create_bulk_batch_records(plan, session.get("user_id"))
        notify_admin(["production", "materials"])
        notify_all(["materials"])
        log_action("create_bulk_batch", target=plan.scent_name,
                   details=f"{plan.input_qty:g} {plan.input_unit} — ₱{total_cost:,.2f} total (₱{cost_per_ml:,.6f}/mL liquid)"
                   + (f", {plan.target_bottle_qty} x {plan.target_unit} packaged" if plan.target_unit else "")
                   + (f" — {plan.formula_label}" if plan.formula_label else "")
                   + (f" — batch {plan.batch_code}" if plan.batch_code else ""))
        flash(
            f"Batch logged — {plan.total_volume_ml:g} mL at ₱{total_cost:,.2f} total.", "success")
    except TransactionAborted as err:
        flash(str(err), "error")
    except Exception:
        current_app.logger.exception(
            "create bulk batch failed for scent_name=%s", plan.scent_name)
        flash("Couldn't log this batch — please try again.", "error")
    return redirect(url_for("admin.bulk_batches"))
