# Business rules (the facts the code and the AI must agree on)

If a change touches one of these, update the code, the reports, Halo (the AI), the Tools calculator, the
Process Flow tab, the tests and this file together (see `CLAUDE.md`). Numbers below are the current rules;
the code is the final truth, so if they ever disagree, fix whichever is wrong and say so.

## Who uses the system
| Role | Where | Can |
|---|---|---|
| Admin / HQ (branch id 1) | `/admin/*` | Everything: catalog, production, materials, formulas, packages, partners, users, reports, backups, audit log. Holds bulk and warehouse stock. |
| Branch staff | `/branch/*` | Own branch only: inventory, sales, requests, receiving, customers, discrepancies, branch reports. Bottled stock only, never bulk. |
| Partner / reseller | `/partner-portal/<slug>/*` | No login. Browse packages, send inquiries. |
| Anyone with a receipt | `/scan/*` | Verify a receipt by its code. |
A Branch user must never see another branch's data or HQ-only data (reports, AI answers and exports included).

## Products, units and stock
- Sellable bottle sizes: **85ML, 50ML, 10ML, 3ML** (`PRODUCT_UNITS`). Each scent also has a **BULK** product for unpackaged liquid, counted in **mL**.
- SKU = base code + unit, e.g. `A1-50ML`, `A1-BULK`. Base code is uppercase letters, digits and `-`, up to 30 characters.
- Bottles are counted in pieces; bulk is counted in mL. Never add them together in totals or reports.
- Categories: Bottled. Variants: Male / Female. Scent names on the public site are hardcoded on purpose.
- Stock is kept per branch (`branch_inventory`). A new product gets a zero-stock row for every branch.
- Every stock change writes a movement log. Logs and the admin audit trail are append-only.

## Sales
- Types: **Sale**, **Refill**, **Freebie**. Payment: **Cash** or **Credit** (credit = "utang", owed by `buyer_name`; also an employee taking product against pay).
- Sale / Freebie reduce that product's stock at that location. **Refill** reduces the scent's **BULK** stock instead (HQ only).
- One receipt can hold up to 60 products; it prints 10 per slip. Several products on one order are saved all-or-none (`sales.order_id`).
- Every sale has a signed QR receipt code (prefix `HAS`).
- A **void** restores stock, keeps a permanent snapshot (`sale_voids`: who and why), and removes the sale row so totals fix themselves.
- Excel sales import follows the same stock and validation rules as manual entry, and is all-or-nothing.
- Credit/backdating behaviour was reviewed and deliberately left as it is.

## Production and cost (the part that is easy to get wrong)
- Bulk batches are made from built-in formulas **15%, 14%, 13%**, each in **500, 250 and 125 mL** sizes. Each ingredient role is tied once to a raw material; cost comes from that material's current price.
- Ingredient roles: Oil Scent (mL), Fixative (g), Alcohol (mL), Moisturizer (mL), Distilled Water (mL), and packaging per bottle: Bottle, Sticker, Plastic Seal (pieces).
- **Alcohol : water mix is 1000 : 200** (1000 mL alcohol with 200 mL water). Stored as `mix_alcohol_parts` / `mix_water_parts`; the app self-heals the ratio.
- **One gallon = 3,200 mL** (the jug actually bought), NOT the US 3,785.41 mL. Liter = 1,000 mL.
- The opening Perfume Alcohol is 9 gallons = 28,800 mL for ₱4,500 (corrected automatically; `scripts/gallon_stock_correction.py` handles edited rows).
- **Chemist and Labor** are flat charges of **₱5 each per bottle** the batch yields. They are costs, not materials: nothing is deducted from stock. They are added to the batch `total_cost` and to the **cost per new bottle**, and must show in Bulk Batches reports, the AI tool and the Tools calculator.
- **Oil is per scent**: a batch for a catalog scent uses that scent's own oil price and takes oil from that scent's stock (`scent_oils`). A scent not in the catalog uses the shared Oil Scent material.
- **Packaging is taken once**: a formula batch deducts bottles, stickers and seals for every bottle it can fill when it is created, and is refused if any is short. An opening batch records none.
- **Cost of goods per bottle** is a flat cost per size (`unit_cogs_settings`) that already includes packaging. It is the capital shown in reports and what a bottle costs when sold.
- **Opening balances**: stock that existed at go-live is one opening record per item (opening batch for liquid, cost-of-goods record for bottles) and never deducts materials.
- The page named **Base Cost of Goods** was formerly "Formulas". The **Process Flow** tab is a read-only picture of how a bottle is made and costed and refreshes live with price changes.

## Materials and prices
- Material units: Gram, Milliliter, Liter, Gallon, Piece. Every restock that changes a price is kept as history and charted.
- Default suppliers: BubbleMix (oil, fixative, moisturizer), Centralpack (alcohol, bottles), Alfamart - Lipa (distilled water), Shopee (stickers, plastic seals).

## Stock movement between locations
1. A branch requests SKUs from HQ. 2. HQ dispatches (HQ stock drops, a movement log is written). 3. The branch receives and confirms received / damaged quantities. 4. The request is fulfilled and the receipt kept. All steps are transactional.

## Packages and partners
- Packages are bundles sold to **Distributor** or **Reseller** partners at wholesale price (scope Distributor, Reseller, or Both). Only **Closed** package sales count in package revenue.
- Partner inquiries are a separate lead workflow, not branch retail sales. Inquiry emails are retried if the first send failed.

## Reports and the AI
- The report builder has 27 report types (PDF and Excel) for Admin and Branch, with a live row count. Cost/capital, COGS, chemist and labor, branch scope and bulk mL must agree between the page, the PDF, the Excel and Halo.
- Halo (AI) is read-mostly and role-scoped; it can propose stock drafts that a person confirms.

## Time, money, accounts
- Business time is **Philippine time (UTC+8)** everywhere (reports, backups, "today"). Do not change it.
- Money is in pesos (₱), two decimals; costs per mL may carry four decimals.
- Accounts are checked against the database on every request; deactivating or forcing a password change applies immediately.

## Backups (rules, not steps)
- Emailed backups are one AES-256 `.enc` file sealed with `BACKUP_ZIP_PASSWORD` (not a locked zip; Gmail refuses those). Without the password nothing is sent.
- Restore accepts the `.enc` as is, a plain backup zip, or a `.sql`. The Admin Log, login activity and app settings are kept as they are on restore.
- Backup times follow Philippine time.
