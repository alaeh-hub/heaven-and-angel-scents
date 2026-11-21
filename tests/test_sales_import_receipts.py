"""Excel import: rows that share a Receipt label become ONE receipt (sales.order_id), like several
scents on one New sale. Blank = its own receipt, and a file with a clashing group is rejected whole."""
import datetime
import io

import pytest
from openpyxl import Workbook

from factories import get_inventory_qty, make_branch, make_inventory, make_product
from heaven_angel.db import TransactionAborted
from heaven_angel.services import sales_import as si
from heaven_angel.utils import ValidationError


def _rows(sql, query, params=()):
    cur = sql.cursor(dictionary=True)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    sql.commit()
    return rows


def _workbook(rows):
    """rows: tuples in the template's column order (SKU, Item, Qty, Price, Type, Payment, Buyer,
    Customer, Address, Date, Receipt). Row 1 = header, row 2 = the example row the importer skips."""
    wb = Workbook()
    ws = wb.active
    ws.title = si.SHEET_NAME
    ws.append(si.HEADERS)
    ws.append(["EXAMPLE", "example", 1, 1, "Sale", "Cash", "", "", "", "", ""])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _run(app, branch_id, rows):
    with app.app_context():
        parsed = si.parse_sales_import_workbook(_workbook(rows))
        return si.import_sales_rows(branch_id, parsed, None)


@pytest.fixture()
def shop(sql):
    branch_id = make_branch(sql)
    skus = [make_product(sql, price="50.00") for _ in range(4)]
    for sku in skus:
        make_inventory(sql, branch_id, sku, stock_qty=10)
    return branch_id, skus


def test_template_has_the_receipt_column():
    """Template has the receipt column."""
    assert si.HEADERS[-1] == "Receipt"
    assert len(si._HEADER_HINTS) == len(si.HEADERS) == len(si._COL_WIDTHS)


def test_rows_with_the_same_receipt_label_become_one_receipt(app, sql, shop):
    """Rows with the same receipt label become one receipt."""
    branch_id, s = shop
    today = datetime.date.today().isoformat()
    inserted, receipts = _run(app, branch_id, [
        (s[0], "", 1, 50, "Sale", "Cash", "", "Ana", "Balayan", today, "A"),
        (s[1], "", 2, 50, "Sale", "Cash", "", "Ana", "Balayan", today, "a"),      # label is case-insensitive
        (s[2], "", 1, 50, "Sale", "Cash", "", "Ben", "", today, "B"),
        (s[3], "", 1, 50, "Sale", "Cash", "", "Cy", "", today, ""),               # blank: its own receipt
    ])
    assert (inserted, receipts) == (4, 3)
    rows = _rows(sql, "SELECT sale_id, order_id, sku FROM sales WHERE branch_id = %s ORDER BY sale_id", (branch_id,))
    by_sku = {r["sku"]: r for r in rows}
    assert by_sku[s[0]]["order_id"] == by_sku[s[1]]["order_id"] == by_sku[s[0]]["sale_id"]
    assert by_sku[s[2]]["order_id"] is None and by_sku[s[3]]["order_id"] is None
    assert get_inventory_qty(sql, branch_id, s[1]) == 8


def test_a_typed_1_and_1_point_0_are_the_same_receipt(app, sql, shop):
    """A typed 1 and 1 point 0 are the same receipt."""
    branch_id, s = shop
    inserted, receipts = _run(app, branch_id, [
        (s[0], "", 1, 50, "Sale", "Cash", "", "", "", "", 1),
        (s[1], "", 1, 50, "Sale", "Cash", "", "", "", "", 1.0),
    ])
    assert (inserted, receipts) == (2, 1)


def test_rows_on_one_receipt_must_agree_and_nothing_is_saved_otherwise(app, sql, shop):
    """Rows on one receipt must agree and nothing is saved otherwise."""
    branch_id, s = shop
    with pytest.raises(ValidationError, match="Receipt 'A' must have the same customer name"):
        _run(app, branch_id, [
            (s[0], "", 1, 50, "Sale", "Cash", "", "Ana", "", "", "A"),
            (s[1], "", 1, 50, "Sale", "Cash", "", "Ben", "", "", "A"),
        ])
    with pytest.raises(ValidationError, match="payment method"):
        _run(app, branch_id, [
            (s[0], "", 1, 50, "Sale", "Cash", "", "Ana", "", "", "A"),
            (s[1], "", 1, 50, "Sale", "Credit", "Ana", "Ana", "", "", "A"),
        ])
    assert _rows(sql, "SELECT 1 FROM sales WHERE branch_id = %s", (branch_id,)) == []


def test_a_short_scent_rejects_the_whole_file(app, sql, shop):
    """A short scent rejects the whole file."""
    branch_id, s = shop
    with pytest.raises(TransactionAborted, match="Not enough"):
        _run(app, branch_id, [
            (s[0], "", 1, 50, "Sale", "Cash", "", "", "", "", "A"),
            (s[1], "", 99, 50, "Sale", "Cash", "", "", "", "", "A"),
        ])
    assert _rows(sql, "SELECT 1 FROM sales WHERE branch_id = %s", (branch_id,)) == []
    assert get_inventory_qty(sql, branch_id, s[0]) == 10


def test_a_receipt_has_a_product_limit(app, sql, shop):
    """A receipt has a product limit."""
    branch_id, s = shop
    rows = [(s[0], "", 1, 50, "Sale", "Cash", "", "", "", "", "BIG")] * 61
    with pytest.raises(ValidationError, match="at most 60"):
        _run(app, branch_id, rows)
