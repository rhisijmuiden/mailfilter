# Changelog

## Unreleased

### Fixes
- Installer on servers without a UTF-8 locale (minimal Debian, Proxmox LXC): `mailfilter-admin migrate` stopped with "cannot use a string pattern on a bytes-like object" because PostgreSQL made the database SQL_ASCII. The database is now always created as UTF-8 (an empty SQL_ASCII database from a failed run is recreated) and the app asks PostgreSQL for UTF-8.
- Installer: Debian 13 ships `unbound-anchor` as its own package; it is now installed.

### Web access
- The installer asks how the web interface is reached (`--web-mode`): directly on the internet (HTTPS with Let's Encrypt, default), behind a firewall or in a DMZ (HTTPS with a self-made or own certificate, no Let's Encrypt request), behind a reverse proxy (plain HTTP only from the proxy, X-Forwarded-* trusted) or plain HTTP (with a clear warning). Optional `--web-from` limits who may open it.
- New page System › Web access to switch mode from the web interface; `install/web-access.sh` does the same on the server (also to get back in when locked out).
- Links in mails (quarantine report, alerts) use the address of the web interface.

### Modules
- New page System › Modules: switch optional modules on or off (usage & billing, monthly report, quarantine report, spam reports, DMARC reports, link protection, mail archive, Mailtrap, cloud mail, REST API, cluster, PMG import). A module that is off leaves the menu, its API answers 403 and its background work stops; modules that change the mail flow re-apply the gateway. Synced in a cluster (Mailtrap stays per server).

### Open source and branding
- Mailfilter is open source under AGPLv3 (was GPLv3) with additional terms (`NOTICE`): the "Powered by RHIS" mark at the bottom right of every page must stay.
- New page System › Branding: own product name, accent and dark colour, logo, dark-mode logo and browser icon. Used in the web app, login page, quarantine report, monthly report and link protection pages; synced in a cluster. The RHIS house style stays the default.
- Info window shows the licence with a link to the source code.

### Documentation
- New README in English and Dutch with every feature.
- Installation guide, user manual and administrator manual in English and Dutch (`docs/`, `docs/nl/`).

## 1.0.0 (2026-11-12)

First official release of RHIS Mailfilter: an inbound and outbound mail filter gateway and trusted relay for Debian 13.

### Mail filtering
- Spam and virus filtering in both directions (Rspamd, ClamAV), greylisting, SPF, DKIM signing, DMARC and ARC.
- Quarantine with release, block and filter training; daily quarantine report; allow and block lists per mailbox or domain.
- Newsletter filter, phishing protection, attachment policy and custom rules.
- Spam reports from Outlook, phones or any mail app.
- Mailtrap for test environments, mail archive per domain, recipient check per domain (mail server, LDAP / Active Directory or a fixed list).
- Microsoft 365 and Google Workspace behind Mailfilter.

### Domains and relay
- Transport and relay domains, relay access per IP with customer servers limited to their own domains, SMTP accounts.
- Throttling per domain, per-domain queues, automatic pause of hacked senders with one-click release.
- Automatic DKIM key rotation, BIMI.

### DNS
- DNS toolbox with Cloudflare: SPF, DKIM, DMARC, MX, MTA-STS, TLS-RPT and DANE; DNS check against the authoritative name servers.
- DMARC report processing with a road-to-p=reject dashboard.

### Interface and administration
- Modern web interface in English and Dutch: dashboard, mail trace, Ctrl+K search, light and dark theme, phone layout.
- Admins and customer-scoped users, two-factor login (TOTP), audit log, REST API with API keys and OpenAPI docs.
- Monthly report per customer, usage per customer for billing.
- Import from Proxmox Mail Gateway.

### Operations
- One-command installer for a bare Debian 13 server (HTTPS with Let's Encrypt, firewall, IPv6).
- Info screen with version, update check and updates from the web interface; switch between the production (main) and test channels.
- Full and incremental backups with restore, copies to SCP, FTPS or FTP (encrypted) and restore from there.
- Own SMTP settings for mail sent by Mailfilter itself.
- Notifications by e-mail, Telegram and webhook.
- Cluster: configuration sync between Mailfilter servers.
