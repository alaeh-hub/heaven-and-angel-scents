"""Collapsible mobile rows (as Record Sale/Customers) for partners, packages, inquiries, branches.

Each row collapses to its primary identity with the rest behind "Details".
Only the markup is checked; the toggle itself is CSS + JS.
"""
from factories import (login, make_branch, make_package, make_partner,
                       make_partner_inquiry, make_user)


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _row_count(sql, table):
    cur = sql.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    count = cur.fetchone()[0]
    cur.close()
    return count


def test_partners_table_renders_collapsible_mobile_markup(client, sql):
    """Partners table renders collapsible mobile markup."""
    _signed_in_admin(client, sql)
    make_partner(sql)

    resp = client.get("/admin/partners")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # 3 columns collapse per row; count rows (shared DB has other partners).
    assert html.count("mobile-detail") == _row_count(sql, "partners") * 3


def test_packages_table_renders_collapsible_mobile_markup(client, sql):
    """Packages table renders collapsible mobile markup."""
    _signed_in_admin(client, sql)
    make_package(sql)

    resp = client.get("/admin/packages")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # 4 columns collapse per row (counted, same reason as above).
    assert html.count("mobile-detail") == _row_count(sql, "packages") * 4


def test_partner_inquiries_table_renders_collapsible_mobile_markup(client, sql):
    """Partner inquiries table renders collapsible mobile markup."""
    _signed_in_admin(client, sql)
    make_partner_inquiry(sql)

    resp = client.get("/admin/partners/inquiries")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # 4 columns collapse; When is mobile-fold. Match the closing quote of the class
    # attribute so "mobile-detail" in the page's <style> block is not counted.
    assert html.count('mobile-detail"') == _row_count(sql, "partner_inquiries") * 4
    # The max-width classes must be on the data cell, not just the <th>.
    assert 'class="pi-col-company"' in html
    assert 'class="pi-col-contact mobile-detail"' in html
    assert 'class="pi-col-package mobile-detail"' in html
    assert 'class="pi-col-remarks mobile-detail"' in html


def test_branches_table_renders_collapsible_mobile_markup(client, sql):
    """Branches table renders collapsible mobile markup."""
    _signed_in_admin(client, sql)
    make_branch(sql)

    resp = client.get("/admin/branches")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # 3 columns collapse per row; schema.sql also seeds a non-HQ branch, so count rows.
    cur = sql.cursor()
    cur.execute("SELECT COUNT(*) FROM branches WHERE is_hq = FALSE")
    branch_count = cur.fetchone()[0]
    cur.close()
    assert html.count("mobile-detail") == branch_count * 3
