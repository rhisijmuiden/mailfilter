# RHIS Mailfilter

A modern inbound and outbound mail filter gateway, the successor to Proxmox Mail Gateway. It installs directly on a bare Debian 13 server and filters spam and viruses in both directions, signs outbound mail with DKIM, throttles per domain and keeps a separate queue per domain. A clear web interface shows for every message whether it was delivered and, if not, why.

The interface is in English and fully translated to Dutch.

## Status

Phase 1 of the roadmap: an interactive interface prototype and the repository setup. The gateway itself is not built yet.

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
| `huisstijl/` | Copy of the RHIS house style (tokens, Archivo font, logos, guide) from `rhisijmuiden/rhis-huisstijl` |

## Planned stack

Postfix (mail server), Rspamd (spam, SPF, DKIM, DMARC, greylisting, rate limits), ClamAV (viruses), Redis, PostgreSQL, a Python FastAPI backend, a React + TypeScript frontend, Caddy with Let's Encrypt, nftables and CrowdSec.
