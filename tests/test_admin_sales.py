"""routes/admin/'s record_sale() — HQ's own copy of branch.record_sale()
(see its docstring: "Mirrors branch.record_sale()"). This hand-duplicated
implementation had no test of its own before this file — test_sales.py
only ever exercised the branch-side copy, so the two could silently drift
apart with nothing to catch it. Mirrors test_sales.py's cases against the
HQ warehouse (branch_id=1) instead of a regular branch.
"""
from factories import (count_sales, get_batch_remaining, get_form_token, get_inventory_qty,
                        last_movement_log, login, make_inventory, make_product,
                        make_scent, make_scent_batch, make_user)

RECORD_SALE_URL = "/admin/record-sale"
HQ_BRANCH_ID = 1  # schema.sql seeds this as the HQ warehouse


def _sell(client, sku, qty, unit_price, sale_type="Sale", payment_method="Cash",
          form_token=None):
    if form_token is None:
        form_token = get_form_token(client, RECORD_SALE_URL)
    return client.post(
        RECORD_SALE_URL,
        data={
            "sku": sku,
            "sale_type": sale_type,
            "payment_method": payment_method,
            "qty_sold": str(qty),
            "unit_price": str(unit_price),
            "form_token": form_token,
        },
    )


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def test_sale_decrements_hq_stock_and_logs_a_matching_movement(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    resp = _sell(client, sku, qty=3, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 7
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1
    movement = last_movement_log(sql, HQ_BRANCH_ID, sku)
    assert movement["movement_type"] == "SALE"
    assert movement["change_qty"] == -3


def _scent_with_batches(sql, *batch_mls):
    """A scent (50ML bottle, 10 at HQ) with one bulk batch per amount given,
    oldest first. Returns (bottle_sku, base_code, [batch_id, ...])."""
    bottle, base = make_scent(sql, unit="50ML")
    make_inventory(sql, HQ_BRANCH_ID, bottle, stock_qty=10)
    batch_ids = [
        make_scent_batch(sql, base, ml, created_at=f"2026-01-0{i + 1} 08:00:00")
        for i, ml in enumerate(batch_mls)
    ]
    return bottle, base, batch_ids


def _usage(sql, sale_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT batch_id, ml, cost_per_ml FROM sale_batch_usage WHERE sale_id = %s ORDER BY usage_id",
                (sale_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


def _last_sale_id(sql, sku):
    cur = sql.cursor()
    cur.execute("SELECT MAX(sale_id) FROM sales WHERE branch_id = %s AND sku = %s", (HQ_BRANCH_ID, sku))
    row = cur.fetchone()
    cur.close()
    return row[0]


def test_refill_draws_the_scents_batches_oldest_first_not_bottles(client, sql):
    _signed_in_admin(client, sql)
    bottle, base, (old, new) = _scent_with_batches(sql, 60, 500)

    resp = _sell(client, bottle, qty=2, unit_price="20.00", sale_type="Refill")

    assert resp.status_code == 302
    # Customer brought their own bottle: bottles untouched, 2 x 50 mL poured —
    # the old batch emptied first (60 mL), the rest (40 mL) from the next one.
    assert get_inventory_qty(sql, HQ_BRANCH_ID, bottle) == 10
    assert get_batch_remaining(sql, old) == 0
    assert get_batch_remaining(sql, new) == 460
    assert count_sales(sql, HQ_BRANCH_ID, bottle) == 1
    usage = _usage(sql, _last_sale_id(sql, bottle))
    assert [(u["batch_id"], float(u["ml"])) for u in usage] == [(old, 60.0), (new, 40.0)]


def test_refill_is_blocked_without_enough_bulk(client, sql):
    _signed_in_admin(client, sql)
    bottle, base, (batch,) = _scent_with_batches(sql, 60)

    resp = _sell(client, bottle, qty=2, unit_price="20.00", sale_type="Refill")

    assert resp.status_code == 302
    assert get_batch_remaining(sql, batch) == 60
    assert count_sales(sql, HQ_BRANCH_ID, bottle) == 0


def test_bulk_sale_by_the_ml_takes_it_from_the_batches(client, sql):
    _signed_in_admin(client, sql)
    bottle, base, (batch,) = _scent_with_batches(sql, 300)

    resp = _sell(client, f"{base}-BULK", qty=100, unit_price="1.00")

    assert resp.status_code == 302
    assert get_batch_remaining(sql, batch) == 200
    assert count_sales(sql, HQ_BRANCH_ID, f"{base}-BULK") == 1
    # Bottles aren't touched, and there is no BULK stock row or movement log.
    assert get_inventory_qty(sql, HQ_BRANCH_ID, bottle) == 10
    assert get_inventory_qty(sql, HQ_BRANCH_ID, f"{base}-BULK") in (None, 0)
    # The sale's cost is the batch's own cost per mL.
    (use,) = _usage(sql, _last_sale_id(sql, f"{base}-BULK"))
    assert (float(use["ml"]), float(use["cost_per_ml"])) == (100.0, 2.0)


def test_bulk_sale_is_blocked_when_the_batches_are_short(client, sql):
    _signed_in_admin(client, sql)
    bottle, base, (batch,) = _scent_with_batches(sql, 50)

    _sell(client, f"{base}-BULK", qty=100, unit_price="1.00")

    assert get_batch_remaining(sql, batch) == 50
    assert count_sales(sql, HQ_BRANCH_ID, f"{base}-BULK") == 0


def test_bulk_sale_page_offers_scents_with_batch_ml_left(client, sql):
    _signed_in_admin(client, sql)
    bottle, base, _ = _scent_with_batches(sql, 300)

    html = client.get(RECORD_SALE_URL).get_data(as_text=True)

    assert f"{base}-BULK" in html


def test_a_bulk_sale_is_refused_at_a_branch(client, sql):
    from factories import make_branch
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    bottle, base, (batch,) = _scent_with_batches(sql, 300)
    token = get_form_token(client, "/branch/record-sale")

    client.post("/branch/record-sale", data={
        "sku": f"{base}-BULK", "sale_type": "Sale", "payment_method": "Cash",
        "qty_sold": "100", "unit_price": "1.00", "form_token": token})

    assert get_batch_remaining(sql, batch) == 300
    assert count_sales(sql, branch_id, f"{base}-BULK") == 0


def test_overselling_hq_stock_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=2)

    resp = _sell(client, sku, qty=5, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 2
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_zero_price_sale_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="0", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 10
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_credit_sale_without_a_buyer_name_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    form_token = get_form_token(client, RECORD_SALE_URL)
    resp = client.post(
        RECORD_SALE_URL,
        data={
            "sku": sku, "sale_type": "Sale", "payment_method": "Credit",
            "qty_sold": "1", "unit_price": "50.00", "buyer_name": "",
            "form_token": form_token,
        },
    )

    assert resp.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_double_submitting_the_same_sale_form_only_records_it_once(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    form_token = get_form_token(client, RECORD_SALE_URL)
    first = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert first.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1

    second = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert second.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 7
