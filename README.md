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
| unattended-upgrades | Nightly Debian security updates |

Configuration that the web app will manage lives in `/etc/mailfilter/`.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Production releases |
| `test` | Every change lands here first and runs on the test server |

The app's Info screen will let you switch between the two and update from the UI.

## What is in this repository

| Path | Contents |
| --- | --- |
| `prototype/index.html` | Clickable interface prototype (open it in a browser). Sample data only |
| `prototype/src/index.template.html` | Prototype source; `python3 prototype/build.py` rebuilds `index.html` with the font inlined |
| `install/` | Installer for Debian 13 and the configuration templates it renders |
| `huisstijl/` | Copy of the RHIS house style (tokens, Archivo font, logos, guide) from `rhisijmuiden/rhis-huisstijl` |

## Planned stack

Postfix (mail server), Rspamd (spam, SPF, DKIM, DMARC, greylisting, rate limits), ClamAV (viruses), Redis, PostgreSQL, a Python FastAPI backend, a React + TypeScript frontend, Caddy with Let's Encrypt, nftables and CrowdSec.
