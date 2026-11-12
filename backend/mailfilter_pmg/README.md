# Proxmox Mail Gateway import

Reads a PMG backup and turns it into an import plan for Mailfilter.

## Make the backup in PMG

*Administration → Backup/Restore → Backup now*, then download the
`pmg-backup_*.tgz`. Or on the PMG server: `pmgbackup backup` (file lands in
`/var/lib/pmg/backup/`).

## Import

In the web interface: **System → Import from PMG**. Upload the backup, check
the preview (new, already there, not possible, problems), tick what you want
and import. Items that already exist are not ticked, so nothing is
overwritten unless you choose to.

On the server:

```sh
B=/opt/mailfilter/backend/bin/mailfilter-pmg-import
$B inspect pmg-backup.tgz            # what is in the backup
$B preview pmg-backup.tgz            # what would change on this server
sudo $B apply pmg-backup.tgz         # allow/block lists and DKIM keys
# Domains, routes, relay networks and TLS: import them in the web interface; it keeps
# them in its database and would overwrite a gateway.json written with --gateway-json.
```

## How applying works

| Part | Written to | By |
|---|---|---|
| Domains, address routes, relay networks, TLS policies | the web app's gateway tables, then applied like a save on Domains / Relay access (validated, written by the root helper, rolled back if a check fails) | the web app |
| Allow and block lists | `/etc/mailfilter/rspamd/{allow,block}_*.map` (Rspamd reloads maps itself) | the root job |
| DKIM | `/var/lib/rspamd/dkim/<domain>.<selector>.key` + `dkim_selectors.map` | the root job |

For the files only root may write, the web app (sandboxed, `/etc` read-only) queues the upload in
`/var/lib/mailfilter/pmg-import` and runs `sudo systemctl start
mailfilter-pmg-import.service` (the only sudo rule it gets for this). The job
treats that directory as untrusted: fixed file names only, no symlinks
followed, results written with an atomic rename.

## What is imported

| PMG | Mailfilter |
| --- | --- |
| Relay Domains (`/etc/pmg/domains`) | Domains |
| Transports + default relay (`transport`, `pmg.conf` mail.relay) | Delivery server per domain |
| Trusted Networks (`mynetworks`) | "May relay" IPs/networks |
| Welcomelist / Blocklist object groups | Global welcome/blocklist |
| SMTP welcomelist (greylist exclusions) | Welcomelist, applied before filtering |
| Quarantine user welcome/blocklists | Per-user lists |
| DKIM selector, key and signing domains | DKIM |
| TLS policy, inbound TLS required | TLS settings |
| Spam, greylisting, SPF, DNSBL, size and rate limits | Settings |

Not imported, with a warning in the preview: PMG user accounts, LDAP,
fetchmail, custom SpamAssassin rules, and custom filter rules (listed with
their conditions and actions so you can rebuild them).

The archive is read in memory only, with size limits; nothing is extracted to disk.
