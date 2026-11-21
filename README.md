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
  <a href="LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-1F2937"></a>
  <img alt="Languages" src="https://img.shields.io/badge/languages-English%20%7C%20Nederlands-1F2937">
</p>

<p align="center">
  <b>English</b> · <a href="README.nl.md">Nederlands</a>
</p>

---

Mailfilter sits between the internet and your mail servers. Incoming mail is checked for spam, viruses and phishing before it reaches the mailbox; outgoing mail from web servers, applications and mail servers is checked, DKIM-signed and rate limited before it leaves. It replaces Proxmox Mail Gateway with the same core (Postfix, Rspamd, ClamAV) and a web interface that answers the question everybody asks first: *did my mail arrive, and if not, why?*

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Mailfilter dashboard: messages, delivered, spam stopped and problems for the last 24 hours, with a live chart of the last 60 minutes" width="100%">
</p>

## Contents

- [At a glance](#at-a-glance)
- [Manuals](#manuals)
- [Feature tour](#feature-tour)
  - [Dashboard](#dashboard) · [Mail trace](#mail-trace) · [Quarantine](#quarantine) · [Filtering and protection](#filtering-and-protection) · [Lists and spam reports](#allow-and-block-lists-and-spam-reports) · [DMARC reports](#dmarc-reports) · [Link protection](#link-protection)
  - [Domains](#domains) · [Relay access](#relay-access) · [Throttling and hacked senders](#throttling-and-hacked-senders) · [Microsoft 365 and Google Workspace](#microsoft-365-and-google-workspace) · [DNS toolbox](#dns-toolbox)
  - [Mailtrap](#mailtrap) · [Archive](#archive) · [Monthly report and usage](#monthly-report-and-usage) · [Customers, users and security](#customers-users-and-security) · [API](#api)
  - [Notifications and e-mail sending](#notifications-and-e-mail-sending) · [Backups](#backups) · [Updates and channels](#updates-and-channels) · [Cluster](#cluster) · [Branding](#branding) · [Import from Proxmox Mail Gateway](#import-from-proxmox-mail-gateway) · [Every screen, everywhere](#every-screen-everywhere)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Updates, branches and backups](#updates-branches-and-backups)
- [Command line](#command-line)
- [Development](#development)
- [Repository layout](#repository-layout)
- [License](#license)

## At a glance

- **Filters both directions**: spam, viruses, phishing and dangerous attachments in incoming mail; spam checks, DKIM signing and rate limits on outgoing mail from your web servers and mail servers.
- **Answers "where is my mail?"** in plain language: every message, every recipient, the reason and who can fix it.
- **Made for hosting companies**: customer and reseller logins that only see their own domains, monthly reports, usage for invoicing, a REST API for your panel.
- **DNS done for you**: SPF, DKIM (with rotation), DMARC, MTA-STS, DANE and BIMI, published to Cloudflare with one button and undoable.
- **Runs itself**: one-command installer for Debian 13, updates from the web interface with automatic rollback, encrypted backups to SCP, FTPS or FTP, alerts by e-mail, Telegram or webhook.
- **English and Dutch**, light and dark, works on a phone.

## Manuals

The manuals are in English. Every manual also has a Dutch translation.

| Manual | For whom | Nederlands |
| --- | --- | --- |
| [Installation guide](docs/installation.md) | Setting up the server: requirements, installer options, first login, moving a domain over | [Installatiehandleiding](docs/nl/installatie.md) |
| [User manual](docs/user-guide.md) | Customers and everyday use: mail trace, quarantine, lists, spam reports, Mailtrap, archive, reports | [Gebruikershandleiding](docs/nl/gebruikershandleiding.md) |
| [Administrator manual](docs/admin-guide.md) | Every screen and setting, how a message is filtered, server reference | [Beheerdershandleiding](docs/nl/beheerdershandleiding.md) |
| [All documentation](docs/README.md) | Index of manuals and reference pages | [Alle documentatie](docs/nl/README.md) |

Reference pages: [updates and backups](docs/updates-and-backups.md), [relay domains](docs/relay-domains.md), [DKIM key rotation](docs/dkim-rotation.md), [link protection](docs/link-protection.md), [Mailtrap](docs/mailtrap.md), [archive](docs/archive.md), [cluster](docs/cluster.md), [REST API](docs/api.md).

## Feature tour

The screenshots come from a demo server with made-up customers and domains. They are made with the scripts in [`tools/screenshots`](tools/screenshots/README.md), so they can be refreshed after every release.

### Dashboard

Everything that went through the gateway in the last 24 hours or 30 days: messages in and out, delivered, spam stopped and problems, each compared with the period before. A live chart shows the last 60 minutes and a live feed shows every message as it passes. **Things to look at** lists what needs attention: mail stuck for more than an hour, a spike in bounces, a server that suddenly sends far more than usual, a paused sender, services that stopped, or one of Mailfilter's own IP addresses on a blocklist. Top senders, top recipient domains and the mail queue complete the picture.

### Mail trace

Search any message by sender, recipient, message ID, queue ID or server, filter by status, direction and period. A green check means delivered; anything else shows the reason inline.

![Mail trace: a list of messages with their status](docs/screenshots/trace.png)

Open a message to see its **route** (sending server → Mailfilter → destination), what happened per recipient with the receiving server's answer, an explanation **in plain language** and **who can fix it**.

| Delivered, with the spam check | Partly delivered: one mailbox is full |
| --- | --- |
| ![A delivered message with its route and spam check](docs/screenshots/trace-detail.png) | ![A message where one recipient's mailbox was full](docs/screenshots/trace-problem.png) |

The **spam check** explains every decision as a checklist: is the sender genuine (SPF, DKIM, DMARC, look-alike domains), reputation and blocklists, links, attachments and viruses, content and format, with the points each finding added and where the limits for greylisting, spam label, quarantine and refusal are.

![Spam check of a phishing message held in quarantine](docs/screenshots/spam-check.png)

### Quarantine

Spam, viruses and risky attachments wait here for a decision. **Release** delivers the message and teaches the spam filter *this is good mail*; **Block and delete** teaches *this is spam*. **Always allow** and **Always block** put the sender on that recipient's list in one click. The content is shown as plain text, so looking is safe. Only admins can release a virus.

| Waiting messages and why they were held | A message opened |
| --- | --- |
| ![Quarantine list](docs/screenshots/quarantine.png) | ![A quarantined message with release and block buttons](docs/screenshots/quarantine-detail.png) |

The **daily quarantine report** mails every recipient the messages held for them, with a button per message to release it, release it and allow the sender, or block the sender. No login needed; links work for 14 days and only for that message.

![Quarantine report settings per domain](docs/screenshots/quarantine-report.png)

### Filtering and protection

- **Spam filtering** with Rspamd: reputation, Bayes learning, fuzzy hashes, DNS blocklists (Spamhaus and others through a local resolver), greylisting and postscreen at the door.
- **Virus scanning** with ClamAV on incoming *and* outgoing mail.
- **Authentication**: SPF, DKIM and DMARC checks on incoming mail, ARC for forwarded mail, DKIM signing of all outgoing mail per domain.
- **Recipient check**: mail for addresses that do not exist is refused at the door (ask the mail server, LDAP / Active Directory, or a fixed list), so there is no backscatter.
- **Backscatter and loop protection**, and bounce messages with an explanation in English and Dutch.

**Custom rules** add protection you switch on or off for all domains or per domain, each with a plain explanation: newsletters (and newsletters without an unsubscribe link), phishing and CEO fraud (look-alike domains, display-name spoofing of your own people, suspicious links, an *external sender* banner, link protection), dangerous attachments (file types, Office macros, encrypted archives) and ARC for forwarded mail.

![Custom rules with an explanation per rule](docs/screenshots/rules.png)

### Allow and block lists and spam reports

Senders that always get through, or never, per mailbox, per domain or for everyone. Customers manage the lists of their own mailboxes.

![Allow and block lists](docs/screenshots/lists.png)

Users report missed spam or wrongly blocked mail from **Outlook** (the built-in Report button in Microsoft 365), a **phone** or **any mail app** by forwarding it to `spam@` or `notspam@` the Mailfilter host. Mailfilter only learns from reports it can trust: mail it delivered itself, reported from that domain.

![Spam reports with their status](docs/screenshots/spam-reports.png)

### DMARC reports

Mailfilter receives the DMARC aggregate reports of your domains and shows who sends mail as them, how much passes, and the next safe step on the **road to p=reject**. With Cloudflare, each step is one button.

![DMARC reports per domain](docs/screenshots/dmarc.png)

### Link protection

Links in incoming mail are rewritten so they are checked again **at the moment someone clicks**: blocklists, look-alike domains, IP-address links, URL shorteners expanded. Dangerous links show a warning page. Every click is listed with what Mailfilter decided. See [link protection](docs/link-protection.md).

![Link clicks: blocked, warned, continued anyway and safe](docs/screenshots/link-clicks.png)

### Domains

**Transport domains** receive mail through Mailfilter and deliver it to your mail server (with backup servers, TLS policy, its own delivery queue and sending limits). **Relay domains** only send, for web shops and applications, with DKIM, SPF and SMTP accounts.

![Transport domains with their mail server, recipient check and queue](docs/screenshots/domains.png)

| A transport domain opened | Relay domains |
| --- | --- |
| ![Settings of one transport domain](docs/screenshots/domain-detail.png) | ![Relay domains with SMTP accounts and DNS status](docs/screenshots/relay-domains.png) |

### Relay access

One simple screen to say *this server may send mail through Mailfilter*. A server linked to a customer can only send as that customer's own domains, and its mail is still checked for spam and viruses, DKIM-signed and limited. Extra relay ports next to 587 and 465 for old software.

![Relay access: allowed servers and relay ports](docs/screenshots/relay.png)

### Throttling and hacked senders

Limits per server, per SMTP account and per sender domain, a separate delivery queue per domain, and slow-down rules for big receiving providers. **Automatic protection** pauses a server or account that suddenly sends spam or a flood of mail: nothing is lost, nothing is sent, and you release it with one click once it is safe.

![Throttling: default limits and slow-down rules](docs/screenshots/throttling.png)

### Microsoft 365 and Google Workspace

An 8-step wizard puts a Microsoft 365 or Google Workspace domain behind Mailfilter: it writes the PowerShell commands, checks the connectors, changes the DNS and finally locks the tenant so only Mailfilter can deliver. Cloud IP ranges are never trusted blindly: relayed mail must carry the tenant ID or a valid DKIM signature.

![Cloud mail: progress per environment](docs/screenshots/cloud.png)

### DNS toolbox

Check a domain against its own name servers, generate SPF and DMARC, look up MX, check blocklists, and publish records to **Cloudflare** with one button, always shown first and undoable from the change log. **Go live** switches a domain's MX to Mailfilter. **DKIM keys** rotate automatically, **MTA-STS, TLS-RPT and DANE** are hosted and published by Mailfilter, and **BIMI** turns your logo into SVG Tiny PS and checks your VMC/CMC certificate.

| SPF generator: add Mailfilter to an existing record | DKIM keys with automatic rotation |
| --- | --- |
| ![SPF generator](docs/screenshots/dns-spf.png) | ![DKIM keys and their next rotation](docs/screenshots/dns-dkim.png) |

![BIMI: what is still needed before your logo shows in Gmail and Apple Mail](docs/screenshots/dns-bimi.png)

### Mailtrap

Catch all mail from test and staging environments in virtual mailboxes so it is **never sent** to real people. Each message is checked: links to test hosts or without HTTPS, template fields that were not filled in, DKIM, size, alt texts, unsubscribe headers and more. View it at desktop or phone width, read the source, forward it to yourself, or read the mailbox over IMAP. See [Mailtrap](docs/mailtrap.md).

![Mailtrap: a caught order confirmation with its checks](docs/screenshots/mailtrap.png)

### Archive

An unchangeable, searchable copy of all mail per domain for up to 10 years. Every message is hash-chained, every day is sealed with the server's Ed25519 key and locked on disk, and a legal hold stops anything from being removed. Search by words, addresses and attachment names, and export with proof that nothing was changed. See [archive](docs/archive.md).

![Archive: search across archived mail](docs/screenshots/archive.png)

### Monthly report and usage

Every customer gets a **monthly report** by e-mail: mail received, spam and viruses stopped. **Usage** counts domains, active mailboxes, traffic and archive storage per customer, applies your prices and reseller discounts, and exports CSV for your accounting software.

| Monthly report | Usage per customer |
| --- | --- |
| ![Monthly report with 12 months of mail](docs/screenshots/monthly-report.png) | ![Usage and amounts per customer](docs/screenshots/usage.png) |

### Customers, users and security

Admins see everything; **customers** only see mail and settings of their own domains; **resellers** only see the usage of their own customers. Two-factor login (TOTP with recovery codes) can be required for admins or for everyone, and the **audit log** records every change, filterable and exportable.

| Users and roles | Audit log |
| --- | --- |
| ![Users with their role, domains and two-factor status](docs/screenshots/users.png) | ![Audit log of every change](docs/screenshots/audit.png) |

| Security: require two-factor login | My account |
| --- | --- |
| ![Two-factor policy](docs/screenshots/security.png) | ![My account with language and two-factor login](docs/screenshots/account.png) |

### API

A REST API for hosting panels and scripts: add domains, let a web server relay, make SMTP accounts, release quarantined mail, search the mail trace. Keys have rights per area, optional IP restrictions and an expiry date; a customer key only works on that customer's domains. The documentation with examples is built in. See [REST API](docs/api.md).

| API keys | Built-in documentation |
| --- | --- |
| ![API keys with their rights](docs/screenshots/api.png) | ![API documentation with an example call](docs/screenshots/api-docs.png) |

### Notifications and e-mail sending

Alerts by **e-mail**, **Telegram** or **webhook** (JSON, Slack, Mattermost, Discord) when a service stops, the queue fills up, an IP is blocklisted, the certificate is not renewed, a sender is paused, or a backup or update fails. Mailfilter's own mail goes through itself (DKIM-signed) or through any SMTP server, with a test button that explains errors in plain words.

| Notifications | E-mail sending |
| --- | --- |
| ![Notification channels and what to alert on](docs/screenshots/notifications.png) | ![How Mailfilter sends its own mail](docs/screenshots/mail-sending.png) |

### Backups

Full and incremental backups every night, restore from the web interface, and encrypted copies (AES-256) to **SCP/SFTP, FTPS or FTP** with restore straight from that remote location, also onto a freshly installed server.

![Backups: list, schedule and remote copy](docs/screenshots/backups.png)

### Updates and channels

**Info** shows the running version, channel, commit and the versions of all components. **Check for updates** lists what changed; **Update now** makes a backup, installs the new version and rolls back by itself if anything fails. Switch between the **production** (`main`) and **test** (`test`) channels with one click.

![Info: version, update available, update channel](docs/screenshots/info.png)

### Cluster

Keep several Mailfilter servers on the same configuration. Each server stays a complete gateway: when one fails, the others keep filtering, and it catches up when it is back. See [cluster](docs/cluster.md).

![Cluster with two servers](docs/screenshots/cluster.png)

### Branding

Your own product name, accent and dark colour, logo (with a dark-mode version) and browser icon, used in the web app, on the login page and in report mails. The RHIS house style is the default, and "Powered by RHIS" stays visible (see [License](#license)).

![Branding with a live preview](docs/screenshots/branding.png)

### Import from Proxmox Mail Gateway

Upload a PMG backup and take over domains, relay networks, address routes, TLS rules, allow and block lists and DKIM keys. You see everything first and choose what to import.

![Import from Proxmox Mail Gateway](docs/screenshots/import-pmg.png)

### Every screen, everywhere

Search everything with **Ctrl+K**, switch between light and dark, use it on a phone, and give customers the interface in their own language: everything is in English and Dutch.

| Ctrl+K search | Dark theme |
| --- | --- |
| ![Command palette searching for a domain and its messages](docs/screenshots/search.png) | ![Mail trace in the dark theme](docs/screenshots/trace-detail-dark.png) |

| A customer's view, in Dutch | Sign in |
| --- | --- |
| ![Dashboard of a customer login in Dutch](docs/screenshots/dashboard-customer.png) | ![Sign-in page](docs/screenshots/login.png) |

<p align="center">
  <img src="docs/screenshots/dashboard-phone.png" alt="Dashboard on a phone" width="280">
  &nbsp;&nbsp;
  <img src="docs/screenshots/quarantine-phone.png" alt="Quarantine on a phone" width="280">
</p>

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

The screenshots in this README are made from a demo database with fictional `.example` domains; [tools/screenshots/README.md](tools/screenshots/README.md) explains how to make them again after a change to the interface.

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
| `docs/` | Manuals and reference pages (English, Dutch in `docs/nl/`), screenshots in `docs/screenshots/` |
| `tools/screenshots/` | Demo data and scripts that make the README screenshots |
| `huisstijl/` | RHIS house style: tokens, Archivo font, logos |
| `prototype/` | The original clickable interface prototype |

## License

Mailfilter is open source under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0), with [additional terms](NOTICE) under section 7 of that licence:

- **Keep "Powered by RHIS".** The small "Powered by RHIS" mark with the RHIS icon at the bottom right of every page must stay visible in every copy and every modified version. It is not a setting and may not be removed or hidden.
- **Your own brand is fine.** Name, colours, logo and browser icon can be changed under **System › Branding**; the RHIS house style is only the default.
- **Changes stay open.** Whoever runs a modified version for others over the network must offer them its source code (AGPL section 13).
- A modified version must say it is changed, and the RHIS name and logos are not licensed for anything else.
