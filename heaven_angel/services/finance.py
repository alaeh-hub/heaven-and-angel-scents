"""Shared money definitions for the dashboard, Reports and the AI tools, so
every page reads profit the same way.

Cost of goods SOLD, not produced: a production batch is an investment in
stock, not an expense of the month it was made. Profit for a period is its
revenue minus what the goods that were actually sold or shipped cost to
make. Unit cost per SKU is the weighted average over every logged
production batch (SUM(total_cogs) / SUM(qty_produced)); a SKU that has never
been produced has no cost on record and counts as 0. Bulk is costed per mL,
the same unit its sales are in. Freebies count as cost with no revenue, and
Closed package orders count their contents (partner_inquiry_items).

"Cost of goods produced" (SUM of cogs_logs) is still shown as capital — what
has gone into making stock — but it is not what profit subtracts.
"""
import decimal

from heaven_angel.db import query

# One row per SKU: its average production cost per unit.
UNIT_COST_SQL = """(SELECT sku, SUM(total_cogs) / NULLIF(SUM(qty_produced), 0) AS unit_cost
                    FROM cogs_logs GROUP BY sku)"""

# A package order is dated by when it was closed, falling back to when the
# inquiry came in for rows that predate partner_inquiries.closed_at.
PACKAGE_DATE_SQL = "COALESCE(pi.closed_at, pi.created_at)"

# What a bulk sale or refill cost: the mL it took from each bulk batch times that
# batch's cost per mL (see sale_batch_usage). Every other sale has no such rows and
# costs its SKU's average production cost per unit instead.
SALE_BATCH_COST_SQL = """(SELECT sale_id, SUM(ml * cost_per_ml) AS cost
                          FROM sale_batch_usage GROUP BY sale_id)"""

# The cost of one sales row (alias s), given the two LEFT JOINs below.
SALE_COST_EXPR = "COALESCE(su.cost, s.qty_sold * COALESCE(uc.unit_cost, 0))"

# `FROM sales s` plus the two joins SALE_COST_EXPR needs.
SALES_WITH_COST_SQL = f"""sales s
                     LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = s.sku
                     LEFT JOIN {SALE_BATCH_COST_SQL} su ON su.sale_id = s.sale_id"""

SALES_COGS_SQL = f"""SELECT COALESCE(SUM({SALE_COST_EXPR}), 0) AS v
                     FROM {SALES_WITH_COST_SQL}"""

PACKAGE_COGS_SQL = f"""SELECT COALESCE(SUM(pii.qty * COALESCE(uc.unit_cost, 0)), 0) AS v
                       FROM partner_inquiry_items pii
                       JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
                       LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
                       WHERE pi.status = 'Closed'"""


def cogs_sold(sales_where="1=1", package_where="1=1", params=()):
    """Cost of goods sold = cost of register sales (alias s) + cost of Closed
    package orders (alias pi), each filtered by its own WHERE fragment. The
    same params feed both fragments, in that order."""
    sales = query(f"{SALES_COGS_SQL} WHERE {sales_where}", tuple(params), fetchone=True)["v"]
    packages = query(f"{PACKAGE_COGS_SQL} AND {package_where}", tuple(params), fetchone=True)["v"]
    return float(sales) + float(packages)


def package_value(discount_percent, reference_total):
    """Apply a package's discount_percent to a reference total, returning
    (reference_total, discounted_total) as Decimals. Shared by the admin
    Packages pages and the public partner portal so the math can't drift."""
    reference_total = decimal.Decimal(reference_total)
    discount_percent = decimal.Decimal(discount_percent)
    discounted_total = reference_total *         (decimal.Decimal("1") - (discount_percent / decimal.Decimal("100")))
    return reference_total, discounted_total
