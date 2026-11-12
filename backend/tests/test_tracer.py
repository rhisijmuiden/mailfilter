from datetime import UTC, datetime

from mailfilter_tracer import explain
from mailfilter_tracer.parser import parse_journal, parse_syslog, split_relay
from mailfilter_tracer.tracker import Tracker

QA = "4ZxY1q2Z3Kz9a1"
QB = "4ZxY1q3H7nz9b2"


def run(lines):
    t = Tracker()
    msgs = {}
    for line in lines:
        ev = parse_syslog(line, 2026)
        assert ev is not None, line
        m = t.apply(ev)
        if m:
            msgs[m.queue_id] = m
    return t, msgs


INBOUND = [
    f"Nov 11 10:00:01 mailfilter01 postfix/smtpd[101]: {QA}: client=mail-ej1.google.com[2a00:1450:4864::42c]:41234",
    f"Nov 11 10:00:01 mailfilter01 postfix/cleanup[102]: {QA}: message-id=<CAF1@mail.gmail.com>",
    "Nov 11 10:00:02 mailfilter01 rspamd[90]: <a1b2c3>; proxy; rspamd_task_write_log: id: <CAF1@mail.gmail.com>, "
    f"qid: <{QA}>, ip: 2a00:1450:4864::42c, from: <jan@gmail.com>, (default: F (no action): [-0.40/15.00] "
    "[DMARC_POLICY_ALLOW(-0.50){gmail.com;none;},R_SPF_ALLOW(-0.20){+ip6:2a00:1450::/32;},BAYES_HAM(-0.10){55%;}]), "
    "len: 4211, time: 120.1ms, dns req: 12, digest: <abc>, rcpts: <info@rhis.nl>, mime_rcpts: <info@rhis.nl>",
    f"Nov 11 10:00:02 mailfilter01 postfix/qmgr[103]: {QA}: from=<jan@gmail.com>, size=4211, nrcpt=1 (queue active)",
    f"Nov 11 10:00:03 mailfilter01 postfix/relay[104]: {QA}: to=<info@rhis.nl>, relay=mail.rhis.nl[192.0.2.10]:25, "
    "delay=1.4, delays=0.9/0/0.2/0.3, dsn=2.0.0, status=sent (250 2.0.0 Ok: queued as 7F2A1)",
    f"Nov 11 10:00:03 mailfilter01 postfix/qmgr[103]: {QA}: removed",
]


def test_inbound_delivered():
    t, msgs = run(INBOUND)
    m = msgs[QA]
    assert m.direction == "inbound"
    assert m.status == "delivered"
    assert m.client_host == "mail-ej1.google.com" and m.client_ip == "2a00:1450:4864::42c"
    assert m.message_id == "CAF1@mail.gmail.com"
    assert m.sender == "jan@gmail.com" and m.size == 4211
    assert m.spam_score == -0.4 and m.spam_action == "no action"
    assert m.symbols["DMARC_POLICY_ALLOW"] == -0.5
    r = m.recipients["info@rhis.nl"]
    assert (r.relay_host, r.relay_ip, r.transport) == ("mail.rhis.nl", "192.0.2.10", "relay")
    assert r.explain is None
    assert m.done


def test_outbound_deferred_then_bounced_with_explanation():
    _, msgs = run([
        f"Nov 11 11:00:00 mailfilter01 postfix/submission/smtpd[201]: {QB}: client=web01.rhis.nl[198.51.100.7], "
        "sasl_method=PLAIN, sasl_username=web01@rhis.nl",
        f"Nov 11 11:00:00 mailfilter01 postfix/qmgr[103]: {QB}: from=<noreply@rhis.nl>, size=1200, nrcpt=1 (queue active)",
        f"Nov 11 11:00:05 mailfilter01 postfix/smtp[205]: {QB}: to=<piet@outlook.com>, "
        "relay=outlook-com.olc.protection.outlook.com[52.101.40.0]:25, delay=5, delays=0.1/0/5/0, dsn=4.4.1, "
        "status=deferred (connect to outlook-com.olc.protection.outlook.com[52.101.40.0]:25: Connection timed out)",
    ])
    m = msgs[QB]
    assert m.direction == "outbound" and m.sasl_user == "web01@rhis.nl" and m.listener == "submission"
    assert m.status == "deferred"
    assert m.recipients["piet@outlook.com"].explain == "server_unreachable"

    _, msgs = run([
        f"Nov 11 11:10:00 mailfilter01 postfix/smtp[205]: {QB}: to=<piet@outlook.com>, "
        "relay=outlook-com.olc.protection.outlook.com[52.101.40.0]:25, delay=600, delays=595/0/0.5/4, dsn=5.7.1, "
        "status=bounced (host outlook-com.olc.protection.outlook.com[52.101.40.0] said: 550 5.7.1 Unfortunately, "
        "messages from [203.0.113.5] weren't sent. Our IP is listed on Spamhaus (in reply to MAIL FROM command))",
    ])
    assert msgs[QB].status == "bounced"
    assert msgs[QB].recipients["piet@outlook.com"].explain == "our_ip_blocked"


def test_quarantine_hold():
    _, msgs = run([
        f"Nov 11 12:00:00 mailfilter01 postfix/cleanup[102]: {QA}: milter-hold: END-OF-MESSAGE from "
        "unknown[203.0.113.9]: milter triggers HOLD action; from=<promo@deals.example> to=<info@rhis.nl> "
        "proto=ESMTP helo=<deals.example>",
    ])
    m = msgs[QA]
    assert m.status == "quarantined"
    assert m.recipients["info@rhis.nl"].explain == "quarantine"


def test_outbound_pause_hold_is_not_quarantine():
    _, msgs = run([
        f"Nov 11 12:05:00 mailfilter01 postfix/submission/smtpd[401]: {QB}: client=web01.rhis.nl[198.51.100.7], "
        "sasl_method=PLAIN, sasl_username=web01@rhis.nl",
        f"Nov 11 12:05:01 mailfilter01 postfix/submission/smtpd[401]: {QB}: hold: END-OF-MESSAGE from "
        "web01.rhis.nl[198.51.100.7]: Paused by Mailfilter: 400 messages in 5 minutes; "
        "from=<noreply@rhis.nl> to=<x@gmail.com> proto=ESMTP helo=<web01.rhis.nl>",
    ])
    m = msgs[QB]
    assert m.status == "held" and m.direction == "outbound"
    assert m.client_ip == "198.51.100.7" and m.sasl_user == "web01@rhis.nl"
    r = m.recipients["x@gmail.com"]
    assert r.status == "held" and r.explain == "outbound_paused"
    assert r.reply.startswith("Paused by Mailfilter")
    assert explain.text("outbound_paused", "nl")["title"].startswith("Vastgehouden")


def test_postscreen_and_smtpd_rejects_get_made_up_ids():
    t, msgs = run([
        "Nov 11 13:00:00 mailfilter01 postfix/postscreen[50]: NOQUEUE: reject: RCPT from [203.0.113.66]:51122: "
        "550 5.7.1 Service unavailable; client [203.0.113.66] blocked using zen.spamhaus.org; "
        "from=<a@spam.example>, to=<info@rhis.nl>, proto=ESMTP, helo=<x>",
        "Nov 11 13:00:01 mailfilter01 postfix/smtpd[101]: NOQUEUE: reject: RCPT from unknown[203.0.113.67]: "
        "550 5.1.1 <nobody@rhis.nl>: Recipient address rejected: Recipient address does not exist; "
        "from=<b@example.org> to=<nobody@rhis.nl> proto=ESMTP helo=<example.org>",
        "Nov 11 13:00:02 mailfilter01 postfix/smtpd[101]: NOQUEUE: reject: RCPT from unknown[198.51.100.99]: "
        "454 4.7.1 <x@gmail.com>: Relay access denied; from=<c@example.org> to=<x@gmail.com> proto=ESMTP helo=<h>",
    ])
    kinds = sorted(r.explain for m in msgs.values() for r in m.recipients.values())
    assert kinds == ["rejected_blocklist", "rejected_relay", "rejected_unknown_user"]
    assert all(q.startswith("R") and m.status == "rejected" and m.done for q, m in msgs.items())
    assert not t.open  # rejects never stay in memory


def test_bounce_links_to_parent():
    _, msgs = run([
        f"Nov 11 14:00:00 mailfilter01 postfix/bounce[300]: {QA}: sender non-delivery notification: {QB}",
        f"Nov 11 14:00:00 mailfilter01 postfix/qmgr[103]: {QB}: from=<>, size=3000, nrcpt=1 (queue active)",
    ])
    assert msgs[QB].parent == QA and msgs[QB].is_bounce


def test_restart_reloads_deferred_message():
    first = Tracker()
    for line in [
        f"Nov 11 11:00:00 mailfilter01 postfix/qmgr[103]: {QB}: from=<noreply@rhis.nl>, size=1200, nrcpt=1 (queue active)",
        f"Nov 11 11:00:05 mailfilter01 postfix/smtp[205]: {QB}: to=<piet@outlook.com>, relay=none, delay=5, "
        "delays=0.1/0/5/0, dsn=4.4.1, status=deferred (connect to x[1.2.3.4]:25: Connection refused)",
    ]:
        saved = first.apply(parse_syslog(line, 2026))
    second = Tracker(loader=lambda q: saved if q == QB else None)
    m = second.apply(parse_syslog(
        f"Nov 11 11:40:00 mailfilter01 postfix/smtp[205]: {QB}: to=<piet@outlook.com>, relay=mx.outlook.com[1.2.3.4]:25, "
        "delay=2400, delays=2399/0/0.5/0.5, dsn=2.0.0, status=sent (250 OK)", 2026))
    assert m.sender == "noreply@rhis.nl" and m.status == "delivered"
    assert m.recipients["piet@outlook.com"].attempts == 2


def test_journal_entry():
    ev = parse_journal({
        "__REALTIME_TIMESTAMP": "1762858800000000", "SYSLOG_IDENTIFIER": "postfix/qmgr",
        "MESSAGE": f"{QA}: removed", "__CURSOR": "s=1",
    })
    assert ev.kind == "removed" and ev.qid == QA
    assert ev.ts == datetime(2025, 11, 11, 11, 0, tzinfo=UTC)
    assert parse_journal({"MESSAGE": [1, 2]}) is None


def test_unrelated_lines_are_ignored():
    assert parse_syslog("Nov 11 10:00:00 h postfix/smtpd[1]: connect from x[1.2.3.4]") is None
    assert parse_syslog("Nov 11 10:00:00 h sshd[1]: Accepted publickey for root") is None


def test_split_relay():
    assert split_relay("mx.example.com[192.0.2.1]:25") == ("mx.example.com", "192.0.2.1")
    assert split_relay("[2001:db8::1]:25") == ("", "2001:db8::1")
    assert split_relay("none") == ("", None)


def test_explain_catalog():
    cases = {
        ("5.1.1", "550 5.1.1 <x@y>: Recipient address rejected: User unknown"): "user_unknown",
        ("5.4.4", "Host or domain name not found. Name service error for name=gmial.com"): "domain_not_found",
        ("5.2.2", "552 5.2.2 Mailbox full"): "mailbox_full",
        ("5.3.4", "552 5.3.4 Message size exceeds fixed limit"): "too_large",
        ("5.7.26", "550 5.7.26 Unauthenticated email from rhis.nl is not accepted due to DMARC policy"): "spf_dmarc_fail",
        ("4.7.1", "451 4.7.1 Greylisted, please try again later"): "greylisted",
        ("4.7.0", "421 4.7.0 Try again later, closing connection. (rate limit)"): "greylisted",
        ("4.7.28", "421 4.7.28 Our system has detected an unusual rate of unsolicited mail; too many messages"): "rate_limited",
        ("4.3.0", "451 4.3.0 Internal error"): "temporary_other",
        ("5.0.0", "550 Go away"): "permanent_other",
        ("2.0.0", "250 OK"): None,
    }
    for (dsn, reply), want in cases.items():
        assert explain.explain_remote(dsn, reply) == want, reply
    t = explain.text("user_unknown", "nl")
    assert t["title"] == "De mailbox bestaat niet." and t["who"] == "sender"
    assert explain.text("quarantine", "en")["title"].startswith("Held")


def test_every_rule_has_both_languages():
    data = explain.load()
    for rule in data["rules"]:
        for lang in ("en", "nl"):
            assert rule[lang]["title"] and rule[lang]["action"], rule["id"]
    for key, val in data["local"].items():
        assert val["en"]["title"] and val["nl"]["title"], key


def test_per_domain_and_throttled_queues():
    _, msgs = run([
        f"Nov 11 15:00:00 h postfix/domain/rhis.nl/smtp[1]: {QA}: to=<info@rhis.nl>, relay=mail.rhis.nl[192.0.2.10]:25, "
        "delay=1, delays=0.5/0/0.2/0.3, dsn=2.0.0, status=sent (250 OK)",
        f"Nov 11 15:00:00 h postfix/out/outlook.com/smtp[2]: {QB}: to=<a@outlook.com>, relay=mx.outlook.com[192.0.2.20]:25, "
        "delay=1, delays=0.5/0/0.2/0.3, dsn=2.0.0, status=sent (250 OK)",
    ])
    assert msgs[QA].direction == "inbound" and msgs[QA].recipients["info@rhis.nl"].transport == "relay"
    assert msgs[QB].direction == "outbound" and msgs[QB].recipients["a@outlook.com"].transport == "smtp"
