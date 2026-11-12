# mailfilter_config

Turns the gateway settings from the web interface (domains, relay access,
extra relay ports, throttling) into Postfix, Rspamd and nftables
configuration, checks it, and applies it. Standard library only, so it runs
as root from `backend/bin/mailfilter-config` without the web app's virtualenv.

## What it manages

| Setting | Written to |
|---|---|
| Domains and where clean mail goes | `/etc/mailfilter/postfix/relay_domains`, `transport` |
| One queue per domain (own concurrency and delay) | `mfq_*` services in the managed block of `master.cf`, `mfq_*` parameters in `main.cf` |
| Throttled outbound queues (e.g. gmail.com) | `mfo_*` services and parameters |
| Single addresses routed elsewhere (SMTP or LMTP) | `transport` |
| TLS per destination | `/etc/mailfilter/postfix/tls_policy` |
| Recipient check per domain (no backscatter): ask the mail server (cached), LDAP / Active Directory, a fixed list or off | `recipient_verify`, `relay_recipients`, `postfix/ldap/<domain>.cf` (root:postfix 0640), `relay_recipient_maps` and the `address_verify_*` cache times in `main.cf` |
| Relay access (IPv4 and IPv6) | `relay_networks.list`, `postfix/relay_networks.cidr`, `rspamd/local_addrs.map` |
| Extra relay ports (syslog name `postfix/port-NNNN`) | managed block of `master.cf`, nft set `relay_ports` |
| Outbound limits per relay IP, SMTP account and sender domain | `/etc/rspamd/local.d/ratelimit.conf` |

The desired state is kept in `/etc/mailfilter/gateway.json`.

## Safety

* **Validation first**, with messages in English and Dutch and stable codes
  for the web interface: open relay (`0.0.0.0/0`) is refused, networks bigger
  than /16 (IPv4) or /32 (IPv6) need `allow_broad`, a destination that is the
  Mailfilter itself or the domain's own MX (a loop after go-live) is refused,
  reserved ports cannot be used for listeners.
* **Check, then reload**: after writing it runs `postmap`, `postfix check`,
  `postconf -n` (no new warnings) and `rspamadm configtest`. If anything
  fails, every file and parameter is restored and nothing is reloaded.
* One change at a time (file lock), atomic file writes that keep owner and mode.

## Use

```sh
mailfilter-config check            # validate /etc/mailfilter/gateway.json
mailfilter-config apply [--json]   # write, check, reload
mailfilter-config render --out DIR # dry run: show what would be written
mailfilter-config seed --relay-net 203.0.113.25 --relay-port 2525 \
    --domain example.nl --destination mail.example.nl   # used by the installer
```

From Python (the web app):

```python
from mailfilter_config import Applier, GatewayConfig, Domain, Destination
report = Applier().apply(GatewayConfig(domains=[Domain("example.nl", [Destination("mail.example.nl")])]))
```

## Tests

```sh
cd backend && python3 -m unittest discover -s mailfilter_config/tests -t .
```

CI also applies a sample configuration with the real Debian 13 tools
(`tests/integration.sh`).
