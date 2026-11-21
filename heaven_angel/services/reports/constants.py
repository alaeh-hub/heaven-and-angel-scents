"""Shared SQL fragments and filter choice lists for the on-demand reports."""


from heaven_angel.utils import (
    PARTNER_TYPES, PAYMENT_METHODS, SALE_TYPES, SALE_UNITS,
)


# Units behind a discrepancy log row: event change_qty if set, else the delivery line (legacy), else -change_qty.
DISCREPANCY_ITEM_JOIN = (
    "LEFT JOIN stock_request_items sri "
    "ON sri.request_id = sml.reference_id AND sri.sku = sml.sku"
)


# The single "low stock" rule used by pages, reports and the AI; bulk mL is never low.
LOW_STOCK_WHERE = "bi.stock_qty <= bi.reorder_level AND p.unit <> 'BULK'"


DISCREPANCY_UNITS_SQL = (
    "(CASE WHEN sri.item_id IS NULL THEN -sml.change_qty "
    "WHEN sml.change_qty <> 0 THEN -sml.change_qty "
    "WHEN sml.movement_type = 'DAMAGE' THEN sri.damaged_qty "
    "ELSE GREATEST(COALESCE(sri.dispatched_qty, 0) - COALESCE(sri.received_qty, 0) "
    "- sri.damaged_qty, 0) END)"
)


MAX_ROWS = 1000


RECENT_CHOICES = (20, 50, 100, 200)


STATUS_CHOICES = ("Pending", "In Transit", "Partially Fulfilled", "Fulfilled", "Rejected")


MOVEMENT_TYPE_CHOICES = ("PRODUCTION", "DISPATCH", "RECEIPT",
                         "SALE", "REFILL", "FREEBIE", "ADJUSTMENT", "DAMAGE",
                         "PACKAGE_ORDER", "PACKAGE_RETURN", "SALE_VOID")


VARIANT_CHOICES = ("Male", "Female")


ROLE_CHOICES = ("Admin", "Branch")


UNIT_CHOICES = SALE_UNITS


SALE_TYPE_CHOICES = SALE_TYPES


PAYMENT_METHOD_CHOICES = PAYMENT_METHODS


PARTNER_TYPE_CHOICES = PARTNER_TYPES


# Mirrors packages.partner_scope: "Both" plus each partner type.
PACKAGE_SCOPE_CHOICES = ("Both",) + PARTNER_TYPES


# packages.is_active as it reads on screen (Packages page's Status
# column) rather than the raw boolean.
PACKAGE_STATUS_CHOICES = ("Active", "Retired")


# Mirrors partner_inquiries.status's ENUM — same pipeline shown on the
# Partner Inquiries page and _macros.html's inquiry_status_badge.
INQUIRY_STATUS_CHOICES = ("New", "Contacted", "Follow-up",
                          "On Hold", "Closed", "Declined")
