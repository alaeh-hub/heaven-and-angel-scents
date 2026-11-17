"""Recording a sale from the Record Sale form — shared by HQ (routes/admin/sales.py)
and branches (routes/branch/sales.py), which differ only in wording, whether a
Refill is allowed, and which branch's stock is used.

parse_sale_form() validates in a fixed order (the first problem found is the one
the person is told about); record_sale() inserts the row and moves the stock in
one transaction.
"""
import decimal
from dataclasses import dataclass

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.services.sale_stock import apply_sale_stock
from heaven_angel.utils import (
    PAYMENT_METHODS, SALE_TYPES, ValidationError, parse_optional_text, parse_past_date,
    parse_positive_decimal, parse_positive_int,
)

MAX_NAME_LENGTH = 120


class SaleFormError(Exception):
    """A form problem to flash to the user as-is."""


@dataclass(frozen=True)
class SaleInput:
    sku: str
    qty: int
    unit_price: decimal.Decimal
    sale_type: str
    payment_method: str
    buyer_name: str | None
    customer_name: str | None
    customer_address: str | None
    sold_at: object


def parse_sale_form(form, *, qty_label, sale_type_prompt, product_prompt,
                    allow_refill=True, refill_error=None):
    """Validate the Record Sale form; returns a SaleInput or raises SaleFormError."""
    sku = form.get("sku")
    sale_type = form.get("sale_type")
    payment_method = form.get("payment_method")
    raw_buyer = form.get("buyer_name", "").strip()

    try:
        qty = parse_positive_int(form.get("qty_sold"), qty_label)
        # Strictly positive for Sale/Refill: a ₱0 "Sale" would otherwise be a
        # way to move stock out with no revenue and nothing marking it as a
        # giveaway. Freebie is the explicit way to log that — always ₱0,
        # whatever was actually typed (the field is read-only client-side, but
        # that is never trusted alone).
        if sale_type == "Freebie":
            unit_price = decimal.Decimal("0")
        else:
            unit_price = parse_positive_decimal(form.get("unit_price"), "Price charged")
        customer_name = parse_optional_text(form.get("customer_name"), "Customer name", MAX_NAME_LENGTH)
        customer_address = parse_optional_text(form.get("customer_address"), "Customer address", 255)
        # Lets a sale that happened earlier be logged under that date; blank is today.
        sold_at = parse_past_date(form.get("sold_at"), "Sale date")
    except ValidationError as err:
        raise SaleFormError(str(err)) from err

    if sale_type not in SALE_TYPES:
        raise SaleFormError(sale_type_prompt)
    if sale_type == "Refill" and not allow_refill:
        raise SaleFormError(refill_error)
    # A freebie is never paid for: payment_method only means something for a
    # real charge (Cash vs. Credit), so it is forced to Cash whatever was submitted.
    if sale_type == "Freebie":
        payment_method = "Cash"
    if payment_method not in PAYMENT_METHODS:
        raise SaleFormError("Select a payment method.")
    if not sku:
        raise SaleFormError(product_prompt)

    buyer_name = None
    if payment_method == "Credit":
        # A plain free-text name, not a lookup against login accounts —
        # whoever collects the credit reconciles it themselves.
        if not raw_buyer:
            raise SaleFormError("Enter who this credit sale is for.")
        if len(raw_buyer) > MAX_NAME_LENGTH:
            raise SaleFormError(f"Name is too long (max {MAX_NAME_LENGTH} characters).")
        buyer_name = raw_buyer

    return SaleInput(sku, qty, unit_price, sale_type, payment_method, buyer_name,
                     customer_name, customer_address, sold_at)


MAX_ORDER_LINES = 60


def parse_sale_order_form(form, **kwargs):
    """Validate a Record Sale form that may carry several scents; returns a list
    of SaleInput (one per scent) or raises SaleFormError.

    The scents the person added to the order come as parallel lists
    cart_sku / cart_qty / cart_price; whatever is still filled in on the form's
    own product, quantity and price fields counts as one more. Everything else
    (type, payment, customer, date) is shared by the whole order. With nothing
    added it is exactly parse_sale_form, so a single sale behaves as before.
    Same scent added twice is merged when the price is the same.
    """
    skus, qtys, prices = form.getlist("cart_sku"), form.getlist("cart_qty"), form.getlist("cart_price")
    if not (len(skus) == len(qtys) == len(prices)):
        raise SaleFormError("The order list is out of step — please re-add the items.")
    lines = list(zip(skus, qtys, prices))
    if form.get("sku"):
        lines.append((form.get("sku"), form.get("qty_sold"), form.get("unit_price")))
    if not lines:
        return [parse_sale_form(form, **kwargs)]       # raises the usual "pick a product" message
    if len(lines) > MAX_ORDER_LINES:
        raise SaleFormError(f"An order can have at most {MAX_ORDER_LINES} different products.")
    base = form.to_dict() if hasattr(form, "to_dict") else dict(form)
    parsed = {}
    for sku, qty, price in lines:
        one = parse_sale_form({**base, "sku": sku, "qty_sold": qty, "unit_price": price}, **kwargs)
        key = (one.sku, one.unit_price)
        if key in parsed:
            parsed[key] = SaleInput(**{**one.__dict__, "qty": parsed[key].qty + one.qty})
        else:
            parsed[key] = one
    return list(parsed.values())


def _record_line(cur, sale, *, branch_id, user_id, note_suffix):
    """Insert one sale row and move its stock; returns the new sale_id."""
    cur.execute("SELECT sku, item_name, unit FROM sale_catalog WHERE sku = %s", (sale.sku,))
    product = cur.fetchone()
    if not product:
        raise TransactionAborted("That product no longer exists.")
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method,
                              buyer_name, customer_name, customer_address, sold_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (branch_id, sale.sku, sale.qty, sale.unit_price, sale.sale_type, sale.payment_method,
         sale.buyer_name, sale.customer_name, sale.customer_address, sale.sold_at),
    )
    sale_id = cur.lastrowid
    if sale.sale_type == "Freebie":
        notes = "Freebie / giveaway" + note_suffix
    elif sale.payment_method == "Cash":
        notes = "Point-of-sale" + note_suffix
    else:
        notes = f"Credit — {sale.buyer_name}"
    # Sale/Freebie take the bottle(s) from this branch's stock; a Refill
    # takes the scent's bulk mL instead (see sale_stock.py). Row-locks the
    # SKU's stock so a concurrent sale of it waits, and blocks if short.
    apply_sale_stock(cur, sale_id=sale_id, branch_id=branch_id, product=product,
                     qty=sale.qty, sale_type=sale.sale_type, notes=notes, user_id=user_id)
    return sale_id


def record_sale_order(sales, *, branch_id, user_id, note_suffix=""):
    """Record every scent of one order in ONE transaction: all of it or none of it.

    Raises TransactionAborted with a user-facing message (unknown product, not
    enough stock of any scent). With more than one scent, the rows are tied
    together by sales.order_id (the first row's sale_id). Returns that first sale_id.
    """
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        ids = [_record_line(cur, sale, branch_id=branch_id, user_id=user_id, note_suffix=note_suffix)
               for sale in sales]
        if len(ids) > 1:
            cur.execute("UPDATE sales SET order_id = %s WHERE sale_id IN (" + ", ".join(["%s"] * len(ids)) + ")",
                        (ids[0], *ids))
        cur.close()
    return ids[0]


def record_sale(sale, *, branch_id, user_id, note_suffix=""):
    """Record a single-scent sale (see record_sale_order). `note_suffix` is appended to the
    stock-log note of cash sales and freebies (HQ passes " (HQ)")."""
    return record_sale_order([sale], branch_id=branch_id, user_id=user_id, note_suffix=note_suffix)


def annotate_receipts(rows):
    """Tell each sales row which receipt it is on, so a list can show ONE Receipt button per receipt.

    An order of several scents is several sales rows (see sales.order_id). Adds to every row:
      receipt_no       the receipt's number (the order's id, or the sale's own id),
      receipt_lines    how many products are on that receipt,
      is_receipt_head  True on the receipt's first remaining row (the one that gets the button).
    Rows are the dicts query() returns; the same list is returned.
    """
    order_ids = {r["order_id"] for r in rows if r.get("order_id")}
    info = {}
    if order_ids:
        marks = ", ".join(["%s"] * len(order_ids))
        for r in query(f"SELECT order_id, MIN(sale_id) AS head_id, COUNT(*) AS n FROM sales "
                       f"WHERE order_id IN ({marks}) GROUP BY order_id", tuple(order_ids)):
            info[r["order_id"]] = r
    for r in rows:
        order = info.get(r.get("order_id"))
        if order:
            r["receipt_no"], r["receipt_lines"] = r["order_id"], order["n"]
            r["is_receipt_head"] = r["sale_id"] == order["head_id"]
        else:
            r["receipt_no"], r["receipt_lines"], r["is_receipt_head"] = r["sale_id"], 1, True
    return rows
