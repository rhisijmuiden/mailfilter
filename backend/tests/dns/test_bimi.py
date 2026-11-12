"""BIMI: SVG Tiny PS check and converter, mark certificates, DMARC prerequisite, record."""

from __future__ import annotations

import base64
import datetime as dt
import gzip

import pytest

from mailfilter_dns import bimi, publish
from mailfilter_dns.resolver import StaticResolver

GOOD = (b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny-ps" viewBox="0 0 100 100">'
        b'<title>RHIS</title><rect width="100" height="100" fill="#0b3d91"/><circle cx="50" cy="50" r="30" fill="#fff"/></svg>')

ILLUSTRATOR = b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     id="Laag_1" x="0px" y="0px" width="200px" height="100px" viewBox="0 0 200 100" inkscape:version="1.3">
  <defs><style>.cls-1{fill:#e30613;}.cls-2,.cls-3{fill:#fff;stroke:#000}</style></defs>
  <inkscape:namedview id="nv"/>
  <a href="https://example.org"><rect class="cls-1" width="200" height="100"/></a>
  <path class="cls-2" style="opacity:0.5;cursor:pointer" d="M10 10h50v50z" onclick="alert(1)"/>
  <script>alert(1)</script>
</svg>"""


def test_good_logo_passes():
    rep = bimi.check_svg(GOOD)
    assert rep.ok, rep.errors
    assert rep.title == "RHIS"


@pytest.mark.parametrize("svg,code", [
    (GOOD.replace(b' version="1.2"', b''), "version"),
    (GOOD.replace(b'tiny-ps', b'tiny'), "base_profile"),
    (GOOD.replace(b'<title>RHIS</title>', b''), "no_title"),
    (GOOD.replace(b'0 0 100 100', b'0 0 200 100'), "not_square"),
    (GOOD.replace(b'<title>', b'<script>x</script><title>'), "element:script"),
    (GOOD.replace(b'<title>', b'<image href="https://x/y.png"/><title>'), "element:image"),
    (GOOD.replace(b'fill="#fff"', b'fill="url(https://x/#g)"'), "external_ref"),
    (GOOD.replace(b'<rect ', b'<rect onload="x()" '), "script_attr"),
    (GOOD.replace(b'<rect ', b'<rect style="fill:red" '), "style_attr"),
    (b'<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + GOOD.split(b"?>", 1)[1], "doctype"),
    (b"not xml", "not_xml"),
    (b'<html xmlns="http://www.w3.org/1999/xhtml"/>', "not_svg"),
])
def test_bad_logo(svg, code):
    rep = bimi.check_svg(svg)
    assert not rep.ok
    assert code in rep.errors


def test_too_large():
    big = GOOD.replace(b"</svg>", b"<desc>" + b"x" * 40000 + b"</desc></svg>")
    assert "too_large:39" in bimi.check_svg(big).errors


def test_convert_ordinary_logo():
    out, changes = bimi.convert_svg(ILLUSTRATOR, "Example BV")
    rep = bimi.check_svg(out)
    assert rep.ok, (rep.errors, out)
    assert rep.title == "Example BV"
    for c in ("inlined_css", "removed_editor_data", "removed_script", "removed_link", "made_square", "set_profile",
              "removed_root_xy", "added_title"):
        assert c in changes
    text = out.decode()
    assert 'viewBox="0 -50 200 200"' in text
    assert 'fill="#e30613"' in text and 'stroke="#000"' in text and 'opacity="0.5"' in text
    assert "cursor" not in text and "script" not in text and "inkscape" not in text and "https://" not in text


def test_convert_keeps_a_good_logo():
    out, changes = bimi.convert_svg(GOOD, "x")
    assert changes == []
    assert bimi.check_svg(out).ok


@pytest.mark.parametrize("extra,code", [
    (b'<image href="data:image/png;base64,AAAA"/>', "cannot_convert:image"),
    (b'<filter id="f"/>', "cannot_convert:filter"),
    (b'<use href="https://x/y.svg#a"/>', "cannot_convert:external_ref"),
])
def test_convert_refuses_what_changes_the_picture(extra, code):
    with pytest.raises(ValueError, match=code):
        bimi.convert_svg(GOOD.replace(b"</svg>", extra + b"</svg>"), "x")


def test_convert_refuses_complex_css():
    svg = GOOD.replace(b"<title>", b"<style>rect:hover{fill:red}</style><title>")
    with pytest.raises(ValueError, match="cannot_convert:style"):
        bimi.convert_svg(svg, "x")


# --- certificates ----------------------------------------------------------------------

def _vmc(domain="rhis.nl", logo=GOOD, kind=bimi.OID_VMC, days=365, eku=True, chain=True):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Mark CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=900))
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    data_url = b"data:image/svg+xml;base64," + base64.b64encode(gzip.compress(logo)) if logo else b""
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RHIS"),
                                  x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
         .issuer_name(ca_name).public_key(key.public_key()).serial_number(2)
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), False)
         .add_extension(x509.CertificatePolicies([x509.PolicyInformation(x509.ObjectIdentifier(kind), None)]), False))
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage([x509.ObjectIdentifier(bimi.OID_EKU_BIMI)]), False)
    if logo:
        # Real certificates wrap this in ASN.1 (RFC 3709); only the data URL matters here
        b = b.add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier(bimi.OID_LOGOTYPE), b"\x30\x00" + data_url), False)
    pem = b.sign(ca_key, hashes.SHA256()).public_bytes(Encoding.PEM)
    return pem + (ca.public_bytes(Encoding.PEM) if chain else b"")


def test_vmc_ok_and_logo_extracted():
    pem = _vmc()
    assert bimi.logo_from_cert(pem) == GOOD
    rep = bimi.check_cert(pem, "rhis.nl", GOOD)
    assert rep.ok, rep.errors
    assert rep.kind == "vmc" and rep.organization == "RHIS" and rep.issuer == "Test Mark CA"
    assert rep.logo_matches is True and rep.chain == 2
    assert bimi.check_cert(pem, "mail.rhis.nl").ok  # subdomain covered


def test_cmc_and_problems():
    rep = bimi.check_cert(_vmc(kind=bimi.OID_CMC, eku=False, logo=None, chain=False, days=10), "other.nl", GOOD)
    assert rep.kind == "cmc"
    assert "no_bimi_eku" in rep.errors and "no_logo" in rep.errors
    assert any(e.startswith("domain_mismatch") for e in rep.errors)
    assert "no_chain" in rep.warnings and any(w.startswith("expires_soon") for w in rep.warnings)
    rep = bimi.check_cert(_vmc(), "rhis.nl", GOOD.replace(b"#fff", b"#000"))
    assert rep.ok and rep.logo_matches is False and "logo_differs" in rep.warnings
    assert bimi.check_cert(b"garbage", "rhis.nl").errors == ["not_pem"]


# --- DMARC, record, storage -------------------------------------------------------------------

@pytest.mark.parametrize("rec,ok,problem", [
    ("v=DMARC1; p=reject", True, None),
    ("v=DMARC1; p=quarantine; rua=mailto:x@rhis.nl", True, None),
    ("v=DMARC1; p=none", False, "dmarc_not_enforced:none"),
    ("v=DMARC1; p=quarantine; pct=50", False, "dmarc_pct:50"),
    ("v=DMARC1; p=reject; sp=none", False, "dmarc_sp_none"),
    (None, False, "dmarc_missing"),
])
def test_dmarc_prerequisite(rec, ok, problem):
    r = StaticResolver({("_dmarc.rhis.nl", "TXT"): [rec] if rec else []})
    out = bimi.dmarc_check("rhis.nl", r)
    assert out["ok"] is ok
    if problem:
        assert problem in out["problems"]


def test_dmarc_subdomain_uses_sp_of_parent():
    r = StaticResolver({("_dmarc.rhis.nl", "TXT"): ["v=DMARC1; p=reject; sp=none"]})
    out = bimi.dmarc_check("news.rhis.nl", r)
    assert out["at"] == "rhis.nl" and out["policy"] == "none" and not out["ok"]


def test_record_and_status(tmp_path):
    host = "mailfilter.rhis.nl"
    assert bimi.record_value(host, "rhis.nl", False) == "v=BIMI1; l=https://mailfilter.rhis.nl/api/bimi/rhis.nl.svg;"
    rec = bimi.record_value(host, "rhis.nl", True)
    assert rec.endswith("a=https://mailfilter.rhis.nl/api/bimi/rhis.nl.pem")
    r = StaticResolver({("_dmarc.rhis.nl", "TXT"): ["v=DMARC1; p=reject"], ("default._bimi.rhis.nl", "TXT"): [rec]})
    st = bimi.status("rhis.nl", host, r, tmp_path)
    assert st["logo"] is None and st["level"] == "none" and st["record_status"] == "different"
    bimi.write_file("rhis.nl", "svg", GOOD, tmp_path)
    st = bimi.status("rhis.nl", host, r, tmp_path)
    assert st["record_status"] == "different"  # the record names a certificate that is not there
    bimi.write_file("rhis.nl", "pem", _vmc(), tmp_path)
    st = bimi.status("rhis.nl", host, r, tmp_path)
    assert st["record_status"] == "ok" and st["level"] == "full", st
    bimi.write_file("rhis.nl", "pem", None, tmp_path)
    assert bimi.read_file("rhis.nl", "pem", tmp_path) is None
    with pytest.raises(ValueError):
        bimi.write_file("../etc", "svg", GOOD, tmp_path)


def test_plan_bimi_replaces_old_record():
    from fakecf import FakeCloudflare

    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "TXT", "name": "default._bimi.rhis.nl", "content": "v=BIMI1; l=https://old/logo.svg"},
        {"type": "TXT", "name": "default._bimi.rhis.nl", "content": "v=BIMI1; l=https://older/logo.svg"},
    ])
    cf = fake.client()
    zone = cf.zone_for("rhis.nl")
    changes = publish.plan_bimi(cf, zone, "rhis.nl", "v=BIMI1; l=https://new/logo.svg;")
    assert [c.action for c in changes] == ["update", "delete"]
    changes = publish.plan_bimi(cf, zone, "news.rhis.nl", "v=BIMI1; l=x;")
    assert [c.action for c in changes] == ["create"] and changes[0].name == "default._bimi.news.rhis.nl"
