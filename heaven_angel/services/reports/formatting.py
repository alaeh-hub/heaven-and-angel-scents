"""Colors, status badges and cell formatting shared by the PDF and Excel renderers."""
import datetime

from reportlab.lib import colors



INK = colors.HexColor("#17140D")


INK_FAINT = colors.HexColor("#5B5445")


ACCENT = colors.HexColor("#C9A227")


ACCENT_INK = colors.HexColor("#8A6D1F")


ACCENT_SOFT = colors.HexColor("#FBF1D6")


BORDER = colors.HexColor("#E9E0C9")


ROW_ALT = colors.HexColor("#F5F0E1")


# (background, text) hex pairs per badge style, matching the badge-* CSS classes.
BADGE_STYLES = {
    "pending":   ("#FBF0D9", "#C9820B"),  # --warning-soft / --warning
    "transit":   ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "fulfilled": ("#E1F5EC", "#17975E"),  # --success-soft / --success
    "rejected":  ("#FCE7EA", "#B31E30"),  # --red-soft / --red-ink
    "active":    ("#E1F5EC", "#17975E"),
    "inactive":  ("#FAF7EF", "#948C76"),  # --bg / --ink-faint
    "male":      ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "female":    ("#F5F0E1", "#17140D"),  # --surface-2 / --ink
    "neutral":   ("#FAF7EF", "#5B5445"),  # --bg / --ink-soft
}


# Column kind to badge style per value; mirrors the _macros.html badge macros.
BADGE_KIND_MAPS = {
    "status": {"Pending": "pending", "In Transit": "transit", "Partially Fulfilled": "transit", "Fulfilled": "fulfilled", "Rejected": "rejected"},
    "movement_type": {
        "PRODUCTION": "fulfilled", "DISPATCH": "transit", "RECEIPT": "fulfilled",
        "SALE": "neutral", "REFILL": "female", "FREEBIE": "pending",
        "ADJUSTMENT": "pending", "DAMAGE": "rejected",
        "PACKAGE_ORDER": "transit", "PACKAGE_RETURN": "inactive", "SALE_VOID": "rejected",
    },
    "sale_type": {"Sale": "fulfilled", "Refill": "transit", "Freebie": "pending"},
    "payment_method": {"Cash": "active", "Credit": "pending"},
    "variant": {"Male": "male", "Female": "female"},
    "active_status": {"Active": "active", "Discontinued": "inactive", "Deactivated": "inactive"},
    "role": {"Admin": "neutral", "Branch": "transit"},
    # Mirrors partners.html / partner_inquiries.html's inline badge:
    # badge-transit for Distributor, badge-neutral for Reseller.
    "partner_type": {"Distributor": "transit", "Reseller": "neutral"},
    # Mirrors packages.html's "For" column.
    "package_scope": {"Both": "pending", "Distributor": "transit", "Reseller": "neutral"},
    # Mirrors packages.html's Status column (Active/Retired badge).
    "package_status": {"Active": "active", "Retired": "inactive"},
    # Mirrors _macros.html's inquiry_status_badge line for line.
    "inquiry_status": {
        "New": "pending", "Contacted": "transit", "Follow-up": "neutral",
        "On Hold": "inactive", "Closed": "fulfilled", "Declined": "rejected",
    },
}


def badge_colors(kind, value):
    """Return (bg_hex, text_hex) for a badge-kind column's value, or
    None to fall back to plain text (unrecognized kind/value)."""
    style_key = BADGE_KIND_MAPS.get(kind, {}).get(value)
    return BADGE_STYLES.get(style_key) if style_key else None


def fmt_cell(value, ctype):
    """Format a cell value by its column type for PDF and Excel."""
    if value is None:
        return "—"
    if ctype == "money":
        return f"₱{float(value):,.2f}"
    if ctype == "price":
        # A per-unit price (₱0.1321 per mL of alcohol): 2 decimals would round it to ₱0.13.
        return f"₱{float(value):,.4f}"
    if ctype == "int":
        return f"{int(value):,}"
    if ctype == "num":
        # Fractional quantity shown to 3 decimals; int() would truncate 2.5 to 2.
        return f"{float(value):,.3f}"
    if ctype == "datetime":
        return fmt_datetime(value)
    return str(value)


def fmt_datetime(value):
    """Nov 8, 2026 1:13 PM (no leading zeros). A plain date has no time of day to show."""
    if isinstance(value, datetime.datetime):
        hour = value.hour % 12 or 12
        return f"{value.strftime('%b')} {value.day}, {value.year} {hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
    if isinstance(value, datetime.date):
        return f"{value.strftime('%b')} {value.day}, {value.year}"
    return str(value)


def sentence_case(label):
    """Sentence-case a column head; all-caps, digit or symbol words stay as they are."""
    words = str(label).split(" ")
    out = []
    for i, w in enumerate(words):
        if i > 0 and len(w) > 1 and w[0].isupper() and w[1:].islower() and w.isalpha():
            w = w.lower()
        out.append(w)
    return " ".join(out)
