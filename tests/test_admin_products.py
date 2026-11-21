"""Admin products page: a product with no photo shows a generated initials avatar.

Goes through the real route, template and Jinja globals, not just the pure function.
"""
from factories import login, make_product, make_user


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def test_product_with_no_photo_renders_an_initials_avatar(client, sql):
    """Product with no photo renders an initials avatar."""
    _signed_in_admin(client, sql)
    make_product(sql)  # make_product never sets image_path -> NULL

    resp = client.get("/admin/products")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "product-thumb-avatar" in html
    # The old broken/empty-image placeholder icon must be gone.
    assert "M21 15l-5-5L5 21" not in html


def test_unit_filter_defaults_to_85ml_but_keeps_all_units_option(client, sql):
    """The catalog's unit filter should open pre-set to 85ML (the most
    commonly sold size) rather than showing everything, but "All units"
    must still be there for anyone who wants to clear it."""
    _signed_in_admin(client, sql)
    make_product(sql, unit="85ML")

    resp = client.get("/admin/products")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<option value="">All units</option>' in html
    assert '<option value="85ML" selected>85ML</option>' in html
    # No other unit option should carry the default selection.
    assert '<option value="50ML" selected>' not in html


# ---------------------------------------------------------------- CSV import/export
def _import_csv(client, text):
    import io
    return client.post(
        "/admin/products/import",
        data={"csv_file": (io.BytesIO(text.encode("utf-8-sig")), "products.csv")},
        content_type="multipart/form-data",
    )


def _product(sql, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT sku, item_name, category, unit, price FROM products WHERE sku = %s", (sku,))
    row = cur.fetchone()
    cur.close()
    return row


def test_import_refuses_a_bulk_row_because_bulk_is_not_a_product(client, sql):
    """Import refuses a bulk row because bulk is not a product."""
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    code = "T" + uuid.uuid4().hex[:6].upper()

    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Male,BULK,12.50\n")

    assert _product(sql, f"{code}-BULK") is None


def test_export_never_lists_a_bulk_row(client, sql):
    """Export never lists a bulk row."""
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    code = "T" + uuid.uuid4().hex[:6].upper()
    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Male,50ML,350.00\n")

    # The export is an Excel file: read every cell back out of it.
    from io import BytesIO
    import openpyxl
    workbook = openpyxl.load_workbook(BytesIO(client.get("/admin/products/export").get_data()))
    cells = {str(c.value) for sheet in workbook for row in sheet.iter_rows() for c in row if c.value is not None}

    assert code in cells and "50ML" in cells
    assert "BULK" not in cells


def test_products_page_counts_skus_by_base_code(client, sql):
    """A1-85ML / A1-50ML are one SKU ("A1") in the count."""
    import re
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    def catalog_count():
        html = client.get("/admin/products").get_data(as_text=True)
        return int(re.search(r'id="catalogCount">(\d+) SKU', html).group(1))

    before = catalog_count()
    code = "T" + uuid.uuid4().hex[:6].upper()
    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Male,85ML,500.00\n"
                        f"{code},Seraph,Male,50ML,350.00\n")

    assert catalog_count() == before + 1
