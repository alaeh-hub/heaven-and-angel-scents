"""Work out (and optionally apply) the stock correction for the alcohol / water mix fix.

Until the fix, every formula batch split its "Alcohol" mix amount 200 : 1000
alcohol : water, so it took about a fifth of the alcohol it should and about
five times too much water out of stock. The ratio is 1000 : 200. This script
reads every batch that was made from a formula, works out what it SHOULD have
taken with the correct ratio, and reports, per material, how far the stock
count is off. It uses whatever database the app is configured for, so run it
on the server (the numbers live in that database, not in any local backup):

    python scripts/mix_stock_correction.py              # report only, changes nothing
    python scripts/mix_stock_correction.py --apply      # also correct raw_materials.stock_qty

Docker deployment:

    docker compose exec web python scripts/mix_stock_correction.py

A batch that already used the right split (made after the fix) works out to a
zero difference, so it is skipped automatically. The report is computed from the
batches, not from today's stock, so apply it ONCE: running --apply a second time
would adjust the stock again. Past batches keep their recorded amounts and costs;
the report shows how much each batch's cost was off, for your information.
"""
import argparse
import decimal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

D = decimal.Decimal
MIL = D("0.001")


def correct_split(recorded_alcohol_ml, recorded_water_ml, alcohol_parts, water_parts):
    """What a batch should have taken, from what it recorded.

    The alcohol and water lines always add back up to the formula's mix, so the
    mix is their sum. Returns (right_alcohol_ml, right_water_ml); water is the
    remainder, exactly as batch_formulas.split_mix() does it.
    """
    mix = D(recorded_alcohol_ml) + D(recorded_water_ml)
    right_alcohol = (mix * D(alcohol_parts) / (D(alcohol_parts) + D(water_parts))).quantize(MIL)
    return right_alcohol, mix - right_alcohol


def batch_corrections(lines, alcohol_id, water_id, alcohol_parts, water_parts, to_ml):
    """Per-batch corrections from the alcohol and water lines of formula batches.

    `lines` are dicts with batch_id, material_id, qty_used, qty_used_unit and
    cost_per_unit_snapshot; `to_ml(qty, unit)` converts a quantity to mL. Returns
    one dict per batch that was wrong (right-ratio batches are left out), with the
    extra alcohol it should have taken, the water it over-took (both in mL) and
    the cost it was understated by.
    """
    by_batch = {}
    for ln in lines:
        entry = by_batch.setdefault(ln["batch_id"], {"meta": ln})
        role = "alcohol" if ln["material_id"] == alcohol_id else "water" if ln["material_id"] == water_id else None
        if role:
            entry[role] = ln
    out = []
    for batch_id, entry in by_batch.items():
        if "alcohol" not in entry or "water" not in entry:
            continue
        a, w = entry["alcohol"], entry["water"]
        rec_a, rec_w = to_ml(a["qty_used"], a["qty_used_unit"]), to_ml(w["qty_used"], w["qty_used_unit"])
        right_a, right_w = correct_split(rec_a, rec_w, alcohol_parts, water_parts)
        extra_alcohol = right_a - rec_a            # should have taken this much MORE alcohol
        over_water = rec_w - right_w               # took this much too much water
        if abs(extra_alcohol) < D("0.002"):
            continue
        # Cost: line costs are qty x the price snapshot, in the line's own unit.
        a_unit = to_ml(D(1), a["qty_used_unit"])
        w_unit = to_ml(D(1), w["qty_used_unit"])
        cost_delta = (extra_alcohol / a_unit * D(a["cost_per_unit_snapshot"])
                      - over_water / w_unit * D(w["cost_per_unit_snapshot"]))
        out.append({"batch_id": batch_id, "meta": entry["meta"],
                    "recorded_alcohol_ml": rec_a, "right_alcohol_ml": right_a,
                    "recorded_water_ml": rec_w, "right_water_ml": right_w,
                    "extra_alcohol_ml": extra_alcohol, "over_water_ml": over_water,
                    "cost_delta": cost_delta})
    return out


def stock_adjustments(corrections, alcohol_unit, water_unit, from_ml):
    """Total change to apply to each material's stock count, in its own stock unit.

    Alcohol stock is too HIGH by the alcohol the batches should have used, so it
    goes down; water stock is too LOW by the water they over-took, so it goes up.
    """
    extra_alcohol = sum((c["extra_alcohol_ml"] for c in corrections), D("0"))
    over_water = sum((c["over_water_ml"] for c in corrections), D("0"))
    return (-from_ml(extra_alcohol, alcohol_unit), from_ml(over_water, water_unit))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="correct raw_materials.stock_qty (asks to confirm)")
    parser.add_argument("--alcohol", type=D, default=D(1000), help="alcohol parts (default 1000)")
    parser.add_argument("--water", type=D, default=D(200), help="water parts (default 200)")
    args = parser.parse_args()

    from heaven_angel.app import create_app
    from heaven_angel.db import execute, query, transaction
    from heaven_angel.utils import ML_PER_BATCH_UNIT, convert_material_qty

    def to_ml(qty, unit):
        return convert_material_qty(D(qty), unit, "Milliliter")

    def from_ml(qty_ml, unit):
        return convert_material_qty(D(qty_ml), "Milliliter", unit)

    with create_app().app_context():
        roles = {r["role"]: r["material_id"] for r in query(
            "SELECT role, material_id FROM batch_formula_materials WHERE role IN ('alcohol', 'water')")}
        if set(roles) != {"alcohol", "water"}:
            sys.exit("The alcohol and water materials are not set on the formula materials page yet.")
        mats = {m["material_id"]: m for m in query(
            "SELECT material_id, material_name, unit, stock_qty FROM raw_materials WHERE material_id IN (%s, %s)",
            (roles["alcohol"], roles["water"]))}
        alc, wat = mats[roles["alcohol"]], mats[roles["water"]]
        for m in (alc, wat):
            if m["unit"] not in ML_PER_BATCH_UNIT:
                sys.exit(f"{m['material_name']} is stocked in {m['unit']}, which is not a volume unit.")

        lines = query(
            """SELECT b.batch_id, b.batch_code, b.scent_name, b.formula_label, b.created_at,
                      b.total_cost, b.total_volume_ml,
                      m.material_id, m.qty_used, m.qty_used_unit, m.cost_per_unit_snapshot
               FROM bulk_batches b
               JOIN bulk_batch_materials m ON m.batch_id = b.batch_id
               WHERE b.formula_label IS NOT NULL AND m.is_packaging = FALSE
                 AND m.material_id IN (%s, %s)
               ORDER BY b.batch_id""",
            (roles["alcohol"], roles["water"]))
        corrections = batch_corrections(lines, roles["alcohol"], roles["water"], args.alcohol, args.water, to_ml)

        print(f"Ratio used for the correction: {args.alcohol:g} mL alcohol : {args.water:g} mL water")
        print(f"Formula batches checked: {len({ln['batch_id'] for ln in lines})}, "
              f"with a wrong split: {len(corrections)}\n")
        if not corrections:
            print("Nothing to correct.")
            return

        print(f"{'batch':>6}  {'scent':<24} {'formula':<20} {'alcohol mL':>22} {'water mL':>22} {'cost was low by':>16}")
        for c in corrections:
            meta = c["meta"]
            print(f"{c['batch_id']:>6}  {str(meta['scent_name'])[:24]:<24} {str(meta['formula_label'])[:20]:<20} "
                  f"{c['recorded_alcohol_ml']:>9.3f} -> {c['right_alcohol_ml']:>9.3f} "
                  f"{c['recorded_water_ml']:>9.3f} -> {c['right_water_ml']:>9.3f} {c['cost_delta']:>16.2f}")

        d_alc, d_wat = stock_adjustments(corrections, alc["unit"], wat["unit"], from_ml)
        print("\nStock correction (add this number to the stock count):")
        for m, delta in ((alc, d_alc), (wat, d_wat)):
            new = m["stock_qty"] + delta
            flag = "   <-- would go below zero: the real stock is lower than the records say" if new < 0 else ""
            print(f"  {m['material_name']}: {delta:+.3f} {m['unit']}   (now {m['stock_qty']:.3f}, would be {new:.3f}){flag}")
        total_cost = sum((c["cost_delta"] for c in corrections), D("0"))
        print(f"\nBatch costs were understated by ₱{total_cost:,.2f} in total (informational; batches are not changed).")

        if not args.apply:
            print("\nReport only. Re-run with --apply to correct the stock counts.")
            return
        if input("\nType CORRECT to apply this stock correction now: ").strip() != "CORRECT":
            sys.exit("Cancelled, nothing changed.")
        with transaction() as conn:
            cur = conn.cursor()
            for m, delta in ((alc, d_alc), (wat, d_wat)):
                cur.execute("UPDATE raw_materials SET stock_qty = stock_qty + %s WHERE material_id = %s",
                            (delta, m["material_id"]))
            cur.close()
        execute("INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details) "
                "VALUES (NULL, 'script', 'mix_stock_correction', 'Alcohol and water stock', %s)",
                (f"alcohol {d_alc:+.3f} {alc['unit']}, water {d_wat:+.3f} {wat['unit']}, "
                 f"{len(corrections)} batches, ratio {args.alcohol:g}:{args.water:g}",))
        print("Applied.")


if __name__ == "__main__":
    main()
