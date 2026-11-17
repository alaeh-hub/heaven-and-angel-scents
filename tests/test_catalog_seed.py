"""schema.sql migration 52 seeds the product catalog from products_20260922_144849.csv.

Checked against the schema text rather than the test database, because other
tests (the app's "Clear data") empty the products table during a test run.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_csv_product_is_seeded_in_schema_sql():
    rows = list(csv.DictReader(open(ROOT / "products_20260922_144849.csv", encoding="utf-8-sig")))
    assert len(rows) == 62
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    for r in rows:
        if r["unit"] == "BULK":
            # Bulk is not a product any more: sold by the mL from the bulk batches.
            assert f"('{r['base_code']}-BULK'" not in schema
            continue
        sku = f"{r['base_code']}-{r['unit']}"
        category = "Bottled"
        name = r["item_name"].replace("'", "''")
        expected = f"('{sku}', '{name}', '{r['variant']}', '{category}', '{r['unit']}', {float(r['price']):.2f})"
        assert expected in schema, f"{sku} missing or different in schema.sql"
