"""Link protection: the check when someone clicks a link in incoming mail.

Rspamd (mailfilter_protection.lua) replaces every link in incoming mail for
domains with the rule "link_protection" on by

    https://<host>/l/<payload>.<signature>

payload   base64url of a small JSON object: u (the link), m (Message-ID),
          q (queue id), r (the recipient when there is one), d (recipient
          domains), t (time of delivery), f (flags: 1 = Rspamd or a phishing
          rule named this link, 2 = the sender is fake)
signature first 16 bytes of keyed BLAKE2b(payload) with the key in
          /etc/mailfilter/link-protection.key, base64url

Nothing is stored per link. Without a valid signature a link is refused, so
the click page is never an open redirect.

At the click the destination is checked again: the admin's block list,
Spamhaus DBL / SURBL / URIBL (through the local resolver), the same checks as
the phishing rules, and link shorteners are followed to their destination.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import dns.exception
import dns.resolver
import httpx

from .settings import get_settings

log = logging.getLogger("mailfilter.links")

Level = Literal["safe", "warn", "block"]
LEVELS: dict[str, int] = {"safe": 0, "warn": 1, "block": 2}
FLAG_LINK, FLAG_SENDER = 1, 2
MAX_TOKEN = 6000

# Same list as SHORTENERS in mailfilter_protection.lua.
SHORTENERS = {
    "bit.ly", "bitly.com", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly",
    "cutt.ly", "shorturl.at", "rb.gy", "t.ly", "tiny.cc", "s.id", "bl.ink", "lnkd.in", "v.gd", "qrco.de",
    "shorturl.com", "tr.im", "x.co", "soo.gd", "clck.ru", "u.to",
}

PSL_FILES = ("/usr/share/rspamd/effective_tld_names.dat", "/usr/share/publicsuffix/public_suffix_list.dat")


# ---- signed click links -----------------------------------------------------------------
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_key_cache: dict[str, Any] = {}


def link_key() -> bytes | None:
    path = Path(get_settings().link_key_file)
    try:
        st = path.stat()
        if _key_cache.get("mtime") != st.st_mtime:
            k = "".join(path.read_text().split()).encode()
            _key_cache.update(mtime=st.st_mtime, key=k if 32 <= len(k) <= 64 else None)
    except OSError:
        _key_cache.clear()
        return None
    return _key_cache.get("key")


def _mac(key: bytes, payload: str) -> bytes:
    # libsodium's keyed generichash (Rspamd's create_keyed) = BLAKE2b, 64 bytes
    return hashlib.blake2b(payload.encode(), key=key).digest()[:16]


def make_link(data: dict, host: str | None = None, key: bytes | None = None) -> str:
    """What Rspamd writes into the mail (used by tests and the "test a link" tool)."""
    key = key or link_key()
    if not key:
        raise RuntimeError("no link protection key")
    payload = _b64e(json.dumps(data, separators=(",", ":")).encode())
    return f"https://{host or get_settings().hostname}/l/{payload}.{_b64e(_mac(key, payload))}"


def read_token(token: str) -> dict | None:
    """The payload of a click link, or None when it was not made by this Mailfilter."""
    key = link_key()
    # Mail programs sometimes take a full stop or bracket after the link along.
    token = token.rstrip(".,;:!?)]>'\"")
    if not key or len(token) > MAX_TOKEN or token.count(".") != 1:
        return None
    payload, mac = token.split(".")
    try:
        if not hmac.compare_digest(_mac(key, payload), _b64d(mac)):
            return None
        data = json.loads(_b64d(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    url = data.get("u")
    if not isinstance(url, str) or urlsplit(url).scheme.lower() not in ("http", "https") or not host_of(url):
        return None
    d = data.get("d")
    data["d"] = [str(x).lower() for x in d] if isinstance(d, list) else []
    data["f"] = data.get("f") if isinstance(data.get("f"), int) else 0
    return data


# ---- host names ---------------------------------------------------------------------------
def host_of(url: str) -> str:
    try:
        h = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    try:
        h = h.encode("idna").decode() if h and not h.isascii() else h
    except UnicodeError:
        pass
    return h.lower().rstrip(".")


@lru_cache(maxsize=1)
def _psl() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    rules, wild, exc = set(), set(), set()
    for f in PSL_FILES:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip().split(" ")[0]
            if not line or line.startswith("//"):
                continue
            try:
                line = line.encode("idna").decode() if not line.isascii() else line
            except UnicodeError:
                continue
            if line.startswith("!"):
                exc.add(line[1:])
            elif line.startswith("*."):
                wild.add(line[2:])
            else:
                rules.add(line)
        break
    return frozenset(rules), frozenset(wild), frozenset(exc)


def org_domain(host: str) -> str:
    """Registered domain: www.shop.co.uk -> shop.co.uk (public suffix list)."""
    host = host.lower().rstrip(".")
    if is_ip(host):
        return host
    labels = host.split(".")
    rules, wild, exc = _psl()
    if not rules:
        return ".".join(labels[-2:])
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in exc:
            return ".".join(labels[i:])
        parent = ".".join(labels[i + 1:])
        if cand in rules or (parent and parent in wild):
            return ".".join(labels[max(i - 1, 0):]) if i > 0 else host
    return ".".join(labels[-2:])


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def in_domains(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


# ---- look-alikes (same rules as the Lua phishing check) ----------------------------------
def _skeleton(s: str) -> str:
    s = s.replace("rn", "m").replace("vv", "w").replace("cl", "d").replace("-", "")
    return s.translate(str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "!": "l", "5": "s", "3": "e"}))


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike(host: str, protected: list[str], other_tld: bool = True) -> str | None:
    """The protected domain this host imitates, or None."""
    if not protected or is_ip(host) or in_domains(host, protected):
        return None
    org = org_domain(host)
    if any(org == org_domain(p) for p in protected):
        return None
    label = org.split(".")[0]
    for p in protected:
        p_org = org_domain(p)
        p_label, _, p_suffix = p_org.partition(".")
        if label == p_label:
            if other_tld:
                return p
        elif len(p_label) >= 4:
            if _skeleton(label) == _skeleton(p_label):
                return p
            mx = 2 if len(p_label) >= 9 else 1
            if abs(len(label) - len(p_label)) <= mx and _lev(label, p_label) <= mx:
                return p
            if label.startswith(p_label + "-") or ("-" + p_label) in label or label == p_label + p_suffix.replace(".", ""):
                return p
        if (p + ".") in host:
            return p
    return None


# ---- blocklists ---------------------------------------------------------------------------
DBL_CODES = {2: "spam", 4: "phishing", 5: "malware", 6: "botnet", 102: "spam", 103: "redirector",
             104: "phishing", 105: "malware", 106: "botnet"}
SURBL_BITS = {8: "phishing", 16: "malware", 64: "abuse", 128: "cracked"}
DANGEROUS = {"phishing", "malware", "botnet", "cracked"}


def _resolver() -> dns.resolver.Resolver:
    r = dns.resolver.Resolver()
    r.timeout = r.lifetime = 2.0
    return r


def _a(name: str) -> list[str]:
    try:
        return [r.address for r in _resolver().resolve(name, "A")]
    except (dns.exception.DNSException, OSError):
        return []


def blocklists(host: str) -> list[tuple[str, str]]:
    """[(list, kind)] for the host, e.g. ("Spamhaus DBL", "phishing"). Errors count as not listed."""
    if is_ip(host):
        return []
    org = org_domain(host)
    jobs: list[tuple[str, str]] = [("dbl", f"{org}.dbl.spamhaus.org"), ("surbl", f"{org}.multi.surbl.org"),
                                   ("uribl", f"{org}.multi.uribl.com")]
    if host != org:
        jobs.append(("dbl", f"{host}.dbl.spamhaus.org"))
    results: dict[int, list[str]] = {}

    def run(i: int, name: str) -> None:
        results[i] = _a(name)

    threads = [threading.Thread(target=run, args=(i, n), daemon=True) for i, (_, n) in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    out: list[tuple[str, str]] = []
    for i, (kind, _) in enumerate(jobs):
        for addr in results.get(i, []):
            parts = addr.split(".")
            if kind == "dbl" and addr.startswith("127.0.1."):
                code = DBL_CODES.get(int(parts[3]))
                if code:
                    out.append(("Spamhaus DBL", code))
            elif kind == "surbl" and addr.startswith("127.0.0.") and addr != "127.0.0.1":
                bits = int(parts[3])
                kinds = [k for b, k in SURBL_BITS.items() if bits & b] or ["spam"]
                out += [("SURBL", k) for k in kinds]
            elif kind == "uribl" and addr in ("127.0.0.2", "127.0.0.8"):
                out.append(("URIBL", "spam"))
    return sorted(set(out))


# ---- link shorteners --------------------------------------------------------------------
def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


def expand(url: str, hops: int = 5) -> str | None:
    """Where a shortened link really goes, or None when that cannot be found out."""
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False,
                          headers={"User-Agent": "Mailfilter link check"}) as c:
            for _ in range(hops):
                host = host_of(url)
                if not host or host not in SHORTENERS:
                    return url
                if not _public(host):
                    return None
                r = c.head(url)
                if r.status_code in (405, 501):
                    r = c.get(url)
                loc = r.headers.get("location")
                if not (300 <= r.status_code < 400 and loc):
                    return None
                url = urljoin(url, loc)
                if urlsplit(url).scheme not in ("http", "https"):
                    return None
    except httpx.HTTPError:
        return None
    return url if host_of(url) not in SHORTENERS else None


# ---- the verdict --------------------------------------------------------------------------
@dataclass
class Reason:
    code: str
    level: Level
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Verdict:
    url: str              # where the recipient goes (a shortener's destination when known)
    level: Level = "safe"
    reasons: list[Reason] = field(default_factory=list)

    def add(self, code: str, level: Level, **params: str) -> None:
        self.reasons.append(Reason(code, level, params))
        if LEVELS[level] > LEVELS[self.level]:
            self.level = level

    def to_dict(self) -> dict:
        return {"url": self.url, "level": self.level,
                "reasons": [{"code": r.code, "level": r.level, "params": r.params} for r in self.reasons]}


@dataclass
class CheckSettings:
    block: list[str] = field(default_factory=list)
    own: list[str] = field(default_factory=list)          # your domains: always safe
    protected: list[str] = field(default_factory=list)    # look-alike check
    other_tld: bool = True
    shorteners: bool = True
    use_blocklists: bool = True


def check(url: str, flags: int, cfg: CheckSettings) -> Verdict:
    v = Verdict(url=url)
    host = host_of(url)
    if in_domains(host, cfg.block):
        v.add("blocked_by_admin", "block", host=host)
        return v
    if in_domains(host, cfg.own):
        return v
    if host in SHORTENERS:
        dest = expand(url) if cfg.shorteners else None
        if dest:
            v.url = dest
            v.add("shortener", "safe", host=host, destination=host_of(dest))
            host = host_of(dest)
            if in_domains(host, cfg.block):
                v.add("blocked_by_admin", "block", host=host)
                return v
            if in_domains(host, cfg.own):
                return v
        else:
            v.add("shortener_unknown", "warn", host=host)
    if flags & FLAG_SENDER:
        v.add("fake_sender", "warn")
    if flags & FLAG_LINK:
        v.add("flagged_at_delivery", "warn")
    parts = urlsplit(v.url)
    if is_ip(host):
        v.add("ip_address", "warn", host=host)
    elif parts.username and "." in parts.username:
        v.add("user_part", "warn", shown=parts.username, host=host)
    target = lookalike(host, cfg.protected, cfg.other_tld)
    if target:
        v.add("lookalike", "warn", host=host, target=target)
    if cfg.use_blocklists:
        for lst, kind in blocklists(host):
            v.add("blocklist", "block" if kind in DANGEROUS else "warn", list=lst, kind=kind)
    return v


# ---- page texts ---------------------------------------------------------------------------
REASONS: dict[str, dict[str, str]] = {
    "blocked_by_admin": {"en": "Your administrator has blocked {host}.",
                         "nl": "Je beheerder heeft {host} geblokkeerd."},
    "blocklist": {"en": "{list} lists this site as {kind_en}.", "nl": "{list} kent deze site als {kind_nl}."},
    "ip_address": {"en": "The link goes to a bare IP address instead of a name.",
                   "nl": "De link gaat naar een kaal IP-adres in plaats van een naam."},
    "user_part": {"en": "The link starts with {shown} but really goes to {host}.",
                  "nl": "De link begint met {shown} maar gaat echt naar {host}."},
    "lookalike": {"en": "{host} looks like {target}, but it is not.", "nl": "{host} lijkt op {target}, maar is het niet."},
    "flagged_at_delivery": {"en": "This link was already suspicious when the mail arrived.",
                            "nl": "Deze link was al verdacht toen de mail binnenkwam."},
    "fake_sender": {"en": "The sender of this mail pretends to be someone else.",
                    "nl": "De afzender van deze mail doet zich voor als iemand anders."},
    "shortener": {"en": "Shortened link: it goes to {destination}.", "nl": "Verkorte link: hij gaat naar {destination}."},
    "shortener_unknown": {"en": "Shortened link ({host}): where it really goes is unknown.",
                          "nl": "Verkorte link ({host}): waar hij echt heen gaat is onbekend."},
}
KINDS = {"spam": ("spam", "spam"), "phishing": ("phishing", "phishing"), "malware": ("malware", "malware"),
         "botnet": ("part of a botnet", "deel van een botnet"), "abuse": ("abused", "misbruikt"),
         "cracked": ("hacked", "gehackt"), "redirector": ("an abused redirect", "een misbruikte doorverwijzing")}


def reason_text(r: dict, lang: str) -> str:
    p = dict(r.get("params") or {})
    if "kind" in p:
        en, nl = KINDS.get(p["kind"], (p["kind"], p["kind"]))
        p.update(kind_en=en, kind_nl=nl)
    t = REASONS.get(r["code"], {}).get(lang) or REASONS.get(r["code"], {}).get("en") or r["code"]
    try:
        return t.format(**p)
    except (KeyError, IndexError):
        return t
