"""DMARC: parse, generate and judge records (RFC 7489)."""

from __future__ import annotations

from dataclasses import dataclass

POLICIES = ("none", "quarantine", "reject")
ALIGNMENT = ("r", "s")


class DmarcSyntaxError(ValueError):
    pass


def is_dmarc(txt: str) -> bool:
    return txt.strip().lower().replace(" ", "").startswith("v=dmarc1")


def parse(txt: str) -> dict[str, str]:
    """Return the tags in order. Tag names are lower case, values as written."""
    if not is_dmarc(txt):
        raise DmarcSyntaxError("not a DMARC record (must start with v=DMARC1)")
    tags: dict[str, str] = {}
    for part in txt.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise DmarcSyntaxError(f"tag without value: '{part}'")
        k, v = part.split("=", 1)
        tags[k.strip().lower()] = v.strip()
    if "p" not in tags:
        raise DmarcSyntaxError("missing required tag p=")
    if tags["p"].lower() not in POLICIES:
        raise DmarcSyntaxError(f"unknown policy p={tags['p']}")
    if "sp" in tags and tags["sp"].lower() not in POLICIES:
        raise DmarcSyntaxError(f"unknown policy sp={tags['sp']}")
    for k in ("adkim", "aspf"):
        if k in tags and tags[k].lower() not in ALIGNMENT:
            raise DmarcSyntaxError(f"{k} must be r or s")
    if "pct" in tags:
        if not tags["pct"].isdigit() or not 0 <= int(tags["pct"]) <= 100:
            raise DmarcSyntaxError("pct must be 0 to 100")
    return tags


@dataclass
class DmarcOptions:
    policy: str = "none"
    subdomain_policy: str | None = None
    pct: int = 100
    rua: list[str] | None = None
    ruf: list[str] | None = None
    adkim: str = "r"
    aspf: str = "r"


def generate(opts: DmarcOptions) -> str:
    if opts.policy not in POLICIES:
        raise ValueError("policy must be none, quarantine or reject")
    if opts.subdomain_policy and opts.subdomain_policy not in POLICIES:
        raise ValueError("subdomain policy must be none, quarantine or reject")
    if opts.adkim not in ALIGNMENT or opts.aspf not in ALIGNMENT:
        raise ValueError("alignment must be r or s")
    if not 0 <= opts.pct <= 100:
        raise ValueError("pct must be 0 to 100")
    parts = ["v=DMARC1", f"p={opts.policy}"]
    if opts.subdomain_policy and opts.subdomain_policy != opts.policy:
        parts.append(f"sp={opts.subdomain_policy}")
    if opts.pct != 100:
        parts.append(f"pct={opts.pct}")
    for tag, addrs in (("rua", opts.rua), ("ruf", opts.ruf)):
        if addrs:
            parts.append(f"{tag}=" + ",".join(_mailto(a) for a in addrs))
    if opts.adkim != "r":
        parts.append(f"adkim={opts.adkim}")
    if opts.aspf != "r":
        parts.append(f"aspf={opts.aspf}")
    return "; ".join(parts)


def _mailto(addr: str) -> str:
    addr = addr.strip()
    if addr.lower().startswith("mailto:"):
        addr = addr[7:]
    if "@" not in addr or " " in addr:
        raise ValueError(f"{addr} is not an email address")
    return "mailto:" + addr


def find_dmarc(txts: list[str]) -> tuple[str | None, list[str]]:
    recs = [t for t in txts if is_dmarc(t)]
    if not recs:
        return None, []
    return recs[0], (["multiple_dmarc"] if len(recs) > 1 else [])


def report_domains(tags: dict[str, str]) -> list[str]:
    """Domains of the rua/ruf addresses, for the external-report check (RFC 7489 7.1)."""
    out = []
    for k in ("rua", "ruf"):
        for uri in tags.get(k, "").split(","):
            uri = uri.strip()
            if uri.lower().startswith("mailto:") and "@" in uri:
                out.append(uri.split("@", 1)[1].split("!")[0].lower())
    return out


def judge(record: str | None) -> tuple[str, list[str]]:
    """Return (status, problem codes). Status is ok, weak or missing/bad."""
    if not record:
        return "missing", ["missing"]
    try:
        tags = parse(record)
    except DmarcSyntaxError:
        return "bad", ["syntax"]
    probs = []
    if tags["p"].lower() == "none":
        probs.append("policy_none")
    if tags.get("pct", "100") != "100":
        probs.append("pct_partial")
    if "rua" not in tags:
        probs.append("no_reports")
    return ("ok" if not probs or probs == ["no_reports"] else "weak"), probs
