# Mail archive

[Nederlands](nl/archief.md) · [All documentation](README.md)

The archive keeps an **unchangeable copy of all mail per domain**, incoming and
outgoing, for up to 10 years. It is searchable, every day is sealed, and you can
prove that nothing was changed or removed. Find it under **Mail flow › Archive**.

## What is archived

Switch the archive on per domain (tab *Domains*, admins only). From then on:

- all mail that is really **delivered** to the domain or **sent** from it is archived,
  including mail released from the quarantine;
- refused spam and viruses are **not** archived (they never entered the queue),
  and neither is mail you delete from the quarantine.

How: Postfix adds one extra recipient, `<domain>@archive.mailfilter.invalid`
(`sender_bcc_maps` / `recipient_bcc_maps`, file `archive_bcc`). That copy travels
with the message and is delivered to the `mfarchive` pipe
(`mailfilter-archive-deliver`), so it is only archived when the message itself is
delivered. The mail trace never shows this extra recipient.

## Unchangeable

- **Files**: each message is stored once, gzip-compressed, read-only, in
  `/var/lib/mailfilter-archive/<domain>/<kept-until month>/<day>/`. Every night
  the root helper locks the finished days (`chattr +i`): not even the web app's
  own user can change or delete them.
- **Hash chain**: each message gets a SHA-256 fingerprint and an entry hash that
  includes the entry hash of the message before it (per domain). Changing,
  removing or inserting one message breaks every hash after it.
- **Day seals**: every night each domain's day is sealed: first and last number,
  the chain head, a digest of the real recipients and the previous seal, signed
  with this server's Ed25519 archive key. The public key is shown on the
  *Domains* tab and included in every export.
- **Database**: triggers refuse changes to sealed messages, deleting a message
  before its retention date or under a legal hold, and any change to seals.
- **Check**: *Check integrity* (admins) and the nightly job recompute all of the
  above; the nightly job reads every file on Sundays. Problems raise an alert
  (Settings › Notifications, type *Backups*).

## Retention and legal hold

- Retention is 1 to 10 years per domain (default 7, the Dutch fiscal period),
  counted to the end of the month. The nightly job removes mail after it and
  records each removal in the audit log, so the chain stays verifiable.
- A shorter retention only applies to mail archived after the change: mail
  already archived keeps at least the retention it was given.
- **Legal hold** stops all removal for a domain, also after the retention, until
  it is switched off. Give a reason; it goes to the audit log.

## Searching and exporting

Search on words in the subject, text and attachment names, on addresses (sender,
To/Cc and the real recipients including Bcc), direction, domain and date. Use
`"exact phrase"` and `-word`. Open a mail to see its fingerprint, day seal and
text; download the original `.eml`.

**Export** gives a zip with the `.eml` files, `manifest.csv`, and
`manifest.json` with the fingerprints, the hash chain, the day seals and the
public key(s), plus a README explaining how to check them without Mailfilter.

Customers see only their own domains. Every search, opened mail, download and
export is written to the audit log.

## Backups and cluster

The archive folder is part of every full and incremental backup (and of the
remote copies); its index, chain and seals are in the database backup. After a
restore the files are locked again. In a cluster, the archive settings per
domain are synced; each server archives the mail that passes through it.

## Commands

    mailfilter-admin archive-maintain [--full-check]   # what the nightly timer runs
    mailfilter-admin archive-verify [--no-files]
    mailfilterctl archive lock
    mailfilterctl archive purge <domain> <YYYY-MM>     # only months whose retention is over
