# mailfilter_dns

The DNS toolbox behind the Domains and DNS toolbox screens. Pure Python logic; the web app's API routes and the `mailfilter-dns` command line both use it.

| Module | What it does |
| --- | --- |
| `cloudflare.py` | Cloudflare API v4 client: verify token, find the zone for a name, list/create/update/delete records |
| `spf.py` | Parse, generate and merge SPF ("add Mailfilter to the existing record"), count DNS lookups (max 10) |
| `dmarc.py` | Parse, generate and judge DMARC |
| `dkim.py` | The key Mailfilter signs with (from Rspamd), and whether DNS matches it |
| `publish.py` | Plans for SPF, DKIM, DMARC and the shared `_spf.<host>` record; `apply` writes and journals the old value, `undo_batch` puts it back |
| `golive.py` | One-time go-live per domain: pre-flight checks, MX plan, apply, rollback |
| `lookup.py` | MX lookup for any domain: addresses, PTR, port 25, STARTTLS, certificate, SPF and DMARC |
| `domaincheck.py` | "Check domain": MX, SPF, DKIM, DMARC, PTR, MTA-STS, TLS-RPT with suggested fixes |
| `blacklist.py` | Own IPv4/IPv6 against DNS blocklists; writes `/var/lib/mailfilter/blacklist.json` |

Every change to Cloudflare goes through a plan (now / new) first, and is appended to `/var/lib/mailfilter/dns-journal.jsonl` with the old value.

The Cloudflare token needs **Zone: Read** and **DNS: Edit**. The CLI reads it from `CLOUDFLARE_API_TOKEN` (environment or `/etc/mailfilter/secrets.env`).

## Command line

```bash
cd /opt/mailfilter/backend
python3 -m mailfilter_dns check rhis.nl            # is the domain ready for Mailfilter?
python3 -m mailfilter_dns mx bouwbedrijf-hoorn.nl  # MX lookup for any domain
python3 -m mailfilter_dns spf-gen --include mailfilter --include m365 --all -
python3 -m mailfilter_dns dmarc-gen --policy quarantine --rua dmarc@rhis.nl
python3 -m mailfilter_dns blacklist                # also runs every 15 minutes (systemd timer)
python3 -m mailfilter_dns cf-verify
python3 -m mailfilter_dns publish spf rhis.nl      # shows the plan; add --apply to write
python3 -m mailfilter_dns publish server-spf --apply
python3 -m mailfilter_dns golive preflight rhis.nl
python3 -m mailfilter_dns golive plan rhis.nl
python3 -m mailfilter_dns golive apply rhis.nl
python3 -m mailfilter_dns golive rollback rhis.nl
```

Add `--json` for machine-readable output.

## Tests

```bash
cd backend && python3 -m pytest tests/dns
```
