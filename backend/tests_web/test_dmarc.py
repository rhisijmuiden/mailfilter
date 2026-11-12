"""DMARC reports: parsing (also compressed and in mail), storing, the
dashboard per domain, customer scoping, advice and the Cloudflare kinds."""

from __future__ import annotations

import gzip
import io
import sys
import time
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "dns"))

from fakecf import FakeCloudflare  # noqa: E402

from mailfilter.api import dmarc as dmarc_api  # noqa: E402
from mailfilter.api import dns as dns_api  # noqa: E402
from mailfilter.db import session  # noqa: E402
from mailfilter.dmarc import advice, ingest, parse  # noqa: E402
from mailfilter.models import DmarcIngest, DmarcReport  # noqa: E402
from mailfilter_dns import publish  # noqa: E402
from mailfilter_dns.config import ServerConfig  # noqa: E402
from mailfilter_dns.resolver import StaticResolver  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "google.xml"
SERVER = ServerConfig("mailfilter.rhis.nl", ["203.0.113.10"], [], "_spf.mailfilter.rhis.nl")
TOKEN = "good-token-0123456789abcdef"


def xml(domain="rhis.nl", report_id="1234567890", days_ago=1, org="google.com") -> bytes:
    begin = int(time.time()) - days_ago * 86400
    return (FIX.read_text().replace("BEGIN", str(begin)).replace("END", str(begin + 86399))
            .replace("<domain>rhis.nl</domain><adkim>", f"<domain>{domain}</domain><adkim>")
            .replace("1234567890", report_id).replace("<org_name>google.com", f"<org_name>{org}")).encode()


def report_mail(payload: bytes, filename: str, ctype: str) -> bytes:
    m = EmailMessage()
    m["From"] = "noreply-dmarc-support@google.com"
    m["To"] = "dmarc-reports@mailfilter.rhis.nl"
    m["Subject"] = "Report domain: rhis.nl Submitter: google.com Report-ID: 1234567890"
    m.set_content("This is an aggregate report from google.com.")
    maintype, subtype = ctype.split("/")
    m.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return m.as_bytes()


RESOLVER = StaticResolver({
    ("_dmarc.rhis.nl", "TXT"): ["v=DMARC1; p=none; rua=mailto:dmarc-reports@mailfilter.rhis.nl"],
    ("198.51.100.77", "PTR"): ["mail77.sea31.mcsv.net"],
    ("mailfilter.rhis.nl", "A"): ["203.0.113.10"],
})


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(dmarc_api, "get_resolver", lambda: RESOLVER)
    monkeypatch.setattr(dmarc_api, "get_server", lambda: SERVER)
    monkeypatch.setattr(ingest, "_resolver", lambda: RESOLVER)
    fake = FakeCloudflare(["rhis.nl", "klant.nl"], [
        {"type": "TXT", "name": "_dmarc.rhis.nl", "content": "v=DMARC1; p=none; rua=mailto:x@dmarcian.com"},
    ])

    def make_cf(token):
        cf = fake.client()
        cf._headers["Authorization"] = "Bearer good" if token == TOKEN else "Bearer wrong"
        return cf

    monkeypatch.setattr(dns_api, "make_cloudflare", make_cf)
    monkeypatch.setattr(dns_api, "get_server", lambda: SERVER)
    monkeypatch.setattr(publish, "JOURNAL", tmp_path / "journal.jsonl")
    return fake


# --- parsing ------------------------------------------------------------------------

def test_parse_xml_gzip_zip_and_dmarcbis_namespace():
    rep = parse.parse_xml(xml())
    assert (rep.org_name, rep.domain, rep.p, rep.messages) == ("google.com", "rhis.nl", "none", 162)
    assert rep.rows[1].envelope_from == "bounce.mcsv.net" and rep.rows[1].dkim_auth[0]["domain"] == "mcsv.net"
    assert parse.unpack("r.xml.gz", gzip.compress(xml()))[0][1] == xml()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("google.com!rhis.nl!1!2.xml", xml())
    assert parse.parse_file("r.zip", buf.getvalue())[0].report_id == "1234567890"
    bis = xml().replace(b"<feedback>", b'<feedback xmlns="urn:ietf:params:xml:ns:dmarc-2.0">')
    assert parse.parse_xml(bis).domain == "rhis.nl"


def test_parse_refuses_unsafe_and_wrong_input():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feedback>&a;</feedback>'
    with pytest.raises(parse.ReportError, match="unsafe_xml"):
        parse.parse_xml(bomb)
    with pytest.raises(parse.ReportError, match="not_dmarc"):
        parse.parse_xml(b"<report/>")
    with pytest.raises(parse.ReportError, match="bad_archive"):
        parse.unpack("x.gz", b"\x1f\x8bnot really")
    big = gzip.compress(b"<" + b"a" * (parse.MAX_XML + 10))
    with pytest.raises(parse.ReportError, match="too_large"):
        parse.unpack("big.gz", big)


def test_ingest_mail_stores_once_and_logs():
    raw = report_mail(gzip.compress(xml()), "google.com!rhis.nl!1!2.xml.gz", "application/gzip")
    with session() as db:
        log = ingest.ingest_mail(db, raw, resolver=RESOLVER)
        assert (log.status, log.reports, log.domains) == ("ok", 1, ["rhis.nl"])
        assert ingest.ingest_mail(db, raw, resolver=RESOLVER).status == "duplicate"
        assert ingest.ingest_mail(db, b"Subject: hi\n\nspam", resolver=RESOLVER).status == "ignored"
        bad = report_mail(b"<oops", "x.xml", "text/xml")
        assert ingest.ingest_mail(db, bad, resolver=RESOLVER).status == "error"
        rep = db.query(DmarcReport).one()
        hosts = {r.source_ip: r.source_host for r in rep.rows}
        assert hosts["198.51.100.77"] == "mail77.sea31.mcsv.net" and hosts["192.0.2.66"] == ""
        assert db.query(DmarcIngest).count() == 4


def test_pipe_command(monkeypatch, capsys):
    raw = report_mail(xml(), "report.xml", "text/xml")
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(raw)})())
    monkeypatch.setattr(ingest, "_resolver", lambda: RESOLVER)
    assert ingest.main(["noreply-dmarc-support@google.com"]) == 0
    assert "ok 1 new rhis.nl" in capsys.readouterr().out


# --- API ----------------------------------------------------------------------------

def _load(*reports):
    with session() as db:
        ingest.store(db, [parse.parse_xml(r) for r in reports], resolver=RESOLVER)
        db.commit()


def test_overview_and_domain_detail(admin, env):
    _load(xml(days_ago=1), xml(report_id="2", days_ago=2, org="Outlook.com"))
    ov = admin.get("/api/dmarc/overview").json()
    assert ov["rua"] == "dmarc-reports@mailfilter.rhis.nl"
    rhis = next(d for d in ov["domains"] if d["domain"] == "rhis.nl")
    assert (rhis["messages"], rhis["passed"], rhis["reports"], rhis["reporters"], rhis["policy"]) == (324, 240, 2, 2, "none")
    assert {d["domain"] for d in ov["domains"]} == {"rhis.nl", "klant.nl"}

    d = admin.get("/api/dmarc/domain/rhis.nl").json()
    assert d["has_rua"] is True and d["tags"]["p"] == "none"
    senders = {s["name"]: s for s in d["senders"]}
    assert senders["Mailfilter"]["verdict"] == "ok" and senders["Mailfilter"]["kind"] == "own"
    assert senders["Mailchimp"]["verdict"] == "fix"  # known service, not aligned: must be fixed before p=quarantine
    assert senders["192.0.2.66"]["verdict"] == "suspect"
    assert d["advice"]["code"] == "fix_senders" and "Mailchimp" in d["advice"]["params"]["senders"]
    assert sum(x["passed"] + x["failed"] for x in d["series"]) == 324 and len(d["series"]) == 30
    rep_id = d["reports"][0]["id"]
    rows = admin.get(f"/api/dmarc/report/{rep_id}").json()["rows"]
    assert rows[0]["sender"] == "Mailfilter"


def test_customer_sees_only_own_domains(customer, env):
    _load(xml(), xml(domain="klant.nl", report_id="k1"))
    ov = customer.get("/api/dmarc/overview").json()
    assert [d["domain"] for d in ov["domains"]] == ["klant.nl"]
    assert customer.get("/api/dmarc/domain/rhis.nl").status_code == 404
    assert customer.get("/api/dmarc/domain/klant.nl").json()["advice"]["action"] is None
    with session() as db:
        rhis_id = db.query(DmarcReport).filter_by(domain="rhis.nl").one().id
    assert customer.get(f"/api/dmarc/report/{rhis_id}").status_code == 404
    assert customer.get("/api/dmarc/setup").status_code == 403
    assert customer.post("/api/dmarc/upload", content=xml()).status_code == 403


def test_upload_and_setup(admin, env):
    r = admin.post("/api/dmarc/upload?name=r.xml.gz", content=gzip.compress(xml()), headers={"Content-Type": "application/octet-stream"})
    assert r.json() == {"status": "ok", "detail": "", "domains": ["rhis.nl"], "reports": 1}
    assert admin.post("/api/dmarc/upload?name=x.xml", content=b"<nope/>").json()["status"] == "error"
    s = admin.get("/api/dmarc/setup").json()
    assert s["checks"] == {"address": True, "auth": False}
    assert [x["status"] for x in s["log"]] == ["error", "ok"]


def test_publish_rua_policy_and_auth(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    plan = admin.post("/api/dns/publish/plan", json={"kind": "dmarc_rua", "domain": "rhis.nl"}).json()
    assert plan["changes"][0]["after"]["content"] == \
        "v=DMARC1; p=none; rua=mailto:x@dmarcian.com,mailto:dmarc-reports@mailfilter.rhis.nl"
    # No record yet: a new p=none record with our address
    plan = admin.post("/api/dns/publish/plan", json={"kind": "dmarc_rua", "domain": "klant.nl"}).json()
    assert plan["changes"][0]["action"] == "create"
    assert plan["changes"][0]["after"]["content"] == "v=DMARC1; p=none; rua=mailto:dmarc-reports@mailfilter.rhis.nl"
    assert admin.post("/api/dns/publish/plan", json={"kind": "dmarc_policy", "domain": "klant.nl", "value": "reject"}).json()["detail"] == "no_dmarc"
    assert admin.post("/api/dns/publish/plan", json={"kind": "dmarc_policy", "domain": "rhis.nl", "value": "bogus"}).status_code == 400
    r = admin.post("/api/dns/publish/apply", json={"kind": "dmarc_policy", "domain": "rhis.nl", "value": "quarantine"})
    assert r.status_code == 200, r.text
    assert env.find("_dmarc.rhis.nl", "TXT")[0]["content"].strip('"') == "v=DMARC1; p=quarantine; rua=mailto:x@dmarcian.com"
    r = admin.post("/api/dns/publish/apply", json={"kind": "dmarc_auth"})
    assert r.status_code == 200, r.text
    assert env.find("*._report._dmarc.mailfilter.rhis.nl", "TXT")[0]["content"].strip('"') == "v=DMARC1"
    again = admin.post("/api/dns/publish/plan", json={"kind": "dmarc_auth"}).json()
    assert again["changes"][0]["action"] == "none"


# --- advice -----------------------------------------------------------------------------

def _g(name, kind, messages, passed):
    return advice.Group(name, kind, messages, passed)


@pytest.mark.parametrize("kw, code", [
    (dict(tags=None), "no_record"),
    (dict(has_our_rua=False), "no_rua"),
    (dict(messages=0), "waiting_reports"),
    (dict(days=3), "collecting"),
    (dict(), "ready_quarantine"),
    (dict(groups=[_g("Microsoft 365", "known", 100, 50)]), "fix_senders"),
    (dict(groups=[_g("Microsoft 365", "known", 1000, 995), _g("203.0.113.9", "unknown", 50, 0)]), "ready_quarantine"),
    (dict(tags={"p": "quarantine", "pct": "50"}), "raise_pct"),
    (dict(tags={"p": "quarantine"}, days_at_policy=5), "quarantine_wait"),
    (dict(tags={"p": "quarantine"}, days_at_policy=20), "ready_reject"),
    (dict(tags={"p": "reject", "sp": "none"}), "done_sp"),
    (dict(tags={"p": "reject"}), "done"),
])
def test_advice_road_to_reject(kw, code):
    base = dict(tags={"p": "none"}, record_error=False, has_our_rua=True, messages=500, days=10, days_at_policy=10,
                groups=[_g("Mailfilter", "own", 500, 500)], can_publish=True)
    assert advice.advise(**{**base, **kw})["code"] == code
