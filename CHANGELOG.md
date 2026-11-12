# Changelog

## Unreleased

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
