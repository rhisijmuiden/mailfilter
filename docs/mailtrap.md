# Mailtrap

[Nederlands](nl/mailtrap.md) · [All documentation](README.md)

Mailtrap catches mail from test environments (staging and acceptance servers,
a developer's laptop) so it is **never sent on**. The mail lands in a Mailtrap
mailbox where you can read it in the web interface or in a mail program, and
check that it looks right and works.

## Switching it on

Mailtrap is a module and is **off** after installing or updating. An
administrator switches it on with the switch at the top of **Mail traffic ›
Mailtrap**. While it is off:

- the menu item is hidden for customers;
- mail from the addresses and logins of Mailtrap mailboxes is **refused**
  (`5.7.1 Mailtrap is switched off`), never relayed;
- IMAP (port 993) is closed.

## A mailbox

**Add mailbox** asks for:

- **Name** and, optionally, the **customer** (domain) it belongs to. Users of
  that customer can open the mailbox; give other users access under
  *Who may look*.
- **Which mail is caught**: IP addresses or networks (IPv4 and IPv6) of the
  test environment. An address is either in Relay access or in the Mailtrap,
  never both, and one address belongs to one mailbox. Networks larger than
  /16 (IPv4) or /32 (IPv6) are refused.
- Optionally **also catch mail sent with the mailbox login**, for test
  environments without a fixed address: they send through port 587 with the
  login `trap-<name>` and the password shown once.
- **What happens with the mail**: *Catch everything*, or *Catch everything
  except to testers*: addresses or whole domains (`@example.nl`) on the testers
  list also get the mail for real, so you can see it in Gmail or Outlook. A copy
  stays in the Mailtrap.
- **Log in with a mail program (IMAP)**: IMAP over TLS on port 993, user
  `trap-<name>`, the same password. SMTP accounts cannot log in on IMAP.
- How long mail is kept (default 7 days) and at most how many messages
  (default 1000); older mail is removed as new mail arrives.

## Reading and checking

The mailbox shows each message with chips for problems found. Opening a
message shows:

- **View**: the HTML as the recipient would see it (desktop or phone width).
  Images from the internet are not loaded.
- **Check**: spam score, links to test hosts (private addresses, `.local`,
  `.test`, `staging.`, other ports), links without HTTPS, template fields that
  were not filled in (`{{ order.id }}`), DKIM signature, From matching the
  envelope sender, HTML and text part, images without alt text, size (Gmail
  clips above 102 KB), subject, date and Message-ID, unsubscribe headers for
  bulk mail, attachments.
- **Text version**, **Headers** and **Source** (download as `.eml`).

**Forward to me** sends a copy to your own address as an attachment.
**Send anyway** (administrators only) sends the message to its original
recipients, without the Mailtrap headers; this is written to the audit log.

In Mail trace a caught message has the status **In Mailtrap**.

## Technical notes

- Postfix: a trap address or login gets a restriction class `mft_<name>` that
  hands the mail to the `mftrap` pipe (master.cf), which runs
  `mailfilter-trap-deliver` as the `mailfilter` user. The checks
  (`/etc/mailfilter/postfix/mailtrap.cidr`, `mailtrap_sasl`) come before
  `permit_mynetworks` on every listener, and trap addresses are not in
  `mynetworks`, so a trap address can never relay.
- Mail is stored as Maildir in `/var/lib/mailfilter/mailtrap/<login>/Maildir`.
  When storing fails, Postfix keeps the mail in the queue and tries again; it is
  never sent on.
- Rspamd treats trap addresses as outgoing (DKIM signing, no greylisting), so
  the spam score and DKIM check reflect the real situation.
- IMAP: Dovecot includes `/etc/mailfilter/dovecot-mailtrap.conf` (written by the
  web interface) with logins from `/etc/mailfilter/mailtrap-users`. The firewall
  opens port 993 only while a mailbox allows IMAP.
- Mailtrap mailboxes are not synced in a cluster: each node keeps its own.
- After updating an existing server, run the update from the Info screen (it
  re-runs the installer) before switching the module on: the web interface
  refuses to switch it on while Postfix does not have the Mailtrap checks yet.
