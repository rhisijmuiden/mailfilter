<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="huisstijl/logo/rhis-logo-wit.svg">
    <img src="huisstijl/logo/rhis-logo.svg" alt="RHIS" height="56">
  </picture>
</p>

<h1 align="center">RHIS Mailfilter</h1>

<p align="center">
  <strong>A modern mail filter gateway and trusted relay for Debian 13.</strong><br>
  Spam and virus filtering in both directions, a mail trace that tells you in plain words what happened to every message, and a clean web interface for you and your customers.
</p>

<p align="center">
  <a href="CHANGELOG.md"><img alt="Version" src="https://img.shields.io/badge/version-1.0.0-E8553D"></a>
  <img alt="Debian 13" src="https://img.shields.io/badge/Debian-13%20trixie-A81D33">
  <img alt="IPv4 and IPv6" src="https://img.shields.io/badge/IPv4%20%2B%20IPv6-yes-1F2937">
  <img alt="Languages" src="https://img.shields.io/badge/languages-English%20%7C%20Nederlands-1F2937">
</p>

<p align="center">
  <b>English</b> · <a href="README.nl.md">Nederlands</a>
</p>

---

Mailfilter sits between the internet and your mail servers. Incoming mail is checked for spam, viruses and phishing before it reaches the mailbox; outgoing mail from web servers, applications and mail servers is checked, DKIM-signed and rate limited before it leaves. It replaces Proxmox Mail Gateway with the same core (Postfix, Rspamd, ClamAV) and a web interface that answers the question everybody asks first: *did my mail arrive, and if not, why?*

## Contents

- [Manuals](#manuals)
- [Features](#features)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Updates, branches and backups](#updates-branches-and-backups)
- [Command line](#command-line)
- [Development](#development)
- [Repository layout](#repository-layout)

## Manuals

| | English | Nederlands |
| --- | --- | --- |
| **Installation** — from bare server to first mail | [Installation guide](docs/installation.md) | [Installatiehandleiding](docs/nl/installatie.md) |
| **User manual** — for customers and everyday use | [User manual](docs/user-guide.md) | [Gebruikershandleiding](docs/nl/gebruikershandleiding.md) |
| **Administrator manual** — every setting explained | [Administrator manual](docs/admin-guide.md) | [Beheerdershandleiding](docs/nl/beheerdershandleiding.md) |
| All documentation | [docs/](docs/README.md) | [docs/nl/](docs/nl/README.md) |

Detailed reference pages: [updates and backups](docs/updates-and-backups.md), [relay domains](docs/relay-domains.md), [DKIM key rotation](docs/dkim-rotation.md), [Mailtrap](docs/mailtrap.md), [archive](docs/archive.md), [cluster](docs/cluster.md), [REST API](docs/api.md).

## Features

### Filtering in both directions

- **Spam filtering** with Rspamd: reputation, Bayes learning, fuzzy hashes, DNS blocklists (Spamhaus and others through a local resolver), greylisting and postscreen at the door.
- **Virus scanning** with ClamAV on incoming *and* outgoing mail.
- **Authentication**: SPF, DKIM and DMARC checks on incoming mail, ARC for forwarded mail, DKIM signing of all outgoing mail per domain.
- **Phishing protection**: look-alike domains (`rh1s.nl`), display-name spoofing of your own people (CEO fraud), suspicious links and an optional *external sender* banner.
- **Link protection** (next release): links in incoming mail are checked again at the moment they are clicked.
- **Attachment policy**: block or quarantine dangerous file types, Office macros and encrypted archives.
- **Newsletter filter**: recognises bulk mail and penalises newsletters without a working unsubscribe link.
- **Custom rules** you switch on or off for all domains or per domain, each with a plain explanation of what it does.
- **Allow and block lists** per mailbox, per domain or for everyone.
- **Recipient check**: mail for addresses that do not exist is refused at the door (ask the mail server, LDAP / Active Directory, or a fixed list), so there is no backscatter.
- **Backscatter and loop protection**, and bounce messages with an explanation in English and Dutch.

### Seeing what happened

- **Mail trace**: search any message by sender, recipient, message ID, queue ID or server. A green check means delivered; otherwise the reason is right there, in plain language, with *who can fix it*.
- **Route picture**: source server → Mailfilter → destination, with spam score and status per hop.
- **Spam check report** per message: a checklist of every rule that fired, grouped and explained.
- **Live dashboard**: mail per hour or day, spam stopped, problems, queue, top senders, and a *things to look at* list (stuck mail, bounce spikes, unusual sending, services down, blocklist listings).

### Quarantine and feedback

- **Quarantine** for spam, viruses and risky attachments. Release or block with one click; the spam filter learns from every choice.
- **Daily quarantine report** by e-mail, with a release button per message (no login needed).
- **Spam reports** from Outlook, a phone or any mail app: forward to `spam@` or `notspam@` and Mailfilter learns from it when it can trust the report.

### Domains and outgoing mail

- **Transport domains** (inbound + outbound, delivered to your mail server, with backup servers) and **relay domains** (outbound only, for web servers and applications).
- **Relay access**: one simple screen to say *this IP may send through Mailfilter*. Customer servers can only send as that customer's own domains.
- **SMTP accounts** for senders without a fixed IP, on ports 587 and 465 plus extra ports you choose.
- **Throttling** per server, per account and per sender domain; slow-down rules for big receiving providers; **a separate delivery queue per domain**.
- **Automatic pause of hacked senders**: a server or account that suddenly sends spam or a flood of mail is put on hold, nothing is lost, and you release it with one click.
- **TLS policy** per destination, outbound DANE.
- **Microsoft 365 and Google Workspace** behind Mailfilter, with a step-by-step wizard that writes the PowerShell commands and DNS records for you.

### DNS made easy

- **DNS toolbox** with Cloudflare integration: check a domain, generate SPF and DMARC, look up MX, check blocklists, publish records with one button and undo them from the change log.
- **Go live** button that switches a domain's MX over to Mailfilter.
- **DKIM keys** with **automatic rotation**.
- **MTA-STS, TLS-RPT and DANE**, served and published by Mailfilter.
- **BIMI**: upload your logo, Mailfilter converts it to SVG Tiny PS, checks your VMC/CMC certificate and publishes the record.
- **DMARC reports**: Mailfilter receives the aggregate reports and shows the road to `p=reject` per domain, with the senders that still fail.
- **Blocklist monitoring** of Mailfilter's own IP addresses with a big dashboard alert.

### For customers and resellers

- **Admin, customer and reseller logins**. Customers only see mail and settings of their own domains.
- **Monthly report** per customer by e-mail: mail received, spam and viruses stopped.
- **Usage per customer** (domains, active mailboxes, traffic, archive storage), prices, reseller discounts and CSV export for invoicing.
- **Mailtrap**: catch all mail from test and staging environments in virtual mailboxes so it is never sent, with a built-in check of every message (links to test hosts, missing template fields, size, DKIM and more). Readable over IMAP too.
- **Mail archive**: an unchangeable, searchable copy of all mail per domain for up to 10 years, hash-chained and sealed every night, with legal hold and export.

### Administration and security

- **Modern interface** in English and Dutch, menu on the left, light and dark theme, works on a phone, **Ctrl+K** search across everything.
- **Two-factor login** (TOTP, recovery codes), optionally required for admins or for everyone.
- **Audit log** of every change, filterable and exportable as CSV.
- **REST API** with scoped API keys, IP restrictions, expiry dates and built-in OpenAPI documentation.
- **Notifications** by e-mail, Telegram or webhook (JSON, Slack, Mattermost, Discord): service down, queue filling, blocklist, certificate, paused sender, backup or update problem.
- **Own SMTP settings** for mail that Mailfilter sends itself.
- **Cluster**: keep several Mailfilter servers on the same configuration; each one keeps filtering on its own if another fails.
- **Import from Proxmox Mail Gateway**: domains, relay networks, address routes, TLS rules, allow and block lists and DKIM keys from a PMG backup file.

### Operations

- **One-command installer** for a bare Debian 13 server, directly on the internet: Let's Encrypt HTTPS, firewall (nftables), CrowdSec, IPv4 and IPv6, nightly security updates.
- **Info button** with the running version, *Check for updates*, *Update now* and a switch between the **production** (`main`) and **test** (`test`) channels. Every update makes a backup first and rolls back by itself when something fails.
- **Backups**: full and incremental, nightly on a schedule, restore from the web interface, and encrypted copies to **SCP/SFTP, FTPS or FTP** with restore from that remote location.

## Quick start

You need a bare Debian 13 server with a public IPv4 (and preferably IPv6) address, port 25 open in both directions, and a DNS name pointing to it.

```bash
# 1. Point A (and AAAA) records for e.g. mailfilter01.example.nl to the server.
# 2. As root on the server:
apt-get install -y git
git clone -b main https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
/opt/mailfilter/install/install.sh
```

The installer asks for the host name, an admin e-mail address and optionally the first domain, and prints the login for the web interface at the end. Open `https://<host name>/` and log in. The [installation guide](docs/installation.md) covers every option, the DNS records you need and how to move a domain over.

Use `-b test` instead of `-b main` for a test server that gets every change first.

## How it works

```
                 ┌──────────────────────────── Mailfilter ────────────────────────────┐
 Internet ──25──▶│ postscreen ▸ Postfix ▸ Rspamd (spam, SPF/DKIM/DMARC, rules) ▸ ClamAV│──▶ your mail server
                 │                         │                                          │
 Web servers ─587/465/extra──▶ relay access / SMTP accounts ▸ same checks ▸ DKIM sign  │──▶ Internet
                 │                         │                                          │
                 │   per-domain queues · quarantine (hold queue) · archive · mailtrap │
                 │   mail trace collector ▸ PostgreSQL ◂ web app (FastAPI + React) ◂──┼── HTTPS 443 (Caddy, Let's Encrypt)
                 └────────────────────────────────────────────────────────────────────┘
```

| Part | Role |
| --- | --- |
| Postfix | SMTP in and out, postscreen, per-domain queues, relay ports, recipient check |
| Rspamd | Spam scoring, SPF, DKIM signing and checks, DMARC, ARC, greylisting, rate limits, custom rules |
| ClamAV | Virus scanning in both directions |
| Redis | Rspamd learning data, greylisting, rate limit counters |
| PostgreSQL | Web app database: mail trace, users, settings, reports, archive index |
| Dovecot | Password checks for SMTP accounts and IMAP for Mailtrap only; no mailboxes |
| unbound | Local DNS resolver for blocklists and DNSSEC |
| Caddy + Let's Encrypt | HTTPS-only web interface, MTA-STS policy hosting; the certificate is also used for SMTP TLS |
| nftables + CrowdSec | Firewall (22, 25, 80, 443, 465, 587 and your extra relay ports) and automatic blocking of attackers |
| `mailfilterctl` | Updates, channel switch, backups, restore (root helper used by the web app) |

## Updates, branches and backups

| Branch | Channel | Purpose |
| --- | --- | --- |
| `main` | Production | Stable releases, see [CHANGELOG.md](CHANGELOG.md) |
| `test` | Test | Every change lands here first and runs on the test server |

Click **Info** in the web interface to see the running version, check for updates, update, or switch channel. Backups (full and incremental, local and remote) are under **System › Backups**. Details: [docs/updates-and-backups.md](docs/updates-and-backups.md).

## Command line

Everything is done from the web interface, but these tools work as root on the server too:

| Command | What it does |
| --- | --- |
| `mailfilterctl info` | Version, channel, commit and component versions |
| `mailfilterctl update check` / `update apply [--branch main\|test]` | Look for and install updates, or switch channel |
| `mailfilterctl backup create --type full\|incremental\|auto` | Make a backup now |
| `mailfilterctl backup list` / `verify ID` | List and check backups |
| `mailfilterctl restore ID [--from-remote] [--only files,database,redis]` | Restore a backup |
| `mailfilterctl remote show\|set\|test\|list\|sync` | Remote backup location |
| `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password EMAIL` | New password for a web login |
| `/var/lib/mailfilter/venv/bin/mailfilter-admin reset-2fa EMAIL` | Turn off two-factor login for a lost phone |
| `/opt/mailfilter/install/install.sh --reconfigure` | Re-apply the whole configuration |

## Development

```bash
# API (needs a PostgreSQL database)
python3 -m venv .venv && .venv/bin/pip install -e "backend[test]"
export MAILFILTER_DB_URL=postgresql+psycopg://mailfilter:dev@127.0.0.1/mailfilter MF_COOKIE_SECURE=0 APP_SECRET=dev
.venv/bin/mailfilter-admin migrate && .venv/bin/mailfilter-admin create-admin you@example.nl
.venv/bin/uvicorn mailfilter.main:app --reload
# Interface, proxies /api to the API on port 8000
cd frontend && npm ci && npm run dev
```

Tests: `cd backend && pytest` (set `MAILFILTER_TEST_DB_URL` to an empty test database). CI also renders and checks the Postfix, Rspamd and Dovecot configuration in a Debian 13 container. Interface texts live in `frontend/src/i18n/en.json` and `nl.json`; CI checks that both have the same keys.

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/mailfilter/` | Web app API (FastAPI, SQLAlchemy, Alembic): routers in `api/`, models in `models/`, migrations in `migrations/versions/` |
| `backend/mailfilter_config/` | Turns the desired configuration into Postfix and Rspamd files, with checks and rollback |
| `backend/mailfilter_dns/` | DNS toolbox and Cloudflare integration (also a CLI: `python3 -m mailfilter_dns`) |
| `backend/mailfilter_tracer/` | Log collector that builds the mail trace |
| `backend/mailfilter_pmg/` | Proxmox Mail Gateway backup reader |
| `frontend/` | Web interface (React, TypeScript, Vite, Tailwind) |
| `install/` | Installer for Debian 13 and the configuration templates |
| `ops/` | `mailfilterctl`: updates, backups, restore |
| `docs/` | Manuals and reference pages (English, Dutch in `docs/nl/`) |
| `huisstijl/` | RHIS house style: tokens, Archivo font, logos |
| `prototype/` | The original clickable interface prototype |

## License

[GNU General Public License v3.0](LICENSE).
