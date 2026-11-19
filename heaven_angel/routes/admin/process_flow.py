"""Process Flow: a read-only picture of how a bottle gets made and costed.

It changes nothing. It shows, step by step and for one formula and size at a time,
what the system does with the numbers it already has: the materials and their
prices, the formula's amounts, the mix split, the cost of the liquid, the bulk batch
(bottles, packaging, chemist and labor, cost per bottle), bottling it into a product
(mL drawn, the flat base cost of goods) and selling it. Every figure is computed by
the same code the Bulk Batches page uses (batch_formulas.compute_formula), so the
picture can never disagree with what producing a batch really does.
"""
from flask import render_template

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.routes.admin._common import bp
from heaven_angel.routes.admin.batch_formulas import (
    calc_to_json, load_catalog, load_formula_config, load_scent_choices, prepare_scent_oils,
)


@bp.route("/process-flow")
@admin_required
def process_flow():
    materials = query(
        "SELECT material_id, material_name, unit, stock_qty, cost_per_unit FROM raw_materials ORDER BY material_name")
    config = load_formula_config()
    _resolved, catalog, missing_roles = load_catalog(materials, config)
    scent_oils = prepare_scent_oils(materials, config)
    base_cost = {r["unit"]: float(r["base_cost_per_unit"])
                 for r in query("SELECT unit, base_cost_per_unit FROM unit_cogs_settings")}
    rate = query("SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True)
    return render_template(
        "admin/process_flow.html",
        formula_specs={f: {"scents": spec["scents"], "sizes": list(spec["sizes"])}
                       for f, spec in config["formulas"].items()},
        formula_catalog_json={
            f: {str(size): calc_to_json(calc) for size, calc in sizes.items()}
            for f, sizes in catalog.items()
        },
        scent_choices=load_scent_choices(config),
        scent_oils_json={code: {"rate": float(r["cost_per_unit"]), "stock": float(r["stock_qty"])}
                         for code, r in scent_oils.items()},
        base_cost_json=base_cost,
        bulk_rate=float(rate["rate_per_ml"]) if rate else 0.0,
        bottle_unit=config["bottle_unit"],
        default_formula=config["default_formula"],
        missing_roles=missing_roles,
    )
