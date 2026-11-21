# Decisions (why things are the way they are)

Short records so the same questions are not re-argued. Add a new entry whenever the owner makes a choice that
a future reader could mistake for a bug. Newest last.

| Decision | Why | Consequence |
|---|---|---|
| **Public catalog scents are hardcoded** in `public-site/src/content/scents.js`. | The owner wants the scent list fixed. | The unused products API (`/api/products`, `fetchProducts`) was removed. Adding a scent to the public page is a code change. |
| **Footer contacts are placeholders** (`public-site/src/content/footer.js`) for now. | The owner will supply real data later. | Do not "fix" them; replace when the owner provides the real details. |
| **Single gunicorn worker.** | Socket.IO rooms and rate limits live in memory. | Never add workers without a shared store (e.g. Redis). |
| **Production is systemd + nginx, not Docker.** Docker files stay for the CI smoke test and local use. | The Hostinger KVM 2 VPS is set up that way. | Server steps in `PRODUCTION.md` assume systemd (`heavenandangels`). |
| **Emailed backup is an opaque `.enc` file** (AES-256-GCM, key from `BACKUP_ZIP_PASSWORD` via scrypt), not a locked zip. | Gmail refused the AES-zip (`552 5.7.0`). | Restore opens the `.enc` as is; `scripts/decrypt_backup.py` opens it by hand. Changing the password makes old `.enc` files unreadable. |
| **A gallon is 3,200 mL**, not the US 3,785 mL. | It is the jug size actually bought. | The opening alcohol was corrected to 9 x 3,200 = 28,800 mL (automatic self-heal and a script). |
| **Alcohol : water mix is 1000 : 200.** | The owner's formula. | Self-healed in the app and in `schema.sql`. |
| **Chemist and Labor are flat ₱5 each per bottle**, as costs not materials. | Owner's costing rule. | In batch cost, cost per new bottle, reports, AI and Tools. No stock deducted. |
| **"Formulas" was renamed "Base Cost of Goods"**; a read-only **Process Flow** tab was added. | Clearer names for the owner. | Search old wording in docs, AI text, tests. |
| **Business time is Philippine time (UTC+8).** | The business operates in the Philippines. | Backups, reports and "today" use it. Do not change. |
| **Credit/backdating behaviour is left as is.** | Reviewed and kept by the owner ("let it be"). | Do not tighten it unprompted. |
| **Voided sales are deleted after a snapshot** (`sale_voids`). | Totals fix themselves without every query filtering voids. | History of voids is in `sale_voids`, not in `sales`. |
| **Production ignores `BACKUP_TEST_RUN_AT`.** | Test settings must not run on the live server. | Test the backup scheduler locally, or use the Automatic backup toggle. Never switch the server to development to test. |
| **No co-author line on commits; no commit or push unless asked.** | Owner's preference. | Applies to every commit. |
| **Code comments are short: one line each, and every function has one.** | The owner wants less noise but no bare code. | Long comment blocks were trimmed project-wide. Keep new comments to one line; do not re-expand them. |
| **Project notes live in `docs/`** (`PRODUCTION.md`, `BUSINESS_RULES.md`, `ARCHITECTURE.md`, `DECISIONS.md`); `CLAUDE.md` and `README.md` stay in the root. | One place the AI reads every session. | Private PDFs stay in `docs/private/` (git-ignored). |
