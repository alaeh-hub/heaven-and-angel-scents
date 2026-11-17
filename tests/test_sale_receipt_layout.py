"""The 80 mm sale receipt grows with its content instead of clipping or overprinting.

A sale is one product line, so the receipt gets longer only through text:
a wrapped address, a long customer / buyer / branch name, a long scent name.
Each of those used to run off the slip or over its own label.
"""
import datetime
import decimal
import re

import pytest

from heaven_angel.services import receipts

D = decimal.Decimal
WIDTH_PT = receipts.RECEIPT_WIDTH
LONG = "Maria Cristina Dela Cruz-Villanueva Ramos y Fernandez de Los Santos"
LONG_ADDRESS = ("Unit 1204, Tower 2, The Residences at Greenbelt Heights Condominium Complex, "
                "Corner of Ayala Avenue and Makati Avenue, Brgy. San Lorenzo, Makati City, Metro Manila")


def _sale(**over):
    sale = dict(
        sale_id=1, branch_id=1, sku="A1-85ML", qty_sold=1, unit_price=D("85"), sale_type="Sale",
        payment_method="Cash", buyer_name=None, buyer_username=None, customer_name=None,
        customer_address=None, sold_at=datetime.datetime(2026, 11, 17, 14, 5), item_name="Lacoste Red",
        variant="Male", unit="85ML", branch_name="HQ Main Warehouse", location="Central Office")
    sale.update(over)
    return sale


@pytest.fixture(autouse=True)
def _in_app(app):
    """The receipt's verification code is signed with the app's secret key."""
    with app.app_context():
        yield


def _height(monkeypatch, **over):
    monkeypatch.setattr(receipts, "_fetch_sale", lambda sale_id, branch_id=None: _sale(**over))
    buf, _sale_row = receipts.build_sale_receipt_pdf(1)
    data = buf.read()
    assert data.startswith(b"%PDF")
    # One page, always: the slip is as tall as its content, never split.
    assert len(re.findall(rb"/Type\s*/Page[^s]", data)) == 1
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    assert float(box.group(1)) == pytest.approx(WIDTH_PT, abs=0.01)
    return float(box.group(2))


def test_slip_is_taller_for_long_text_than_for_short(monkeypatch):
    base = _height(monkeypatch)
    for field, value in (
        ("customer_name", LONG * 2),
        ("customer_address", LONG_ADDRESS * 2),
        ("buyer_username", LONG * 2),
        ("branch_name", "Southern Luzon Fragrance Distribution Corporation Main Branch"),
        ("item_name", "Salvatorre Ferragamo Incanto Shine Eau de Parfum Limited Collector's Edition"),
    ):
        over = {field: value}
        if field == "buyer_username":
            over["payment_method"] = "Credit"
        assert _height(monkeypatch, **over) > base, field


def test_huge_amounts_still_build_one_slip(monkeypatch):
    assert _height(monkeypatch, qty_sold=9999, unit_price=D("99999.99")) > 0
    assert _height(monkeypatch, sku="A1-BULK", unit="BULK", qty_sold=12500, unit_price=D("7.50")) > 0
