"""Correct the opening Perfume Alcohol for a gallon that is 3,200 mL.

scripts/import_opening_stock.py stocked the opening alcohol as 9 gallons, worked
out with the US gallon (3,785.411784 mL): 34,068.706 mL for 4,500 pesos. The jugs
actually hold 3,200 mL, so those 9 gallons are 28,800 mL: the purchase quantity
and the stock were overstated by 9 x (3785.411784 - 3200) = 5,268.706 mL, and the
cost per mL was understated (4500 / 34,068.706 = 0.1321 instead of 0.15625).

This script takes that overstatement off the alcohol's purchase quantity and its
stock, and recomputes the cost per mL from the package cost. It uses whatever
database the app is configured for, so run it on the server:

    python scripts/gallon_stock_correction.py            # report only, changes nothing
    python scripts/gallon_stock_correction.py --apply    # correct it (asks to confirm)

Docker deployment:

    docker compose exec web python scripts/gallon_stock_correction.py

Apply it ONCE. If the alcohol was restocked since, those purchases are kept (only
the opening 9 gallons are corrected). Pass --gallons if the opening purchase was a
different number of gallons.
"""
import argparse
import decimal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

D = decimal.Decimal
US_GALLON_ML = D("3785.411784")
GALLON_ML = D("3200")


def overstatement_ml(gallons):
    """How many mL too many a purchase of `gallons` was stocked with."""
    return (D(gallons) * (US_GALLON_ML - GALLON_ML)).quantize(D("0.001"))


def corrected(package_qty, package_cost, stock_qty, gallons):
    """New (package_qty, cost_per_unit, stock_qty) after taking the overstatement off."""
    over = overstatement_ml(gallons)
    new_qty = D(package_qty) - over
    if new_qty <= 0:
        raise ValueError("The correction is larger than the recorded purchase quantity.")
    return new_qty, (D(package_cost) / new_qty).quantize(D("0.0001")), D(stock_qty) - over


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="correct the alcohol (asks to confirm)")
    parser.add_argument("--gallons", type=D, default=D(9), help="gallons in the opening purchase (default 9)")
    args = parser.parse_args()

    from heaven_angel.app import create_app
    from heaven_angel.db import execute, query, transaction

    with create_app().app_context():
        row = query(
            """SELECT rm.material_id, rm.material_name, rm.unit, rm.package_qty, rm.package_cost,
                      rm.cost_per_unit, rm.stock_qty
               FROM batch_formula_materials bfm JOIN raw_materials rm ON rm.material_id = bfm.material_id
               WHERE bfm.role = 'alcohol'""", fetchone=True) or query(
            "SELECT material_id, material_name, unit, package_qty, package_cost, cost_per_unit, stock_qty "
            "FROM raw_materials WHERE material_name = 'Perfume Alcohol'", fetchone=True)
        if not row:
            sys.exit("No alcohol material found.")
        if row["unit"] != "Milliliter":
            sys.exit(f"{row['material_name']} is stocked in {row['unit']}, not Milliliter: nothing to correct here.")
        over = overstatement_ml(args.gallons)
        try:
            new_qty, new_cost, new_stock = corrected(row["package_qty"], row["package_cost"], row["stock_qty"], args.gallons)
        except ValueError as err:
            sys.exit(str(err))

        print(f"{row['material_name']} ({args.gallons:g} gallons x 3,200 mL instead of x 3,785.411784 mL)")
        print(f"  purchase quantity : {row['package_qty']:.3f} mL  ->  {new_qty:.3f} mL   (-{over:.3f})")
        print(f"  cost per mL       : {row['cost_per_unit']:.4f}  ->  {new_cost:.4f}   (package cost {row['package_cost']:.2f})")
        print(f"  stock on hand     : {row['stock_qty']:.3f} mL  ->  {new_stock:.3f} mL   (-{over:.3f})")
        if new_stock < 0:
            print("  NOTE: stock would go below zero: more alcohol was used than the corrected stock covers.")
        if not args.apply:
            print("\nReport only. Re-run with --apply to correct it.")
            return
        if input("\nType CORRECT to apply this correction now: ").strip() != "CORRECT":
            sys.exit("Cancelled, nothing changed.")
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE raw_materials SET package_qty = %s, cost_per_unit = %s, stock_qty = stock_qty - %s "
                "WHERE material_id = %s", (new_qty, new_cost, over, row["material_id"]))
            cur.close()
        execute("INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details) "
                "VALUES (NULL, 'script', 'gallon_stock_correction', %s, %s)",
                (row["material_name"], f"-{over:.3f} mL, cost per mL {row['cost_per_unit']:.4f} -> {new_cost:.4f}"))
        print("Applied.")


if __name__ == "__main__":
    main()
