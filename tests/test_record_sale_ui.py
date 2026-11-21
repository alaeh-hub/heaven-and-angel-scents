"""Record Sale's "Recent sales" list: rows start collapsed to the item with a "Details" bar.

Only the template markup is checked; the toggle itself is CSS + JS.
"""
from factories import (get_form_token, login, make_branch, make_inventory,
                        make_product, make_user)

RECORD_SALE_URL = "/branch/record-sale"


def _sell(client, sku, qty, unit_price):
    form_token = get_form_token(client, RECORD_SALE_URL)
    return client.post(
        RECORD_SALE_URL,
        data={
            "sku": sku,
            "sale_type": "Sale",
            "payment_method": "Cash",
            "qty_sold": str(qty),
            "unit_price": str(unit_price),
            "form_token": form_token,
        },
    )


def test_recent_sales_table_renders_the_collapsible_mobile_markup(client, sql):
    """Recent sales table renders the collapsible mobile markup."""
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell(client, sku, 1, "500.00")

    resp = client.get(RECORD_SALE_URL)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Qty/Price/Total/Payment are the cells that collapse away.
    assert html.count("mobile-detail") == 4
