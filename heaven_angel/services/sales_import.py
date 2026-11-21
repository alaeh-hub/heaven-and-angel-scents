"""Bulk sales import — the "Import from Excel" flow on Record Sale.

build_sales_import_template() builds the downloadable .xlsx a branch/HQ
fills in and re-uploads; parse_sales_import_workbook() reads it back
into plain per-row tuples; import_sales_rows() then validates and
inserts the whole file as ONE atomic transaction — either every row
goes in, or nothing does, with every problem reported at once instead
of trickling out one row at a time (same "all-or-nothing" contract as
every other multi-write route in this app — see db.TransactionAborted).

Row shape mirrors exactly what routes/branch.py's and routes/admin/sales.py's
own record_sale() insert by hand: one sales row + one stock_movement_logs
row per line, with stock checked/decremented by the same
sale_stock.apply_sale_stock() (a Refill takes HQ bulk, and is refused
for a branch).

The optional last column, Receipt, puts several rows on ONE receipt (like several
scents on one New sale): rows with the same text there share a customer, payment, type
and date, and are tied together by sales.order_id exactly as the Record Sale form does.
Blank = the row is its own receipt, as before.
"""
import datetime
import decimal
import io

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, Protection
from openpyxl.worksheet.datavalidation import DataValidation

from heaven_angel.db import TransactionAborted, transaction
from heaven_angel.services.reports import XL_BORDER, XL_MONEY_FMT
from heaven_angel.services.reports.excel import XL_ALT_FILL, XL_BAND_FILL
from heaven_angel.services.sale_stock import apply_sale_stock
from heaven_angel.utils import (
    PAYMENT_METHODS, SALE_TYPES, ValidationError, business_now, parse_optional_text,
    parse_positive_decimal, parse_positive_int,
)
from heaven_angel.services.sale_entry import MAX_ORDER_LINES

SHEET_NAME = "Import Sales"

# Column order is load-bearing — parse_sales_import_workbook() and the
# template's own header row both read/write positionally against this.
HEADERS = [
    "SKU*",
    "Item",
    "Qty*",
    "Price*",
    "Type*",
    "Payment*",
    "Buyer",
    "Customer",
    "Address",
    "Date",
    "Receipt",
]
# Hover hints on the short headers (cell comments) keep the detail the
# old long labels carried.
_HEADER_HINTS = [
    "Pick the SKU from the dropdown (or type it). The Item fills in automatically.",
    "Fills in automatically from the SKU. Not imported.",
    "Quantity sold (whole number).",
    "Price actually charged. 0 or blank for a Freebie.",
    "Sale, Refill, or Freebie.",
    "Cash or Credit (ignored for Freebie).",
    "Buyer name — Credit sales only.",
    "Customer name (optional).",
    "Customer address (optional).",
    "Sale date as YYYY-MM-DD. Blank = today.",
    "Optional. Rows with the same text here (A, B, 1, 2...) go on ONE receipt: same customer, "
    "payment, type and date. Blank = its own receipt.",
]
_COL_WIDTHS = [16, 26, 9, 12, 12, 13, 18, 20, 26, 14, 12]
XL_FONT = "Segoe UI"

# Cap per import batch, so one bad file cannot hold a long transaction.
MAX_IMPORT_ROWS = 500


def build_sales_import_template(inventory):
    """Build the downloadable blank template.

    `inventory` is the same branch-scoped
    `branch_inventory JOIN products` rows record_sale() already queries
    (sku, item_name, variant, unit, price, stock_qty) — used only to
    fill a read-only "Products" reference sheet so whoever fills the
    template can copy a valid SKU instead of guessing one.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    ws.sheet_properties.tabColor = "0C0B0A"
    # Every header is black with white text; a trailing "*" marks the
    # required columns.
    for c, label in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = Font(name=XL_FONT, size=9, bold=True, color="FFFFFF")
        cell.fill = XL_BAND_FILL
        cell.comment = Comment(_HEADER_HINTS[c - 1], "Heaven & Angel")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = XL_BORDER
        ws.column_dimensions[chr(64 + c)].width = _COL_WIDTHS[c - 1]
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"

    # Row 2 is an example row; the parser always skips it.
    example_values = [
        inventory[0]["sku"] if inventory else "SKU-HERE",
        (inventory[0]["item_name"] if inventory else "Example Item") + " — example row, not imported",
        1, 120.00, "Sale", "Cash", "", "Juan Dela Cruz", "", "", "",
    ]
    for c, value in enumerate(example_values, start=1):
        cell = ws.cell(row=2, column=c, value=value)
        cell.font = Font(name=XL_FONT, size=10, italic=True, color="8A90A0")
    ws.cell(row=2, column=4).number_format = XL_MONEY_FMT

    # Blank working rows below the example, ready to type/paste into.
    n_rows = 200
    last_data_row = 3 + n_rows

    sale_type_dv = DataValidation(
        type="list", formula1=f'"{",".join(SALE_TYPES)}"', allow_blank=True,
        showErrorMessage=True, errorTitle="Invalid Sale Type",
        error="Pick Sale, Refill, or Freebie from the dropdown.",
    )
    ws.add_data_validation(sale_type_dv)
    sale_type_dv.add(f"E2:E{last_data_row}")

    payment_dv = DataValidation(
        type="list", formula1=f'"{",".join(PAYMENT_METHODS)}"', allow_blank=True,
        showErrorMessage=True, errorTitle="Invalid Payment Method",
        error="Pick Cash or Credit from the dropdown.",
    )
    ws.add_data_validation(payment_dv)
    payment_dv.add(f"F2:F{last_data_row}")

    # SKU dropdown fills Item from the reference sheet (Item is never read on import).
    ref_sheet = "Products (reference)"
    if inventory:
        sku_dv = DataValidation(
            type="list", formula1=f"='{ref_sheet}'!$A$2:$A${len(inventory) + 1}",
            allow_blank=True, showErrorMessage=True, errorTitle="Unknown SKU",
            error="Pick a SKU from the dropdown (only SKUs stocked here can be imported).",
        )
        ws.add_data_validation(sku_dv)
        sku_dv.add(f"A3:A{last_data_row}")
        for r in range(3, last_data_row + 1):
            ws.cell(row=r, column=2).value = (
                f"=IF(A{r}=\"\",\"\",IFERROR(VLOOKUP(A{r},'{ref_sheet}'!$A:$B,2,FALSE),\"\"))")

    for r in range(2, last_data_row + 1):
        for c in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = XL_BORDER
            if r > 2:
                cell.font = Font(name=XL_FONT, size=10)
            # Zebra the blank working rows (the example row keeps its own tint).
            if r > 2 and r % 2 == 1:
                cell.fill = XL_ALT_FILL
        if r > 2:
            ws.cell(row=r, column=4).number_format = XL_MONEY_FMT

    # Protect the sheet and unlock every cell but Item (no password).
    for r in range(2, last_data_row + 1):
        for c in range(1, len(HEADERS) + 1):
            if c != 2:
                ws.cell(row=r, column=c).protection = Protection(locked=False)
    ws.protection.sheet = True
    ws.protection.formatColumns = False
    ws.protection.formatRows = False

    # ---- reference sheet: every SKU this branch can actually sell ----
    ref = wb.create_sheet("Products (reference)")
    ref.sheet_properties.tabColor = "0C0B0A"
    ref_headers = ["SKU", "Item", "Variant", "Unit", "Price", "Stock"]
    for c, label in enumerate(ref_headers, start=1):
        cell = ref.cell(row=1, column=c, value=label)
        cell.font = Font(name=XL_FONT, size=9, bold=True, color="FFFFFF")
        cell.fill = XL_BAND_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = XL_BORDER
    ref.row_dimensions[1].height = 22
    ref.freeze_panes = "A2"
    for r, row in enumerate(inventory, start=2):
        values = [row["sku"], row["item_name"], row["variant"], row["unit"],
                  float(row["price"]), int(row["stock_qty"])]
        for c, value in enumerate(values, start=1):
            cell = ref.cell(row=r, column=c, value=value)
            cell.font = Font(name=XL_FONT, size=10)
            cell.border = XL_BORDER
            if r % 2 == 1:
                cell.fill = XL_ALT_FILL
        ref.cell(row=r, column=5).number_format = XL_MONEY_FMT
    for c, width in enumerate([16, 26, 10, 12, 12, 14], start=1):
        ref.column_dimensions[chr(64 + c)].width = width

    note = ref.cell(
        row=len(inventory) + 3, column=1,
        value="Copy a SKU from this list into the Import Sales sheet — only SKUs stocked at this branch can be imported.",
    )
    note.font = Font(name=XL_FONT, italic=True, size=9.5, color="8A7A4A")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def parse_sales_import_workbook(file_storage):
    """Read the uploaded file's Import Sales sheet into a list of
    `(excel_row_number, values_tuple)` pairs, skipping the header row
    (1), the built-in example row (2, see build_sales_import_template()),
    and any fully blank row. Raises ValidationError if the file can't be
    read as an .xlsx at all.
    """
    try:
        wb = load_workbook(file_storage, data_only=True, read_only=True)
    except Exception:
        raise ValidationError(
            "That doesn't look like a valid .xlsx file — download the template and fill that in.")

    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.worksheets[0]
    rows = []
    # Bounded read: a huge empty range cannot make the worker scan it all.
    last_row = MAX_IMPORT_ROWS + 1000
    for excel_row_num, row in enumerate(
            ws.iter_rows(min_row=3, max_row=last_row, max_col=12, values_only=True), start=3):
        if row is None or all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        rows.append((excel_row_num, row))
    return rows


def _cell(row, idx):
    """Cell value at an index, or None if the row is short."""
    return row[idx] if idx < len(row) else None


def _parse_import_date(raw):
    """Excel gives back a real datetime for a date-formatted cell, or a
    plain string/None for a text cell — accept either.
    """
    now = business_now()
    today = now.date()
    if isinstance(raw, datetime.datetime):
        picked = raw.date()
    elif isinstance(raw, datetime.date):
        picked = raw
    else:
        raw = str(raw or "").strip()
        if not raw:
            picked = today
        else:
            try:
                picked = datetime.datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError(
                    "Sale Date must be YYYY-MM-DD, or blank for today.")
    if picked > today:
        raise ValidationError("Sale Date can't be in the future.")
    return datetime.datetime.combine(picked, now.time())


def _parse_row(excel_row_num, row):
    """Validate one import row and return its sale."""
    sku = str(_cell(row, 0) or "").strip()
    if not sku:
        raise ValidationError("SKU is required.")

    qty_raw = _cell(row, 2)
    # Excel stores whole numbers as floats (1.0); int(str()) would reject them.
    if isinstance(qty_raw, float) and qty_raw.is_integer():
        qty_raw = int(qty_raw)
    qty = parse_positive_int(qty_raw, "Qty Sold")

    sale_type = str(_cell(row, 4) or "").strip().title()
    if sale_type not in SALE_TYPES:
        raise ValidationError(
            f"Sale Type must be one of: {', '.join(SALE_TYPES)}.")

    # Freebie is always 0 whatever the price column says; other types need a positive price.
    if sale_type == "Freebie":
        unit_price = decimal.Decimal("0")
    else:
        unit_price = parse_positive_decimal(_cell(row, 3), "Price Charged").quantize(
            decimal.Decimal("0.01"))
        if unit_price < decimal.Decimal("0.01"):
            raise ValidationError("Price Charged must be at least 0.01.")

    # Freebie is never paid for, so Payment Method is forced to Cash.
    if sale_type == "Freebie":
        payment_method = "Cash"
    else:
        payment_method = str(_cell(row, 5) or "").strip().title()
        if payment_method not in PAYMENT_METHODS:
            raise ValidationError(
                f"Payment Method must be one of: {', '.join(PAYMENT_METHODS)}.")

    raw_buyer = str(_cell(row, 6) or "").strip()
    buyer_name = None
    if payment_method == "Credit":
        if not raw_buyer:
            raise ValidationError("Buyer Name is required for a Credit sale.")
        if len(raw_buyer) > 120:
            raise ValidationError("Buyer Name is too long (max 120 characters).")
        buyer_name = raw_buyer

    customer_name = parse_optional_text(_cell(row, 7), "Customer Name", 120)
    customer_address = parse_optional_text(
        _cell(row, 8), "Customer Address", 255)
    sold_at = _parse_import_date(_cell(row, 9))

    # Excel hands back a typed 1 as 1.0: "1" and "1.0" must be the same receipt.
    receipt_raw = _cell(row, 10)
    if isinstance(receipt_raw, float) and receipt_raw.is_integer():
        receipt_raw = int(receipt_raw)
    receipt = str(receipt_raw if receipt_raw is not None else "").strip()
    if len(receipt) > 40:
        raise ValidationError("Receipt is too long (max 40 characters).")

    return {
        "row": excel_row_num,
        "sku": sku,
        "qty": qty,
        "unit_price": unit_price,
        "sale_type": sale_type,
        "payment_method": payment_method,
        "buyer_name": buyer_name,
        "customer_name": customer_name,
        "customer_address": customer_address,
        "sold_at": sold_at,
        "receipt": receipt.lower(),
        "receipt_label": receipt,
    }


def import_sales_rows(branch_id, raw_rows, user_id):
    """Validate and insert every row from parse_sales_import_workbook()
    as one atomic transaction. Returns (rows inserted, receipts they make up): rows
    sharing a Receipt label are one receipt, every other row is its own.

    Raises ValidationError for problems that don't need the database
    (empty file, too many rows, bad values on individual rows) and
    TransactionAborted (rolled back, nothing written) for a problem
    only the database can tell us — an unknown SKU or not enough stock.
    Both carry a single newline-joined message; callers flash it one
    line per flash message.
    """
    if not raw_rows:
        raise ValidationError("The file has no data rows to import.")
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ValidationError(
            f"Too many rows in one file (max {MAX_IMPORT_ROWS}) — split it into smaller batches.")

    parsed = []
    errors = []
    for excel_row_num, row in raw_rows:
        try:
            parsed.append(_parse_row(excel_row_num, row))
        except ValidationError as err:
            errors.append(f"Row {excel_row_num}: {err}")

    # Rows sharing a Receipt label are one receipt: one customer, one payment, one type, one date.
    groups = {}
    for item in parsed:
        if item["receipt"]:
            groups.setdefault(item["receipt"], []).append(item)
    for label, members in groups.items():
        first = members[0]
        if len(members) > MAX_ORDER_LINES:
            errors.append(f"Receipt '{first['receipt_label']}' has {len(members)} rows; "
                          f"one receipt can hold at most {MAX_ORDER_LINES} products.")
            continue
        same = ("sale_type", "payment_method", "buyer_name", "customer_name", "customer_address")
        for item in members[1:]:
            clash = [k for k in same if item[k] != first[k]]
            if item["sold_at"].date() != first["sold_at"].date():
                clash.append("date")
            if clash:
                errors.append(f"Row {item['row']}: Receipt '{item['receipt_label']}' must have the same "
                              f"{', '.join(c.replace('_', ' ') for c in clash)} as row {first['row']}.")
        for item in members:
            item["sold_at"] = first["sold_at"]          # one receipt, one moment

    if errors:
        shown = errors[:20]
        if len(errors) > len(shown):
            shown.append(f"…and {len(errors) - len(shown)} more row(s) with problems.")
        raise ValidationError("\n".join(shown))

    inserted = 0
    new_ids = {}                                       # excel row -> new sale_id
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        for item in parsed:
            sku = item["sku"]
            cur.execute("SELECT sku, item_name, unit FROM sale_catalog WHERE sku = %s", (sku,))
            product = cur.fetchone()
            if not product:
                cur.close()
                raise TransactionAborted(f"Row {item['row']}: SKU '{sku}' doesn't exist.")
            cur.execute(
                """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method,
                                      buyer_name, customer_name, customer_address, sold_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (branch_id, sku, item["qty"], item["unit_price"], item["sale_type"],
                 item["payment_method"], item["buyer_name"], item["customer_name"],
                 item["customer_address"], item["sold_at"]),
            )
            if item["sale_type"] == "Freebie":
                notes = "Freebie / giveaway (imported)"
            else:
                notes = "Imported from Excel" if item["payment_method"] == "Cash" else f"Imported from Excel — Credit — {item['buyer_name']}"
            # Same stock rules as Record Sale: rows re-lock stock; a Refill takes HQ bulk and is refused at a branch.
            sale_id = cur.lastrowid
            new_ids[item["row"]] = sale_id
            try:
                apply_sale_stock(cur, sale_id=sale_id, branch_id=branch_id, product=product,
                                 qty=item["qty"], sale_type=item["sale_type"], notes=notes,
                                 user_id=user_id)
            except TransactionAborted as err:
                cur.close()
                raise TransactionAborted(f"Row {item['row']}: {err}") from None
            inserted += 1
        # Tie the rows of each shared receipt together (sales.order_id = the first row's id).
        for members in groups.values():
            if len(members) > 1:
                ids = [new_ids[m["row"]] for m in members]
                cur.execute("UPDATE sales SET order_id = %s WHERE sale_id IN (" + ", ".join(["%s"] * len(ids)) + ")",
                            (ids[0], *ids))
        cur.close()

    receipts = inserted - sum(len(m) - 1 for m in groups.values() if len(m) > 1)
    return inserted, receipts
