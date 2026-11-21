# This system is LIVE in production (read this before you finish any change)

Heaven & Angel is **already running on a real server** with real customers, stock and money in its
database. A change that works on a developer machine can still need something done **on the server**.
Whenever your change needs one of the actions below, **tell the user plainly, at the end of your
reply, in a "Do this on the server" block**, with the exact commands. Do not assume they will
notice. If nothing is needed, say "No server action needed."

## How production is set up
- One **Hostinger KVM 2 VPS** (Ubuntu Linux), **not Docker**. App under systemd, nginx in front, MySQL on the box.
  The app runs **single-worker** (`gunicorn -k geventwebsocket... -w 1`); do not add workers (Socket.IO
  and rate limits live in memory).
- App folder: **`/var/www/heavenandangels`**. systemd service: **`heavenandangels.service`**
  (restart: `sudo systemctl restart heavenandangels`). Logs: `sudo journalctl -u heavenandangels -n 100 --no-pager`.
  These match the deploy workflow defaults.
- Public address: `https://heavenandangels.jampongcorp.tech` (health check: `/healthz` answers `ok`).
- Config is the server's own **`.env`** file. It is **never in git** and **never touched by a deploy**.
  systemd reads it only when the service starts, so any `.env` edit needs a restart.
- Production config forces some things off (for example `BACKUP_TEST_RUN_AT` is ignored); see
  `ProductionConfig` in `heaven_angel/config.py`.
- Business time is Philippine time (UTC+8).

## What a deploy already does by itself (no action needed for these)
Every push to `main` that passes CI triggers `.github/workflows/deploy.yml`, which on the server:
`git pull` → pre-deploy database dump (`backups/pre_deploy_*.sql.gz`) → `pip install -r requirements.txt`
→ `npm ci && npm run build` for `public-site/` → `mysql < schema.sql` → `systemctl restart` → health check.

So these need **no manual step**: Python/template/JS code changes, new pip packages, public-site changes,
additive and re-runnable `schema.sql` changes (new tables, new columns, seed rows with safe `INSERT IGNORE`).
The restart in the deploy also reloads code.

## What DOES need a manual step (tell the user)

| If the change… | Tell the user to… |
|---|---|
| adds, renames or changes an **environment variable** | edit `.env` **on the server**, then `sudo systemctl restart heavenandangels`. Give the exact `KEY=value` line (never real secrets) and update `.env.example` too. |
| changes the meaning of an existing `.env` value (a slug, a recipient, a password) | the same, and say which old value is now wrong. |
| needs a new **GitHub secret/variable** (deploy key, host, port, known hosts, health URL…) | add it in GitHub → Settings → Secrets and variables → Actions (environment `production`) **before** merging, or the deploy fails. |
| changes **nginx** (headers, upload size, websocket, new path) | edit the nginx site file, `sudo nginx -t`, `sudo systemctl reload nginx`. Show the exact block. |
| changes the **systemd unit** (ExecStart, workers, EnvironmentFile, timeouts) | edit `/etc/systemd/system/...service`, `sudo systemctl daemon-reload`, then restart. |
| needs a **new system package or tool** (apt package, newer Node/Python, font, library for a pip wheel) | `sudo apt install ...` first, then restart. Say so before the deploy runs, not after it fails. |
| has a **destructive or non-idempotent database change** (drop/rename a column or table, change a type, backfill existing rows, delete data) | do **not** rely on `schema.sql` alone. Provide a one-off SQL or script, say to take a backup first, and say whether it must run **before or after** the deploy. |
| needs a **one-off data correction script** (like `scripts/mix_stock_correction.py`, `gallon_stock_correction.py`, imports) | give the exact command, run from the app folder with the venv (`cd /var/www/heavenandangels && venv/bin/python scripts/...`; the folder is `venv` or `.venv`, check with `ls -a`), say whether it is safe to run twice, and recommend a backup first. |
| changes **file permissions, folders or uploads** (`static/uploads/`, `backups/`, certs) | give the `mkdir`/`chown`/`chmod` commands. Uploads and backups are not in git. |
| changes a **scheduled job** (backup schedule, cron, the in-app scheduler) | say whether a restart is needed for it to pick the change up, and how to confirm it ran (Admin Log). |
| affects **backups or restore** (new table/file, format change, new password) | remind them to take a manual "Email backup now" first, and to keep `BACKUP_ZIP_PASSWORD` unchanged unless they accept old `.enc` files will not open. |
| changes the **login/session/security settings** (SECRET_KEY, cookie flags, session hours) | warn that changing `SECRET_KEY` logs everyone out, then restart. |
| changes **CSP, TLS, domain or the partner-portal slug** | give the nginx/`.env` changes and the new public URL to check. |
| is **risky to roll back** (data format changes) | say how to roll back: the pre-deploy dump in `backups/`, and re-run the Deploy workflow on an earlier commit from the Actions tab. |

## Things that look like server work but are NOT
- Editing `.env.example`, README or docs: no server action.
- Changing a template, CSS or JS: the deploy restarts the app; users may need a hard refresh (check
  `static/sw.js` cache version if a stale copy would break the page).
- Do **not** tell the user to switch the server to development mode to test something; production
  deliberately disables test-only settings. Test on a local machine instead.

## How to give the server steps (always do it this way)
When the user has to do something on the server, write it as a **numbered, copy-paste guide** for
someone who is not a server expert:
1. Say **when**: "before pushing", "after the deploy finishes", or "now".
2. Start with how to get in: `ssh <user>@<server-ip>` (Hostinger hPanel → VPS → KVM 2 shows the IP; the
   browser terminal in hPanel also works), then `cd /var/www/heavenandangels`.
3. One command per step, in a code block, in the order to run them, with a one-line "what this does"
   and **what success looks like** ("you should see `active (running)`").
4. For file edits, give the exact line to add (`nano .env`, Ctrl+O, Enter, Ctrl+X) and never put real secrets in it.
5. Finish with a **check** (status, health URL `curl -s https://heavenandangels.jampongcorp.tech/healthz`
   should print `ok`, or a page to open) and **what to do if it fails** (the `journalctl` command,
   and how to undo the step).
6. Mention anything that logs people out or briefly takes the site down (a restart takes a few seconds).

Example:
```
Do this on the server (after the deploy finishes):
1. ssh <user>@<server-ip>
2. cd /var/www/heavenandangels
3. nano .env          -> add the line  NEW_SETTING=value   (save: Ctrl+O, Enter, Ctrl+X)
4. sudo systemctl restart heavenandangels
5. sudo systemctl status heavenandangels      -> should say: active (running)
6. curl -s https://heavenandangels.jampongcorp.tech/healthz      -> should print: ok
If it fails: sudo journalctl -u heavenandangels -n 50 --no-pager, and remove the line from .env.
```

## Rules for the AI
1. Assume every change will reach real data. Prefer additive, re-runnable, reversible changes.
2. Never ask for, print or commit real secrets, passwords, keys or the server's `.env` contents.
3. Do not run anything against the production server yourself; you do not have access. Give the
   user commands to run.
4. Before a risky change, say what to back up first (admin "Email backup now", or the pre-deploy dump).
5. End every reply that changes code with the **"Do this on the server"** block: either the numbered
   steps (in order, with exact commands, and "before deploy" / "after deploy") or "No server action needed."
6. Also keep `CLAUDE.md` (the keep-in-sync checklist) and `.github/workflows/deploy.yml` accurate when deploy steps change.

## Handy commands (Ubuntu server, run by the user)
```
sudo systemctl status heavenandangels
sudo systemctl restart heavenandangels
sudo journalctl -u heavenandangels -n 100 --no-pager
curl -s https://heavenandangels.jampongcorp.tech/healthz            # should print: ok
sudo nginx -t && sudo systemctl reload nginx
```
