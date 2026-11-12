# DKIM key rotation

Every domain (transport and relay) signs its outgoing mail with a DKIM key.
Mailfilter replaces each key on a fixed schedule, without a moment where mail
fails DKIM. Settings and status: **DNS toolbox → DKIM keys**.

## How one rotation goes

1. **New key published.** A new 2048-bit key with a new selector
   (`mfYYYYMM`, e.g. `mf202611`) is made next to the current one. When the
   domain's zone is in Cloudflare, its record `mf202611._domainkey.<domain>`
   is created there (it shows in the DNS change log and can be undone). The
   current key keeps signing.
2. **Seen in DNS.** Mailfilter asks the domain's own name servers (not a
   cache) until the record is there.
3. **Signing with the new key** after *wait after publishing* (default 48
   hours), so every DNS cache has the new record.
4. **Old key removed** after *keep old record* (default 7 days): the old
   record is deleted from Cloudflare (only the record with exactly the old
   public key) and the old private key is deleted from the server.

The timer `mailfilter-dkim-rotate.timer` runs every six hours; **Check now**
runs the same steps right away. `mailfilter-admin dkim-rotate` does it from
the command line.

## Which domains rotate automatically

* the key is older than *replace every* (default 6 months), and
* the current key is in DNS (a domain that is not live yet is left alone), and
* the domain's zone is in Cloudflare, unless **Also domains outside
  Cloudflare** is on. Then Mailfilter shows the new record and waits until you
  have published it yourself; the old key keeps signing until then. After the
  overlap, remove the old record at your DNS provider (the page says which).

**Rotate now** starts a rotation for any domain at once. A rotation can be
cancelled until the new key signs.

## Cluster

One server rotates: the first by name. The new key reaches the other servers
through the normal configuration sync right after the switch; the old record
stays in DNS during the overlap, so a server that syncs a little later still
signs with a key that verifies. The settings are synced; the page on the other
servers says which server does the rotation.
