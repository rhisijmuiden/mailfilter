"""Parsers for the files inside a PMG backup.

The formats follow PMG's own readers in ``PMG/Config.pm`` (pmg-api), so a file
that PMG accepts is read the same way here. Lines PMG would reject are reported
back instead of silently dropped.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterator

from .model import Transport

ParseResult = tuple[list[dict], list[str]]  # (entries, error lines)


def _lines(text: str) -> Iterator[str]:
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.strip():
            yield line


def parse_domains(text: str) -> ParseResult:
    """``/etc/pmg/domains`` and ``/etc/pmg/dkim/domains``.

    A ``#comment`` line belongs to the domain on the next line, the domain line
    is ``example.com 1``.
    """
    out: list[dict] = []
    errors: list[str] = []
    comment = ""
    for line in _lines(text):
        if line.startswith("#"):
            comment = line[1:].strip()
            continue
        m = re.match(r"^(\S+)\s.*$", line) or re.match(r"^(\S+)$", line)
        if m:
            out.append({"domain": m.group(1).lower(), "comment": comment})
        else:
            errors.append(line)
        comment = ""
    return out, errors


def parse_mynetworks(text: str) -> ParseResult:
    """``/etc/pmg/mynetworks``: ``192.0.2.0/24 #comment`` per line."""
    out: list[dict] = []
    errors: list[str] = []
    for line in _lines(text):
        m = re.match(r"^\s*(\S+?/\d+)\s*(?:#(.*?))?\s*$", line)
        if not m:
            errors.append(line)
            continue
        try:
            net = ipaddress.ip_network(m.group(1), strict=False)
        except ValueError:
            errors.append(line)
            continue
        comment = (m.group(2) or "").strip()
        if comment == "*":  # PMG writes "*" when there is no comment
            comment = ""
        out.append({"cidr": str(net), "comment": comment})
    return out, errors


_TRANSPORT_RE = re.compile(r"^(\S+)\s+(?:(lmtp):inet|(smtp)):(\S+):(\d+)\s*$")


def parse_transport(text: str) -> ParseResult:
    """``/etc/pmg/transport``: ``example.com smtp:[10.0.0.5]:25``.

    Brackets around the host mean "no MX lookup", as in Postfix.
    """
    out: list[dict] = []
    errors: list[str] = []
    comment = ""
    for line in _lines(text):
        if line.startswith("#"):
            comment = line[1:].strip()
            continue
        m = _TRANSPORT_RE.match(line)
        if not m:
            errors.append(line)
            comment = ""
            continue
        match, protocol, host, port = m.group(1), m.group(2) or m.group(3), m.group(4), int(m.group(5))
        use_mx = True
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
            use_mx = False
        if protocol == "lmtp":
            use_mx = False
        if host.lower().startswith("ipv6:"):
            host = host[5:]
        out.append(
            {
                "match": match.lower(),
                "transport": Transport(host=host, port=port, protocol=protocol, use_mx=use_mx, comment=comment),
            }
        )
        comment = ""
    return out, errors


def parse_tls_policy(text: str) -> ParseResult:
    """``/etc/pmg/tls_policy``: ``example.com encrypt``."""
    out: list[dict] = []
    errors: list[str] = []
    for line in _lines(text):
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            out.append({"destination": parts[0].lower(), "policy": " ".join(parts[1:])})
        else:
            errors.append(line)
    return out, errors


def parse_tls_inbound_domains(text: str) -> ParseResult:
    """``/etc/pmg/tls_inbound_domains``: ``example.com reject_plaintext_session``."""
    out: list[dict] = []
    errors: list[str] = []
    for line in _lines(text):
        if line.startswith("#"):
            continue
        m = re.match(r"^(\S+) reject_plaintext_session$", line.strip())
        if m:
            out.append({"domain": m.group(1).lower()})
        else:
            errors.append(line)
    return out, errors


def parse_pmg_conf(text: str) -> dict[str, dict[str, str]]:
    """``/etc/pmg/pmg.conf``: ``section: mail`` followed by indented ``key value`` lines."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^section:\s*(\S+)\s*$", raw)
        if m:
            current = sections.setdefault(m.group(1), {})
            continue
        if current is None or not raw[:1].isspace():
            continue
        parts = raw.strip().split(None, 1)
        current[parts[0]] = parts[1] if len(parts) > 1 else ""
    return sections


# --- PostgreSQL COPY dump (Proxmox_ruledb.sql) -------------------------------------

_COPY_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def _unescape_copy(field: str) -> str | None:
    """Undo PostgreSQL COPY text escaping. ``\\N`` is NULL."""
    if field == r"\N":
        return None
    if "\\" not in field:
        return field
    out = []
    i = 0
    while i < len(field):
        c = field[i]
        if c != "\\" or i + 1 >= len(field):
            out.append(c)
            i += 1
            continue
        n = field[i + 1]
        if n in _COPY_ESCAPES:
            out.append(_COPY_ESCAPES[n])
            i += 2
        elif n in "01234567":
            m = re.match(r"[0-7]{1,3}", field[i + 1 :])
            out.append(chr(int(m.group(0), 8)))
            i += 1 + len(m.group(0))
        elif n == "x" and re.match(r"[0-9a-fA-F]{1,2}", field[i + 2 :]):
            m = re.match(r"[0-9a-fA-F]{1,2}", field[i + 2 :])
            out.append(chr(int(m.group(0), 16)))
            i += 2 + len(m.group(0))
        else:
            out.append(n)
            i += 2
    return "".join(out)


def decode_bytea(value: str | None) -> str | None:
    """A bytea column as text: hex format (``\\x6162``) or the old escape format."""
    if value is None:
        return None
    if value.startswith("\\x"):
        raw = bytes.fromhex(value[2:])
    else:
        buf = bytearray()
        i = 0
        while i < len(value):
            if value[i] == "\\" and value[i + 1 : i + 2] == "\\":
                buf.append(0x5C)
                i += 2
            elif value[i] == "\\" and re.match(r"[0-7]{3}", value[i + 1 : i + 4]):
                buf.append(int(value[i + 1 : i + 4], 8))
                i += 4
            else:
                buf.extend(value[i].encode("utf-8"))
                i += 1
        raw = bytes(buf)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_copy_dump(text: str) -> dict[str, list[dict[str, str | None]]]:
    """Read every ``COPY table (cols) FROM stdin;`` block into rows of dicts.

    Table and column names are lower-cased, like PostgreSQL stores them.
    """
    tables: dict[str, list[dict[str, str | None]]] = {}
    lines = text.split("\n")
    i = 0
    header = re.compile(r"^COPY\s+(\S+)\s*\(([^)]*)\)\s+FROM\s+stdin;\s*$", re.IGNORECASE)
    while i < len(lines):
        m = header.match(lines[i])
        i += 1
        if not m:
            continue
        table = m.group(1).strip('"').lower().split(".")[-1]
        cols = [c.strip().strip('"').lower() for c in m.group(2).split(",")]
        rows = tables.setdefault(table, [])
        while i < len(lines) and lines[i] != "\\.":
            if lines[i] != "":
                values = [_unescape_copy(v) for v in lines[i].split("\t")]
                rows.append(dict(zip(cols, values)))
            i += 1
        i += 1
    return tables
