"""The stock correction for the alcohol / water mix fix (scripts/mix_stock_correction.py)."""
import importlib.util
from decimal import Decimal
from pathlib import Path

from heaven_angel.utils import convert_material_qty

D = Decimal
_spec = importlib.util.spec_from_file_location(
    "mix_stock_correction", Path(__file__).resolve().parent.parent / "scripts" / "mix_stock_correction.py")
fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix)

ALC, WAT = 3, 5


def to_ml(qty, unit):
    return convert_material_qty(D(qty), unit, "Milliliter")


def from_ml(qty, unit):
    return convert_material_qty(D(qty), "Milliliter", unit)


def _batch(batch_id, alcohol_ml, water_ml, alcohol_rate="0.13209", water_rate="0.0082"):
    meta = {"batch_id": batch_id, "scent_name": "x", "formula_label": "Formula 15%, 500 ml"}
    return [
        {**meta, "material_id": ALC, "qty_used": D(alcohol_ml), "qty_used_unit": "Milliliter",
         "cost_per_unit_snapshot": D(alcohol_rate)},
        {**meta, "material_id": WAT, "qty_used": D(water_ml), "qty_used_unit": "Milliliter",
         "cost_per_unit_snapshot": D(water_rate)},
    ]


def test_a_wrong_batch_works_out_to_the_right_split():
    right_a, right_w = fix.correct_split(D("466.667"), D("2333.333"), 1000, 200)
    assert (right_a, right_w) == (D("2333.333"), D("466.667"))
    assert right_a + right_w == D("2800")


def test_corrections_total_stock_and_cost_across_batches():
    lines = _batch(1, "466.667", "2333.333") + _batch(2, "233.333", "1166.667")   # 2800 and 1400 mixes
    out = fix.batch_corrections(lines, ALC, WAT, D(1000), D(200), to_ml)
    assert [c["batch_id"] for c in out] == [1, 2]
    assert out[0]["extra_alcohol_ml"] == D("1866.666") and out[0]["over_water_ml"] == D("1866.666")
    # Alcohol stock is too high (goes down), water stock too low (goes up); water is stocked in liters.
    alcohol_delta, water_delta = fix.stock_adjustments(out, "Milliliter", "Liter", from_ml)
    assert alcohol_delta == -(out[0]["extra_alcohol_ml"] + out[1]["extra_alcohol_ml"])
    assert abs(water_delta - (out[0]["over_water_ml"] + out[1]["over_water_ml"]) / 1000) < D("0.000001")
    assert alcohol_delta < 0 < water_delta
    # Cost was low: the missing alcohol at 0.13209/mL costs more than the extra water at 0.0082/mL saved.
    assert out[0]["cost_delta"] > 0


def test_batches_that_already_used_the_right_split_are_skipped():
    lines = _batch(7, "2333.333", "466.667") + _batch(8, "466.667", "2333.333")
    out = fix.batch_corrections(lines, ALC, WAT, D(1000), D(200), to_ml)
    assert [c["batch_id"] for c in out] == [8]


def test_a_water_line_logged_in_liters_is_converted():
    lines = _batch(9, "466.667", "2333.333")
    lines[1]["qty_used"], lines[1]["qty_used_unit"] = D("2.333333"), "Liter"
    out = fix.batch_corrections(lines, ALC, WAT, D(1000), D(200), to_ml)
    assert len(out) == 1 and abs(out[0]["over_water_ml"] - D("1866.666")) < D("0.01")


def test_a_batch_missing_one_of_the_two_lines_is_ignored():
    assert fix.batch_corrections(_batch(4, "466.667", "2333.333")[:1], ALC, WAT, D(1000), D(200), to_ml) == []
