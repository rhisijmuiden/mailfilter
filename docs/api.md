# REST API

Mailfilter has a REST API for hosting panels and scripts: add domains, let a
web server relay, make SMTP accounts, release quarantined mail, search the
mail trace and more.

## API keys

Make a key under **System › API › API keys**. Choose per area what the key may
do (none, read, write), and optionally the IP addresses it may be used from and
an expiry date. The key (`mfk_…`) is shown once; Mailfilter stores only its
SHA-256.

- A key acts as its **owner**: an admin key works on every domain, a customer
  key only on that customer's domains. Admins can make a key for a customer.
- Everything done with a key is in the audit log, marked "via API key <name>".
- Keys are synced between cluster nodes, so a panel can talk to either server.

## Using the API

```
curl https://mailfilter.example.nl/api/v1/gateway/relay-networks \
  -H "Authorization: Bearer mfk_…" \
  -H "Content-Type: application/json" \
  -d '{"cidr": "203.0.113.10", "domain": "klant.nl", "description": "Webserver"}'
```

- Base URL `/api/v1`; it takes keys only (no session cookie).
- Read scopes allow GET, write scopes every method.
- At most 600 requests per key per minute (429 with `Retry-After`).
- `GET /api/v1/me` shows what the key may do.
- The OpenAPI description is at `/api/v1/openapi.json`; the documentation tab
  in the web app shows every endpoint with examples.

Areas: domains (with DNS records), relay IPs, SMTP accounts, quarantine, mail
trace (read), allow/block lists, customers (admin keys only; admin accounts
cannot be managed through the API) and the audit log (read).

Never available with a key: API keys themselves, updates, backups, cluster,
throttling and other system settings (`403 not_available_in_api`), so a leaked
key cannot give itself more rights.
