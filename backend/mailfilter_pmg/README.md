# Proxmox Mail Gateway import

Reads a PMG backup and turns it into an import plan for Mailfilter.

## Make the backup in PMG

*Administration → Backup/Restore → Backup now*, then download the
`pmg-backup_*.tgz`. Or on the PMG server: `pmgbackup backup` (file lands in
`/var/lib/pmg/backup/`).

## Try it

```sh
cd backend
python3 -m mailfilter_pmg inspect pmg-backup_2026_11_11.tgz         # readable overview
python3 -m mailfilter_pmg inspect pmg-backup_2026_11_11.tgz --json  # full plan
```

Nothing is changed; it only shows what would be imported.

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
