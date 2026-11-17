"""scripts/demo_data.py: its built-in tables must stay in step with what schema.sql seeds.

The script itself needs an empty, freshly seeded database (it refuses to run on
one that already holds records), so the test session's shared database, full of
other tests' rows, can't run it end to end. These checks catch the ways it
quietly goes stale: a material renamed or added in schema.sql, or a price story
that no longer adds up to the whole amount bought.
"""
import importlib.util
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "demo_data.py"
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")


def _load_script():
    spec = importlib.util.spec_from_file_location("demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seeded_material_names():
    block = SCHEMA[SCHEMA.index("INSERT IGNORE INTO raw_materials"):]
    block = block[:block.index("INSERT IGNORE INTO batch_formula_materials")]
    return set(re.findall(r"^\s+\('([^']+)', '(?:Gram|Milliliter|Liter|Gallon|Piece)'", block, re.M))


def test_every_seeded_material_has_a_price_story_and_margin():
    script = _load_script()
    names = _seeded_material_names()
    assert len(names) == 8
    assert set(script.PRICE_STORY) == names
    assert set(script.STOCK_MARGIN) == names
    assert set(script.RECEIPT_PREFIX) == names


def test_price_stories_add_up_to_everything_bought():
    script = _load_script()
    for name, story in script.PRICE_STORY.items():
        assert sum(Decimal(str(share)) for _days, share, _mult in story) == Decimal("1"), name
        days = [d for d, _s, _m in story]
        assert days == sorted(days, reverse=True), f"{name}: purchases must run oldest to newest"
