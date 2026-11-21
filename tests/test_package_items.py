"""Adding products to a package: the picker offers only bottled products, and a bulk SKU is refused."""
import json
import re

from factories import login, make_package, make_product, make_user, unique_suffix


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def _offered_skus(html):
    match = re.search(r"data-products='([^']*)'", html)
    assert match, "product combobox missing from the page"
    return {p["sku"] for p in json.loads(match.group(1).replace("&#39;", "'").replace("&amp;", "&"))}


def _package_skus(sql, package_id):
    cur = sql.cursor()
    cur.execute("SELECT sku FROM package_items WHERE package_id = %s", (package_id,))
    skus = {row[0] for row in cur.fetchall()}
    cur.close()
    return skus


def test_package_page_offers_bottled_products_but_not_bulk(client, sql):
    """Package page offers bottled products but not bulk."""
    _signed_in_admin(client, sql)
    package_id = make_package(sql)
    bottled = make_product(sql, unit="85ML")

    html = client.get(f"/admin/packages/{package_id}").get_data(as_text=True)

    offered = _offered_skus(html)
    assert bottled in offered
    assert not any(sku.endswith("-BULK") for sku in offered)  # bulk is not a product
    assert 'id="productSearch"' in html


def test_adding_a_bottled_product_by_sku(client, sql):
    """Adding a bottled product by sku."""
    _signed_in_admin(client, sql)
    package_id = make_package(sql)
    bottled = make_product(sql, unit="50ML")

    client.post(f"/admin/packages/{package_id}/items", data={"sku": bottled, "qty": "2"})

    assert _package_skus(sql, package_id) == {bottled}


def test_a_bulk_product_is_refused_even_when_posted_directly(client, sql):
    """A bulk product is refused even when posted directly."""
    _signed_in_admin(client, sql)
    package_id = make_package(sql)
    bulk = f"BLK-{unique_suffix().upper()}-BULK"  # bulk is not a product, so there is no such SKU

    resp = client.post(f"/admin/packages/{package_id}/items", data={"sku": bulk, "qty": "1"},
                       follow_redirects=True)

    assert _package_skus(sql, package_id) == set()
    assert "Bulk can&#39;t go in a package" in resp.get_data(as_text=True)
