"""Stock side of recording and voiding a sale — shared by admin/branch
Record Sale and the Excel sales import, so every way a sale enters the
system moves stock the same way.

Recording (apply_sale_stock):
  - Sale / Freebie of a bottle: the product's own stock at that location
    goes down by the quantity sold.
  - Bulk sale (sold by the mL, sku '<code>-BULK') and Refill (the customer
    brings their own bottle): neither takes a bottle, they take the scent's
    bulk, straight out of its bulk batches (bulk_batches.base_code = the
    product code, e.g. A1) — a Refill by bottles x bottle size in mL, a
    bulk sale by the typed mL. HQ-only (batches are HQ's, branches never
    hold bulk). The OLDEST batch with mL left is used first, then the next
    one if it runs out, and each batch used is recorded with its cost per
    mL in sale_batch_usage (that is where the sale's cost comes from).
    Blocked when the scent's batches don't hold enough.
  Each movement log row carries reference_id = sale_id, so a void can
  reverse exactly what the sale took.

Voiding (void_sale): puts that stock back (logged as SALE_VOID; a bulk sale
or refill gives its mL back to the batches it came from), keeps a permanent
snapshot in sale_voids with who voided it and why, then deletes the sales
row so every revenue / units / customer / credit total corrects itself
without each query having to filter voids out.

Settling (settle_credit): a Credit sale was never "wrong" the way a voided
one is — the product really did leave on credit — so this doesn't touch
stock or delete anything. It just stamps credit_settled_at/_by once the
buyer pays it off, so routes' credit_purchases() (branch and admin) can
exclude it from what's still outstanding.

All three run inside the caller's transaction() on its dictionary cursor
and raise TransactionAborted (rolled back, nothing written) with a
message fit to flash.
"""
import decimal

from heaven_angel.db import TransactionAborted
from heaven_angel.utils import bottle_size_ml

HQ_BRANCH_ID = 1

_MOVEMENT_BY_SALE_TYPE = {"Sale": "SALE", "Refill": "REFILL", "Freebie": "FREEBIE"}


def base_code_of(product):
    """The product code (A1) a product or a '<code>-BULK' sale key belongs to."""
    suffix = f"-{product['unit']}"
    sku = product["sku"]
    return sku[:-len(suffix)] if sku.endswith(suffix) else sku


def bulk_ml_needed(product, qty):
    """mL of bulk a sale of `qty` of `product` takes, or None if it takes no bulk.

    A bulk sale is already in mL; a Refill of a bottle size takes bottles x
    that size in mL. Called with the sale type already known to be a Refill
    for the bottle case (see apply_sale_stock).
    """
    if product["unit"] == "BULK":
        return qty
    return qty * int(bottle_size_ml(product["unit"]))


def take_bulk_from_batches(cur, *, sale_id, base_code, scent_name, ml):
    """Take `ml` of `base_code`'s bulk out of its batches, oldest first.

    Row-locks the scent's batches, raises TransactionAborted when they don't
    hold enough, and records each batch used (and its cost per mL) in
    sale_batch_usage so the sale can be costed and voided exactly.
    """
    cur.execute(
        """SELECT batch_id, remaining_ml, cost_per_ml FROM bulk_batches
           WHERE base_code = %s AND remaining_ml > 0
           ORDER BY created_at, batch_id FOR UPDATE""",
        (base_code,),
    )
    batches = cur.fetchall()
    have = sum((b["remaining_ml"] for b in batches), decimal.Decimal("0"))
    if have < ml:
        raise TransactionAborted(
            f"Not enough {scent_name} bulk in its batches: need {ml:g} mL, have {have:g} mL.")
    left = decimal.Decimal(ml)
    for batch in batches:
        if left <= 0:
            break
        take = min(batch["remaining_ml"], left)
        cur.execute(
            "UPDATE bulk_batches SET remaining_ml = remaining_ml - %s WHERE batch_id = %s",
            (take, batch["batch_id"]),
        )
        cur.execute(
            "INSERT INTO sale_batch_usage (sale_id, batch_id, ml, cost_per_ml) VALUES (%s, %s, %s, %s)",
            (sale_id, batch["batch_id"], take, batch["cost_per_ml"]),
        )
        left -= take


def apply_sale_stock(cur, *, sale_id, branch_id, product, qty, sale_type, notes, user_id):
    """Deduct the stock a just-inserted sale consumes and log it."""
    if sale_type == "Refill" or product["unit"] == "BULK":
        # Bulk sale or Refill: no bottle moves, the scent's bulk batches pay.
        if branch_id != HQ_BRANCH_ID:
            raise TransactionAborted(
                "Bulk sales and refills are only recorded at HQ — branches don't hold bulk stock.")
        take_bulk_from_batches(
            cur, sale_id=sale_id, base_code=base_code_of(product), scent_name=product["item_name"],
            ml=bulk_ml_needed(product, qty))
        return
    source_sku, amount = product["sku"], qty
    unit_label = ""
    what = product["item_name"]

    cur.execute(
        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
        (branch_id, source_sku),
    )
    row = cur.fetchone()
    have = row["stock_qty"] if row else 0
    if have < amount:
        raise TransactionAborted(
            f"Not enough {what} on hand: need {amount}{unit_label}, have {have}{unit_label}.")
    after_qty = have - amount
    cur.execute(
        "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
        (after_qty, branch_id, source_sku),
    )
    cur.execute(
        """INSERT INTO stock_movement_logs
           (branch_id, sku, change_qty, movement_type, notes,
            created_by_user_id, reference_type, reference_id, before_qty, after_qty)
           VALUES (%s, %s, %s, %s, %s, %s, 'SALE', %s, %s, %s)""",
        (branch_id, source_sku, -amount, _MOVEMENT_BY_SALE_TYPE[sale_type], notes,
         user_id, sale_id, have, after_qty),
    )


def void_sale(cur, *, sale_id, reason, user_id, username, branch_id=None, today_only=False):
    """Void one sale: return its stock, snapshot it, delete it.

    branch_id / today_only restrict a branch user to their own branch's
    sales recorded today. Returns the voided sale's snapshot dict.
    """
    cur.execute(
        """SELECT s.*, p.item_name, p.unit, DATE(s.recorded_at) = CURDATE() AS recorded_today
           FROM sales s JOIN sale_catalog p ON p.sku = s.sku
           WHERE s.sale_id = %s FOR UPDATE""",
        (sale_id,),
    )
    sale = cur.fetchone()
    if not sale or (branch_id is not None and sale["branch_id"] != branch_id):
        raise TransactionAborted("That sale no longer exists.")
    if today_only and not sale["recorded_today"]:
        raise TransactionAborted(
            "Branch staff can only void sales recorded today — ask HQ to void older ones.")

    # Give back exactly what the sale took (bulk: mL per batch via sale_batch_usage, capped at batch size).
    cur.execute(
        "SELECT batch_id, ml FROM sale_batch_usage WHERE sale_id = %s FOR UPDATE", (sale_id,))
    batch_usage = cur.fetchall()
    for use in batch_usage:
        cur.execute(
            """UPDATE bulk_batches
               SET remaining_ml = LEAST(total_volume_ml, remaining_ml + %s) WHERE batch_id = %s""",
            (use["ml"], use["batch_id"]),
        )

    cur.execute(
        """SELECT branch_id, sku, change_qty FROM stock_movement_logs
           WHERE reference_type = 'SALE' AND reference_id = %s AND change_qty < 0""",
        (sale_id,),
    )
    taken = [(r["branch_id"], r["sku"], -r["change_qty"]) for r in cur.fetchall()]
    if not taken and not batch_usage and sale["sale_type"] != "Refill" and sale["unit"] != "BULK":
        taken = [(sale["branch_id"], sale["sku"], sale["qty_sold"])]

    for loc, sku, qty in taken:
        cur.execute(
            """INSERT INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
            (loc, sku, qty),
        )
        cur.execute(
            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s", (loc, sku))
        after_qty = cur.fetchone()["stock_qty"]
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, reference_id, before_qty, after_qty)
               VALUES (%s, %s, %s, 'SALE_VOID', %s, %s, 'SALE_VOID', %s, %s, %s)""",
            (loc, sku, qty, f"Voided {sale['sale_type'].lower()} #{sale_id} — {reason}"[:255],
             user_id, sale_id, after_qty - qty, after_qty),
        )

    cur.execute(
        """INSERT INTO sale_voids
           (sale_id, branch_id, sku, item_name, unit, qty_sold, unit_price, sale_type, payment_method,
            buyer_name, customer_name, sold_at, reason, voided_by_user_id, voided_by_username)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (sale_id, sale["branch_id"], sale["sku"], sale["item_name"], sale["unit"], sale["qty_sold"],
         sale["unit_price"], sale["sale_type"], sale["payment_method"], sale.get("buyer_name"),
         sale.get("customer_name"), sale["sold_at"], reason, user_id, username),
    )
    cur.execute("DELETE FROM sales WHERE sale_id = %s", (sale_id,))
    return sale


def settle_credit(cur, *, sale_id, username, branch_id=None):
    """Mark one Credit sale as paid back. branch_id restricts a branch
    user to their own branch's sales, same convention as void_sale.
    Returns the settled sale's row.
    """
    cur.execute(
        """SELECT s.*, p.item_name FROM sales s JOIN sale_catalog p ON p.sku = s.sku
           WHERE s.sale_id = %s FOR UPDATE""",
        (sale_id,),
    )
    sale = cur.fetchone()
    if not sale or (branch_id is not None and sale["branch_id"] != branch_id):
        raise TransactionAborted("That sale no longer exists.")
    if sale["payment_method"] != "Credit":
        raise TransactionAborted("That sale isn't a Credit sale.")
    if sale["credit_settled_at"] is not None:
        raise TransactionAborted("That credit was already marked as paid.")
    cur.execute(
        "UPDATE sales SET credit_settled_at = NOW(), credit_settled_by = %s WHERE sale_id = %s",
        (username, sale_id),
    )
    return sale
