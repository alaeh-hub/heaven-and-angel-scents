"""The built-in bulk batch formulas from formula.txt / the "Costing
Calculator By Formula" spreadsheet.

There are three formulas (15%, 14%, 13%), each in three timpla sizes (500,
250 and 125 ml of oil scent). The amounts live in the database (bulk_formulas,
bulk_formula_sizes, bulk_formula_settings, bulk_formula_packaging — seeded in
schema.sql, migration 50), so producing a batch means choosing a formula and a
size; nobody picks materials one by one. Each ingredient role (oil scent,
fixative, ...) is tied once to a raw material (batch_formula_materials), and
everything — total mL, the alcohol/water split, bottles, packaging, cost per
bottle — is computed from the database's amounts and that material's current
cost.

The "Alcohol" line of a formula (e.g. 2800 ml) is already alcohol AND
distilled water mixed together, so it is split by the formula settings'
alcohol : water parts (200 : 1000 in the spreadsheet).
"""
import decimal
import math

from flask import flash, redirect, request, url_for

from heaven_angel.db import query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_admin
from heaven_angel.utils import (
    ML_PER_BATCH_UNIT, ValidationError, base_code_from_sku, bottle_size_ml, compatible_material_units,
    convert_material_qty, parse_positive_int,
)

from heaven_angel.routes.admin._common import bp

D = decimal.Decimal

# role -> (label shown to the user, unit the formula states it in,
#          words that identify a material of that role by name)
ROLES = {
    "oil": ("Oil Scent", "Milliliter", ("oil scent", "oil", "fragrance")),
    "fixative": ("Fixative", "Gram", ("fixative",)),
    "alcohol": ("Alcohol", "Milliliter", ("perfume alcohol", "alcohol", "ethanol")),
    "moisturizer": ("Moisturizer", "Milliliter", ("moisturizer",)),
    "water": ("Distilled Water", "Milliliter", ("distilled water", "water")),
    # Packaging: the amount per bottle is in bulk_formula_packaging, so the
    # amount needed is that times the number of bottles the formula makes.
    "bottle": ("Bottle", "Piece", ("frosted", "bottle")),
    "sticker": ("Sticker", "Piece", ("sticker",)),
    "seal": ("Plastic Seal", "Piece", ("plastic seal", "seal")),
}
PACKAGING_ROLES = ("bottle", "sticker", "seal")


def load_formula_config():
    """The formulas and their shared settings, read from the database."""
    settings = query("SELECT bottle_unit, mix_alcohol_parts, mix_water_parts FROM bulk_formula_settings "
                     "WHERE id = 1", fetchone=True)
    if settings is None:
        # Not seeded (schema.sql migration 50 not run): no formulas to offer.
        return {"bottle_unit": None, "bottle_ml": None, "alcohol_parts": None, "water_parts": None,
                "packaging": {}, "formulas": {}, "default_formula": None, "scent_formulas": {}}
    packaging = {r["role"]: r["qty_per_bottle"]
                 for r in query("SELECT role, qty_per_bottle FROM bulk_formula_packaging")}
    # Formulas in the order they were seeded; sizes largest first.
    formulas = {}
    by_id = {}
    default_formula = None
    for f in query("SELECT formula_id, name, is_default FROM bulk_formulas ORDER BY formula_id"):
        by_id[f["formula_id"]] = formulas[f["name"]] = {"sizes": {}, "scent_codes": []}
        if f["is_default"] and default_formula is None:
            default_formula = f["name"]
    for z in query("SELECT formula_id, oil_ml, fixative_g, mix_ml, moisturizer_ml FROM bulk_formula_sizes "
                   "ORDER BY oil_ml DESC"):
        if z["formula_id"] in by_id:
            by_id[z["formula_id"]]["sizes"][int(z["oil_ml"])] = (
                z["fixative_g"], z["mix_ml"], z["moisturizer_ml"])
    scent_formulas = {}
    for r in query("SELECT base_code, formula_id FROM bulk_formula_scents ORDER BY base_code"):
        if r["formula_id"] in by_id:
            by_id[r["formula_id"]]["scent_codes"].append(r["base_code"])
    for name, spec in formulas.items():
        for code in spec["scent_codes"]:
            scent_formulas[code] = name
        # Text for the page: the default formula covers every scent not listed.
        spec["scents"] = "All other scents" if name == default_formula else ", ".join(spec["scent_codes"])
    return {
        "bottle_unit": settings["bottle_unit"],
        "bottle_ml": bottle_size_ml(settings["bottle_unit"]),
        "alcohol_parts": settings["mix_alcohol_parts"],
        "water_parts": settings["mix_water_parts"],
        "packaging": packaging,
        "formulas": formulas,
        "default_formula": default_formula,
        "scent_formulas": scent_formulas,
    }


def formula_for_scent(config, base_code):
    """Which formula a scent (product base code) uses: its own entry in
    bulk_formula_scents, otherwise the default formula."""
    return config["scent_formulas"].get(base_code, config["default_formula"])


def load_scent_choices(config):
    """The scents offered on the Bulk Batches page: every product sold in the
    formulas' bottle size, each with the formula it uses."""
    choices = []
    seen = set()
    for p in query("SELECT sku, item_name FROM products WHERE unit = %s ORDER BY item_name",
                   (config["bottle_unit"],)):
        code = base_code_from_sku(p["sku"], config["bottle_unit"])
        if code in seen:
            continue
        seen.add(code)
        choices.append({"code": code, "name": p["item_name"], "formula": formula_for_scent(config, code)})
    return choices


def split_mix(mix_ml, alcohol_parts, water_parts):
    """Split a mix amount into (alcohol_ml, water_ml), e.g. 2800 ml ->
    (466.667, 2333.333). Water is the remainder so they always add back up."""
    alcohol_ml = (D(mix_ml) * alcohol_parts / (alcohol_parts + water_parts)).quantize(D("0.001"))
    return alcohol_ml, D(mix_ml) - alcohol_ml


def formula_label(formula, size):
    return f"Formula {formula}, {size} ml"


def resolve_materials(materials):
    """role -> raw material row. A saved choice wins; otherwise the first
    material whose name matches the role (see ROLES)."""
    by_id = {m["material_id"]: m for m in materials}
    resolved = {}
    for row in query("SELECT role, material_id FROM batch_formula_materials"):
        if row["role"] in ROLES and row["material_id"] in by_id:
            resolved[row["role"]] = by_id[row["material_id"]]
    used = {m["material_id"] for m in resolved.values()}
    for role, (_label, _unit, keywords) in ROLES.items():
        if role in resolved:
            continue
        for keyword in keywords:
            match = next((m for m in materials
                          if m["material_id"] not in used and keyword in m["material_name"].lower()), None)
            if match:
                resolved[role] = match
                used.add(match["material_id"])
                break
    return resolved


def _line_unit(preferred, material_unit):
    """The unit to measure a role in, given how its material is stocked."""
    if preferred in compatible_material_units(material_unit):
        return preferred
    if material_unit == "Gram" or material_unit in ML_PER_BATCH_UNIT:
        # The sheet counts grams and mL 1:1 in the total, so do the same.
        return "Gram" if material_unit == "Gram" else "Milliliter"
    return material_unit


def _line(role, label, material, qty, preferred_unit):
    unit = _line_unit(preferred_unit, material["unit"])
    rate = material["cost_per_unit"] * convert_material_qty(D("1"), unit, material["unit"])
    return {
        "role": role, "label": label, "material_id": material["material_id"],
        "material_name": material["material_name"], "qty": D(qty), "qty_unit": unit,
        "rate": rate, "line_cost": (D(qty) * rate).quantize(D("0.0001")),
    }


def compute_formula(formula, size, resolved, config, stock_by_id=None):
    """The costing for one formula + size, like the spreadsheet's Formula
    sheet. Returns None when a needed material isn't set up yet.

    bottles = floor(total ml / bottle size); cost per NEW bottle = (liquid +
    packaging) / bottles; cost per REFILL = liquid / bottles.
    """
    if any(role not in resolved for role in ROLES):
        return None
    fixative_g, mix_ml, moisturizer_ml = config["formulas"][formula]["sizes"][size]
    alcohol_ml, water_ml = split_mix(mix_ml, config["alcohol_parts"], config["water_parts"])
    lines = [
        _line("oil", "Oil Scent", resolved["oil"], size, ROLES["oil"][1]),
        _line("fixative", "Fixative", resolved["fixative"], fixative_g, ROLES["fixative"][1]),
        _line("alcohol", "Alcohol (in the mix)", resolved["alcohol"], alcohol_ml, "Milliliter"),
        _line("water", "Distilled Water (in the mix)", resolved["water"], water_ml, "Milliliter"),
        _line("moisturizer", "Moisturizer", resolved["moisturizer"], moisturizer_ml,
              ROLES["moisturizer"][1]),
    ]

    # The sheet's "mixing batches": alcohol and water are prepared in whole
    # mixing batches (e.g. 200 ml alcohol + 1000 ml water = 1200 ml), several
    # until the formula's amount is reached; the leftover keeps for next time.
    # Only the mix actually used is costed and deducted from stock.
    mix_batch_ml = config["alcohol_parts"] + config["water_parts"]
    mix_batches = int(math.ceil(mix_ml / mix_batch_ml)) if mix_ml else 0
    mix_prepared_ml = mix_batches * mix_batch_ml

    total_ml = D(size) + fixative_g + mix_ml + moisturizer_ml
    liquid_cost = sum((ln["line_cost"] for ln in lines), D("0")).quantize(D("0.01"))
    bottles = int(total_ml // config["bottle_ml"])
    # Packaging is automatic: bottles x the per-bottle amount, priced at the
    # material's current cost.
    packaging = []
    for role in PACKAGING_ROLES:
        material = resolved[role]
        per_bottle = config["packaging"][role]
        qty = per_bottle * bottles
        packaging.append({
            "role": role, "material_id": material["material_id"], "name": material["material_name"],
            "unit": material["unit"], "per_bottle": per_bottle, "qty": qty,
            "rate": material["cost_per_unit"],
            "line_cost": (qty * material["cost_per_unit"]).quantize(D("0.01")),
        })
    packaging_cost = sum((p["line_cost"] for p in packaging), D("0"))
    total_cost = liquid_cost + packaging_cost

    # Is there enough of everything in stock? Same rule as producing the
    # batch (_price_liquid_lines / _price_packaging_lines in production.py):
    # the amount needed, in the material's own stock unit, vs stock_qty.
    shortages = []
    if stock_by_id is not None:
        for role, ln in zip(("oil", "fixative", "alcohol", "water", "moisturizer"), lines):
            material = resolved[role]
            stock = stock_by_id.get(ln["material_id"], D("0"))
            needed = convert_material_qty(ln["qty"], ln["qty_unit"], material["unit"])
            ln["stock"] = convert_material_qty(stock, material["unit"], ln["qty_unit"])
            ln["short_by"] = max(D("0"), convert_material_qty(
                needed - stock, material["unit"], ln["qty_unit"]))
            if needed > stock:
                shortages.append({"name": ln["material_name"], "short_by": ln["short_by"],
                                  "unit": ln["qty_unit"]})
        for p in packaging:
            stock = stock_by_id.get(p["material_id"], D("0"))
            p["stock"] = stock
            p["short_by"] = max(D("0"), p["qty"] - stock)
            if p["qty"] > stock:
                shortages.append({"name": p["name"], "short_by": p["short_by"], "unit": p["unit"]})
    return {
        "formula": formula, "size": size, "label": formula_label(formula, size),
        "lines": lines, "packaging": packaging,
        "mix_ml": D(mix_ml), "alcohol_ml": alcohol_ml, "water_ml": water_ml,
        "mix_batch_ml": mix_batch_ml, "mix_batches": mix_batches, "mix_prepared_ml": mix_prepared_ml,
        "mix_leftover_ml": mix_prepared_ml - mix_ml,
        "alcohol_parts": config["alcohol_parts"], "water_parts": config["water_parts"],
        "total_ml": total_ml, "bottles": bottles,
        "leftover_ml": total_ml - bottles * config["bottle_ml"], "bottle_ml": config["bottle_ml"],
        "liquid_cost": liquid_cost, "packaging_cost": packaging_cost, "total_cost": total_cost,
        "cost_per_new_bottle": (total_cost / bottles).quantize(D("0.01")) if bottles else None,
        "cost_per_refill_bottle": (liquid_cost / bottles).quantize(D("0.01")) if bottles else None,
        "shortages": shortages, "enough_stock": not shortages,
    }


def calc_to_json(c):
    """One formula+size's costing as plain JSON for the page's summary panel
    and the Tools calculators (display only — producing a batch recomputes
    everything server-side)."""
    if c is None:
        return None
    f = float
    return {
        "label": c["label"], "total_ml": f(c["total_ml"]), "bottles": c["bottles"],
        "bottle_ml": f(c["bottle_ml"]),
        "leftover_ml": f(c["leftover_ml"]), "mix_ml": f(c["mix_ml"]),
        "alcohol_ml": f(c["alcohol_ml"]), "water_ml": f(c["water_ml"]),
        "mix_batch_ml": f(c["mix_batch_ml"]), "mix_batches": c["mix_batches"],
        "mix_prepared_ml": f(c["mix_prepared_ml"]), "mix_leftover_ml": f(c["mix_leftover_ml"]),
        "alcohol_parts": f(c["alcohol_parts"]), "water_parts": f(c["water_parts"]),
        "liquid_cost": f(c["liquid_cost"]), "packaging_cost": f(c["packaging_cost"]),
        "total_cost": f(c["total_cost"]),
        "cost_per_new_bottle": f(c["cost_per_new_bottle"]) if c["bottles"] else None,
        "cost_per_refill_bottle": f(c["cost_per_refill_bottle"]) if c["bottles"] else None,
        "enough_stock": c["enough_stock"],
        "shortages": [
            {"name": x["name"], "short_by": f(x["short_by"]), "unit": x["unit"]}
            for x in c["shortages"]
        ],
        "lines": [
            {"label": ln["label"], "material": ln["material_name"], "qty": f(ln["qty"]),
             "unit": ln["qty_unit"], "rate": f(ln["rate"]), "line_cost": f(ln["line_cost"]),
             "stock": f(ln["stock"]), "short_by": f(ln["short_by"])}
            for ln in c["lines"]
        ],
        "packaging": [
            {"name": p["name"], "per_bottle": f(p["per_bottle"]), "qty": f(p["qty"]),
             "unit": p["unit"], "rate": f(p["rate"]), "line_cost": f(p["line_cost"]),
             "stock": f(p["stock"]), "short_by": f(p["short_by"])}
            for p in c["packaging"]
        ],
    }


def load_catalog(materials, config=None):
    """Every formula x size, computed. Returns (resolved materials,
    {formula: {size: calc or None}}, missing role labels)."""
    config = config or load_formula_config()
    resolved = resolve_materials(materials)
    missing = [ROLES[r][0] for r in ROLES if r not in resolved]
    stock_by_id = {m["material_id"]: m["stock_qty"] for m in materials}
    catalog = {
        formula: {size: compute_formula(formula, size, resolved, config, stock_by_id)
                  for size in spec["sizes"]}
        for formula, spec in config["formulas"].items()
    }
    return resolved, catalog, missing


@bp.route("/bulk-batches/formula-materials", methods=["POST"])
@admin_required
def save_formula_materials():
    """Remember which raw material plays each role (oil, fixative, ...)."""
    chosen = {}
    try:
        for role, (label, _unit, _kw) in ROLES.items():
            chosen[role] = parse_positive_int(request.form.get(role), label)
        if len(set(chosen.values())) != len(chosen):
            raise ValidationError("Each role needs its own material — the same one can't be used twice.")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))

    known = {m["material_id"] for m in query("SELECT material_id FROM raw_materials")}
    if not set(chosen.values()) <= known:
        flash("One of the selected materials no longer exists.", "error")
        return redirect(url_for("admin.bulk_batches"))

    with transaction() as conn:
        cur = conn.cursor()
        for role, material_id in chosen.items():
            cur.execute(
                """INSERT INTO batch_formula_materials (role, material_id) VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE material_id = VALUES(material_id)""",
                (role, material_id))
        cur.close()
    notify_admin(["production"])
    log_action("save_formula_materials", target="Bulk batch formulas")
    flash("Formula materials saved.", "success")
    return redirect(url_for("admin.bulk_batches"))
