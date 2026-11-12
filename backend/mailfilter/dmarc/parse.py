"""Read DMARC aggregate reports (RFC 7489 appendix C, and the DMARCbis format).

Reports arrive as mail with the XML attached, usually compressed as .gz or
.zip. Everything here is untrusted input from the internet, so sizes are
capped (no zip bombs) and XML with a DOCTYPE is refused (no entity tricks).
"""

from __future__ import annotations

import email
import email.policy
import gzip
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from xml.etree import ElementTree as ET

MAX_XML = 50 * 1024 * 1024  # uncompressed size of one report
MAX_FILES = 20  # report files in one mail or zip


class ReportError(ValueError):
    """Not a usable DMARC report; the message is a stable code plus detail."""


@dataclass
class Row:
    source_ip: str
    count: int
    disposition: str = "none"
    dkim: str = ""
    spf: str = ""
    header_from: str = ""
    envelope_from: str = ""
    dkim_auth: list[dict] = field(default_factory=list)
    spf_auth: list[dict] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.dkim == "pass" or self.spf == "pass"


@dataclass
class Report:
    org_name: str
    org_email: str
    report_id: str
    domain: str
    begin: datetime
    end: datetime
    p: str = ""
    sp: str = ""
    pct: int | None = None
    adkim: str = "r"
    aspf: str = "r"
    rows: list[Row] = field(default_factory=list)

    @property
    def messages(self) -> int:
        return sum(r.count for r in self.rows)


# --- files ------------------------------------------------------------------------

def _read_limited(f, name: str) -> bytes:
    data = f.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise ReportError(f"too_large:{name}")
    return data


def unpack(name: str, data: bytes) -> list[tuple[str, bytes]]:
    """A report file (.xml, .xml.gz, .zip) -> [(name, xml bytes)]."""
    lname = name.lower()
    if data[:2] == b"\x1f\x8b" or lname.endswith((".gz", ".gzip")):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
                return [(name.rsplit(".", 1)[0], _read_limited(g, name))]
        except (OSError, EOFError) as exc:
            raise ReportError(f"bad_archive:{name}") from exc
    if data[:4] == b"PK\x03\x04" or lname.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                members = [i for i in z.infolist() if not i.is_dir()][:MAX_FILES]
                out = []
                for info in members:
                    if info.file_size > MAX_XML:
                        raise ReportError(f"too_large:{info.filename}")
                    with z.open(info) as f:
                        out.append((info.filename, _read_limited(f, info.filename)))
                return out
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise ReportError(f"bad_archive:{name}") from exc
    return [(name, data)]


REPORT_TYPES = {"application/zip", "application/x-zip-compressed", "application/gzip", "application/x-gzip",
                "application/xml", "text/xml", "application/x-zip", "application/tlsrpt+gzip"}


def attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Report files in a mail: attachments, or a body that is the report itself."""
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        name = part.get_filename() or ""
        lname = name.lower()
        if ctype in REPORT_TYPES or lname.endswith((".xml", ".gz", ".zip")) or \
                (ctype == "application/octet-stream" and lname):
            payload = part.get_payload(decode=True)
            if payload:
                out.append((name or f"report.{ctype.split('/')[-1]}", payload))
    return out[:MAX_FILES]


def parse_mail(raw: bytes) -> tuple[Message, list[Report], list[str]]:
    """Returns the message, the reports in it and an error code per bad file."""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    reports, errors = [], []
    for name, data in attachments(msg):
        try:
            for xname, xml in unpack(name, data):
                reports.append(parse_xml(xml, xname))
        except ReportError as exc:
            errors.append(str(exc))
    return msg, reports, errors


def parse_file(name: str, data: bytes) -> list[Report]:
    """A file uploaded in the UI: a report (.xml/.gz/.zip) or a saved mail (.eml)."""
    if name.lower().endswith(".eml") or data.startswith(b"From ") or re.match(rb"[A-Za-z][A-Za-z0-9-]{1,60}: ", data[:100]):
        _, reports, errors = parse_mail(data)
        if not reports:
            raise ReportError(errors[0] if errors else "no_report")
        return reports
    return [parse_xml(xml, n) for n, xml in unpack(name, data)]


# --- XML ------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        el.tag = _local(el.tag)
    return root


def _text(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else default


def _ts(value: str, name: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(float(value)), tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ReportError(f"bad_date:{name}") from exc


def _lower(v: str) -> str:
    return v.strip().lower().rstrip(".")


def parse_xml(data: bytes, name: str = "report.xml") -> Report:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ReportError(f"unsafe_xml:{name}")
    try:
        root = _strip_ns(ET.fromstring(data))
    except ET.ParseError as exc:
        raise ReportError(f"not_xml:{name}") from exc
    if root.tag != "feedback":
        raise ReportError(f"not_dmarc:{name}")
    meta = root.find("report_metadata")
    pol = root.find("policy_published")
    domain = _lower(_text(pol, "domain"))
    report_id = _text(meta, "report_id")
    org = _text(meta, "org_name")
    if not domain or not report_id or not org:
        raise ReportError(f"incomplete:{name}")
    pct = _text(pol, "pct")
    rep = Report(
        org_name=org[:255], org_email=_text(meta, "email")[:320], report_id=report_id[:255], domain=domain[:253],
        begin=_ts(_text(meta, "date_range/begin", "0"), name), end=_ts(_text(meta, "date_range/end", "0"), name),
        p=_lower(_text(pol, "p"))[:12], sp=_lower(_text(pol, "sp"))[:12],
        pct=int(pct) if pct.isdigit() else None,
        adkim=(_lower(_text(pol, "adkim", "r")) or "r")[:1], aspf=(_lower(_text(pol, "aspf", "r")) or "r")[:1],
    )
    for rec in root.findall("record"):
        row = rec.find("row")
        ev = rec.find("row/policy_evaluated")
        ip = _text(row, "source_ip")
        count = _text(row, "count", "0")
        if not ip or not count.isdigit():
            continue
        r = Row(
            source_ip=ip[:45], count=int(count),
            disposition=(_lower(_text(ev, "disposition", "none")) or "none")[:12],
            dkim=_lower(_text(ev, "dkim"))[:10], spf=_lower(_text(ev, "spf"))[:10],
            header_from=_lower(_text(rec, "identifiers/header_from"))[:253],
            envelope_from=_lower(_text(rec, "identifiers/envelope_from"))[:253],
        )
        if ev is not None:
            r.reasons = [{"type": _lower(_text(x, "type")), "comment": _text(x, "comment")[:300]}
                         for x in ev.findall("reason")][:10]
        r.dkim_auth = [{"domain": _lower(_text(x, "domain")), "selector": _text(x, "selector")[:100],
                        "result": _lower(_text(x, "result"))} for x in rec.findall("auth_results/dkim")][:10]
        r.spf_auth = [{"domain": _lower(_text(x, "domain")), "scope": _lower(_text(x, "scope")),
                       "result": _lower(_text(x, "result"))} for x in rec.findall("auth_results/spf")][:10]
        rep.rows.append(r)
    return rep
