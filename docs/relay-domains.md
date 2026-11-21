# Transport domains and relay domains

[Nederlands](nl/relay-domeinen.md) · [All documentation](README.md)

Mailfilter knows two kinds of domains (Configuration › Domains):

| | Transport domain | Relay domain |
|---|---|---|
| Incoming mail | Accepted, filtered and delivered to the domain's mail server | Not accepted (the MX stays with the domain's own provider) |
| Outgoing mail | Signed with DKIM, checked, limited | Signed with DKIM, checked, limited |
| Mail server to deliver to | Required | None |

A relay domain is for web servers and applications that only send mail.

## Setting one up

1. **Domains › Add domain**, enter the name and choose **Relay domain**.
2. Saving creates a DKIM key (RSA 2048, selector `mfYYYY`) straight away.
3. Under **Who may send for this domain**, link the IP address of the web server
   and/or make an SMTP account. The password of an SMTP account is shown once;
   use the Mailfilter's host name, port 587 (STARTTLS) or 465 (SSL/TLS).
4. Under **DNS records**, check DKIM, SPF and DMARC. When the domain is in
   Cloudflare, publish each record with one button (shown first, undoable under
   DNS › Change log). Otherwise copy the records to the DNS provider and press
   **Check again**.

SPF: by default Mailfilter adds `include:_spf.<mailfilter host>` to the record
that is already there. Choose "Only the Mailfilter" when every mail of the
domain goes through it, with `~all` or `-all`.

## Sender lock

"Linked servers and accounts may only send as @domain" (on by default for new
relay domains, also available on transport domains): mail from the linked IP
addresses and SMTP accounts with another envelope sender is refused with
`5.7.1 This server may only send mail as @domain`. Subdomains and bounces
(empty sender) are allowed. Postfix checks this at the end of every message,
after a paused sender is put on hold.

## Converting

A relay domain can become a transport domain (enter its mail server) and the
other way around (Mailfilter then stops accepting incoming mail for it). The
name, limits, linked IP addresses and accounts stay.

## Technical notes

- Relay domains are left out of Postfix `relay_domains`, `transport` and
  `recipient_verify`; they keep their Rspamd send limits and DKIM key.
- SMTP accounts made in the web interface are written between marker lines in
  `/etc/mailfilter/smtp-users` (Dovecot passwd-file, `{SSHA512}` hashes of
  generated passwords). Lines outside the markers, added by hand, stay.
- In a cluster, SMTP accounts and the domain kind are synced with the rest of
  the configuration.
