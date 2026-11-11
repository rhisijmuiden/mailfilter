# RHIS Mailfilter

A modern inbound and outbound mail filter gateway, the successor to Proxmox Mail Gateway. It installs directly on a bare Debian 13 server and filters spam and viruses in both directions, signs outbound mail with DKIM, throttles per domain and keeps a separate queue per domain. A clear web interface shows for every message whether it was delivered and, if not, why.

The interface is in English and fully translated to Dutch.

## Status

- Phase 1 (done): interactive interface prototype and repository setup.
- Phase 2 (in progress): installer that turns a bare Debian 13 server into a working gateway.
- Phase 3 (in progress): the web app. Login with admin and customer roles, users, English and Dutch, left menu in the house style. Mail trace and quarantine follow.

## Install on a bare Debian 13 server

Point an A record (and AAAA for IPv6) for the host name, for example `mailfilter01.rhis.nl`, to the server first, so Let's Encrypt can issue a certificate. Then, as root:

```bash
apt-get install -y git
git clone -b test https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
/opt/mailfilter/install/install.sh
```

The installer asks for the host name, an admin email address and optionally the first domain. Everything can also be passed as options, see `install.sh --help`. Examples:

```bash
install.sh --hostname mailfilter01.rhis.nl --admin-email reinder@rhis.nl \
  --domain rhis.nl --destination mail.rhis.nl \
  --relay-net 203.0.113.25 --relay-net 2a02:58:1f:42::/64 \
  --relay-port 2525 --ssh-from 198.51.100.7
```

It is safe to run again; it re-applies the configuration and keeps keys, passwords and lists. The full log is in `/var/log/mailfilter-install.log`.

What it sets up:

| Part | What it does |
| --- | --- |
| Postfix | Inbound on 25 with postscreen, relay on 587 (STARTTLS), 465 (TLS) and optional extra ports. Recipient verification, per-destination queues, IPv4 and IPv6 |
| Rspamd | Spam score, SPF, DKIM signing per domain, DMARC, ARC, greylisting, blocklists, Bayes learning, outbound throttling, allow and block lists. Quarantine uses Postfix's hold queue |
| ClamAV | Virus scan in both directions; viruses and risky attachments go to quarantine |
| Dovecot | Only checks passwords for SMTP relay accounts; no mailboxes |
| unbound | Local DNS resolver, needed for blocklists such as Spamhaus |
| Let's Encrypt + Caddy | HTTPS-only web interface, certificate also used for SMTP TLS, automatic renewal |
| nftables + CrowdSec | Firewall (only 22, 25, 80, 443, 465, 587 and extra relay ports) and automatic blocking of attackers |
| PostgreSQL, Redis | Database for the web app, storage for Rspamd |
| unattended-upgrades | Nightly Debian security updates |

Configuration that the web app will manage lives in `/etc/mailfilter/`.

At the end the installer prints the web interface login: the admin e-mail address and a generated password (also stored in `/etc/mailfilter/initial-admin-password`). Change it under My account after logging in. Lost the password? `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password you@example.nl` prints a new one.

## Develop the web app

```bash
# API (needs a PostgreSQL database)
python3 -m venv .venv && .venv/bin/pip install -e "backend[test]"
export MAILFILTER_DB_URL=postgresql+psycopg://mailfilter:dev@127.0.0.1/mailfilter MF_COOKIE_SECURE=0 APP_SECRET=dev
.venv/bin/mailfilter-admin migrate && .venv/bin/mailfilter-admin create-admin you@example.nl
.venv/bin/uvicorn mailfilter.main:app --reload
# Interface, proxies /api to the API on port 8000
cd frontend && npm ci && npm run dev
```

Tests: `cd backend && pytest` (set `MAILFILTER_TEST_DB_URL` to an empty test database).

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Production releases |
| `test` | Every change lands here first and runs on the test server |

The app's Info screen will let you switch between the two and update from the UI.

## What is in this repository

| Path | Contents |
| --- | --- |
| `backend/mailfilter/` | Web app API (FastAPI, SQLAlchemy, Alembic). Routers in `api/`, models in `models/`, migrations in `migrations/versions/` |
| `frontend/` | Web interface (React, TypeScript, Vite, Tailwind). Pages in `src/pages/`, menu in `src/nav.ts`, routes in `src/routes.tsx`, texts in `src/i18n/en.json` and `nl.json` |
| `prototype/index.html` | Clickable interface prototype (open it in a browser, or `/prototype/` on the server). Sample data only |
| `prototype/src/index.template.html` | Prototype source; `python3 prototype/build.py` rebuilds `index.html` with the font inlined |
| `install/` | Installer for Debian 13 and the configuration templates it renders |
| `huisstijl/` | Copy of the RHIS house style (tokens, Archivo font, logos, guide) from `rhisijmuiden/rhis-huisstijl` |

## Planned stack

Postfix (mail server), Rspamd (spam, SPF, DKIM, DMARC, greylisting, rate limits), ClamAV (viruses), Redis, PostgreSQL, a Python FastAPI backend, a React + TypeScript frontend, Caddy with Let's Encrypt, nftables and CrowdSec.
