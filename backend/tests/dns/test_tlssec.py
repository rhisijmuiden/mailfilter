"""MTA-STS policy store, TLS-RPT values and DANE (TLSA) helpers."""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from mailfilter_dns import tlssec
from mailfilter_dns.cloudflare import DnsRecord
from mailfilter_dns.resolver import StaticResolver


def _cert(tmp_path, host="mailfilter.rhis.nl"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, host)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=90)).sign(key, hashes.SHA256()))
    d = tmp_path / host
    d.mkdir()
    (d / "cert.pem").write_bytes(cert.public_bytes(Encoding.PEM))
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def test_local_tlsa_is_spki_hash(tmp_path):
    h = _cert(tmp_path)
    assert tlssec.local_tlsa("mailfilter.rhis.nl", tmp_path) == f"3 1 1 {h}"
    assert tlssec.local_tlsa("other.rhis.nl", tmp_path) is None


def test_policy_store_keeps_id_until_policy_changes(tmp_path):
    f = tmp_path / "sts.json"
    p1 = tlssec.save_policy("RHIS.nl", "testing", ["mailfilter.rhis.nl", "mailfilter.rhis.nl."], 86400, f)
    assert p1.mx == ["mailfilter.rhis.nl"] and p1.txt == f"v=STSv1; id={p1.id}"
    assert tlssec.save_policy("rhis.nl", "testing", ["mailfilter.rhis.nl"], 86400, f).id == p1.id
    p2 = tlssec.save_policy("rhis.nl", "enforce", ["mailfilter.rhis.nl"], 604800, f)
    assert p2.id > p1.id and tlssec.get_policy("rhis.nl", f).mode == "enforce"
    with pytest.raises(ValueError, match="invalid_mode"):
        tlssec.save_policy("rhis.nl", "strict", ["a.rhis.nl"], 86400, f)
    with pytest.raises(ValueError, match="no_mx"):
        tlssec.save_policy("rhis.nl", "enforce", [], 86400, f)
    assert tlssec.policy_for_host("MTA-STS.rhis.nl:443", lambda d: True, f).mode == "enforce"
    assert tlssec.policy_for_host("rhis.nl", lambda d: True, f) is None
    assert tlssec.policy_for_host("mta-sts.rhis.nl", lambda d: False, f) is None


def test_mx_patterns():
    assert tlssec.mx_matches("mx1.rhis.nl", ["*.rhis.nl"])
    assert not tlssec.mx_matches("a.b.rhis.nl", ["*.rhis.nl"])  # a wildcard covers one label
    assert tlssec.mx_matches("MAILFILTER.rhis.nl.", ["mailfilter.rhis.nl"])
    pol = tlssec.parse_policy("version: STSv1\r\nmode: enforce\r\nmx: mailfilter.rhis.nl\r\nmax_age: 604800\r\n")
    assert pol == {"version": "STSv1", "mode": "enforce", "mx": ["mailfilter.rhis.nl"], "max_age": "604800"}


def test_tlsrpt_value():
    assert tlssec.tlsrpt_value(["a@rhis.nl", "mailto:b@rhis.nl", "https://r.example/tls"]) == \
        "v=TLSRPTv1; rua=mailto:a@rhis.nl,mailto:b@rhis.nl,https://r.example/tls"
    with pytest.raises(ValueError, match="invalid_email"):
        tlssec.tlsrpt_value(["nope"])
    with pytest.raises(ValueError, match="no_rua"):
        tlssec.tlsrpt_value([])


def test_dane_status():
    exp = "3 1 1 " + "AB" * 32
    r = StaticResolver({("_25._tcp.mailfilter.rhis.nl", "TLSA"): [exp.lower()],
                        ("mailfilter.rhis.nl", "SECURE"): True, ("rhis.nl", "SECURE"): True})
    assert tlssec.dane_status("rhis.nl", "mailfilter.rhis.nl", r, expected=exp).status == "ok"
    st = tlssec.dane_status("klant.nl", "mailfilter.rhis.nl", r, expected="3 1 1 " + "cd" * 32)
    assert st.status == "bad" and st.problems == ["dnssec_off:klant.nl", "tlsa_mismatch"]
    assert tlssec.dane_status("rhis.nl", "mailfilter.rhis.nl", StaticResolver(), expected=exp).status == "missing"


def test_tlsa_record_body():
    rec = DnsRecord("TLSA", "_25._tcp.mailfilter.rhis.nl", "3 1 1 ABCD")
    assert rec.body()["data"] == {"usage": 3, "selector": 1, "matching_type": 1, "certificate": "abcd"}
    assert "content" not in rec.body()
    back = DnsRecord.from_api({"type": "TLSA", "name": "_25._tcp.mailfilter.rhis.nl",
                               "data": {"usage": 3, "selector": 1, "matching_type": 1, "certificate": "ABCD"}})
    assert back.content == "3 1 1 abcd"
