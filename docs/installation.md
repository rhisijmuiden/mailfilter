# Installation guide

[Nederlands](nl/installatie.md) · [All documentation](README.md)

This guide takes you from a bare Debian 13 server to a working Mailfilter that filters the mail of your first domain.

## 1. What you need

| | |
| --- | --- |
| Server | A fresh **Debian 13 (trixie)** install, 64-bit, nothing else on it. At least 2 CPU cores, 4 GB RAM (ClamAV alone uses about 1.5 GB) and 40 GB disk. More disk if you use the archive. |
| Network | A public IPv4 address, preferably also IPv6, with port 25 reaching the server. The web interface can be directly on the internet, behind a firewall or in a DMZ, or behind a reverse proxy (see *Web interface* below). |
| Port 25 | Open **in and out**. Many cloud providers block outgoing port 25 by default; ask them to open it. |
| Host name | A name such as `mailfilter01.example.nl` with an **A** record (and **AAAA** for IPv6) pointing to the server. |
| Reverse DNS | A **PTR** record for each IP address pointing back to that host name. You set this at your hosting provider. Without it many receivers refuse your mail. |
| Access | Root access over SSH. |

## 2. Install

Log in as root and run:

```bash
apt-get install -y git
git clone -b main https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
/opt/mailfilter/install/install.sh
```

Use `-b test` instead of `-b main` for a test server: it follows the test channel, where every change lands first. You can switch channel later from the web interface.

The installer asks four things:

1. **Host name** of this server, for example `mailfilter01.example.nl`.
2. **Admin e-mail address**: your login for the web interface; alerts and Let's Encrypt notices go there.
3. **First domain** to filter (optional, you can add domains later in the web interface) and the mail server that receives its clean mail.
4. **How the web interface is reached** (see below).

It then installs and configures everything, which takes about 10 to 15 minutes. You can follow along on screen; the full log is in `/var/log/mailfilter-install.log`.

### Options

Everything can be given on the command line, for example to install without questions:

```bash
/opt/mailfilter/install/install.sh --non-interactive \
  --hostname mailfilter01.example.nl --admin-email admin@example.nl \
  --domain example.nl --destination mail.example.nl \
  --relay-net 203.0.113.25 --relay-net 2001:db8:42::/64 \
  --relay-port 2525 --ssh-from 198.51.100.7
```

| Option | Meaning |
| --- | --- |
| `--hostname NAME` | Full host name of this server |
| `--admin-email ADDRESS` | Admin login and address for alerts and certificate notices |
| `--domain DOMAIN` | First domain to filter (optional) |
| `--destination HOST` | Mail server that receives clean mail for `--domain` |
| `--relay-net CIDR` | Server or network that may send mail out through Mailfilter (IPv4 or IPv6). Repeatable |
| `--relay-port PORT` | Extra relay port next to 587 and 465, for example 2525. Repeatable |
| `--ssh-from CIDR` | Only allow SSH from this address or network. Repeatable. Without it SSH stays open (CrowdSec still guards it) |
| `--web-mode MODE` | How the web interface is reached: `public`, `internal`, `proxy` or `http` (see below) |
| `--trusted-proxy CIDR` | Address of the reverse proxy (`proxy`). Repeatable |
| `--web-url URL` | Address users open on the reverse proxy (`proxy`), default `https://<host name>` |
| `--web-port PORT` | HTTP port for `proxy` (default 8080) or `http` (default 80) |
| `--web-from CIDR` | Only allow the web interface from here (`internal`, `http`). Repeatable |
| `--web-cert FILE`, `--web-key FILE` | Own certificate and key (PEM) for `internal` instead of the self-made one |
| `--skip-certificate` | Do not request a Let's Encrypt certificate yet (DNS not ready) |
| `--non-interactive` | Never ask; stop when a required value is missing |
| `--reconfigure` | Re-apply everything with the settings of the previous install, without questions |

Running the installer again is safe: it re-applies the configuration and keeps keys, passwords, lists and the database.

### Web interface: on the internet, in a DMZ or behind a proxy

| Mode | When | What happens |
| --- | --- | --- |
| `public` (default) | The server is directly on the internet | HTTPS with a Let's Encrypt certificate on ports 80 and 443, renewed automatically. Needed for MTA-STS and link protection. |
| `internal` | Behind a firewall or in a DMZ | HTTPS with a certificate Caddy makes itself (or your own with `--web-cert`/`--web-key`). **No Let's Encrypt request.** Browsers warn until you trust it. Optionally only from `--web-from`. |
| `proxy` | Behind nginx, HAProxy, Traefik or a similar reverse proxy | Plain HTTP on `--web-port` (8080), only reachable from `--trusted-proxy`. The proxy does HTTPS; its `X-Forwarded-For` and `X-Forwarded-Proto` are trusted so the audit log shows the real visitor. |
| `http` | A closed management network | Plain HTTP without encryption. The installer warns; use `--web-from` to limit who can connect. |

```bash
# In a DMZ, web interface only from the office network:
install.sh --web-mode internal --web-from 192.168.10.0/24
# Behind a reverse proxy at 10.0.0.5 that serves https://mail.example.nl:
install.sh --web-mode proxy --trusted-proxy 10.0.0.5 --web-url https://mail.example.nl
```

Mail filtering is the same in every mode; port 25 must still reach the server. You can switch later under **System › Web access**. Locked out? On the server: `sudo /opt/mailfilter/install/web-access.sh --mode public` (or another mode).

### What gets installed

| Part | What it does |
| --- | --- |
| Postfix | Inbound on port 25 with postscreen, relay on 587 (STARTTLS), 465 (TLS) and your extra ports, a delivery queue per domain, IPv4 and IPv6 |
| Rspamd | Spam score, SPF, DKIM signing, DMARC, ARC, greylisting, blocklists, Bayes learning, throttling, allow and block lists |
| ClamAV | Virus scanning in both directions |
| Dovecot | Password checks for SMTP accounts, IMAP for the Mailtrap; no mailboxes |
| unbound | Local DNS resolver for blocklists and DNSSEC |
| Caddy + Let's Encrypt | The web interface (HTTPS by default); the Let's Encrypt certificate is also used for SMTP TLS and renews automatically |
| nftables + CrowdSec | Firewall and automatic blocking of attackers |
| PostgreSQL, Redis | Web app database, spam filter storage |
| Mailfilter web app | The web interface and API, the mail trace collector and the timers for backups, reports, alerts and more |
| unattended-upgrades | Nightly Debian security updates |

### Firewall

Open from the internet: **25** (mail in), the web interface (**80** and **443** in `public` and `internal` mode, or the `--web-port` from the proxy or `--web-from` networks), **465** and **587** (relay), your extra relay ports, **22** (SSH, or only from `--ssh-from`), and **993** only while a Mailtrap mailbox allows IMAP. Everything else is closed.

## 3. First login

At the end the installer prints:

- the **DNS records** to add (A, AAAA, PTR, SPF and DKIM for your first domain, and the MX for later);
- the **web interface login**: your admin e-mail address and a generated password. The password is also in `/etc/mailfilter/initial-admin-password`.

Open `https://<host name>/` and log in. Then straight away:

1. **My account** (click your initials at the top right): change the password and choose your language (English or Nederlands). Delete `/etc/mailfilter/initial-admin-password` on the server.
2. **My account › Two-factor login**: set it up with an authenticator app and store the recovery codes.
3. Click **Info** (at the bottom of the left menu) to check the version and that the channel is the one you want.

Lost the password? On the server: `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password you@example.nl` prints a new one.

## 4. Set up the basics

Work through these in the web interface. The [administrator manual](admin-guide.md) explains each screen.

1. **System › E-mail sending**: how Mailfilter sends its own mail (alerts, reports). The default, *through this Mailfilter*, is fine for most setups. Press **Send a test message**.
2. **System › Notifications**: choose where alerts go (e-mail, Telegram or webhook).
3. **System › Backups**: check the nightly schedule and set up a **remote copy** (SCP recommended). Store the encryption passphrase in your password manager.
4. **DNS toolbox › Cloudflare**: if your domains are in Cloudflare, enter an API token with *Zone:Read* and *DNS:Edit*. Mailfilter can then publish all records for you.
5. **Configuration › Domains**: add your domains (or **System › Import from PMG** when you come from Proxmox Mail Gateway).
6. **Configuration › Relay access**: add the web servers and mail servers that may send mail out through Mailfilter.

## 5. Move a domain over

For each domain:

1. **Add it** under Configuration › Domains as a *transport domain* with its mail server, or as a *relay domain* when it only sends.
2. **Check the DNS** in the DNS toolbox (*Check domain*). Publish SPF (Mailfilter adds `include:_spf.<host name>` to the existing record), DKIM and, if you like, DMARC with reports to Mailfilter.
3. **Let your mail server send through Mailfilter** (add its IP under Relay access and set Mailfilter as its smarthost), and check in the mail trace that outgoing mail is delivered and DKIM-signed.
4. **Make sure your mail server accepts mail from Mailfilter** and, once you are live, only from Mailfilter.
5. **Go live**: in the DNS toolbox press **Go live** for the domain (Cloudflare), or change the MX record at your DNS provider to the Mailfilter host name. Incoming mail now flows through Mailfilter. Follow it in the mail trace.

For Microsoft 365 or Google Workspace use **Configuration › Cloud mail** instead: the wizard walks you through connectors, DNS and lockdown.

## 6. Updating

Click **Info › Check for updates › Update now**. Mailfilter makes a backup, installs the new version, re-applies the configuration and rolls back by itself if that fails. On the command line: `mailfilterctl update apply`. See [updates and backups](updates-and-backups.md).

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Let's Encrypt failed | The A/AAAA record does not point to the server yet, or port 80 is blocked. Fix DNS and run `install.sh --reconfigure`. Use `--skip-certificate` to install first. |
| Outgoing mail stays in the queue | Port 25 outbound is blocked by the provider. The mail trace shows "connection timed out". Ask the provider to open it. |
| Mail is refused by Gmail or Microsoft | Check the PTR record, SPF and DKIM in the DNS toolbox (*Check domain*), and *Blacklist check* for your IP. |
| A service is not running | The dashboard shows which. Run `install.sh --reconfigure`; the log in `/var/log/mailfilter-install.log` tells why it failed. |
| Locked out by two-factor login | `mailfilter-admin reset-2fa you@example.nl` on the server. |
