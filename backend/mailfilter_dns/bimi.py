"""BIMI: the brand logo next to a domain's mail in Gmail, Apple Mail, Yahoo.

What a mailbox provider needs before it shows the logo:

* DMARC at enforcement: ``p=quarantine`` or ``p=reject``, ``pct=100`` (or no
  pct), and for a subdomain no ``sp=none``.
* A TXT record ``default._bimi.<domain>``: ``v=BIMI1; l=<logo url>; a=<cert url>``.
* The logo as SVG Tiny Portable/Secure (SVG Tiny PS): a restricted SVG with
  ``version="1.2" baseProfile="tiny-ps"``, a ``<title>``, square, no scripts,
  no external references, no raster images, at most 32 KB.
* Gmail and Apple Mail only show it with a mark certificate: a VMC (Verified
  Mark Certificate, needs a registered trademark) or a CMC (Common Mark
  Certificate, the logo has been in use on the domain for a year). Yahoo and
  some others show the logo without one.

Mailfilter hosts both files itself under ``https://<hostname>/api/bimi/``, so
the domain owner only needs the DNS record. Everything here is plain Python
plus ``cryptography`` (a Debian package), like the rest of the toolbox.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import gzip
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import dmarc as dmarc_mod
from .config import STATE
from .resolver import DnsError, Resolver, norm

BIMI_DIR = STATE / "bimi"
SELECTOR = "default"
MAX_SVG = 32 * 1024
MAX_PEM = 64 * 1024

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

# SVG Tiny 1.2 elements a logo may use. Everything else (script, image,
# foreignObject, animation, filters, masks, clip paths, patterns, links,
# <style>) is either forbidden by SVG Tiny PS or not part of SVG Tiny 1.2.
ALLOWED = {"svg", "title", "desc", "metadata", "g", "defs", "use", "path", "rect", "circle", "ellipse", "line",
           "polyline", "polygon", "linearGradient", "radialGradient", "stop", "solidColor", "text", "tspan",
           "textArea", "tbreak"}
# Elements the converter simply drops (they only add behaviour, not the picture)
DROP = {"script", "animate", "animateMotion", "animateColor", "animateTransform", "set", "discard", "handler",
        "listener", "prefetch", "audio", "video"}
# Presentation properties that may move from CSS / style="" to attributes
PRESENTATION = {"fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
                "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "opacity",
                "color", "display", "visibility", "stop-color", "stop-opacity", "solid-color", "solid-opacity",
                "font-family", "font-size", "font-style", "font-weight", "text-anchor", "vector-effect",
                "line-increment", "text-align", "display-align"}

# Mark certificates (RFC 3709 logotype, BIMI extended key usage, MVA policy OIDs)
OID_LOGOTYPE = "1.3.6.1.5.5.7.1.12"
OID_EKU_BIMI = "1.3.6.1.5.5.7.3.31"
OID_VMC = "1.3.6.1.4.1.53087.1.1"
OID_CMC = "1.3.6.1.4.1.53087.4.1"


def _local(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


# --- SVG Tiny PS check ------------------------------------------------------------

@dataclass
class SvgReport:
    ok: bool = False
    errors: list[str] = field(default_factory=list)  # codes, some as code:value
    warnings: list[str] = field(default_factory=list)
    title: str = ""
    size: int = 0
    sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(data: bytes) -> ET.Element:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        # Entities are a classic XML attack, and SVG Tiny PS has no DTD
        raise ValueError("doctype")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("not_xml") from exc


def _viewbox(root: ET.Element) -> tuple[float, float, float, float] | None:
    vb = root.get("viewBox")
    if not vb:
        return None
    try:
        parts = [float(x) for x in re.split(r"[\s,]+", vb.strip())]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 4 and parts[2] > 0 and parts[3] > 0 else None  # type: ignore[return-value]


def check_svg(data: bytes) -> SvgReport:
    rep = SvgReport(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    if len(data) > MAX_SVG:
        rep.errors.append(f"too_large:{len(data) // 1024}")
    try:
        root = _parse(data)
    except ValueError as exc:
        rep.errors.append(str(exc))
        return rep
    ns, name = _local(root.tag)
    if name != "svg" or ns != SVG_NS:
        rep.errors.append("not_svg")
        return rep
    if root.get("version") != "1.2":
        rep.errors.append("version")
    if root.get("baseProfile") != "tiny-ps":
        rep.errors.append("base_profile")
    if root.get("x") is not None or root.get("y") is not None:
        rep.errors.append("root_xy")
    title = next((c for c in root if _local(c.tag) == (SVG_NS, "title")), None)
    rep.title = (title.text or "").strip() if title is not None else ""
    if not rep.title:
        rep.errors.append("no_title")
    vb = _viewbox(root)
    if vb is None:
        rep.errors.append("no_viewbox")
    elif abs(vb[2] - vb[3]) > max(vb[2], vb[3]) * 0.001:
        rep.errors.append("not_square")
    seen: set[str] = set()

    def once(code: str) -> None:
        if code not in seen:
            seen.add(code)
            rep.errors.append(code)

    for el in root.iter():
        ens, ename = _local(el.tag)
        if ens != SVG_NS:
            once(f"foreign_element:{ename}")
        elif ename not in ALLOWED:
            once(f"element:{ename}")
        for attr, val in el.attrib.items():
            ans, aname = _local(attr)
            if aname.lower().startswith("on"):
                once("script_attr")
            elif (ans in ("", XLINK_NS)) and aname == "href" and not val.startswith("#"):
                once("external_ref")
            elif aname == "style" and ans == "":
                once("style_attr")
            elif ans not in ("", XLINK_NS, XML_NS):
                once("foreign_attr")
            if "url(" in val and not re.fullmatch(r"\s*url\(\s*#[^)]+\)\s*", val):
                once("external_ref")
    rep.ok = not rep.errors
    return rep


# --- converter ------------------------------------------------------------------------

_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _decls(text: str) -> dict[str, str]:
    out = {}
    for part in text.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k and v:
                out[k] = v.replace("!important", "").strip()
    return out


def _css_rules(css: str) -> list[tuple[list[str], dict[str, str]]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    rest = _CSS_RULE.sub("", css).strip()
    if rest:
        raise ValueError("cannot_convert:style")
    for sel, body in _CSS_RULE.findall(css):
        classes = []
        for s in sel.split(","):
            s = s.strip()
            if not re.fullmatch(r"\.[A-Za-z0-9_-]+", s):
                raise ValueError("cannot_convert:style")
            classes.append(s[1:])
        rules.append((classes, _decls(body)))
    return rules


def _set_props(el: ET.Element, props: dict[str, str], changes: set[str]) -> None:
    for k, v in props.items():
        if k in PRESENTATION:
            el.set(k, v)
        else:
            changes.add("dropped_css")


def _num(v: str | None) -> float | None:
    m = re.fullmatch(r"\s*([0-9.]+)\s*(px)?\s*", v or "")
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def convert_svg(data: bytes, title: str) -> tuple[bytes, list[str]]:
    """Turn an ordinary SVG logo into SVG Tiny PS where that is safe to do.

    Returns the new file and the list of changes. Raises ``ValueError`` with
    ``cannot_convert:<what>`` for things that would change the picture
    (raster images, masks, filters, ...): those need a designer.
    """
    root = _parse(data)
    if _local(root.tag) != (SVG_NS, "svg"):
        raise ValueError("not_svg")
    changes: set[str] = set()

    # 1. CSS from <style> elements to attributes (only simple .class rules)
    styles = [el for el in root.iter() if _local(el.tag) == (SVG_NS, "style")]
    rules = []
    for st in styles:
        rules += _css_rules(st.text or "")
    if rules:
        for el in root.iter():
            cls = (el.get("class") or "").split()
            for classes, props in rules:
                if any(c in cls for c in classes):
                    _set_props(el, props, changes)
        changes.add("inlined_css")

    # 2. Walk the tree: drop what only adds behaviour, refuse what changes the picture
    def walk(parent: ET.Element) -> None:
        for el in list(parent):
            ns, name = _local(el.tag)
            if ns != SVG_NS:
                parent.remove(el)
                changes.add("removed_editor_data")
                continue
            if name == "style":
                parent.remove(el)
                continue
            if name in DROP:
                parent.remove(el)
                changes.add("removed_script" if name == "script" else "removed_animation")
                continue
            if name == "a":
                # Unwrap links: keep the shapes, lose the link
                idx = list(parent).index(el)
                parent.remove(el)
                for i, child in enumerate(list(el)):
                    parent.insert(idx + i, child)
                changes.add("removed_link")
                walk(parent)
                return
            if name not in ALLOWED:
                raise ValueError(f"cannot_convert:{name}")
            clean(el)
            walk(el)

    def clean(el: ET.Element) -> None:
        for attr in list(el.attrib):
            ans, aname = _local(attr)
            val = el.attrib[attr]
            if aname.lower().startswith("on"):
                del el.attrib[attr]
                changes.add("removed_script")
            elif ans not in ("", XLINK_NS, XML_NS):
                del el.attrib[attr]
                changes.add("removed_editor_data")
            elif aname == "href" and not val.startswith("#"):
                raise ValueError("cannot_convert:external_ref")
            elif aname == "style" and ans == "":
                del el.attrib[attr]
                _set_props(el, _decls(val), changes)
                changes.add("inlined_css")
            elif aname == "class" and rules:
                del el.attrib[attr]
            elif "url(" in val and not re.fullmatch(r"\s*url\(\s*#[^)]+\)\s*", val):
                raise ValueError("cannot_convert:external_ref")

    clean(root)
    walk(root)

    # 3. The root element
    for a in ("x", "y"):
        if a in root.attrib:
            del root.attrib[a]
            changes.add("removed_root_xy")
    if root.get("version") != "1.2" or root.get("baseProfile") != "tiny-ps":
        root.set("version", "1.2")
        root.set("baseProfile", "tiny-ps")
        changes.add("set_profile")
    vb = _viewbox(root)
    if vb is None:
        w, h = _num(root.get("width")), _num(root.get("height"))
        if not w or not h:
            raise ValueError("cannot_convert:no_size")
        vb = (0.0, 0.0, w, h)
        changes.add("added_viewbox")
    x, y, w, h = vb
    if abs(w - h) > max(w, h) * 0.001:
        s = max(w, h)
        x, y, w, h = x - (s - w) / 2, y - (s - h) / 2, s, s
        changes.add("made_square")
        for a in ("width", "height"):
            root.attrib.pop(a, None)
    root.set("viewBox", " ".join(_fmt(v) for v in (x, y, w, h)))

    # 4. A <title> as first child
    for el in [c for c in root if _local(c.tag) == (SVG_NS, "title")]:
        if (el.text or "").strip():
            if list(root).index(el) != 0:
                root.remove(el)
                root.insert(0, el)
            break
        root.remove(el)
    else:
        t = ET.Element(f"{{{SVG_NS}}}title")
        t.text = (title or "Logo").strip()[:100]
        root.insert(0, t)
        changes.add("added_title")

    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    out = buf.getvalue()
    out = re.sub(rb">\s+<", b"><", out)  # smaller, and the picture does not depend on whitespace between tags
    return out, sorted(changes)


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:.3f}".rstrip("0").rstrip(".")


# --- mark certificate (VMC / CMC) -------------------------------------------------------

@dataclass
class CertReport:
    ok: bool = False
    kind: str = ""  # vmc, cmc or unknown
    subject: str = ""
    organization: str = ""
    issuer: str = ""
    not_after: str = ""
    days_left: int | None = None
    names: list[str] = field(default_factory=list)
    chain: int = 0
    has_logo: bool = False
    logo_matches: bool | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _load_certs(pem: bytes) -> list:
    from cryptography import x509

    blocks = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", pem, flags=re.S)
    if not blocks:
        raise ValueError("not_pem")
    try:
        return [x509.load_pem_x509_certificate(b) for b in blocks]
    except ValueError as exc:
        raise ValueError("not_pem") from exc


def logo_from_cert(pem: bytes) -> bytes | None:
    """The SVG inside the certificate's logotype extension (RFC 3709/6170),
    stored as ``data:image/svg+xml;base64,<gzip of the svg>``."""
    from cryptography import x509

    cert = _load_certs(pem)[0]
    try:
        ext = cert.extensions.get_extension_for_oid(x509.ObjectIdentifier(OID_LOGOTYPE))
    except x509.ExtensionNotFound:
        return None
    raw = getattr(ext.value, "value", b"") or b""
    m = re.search(rb"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", raw)
    if not m:
        return None
    try:
        blob = base64.b64decode(m.group(1))
    except binascii.Error:
        return None
    if blob[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as f:
            blob = f.read(MAX_SVG * 4 + 1)
        if len(blob) > MAX_SVG * 4:
            return None
    return blob


def check_cert(pem: bytes, domain: str, logo: bytes | None = None, now: dt.datetime | None = None) -> CertReport:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    rep = CertReport()
    if len(pem) > MAX_PEM:
        rep.errors.append("too_large")
        return rep
    try:
        certs = _load_certs(pem)
    except ValueError as exc:
        rep.errors.append(str(exc))
        return rep
    leaf = certs[0]
    rep.chain = len(certs)
    now = now or dt.datetime.now(dt.timezone.utc)

    def attr(name, oid) -> str:
        v = name.get_attributes_for_oid(oid)
        return str(v[0].value) if v else ""

    rep.subject = leaf.subject.rfc4514_string()
    rep.organization = attr(leaf.subject, NameOID.ORGANIZATION_NAME)
    rep.issuer = attr(leaf.issuer, NameOID.ORGANIZATION_NAME) or attr(leaf.issuer, NameOID.COMMON_NAME)
    after = leaf.not_valid_after_utc
    rep.not_after = after.isoformat(timespec="seconds")
    rep.days_left = (after - now).days
    if after < now:
        rep.errors.append("expired")
    elif rep.days_left < 30:
        rep.warnings.append(f"expires_soon:{rep.days_left}")
    if leaf.not_valid_before_utc > now:
        rep.errors.append("not_yet_valid")

    try:
        eku = leaf.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
        has_bimi = any(o.dotted_string == OID_EKU_BIMI for o in eku)
    except x509.ExtensionNotFound:
        has_bimi = False
    if not has_bimi:
        rep.errors.append("no_bimi_eku")
    try:
        policies = {p.policy_identifier.dotted_string
                    for p in leaf.extensions.get_extension_for_oid(ExtensionOID.CERTIFICATE_POLICIES).value}
    except x509.ExtensionNotFound:
        policies = set()
    rep.kind = "vmc" if OID_VMC in policies else "cmc" if OID_CMC in policies else "unknown"

    try:
        san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        rep.names = [norm(n) for n in san.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        rep.names = []
    d = norm(domain)
    # The certificate names the domain, or a parent of it (it then covers subdomains)
    if not any(d == n or d.endswith("." + n) or n == f"{SELECTOR}._bimi.{d}" for n in rep.names):
        rep.errors.append(f"domain_mismatch:{', '.join(rep.names[:5]) or '-'}")

    embedded = logo_from_cert(pem)
    rep.has_logo = embedded is not None
    if not rep.has_logo:
        rep.errors.append("no_logo")
    elif logo is not None:
        rep.logo_matches = _same_svg(embedded, logo)
        if not rep.logo_matches:
            rep.warnings.append("logo_differs")
    if rep.chain < 2:
        rep.warnings.append("no_chain")
    rep.ok = not rep.errors
    return rep


def _same_svg(a: bytes, b: bytes) -> bool:
    if a == b:
        return True
    squash = lambda x: re.sub(rb"\s+", b"", x.split(b"?>", 1)[-1] if x.lstrip().startswith(b"<?xml") else x)  # noqa: E731
    return squash(a) == squash(b)


# --- storage ------------------------------------------------------------------------------

def _path(domain: str, ext: str, base: Path | None = None) -> Path:
    d = norm(domain)
    if not re.fullmatch(r"[a-z0-9._-]+", d) or ".." in d:
        raise ValueError("invalid_domain")
    return (base or BIMI_DIR) / f"{d}.{ext}"


def read_file(domain: str, ext: str, base: Path | None = None) -> bytes | None:
    try:
        return _path(domain, ext, base).read_bytes()
    except (FileNotFoundError, ValueError):
        return None


def write_file(domain: str, ext: str, data: bytes | None, base: Path | None = None) -> None:
    p = _path(domain, ext, base)
    if data is None:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o644)  # served to the world anyway
    tmp.replace(p)


def file_info(domain: str, ext: str, base: Path | None = None) -> dict | None:
    try:
        st = _path(domain, ext, base).stat()
    except (FileNotFoundError, ValueError):
        return None
    return {"size": st.st_size,
            "updated": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")}


# --- record, DMARC prerequisite, status -------------------------------------------------------

def record_name(domain: str) -> str:
    return f"{SELECTOR}._bimi.{norm(domain)}"


def urls(host: str, domain: str) -> tuple[str, str]:
    base = f"https://{norm(host)}/api/bimi/{norm(domain)}"
    return f"{base}.svg", f"{base}.pem"


def record_value(host: str, domain: str, with_cert: bool) -> str:
    logo, cert = urls(host, domain)
    return f"v=BIMI1; l={logo}; a={cert}" if with_cert else f"v=BIMI1; l={logo};"


def is_bimi(txt: str) -> bool:
    return txt.replace(" ", "").lower().startswith("v=bimi1")


def parse_record(txt: str) -> dict[str, str]:
    tags = {}
    for part in txt.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = v.strip()
    return tags


def same_record(a: str, b: str) -> bool:
    pa, pb = parse_record(a), parse_record(b)
    return pa.get("l", "") == pb.get("l", "") and pa.get("a", "") == pb.get("a", "")


def dmarc_check(domain: str, resolver: Resolver) -> dict:
    """Is DMARC strict enough for BIMI? Looks at the domain itself and, when it
    has no record, at its parent domains (the organisational domain)."""
    d = norm(domain)
    labels = d.split(".")
    record, at = None, None
    for i in range(0, max(1, len(labels) - 1)):
        name = ".".join(labels[i:])
        try:
            rec, _ = dmarc_mod.find_dmarc(resolver.txt(f"_dmarc.{name}"))
        except DnsError:
            rec = None
        if rec:
            record, at = rec, name
            break
    out = {"record": record, "at": at, "policy": None, "pct": 100, "ok": False, "problems": []}
    if not record:
        out["problems"].append("dmarc_missing")
        return out
    try:
        tags = dmarc_mod.parse(record)
    except dmarc_mod.DmarcSyntaxError:
        out["problems"].append("dmarc_invalid")
        return out
    policy = tags["p"].lower()
    if at != d and "sp" in tags:
        policy = tags["sp"].lower()  # a subdomain follows sp= of its organisational domain
    out["policy"] = policy
    out["pct"] = int(tags.get("pct", "100") or 100)
    if policy not in ("quarantine", "reject"):
        out["problems"].append(f"dmarc_not_enforced:{policy}")
    if out["pct"] != 100:
        out["problems"].append(f"dmarc_pct:{out['pct']}")
    if at == d and tags.get("sp", "").lower() == "none":
        out["problems"].append("dmarc_sp_none")
    out["ok"] = not out["problems"]
    return out


def published(domain: str, resolver: Resolver) -> list[str]:
    try:
        return [t for t in resolver.txt(record_name(domain)) if is_bimi(t)]
    except DnsError:
        return []


def status(domain: str, host: str, resolver: Resolver, base: Path | None = None) -> dict:
    d = norm(domain)
    logo = read_file(d, "svg", base)
    pem = read_file(d, "pem", base)
    logo_rep = check_svg(logo) if logo is not None else None
    cert_rep = check_cert(pem, d, logo) if pem is not None else None
    expected = record_value(host, d, pem is not None) if logo is not None else None
    found = published(d, resolver)
    if not found:
        rec_status = "missing"
    elif len(found) > 1:
        rec_status = "bad"
    elif expected and same_record(found[0], expected):
        rec_status = "ok"
    else:
        rec_status = "different"
    dm = dmarc_check(d, resolver)
    logo_url, cert_url = urls(host, d)
    steps = {
        "dmarc": dm["ok"],
        "logo": bool(logo_rep and logo_rep.ok),
        "record": rec_status == "ok",
        "cert": bool(cert_rep and cert_rep.ok),
    }
    # Yahoo and others: DMARC + logo + record. Gmail and Apple Mail also need the certificate.
    level = "full" if all(steps.values()) else "basic" if steps["dmarc"] and steps["logo"] and steps["record"] else "none"
    return {
        "domain": d, "selector": SELECTOR, "record_name": record_name(d),
        "logo": {**(file_info(d, "svg", base) or {}), "report": logo_rep.to_dict(), "url": logo_url} if logo_rep else None,
        "cert": {**(file_info(d, "pem", base) or {}), "report": cert_rep.to_dict(), "url": cert_url} if cert_rep else None,
        "expected": expected, "published": found, "record_status": rec_status,
        "dmarc": dm, "steps": steps, "level": level,
    }
