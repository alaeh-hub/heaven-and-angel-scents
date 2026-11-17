"""schema.sql migration 52 seeds the product catalog.

Checked against the schema text rather than the test database, because other
tests (the app's "Clear data") empty the products table during a test run.

Two checks:
  * the seeded catalog is consistent on its own (always runs, including on CI);
  * every row of the local products export is seeded (runs only where that file
    exists: `products_*.csv` is git-ignored, so it is not in a fresh clone or on CI).
"""
import csv
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSV_EXPORT = ROOT / "products_20260922_144849.csv"
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")

# ('A4-85ML', 'Abercrombie & Fitch', 'Male', 'Bottled', '85ML', 85.00)   (a quote in a name is doubled)
SEED_ROW = re.compile(
    r"\('([^']+)', '((?:[^']|'')+)', '(Male|Female)', '(Bottled)', '(85ML|50ML|10ML|3ML)', (\d+\.\d\d)\)")


def _seeded_products():
    start = SCHEMA.index("INSERT IGNORE INTO products (sku, item_name, variant, category, unit, price) VALUES")
    block = SCHEMA[start:SCHEMA.index(";", start)]
    return [m.groups() for m in SEED_ROW.finditer(block)]


def test_seeded_catalog_is_consistent():
    rows = _seeded_products()
    assert len(rows) == 31, "the catalog seed should hold 31 scents"
    skus = [r[0] for r in rows]
    assert len(set(skus)) == len(skus), "duplicate SKU in the seed"
    names = [r[1] for r in rows]
    assert len(set(names)) == len(names), "duplicate scent name in the seed"
    for sku, _name, _variant, _category, unit, price in rows:
        assert sku.endswith(f"-{unit}"), f"{sku}: the SKU must end in its unit"
        assert not sku.endswith("-BULK")
        assert float(price) > 0
    # Bulk is not a product: nothing in the seed is a BULK SKU.
    assert "-BULK'" not in SCHEMA[SCHEMA.index("INSERT IGNORE INTO products"):SCHEMA.index("INSERT IGNORE INTO branch_inventory")]


@pytest.mark.skipif(not CSV_EXPORT.exists(), reason="local products export not present (git-ignored)")
def test_every_csv_product_is_seeded_in_schema_sql():
    with open(CSV_EXPORT, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 62
    for r in rows:
        if r["unit"] == "BULK":
            # Bulk is not a product any more: sold by the mL from the bulk batches.
            assert f"('{r['base_code']}-BULK'" not in SCHEMA
            continue
        sku = f"{r['base_code']}-{r['unit']}"
        category = "Bottled"
        name = r["item_name"].replace("'", "''")
        expected = f"('{sku}', '{name}', '{r['variant']}', '{category}', '{r['unit']}', {float(r['price']):.2f})"
        assert expected in SCHEMA, f"{sku} missing or different in schema.sql"
