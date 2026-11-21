# Architecture (a map of the code)

`README.md` has the feature tour and the file-by-file table. This file is the short map: how the parts
fit together and where to look first.

## Shape of the system
One Flask app (Python 3, MySQL, Socket.IO) that renders pages on the server with Jinja, plus one separate
React site for the public partner portal. Production is a single process (one gunicorn gevent worker).

```
Browser ── nginx (TLS) ── gunicorn (1 worker, gevent-websocket) ── Flask app ── MySQL
                                                        │
                                                        ├── Socket.IO (live page refresh)
                                                        ├── Gemini API (Halo, the AI assistant)
                                                        └── SMTP (inquiry alerts, emailed backups)
```

## Code layout
- `wsgi.py` (production) and `app.py` (local) start `heaven_angel/app.py`, the app factory: config, security
  headers (Talisman/CSP), CSRF, rate limits, blueprints, background workers.
- `heaven_angel/config.py` — settings from `.env`; `ProductionConfig` forces unsafe/test options off.
- `heaven_angel/db.py` — pooled MySQL: `query`, `execute`, `transaction()` (all-or-nothing multi-table writes).
- `heaven_angel/routes/` — one blueprint per audience: `admin/` (one module per feature), `branch/`, `portal/`
  (public, slug-gated JSON API plus the built React pages), `auth.py`, `scan.py`, `ai.py`, `ai_tools/`.
- `heaven_angel/services/` — business logic shared by routes: sales and stock (`sale_stock`, `sale_entry`,
  `sales_import`), `reports/`, `receipts`, `backup*`, `mailer`, `audit`, `material_prices`, `scent_oils`.
- `templates/` (Jinja: `admin/`, `branch/`, `ai/`, `scan/`, shared partials) and `static/` (css, js, img,
  `sw.js`, `manifest.webmanifest`).
- `public-site/` (React + Vite + `motion/react`) builds into `static/public-site/` (git-ignored, rebuilt on every deploy).
- `schema.sql` — the whole schema plus guarded, re-runnable migrations; the deploy applies it every release.
- `scripts/` — admin creation, demo data, one-off corrections, backup decrypt tool.
- `tests/` — pytest. Most need MySQL (run in CI); a few are DB-free.

## How things flow
- **Request:** route → decorator checks (login, role, branch) → service/db → template. Role and branch are
  re-validated against the database on every request.
- **Stock:** every change goes through shared service code in a transaction, with a movement log; manual sales and
  Excel import use the same path.
- **Live updates:** a route that changes data calls `notify_all([...scopes])` (`heaven_angel/sockets.py`); the browser
  (`static/js/app/realtime.js`, `ROUTE_SCOPES`) re-fetches the page and swaps `.content` only if its HTML changed.
- **Reports:** `services/reports/definitions` → `filters`/`totals` → `builders_*` → `registry.get_report()` → `pdf.py` / `excel.py`.
- **Halo (AI):** `routes/ai.py` holds the system prompt and agent loop; tools in `routes/ai_tools/` are scoped by role/branch.
- **Backups:** `services/backup.py` dumps every table in one consistent snapshot into a zip; `backup_crypto.py`
  seals it as `.enc` for email; `backup_jobs.py` schedules and logs; restore reads the `.enc`, a zip or a `.sql`.
- **Deploy:** push to `main` → CI (`ci.yml`) → on green, `deploy.yml` over SSH (see `PRODUCTION.md`).

## Where to start for common changes
| I want to… | Start in |
|---|---|
| add an admin page | `routes/admin/<feature>.py`, `templates/admin/`, nav in `templates/base.html`, `ROUTE_SCOPES` |
| change costing | `routes/admin/batch_formulas.py`, `schema.sql`, Tools, Process Flow, reports, AI (see `BUSINESS_RULES.md`) |
| add a report | `services/reports/definitions.py` + a `builders_*` function |
| add an AI tool | `routes/ai_tools/declarations.py`, `registry.py`, the matching module |
| change the public site | `public-site/src/` then `npm run build` |
| add a table | `schema.sql`, backup rules (`PRESERVE_ON_RESTORE`/`EMPTY_IF_MISSING`), `CLEAR_DATA_KEEP_TABLES`, tests |

## Testing and CI
- Local: `python -m ruff check .` and the DB-free tests. Full suite runs in CI against MySQL (`ci.yml`), together with
  the public-site build, `pip-audit`/`npm audit` and a Docker smoke test.
- `tests/test_ui_copy_and_mobile.py` enforces the 130-character limit on visible text per template block.

## Visual system
Back-office theme tokens are in `static/css/tokens.css`, foundations in `base.css`, app styles in `style.css`
and `themes.css` (15 interface styles, light and dark). Every colour and font stack lives in the tokens; do not
hardcode colours in templates.
