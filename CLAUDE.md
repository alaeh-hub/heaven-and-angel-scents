# Keep-in-sync checklist (read this before you finish any change)

This file is a reminder for any AI (or person) working on this repo. A new feature is **not done**
when its page works. Many places in the system describe or list the features, and they go stale
silently. After every new feature, changed behaviour, renamed thing, new table/column, new route
or new setting, walk this list and update every place that applies. If a place does not apply,
skip it, but decide that on purpose.

The notes that must stay accurate live in `docs/` (`PRODUCTION.md`, `BUSINESS_RULES.md`, `ARCHITECTURE.md`, `DECISIONS.md`); read the ones that apply before changing the related area.

Rule of thumb: **search the repo for the old name/number/behaviour** (`grep -rn`) and for the
sibling features that already do the same kind of thing. Wherever a sibling is listed, the new
feature probably belongs there too.

---

## 1. Reports ("Generate report")
A new number, column or kind of record usually has to show up in the reports.

- `heaven_angel/services/reports/definitions.py` — `REPORT_TYPES` / `REPORT_GROUPS` (what exists, which roles, filters).
- `heaven_angel/services/reports/builders_*.py` — the data each report builds (sales / inventory / admin).
- `heaven_angel/services/reports/pdf.py`, `excel.py`, `totals.py`, `formatting.py` — PDF and Excel output.
- `heaven_angel/services/admin_report_data.py` and the `routes/*/reports.py` pages, `templates/_report_builder*.html`.
- Test that must stay green: `tests/test_reports_alignment.py` (every report builds and renders for each role).
- Ask: does the new field appear in the on-screen page **and** the PDF **and** the Excel? Do branch-scoped and HQ views agree?

## 2. The AI assistant ("Halo")
It answers from tools, and its instructions describe the system. Both go stale.

- `heaven_angel/routes/ai.py` — `SYSTEM_PROMPT` (what Halo believes about the system; fix any outdated rule, price, ratio, flow).
- `heaven_angel/routes/ai_tools/declarations.py` (tool schemas), `registry.py` (registration), and the tool modules `sales.py`, `stock.py`, `catalog.py`, `insights.py`.
- A new feature that changes money, stock, batches, products or sales should be readable by a tool, and existing tools must still return the right numbers (cost, COGS, totals).
- Tool results must respect role/branch limits (a Branch user must never see HQ-only data).
- Test: `tests/test_ai_tools_alignment.py`.

## 3. CI workflow
- `.github/workflows/ci.yml` — lint (ruff), public-site build, audits, docker smoke test, pytest against MySQL.
- New dependency, new env variable, new build step, new script, new service → CI must install/set/run it.
- If CI needs a new `.env` value, add it to the throwaway `.env` in the docker job and the pytest job.
- Run `python -m ruff check .` locally before pushing. DB tests only run in CI (MySQL), so read tests you touch carefully.

## 4. Deploy workflow and server notes
- `.github/workflows/deploy.yml`, `docs/PRODUCTION.md`, `Dockerfile`, `docker-compose.yml`, `Caddyfile`.
- New package → `requirements.txt` (and `requirements-dev.txt` for dev-only). The deploy installs requirements and applies `schema.sql` automatically.
- New env variable → `.env.example` (with a comment), `heaven_angel/config.py` (incl. the Production config), the docs above.

## 5. README and docs
- `README.md` — feature list, setup, env variables, scripts, commands. Update when a feature is added, renamed or removed.
- Notes in `docs/`: `docs/BUSINESS_RULES.md` (costing, units, roles: update when a rule changes), `docs/ARCHITECTURE.md` (code map), `docs/DECISIONS.md` (add an entry when the owner decides something that could look like a bug), `docs/PRODUCTION.md` (server facts). `docs/private/` is git-ignored.
- Rename = search every doc, template text, AI text, comment and test for the old name (example: "Formulas" became "Base Cost of Goods").

## 6. .gitignore (and .dockerignore if present)
- New local-only folder, generated file, secret, upload, build output, cache or backup → add it to `.gitignore` **before** committing.
- Never commit `.env`, real backups (`backups/`, `.enc`, `.zip`), customer data, certs/keys, `static/uploads/`, `static/public-site/`, `node_modules/`.

## 7. Database
- `schema.sql` — new tables/columns/seed rows go here, written so it can run again safely (the deploy re-runs it on every release). Include migrations for existing servers, not only fresh installs.
- Runtime self-heal in `heaven_angel/routes/admin/batch_formulas.py` (`ensure_cost_columns`, `reapply_data_fixes`) — a restored older backup must get new columns back.
- Backup and restore: see section 8 below.
- `heaven_angel/routes/admin/clear_data.py` — `CLEAR_DATA_KEEP_TABLES`: decide whether a new table is wiped or kept by "Clear data".
- `scripts/demo_data.py`, `scripts/import_opening_*.py`, correction scripts, `tests/test_demo_data.py`, `tests/test_catalog_seed.py` — keep in step with the schema seeds.

## 8. Backup and restore
Every new table, column, file or setting must survive a backup, an emailed copy and a restore. Backups dump **every base table automatically** (`SHOW TABLES`), so a new table is included without edits. What needs a decision:

- `heaven_angel/services/backup.py` — `PRESERVE_ON_RESTORE` (tables a restore must NOT overwrite, such as the Admin Log, login activity and app settings) and `EMPTY_IF_MISSING` (tables that only make sense with the rest of the data; emptied when an older backup lacks them). Decide for each new table; children before parents.
- A new column or table added after old backups were taken: an older backup restores without it. Make sure `reapply_data_fixes()` (`routes/admin/batch_formulas.py`) or the schema step adds it back after a restore, and extend `tests/test_backup_encrypted_restore.py` / `tests/test_backup_restore.py`.
- New uploaded files (photos, documents) must be added to `_uploads()` so they are in the zip and put back by `restore_uploads`.
- `heaven_angel/services/backup_jobs.py` — email text, schedule, log lines (sizes in KB/MB, masked recipient). `services/backup_crypto.py` — the `.enc` format (do not change the format without keeping old files readable). `services/mailer.py` — sending.
- `scripts/decrypt_backup.py` and the Restore file picker in `templates/base.html` (`.zip,.sql,.enc`) — update if the format or accepted files change.
- Settings: `BACKUP_ZIP_PASSWORD` (required to send), `BACKUP_NOTIFY_EMAIL`, mail settings, `BACKUP_TEST_RUN_AT` (ignored in production) — keep `.env.example`, `config.py` and the README in step.
- The manifest in the zip (`_manifest`) and README backup text must describe what is really inside.
- After any change here, think through: backup → email → restore on a fresh server → app starts and shows the data.

## 9. Pages, navigation, permissions, live updates
- `templates/base.html` navigation and the role it shows for (Admin / Branch). Admin-only pages need `@admin_required`; check `tests/test_rbac.py`, `tests/test_branch_isolation.py`.
- Live refresh: `static/js/app/realtime.js` `ROUTE_SCOPES` + `notify_all([...])` in the route that changes data (`heaven_angel/sockets.py`). The soft refresh only compares the `.content` HTML, so numbers that matter must be rendered inside `.content`.
- `templates/admin/process_flow.html` — the Process Flow tab describes how a bottle is made and costed; update it when batches, formulas, costs or steps change.
- Audit trail: new money/stock/user actions should call `log_action(...)` (`services/audit.py`).
- Branch vs Admin: does the branch version of the page/report need the same change?
- Other places that quietly repeat a number or rule: the Tools calculator (`routes/admin/tools.py`), Base Cost of Goods (`routes/admin/formulas.py`, `batch_formulas.py`), sale receipts and the sales import (`services/receipts.py`, `sales_import.py`), the dashboard and trends, and the Halo help/draft pages (`templates/ai/`).
- Front-end files: if you change shared CSS/JS in `static/`, check `static/sw.js` (cached under `ha-static-v3`; bump the version if a stale copy would break the page) and `static/manifest.webmanifest` for app name/icon changes.

## 10. Public website / partner portal
- `public-site/` (React). Product scents are **hardcoded** in `public-site/src/content/scents.js` on purpose; there is no products API. Footer contacts in `src/content/footer.js` are placeholders until real data is provided.
- Portal API lives in `heaven_angel/routes/portal/` (packages, site, inquiries). Changes there need `tests/test_partner_portal_api.py` and a rebuild check: `cd public-site && npm run build`.
- Default product photos: `static/img/scents/` and schema section 54a.

## 11. Tests
- Add or adjust tests in `tests/` for the new behaviour; put the "stays aligned" assertions next to the existing alignment tests.
- `tests/test_ui_copy_and_mobile.py` enforces: no visible text block over 130 characters in any template, centred empty states, mobile layouts. Keep helper text short.
- `tests/conftest.py` and `tests/factories.py` — add factories for new tables.
- Update tests that assert old numbers/names instead of deleting the check.

## 12. Security and config
- Secrets only in `.env` (never in code, docs, tests or logs). Update `.env.example` with a placeholder only.
- New POST routes: CSRF, role check, input validation. Report real error causes honestly (do not show "duplicate" for a database outage; use `is_duplicate_error`).
- CSP (`tests/test_csp.py`) when adding external scripts, fonts, images or inline code.
- Dependencies are audited in CI (`pip-audit`, `npm audit`); pin exact versions.

---

## Production server
This system is live on a real server. Also read `docs/PRODUCTION.md`: if a change needs a server restart, a `.env` edit, a one-off script, an nginx/systemd change or a manual database step, tell the user in a "Do this on the server" block at the end of your reply.

## Project conventions
- Business time is Philippine time (UTC+8, `BUSINESS_TIMEZONE_OFFSET`). Do not change it.
- Do **not** commit or push unless the user says so. When they do: **no `Co-Authored-By` line**, never.
- Stock units: bottles are counted in pieces; bulk is in mL. Alcohol:water mix is 1000:200; one gallon is 3200 mL; chemist and labor are flat charges per bottle on formula batches.
- Local helpers outside the repo are not part of the project; do not reference them in committed files.

## Before you say "done"
1. Walk sections 1–12 and note which ones applied.
2. `python -m ruff check .`
3. Run the tests that can run locally; list the ones only CI can run.
4. If `public-site/` changed: `npm run build`.
5. In your final message, say which of the places above you updated and which you checked and left alone.
6. End with the "Do this on the server" block from `docs/PRODUCTION.md` (or "No server action needed").
