# README screenshots

The screenshots in [`docs/screenshots/`](../../docs/screenshots) are made from a demo
database with made-up customers and `.example` domains. Make them again after a
change to the interface, so the README and manuals stay true.

Nothing here touches a real server: the root helpers (`mailfilterctl`, the
queue helper and `mailfilter-config`) are replaced by the fakes in
[`fakes/`](fakes), which return fixed answers.

## What you need

- PostgreSQL with an **empty** database (the seed adds rows and never cleans up).
  `env.sh` expects `postgresql://mailfilter:dev@127.0.0.1:5433/mailfilter`; set
  `MAILFILTER_DB_URL` before sourcing it to use another one.
- Python 3.11+ with the backend installed: `python3 -m venv .venv && .venv/bin/pip install -e backend`
- Node 20+ and Playwright with Chromium (`npm i -g playwright`, `npx playwright install chromium`)

## Steps

Run everything from the root of the repository.

```bash
# 1. Demo environment (writes its files to $DEMO_DIR, default /var/tmp/mfdemo)
. tools/screenshots/env.sh

# 2. Database and demo data
.venv/bin/mailfilter-admin migrate
.venv/bin/mailfilter-admin create-admin admin@example.net
.venv/bin/python tools/screenshots/seed.py
# The seed turns that admin into reinder@rhis.example with password
# demo-password-123 (set DEMO_PASSWORD for another one).

# 3. API and built interface (the preview proxies /api to port 8000)
.venv/bin/uvicorn mailfilter.main:app --port 8000 &
(cd frontend && npm ci && npm run build && npx vite preview --port 4173) &

# 4. Screenshots, then shrink them (256 colours, no visible difference)
NODE_PATH=$(npm root -g) node tools/screenshots/shoot.mjs http://127.0.0.1:4173 docs/screenshots
python3 tools/screenshots/optimize.py docs/screenshots
```

`shoot.mjs` takes an optional third argument to make only some screenshots,
for example `trace,quarantine`. The list of screenshots, the page each one
shows and the clicks needed to get there are in `SHOTS` at the top of the
script. Desktop shots are 1440×900; the phone shots are 390×844 at double
resolution and in Dutch, and the customer shots log in as a customer.

## Adding a screenshot

1. Add data for the new screen to `seed.py` if the screen would be empty.
2. Add an entry to `SHOTS` in `shoot.mjs` with a name, the page and, if needed, an
   action (a click or a wait).
3. Run step 4 above with only that name, check the picture, and refer to it from
   `README.md` and `README.nl.md`.
