"""Tools: calculators for the numbers the business works with every day — a
unit converter, the alcohol + water mix, bottle yield, price per unit, and a
formula cost calculator with editable prices.

Everything is calculated in the browser from numbers the server hands over
(the unit factors, the formula settings from the database, the formulas'
current costing), so nothing here writes to the database.
"""
from flask import render_template

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.utils import BOTTLE_UNITS, ML_PER_BATCH_UNIT, bottle_size_ml

from heaven_angel.routes.admin._common import bp
from heaven_angel.routes.admin.batch_formulas import (
    calc_to_json, load_catalog, load_formula_config, load_scent_choices,
)

# How many of the base unit (mL for volume, g for weight) one of each unit is.
# The US gallon comes from the same constant the bulk batches use.
UNIT_FACTORS = {
    "volume": {
        "mL": 1.0,
        "L": float(ML_PER_BATCH_UNIT["Liter"]),
        "gal": float(ML_PER_BATCH_UNIT["Gallon"]),
        "fl oz": 29.5735295625,
    },
    "weight": {
        "g": 1.0,
        "kg": 1000.0,
        "oz": 28.349523125,
        "lb": 453.59237,
    },
}


@bp.route("/tools")
@admin_required
def tools():
    materials = query(
        "SELECT material_id, material_name, unit, stock_qty, cost_per_unit FROM raw_materials")
    config = load_formula_config()
    _resolved, catalog, missing_roles = load_catalog(materials, config)
    return render_template(
        "admin/tools.html",
        unit_factors=UNIT_FACTORS,
        bottle_sizes={u: float(bottle_size_ml(u)) for u in BOTTLE_UNITS},
        mix={
            "alcohol_parts": float(config["alcohol_parts"] or 0),
            "water_parts": float(config["water_parts"] or 0),
        },
        formula_catalog_json={
            f: {str(size): calc_to_json(calc) for size, calc in sizes.items()}
            for f, sizes in catalog.items()
        },
        scent_choices=load_scent_choices(config),
        formula_names=list(config["formulas"]),
        missing_roles=missing_roles,
    )
