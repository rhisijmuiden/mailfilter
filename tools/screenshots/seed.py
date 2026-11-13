"""Fill an empty Mailfilter database with realistic demo data for the README screenshots.

Run against a throw-away database only (it adds rows, it does not clean up):

    . tools/screenshots/env.sh && python tools/screenshots/seed.py

Every name, domain and address here is made up (`.example` domains).
"""

from __future__ import annotations

import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from sqlalchemy import select

from mailfilter.apikeys import new_token
from mailfilter.api.gateway import dovecot_hash
from mailfilter.archive.ingest import archive_message
from mailfilter.db import session
from mailfilter.mailtrap.deliver import deliver
from mailfilter.models import (
    ApiKey, ArchiveDomain, AuditLog, BillingCustomer, CloudTenant, DestinationPolicy, DmarcReport, DmarcRow,
    DigestDomain, Domain, DomainDestination, DomainRoute, LinkClick, MailTrap, MailTrapNetwork, MonthlyStats, OutboundHold,
    QuarantineAction, RelayNetwork, RelayPort, Reseller, SenderListEntry, SmtpAccount, SpamFeedback, TlsPolicy,
    TraceMessage, TraceRecipient, User,
)
from mailfilter.security import hash_password
from mailfilter.settings import get_settings
from mailfilter.settings_store import set_setting

random.seed(7)
NOW = datetime.now(timezone.utc).replace(microsecond=0)
HOST = get_settings().hostname
PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password-123")

TRANSPORT = {
    "rhis.example": "mail.rhis.example",
    "bakkerij-jansen.example": "mx.bakkerij-jansen.example",
    "devries-auto.example": "bakkerij-mail.hosting.example",
    "studio-noord.example": "studio-noord-example.mail.protection.outlook.com",
    "tandarts-zuid.example": "mail.tandarts-zuid.example",
}
RELAY_ONLY = ["webshop-zon.example", "nieuwsbrief.rhis.example"]
PEOPLE = {
    "rhis.example": ["reinder", "info", "support", "facturen", "sanne"],
    "bakkerij-jansen.example": ["info", "bestellingen", "piet", "marieke"],
    "devries-auto.example": ["info", "werkplaats", "verkoop", "jan"],
    "studio-noord.example": ["hello", "lisa", "tom", "projecten"],
    "tandarts-zuid.example": ["praktijk", "afspraken", "dr.bos"],
}
OUTSIDE = ["gmail.com", "outlook.com", "hotmail.com", "ziggo.nl", "kpnmail.nl", "icloud.com", "leverancier.example",
           "boekhouder.example", "gemeente.example", "partner-bv.example"]
SPAMMERS = ["win-prize.example", "crypto-boost.example", "cheap-meds.example", "secure-login-update.example",
            "invoice-dhl-track.example", "seo-rank-fast.example"]


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def qid() -> str:
    return "".join(random.choice("0123456789ABCDEF") for _ in range(10)) + random.choice("ABCDEF")


# ---------------------------------------------------------------- domains, users, relay
def seed_config(db) -> dict[str, Domain]:
    doms: dict[str, Domain] = {}
    cust_a = BillingCustomer(name="Bakkerij Jansen", ref="D1001", tariffs={})
    reseller = Reseller(name="Webbureau Noord", ref="R200", discount_percent=15)
    cust_b = BillingCustomer(name="De Vries Auto", ref="D1002", tariffs={})
    cust_c = BillingCustomer(name="Studio Noord", ref="D1003", tariffs={}, reseller=reseller)
    cust_d = BillingCustomer(name="Tandartspraktijk Zuid", ref="D1004", tariffs={})
    own = BillingCustomer(name="RHIS (own)", ref="D0001", tariffs={})
    db.add_all([cust_a, cust_b, cust_c, cust_d, own, reseller])
    payer = {"rhis.example": own, "bakkerij-jansen.example": cust_a, "devries-auto.example": cust_b,
             "studio-noord.example": cust_c, "tandarts-zuid.example": cust_d, "webshop-zon.example": cust_c,
             "nieuwsbrief.rhis.example": own}
    for i, (name, host) in enumerate(TRANSPORT.items()):
        d = Domain(name=name)
        db.add(d)
        db.flush()
        d.billing_customer_id = payer[name].id
        r = DomainRoute(domain=d, kind="transport", sender_lock=False, enabled=True, tls="verify" if i % 2 == 0 else "encrypt",
                        verify_recipients=True, recipient_check=["callout", "ldap", "callout", "callout", "list"][i],
                        recipient_list=["praktijk@tandarts-zuid.example", "afspraken@tandarts-zuid.example",
                                        "dr.bos@tandarts-zuid.example"] if i == 4 else [],
                        recipient_ldap={"server": "ldaps://dc01.devries-auto.example", "base_dn": "DC=devries,DC=local",
                                        "bind_dn": "svc-mailfilter@devries.local", "filter": "(proxyAddresses=smtp:%s)"} if i == 1 else None,
                        concurrency=None if i else 10, out_per_hour=None if i else 2000, note="")
        r.destinations = [DomainDestination(position=0, host=host, port=25, mx=False, protocol="smtp")]
        if name == "rhis.example":
            r.destinations.append(DomainDestination(position=1, host="mail2.rhis.example", port=25, mx=False, protocol="smtp"))
        db.add(r)
        doms[name] = d
    for name in RELAY_ONLY:
        d = Domain(name=name)
        db.add(d)
        db.flush()
        d.billing_customer_id = payer[name].id
        db.add(DomainRoute(domain=d, kind="relay", sender_lock=True, enabled=True, tls="may", verify_recipients=False,
                           recipient_check="off", recipient_list=[], out_per_hour=500, out_per_day=5000, note=""))
        doms[name] = d
    db.flush()

    admin = db.execute(select(User).where(User.role == "admin")).scalars().first()
    admin.name, admin.email = "Reinder", "reinder@rhis.example"
    admin.password_hash = hash_password(PASSWORD)
    admin.domains = []
    admin.last_login_at = ago(minutes=3)
    customers = [
        User(email="info@bakkerij-jansen.example", name="Marieke Jansen", role="customer", language="nl",
             password_hash=hash_password(PASSWORD), domains=[doms["bakkerij-jansen.example"]], last_login_at=ago(hours=5)),
        User(email="jan@devries-auto.example", name="Jan de Vries", role="customer", language="nl",
             password_hash=hash_password(PASSWORD), domains=[doms["devries-auto.example"]], last_login_at=ago(days=2)),
        User(email="lisa@studio-noord.example", name="Lisa Bakker", role="customer", language="en",
             password_hash=hash_password(PASSWORD), domains=[doms["studio-noord.example"], doms["webshop-zon.example"]],
             last_login_at=ago(hours=20)),
        User(email="praktijk@tandarts-zuid.example", name="Praktijk Zuid", role="customer", language="nl",
             password_hash=hash_password(PASSWORD), domains=[doms["tandarts-zuid.example"]]),
        User(email="sanne@rhis.example", name="Sanne de Groot", role="admin", language="nl",
             password_hash=hash_password(PASSWORD), last_login_at=ago(days=1)),
        User(email="partner@webbureau-noord.example", name="Webbureau Noord", role="reseller", language="nl",
             password_hash=hash_password(PASSWORD), reseller_id=reseller.id),
    ]
    db.add_all(customers)

    db.add_all([
        RelayNetwork(cidr="203.0.113.25/32", description="Web server shop", domain=doms["webshop-zon.example"], enabled=True),
        RelayNetwork(cidr="203.0.113.40/32", description="Order system", domain=doms["bakkerij-jansen.example"], enabled=True),
        RelayNetwork(cidr="2001:db8:42::/64", description="Hosting cluster", domain=doms["studio-noord.example"], enabled=True),
        RelayNetwork(cidr="198.51.100.7/32", description="Exchange server", domain=doms["rhis.example"], enabled=True),
        RelayNetwork(cidr="198.51.100.80/32", description="Scanner/printer office", domain=doms["devries-auto.example"], enabled=True),
        RelayNetwork(cidr="192.0.2.15/32", description="Old webserver (switched off)", domain=None, enabled=False),
        RelayPort(port=2525, tls="may", description="Legacy software"),
        SmtpAccount(username="shop@webshop-zon.example", domain=doms["webshop-zon.example"],
                    password_hash=dovecot_hash("x"), description="WooCommerce", enabled=True),
        SmtpAccount(username="mailer@nieuwsbrief.rhis.example", domain=doms["nieuwsbrief.rhis.example"],
                    password_hash=dovecot_hash("x"), description="Newsletter tool", enabled=True),
        DestinationPolicy(domain="hotmail.com", concurrency=5, rate_delay=1, note="Outlook.com throttles fast senders"),
        DestinationPolicy(domain="gmail.com", concurrency=10, rate_delay=None, note=""),
        TlsPolicy(destination="bank.example", level="verify", note="Bank requires TLS"),
        OutboundHold(kind="sasl", value="shop@webshop-zon.example", reason="spam", automatic=True, created_at=ago(minutes=26),
                     detail={"spam": 42, "total": 50, "minutes": 10}),
    ])
    for d in doms.values():
        if d.name in TRANSPORT:
            db.add(DigestDomain(domain_id=d.id, enabled=d.name != "rhis.example"))
    db.flush()
    return doms


# ---------------------------------------------------------------- mail trace
SUBJECTS_IN = ["Offerte aanvraag", "Factuur 2026-1142", "Re: afspraak dinsdag", "Your order has shipped", "Nieuwsbrief november",
               "Vraag over levering", "Meeting notes", "Re: Re: planning", "Bevestiging reservering", "Contract getekend"]


def trace_msg(db, *, minutes: float, direction: str, sender: str, rcpts: list[tuple], status: str, score: float = 0.0,
              symbols: dict | None = None, client: tuple[str, str] | None = None, sasl: str | None = None,
              size: int | None = None, action: str | None = None, done: bool = True, opts: dict | None = None) -> str:
    q = qid()
    first = ago(minutes=minutes)
    m = TraceMessage(queue_id=q, first_seen=first, last_seen=first + timedelta(seconds=random.randint(2, 40)),
                     direction=direction, status=status, listener="smtpd" if direction == "inbound" else "submission",
                     client_host=(client or ("mail-ed1-f54.google.com", "209.85.208.54"))[0],
                     client_ip=(client or ("", "209.85.208.54"))[1], sasl_user=sasl,
                     message_id=f"<{q.lower()}@{sender.split('@')[-1] or HOST}>", sender=sender,
                     size=size or random.randint(6_000, 240_000), spam_score=score, spam_action=action,
                     symbols=symbols or {"DMARC_POLICY_ALLOW": -0.5, "R_DKIM_ALLOW": -0.2, "R_SPF_ALLOW": -0.2,
                                         "BAYES_HAM": -3.0, "ARC_NA": 0.0, "MIME_GOOD": -0.1},
                     symbol_options=opts or {}, done=done)
    m.recipients = [TraceRecipient(queue_id=q, address=a, status=s, explain=e, reply=r, relay_host=h,
                                   dsn={"sent": "2.0.0", "bounced": "5.1.1", "deferred": "4.4.1"}.get(s),
                                   delay=round(random.uniform(0.3, 3.2), 1), attempts=1 if s != "deferred" else 6,
                                   updated=first + timedelta(seconds=5)) for a, s, e, r, h in rcpts]
    db.add(m)
    return q


def seed_trace(db) -> dict[str, str]:
    keep: dict[str, str] = {}
    hosts = {"gmail.com": "gmail-smtp-in.l.google.com", "outlook.com": "outlook-com.olc.protection.outlook.com",
             "hotmail.com": "hotmail-com.olc.protection.outlook.com", "ziggo.nl": "mx.ziggo.nl"}
    clients = [("mail-ed1-f54.google.com", "209.85.208.54"), ("mail-db8eur05on2091.outbound.protection.outlook.com", "40.107.20.91"),
               ("smtp.leverancier.example", "198.51.100.33"), ("mx2.boekhouder.example", "192.0.2.77")]
    total_minutes = 30 * 24 * 60
    # Volume: a day/night rhythm, more on weekdays.
    for _ in range(9000):
        minutes = random.random() ** 1.15 * total_minutes
        t = ago(minutes=minutes)
        if t.hour < 6 and random.random() < 0.7:
            continue
        dom = random.choice(list(PEOPLE))
        local = f"{random.choice(PEOPLE[dom])}@{dom}"
        roll = random.random()
        if roll < 0.52:  # inbound, delivered
            ext = random.choice(OUTSIDE)
            trace_msg(db, minutes=minutes, direction="inbound", sender=f"{random.choice(['p.smit', 'info', 'noreply', 'anna', 'support'])}@{ext}",
                      rcpts=[(local, "sent", None, "250 2.0.0 Ok: queued", TRANSPORT[dom])], status="delivered",
                      score=round(random.uniform(-4, 3.5), 2), client=random.choice(clients))
        elif roll < 0.78:  # outbound, delivered
            ext = random.choice(OUTSIDE)
            trace_msg(db, minutes=minutes, direction="outbound", sender=local,
                      rcpts=[(f"klant{random.randint(1, 99)}@{ext}", "sent", None, "250 2.0.0 OK", hosts.get(ext, f"mx.{ext}"))],
                      status="delivered", score=round(random.uniform(-1, 2), 2), client=("exchange.rhis.example", "198.51.100.7"),
                      symbols={"DKIM_SIGNED": 0.0, "MID_RHS_MATCH_FROM": 0.0, "ARC_NA": 0.0})
        elif roll < 0.90:  # spam refused at the door
            sp = random.choice(SPAMMERS)
            trace_msg(db, minutes=minutes, direction="inbound", sender=f"offer@{sp}", rcpts=[(local, "rejected", "rejected_spam",
                      "554 5.7.1 Spam message rejected", None)], status="rejected", score=round(random.uniform(15, 28), 2),
                      action="reject", client=(f"mail.{sp}", f"185.{random.randint(10, 250)}.{random.randint(1, 250)}.{random.randint(1, 250)}"),
                      symbols={"RBL_SPAMHAUS_XBL": 4.0, "BAYES_SPAM": 5.1, "R_SPF_FAIL": 1.0, "DMARC_POLICY_REJECT": 2.0,
                               "PHISHING": 4.5, "MANY_INVISIBLE_PARTS": 1.0})
        elif roll < 0.95:  # unknown user
            trace_msg(db, minutes=minutes, direction="inbound", sender=f"news@{random.choice(OUTSIDE)}",
                      rcpts=[(f"oud-medewerker@{dom}", "rejected", "rejected_unknown_user", "550 5.1.1 Recipient address rejected: User unknown", None)],
                      status="rejected", score=0.0)
        elif roll < 0.975:  # greylisted then fine
            trace_msg(db, minutes=minutes, direction="inbound", sender=f"service@{random.choice(OUTSIDE)}",
                      rcpts=[(local, "sent", None, "250 2.0.0 Ok", TRANSPORT[dom])], status="delivered", score=4.4,
                      symbols={"GREYLIST": 0.0, "BAYES_HAM": -1.0, "FORGED_SENDER": 0.3, "R_SPF_SOFTFAIL": 0.0, "MIME_HTML_ONLY": 0.2})
        else:  # outbound bounce
            trace_msg(db, minutes=minutes, direction="outbound", sender=local,
                      rcpts=[(f"vroeger@{random.choice(['ziggo.nl', 'kpnmail.nl'])}", "bounced", "user_unknown",
                              "host mx.ziggo.nl[212.54.42.8] said: 550 5.1.1 <vroeger@ziggo.nl>: Recipient address rejected: User unknown", "mx.ziggo.nl")],
                      status="bounced", score=0.2)

    # Hand-made messages that the screenshots open.
    keep["delivered"] = trace_msg(db, minutes=7, direction="inbound", sender="p.smit@leverancier.example",
                                  rcpts=[("bestellingen@bakkerij-jansen.example", "sent", None, "250 2.0.0 Ok: queued as 4dQx7", "mx.bakkerij-jansen.example")],
                                  status="delivered", score=-3.1, client=("smtp.leverancier.example", "198.51.100.33"),
                                  symbols={"DMARC_POLICY_ALLOW": -0.5, "R_DKIM_ALLOW": -0.2, "R_SPF_ALLOW": -0.2, "BAYES_HAM": -3.0,
                                           "ARC_NA": 0.0, "MIME_GOOD": -0.1, "RCVD_TLS_ALL": 0.0, "FROM_EQ_ENVFROM": 0.0})
    keep["bounced"] = trace_msg(db, minutes=34, direction="outbound", sender="info@devries-auto.example",
                                rcpts=[("j.peters@ziggo.nl", "bounced", "mailbox_full",
                                        "host mx.ziggo.nl[212.54.42.8] said: 552 5.2.2 <j.peters@ziggo.nl>: Mailbox full", "mx.ziggo.nl"),
                                       ("m.peters@gmail.com", "sent", None, "250 2.0.0 OK 1731491 si2-20020a17", "gmail-smtp-in.l.google.com")],
                                status="partly_delivered", score=0.4, client=("exchange.devries-auto.example", "198.51.100.80"))
    keep["deferred"] = trace_msg(db, minutes=95, direction="inbound", sender="nieuws@partner-bv.example",
                                 rcpts=[("praktijk@tandarts-zuid.example", "deferred", "server_unreachable",
                                         "connect to mail.tandarts-zuid.example[192.0.2.60]:25: Connection timed out", "mail.tandarts-zuid.example")],
                                 status="deferred", score=0.8, done=False)
    # Quarantine
    q_spam = [
        ("security@secure-login-update.example", "info@bakkerij-jansen.example", "Uw account wordt geblokkeerd - bevestig nu", 11.4,
         {"PHISHING": 4.5, "BAYES_SPAM": 3.2, "MF_LOOKALIKE_DOMAIN": 2.0, "R_SPF_SOFTFAIL": 0.6, "HFILTER_URL_ONLY": 1.1}),
        ("dhl@invoice-dhl-track.example", "jan@devries-auto.example", "Pakket kon niet worden bezorgd", 9.6,
         {"BAYES_SPAM": 4.1, "SUSPICIOUS_RECIPS": 1.5, "MF_SUSPICIOUS_LINK": 2.5, "DMARC_POLICY_QUARANTINE": 1.5}),
        ("ceo@rhis-example.example", "facturen@rhis.example", "Urgent: betaling vandaag nog", 10.2,
         {"MF_DISPLAYNAME_SPOOF": 6.0, "FREEMAIL_REPLYTO": 2.1, "BAYES_SPAM": 2.1}),
        ("promo@seo-rank-fast.example", "hello@studio-noord.example", "Page 1 of Google in 7 days", 8.7,
         {"BAYES_SPAM": 5.1, "MF_NEWSLETTER_NO_UNSUB": 4.0, "R_SPF_NA": 0.0}),
        ("scan@leverancier.example", "werkplaats@devries-auto.example", "Gescand document", 12.0,
         {"CLAM_VIRUS": 10.0, "MF_ATTACHMENT_MACRO": 2.0}),
        ("noreply@crypto-boost.example", "afspraken@tandarts-zuid.example", "Earn 4,000 EUR a week from home", 8.3,
         {"BAYES_SPAM": 5.1, "RBL_SENDERSCORE": 2.0, "MIME_HTML_ONLY": 0.2, "R_SPF_FAIL": 1.0}),
    ]
    queue = {"hold": [], "messages": {}}
    for i, (frm, to, subj, score, syms) in enumerate(q_spam):
        q = trace_msg(db, minutes=12 + i * 47, direction="inbound", sender=frm, rcpts=[(to, "quarantined", "quarantine", "", None)],
                      status="quarantined", score=score, symbols=syms, action="quarantine", done=False,
                      client=(f"mail.{frm.split('@')[1]}", f"45.{60 + i}.{12 * i + 3}.{90 + i}"))
        queue["hold"].append(q)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = frm, to, subj
        msg["Date"] = format_datetime(ago(minutes=12 + i * 47))
        msg["Message-ID"] = make_msgid(domain=frm.split("@")[1])
        msg.set_content(f"Beste klant,\n\n{subj}. Klik op de onderstaande link om dit binnen 24 uur op te lossen.\n\n"
                        f"https://{frm.split('@')[1]}/verify?id=8812\n\nMet vriendelijke groet,\nKlantenservice")
        if "CLAM_VIRUS" in syms:
            msg.add_attachment(b"PK\x03\x04 demo", maintype="application", subtype="vnd.ms-word.document.macroEnabled.12",
                               filename="scan_0042.docm")
        queue["messages"][q] = msg.as_string()
        if i == 0:
            keep["quarantine"] = q
    released = trace_msg(db, minutes=300, direction="inbound", sender="nieuwsbrief@leverancier.example",
                         rcpts=[("info@bakkerij-jansen.example", "sent", None, "250 2.0.0 Ok", "mx.bakkerij-jansen.example")],
                         status="delivered", score=8.4, symbols={"BAYES_SPAM": 3.0, "MF_NEWSLETTER": 0.0, "MIME_HTML_ONLY": 0.2})
    db.add(QuarantineAction(queue_id=released, action="released", learned="ham", user_email="info@bakkerij-jansen.example", at=ago(minutes=280)))
    # Mailtrap
    trace_msg(db, minutes=18, direction="outbound", sender="noreply@staging.webshop-zon.example",
              rcpts=[("test.klant@gmail.com", "trapped", "mailtrap", "", None)], status="trapped", score=1.2,
              client=("staging.webshop-zon.example", "203.0.113.90"))
    Path(os.environ["FAKE_QUEUE"]).write_text(json.dumps(queue))
    return keep


# ---------------------------------------------------------------- lists, reports, feedback
def seed_lists(db, doms) -> None:
    rows = [
        ("allow", "*", "leverancier.example", None, "Supplier invoices"),
        ("allow", "@bakkerij-jansen.example", "groothandel-meel.example", "bakkerij-jansen.example", "Flour wholesaler"),
        ("allow", "jan@devries-auto.example", "rdw.example", "devries-auto.example", ""),
        ("block", "*", "seo-rank-fast.example", None, "Keeps sending SEO spam"),
        ("block", "@studio-noord.example", "crypto-boost.example", "studio-noord.example", ""),
        ("block", "praktijk@tandarts-zuid.example", "leads@dental-marketing.example", "tandarts-zuid.example", "Unwanted marketing"),
    ]
    for lst, rcpt, sender, dom, note in rows:
        db.add(SenderListEntry(list=lst, recipient=rcpt, sender=sender, domain_id=doms[dom].id if dom else None, comment=note,
                               created_by="reinder@rhis.example", created_at=ago(days=random.randint(1, 40))))


def seed_dmarc(db) -> None:
    orgs = [("google.com", "noreply-dmarc-support@google.com"), ("Microsoft Corporation", "dmarcreport@microsoft.com"),
            ("Yahoo", "dmarc_support@yahoo.com"), ("Mail.Ru", "dmarc_support@corp.mail.ru")]
    senders = [("209.85.220.41", "mail-sor-f41.google.com", "pass", "pass"),
               ("40.107.22.70", "mail-am6eur05on2070.outbound.protection.outlook.com", "pass", "pass"),
               ("203.0.113.10", HOST, "pass", "pass"),
               ("198.51.100.150", "mta150.mailchimp.example", "fail", "pass"),
               ("185.12.33.4", "unknown.host.example", "fail", "fail")]
    for dom in ["rhis.example", "bakkerij-jansen.example", "devries-auto.example", "studio-noord.example"]:
        for day in range(1, 29):
            for org, email in orgs[: 2 if day % 3 else 4]:
                begin = ago(days=day).replace(hour=0, minute=0, second=0)
                r = DmarcReport(org_name=org, org_email=email, report_id=f"{dom}-{org}-{day}", domain=dom, begin=begin,
                                end=begin + timedelta(days=1), p="none" if dom != "rhis.example" else "quarantine", sp="",
                                pct=100, adkim="r", aspf="r", messages=0, source="rua", received_at=begin + timedelta(days=1, hours=3))
                total = 0
                for ip, host, dkim, spf in senders:
                    if host.startswith("unknown") and random.random() < 0.6:
                        continue
                    n = random.randint(20, 160) if dkim == "pass" else random.randint(1, 14)
                    total += n
                    r.rows.append(DmarcRow(source_ip=ip, source_host=host, count=n,
                                           disposition="none", dkim=dkim, spf=spf, header_from=dom, envelope_from=dom,
                                           dkim_auth=[], spf_auth=[], reasons=[]))
                r.messages = total
                db.add(r)


def seed_feedback(db) -> None:
    rows = [
        ("spam", "learned", "info@bakkerij-jansen.example", "bakkerij-jansen.example", "proven", "promo@seo-rank-fast.example",
         "Gratis SEO-scan voor uw bakkerij", 4.1, "spam", True),
        ("spam", "learned", "jan@devries-auto.example", "devries-auto.example", "proven", "deals@cheap-meds.example",
         "Special offer only today", 6.2, "spam", False),
        ("ham", "learned", "lisa@studio-noord.example", "studio-noord.example", "proven", "noreply@partner-bv.example",
         "Your invoice for October", 8.9, "ham", False),
        ("spam", "unverified", "random@gmail.com", "", "none", "info@leverancier.example", "Offerte 2026-118", 0.3, None, False),
        ("spam", "learned", "praktijk@tandarts-zuid.example", "tandarts-zuid.example", "proven", "hello@dental-marketing.example",
         "Meer patiënten via Google", 3.4, "spam", True),
    ]
    for i, (kind, status, rep, dom, ver, sender, subj, score, learned, blocked) in enumerate(rows):
        db.add(SpamFeedback(at=ago(hours=3 + i * 9), kind=kind, status=status, detail="", source="forward", reporter=rep, domain=dom,
                            verified=ver, queue_id=None, message_id=f"<{i}x@demo>", sender=sender, recipient=rep, subject=subj,
                            score=score, action="", learned=learned, blocked=blocked, raw=None, handled_by=""))


def seed_monthly(db) -> None:
    for back in range(1, 7):
        m = (NOW.date().replace(day=1) - timedelta(days=28 * back)).replace(day=1)
        for dom, base in [("rhis.example", 4200), ("bakkerij-jansen.example", 1900), ("devries-auto.example", 2600),
                          ("studio-noord.example", 3100), ("tandarts-zuid.example", 950)]:
            rec = int(base * random.uniform(0.85, 1.15))
            spam = int(rec * random.uniform(0.18, 0.31))
            db.add(MonthlyStats(month=m, domain=dom, received=rec, delivered=rec - spam - 12, spam=spam, virus=random.randint(0, 9),
                                refused=int(spam * 0.7), other=3, outbound=int(base * 0.6), outbound_blocked=random.randint(0, 6)))


def seed_links(db) -> None:
    R = lambda code, level, **p: {"code": code, "level": level, "params": p}
    rows = [("https://www.leverancier.example/factuur/2026-1142", "safe", [], False),
            ("https://bit.ly/3xYz", "safe", [R("shortener", "safe", host="bit.ly", destination="studio-noord.example")], False),
            ("https://secure-login-update.example/verify?id=8812", "block",
             [R("blocklist", "block", list="Spamhaus DBL", kind="phish")], False),
            ("https://rnicrosoft-support.example/reset", "warn",
             [R("lookalike", "warn", host="rnicrosoft-support.example", target="microsoft.com")], True),
            ("https://studio-noord.example/portfolio", "safe", [], False),
            ("http://203.0.113.200/claim", "warn", [R("ip_address", "warn", host="203.0.113.200")], False)]
    for i, (url, level, reasons, cont) in enumerate(rows * 3):
        host = url.split("/")[2]
        db.add(LinkClick(at=ago(hours=i * 5 + 1), url=url, host=host, final_url=url, recipient=random.choice(
            ["info@bakkerij-jansen.example", "jan@devries-auto.example", "lisa@studio-noord.example"]),
            domains=[host], message_id="<demo@x>", queue_id="", delivered_at=ago(hours=i * 5 + 2), level=level, reasons=reasons,
            continued=cont, scanner=False, ip="198.51.100.200", user_agent="Mozilla/5.0"))


def seed_mailtrap(db, doms) -> None:
    root = get_settings().mailtrap_dir
    t = MailTrap(slug="webshop-staging", name="Webshop staging", domain=doms["webshop-zon.example"], login="trap-webshop-staging",
                 password_hash=dovecot_hash("x"), smtp_login=True, imap=True, mode="except",
                 allow=["@studio-noord.example"], retention_days=7, max_messages=1000, enabled=True)
    t.networks = [MailTrapNetwork(cidr="203.0.113.90/32", description="staging.webshop-zon.example")]
    db.add(t)
    db.add(MailTrap(slug="crm-test", name="CRM acceptance", domain=doms["rhis.example"], login="trap-crm-test",
                    password_hash=dovecot_hash("x"), smtp_login=False, imap=False, mode="all", allow=[], retention_days=14,
                    max_messages=500, enabled=True, networks=[MailTrapNetwork(cidr="2001:db8:77::/64", description="CRM test")]))
    cfg = {"boxes": {"webshop-staging": {"mode": "except", "allow": ["@studio-noord.example"], "maildir": str(root / "trap-webshop-staging" / "Maildir"),
                                         "retention_days": 7, "max_messages": 1000}}}
    html = """<html><body style="font-family:Arial"><table width="600"><tr><td style="background:#E8553D;color:#fff;padding:16px">
<h1>Bedankt voor je bestelling!</h1></td></tr><tr><td style="padding:16px"><p>Hoi {{ customer.first_name }},</p>
<p>We hebben je bestelling <b>#{{ order.id }}</b> ontvangen.</p><img src="https://cdn.webshop-zon.example/logo.png">
<p><a href="http://staging.webshop-zon.example:8080/account/orders">Bekijk je bestelling</a></p></td></tr></table></body></html>"""
    for i, (to, subj) in enumerate([("test.klant@gmail.com", "Bedankt voor je bestelling #10422"),
                                    ("lisa@studio-noord.example", "Je wachtwoord resetten"),
                                    ("piet@example.org", "Je bestelling is verzonden"),
                                    ("anna@example.org", "Nieuwsbrief: herfstactie 20% korting")]):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "Webshop Zon <noreply@webshop-zon.example>", to, subj
        m["Date"] = format_datetime(ago(minutes=18 + i * 61))
        m["Message-ID"] = make_msgid(domain="staging.webshop-zon.example")
        m.set_content("Hoi,\n\nBedankt voor je bestelling.\n")
        m.add_alternative(html if i != 1 else html.replace("{{ customer.first_name }}", "Lisa").replace("#{{ order.id }}", ""), subtype="html")
        deliver("webshop-staging", m.as_bytes(), client="203.0.113.90", sender="noreply@webshop-zon.example",
                recipients=[to], config=cfg, sendmail=lambda *a: None)


def seed_archive(db) -> None:
    for name in ["rhis.example", "devries-auto.example"]:
        db.add(ArchiveDomain(name=name, enabled=True, retention_years=7, legal_hold=name == "devries-auto.example",
                             legal_hold_since=ago(days=12) if name == "devries-auto.example" else None,
                             legal_hold_note="Case 2026-14, request from the lawyer" if name == "devries-auto.example" else "",
                             last_seq=0, head=""))
    db.commit()
    subjects = ["Offerte nieuwe website", "Factuur 2026-1142", "Re: planning onderhoud", "Contract verlenging 2027",
                "Bevestiging APK-afspraak", "Jaarrekening 2025", "Re: schadeclaim", "Notulen overleg", "Prijslijst onderdelen"]
    for i in range(60):
        dom = "rhis.example" if i % 2 else "devries-auto.example"
        m = EmailMessage()
        frm = f"{random.choice(['p.smit', 'anna', 'boekhouding'])}@{random.choice(OUTSIDE)}"
        to = f"{random.choice(PEOPLE[dom])}@{dom}"
        m["From"], m["To"], m["Subject"] = frm, to, random.choice(subjects)
        m["Date"] = format_datetime(ago(hours=i * 7 + 1))
        m["Message-ID"] = make_msgid(domain="demo.example")
        m.set_content("Goedemiddag,\n\nZie bijgaand de stukken.\n\nGroet")
        if i % 4 == 0:
            m.add_attachment(b"%PDF-1.4 demo", maintype="application", subtype="pdf", filename=f"document-{i}.pdf")
        archive_message(db, dom, m.as_bytes(), queue_id=f"ARC{i:05d}", sender=frm, now=ago(hours=i * 7 + 1))


def seed_misc(db, doms) -> None:
    admin = db.execute(select(User).where(User.email == "reinder@rhis.example")).scalar_one()
    for name, scopes, ips in [("DirectAdmin panel", {"domains": "write", "relay": "write", "smtp": "write", "quarantine": "read"}, ["198.51.100.12"]),
                              ("Monitoring", {"trace": "read", "audit": "read"}, [])]:
        _, h, hint = new_token()
        db.add(ApiKey(user_id=admin.id, name=name, token_hash=h, hint=hint, scopes=scopes, allowed_ips=ips,
                      expires_at=None if ips else NOW + timedelta(days=180), created_by=admin.email,
                      last_used_at=ago(minutes=9), last_used_ip="198.51.100.12"))
    db.add(CloudTenant(domain_id=doms["studio-noord.example"].id, provider="m365", tenant_id="3f2b9c1e-7d44-4a1b-9e0f-5c6d7e8f9a01",
                       enabled=True, steps={"detect": "done", "mailfilter": "done", "inbound_connector": "done",
                                            "enhanced_filtering": "done", "outbound_connector": "done"}))
    actions = [("domain.update", "bakkerij-jansen.example", "reinder@rhis.example"), ("relay.add", "203.0.113.25/32", "reinder@rhis.example"),
               ("quarantine.release", "info@bakkerij-jansen.example", "info@bakkerij-jansen.example"),
               ("lists.add", "seo-rank-fast.example", "sanne@rhis.example"), ("user.create", "praktijk@tandarts-zuid.example", "reinder@rhis.example"),
               ("auth.login", "", "lisa@studio-noord.example"), ("archive.search", "devries-auto.example", "reinder@rhis.example"),
               ("dns.publish", "studio-noord.example", "reinder@rhis.example"), ("backup.create", "full", "reinder@rhis.example"),
               ("auth.2fa_enabled", "", "sanne@rhis.example")]
    for i, (action, target, who) in enumerate(actions):
        db.add(AuditLog(at=ago(minutes=20 + i * 95), user_email=who, domain=target if "." in target and "@" not in target and "/" not in target else None,
                        action=action, target=target, detail={"kind": "spf"} if action == "dns.publish" else {}, ip="198.51.100." + str(10 + i)))
    set_setting(db, "mailtrap.enabled", True)
    set_setting(db, "gateway.last_apply", {"at": ago(minutes=40).isoformat(), "ok": True, "changed": []})
    from mailfilter.cluster import state
    from mailfilter.models import ClusterNode
    me = state.new_id()
    set_setting(db, state.CONFIG_KEY, {"cluster_id": state.new_id(), "node_id": me, "spf_name": f"_spf.{HOST}",
                                       "created_at": ago(days=9).isoformat()})
    state.set_cluster_secret(db, state.new_secret())
    db.add_all([
        ClusterNode(node_id=me, name="mailfilter01", url=f"https://{HOST}", addresses=["203.0.113.10", "2001:db8:10::25"],
                    is_self=True, joined_at=ago(days=9), last_seen_at=NOW, last_sync_at=ago(seconds=40), version="1.0.0"),
        ClusterNode(node_id=state.new_id(), name="mailfilter02", url="https://mailfilter02.example.net",
                    addresses=["198.51.100.20", "2001:db8:20::25"], is_self=False, joined_at=ago(days=8),
                    last_seen_at=ago(seconds=35), last_sync_at=ago(seconds=35), version="1.0.0", latency_ms=14)])
    set_setting(db, "quarantine_report", {"enabled": True, "hour": 8, "language": "nl", "only_new": True})
    set_setting(db, "monthly_report", {"enabled": True, "day": 1, "hour": 9, "admin_summary": True})
    set_setting(db, "spam_reports", {"enabled": True, "require_auth": True, "auto_block": True, "per_hour": 20})
    set_setting(db, "security", {"require_2fa": "off"})
    set_setting(db, "billing.tariffs", {"currency": "EUR", "per_domain": "4.50", "per_mailbox": "0.85", "per_1000_in": "0",
                                        "per_1000_out": "0.40", "per_gb_archive": "0.25", "minimum": "15.00", "fixed": "0"})
    set_setting(db, "notifications", {"email": {"enabled": True, "to": ["beheer@rhis.example"]},
                                      "telegram": {"enabled": True, "chat_id": "-1001234567890"},
                                      "webhook": {"enabled": False, "url": "", "format": "json"}, "language": "nl",
                                      "repeat_hours": 6, "resolved": True})
    set_setting(db, "notifications.history", [
        {"at": ago(minutes=26).isoformat(), "key": "abuse", "code": "abuse", "level": "warning", "params": {"count": 1},
         "channels": ["email", "telegram"], "resolved": False},
        {"at": ago(days=2).isoformat(), "key": "backup", "code": "remote_failed", "level": "warning", "params": {},
         "channels": ["email"], "resolved": True},
        {"at": ago(days=6).isoformat(), "key": "services:clamav-daemon", "code": "services", "level": "critical",
         "params": {"service": "clamav-daemon"}, "channels": ["email", "telegram"], "resolved": True}])


def main() -> None:
    with session() as db:
        doms = seed_config(db)
        db.commit()
        seed_trace(db)
        seed_lists(db, doms)
        seed_dmarc(db)
        seed_feedback(db)
        seed_monthly(db)
        seed_links(db)
        db.commit()
        seed_mailtrap(db, doms)
        seed_misc(db, doms)
        db.commit()
        seed_archive(db)
        db.commit()
    print("Demo data ready. Log in as reinder@rhis.example with", PASSWORD)


if __name__ == "__main__":
    main()
