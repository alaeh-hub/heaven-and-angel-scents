"""One receipt, several scents: Record Sale with a list of products (cart_sku / cart_qty /
cart_price), recorded all-or-nothing, printed 10 products to a slip, verified and voided
as one order."""
import re

import pytest

from factories import (get_form_token, get_inventory_qty, login, make_branch, make_inventory,
                       make_product, make_user)
from heaven_angel.utils import make_receipt_code

URL = "/branch/record-sale"


def _rows(sql, query, params=()):
    cur = sql.cursor(dictionary=True)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    sql.commit()
    return rows


def _setup(client, sql, count, stock=5):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    skus = [make_product(sql, price="50.00") for _ in range(count)]
    for sku in skus:
        make_inventory(sql, branch_id, sku, stock_qty=stock)
    return branch_id, skus


def _post_order(client, skus, qty=2, **extra):
    data = {"sale_type": "Sale", "payment_method": "Cash", "customer_name": "Order Tester",
            "cart_sku": skus, "cart_qty": [str(qty)] * len(skus), "cart_price": ["50.00"] * len(skus),
            "form_token": get_form_token(client, URL)}
    data.update(extra)
    return client.post(URL, data=data)


def _pages(pdf_bytes):
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


def test_order_is_recorded_as_one_order_and_takes_stock_for_every_scent(client, sql):
    """Order is recorded as one order and takes stock for every scent."""
    branch_id, skus = _setup(client, sql, 3)
    resp = _post_order(client, skus, qty=2)
    assert resp.status_code == 302

    rows = _rows(sql, "SELECT sale_id, order_id, sku, qty_sold FROM sales WHERE branch_id = %s ORDER BY sale_id", (branch_id,))
    assert [r["sku"] for r in rows] == skus
    assert {r["order_id"] for r in rows} == {rows[0]["sale_id"]}        # tied to the first row
    for sku in skus:
        assert get_inventory_qty(sql, branch_id, sku) == 3


def test_a_single_product_sale_has_no_order(client, sql):
    """A single product sale has no order."""
    branch_id, skus = _setup(client, sql, 1)
    client.post(URL, data={"sku": skus[0], "sale_type": "Sale", "payment_method": "Cash", "qty_sold": "1",
                           "unit_price": "50.00", "form_token": get_form_token(client, URL)})
    row = _rows(sql, "SELECT order_id FROM sales WHERE branch_id = %s", (branch_id,))[0]
    assert row["order_id"] is None


def test_the_field_still_filled_in_counts_as_one_more_product(client, sql):
    """The field still filled in counts as one more product."""
    branch_id, skus = _setup(client, sql, 3)
    _post_order(client, skus[:2], sku=skus[2], qty_sold="1", unit_price="50.00")
    assert len(_rows(sql, "SELECT 1 FROM sales WHERE branch_id = %s", (branch_id,))) == 3


def test_one_scent_out_of_stock_records_nothing(client, sql):
    """One scent out of stock records nothing."""
    branch_id, skus = _setup(client, sql, 3)
    cur = sql.cursor()
    cur.execute("UPDATE branch_inventory SET stock_qty = 1 WHERE branch_id = %s AND sku = %s", (branch_id, skus[2]))   # needs 2
    sql.commit()
    cur.close()
    _post_order(client, skus, qty=2)
    assert _rows(sql, "SELECT 1 FROM sales WHERE branch_id = %s", (branch_id,)) == []
    assert get_inventory_qty(sql, branch_id, skus[0]) == 5, "the earlier scents must not keep their deduction"


@pytest.mark.parametrize("count, slips", [(10, 1), (11, 2), (25, 3)])
def test_receipt_prints_ten_products_to_a_slip(client, sql, count, slips):
    """Receipt prints ten products to a slip."""
    branch_id, skus = _setup(client, sql, count)
    _post_order(client, skus, qty=1)
    first = _rows(sql, "SELECT MIN(sale_id) AS id FROM sales WHERE branch_id = %s", (branch_id,))[0]["id"]
    last = _rows(sql, "SELECT MAX(sale_id) AS id FROM sales WHERE branch_id = %s", (branch_id,))[0]["id"]

    for sale_id in (first, last):       # any row of the order gives the same whole receipt
        resp = client.get(f"/branch/record-sale/{sale_id}/receipt")
        assert resp.status_code == 200 and _pages(resp.data) == slips
        assert resp.headers["Content-Disposition"].endswith(f"SR-{first:06d}.pdf")


def test_other_branch_cannot_download_the_order_receipt(app, client, sql):
    """Other branch cannot download the order receipt."""
    branch_id, skus = _setup(client, sql, 2)
    _post_order(client, skus)
    first = _rows(sql, "SELECT MIN(sale_id) AS id FROM sales WHERE branch_id = %s", (branch_id,))[0]["id"]
    other = app.test_client()
    user = make_user(sql, role="Branch", branch_id=make_branch(sql))
    login(other, user["username"], user["password"], "Branch")
    assert other.get(f"/branch/record-sale/{first}/receipt").status_code == 404


def test_scan_shows_every_product_and_voiding_returns_them_all(app, client, sql):
    """Scan shows every product and voiding returns them all."""
    branch_id, skus = _setup(client, sql, 3)
    _post_order(client, skus, qty=2)
    first = _rows(sql, "SELECT MIN(sale_id) AS id FROM sales WHERE branch_id = %s", (branch_id,))[0]["id"]
    with app.test_request_context():
        code = make_receipt_code(first)

    sale = client.post("/scan/verify", json={"code": code}).get_json()["sale"]
    assert [i["sku"] for i in sale["items"]] == skus
    assert sale["line_total"] == pytest.approx(3 * 2 * 50.0)

    assert client.post(f"/scan/sales/{first}/void", json={"reason": "wrong order"}).status_code == 200
    assert _rows(sql, "SELECT 1 FROM sales WHERE branch_id = %s", (branch_id,)) == []
    for sku in skus:
        assert get_inventory_qty(sql, branch_id, sku) == 5


def test_both_record_sale_pages_offer_adding_another_scent(app, client, sql):
    """Both record sale pages offer adding another scent."""
    _setup(client, sql, 1)
    assert b'id="addToOrderBtn"' in client.get(URL).data

    admin = app.test_client()
    user = make_user(sql, role="Admin")
    login(admin, user["username"], user["password"], "Admin")
    assert b'id="addToOrderBtn"' in admin.get("/admin/record-sale").data


def test_an_order_shows_one_receipt_button_not_one_per_row(client, sql):
    """An order shows one receipt button not one per row."""
    branch_id, skus = _setup(client, sql, 3)
    _post_order(client, skus, qty=1)
    first = _rows(sql, "SELECT MIN(sale_id) AS id FROM sales WHERE branch_id = %s", (branch_id,))[0]["id"]

    for url in (URL, "/branch/sales-history"):
        html = client.get(url).get_data(as_text=True)
        buttons = re.findall(r'href="/branch/record-sale/(\d+)/receipt"', html)
        assert buttons == [str(first)], f"{url}: expected one Receipt button, got {buttons}"
        assert "Receipt <span" in html and "(3)" in html          # says how many products it covers
        assert html.count(f"on SR-{first:06d}") >= 2               # the other two rows point at it


def test_a_single_sale_keeps_a_plain_receipt_button(client, sql):
    """A single sale keeps a plain receipt button."""
    branch_id, skus = _setup(client, sql, 1)
    client.post(URL, data={"sku": skus[0], "sale_type": "Sale", "payment_method": "Cash", "qty_sold": "1",
                           "unit_price": "50.00", "form_token": get_form_token(client, URL)})
    html = client.get(URL).get_data(as_text=True)
    assert len(re.findall(r'/branch/record-sale/\d+/receipt', html)) == 1
    assert "Receipt <span" not in html          # no "(n)" count on a one-product receipt
