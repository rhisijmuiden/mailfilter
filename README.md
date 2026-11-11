# RHIS Mailfilter

A modern inbound and outbound mail filter gateway, the successor to Proxmox Mail Gateway. It installs directly on a bare Debian 13 server and filters spam and viruses in both directions, signs outbound mail with DKIM, throttles per domain and keeps a separate queue per domain. A clear web interface shows for every message whether it was delivered and, if not, why.

The interface is in English and fully translated to Dutch.

## Status

- Phase 1 (done): interactive interface prototype and repository setup.
- Phase 2 (in progress): installer that turns a bare Debian 13 server into a working gateway. The web app follows in phase 3; until then the server shows the prototype.

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
| mailfilterctl + timer | Updates from the web interface and a nightly local backup |
| unattended-upgrades | Nightly Debian security updates |

Configuration that the web app will manage lives in `/etc/mailfilter/`.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Production releases |
| `test` | Every change lands here first and runs on the test server |

The app's Info screen shows the running version and lets you check for updates, update, and switch between production and test.

## Updates and backups

Everything below is done from the web interface; the same commands work on the server as root with `mailfilterctl` (add `--json` for machine-readable output). See [docs/updates-and-backups.md](docs/updates-and-backups.md) ([Nederlands](docs/nl/updates-en-backups.md)).

| Command | What it does |
| --- | --- |
| `mailfilterctl info` | Version, branch, commit and versions of Postfix, Rspamd, ClamAV and the rest |
| `mailfilterctl update check` | Looks on GitHub for a newer version of the current branch and of the other one |
| `mailfilterctl update apply [--branch main\|test]` | Backs up, installs the newest version (or switches branch), re-applies the configuration; rolls back when that fails |
| `mailfilterctl backup create --type full\|incremental\|auto` | Local backup of configuration, DKIM keys, certificates, quarantine, database and spam filter learning data |
| `mailfilterctl backup list` / `verify ID` / `delete CHAIN` | Manage backups |
| `mailfilterctl backup config time=03:00 full_every_days=7 keep_full=4` | Schedule and retention |
| `mailfilterctl restore ID [--only files,database,redis]` | Restore, after making a safety backup of the current state |

A timer makes a backup every night: a full backup once a week, incremental backups in between. Backups are stored in `/var/backups/mailfilter`. Copying them to FTP, FTPS or SCP comes in a later version.

## What is in this repository

| Path | Contents |
| --- | --- |
| `prototype/index.html` | Clickable interface prototype (open it in a browser). Sample data only |
| `prototype/src/index.template.html` | Prototype source; `python3 prototype/build.py` rebuilds `index.html` with the font inlined |
| `install/` | Installer for Debian 13 and the configuration templates it renders |
| `ops/` | `mailfilterctl`: updates, branch switch, backup and restore (runs as root, called by the web app through sudo) |
| `huisstijl/` | Copy of the RHIS house style (tokens, Archivo font, logos, guide) from `rhisijmuiden/rhis-huisstijl` |

## Planned stack

Postfix (mail server), Rspamd (spam, SPF, DKIM, DMARC, greylisting, rate limits), ClamAV (viruses), Redis, PostgreSQL, a Python FastAPI backend, a React + TypeScript frontend, Caddy with Let's Encrypt, nftables and CrowdSec.
