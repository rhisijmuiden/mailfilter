"""Runs against a real PostgreSQL when MAILFILTER_TEST_DB is set (CI does this)."""
import os

import pytest

from mailfilter_tracer.parser import parse_syslog
from mailfilter_tracer.tracker import Tracker

DSN = os.environ.get("MAILFILTER_TEST_DB")
pytestmark = pytest.mark.skipif(not DSN, reason="MAILFILTER_TEST_DB not set")

Q = "4ZxY1q3H7nz9b2"


def test_save_load_roundtrip():
    from mailfilter_tracer.store import Store
    store = Store(DSN)
    store.migrate()
    store.migrate()  # running twice is fine
    t = Tracker()
    msg = None
    for line in [
        f"Nov 11 11:00:00 h postfix/submission/smtpd[1]: {Q}: client=web01[2001:db8::7], sasl_method=PLAIN, sasl_username=web01",
        f"Nov 11 11:00:00 h postfix/qmgr[2]: {Q}: from=<noreply@rhis.nl>, size=1200, nrcpt=1 (queue active)",
        f"Nov 11 11:00:05 h postfix/smtp[3]: {Q}: to=<piet@outlook.com>, relay=mx.outlook.com[192.0.2.5]:25, delay=5, "
        "delays=0.1/0/5/0, dsn=4.4.1, status=deferred (connect to mx.outlook.com[192.0.2.5]:25: Connection timed out)",
    ]:
        msg = t.apply(parse_syslog(line, 2026))
    store.save([msg], cursor="s=abc")
    assert store.cursor() == "s=abc"
    back = store.load(Q)
    assert back.sender == "noreply@rhis.nl" and back.client_ip == "2001:db8::7"
    assert back.status == "deferred"
    r = back.recipients["piet@outlook.com"]
    assert r.explain == "server_unreachable" and r.relay_ip == "192.0.2.5" and r.attempts == 1

    t2 = Tracker(loader=store.load)
    m2 = t2.apply(parse_syslog(
        f"Nov 11 11:40:00 h postfix/smtp[3]: {Q}: to=<piet@outlook.com>, relay=mx.outlook.com[192.0.2.5]:25, "
        "delay=2400, delays=2399/0/0.5/0.5, dsn=2.0.0, status=sent (250 OK)", 2026))
    store.save([m2])
    row = store.conn.execute("SELECT status, direction FROM trace_message WHERE queue_id = %s", [Q]).fetchone()
    assert row == ("delivered", "outbound")
    assert store.load(Q).recipients["piet@outlook.com"].attempts == 2
