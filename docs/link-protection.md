# Link protection

Phishing sites are often harmless when a message arrives and turn dangerous an
hour later. With link protection Mailfilter checks a link **when someone
clicks it**, not only when the mail comes in.

## How it works

1. **Delivery.** For domains with the rule on, Rspamd replaces every http(s)
   link in the text and HTML of incoming mail by
   `https://<mailfilter host>/l/<payload>.<signature>`. The payload holds the
   original link, the Message-ID, the recipient and the time; the signature
   (keyed BLAKE2b with `/etc/mailfilter/link-protection.key`) means nobody can
   make such links themselves, so the click page is never an open redirect.
   Nothing is stored per link. Links to your own domains and to the domains
   under *Do not change links* stay as they are. The message gets the header
   `X-Mailfilter-Links: protected; <n>`.
2. **Click.** The web app checks the destination at that moment: your block
   list, Spamhaus DBL, SURBL and URIBL (through the local resolver), the same
   checks as the phishing rules (bare IP address, `www.bank.nl@evil.example`,
   look-alikes of your domains), and it follows link shorteners to their real
   destination. A link that Rspamd or a phishing rule already named at
   delivery, or mail from a fake sender, gives a warning too.
3. **Result.** Safe: the recipient goes straight on. Suspicious: a warning
   page with *Continue anyway* (can be switched off). Dangerous: a block page
   without a way on. Pages are in English and Dutch, in the house style.

Every click is listed under **Mail flow › Link clicks**. Security scanners and
link previews that open links are marked and hidden by default. Admins can
block a domain from there; that also stops the links in mail that was already
delivered. A blocked click, or someone continuing after a warning, can raise an
alert (Settings › Notifications, "Dangerous link clicked"). Clicks are kept for
180 days.

## Switching it on

**Configuration › Custom rules › Phishing › Check links when they are
clicked.** Off by default; on for everyone or per domain. Customers can switch
it for their own domains.

## DKIM and signed mail

Changing a link changes the message, so the sender's DKIM signature no longer
matches. Therefore:

- Mailfilter checks SPF, DKIM and DMARC **before** it changes anything; the
  result is in `Authentication-Results`.
- Signed or encrypted mail (S/MIME, PGP) and forwarded messages as attachment
  are never changed.
- Mail that was changed is **not ARC-sealed**: Rspamd seals the original body,
  so the seal would be broken (tested with Rspamd).
- Only switch link protection on for domains whose mail server behind
  Mailfilter does not check DKIM/DMARC again, or put senders whose signature
  must stay intact under *Skip senders*.
- Outgoing mail and mail from your own domains is never changed.
