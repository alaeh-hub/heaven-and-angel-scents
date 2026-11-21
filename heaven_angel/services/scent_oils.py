"""Oil scent price and stock per scent (schema.sql, migration 57).

Each scent has its own oil. scent_oils keeps one row per scent (the product
base code); the single "Oil Scent" raw material stays as the summary row on
the Materials page, and batches made for a scent use that scent's row.
"""
import csv
import decimal
import io

from heaven_angel.db import query, transaction
from heaven_angel.utils import ValidationError

D = decimal.Decimal

# Oil restocks counted as Oil Scent purchases (per supplier). LEFT JOIN on material_id.
OIL_PURCHASES_SQL = """(SELECT oil.material_id AS material_id, SUM(sor.cost) AS cost, SUM(sor.qty) AS qty,
                               COUNT(*) AS n, MAX(sor.purchased_at) AS last_at
                        FROM scent_oil_restocks sor
                        JOIN batch_formula_materials bfm ON bfm.role = 'oil'
                        JOIN raw_materials oil ON oil.material_id = bfm.material_id
                        GROUP BY oil.material_id)"""
CSV_HEADER = ["code", "scent", "price_per_unit", "stock"]


def ensure_scent_oils(oil_material, scents):
    """Make sure every scent has a row. `scents` is [(base_code, name)];
    `oil_material` the shared Oil Scent raw material row (or None).

    New rows start at the shared material's price and with 0 stock: the oil
    each scent really has is entered by the owner (edit, restock or CSV import).
    The shared material's own stock is left alone; it is what batches of an
    "other" scent (one with no catalog code) still draw from.
    """
    if not oil_material or not scents:
        return
    known = {r["base_code"] for r in query("SELECT base_code FROM scent_oils")}
    missing = [(code, name) for code, name in scents if code not in known]
    if not missing:
        return
    price = D(oil_material["cost_per_unit"])
    with transaction() as conn:
        cur = conn.cursor()
        for code, name in missing:
            cur.execute(
                """INSERT IGNORE INTO scent_oils (base_code, scent_name, cost_per_unit, stock_qty)
                   VALUES (%s, %s, %s, 0)""",
                (code, name[:150], price))
        cur.close()


def load_scent_oils():
    """Load every scent with its oil stock."""
    return query("SELECT base_code, scent_name, cost_per_unit, stock_qty FROM scent_oils "
                 "ORDER BY scent_name, base_code")


def oil_summary(rows):
    """The numbers for the Oil Scent summary row."""
    if not rows:
        return None
    prices = [r["cost_per_unit"] for r in rows]
    return {"count": len(rows), "low": min(prices), "high": max(prices),
            "stock": sum((r["stock_qty"] for r in rows), D("0")),
            "empty": sum(1 for r in rows if r["stock_qty"] <= 0)}


def scent_oil_template_csv(rows):
    """CSV template for importing oil stock."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for r in rows:
        writer.writerow([r["base_code"], r["scent_name"], f"{r['cost_per_unit']:f}", f"{r['stock_qty']:f}"])
    return out.getvalue()


def _cell(raw, label, line):
    """Parse an optional number from a CSV cell."""
    text = (raw or "").strip().replace(",", "")
    if text == "":
        return None
    try:
        value = D(text)
    except decimal.InvalidOperation:
        raise ValidationError(f"Row {line}: {label} '{raw}' is not a number.") from None
    if not value.is_finite() or value < 0:
        raise ValidationError(f"Row {line}: {label} must be 0 or more.")
    return value


def parse_scent_oil_csv(text, known_codes):
    """Rows of (code, price or None, stock or None) from an uploaded CSV, or a
    ValidationError naming the first bad row. A blank cell leaves that value
    unchanged. Nothing is applied here."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
    if "code" not in fields or not ({"price_per_unit", "stock"} & fields):
        raise ValidationError("The file needs a 'code' column and a 'price_per_unit' and/or 'stock' column. "
                              "Download the template to start from.")
    rows, seen = [], set()
    for line, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): v for k, v in raw.items()}
        code = (row.get("code") or "").strip()
        if not code:
            continue
        if code not in known_codes:
            raise ValidationError(f"Row {line}: '{code}' is not a scent code.")
        if code in seen:
            raise ValidationError(f"Row {line}: '{code}' appears twice.")
        seen.add(code)
        rows.append((code, _cell(row.get("price_per_unit"), "price", line),
                     _cell(row.get("stock"), "stock", line)))
    if not rows:
        raise ValidationError("The file has no rows.")
    return rows
