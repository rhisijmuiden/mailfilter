# Administrator manual

[Nederlands](nl/beheerdershandleiding.md) · [All documentation](README.md)

This manual explains every screen and setting for administrators. Everyday work (mail trace, quarantine, lists) is in the [user manual](user-guide.md); installing the server is in the [installation guide](installation.md).

**Contents**

- [Overview](#overview): [Dashboard](#dashboard) · [Monthly report](#monthly-report) · [Usage](#usage)
- [Mail flow](#mail-flow): [Mail trace](#mail-trace) · [Quarantine](#quarantine) · [Mailtrap](#mailtrap) · [Archive](#archive) · [Allow and block lists](#allow-and-block-lists) · [Spam reports](#spam-reports) · [Quarantine report](#quarantine-report) · [DMARC reports](#dmarc-reports)
- [Configuration](#configuration): [Domains](#domains) · [Relay access](#relay-access) · [Cloud mail](#cloud-mail-microsoft-365-and-google-workspace) · [Custom rules](#custom-rules) · [Throttling and automatic protection](#throttling-and-automatic-protection) · [DNS toolbox](#dns-toolbox)
- [System](#system): [Users](#users) · [Customers and resellers](#customers-and-resellers) · [Security](#security) · [Audit log](#audit-log) · [API](#api) · [E-mail sending](#e-mail-sending) · [Notifications](#notifications) · [Cluster](#cluster) · [Backups](#backups) · [Import from PMG](#import-from-pmg) · [Info and updates](#info-and-updates)
- [How a message is filtered](#how-a-message-is-filtered)
- [Server reference](#server-reference)

---

## Overview

### Dashboard

Admins see all domains: totals for the last 24 hours or 30 days, a chart per hour or day, the mail queue (being sent, waiting to retry, in quarantine), top senders and top recipient domains, and **Things to look at**:

| Alert | What to do |
| --- | --- |
| Mail to a domain waiting for more than an hour | The destination server does not accept mail. Mailfilter keeps trying for 5 days. |
| Many messages not delivered in the last hour | Open the list and read the reasons; often a blocklist or a broken destination. |
| Unusually much mail sent by a server | Possibly a hacked website or mailbox. Check it; pause or block it under Relay access. |
| Senders paused | See [automatic protection](#throttling-and-automatic-protection). |
| Many messages waiting in the queue | Look at the delayed mail in the mail trace. |
| Service not running | Restart it or run the installer again (`install.sh --reconfigure`). |
| Blocklist listing (big red banner) | One of Mailfilter's own IP addresses is on a blocklist. The DNS toolbox *Blacklist check* tab shows which, with a link to the delisting form. |

### Monthly report

**Overview › Monthly report** shows per customer how much mail came in and how much spam and how many viruses were stopped, per month. Under settings:

- **Send the monthly report**: off by default. Choose the **day of the month** (1–28) and the **time**.
- **Send me an overview of all customers**: every admin gets one mail with the totals per customer.
- **Send me an example** shows what customers get. Each customer gets it in their own language and can turn their own copy off.

Finished months are stored, so the report stays available after the 90 days of the mail trace.

### Usage

**Overview › Usage** is the basis for invoicing. Per customer and per domain: domains, **active mailboxes** (unique addresses that really received or sent mail in the month; spam alone does not count), messages in and out, and archive storage (the highest size in the month).

- **Prices** tab: price per domain, per active mailbox, per 1,000 messages in or out, per GB archive, a minimum and a fixed price per customer per month (excl. VAT). Customers can have their own prices; resellers get a discount.
- Finished months are frozen with the prices of that moment. **Recalculate** redoes a month after a correction.
- **Export** as CSV (choose `;` with decimal comma for Dutch Excel, or `,`), ready for your accounting software.

Customer logins see quantities without prices; reseller logins see only their own customers.

---

## Mail flow

### Mail trace

See the [user manual](user-guide.md#4-mail-trace). Admins see every domain and also the sending server's IP and host name, the SMTP account and the raw answer of the receiving server. The trace is built from the Postfix and Rspamd logs by `mailfilter-tracer` and kept for 90 days.

### Quarantine

Quarantine is Postfix's *hold* queue: nothing leaves the server until someone decides. Spam with a score of **8 or more** goes to quarantine, **15 or more** is refused at the door; viruses and blocked attachments go to quarantine. Releasing teaches Rspamd *ham*, blocking teaches *spam*. Only admins can release a message with a virus.

### Mailtrap

A module that catches mail from test environments so it is never sent. It is **off** until an admin switches it on at the top of **Mail flow › Mailtrap**; while off, mail from Mailtrap addresses and logins is refused, never relayed.

Per mailbox: name, customer, which IP addresses or networks are caught (an IP is either in Relay access or in a Mailtrap, never both), optionally a login `trap-<name>` for environments without a fixed IP, *catch everything* or *catch everything except mail to testers*, IMAP access on port 993, retention (default 7 days) and maximum number of messages (default 1000). Only admins can **Send anyway** (audited). Full details: [Mailtrap](mailtrap.md).

### Archive

Switch the archive on per domain on the **Domains** tab of **Mail flow › Archive**:

- **Retention** 1 to 10 years (default 7, the Dutch fiscal period), counted to the end of the month. A longer retention applies to new mail; existing mail keeps at least what it was promised.
- **Legal hold** with a reason: nothing of the domain is removed, not even after the retention.
- **Check integrity** recomputes fingerprints, the hash chain and the signed day seals. The nightly job does this too and raises an alert on problems.

Only mail that was really delivered or sent is archived (including mail released from quarantine). Archived days are locked on disk (`chattr +i`) and sealed with the server's Ed25519 key, whose public key is shown here and included in every export. Every search, view, download and export is audited. Full details: [archive](archive.md).

### Allow and block lists

Entries apply to one mailbox, to everyone at a domain, or to all domains (admins only). Allowed senders get −20 on the spam score (viruses and dangerous attachments are still stopped); blocked senders are refused with *recipient's block list*. Lists imported from PMG are kept separately and still work.

### Spam reports

Users report spam to `spam@<host name>` and good mail to `notspam@<host name>` (see the [user manual](user-guide.md#8-reporting-spam-and-good-mail)). **Mail flow › Spam reports** lists every report with its status, and has these settings:

- **Only trust reports that are proven**: the report must pass DMARC, or carry a DKIM signature or SPF pass of the reporter's domain. Other reports wait for a click on **Learn**.
- **Block the sender for the reporter**: a learned spam report also puts the sender on the block list of that mailbox. **Undo** reverses it.
- **Reports per person per hour**: more are listed but not learned (0 = no limit).

Mailfilter only learns from mail that it delivered itself to the reporter's domain. For Microsoft 365, set the tenant's *user reported settings* to send reports to a mailbox that forwards to `spam@<host name>`; the page shows the steps.

### Quarantine report

**Mail flow › Quarantine report** sends every recipient with held mail one e-mail a day:

- **Send the daily report** (off by default), the **time**, the **language** (one for everyone, recipients have no account) and **Only when there is new mail in quarantine**.
- Per domain you choose who gets the report.
- **Send me an example** first.

The links in the report work for 14 days and only for that message; opening a link changes nothing, only a button does.

### DMARC reports

Mailfilter receives DMARC aggregate reports at `dmarc-reports@<host name>`. Add `rua=mailto:dmarc-reports@<host name>` to a domain's DMARC record (the DNS toolbox does this for you) and publish the one-time authorisation record `*._report._dmarc.<host name>`. Reports are kept for 400 days.

Per domain the page shows who sends as the domain and the **road to p=reject**: collect reports at `p=none` for at least 7 days, fix the real senders that fail, move to `quarantine`, wait 14 days, then `reject`. With Cloudflare, each step is one button.

---

## Configuration

### Domains

**Configuration › Domains** has two kinds:

| | Transport domain | Relay domain |
| --- | --- | --- |
| Incoming mail | Accepted, filtered and delivered to the domain's mail server | Not accepted |
| Outgoing mail | Checked, DKIM-signed, limited | Checked, DKIM-signed, limited |

**Add domain** asks for the kind first. A domain can be converted later. For a **transport domain**:

- **Mail server**: host name or IP, port, *Use MX* (look up the MX of the host name), plus **backup servers** used when the main one is unreachable.
- **Encryption to the mail server**: when available, required, or required and verified.
- **Recipients**: how Mailfilter checks that an address exists before accepting mail:
  - **Ask the mail server** (default): an SMTP check, cached;
  - **LDAP / Active Directory**: server, base DN, bind user and filter; the password is stored encrypted;
  - **Fixed list** of addresses;
  - **Off**: accept everything (not recommended: backscatter).

  **Test** tries an address and warns when the server accepts made-up addresses.
- **Delivery queue for this domain**: parallel connections and a pause between messages for slow servers. Every domain has its own queue, so one slow server never holds up the others.
- **Sending limits for this domain**: messages per hour and per day.
- **Users with access**: the customers who see this domain.
- **Single addresses**: deliver one address (or subdomain) somewhere else.
- **Encryption per destination**: require TLS, DANE or a verified certificate for specific receiving domains (for example banks).

For a **relay domain** you link the relay IPs and **SMTP accounts** that may send for it, set the **sender lock** (*linked servers and accounts may only send as @domain*) and publish DKIM, SPF and DMARC. See [relay domains](relay-domains.md).

Saving a domain creates its DKIM key. Every change is validated (no open relay, no mail loops) and checked with `postfix check` and `rspamadm configtest` before it is applied; on an error nothing changes and you get the reason.

### Relay access

**Configuration › Relay access** lists the servers that may send mail out through Mailfilter.

- **Allow** an IP address or network (IPv4 or IPv6) with a description and, for customer servers, the **customer domain**. A server linked to a customer may only send as that customer's domains. Large networks need an extra confirmation.
- Each entry can be switched off without deleting it.
- **Relay ports**: 587 (STARTTLS) and 465 (TLS) are always on. Add extra ports, for example 2525 for old software, with *STARTTLS when available*, *STARTTLS required* or *TLS from the start*. The firewall opens them automatically.

Relayed mail is still checked for spam and viruses, DKIM-signed and limited.

### Cloud mail (Microsoft 365 and Google Workspace)

**Configuration › Cloud mail** puts a Microsoft 365 or Google Workspace domain behind Mailfilter with an 8-step wizard. Every step shows exactly what to click or the PowerShell to paste, and Mailfilter checks the result.

| Microsoft 365 | Google Workspace |
| --- | --- |
| 1. Domain and tenant (detected from DNS) | 1. Domain |
| 2. Set up Mailfilter (transport domain, delivery to the tenant) | 2. Set up Mailfilter |
| 3. Inbound connector | 3. DKIM in Google |
| 4. Enhanced Filtering (so M365 sees the real sender IP) | 4. Inbound gateway |
| 5. Outbound connector (outgoing mail through Mailfilter) | 5. Outbound gateway |
| 6. Test | 6. Test |
| 7. Change DNS (MX, SPF) | 7. Change DNS (MX, SPF) |
| 8. Lock down (only accept mail from Mailfilter, after 48 hours) | 8. Lock down |

Security: Microsoft's and Google's IP ranges are **not** added to Relay access. Mail relayed from the cloud is only accepted for the linked domains, and only when it carries the tenant ID (M365) or a valid DKIM signature of the domain (Google). The provider signs its own outgoing mail with DKIM.

### Custom rules

**Configuration › Custom rules** has switchable rules in four groups. Each rule explains what it does, shows the name it gets in the mail trace and can be on or off for all domains, with an exception per domain (customers can choose for their own domains). Rules with a score add to the spam score.

| Group | Rules |
| --- | --- |
| **Newsletters** | Mark newsletters, newsletter without unsubscribe link (+4), without one-click unsubscribe, without DKIM, `X-Mailfilter-Newsletter` header, optional subject tag |
| **Phishing and CEO fraud** | Look-alike domains of your own domains, display-name spoofing of your own people (with a list of protected names), suspicious links, *external sender* banner, link protection (next release) |
| **Attachments** | Block or quarantine dangerous file types (editable list), Office macros, encrypted archives |
| **Forwarded mail (ARC)** | Trust ARC seals of known forwarders (Google, Microsoft, Yahoo, iCloud by default) so forwarded mail keeps passing DMARC |

The banner and the subject tag change the message and therefore break the sender's DKIM signature; they are off by default.

**Link protection** (next release, off by default): links in incoming mail are rewritten to go through Mailfilter. When someone clicks, the link is checked again at that moment (blocklists, look-alike domains, URL shorteners expanded); a dangerous link shows a warning page. You choose whether users may continue anyway, which senders and links are skipped and which are always blocked. Clicks are listed on their own page.

### Throttling and automatic protection

**Configuration › Throttling** limits outgoing mail so one hacked server or account cannot send thousands of spam messages.

- **Default limits**: per server per minute and per hour, per sender domain per hour and per day, per SMTP account per hour, and parallel deliveries per domain. Domains can have their own limits.
- **Slow down for receiving domains**: fewer parallel connections and a pause between messages for big providers that block senders who go too fast.

**Automatic protection** checks the outgoing mail of every server and SMTP account every minute and **pauses** a sender that suddenly sends spam or far more than usual. Its mail is held; nothing is lost or sent.

| Setting | Meaning |
| --- | --- |
| Pause senders automatically | Off: nothing is paused automatically (you can still pause by hand) |
| Look back (minutes) | How many minutes of mail are checked each time |
| Spam: at least this many messages / this share (%) | Both must be reached |
| Volume: at least this many messages / times the usual amount | Compared with the average of the last 7 days; small senders are never paused for volume |
| Leave alone after a release (hours) | So a released sender is not paused again straight away |
| Never pause | IP addresses or SMTP accounts |
| Also alert | Extra addresses on top of the notification recipients |

Release a paused sender under **Relay access** once the cause is fixed: **Release and send held mail** or **Release and delete held mail**.

### DNS toolbox

**Configuration › DNS toolbox** has these tabs:

| Tab | What it does |
| --- | --- |
| **Check domain** | Checks MX, SPF, DKIM, DMARC, MTA-STS, TLS-RPT and BIMI of a domain against its own authoritative name servers, with a fix button per item |
| **MX lookup** | MX records of any domain, also of customers that are not on Mailfilter. With Cloudflare you can edit a domain's MX here |
| **SPF generator** | Builds an SPF record; *add Mailfilter to the existing SPF* keeps the current value and adds `include:_spf.<host name>`. Counts DNS lookups (max 10) |
| **DMARC generator** | Builds a DMARC record with reports to Mailfilter |
| **Blacklist check** | Mailfilter's own IPv4 and IPv6 addresses against the big blocklists, every 15 minutes; a listing shows a red banner on the dashboard |
| **DKIM keys** | Every domain's key and selector, and **automatic rotation** |
| **BIMI logo** | Upload a logo; Mailfilter converts it to SVG Tiny PS, checks a VMC/CMC certificate and publishes `default._bimi` |
| **MTA-STS & DANE** | MTA-STS policy (testing or enforce) hosted by Mailfilter, TLS-RPT reports, DANE TLSA record for the server |
| **Cloudflare** | API token (*Zone:Read* + *DNS:Edit*), stored encrypted |
| **Change log** | Every DNS change Mailfilter made, with the old value and **Undo** per batch |

**Publishing**: with Cloudflare every record is published with one button. You always see the plan (what will be added, changed or removed) before it is applied. Without Cloudflare you copy the record to your DNS provider and press **Check again**.

**SPF**: each domain gets `include:_spf.<host name>`. That one shared record lists all Mailfilter IP addresses, so adding a server to a cluster changes only one record.

**Go live** (per domain, once): switches the domain's MX to Mailfilter in Cloudflare. It is journaled and can be rolled back.

**DKIM rotation**: a new key with selector `mfYYYYMM` is published, used after 48 hours, and the old key stays valid for 7 more days before it is removed. Automatic only for live domains in Cloudflare, unless you choose *manual DNS*. See [DKIM key rotation](dkim-rotation.md).

**BIMI**: Gmail and Apple Mail need a paid VMC or CMC certificate; Yahoo and others show the logo with an enforced DMARC policy and the record alone.

---

## System

### Users

**System › Users**: add, edit and disable logins.

- **Role**: *Admin* (everything), *Customer* (only the linked **domains**) or *Reseller* (only usage of its own customers).
- **Language** of the interface and of the mails the user gets.
- **Password**: leave empty to generate one; it is shown once.
- **Reset 2FA** when someone lost their phone and recovery codes.

### Customers and resellers

**System › Customers & resellers** defines who pays: a **customer** is an organisation (name and debtor number) that owns domains; one domain has one payer. A **reseller** sells to its own customers and gets one invoice for all of them, with a discount percentage. Give a reseller a login with role *Reseller*.

### Security

**System › Security › Require two-factor login**: *Optional*, *Required for admins* or *Required for everyone*. Turn it on for yourself first. Users without it are asked to set it up at their next sign-in; the page lists who still has to. Emergency on the server: `mailfilter-admin reset-2fa EMAIL`.

### Modules

**System › Modules** switches optional parts of Mailfilter on or off: Usage & billing, Monthly report, Quarantine report, Spam reports, DMARC reports, Link protection, Mail archive, Mailtrap, Cloud mail, REST API, Cluster and Import from PMG. Spam and virus filtering, quarantine, mail trace, domains, relay access, lists and throttling are always on.

A module that is off disappears from the menu (its page says it is switched off), its API answers `403 module_disabled` and its background work stops: no report mails, no usage measurement, mail to the DMARC and spam report addresses is dropped, API keys are refused. Modules marked *Changes the mail flow* re-apply the gateway: link protection no longer rewrites new mail (links in delivered mail keep working), the archive stops archiving new mail (the existing archive stays sealed and is cleaned up after its retention), cloud mail no longer accepts mail relayed by Microsoft 365 or Google Workspace, and the Mailtrap refuses trapped senders. Switching off something that is in use asks first. Nothing is deleted, so switching a module back on continues where it was. Everything is on by default except the Mailtrap. The switches are copied to the other servers in a cluster, except the Mailtrap (per server); the Cluster module cannot be switched off while the server is in a cluster.

### Branding

**System › Branding** gives this Mailfilter your own look: product name, accent colour (main buttons, active menu item), dark colour (header of report mails), a logo, an optional light logo for dark mode and a browser icon (SVG, PNG, WebP, JPEG or ICO, at most 256 KB each). The preview shows the result before you save. The name and colours are also used in the quarantine report, the monthly report and the link protection pages. **Back to default** returns to the RHIS house style. In a cluster the branding is copied to the other servers.

The small "Powered by RHIS" mark at the bottom right of every page is not a setting: it is a condition of the open-source licence (see `NOTICE`).

### Audit log

Every change in the web interface and API is logged with user, time, domain, IP address and details. Filter by user, action group, domain and period; export as CSV. Actions done with an API key are marked *via API key*.

### API

**System › API**: REST API at `https://<host name>/api/v1/` for hosting panels and scripts.

- Keys (`mfk_…`) act as their owner (admin: all domains; customer: own domains), with per-area rights (domains, relay, SMTP accounts, quarantine, trace, lists, users, audit: none, read or write), optional IP restriction and expiry. Only a hash is stored. Limit: 600 requests per minute.
- The **Documentation** tab renders the OpenAPI description (`/api/v1/openapi.json`) with an example for every call.

See [REST API](api.md).

### E-mail sending

**System › E-mail sending**: how Mailfilter sends its own mail (alerts, reports, quarantine reports):

- **Through this Mailfilter** (default): DKIM-signed like all other mail.
- **Through another SMTP server**: host, port, STARTTLS or TLS, user name and password (stored encrypted).

**Send a test message** tries the settings in the form, also before saving, and explains in plain words what went wrong.

### Notifications

**System › Notifications** sends alerts when something needs attention, also when nobody is looking:

- **E-mail**: to the listed addresses, or all admins.
- **Telegram**: create a bot with @BotFather, send it a message, paste the token and press **Find chat**.
- **Webhook**: JSON for your own system, Slack/Mattermost or Discord, optionally signed (`X-Mailfilter-Signature: sha256=HMAC(secret, timestamp + "." + body)`).

Per alert type you choose the channels and thresholds:

| Alert | When |
| --- | --- |
| Service stopped | Postfix, Rspamd, ClamAV, the web interface or another part has been down for 3 minutes |
| Mail queue filling up | More than *n* messages waiting |
| Blacklist listing | One of the server's addresses is on a blocklist |
| Certificate expiring | The Let's Encrypt certificate was not renewed *n* days before expiry |
| Sender paused | Automatic protection paused a server or account |
| Backup or update problem | A backup, remote copy, restore or update failed, or no successful backup for *n* hours; also archive integrity problems |

General: language of the alerts, **remind every** *n* hours while a problem lasts, **report when solved**. **Send test** per channel, and lists of current problems and recently sent alerts.

### Cluster

**System › Cluster** keeps several Mailfilter servers on the same configuration. Each server stays a complete gateway: if one fails, the others keep filtering, and it catches up when it is back.

1. On the first server: **Create cluster**.
2. Install Mailfilter on the new server.
3. On the first server: **Add a server** and copy the join code.
4. On the new server: **Join an existing cluster** and paste the code.

Then publish the shared SPF record again and give every domain an MX for every server. Domains, relay access, throttling, users (with 2FA), lists, rules, API keys, billing and DKIM keys are synced; queues, quarantine, mail trace, backups, Mailtrap mailboxes and archived mail stay per server. See [cluster](cluster.md).

### Backups

**System › Backups**:

- **Backups** list: every full and incremental backup with size and status; **Full backup now**, **Incremental backup now**, **Check that the backup is complete**, **Restore**, **Delete**.
- **Schedule**: time of the nightly backup, a new full backup every *n* days (default 7), keep *n* full backups with their incrementals (default 4).
- **Remote copy**: **SCP/SFTP** (recommended; Mailfilter shows its public key for `authorized_keys` and pins the server's fingerprint), **FTPS** (explicit TLS 1.2+) or **FTP** (trusted networks only). Files are encrypted with AES-256 before upload. **Store the passphrase in your password manager**: you need it to restore on a new server. After every backup the new part is uploaded; **Copy now** catches up.
- **Restore from the remote location** downloads, checks and restores a backup straight from the remote server, also on a freshly installed server (set up the remote location with the same passphrase first).

A backup holds all settings, DKIM keys, certificates, quarantine, the database and the spam filter's learning data. A restore first makes a safety backup. See [updates and backups](updates-and-backups.md).

### Import from PMG

**System › Import from PMG** takes over a Proxmox Mail Gateway:

1. In PMG make a backup (*Configuration › Backup/Restore*) and download the `.tgz`.
2. Upload it here. Mailfilter shows everything it found per tab: domains, *may relay* networks, address routes, TLS rules, allow and block lists, DKIM keys, PMG rules and settings.
3. Tick what to import and press **Import**.

Custom PMG rules, per-user quarantine lists, LDAP settings, users and custom SpamAssassin rules are not converted; they are shown for a manual check. Moving the domains' MX is done afterwards with **Go live** in the DNS toolbox.

### Info and updates

**Info** (bottom of the left menu) shows the version, channel, commit, server, operating system, uptime, last update and the versions of all components.

- **Check for updates** shows the newest version of both channels and the changes since your version.
- **Update now** makes a backup (recommended, ticked by default), installs the new version, runs the installer with the saved settings and **rolls back** by itself if that fails. Mail waits in the queue meanwhile.
- **Update channel**: switch between **Production** (`main`, stable releases) and **Test** (`test`, newest changes, for the test server).

The top bar shows a green *Production* or blue *Test* label so you always know where you are.

---

## How a message is filtered

**Incoming (port 25)**

1. **postscreen** stops bots and blocklisted IPs before they talk.
2. **Postfix** checks that the domain is ours and the recipient exists (recipient check), and enforces TLS policy.
3. **Rspamd**: greylisting from a score of 4, SPF, DKIM, DMARC, ARC, reputation and blocklists, Bayes, fuzzy hashes, custom rules, allow and block lists. **ClamAV** scans for viruses.
4. Score below 8: delivered (spam headers added from 5). 8–15, virus or blocked attachment: **quarantine**. 15 or more: **refused**.
5. Delivered through the domain's own queue to its mail server, with a copy to the archive if switched on.

**Outgoing (587, 465, extra ports)**

1. Sender allowed by **Relay access**, an **SMTP account** or a **cloud tenant**; sender lock checked.
2. **Rspamd and ClamAV** check the message; **throttling** and **automatic protection** apply.
3. **DKIM-signed** with the domain's key and delivered, with DANE or MTA-STS where the receiver supports it.

## Server reference

| Path | Contents |
| --- | --- |
| `/opt/mailfilter` | The git checkout (branch `main` or `test`) |
| `/etc/mailfilter/` | Settings, lists, secrets (`secrets.env`), backup settings, hooks in `hooks.d/` |
| `/var/lib/mailfilter/` | Web app data, venv, Mailtrap mailboxes, BIMI files, DNS journal |
| `/var/lib/mailfilter-archive/` | The mail archive |
| `/var/lib/rspamd/dkim/` | DKIM keys |
| `/var/backups/mailfilter/` | Local backups |
| `/var/log/mailfilter-install.log` | Log of the last installer run |

| Timer | Does |
| --- | --- |
| `mailfilter-backup` | Nightly backup and remote copy |
| `mailfilter-notify` | Alerts, every 2 minutes |
| `mailfilter-abuse` | Automatic protection, every minute |
| `mailfilter-blacklist` | Blocklist check of own IPs, every 15 minutes |
| `mailfilter-quarantine-report` | Daily quarantine report, hourly check |
| `mailfilter-monthly-report` | Monthly report and usage, hourly check |
| `mailfilter-dkim-rotate` | DKIM rotation, every 6 hours |
| `mailfilter-archive` | Archive seals and maintenance, nightly |
| `mailfilter-cluster` | Cluster sync check, every minute |

Useful commands: see the [README](../README.md#command-line).
