# Heaven & Angel Scents

A Flask-based operations platform for a perfume brand with a central warehouse, multiple retail branches, resale partners, and internal staff users. This is the system actually used day-to-day to manage inventory, production, branch sales, stock requests, partner leads, receipts, and reporting in one place — not a demo or starter template.

## Current project status

This repository is an active operational application. The system currently includes:

- HQ administration for catalog, product management, production, suppliers, packages, partners, customers, users, and reports
- Branch-side workflows for inventory, sales, refills, requests, receiving, and credit purchases
- Public partner portal for browsing packages and submitting inquiries without a login
- Role-based access control for Admin and Branch accounts
- Real-time updates with Socket.IO for shared operational views
- Installable back-office app (PWA) with static-asset caching and an offline notice; operational pages and records are never cached
- AI-powered internal assistant for scoped business questions over live data
- Audit trail and movement logging for operational accountability
- Receipt generation and QR-code scan verification for branch sales, plus a plain printed goods-received receipt for deliveries
- Several scents on one receipt: Record Sale and the Excel import can put up to 60 products on one order, printed 10 to a slip (a bigger order continues on the next slip)
- Raw-material price tracking with a price-trend chart, and built-in bulk batch formulas (15%, 14%, 13%) with a cost calculator under Tools
- Oil stocked and priced per scent (a Scent oils section on the Materials page, with restocks and a CSV import), used automatically when a bulk batch is made for that scent
- A report builder with 27 report types (PDF and Excel), a live row count before you download, and plain printed-document styling
- Products catalogue export (Excel or CSV) that can be edited and imported again
- Helpful empty states on every table, chart and list, and forms whose live notes never make the layout jump
- Bulk sales import/export via Excel, with the same stock and validation rules as manual entry, and an optional Receipt column to group rows into one receipt
- Light and dark mode in 15 interface styles, checked for readability at desktop, tablet and phone widths
- In-app database backup, restore, reminders, and optional scheduled email
  delivery, with an optional scheduled off-host copy

---

## What the system does

Heaven & Angel Scents runs as a single operating layer for the business across a few distinct roles:

| Role | Primary access | Main responsibilities |
| --- | --- | --- |
| Admin / HQ | `/admin/*` | Product catalog, production, stock dispatch, partner management, user management, financial/reporting views, audit log review |
| Branch staff | `/branch/*` | Inventory checks, branch sales, stock requests, receiving shipments, customer records, discrepancies, branch reports |
| Partner / reseller | `/partner-portal/<slug>/*` | Browse package offerings and submit inquiries through a public-facing portal, no account needed |
| Signed-in users | `/ai/*` | Ask business questions using a read-only AI assistant scoped to the user's data and role |
| Anyone with a receipt | `/scan/*` | Verify a printed/shared sale receipt is genuinely on file, by scanning or typing its QR code |

The system is built around a single source of truth in MySQL, with append-only movement logs and admin audit records so internal actions remain traceable after the fact.

---

## Product and business model

The app is designed around a retail and wholesale perfume operation:

- Products are tracked by SKU, with base code + unit size forming the unique identifier (e.g. `A1-50ML`, with a corresponding `A1-BULK` for the scent's unpackaged stock).
- Inventory is maintained per branch, not just globally — HQ (branch ID 1) holds bulk and warehouse stock; retail branches hold bottled stock only.
- HQ manages production batches, turning raw materials into bottled and bulk stock, with cost-of-goods tracked per batch.
- Branches place stock requests to HQ and receive fulfilled deliveries, with received/damaged quantities reconciled on arrival.
- Sales and refills are recorded per branch with payment method tracking (cash and credit).
- A refill draws from the scent's *bulk* stock rather than decrementing a bottled SKU, and is HQ-only since branches don't hold bulk.
- Packages (bundles of products) can be sold to partners or resellers at wholesale pricing.
- Partner inquiries are kept as a separate lead/opportunity workflow, distinct from branch retail sales.
- Financial reporting combines branch sales, partner orders, COGS, and operational stock movement history.

---

## High-level architecture

The app is a single Flask application with several route blueprints, a shared database layer, and realtime notification support via Socket.IO.

| File | Responsibility |
| --- | --- |
| `heaven_angel/app.py` | Builds the Flask app, configures security headers (Talisman), CSP, logging, and registers all blueprints |
| `heaven_angel/config.py` | Environment-based configuration: database, security, mail, AI, rate limits, partner portal slug |
| `heaven_angel/extensions.py` | Shared extension instances (Flask-Limiter, Flask-SocketIO) — single-process/single-worker by design |
| `heaven_angel/db.py` | MySQL connection pooling and a `transaction()` helper for atomic, all-or-nothing multi-table writes |
| `heaven_angel/decorators.py` | Shared route decorators for login/role/branch access control |
| `heaven_angel/utils.py` | Form-parsing helpers, constants (payment methods, sale types), validation, receipt-code signing |
| `heaven_angel/services/sale_stock.py` | Shared stock logic for recording and voiding a sale — used by both manual entry and Excel import, so every path moves stock identically |
| `heaven_angel/services/sales_import.py` | Builds/parses the Excel bulk sales import template and inserts an entire file as one atomic transaction; rows sharing a `Receipt` label become one order |
| `heaven_angel/services/audit.py` | Admin action audit log (`admin_actions` table) |
| `heaven_angel/services/login_activity.py` | Login/logout activity tracking (`login_activity` table) |
| `heaven_angel/services/mailer.py` | Outbound SMTP notifications (currently: new partner-inquiry alerts to HQ) |
| `heaven_angel/services/appsettings.py` | Small database-backed store for admin-managed settings such as the backup schedule |
| `heaven_angel/services/backup.py` | Consistent database snapshots, backup archives, and validated in-app restore |
| `heaven_angel/services/backup_jobs.py` | Backup reminders, schedule tracking, and optional automatic email backups |
| `heaven_angel/services/receipts.py` | PDF sale receipt generation, including the signed QR verification code. An order of several scents is printed as one page per slip, 10 products to a slip, each page as tall as its content |
| `heaven_angel/services/brand_assets.py` | Shared reportlab-drawn brand mark used in generated PDFs (reports, receipts) |
| `heaven_angel/services/reports/` | On-demand reports: `definitions` (groups, descriptions, `report_catalog(role)`) → `filters`/`totals` → `builders_*` (one query per report) → `registry.get_report()`; `pdf.py` / `excel.py` render them as plain printed documents |
| `heaven_angel/services/material_prices.py` | Raw-material price history and the trend figures behind the Materials chart |
| `heaven_angel/routes/admin/batch_formulas.py` | The built-in bulk batch formulas, sizes, settings and packaging, and their costing from live material prices |
| `heaven_angel/routes/admin/tools.py` | Tools page: unit converter, alcohol and water mix, bottle yield, price per unit, formula cost calculator (calculated in the browser, writes nothing) |
| `heaven_angel/services/sale_entry.py` | Record Sale form validation and the insert-plus-stock step, shared by HQ and branches. An order of several scents is recorded in one transaction (`record_sale_order`), and `annotate_receipts` tells a list which row carries the order's Receipt button |
| `templates/_order_basket.html`, `templates/_order_basket_list.html` | The "Add to receipt" button and the list of scents on the New sale dialog (shared by HQ and branch) |
| `heaven_angel/services/admin_report_data.py` | The queries behind the admin Reports charts |
| `heaven_angel/services/trends.py` | Time-bucket options shared by the admin and branch trend charts |
| `heaven_angel/app.py` | App factory: config, security headers, blueprints, background workers (small steps, one function each) |
| `heaven_angel/context.py` | Template context processors (top-bar sales chip, sidebar badges) |
| `heaven_angel/template_helpers.py` | Jinja filters (`peso`, `compact_peso`) and shared error pages |
| `heaven_angel/logging_setup.py` | Colour/JSON log formatting and request-ID stamping |
| `heaven_angel/sockets.py` | Socket.IO room membership and role/branch-scoped realtime event broadcasting |
| `scripts/create_admin.py` | Create or reset an Admin/Branch login (dev and server) |
| `scripts/demo_data.py` | Fills a freshly seeded local database with about 80 days of demo data for every part of the system (logins, branches, material purchases, bulk batches, production, deliveries, sales, voids, partners, packages, inquiries, logs). It refuses to run when records already exist; `--sales N` sets how many sales to make |
| `schema.sql` | Full operational database schema, including guarded migrations for existing installs |
| `heaven_angel/routes/auth.py` | Login, logout, and forced password-change flows |
| `heaven_angel/routes/admin/` | HQ screens, one module per feature (`products`, `production`, `formulas`, `materials`, `sales`, `stock_requests`, `partners`, `packages`, `inquiries`, `reports`, `backup`, `users`, `logs`, …), all registering on one shared blueprint in `_common.py` |
| `heaven_angel/routes/branch/` | Branch-side screens, one module per feature (`dashboard`, `inventory`, `stock_requests`, `sales`, `reports`) on one shared blueprint |
| `heaven_angel/routes/portal/` | Public partner portal, slug-gated: `pages` (serves the built `public-site/`), `catalog` (JSON API), `inquiries` (partner inquiry intake), `email_retry` (resends unsent notifications) |
| `heaven_angel/routes/ai.py` | Chat endpoint and session handling for the read-only AI assistant |
| `heaven_angel/routes/ai_tools/` | The data-fetching "tools" the AI assistant may call, scoped by role/branch (22 for Admin, 12 for Branch): `stock`, `sales`, `catalog`, `insights` (material price history, voided sales, inventory log, find-a-report), `declarations` (what the model is told), `registry` (dispatch) |
| `heaven_angel/routes/scan.py` | QR receipt verification — decodes and checks a signed receipt code against the database |

The platform uses:

- Flask 3 for application logic and routing
- MySQL (via `mysql-connector-python`) as the system database, with a pooled connection layer
- Flask-SocketIO for live page refreshes and notifications (threading mode in dev, gevent in production)
- Flask-Talisman for HTTPS enforcement, CSP, and security headers
- Flask-WTF for CSRF protection
- Flask-Limiter for request throttling (in-memory store, single-worker deployment)
- reportlab and openpyxl for printable/exportable PDF and Excel reports
- Pillow for image handling (product photos, uploads)
- Google's Gemini API for the internal AI assistant
- A separate React + Vite front end (`public-site/`) for the public partner portal

The back-office visual system is documented in [`docs/design.md`](./docs/design.md). Its shared theme tokens live in `static/css/tokens.css`, global foundations in `static/css/base.css`, and application styles in `static/css/style.css` and `static/css/themes.css`.

---

## Core workflows

### 1. Authentication and access control

Users log in through a role-aware flow. Admin and Branch accounts are validated against the database on every authenticated request, not only at login — so deactivating an account or forcing a password reset takes effect immediately, not just on next login.

Key behaviors:

- Admin and Branch login are handled in the same auth system with role checks
- Inactive accounts are blocked outright
- Password-change enforcement is supported for fresh or admin-reset accounts
- Session state is kept lightweight but revalidated against live DB state on each request
- Login/logout events are recorded in `login_activity` for visibility into account usage

### 2. Warehouse and branch inventory flow

The system supports the movement of stock between HQ and branches:

1. A branch creates a stock request for one or more SKUs.
2. HQ reviews and dispatches the request.
3. HQ stock is reduced and a movement log is recorded.
4. The branch later receives the shipment and confirms the received/damaged quantities.
5. The request is marked fulfilled and the final receipt is preserved for traceability.

This process is transactional end-to-end, so stock counts and movement history never drift out of sync even if a step fails partway through.

### 3. Production tracking

HQ logs production batches that convert raw materials into finished (bottled) and bulk stock. Each batch is costed against unit formulas and bulk rate settings, updating warehouse inventory and recording the production event in movement history — tying production directly into COGS and profit reporting.

Bulk batches are made from the built-in formulas (15%, 14%, 13%, each in 500, 250 and 125 mL timpla sizes), so nobody picks materials one by one. Each ingredient role is tied once to a raw material, and the batch cost comes from that material's current price. When logging a production run, a short one-line note shows the cost per unit and the mL drawn from the chosen batch. A gallon is 3,200 mL everywhere (`ML_PER_BATCH_UNIT` in `heaven_angel/utils.py`), not the 3,785 mL US gallon. A formula's "Alcohol" amount is alcohol and distilled water together, split 1000 : 200 (1000 mL alcohol to 200 mL water); the ratio lives in `bulk_formula_settings`, and `schema.sql` corrects a database still on the old 200 : 1000 default.

How stock and cost move (so a batch's bottles are never counted twice):

- **Oil is per scent.** A batch made for a catalog scent is costed at that scent's own oil price and takes its oil from that scent's stock (`scent_oils`). A batch of a scent that is not in the catalog uses the shared Oil Scent material instead.
- **Packaging is taken once.** A batch made through the formula flow takes the bottles, stickers and seals for every bottle it can fill out of materials stock when it is created, and is refused if any is short. A batch with no packaging recorded (an opening balance, for example) has its packaging taken out of stock when production runs are logged from it, one bottle, sticker and seal per bottle made, and the run is refused if any is short. A production run never deducts packaging a batch already took.
- **Cost of goods per bottle** is a flat cost per bottle size (`unit_cogs_settings`) that already includes packaging. The cost of goods logged is shown as capital on the Reports page and is what a bottle costs when it is sold.
- **Opening balances.** Stock that already existed when the system went live is loaded with one opening record per item (an "Opening balance" in the stock history, an opening batch for mixed liquid, a cost-of-goods record for bottles) and never deducts materials or counts as a production run. This was done with one-time scripts (a dry run first, refusing to run twice) kept out of git because they hold real stock figures; see `docs/OPENING_DATA_IMPORT.md` (a local note).

### 3a. Materials and price trends

The Materials page tracks raw materials, suppliers and restocks. Every restock that changes a price is kept as history, and the page charts it on a real time axis with a summary of the biggest movers. Admins can also ask the AI assistant about price history. Branch accounts cannot see material prices.

Oil is the exception to "one row per material". Each scent has its own oil, bought at its own price and kept in its own stock, so the Oil Scent row in the table is a summary (price range, total stock, how many scents are out), and the scents live in a collapsed, searchable **Scent oils** section below it. Each scent can be edited (price and stock), restocked (adds stock and averages the price with what is on hand, recorded with its purchase date), or set in bulk from a CSV (download the template, fill in `price_per_unit` and `stock`, import; every row is checked first and a blank cell keeps the current value). Scents start at 0 stock. Oil restocks count in the "raw materials purchased" figures and appear in the Materials report; the Oil Scent material's own stock is only the shared pool used by scents that are not in the catalog.

Default suppliers are seeded with the materials: BubbleMix (oil, fixative, moisturizer), Centralpack (alcohol, bottles), Alfamart - Lipa (distilled water) and Shopee (stickers, plastic seals, no address). Contact details are filled in on the page.

### 3b. Tools

The Tools page gives HQ calculators for everyday numbers: unit converter, alcohol and water mix, bottle yield, price per unit, and a formula cost calculator with editable prices. Nothing there writes to the database.

### 4. Sales, refills, and voids

Branches (and HQ) record sales through `heaven_angel/services/sale_stock.py`'s shared logic, so manual entry and Excel bulk import both move stock identically:

- **Sale / Freebie** — the product's own stock at that location decreases.
- **Refill** — the customer brings their own bottle; no bottled unit leaves stock, but the scent's *bulk* product is drawn down instead (HQ-only, since branches don't stock bulk).
- Payment methods include cash and credit entries.
- Every sale produces a PDF receipt with a signed, scannable QR code (see `heaven_angel/services/receipts.py` / `heaven_angel/routes/scan.py`).
- **Several scents on one receipt.** On the New sale dialog, "Add to receipt" builds a list of scents with one customer, payment method, type and date. The scents are recorded together, all or none, as separate sales rows tied by `sales.order_id` (the first row's id), so stock, voids and reports still work per product. A sale of one scent has no `order_id` and behaves as before.
- **Slips.** A receipt prints 10 products to a slip. A bigger order continues on a second and third slip (one PDF page each, headed "Slip 2 of 3", with a subtotal per slip and the order total on the last). Long names and addresses wrap and the slip grows to fit.
- A sales list shows one **Receipt** button per order (with its product count) on the order's first row; the other rows say which receipt they are on. Voiding from Sales History is still per row; voiding or settling credit from Scan Receipt applies to the whole order.
- A sale can be **voided**: stock is restored, a permanent snapshot is kept in `sale_voids` (who voided it and why), and the sale row is removed so revenue/units/customer/credit totals self-correct without every query having to filter voids out.

### 4a. Reports and documents

Admin and Branch each get a report builder: report cards in group tabs with search, choice chips for options, date presets, a plain-words summary of what will be produced, and a live row count before you download. There are 27 report types (including material prices, scent oils and shipments), each as PDF or Excel. The Materials report lists scent oil purchases too, the Scent Oils report shows oil stock, price, value and purchases per scent, and every "raw materials purchased" figure (dashboard, Reports page, Financial Summary and the AI assistant) includes oil bought per scent.

All printed documents follow one plain style: sentence-case headings, hairline rules, no outer boxes and a restrained accent. This covers the report PDFs, the Excel reports, the sale receipt and the goods-received (delivery) receipt. The Products export keeps its header in row 1 and data from row 2 so a downloaded file can be edited and imported again.

### 5. Bulk sales import

HQ or a branch can download an Excel template, fill in a batch of sales offline, and re-upload it. The whole file is validated and inserted as **one atomic transaction** — either every row is applied, or none are — with every problem reported at once rather than failing row-by-row partway through an import.

The last column, **Receipt**, is optional. Rows with the same text there (A, B, 1, 2…) go on one receipt, like several scents on one New sale: they must share the same customer, payment, type and date, and a receipt holds at most 60 products. A blank cell is a receipt of its own.

### 6. Partner portal

The public partner portal gives distributors and resellers access to package offerings without requiring an authenticated account. It's protected by a deployment-specific slug instead of a login — the link is only ever shared directly by HQ, never linked from the signed-in app.

Its pages are a separate React app in `public-site/` (Vite + React Router + Motion, Geist type, Phosphor icons), with its own design tokens in `public-site/src/styles/tokens.css` (light and dark, following the visitor's system setting). Flask checks the slug, serves the built app at `/partner-portal/<slug>/packages[/<id>]`, and exposes the JSON API it uses under `/partner-portal/<slug>/api/` (see `heaven_angel/routes/portal.py`). Product photos, bottle renders, and video are still served from Flask's `static/`.

Product photos: each catalog scent (A1-A14, H1-H17) uses its built-in photo from `static/img/scents/` as the default product photo, for every size, so none need uploading. The default is the square, bottle-centred `-thumb.webp` (the full `-bottle`/`-box` renders are wide canvases with the bottle on the right, which looks off-centre in small square boxes; the public site uses those full renders). `schema.sql` (section 54a) fills the default for any product with no photo on every deploy, replaces the earlier wide render, and never replaces an uploaded photo; adding or importing a product, or removing its photo, falls back to the same default. A photo uploaded from the Products page always wins. A12-A14 and H17 were generated from the existing renders (same artwork, relabelled), and their angel names (Michael, Chamuel, Barachiel, Ariel) and label descriptors are placeholders: change them in `public-site/src/content/scents.js`.

The flow includes:

- browsing partner package listings and viewing package details
- submitting inquiries with validated contact information
- server-side price/order recalculation (never trusting client-submitted totals)
- stored inquiry history and status progression
- real-time HQ notification of new partner leads over Socket.IO

### 7. Receipt verification (QR scan)

Every sale receipt PDF carries a short, HMAC-signed code (not the sale's actual data) rendered as a QR code. For an order of several scents the code points at the order's first row, and the scan page lists every product with the order total. `/scan/*` lets anyone with the receipt — staff or customer — scan that QR (webcam) or upload a photo of it, decode it client-side, and verify server-side that the sale it points to genuinely exists, pulling the real details from the database rather than trusting anything printed on the receipt itself. Manual code entry is available as a fallback. Branch staff can only verify sales recorded at their own branch; Admin can verify any sale.

### 8. AI assistant

The AI assistant is intentionally read-only and scoped to the signed-in user's role and branch. It looks up live business data with tools (`heaven_angel/routes/ai_tools/`, chosen by Gemini per question) and answers with strict rules:

- use only the data it was given
- never invent inventory, prices, or history
- never perform writes
- answer only in plain text
- point the user to the relevant operational page when they ask for an action

HQ admins also get tools for materials, oil stock per scent, bulk batches (including whether a batch's packaging is recorded), production cost of goods (with opening-balance stock shown apart) and financials. Branch accounts never see those. This keeps the assistant genuinely useful for business questions without allowing it to operate the system unsupervised. Chat usage is rate-limited per user to control API cost.

### 9. Audit and movement tracking

The system keeps two separate but complementary records:

- `admin_actions` — who changed what in configuration or operational metadata
- `stock_movement_logs` — what happened to stock quantities over time, and why (sale, refill, production, dispatch, receipt, void, etc.)

Together they make it possible to answer both operational and administrative "what happened and who did it" questions after the fact.

### 9a. Interface behaviour

- Every table, chart and list shows a short empty state (what is missing and the next step) instead of a blank box; the shared markup is `templates/_empty.html`.
- Confirmations use a styled in-app dialog (`window.appConfirm()`), not the browser's `confirm()`.
- Dropdowns share one smart matcher (`static/js/app/smart-match.js`): case, accents and word order are ignored and one small typo is forgiven.
- Notes that fill in as you type (a total, a stock count, a price per unit) sit in reserved space (`.live-slot` in `style.css`), so forms and the cards sized to them do not change height.
- The sidebar groups are labelled for screen readers and mark the current page with `aria-current`.
- Light and dark mode work in every interface style. The sidebar wordmark is deepened to bronze on light surfaces, placeholder text and disabled buttons are kept readable, and chart and sparkline colours follow the theme live (no reload) and are kept at least 3:1 against what they are drawn on (`static/js/app/charts.js`).
- The top bar sheds its extras as the room narrows (clock, shortcut hint, trend, "Today", full amount) so the bell, sound and theme buttons stay reachable on a tablet or with the sidebar pinned; on a phone the sales chip shows the compact amount.
- The New sale dialog fits the screen without scrolling; the list of scents scrolls inside itself after about five rows.
- On a phone the floating assistant button tucks away while the page scrolls down, hides while the menu is open, and the Halo panel opens full screen with its own close button. On Halo's full chat page there is no floating button.
- Empty states are centred (icon on top, text and action under it) at every width.
- Touch screens get larger tap targets for report chips, tabs and mode switches, and the top-bar sales chip fits phones from 320px up (compact amount, count without the word "sales" on small phones).
- Helper text is kept to one short sentence per note (`tests/test_ui_copy_and_mobile.py` fails if a template carries a paragraph over 130 characters).

### 10. Backups and restore

Admins can create and restore database backups from **Settings → Backup**. A backup
contains a consistent database snapshot, spreadsheet-friendly CSVs, and uploaded
product photos when the email attachment limit allows. Restore validates the
archive before replacing database tables, keeps a safety copy of the current data,
and records the attempt in the Admin Log. Every table is included automatically, new ones too. Restoring a backup that predates the per-scent oil tables empties them rather than leaving today's oil stock beside older data. Admins can also enable reminders and
opt-in scheduled email backups. These archives contain customer data and password
hashes, so the emailed copy is wrapped in an AES-256 encrypted zip using the
`BACKUP_ZIP_PASSWORD` passphrase (12+ characters, never emailed). Without it, no
backup email is sent. To restore from an emailed backup, open it with 7-Zip or
WinRAR and the passphrase, then upload the inner `.zip` to **Restore**. Backups
downloaded directly, and the on-disk safety copies, are not wrapped (the safety
copy is written owner-only).
In Docker Compose deployments, the pre-restore safety copy is stored in the
persistent `appbackups` volume so it survives container rebuilds. This volume is
not a substitute for the separate, off-server backup routine.

For off-server scheduled backups, retention, secrets, and restore drills, see
[BACKUP.md](./docs/BACKUP.md). In-app email backups complement rather than replace an
independent off-server copy. Docker Compose deployments can also enable the
optional `offsite` profile to copy local backup archives to an rclone remote
every six hours; set `OFFSITE_DEST` and the relevant `RCLONE_CONFIG_OFFSITE_*`
variables in `.env` before starting it with
`docker compose --profile offsite up -d`.

---

## Security and operational safeguards

The application is built with a production-minded security posture:

- HTTPS enforcement and hardened security headers in production via Flask-Talisman, including a locked-down Content-Security-Policy
- CSRF protection on all state-changing forms (Flask-WTF)
- Session cookies set with security-friendly defaults (`HttpOnly`, `SameSite=Lax`, `Secure` in production)
- Signed-in sessions expire after an idle timeout (8 hours by default), and password changes invalidate other sessions for that account
- Socket.IO connections are authorized against the live account and password state before joining a role/branch room
- Inline scripts are nonce-authorized by the Content-Security-Policy; inline event handlers are not allowed
- Role-based access control enforced through shared route decorators, re-checked against the database on every request
- Rate limiting on high-risk endpoints such as login and AI chat usage
- A hard cap on request body size (`MAX_CONTENT_LENGTH`) so no endpoint can be forced to buffer unbounded uploads
- Public portal isolation behind a long, random deployment slug rather than a signed-in session
- Receipt QR codes are HMAC-signed, not raw sale data — a garbled scan or hand-typed guess is rejected rather than trusted
- Atomic multi-table writes through a shared transaction layer (`db.transaction()`) to prevent partial updates on failure
- Best-effort audit logging and email notifications that never block the underlying business action if they fail
- The app refuses to start in a non-debug run with a default/insecure `SECRET_KEY`
- No third-party scripts: Socket.IO is vendored in `static/js/vendor/`, and the CSP also sets `base-uri`, `form-action` and `object-src`
- Signed-in pages and exports are sent with `Cache-Control: no-store`, so the Back button after logout can't show cached data
- Logging out invalidates that session server-side and closes its live connections; deactivating an account or resetting its password closes the account's live connections
- Single-use form tokens are remembered server-side, so a double-click or a replayed cookie can't record the same sale twice
- Login lockout is counted per username and IP address (with a higher account-wide threshold), so a stranger can't lock the real admin out from another IP
- Excel exports neutralise cell text that starts with `=`, `+`, `-` or `@`; restoring a backup only accepts image files for uploads, with size caps
- Temporary passwords are shown once from server memory, never stored in the session cookie
- Clear Data keeps the Admin Log and login activity; the web container does not receive the MySQL root password

> The in-memory pieces above (used form tokens, signed-out sessions, live-connection tracking) rely on the single gunicorn worker the app is designed to run with; a restart forgets them.

---

## Configuration and environment

The app reads configuration from environment variables and a local `.env` file when present. Core settings include:

- `APP_ENV` = `development` or `production`
- `FLASK_DEBUG` = enables debug mode locally
- `SECRET_KEY` = application secret, required to be set explicitly for any non-debug run
- `SESSION_IDLE_HOURS` = signed-in session idle timeout in hours (defaults to `8`)
- `LOG_FORMAT` = `json` for structured JSON logs or `text`; defaults to JSON in production and text otherwise
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, `MYSQL_POOL_SIZE` (defaults to `20`, capped at `32`)
- `MYSQL_POOL_WAIT_SECONDS` = how long a request waits for a free pooled database connection before receiving a `503` (defaults to `5`)
- `BUSINESS_TIMEZONE_OFFSET` = the UTC offset used for "business day" boundaries in reporting (defaults to `+08:00`, Philippines)
- `PARTNER_PORTAL_SLUG` = stable public portal slug for partner access. There is no built-in default: set a long random value (`python -c "import secrets; print(secrets.token_urlsafe(16))"`). If unset, a random one is generated per process and the shared link breaks on every restart, so always set it in production
- `GEMINI_API_KEY` and `GEMINI_MODEL` for the AI assistant, plus `AI_CHAT_RATE_LIMIT`
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`, `PARTNER_INQUIRY_NOTIFY_EMAIL`
- `BACKUP_NOTIFY_EMAIL` (defaults to the partner-inquiry notification address), `BACKUP_EMAIL_MAX_MB` (attachment limit), and `BACKUP_SCHEDULER` (disable scheduled backup checks in a process)
- `BACKUP_ZIP_PASSWORD` = passphrase (12+ characters) that encrypts the emailed backup; required for backup emails to be sent
- `PORTAL_CONTACT_PHONE`, `PORTAL_CONTACT_EMAIL`, `PORTAL_CONTACT_VIBER`, `PORTAL_CONTACT_MESSENGER_URL`, `PORTAL_REPLY_TIME` — optional contact details shown on the public portal
- `RATELIMIT_ENABLED` and `MAX_CONTENT_LENGTH`
- `SOCKETIO_CORS_ALLOWED_ORIGINS` = origins allowed to connect over Socket.IO (defaults to `same-origin`; use `*` only if you really want any origin)
- `TRUST_PROXY` = set to `1` only when the app is reachable exclusively through a trusted reverse proxy (nginx/Caddy); enables one-hop forwarded host/protocol/IP handling. Docker Compose sets it already; on a direct server install add it to that server's `.env`, otherwise every visitor shares one rate-limit bucket and the login log records `127.0.0.1`
- `ALLOW_DATA_RESET` = enables the dev-only "clear all data" admin action; hard-disabled in production regardless of this setting

The app is intentionally strict in production mode and will warn, or fail to start, if required configuration is missing or insecure.

---

## Local development workflow

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Ensure MySQL is running and reachable (defaults match a stock XAMPP install: `localhost:3306`, user `root`, empty password).
4. Load the schema in `schema.sql` into your database.
5. Set required environment variables in a `.env` file or shell config (see above).
6. Optionally load demo data into a freshly seeded local dev database only. The schema seeds the 8 materials, 2 suppliers, 31 products and two branches; the script adds logins (password `Demo@12345678`, printed at the end), batches, deliveries, a few hundred sales (including multi-scent orders that print on 2 and 3 slips), partners and logs:

```bash
python scripts/demo_data.py              # needs an empty database, see "Starting a local database over" below
python scripts/demo_data.py --sales 900  # more sales
```

7. Start the app with:

```bash
python app.py
```

A local dev database can be emptied from the admin Clear data page (type `CLEAR ALL DATA`, needs `ALLOW_DATA_RESET`). It keeps users, branches and pricing/formula settings.

#### Starting a local database over

To go back to a clean install (only the seeded materials, suppliers, products and branches), drop the database and apply the schema again:

```bash
mysql -u root -p -e "DROP DATABASE IF EXISTS heaven_and_angel_scents;"
mysql -u root -p < schema.sql
python scripts/create_admin.py        # there are no logins until you make one
```

This deletes everything, so it is for a development machine only. `python scripts/demo_data.py` can then be run on top of it.

#### Database schema and deploys

`schema.sql` is additive and safe to run again: tables and columns are only created when missing, seeds use `INSERT IGNORE`, and migrations check `information_schema` first. Running it on a database that already holds real data does not delete or change that data (a seeded material or product you deleted is added back). The deploy workflow (`.github/workflows/deploy.yml`) takes a database dump, then applies `schema.sql` before restarting the service, so a pushed change that needs a new column or table arrives with it. The file starts with `USE heaven_and_angel_scents;`, so the live database must have that name (`MYSQL_DB`).

For the partner portal front end (`public-site/`), in a second terminal:

```bash
cd public-site
npm install
npm run dev      # http://localhost:5173/partner-portal/<slug>/packages, hot reload,
                 # proxies the API and /static to Flask on :5000
npm run build    # or build once and use Flask's own :5000 URL
```

(The npm scripts call Vite through `node` directly because npm's Windows shims break on the `&` in this folder's name.)

The signed-in back office can be installed from a supported browser's install option. Service workers require HTTPS (except on localhost); the worker caches static files only and shows an offline notice when a page cannot load. It does not make operational pages or records available offline.

For production-style WSGI serving, the repo also includes `wsgi.py` and the required Gunicorn + gevent stack in `requirements.txt`. The app is designed as a **single worker process** by default. Rate-limit counters use in-memory state unless `RATELIMIT_STORAGE_URI` is set to a shared backend such as Redis (install the `redis` package when using Redis); Socket.IO still uses process-local state.

### Running tests

The test suite (`tests/`) covers RBAC, branch isolation, session and Socket.IO authorization, CSP nonce enforcement, sales/voids, stock requests, bulk batches and batch formulas, scent oils and production packaging, material price trends, multi-scent orders and their receipts (slip count, layout, scan and void), Excel import grouping, the demo-data script's tables, reports, AI tool alignment, partner portal API, dashboard trends, and more. Integration tests use a disposable MySQL database:

```bash
pip install -r requirements-dev.txt   # pytest + ruff (not in the production image)
pytest
```

The public site's product imagery is checked in as optimized WebP files
under `static/img/scents/` (including the `-s` and `-m` thumbnail copies the
site loads), with the H1 and A3 hero label textures under
`static/img/scene/`. Commit and deploy those folders with the site; the
product list lives in `public-site/src/content/scents.js`. The full-size
source renders in `assets/scent-renders/` are excluded by `.gitignore`.

---

## Project structure

```text
.
├── app.py                # local dev entry point
├── wsgi.py               # production entry point (gunicorn)
├── heaven_angel/         # the Flask application package
│   ├── app.py, config.py, extensions.py, db.py, decorators.py, utils.py, sockets.py
│   ├── routes/           # blueprints (admin, auth, branch, portal, ai, ai_tools, scan)
│   └── services/         # business logic: reports, receipts, backup, mailer, material prices, ...
├── scripts/              # create_admin.py, demo_data.py (fills a fresh database with demo data)
├── schema.sql
├── requirements.txt
├── README.md
├── docs/                 # design (visual system), BACKUP, DEPLOY, DOCKER_DEPLOY, HOSTINGER_DEPLOY, QA pack
│                         # (local-only notes such as OPENING_DATA_IMPORT and OPEN_QUESTION_* are git-ignored)
├── public-site/          # partner portal front end (React + Vite)
│   └── src/
│       ├── components/
│       ├── content/
│       ├── hooks/
│       ├── pages/
│       └── styles/
├── static/
│   ├── css/              # Tokens, shared foundations, and application themes
│   ├── js/
│   ├── img/
│   ├── video/
│   ├── uploads/
│   ├── public-site/       # build output of public-site/ (git-ignored)
│   ├── manifest.webmanifest
│   └── sw.js              # Static-only service worker
├── templates/
│   ├── admin/
│   ├── branch/
│   ├── ai/
│   ├── scan/
│   ├── base.html
│   ├── login.html
│   └── errors/
├── tests/
└── fonts/
```

---

## Business notes and current characteristics

The current implementation is optimized for a real operating environment, not a purely academic prototype. In practice, that means:

- most workflows are server-rendered and database-backed, not a decoupled SPA (the partner portal is the one exception, by design)
- stock and revenue reporting are tied to live transactional data, not periodic snapshots
- branch, admin, and partner access are intentionally segmented at the route and data level
- audit visibility and movement history are part of the expected user experience, not an afterthought
- realtime updates are built into core workflows (new leads, stock changes) rather than bolted on later
- the system expects a reliable MySQL environment and correct production configuration (secret key, slug, mail, CORS) to run safely

This is a serious internal operations platform with real business workflows, accounting logic, and operational visibility built into the core application design.

---

## Known open items

- Per-scent oil price and stock is built (Materials page, "Scent oils" section; table `scent_oils`). Scent oil price history and a per-scent price trends chart are not built; restocks are recorded in `scent_oil_restocks` and appear in the Materials and Scent Oils reports. Oil bought per scent counts toward the Oil Scent material's supplier in the supplier spend totals.
- Opening stock is loaded for materials, scent oils (13 scents), mixed bulk (29 scents, costed at an estimated 0.82 per mL from the old computation) and finished 85 mL bottles at HQ only. Balayan's bottle stock is still 0 and needs its own opening count. Cost of goods is set for 85 mL only; the other sizes and the Bulk/Refill selling rate per mL are still 0 until set on the Formulas page.
- Report documents are checked structurally and by rendering; Excel files and the printed QR have not been checked by eye or by camera.
- Sale receipts, including the long multi-slip ones, have been checked as PDFs on screen but not yet on a real thermal printer.
- The AI assistant's tools are tested directly; the live Gemini model has not been tested end to end after the latest tool changes.

---

## Summary

Heaven & Angel Scents is a full inventory and branch operations system for a fragrance brand. It combines warehouse management, production and COGS tracking, branch retail operations (sales, refills, voids, bulk import), partner lead handling through a public portal, receipt generation with QR verification, financial and operational reporting, audit logging, and AI-assisted business support into a single Flask application.

It is currently structured as a real-world internal business system with strong operational guardrails, role isolation, and traceable inventory and finance workflows.
