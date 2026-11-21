"""Total rows and bulk-quantity column handling."""




# Columns never summed in totals rows: per-unit prices/costs, ratios and counts
# across different materials or products add up to a meaningless number.
NO_TOTAL_COLUMNS = {"unit_price", "cost_per_unit", "cogs_per_unit",
                    "qty_per_unit", "package_qty", "price", "hq_price",
                    "cost_per_ml", "turnover",
                    # Per-row levels and signed changes (in and out net to nothing meaningful).
                    "reorder_level", "stock_on_hand", "change_qty", "change_qty_bulk_ml",
                    # Partner Inquiries' quoted amount covers every lead;
                    # only the Closed-only sale_amount column is a total.
                    "package_value",
                    # Financial Summary is one row per different figure.
                    "figure_amount",
                    # Material Price Changes: how many purchases each material had is a count per row,
                    # not an amount that means anything added across different materials.
                    "purchase_count"}


# Columns holding mL (not bottles) on a bulk product's row (products.unit = 'BULK').
BULK_QTY_COLUMNS = {"qty_sold", "stock_qty", "total_stock", "qty_produced", "change_qty"}


def split_bulk_quantities(columns, rows):
    """Keep bulk mL out of bottle-count columns.

    For a report whose rows carry the product's `unit`, each column in
    BULK_QTY_COLUMNS is split in two when the rows mix bottled and bulk
    products: "<label> (bottles)" and "<label> (bulk mL)", each row's
    value landing in exactly one of them. compute_totals() then sums
    each on its own instead of adding mL to bottles. When every row is
    bulk the column is just relabelled; all-bottled reports are unchanged.
    """
    if not rows or "unit" not in rows[0]:
        return columns
    bulk_flags = [r.get("unit") == "BULK" for r in rows]
    if not any(bulk_flags):
        return columns
    all_bulk = all(bulk_flags)
    out = []
    for col in columns:
        key, label = col[0], col[1]
        if key not in BULK_QTY_COLUMNS:
            out.append(col)
            continue
        if all_bulk:
            out.append((key, f"{label} (mL)") + tuple(col[2:]))
            continue
        ml_key = f"{key}_bulk_ml"
        for r, is_bulk in zip(rows, bulk_flags):
            r[ml_key] = r[key] if is_bulk else None
            if is_bulk:
                r[key] = None
        out.append((key, f"{label} (bottles)") + tuple(col[2:]))
        out.append((ml_key, f"{label} (bulk mL)") + tuple(col[2:]))
    return out


def compute_totals(columns, rows):
    """Sum every money/int column across all rows of a report.

    Returns a {column_key: summed_value} dict, or None when there's
    nothing summable (e.g. a report made entirely of text/date/badge
    columns, like Accounts or Partners) — a totals row would just be
    a row of dashes there, so it's skipped rather than shown.

    Columns listed in NO_TOTAL_COLUMNS (e.g. a per-unit price) are
    skipped even though they're typed money/int — summing a per-unit
    figure across rows doesn't produce a meaningful total the way
    summing a quantity or a line total does.
    """
    if not rows:
        return None
    totals = {}
    for col in columns:
        key, _, ctype = col[0], col[1], col[2]
        if ctype not in ("money", "int", "num") or key in NO_TOTAL_COLUMNS:
            continue
        total = 0
        has_value = False
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            total += value
            has_value = True
        if has_value:
            totals[key] = total
    return totals or None
