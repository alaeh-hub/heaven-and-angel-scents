"""The report catalog: which reports exist, their columns, filters and who may run them."""




# ---------------------------------------------------------------- registry
# admin / branch: whether that role can generate this report at all.
# windowed: whether a time-window (Recent/Range/All) control applies.
REPORT_TYPES = {
    "products":        {"label": "Products",        "admin": True, "branch": False, "windowed": False},
    "production_log":  {"label": "Production Log",  "admin": True, "branch": False, "windowed": True},
    # Materials/Suppliers/Base Cost of Goods/Cost of Goods Log — admin-only (raw
    # materials and cost of goods are HQ-side bookkeeping; branch
    # accounts never see the Materials/Base Cost of Goods pages either). Materials
    # and Cost of Goods Log are dated logs (windowed); Suppliers and
    # Formulas are catalog-style snapshots, same reasoning as Products/
    # Accounts/Partners/Packages above.
    "materials":       {"label": "Materials",        "admin": True, "branch": False, "windowed": True},
    "suppliers":       {"label": "Suppliers",        "admin": True, "branch": False, "windowed": False},
    "formulas":        {"label": "Base Cost of Goods", "admin": True, "branch": False, "windowed": False},
    "cogs_logs":       {"label": "Cost of Goods Log", "admin": True, "branch": False, "windowed": True},
    # Bulk Batches — the step between raw materials and bottling (see
    # bulk_batches in schema.sql). Admin-only like the rest of the
    # materials/cost-of-goods bookkeeping above, and a dated log (when
    # each batch was mixed), so windowed.
    "bulk_batches":    {"label": "Bulk Batches",     "admin": True, "branch": False, "windowed": True},
    "branch_stock":    {"label": "Stock (HQ & Branches)", "admin": True, "branch": True,  "windowed": False,
                        "branch_label": "My Inventory"},
    # Low Stock — the Low Stock page as a report: every SKU at or below
    # its reorder level (LOW_STOCK_WHERE), HQ first, most critical first.
    "low_stock":       {"label": "Low Stock",        "admin": True, "branch": True,  "windowed": False},
    "stock_requests":  {"label": "Stock Requests",   "admin": True, "branch": True,  "windowed": True},
    "inventory_log":   {"label": "Inventory Log",    "admin": True, "branch": True,  "windowed": True},
    "sales_history":   {"label": "Sales History",    "admin": True, "branch": True,  "windowed": True},
    "credit_purchases": {"label": "Credit Purchases", "admin": True, "branch": True,
                         "windowed": True, "branch_label": "Credit Purchases"},
    # Voided Sales — the permanent record of sales removed by Void
    # (sale_voids): what it was, who voided it and why.
    "voided_sales":    {"label": "Voided Sales",     "admin": True, "branch": True,  "windowed": True},
    # Customers — the repeat-customer directory (built from
    # sales.customer_name, no table of its own). A per-customer rollup
    # like the Customers pages, so a snapshot rather than windowed.
    "customers":       {"label": "Customers",        "admin": True, "branch": True,  "windowed": False},
    # Discrepancies — damaged/short units on deliveries, the same
    # DAMAGE/ADJUSTMENT rows both Discrepancies pages list. Dated, so
    # windowed.
    "discrepancies":   {"label": "Discrepancies",    "admin": True, "branch": True,  "windowed": True},
    # Branch Performance — one summary row per retail branch (the Branch
    # Performance page). Windowed so revenue/units can be pulled for a
    # month or a date range; "Recent N" doesn't mean anything for a
    # per-branch total, so it reads as all time (see the builder).
    "branch_performance": {"label": "Branch Performance", "admin": True, "branch": False, "windowed": True},
    # Financial Summary — the Dashboard / Reports page money figures for
    # a window: register sales + Closed package orders, cost of goods,
    # gross profit, raw materials bought. Recent mode reads as all time.
    "financial_summary": {"label": "Financial Summary", "admin": True, "branch": False, "windowed": True},
    "accounts":        {"label": "Accounts",         "admin": True, "branch": False, "windowed": False},
    # Partners & Distribution — admin-only (branch accounts never see
    # this section of the app at all, same as Accounts above).
    # Partners/Packages are catalog-style snapshots (not windowed),
    # same reasoning as Products/Accounts. Partner Inquiries is a dated
    # pipeline of leads, so it gets the Recent/Range/All time window.
    "partners":        {"label": "Partners",         "admin": True, "branch": False, "windowed": False},
    "packages":        {"label": "Packages",         "admin": True, "branch": False, "windowed": False},
    "partner_inquiries": {"label": "Partner Inquiries", "admin": True, "branch": False, "windowed": True},
    # Material Price Changes — per material, first vs latest price per unit and
    # the blended cost now (the Materials page's price trends, as a table).
    # A snapshot, like Suppliers/Formulas.
    "material_prices": {"label": "Material Price Changes", "admin": True, "branch": False, "windowed": False},
    # Scent Oils — the oil stocked per scent: stock, price per unit and what was bought.
    # A snapshot, like Suppliers/Material Price Changes.
    "scent_oils":      {"label": "Scent Oils",       "admin": True, "branch": False, "windowed": False},
    # Shipments — one row per delivery shipment (a partial dispatch makes several
    # for one Stock Request). Dated by when HQ dispatched it.
    "shipments":       {"label": "Shipments",        "admin": True, "branch": True,  "windowed": True},
    # Admin Log / Login Activity — the two audit pages, as dated logs.
    "admin_log":       {"label": "Admin Log",        "admin": True, "branch": False, "windowed": True},
    "login_activity":  {"label": "Login Activity",   "admin": True, "branch": False, "windowed": True},
}


# Report types whose builder actually honors filters["branch_id"]. Only
# these get a branch name in the subtitle, so a Branch picked for one
# report type and left behind on the form never mislabels another.
BRANCH_FILTERED_TYPES = {
    "branch_stock", "low_stock", "stock_requests", "inventory_log", "sales_history",
    "credit_purchases", "customers", "discrepancies", "voided_sales", "shipments",
}


# How the report builder presents each report: the group it sits in and one line on
# what you get. Kept beside the registry so a new report can't be added without them.
REPORT_GROUPS = ("Sales and customers", "Stock and deliveries", "Materials and production", "Partners and packages", "Money and oversight")

_PRESENTATION = {
    "sales_history":      ("Sales and customers", "Every sale, refill and freebie, with price, payment and customer."),
    "credit_purchases":   ("Sales and customers", "Credit sales still owed, by customer."),
    "voided_sales":       ("Sales and customers", "Sales that were voided, who voided them and why."),
    "customers":          ("Sales and customers", "Named customers with their purchases and totals."),
    "branch_stock":       ("Stock and deliveries", "Stock on hand per product, at HQ and each branch."),
    "low_stock":          ("Stock and deliveries", "Products at or below their reorder level, most urgent first."),
    "stock_requests":     ("Stock and deliveries", "Branch requests to HQ and where each one stands."),
    "shipments":          ("Stock and deliveries", "Each delivery shipment: sent, received and damaged units."),
    "inventory_log":      ("Stock and deliveries", "Every stock movement: production, dispatch, sales, adjustments."),
    "discrepancies":      ("Stock and deliveries", "Damaged or short units found on deliveries."),
    "products":           ("Materials and production", "The product catalog with prices and total stock."),
    "production_log":     ("Materials and production", "Production runs: what was made, how many, which batch."),
    "materials":          ("Materials and production", "Every material and scent oil purchase and restock, with cost per unit."),
    "material_prices":    ("Materials and production", "How each material's price moved, biggest changes first."),
    "scent_oils":         ("Materials and production", "Oil stock and price for each scent, and what was bought."),
    "suppliers":          ("Materials and production", "Suppliers with what they supply and what was spent."),
    "bulk_batches":       ("Materials and production", "Bulk batches: volume, cost, what is left and what went in."),
    "cogs_logs":          ("Materials and production", "Cost of goods logged for each production run."),
    "formulas":           ("Materials and production", "The base cost per bottle size and the bulk rate."),
    "partners":           ("Partners and packages", "Distributors and resellers on file."),
    "partner_inquiries":  ("Partners and packages", "Inquiries from the partner portal and their status."),
    "packages":           ("Partners and packages", "Partner packages with items, discount and totals."),
    "financial_summary":  ("Money and oversight", "Sales, cost of goods, profit and materials bought for a period."),
    "branch_performance": ("Money and oversight", "Each branch side by side: sales, units, stock and turnover."),
    "accounts":           ("Money and oversight", "Login accounts, their role and whether they are active."),
    "admin_log":          ("Money and oversight", "What admins changed, and when."),
    "login_activity":     ("Money and oversight", "Sign-in attempts, successful and failed."),
}
# The branch side only has sales, stock and delivery reports, so it gets its own groups.
for _key, (_group, _desc) in _PRESENTATION.items():
    REPORT_TYPES[_key]["group"] = _group
    REPORT_TYPES[_key]["description"] = _desc
assert set(_PRESENTATION) == set(REPORT_TYPES), set(REPORT_TYPES) ^ set(_PRESENTATION)


# What a branch account sees for the reports it can run: the same reports, scoped to its own branch.
_BRANCH_DESCRIPTIONS = {
    "sales_history":    "Every sale, refill and freebie at your branch, with price, payment and customer.",
    "credit_purchases": "Credit sales at your branch that are still owed, by customer.",
    "voided_sales":     "Sales at your branch that were voided, and why.",
    "customers":        "Named customers at your branch with their purchases and totals.",
    "branch_stock":     "Your branch's stock on hand per product.",
    "low_stock":        "Your products at or below their reorder level, most urgent first.",
    "stock_requests":   "Your requests to HQ and where each one stands.",
    "shipments":        "Each delivery shipment to your branch: sent, received and damaged units.",
    "inventory_log":    "Every stock movement at your branch.",
    "discrepancies":    "Damaged or short units found on your deliveries.",
}


def report_catalog(role):
    """The reports a role can run, in presentation order, for the report builder:
    key, label, windowed, group and the one-line description."""
    admin = role == "Admin"
    out = []
    for key in _PRESENTATION:
        meta = REPORT_TYPES[key]
        if not meta["admin" if admin else "branch"]:
            continue
        out.append({
            "key": key,
            "label": meta["label"] if admin else meta.get("branch_label", meta["label"]),
            "windowed": meta["windowed"],
            "group": meta["group"],
            "description": meta["description"] if admin else _BRANCH_DESCRIPTIONS.get(key, meta["description"]),
        })
    return out
