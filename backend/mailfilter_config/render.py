"""Turns a validated GatewayConfig into the files and settings Postfix,
Rspamd and nftables read. Pure functions: nothing here touches the server.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .model import Domain, GatewayConfig, QueuePolicy
from .rules import render_files as render_rule_files

ETC = "/etc/mailfilter"
PF = f"{ETC}/postfix"

# Every main.cf parameter this package sets starts with one of these, so
# stale ones (from a removed domain) can be found and deleted.
MANAGED_PARAM_PREFIXES = ("mfq_", "mfo_")
# Standard Postfix parameters this package owns completely (always set).
MANAGED_PARAMS = ("smtpd_end_of_data_restrictions",)
BEGIN = "# BEGIN mailfilter-config (managed by the web interface, do not edit)"
END = "# END mailfilter-config"

HEADER = "# Managed by Mailfilter (web interface). Changes here are overwritten.\n"


@dataclass
class Rendered:
    files: dict[str, str] = field(default_factory=dict)  # path -> content
    hash_maps: list[str] = field(default_factory=list)  # need postmap
    main_cf: dict[str, str] = field(default_factory=dict)  # postconf -e
    master_cf_block: str = ""
    relay_ports: list[int] = field(default_factory=list)


def service_name(prefix: str, domain: str) -> str:
    """Postfix transport name for a domain, e.g. mfq_rhis_nl_3fa2.

    Deterministic, safe as a main.cf parameter prefix, and unique even for
    domains that only differ in dots and dashes.
    """
    slug = re.sub(r"[^a-z0-9]", "_", domain.lower())[:40]
    digest = hashlib.sha1(domain.lower().encode()).hexdigest()[:4]
    return f"{prefix}{slug}_{digest}"


def _clean(text: str | None) -> str:
    return re.sub(r"[\r\n]+", " ", text or "").strip()


def _comment(text: str | None) -> str:
    """Inline comment; only for files whose readers allow it (not Postfix tables)."""
    text = _clean(text)
    return f"  # {text}" if text else ""


def _comment_line(text: str | None) -> str:
    """Comment on its own line, for Postfix tables (they have no inline comments)."""
    text = _clean(text)
    return f"# {text}\n" if text else ""


def _queue_params(svc: str, q: QueuePolicy, dflt_concurrency: int) -> dict[str, str]:
    params: dict[str, str] = {}
    if q.concurrency is not None:
        params[f"{svc}_destination_concurrency_limit"] = str(int(q.concurrency))
        params[f"{svc}_initial_destination_concurrency"] = str(min(5, int(q.concurrency)))
    else:
        params[f"{svc}_destination_concurrency_limit"] = str(int(dflt_concurrency))
    if q.rate_delay:
        params[f"{svc}_destination_rate_delay"] = f"{int(q.rate_delay)}s"
    return params


def _transport_service(svc: str, syslog: str, extra: list[str] | None = None) -> str:
    lines = [f"{svc:<10} unix  -       -       y       -       -       smtp",
             f"  -o syslog_name=postfix/{syslog}"]
    lines += [f"  -o {o}" for o in extra or []]
    return "\n".join(lines)


def _listener(port: int, tls: str) -> str:
    opts = [f"syslog_name=postfix/port-{port}"]
    if tls == "wrapper":
        opts.append("smtpd_tls_wrappermode=yes")
    else:
        opts.append(f"smtpd_tls_security_level={tls}")
    opts += [
        "smtpd_sasl_auth_enable=yes",
        "smtpd_client_restrictions=permit_mynetworks,permit_sasl_authenticated,reject",
        "smtpd_helo_restrictions=",
        "smtpd_relay_restrictions=permit_mynetworks,permit_sasl_authenticated,reject",
        "smtpd_recipient_restrictions=permit_mynetworks,permit_sasl_authenticated,reject",
        "milter_macro_daemon_name=ORIGINATING",
    ]
    return "\n".join([f"{port:<10} inet  n       -       y       -       -       smtpd"] + [f"  -o {o}" for o in opts])


def render(cfg: GatewayConfig) -> Rendered:
    """Expects a configuration that passed validate.check()."""
    out = Rendered()
    domains: list[Domain] = sorted((d for d in cfg.domains if d.enabled), key=lambda d: d.name)

    relay_domains, transport, tls_policy, verify = [HEADER], [HEADER], [HEADER], [HEADER]
    master: list[str] = []

    # ---- inbound: one queue (transport) per domain -------------------------------
    for d in domains:
        svc = service_name("mfq_", d.name)
        first, *fallback = d.destinations
        relay_domains.append(_comment_line(d.customer and f"customer {d.customer}") + f"{d.name} OK\n")
        if first.protocol == "lmtp":
            # LMTP goes straight to the mailbox server; Postfix has no fallback for it.
            transport.append(f"{d.name} lmtp:{first.nexthop()}\n")
        else:
            transport.append(f"{d.name} {svc}:{first.nexthop()}\n")
            extra = []
            if fallback:
                extra.append("smtp_fallback_relay=" + ",".join(x.nexthop() for x in fallback))
            master.append(f"# Queue for {d.name}\n" + _transport_service(svc, f"domain/{d.name}", extra))
            out.main_cf.update(_queue_params(svc, d.queue, cfg.defaults.concurrency))
        if d.tls != "may":
            for dest in d.destinations:
                tls_policy.append(f"{dest.nexthop()} {d.tls}\n")
        if d.verify_recipients:
            verify.append(f"{d.name} reject_unverified_recipient\n")

    # ---- outbound: throttled queues for big remote domains ------------------------
    for p in sorted(cfg.destination_policies, key=lambda p: p.domain):
        svc = service_name("mfo_", p.domain)
        transport.append(_comment_line(p.note) + f"{p.domain} {svc}:\n")
        master.append(f"# Outbound queue for {p.domain}\n" + _transport_service(svc, f"out/{p.domain}"))
        out.main_cf.update(_queue_params(svc, p.queue, cfg.defaults.concurrency))

    # ---- single addresses routed elsewhere ----------------------------------------
    for a in sorted(cfg.address_routes, key=lambda a: a.address):
        method = "lmtp" if a.destination.protocol == "lmtp" else "relay"
        transport.append(_comment_line(a.note) + f"{a.address} {method}:{a.destination.nexthop()}\n")

    for t in cfg.tls_policies:
        tls_policy.append(_comment_line(t.note) + f"{t.destination} {t.level}\n")

    # ---- extra relay listeners --------------------------------------------------
    for rp in sorted(cfg.relay_ports, key=lambda p: p.port):
        desc = _clean(rp.description)
        master.append(f"# Extra relay port{': ' + desc if desc else ''}\n" + _listener(int(rp.port), rp.tls))
        out.relay_ports.append(int(rp.port))

    out.master_cf_block = "\n\n".join(master)

    for name, lines in (("relay_domains", relay_domains), ("transport", transport),
                        ("tls_policy", tls_policy), ("recipient_verify", verify)):
        out.files[f"{PF}/{name}"] = "".join(lines)
        out.hash_maps.append(f"{PF}/{name}")

    # ---- relay access -----------------------------------------------------------
    nets = [n for n in cfg.relay_networks if n.enabled]
    plain = [HEADER, "# One IP address or network per line (IPv4 and IPv6).\n"]
    cidr = [HEADER]
    local_addrs = [HEADER, "127.0.0.0/8\n", "::1\n"]
    for n in nets:
        label = " ".join(x for x in (n.description, n.customer and f"(customer {n.customer})") if x)
        plain.append(f"{n.cidr}{_comment(label)}\n")
        cidr.append(_comment_line(label) + f"{n.cidr} OK\n")
        local_addrs.append(f"{n.cidr}\n")
    out.files[f"{ETC}/relay_networks.list"] = "".join(plain)
    out.files[f"{PF}/relay_networks.cidr"] = "".join(cidr)
    out.files[f"{ETC}/rspamd/local_addrs.map"] = "".join(local_addrs)

    # ---- nftables: open the extra relay ports -----------------------------------
    ports = ", ".join(str(p) for p in out.relay_ports)
    out.files[f"{ETC}/nftables/relay_ports.nft"] = (
        HEADER + "flush set inet mailfilter relay_ports\n"
        + (f"add element inet mailfilter relay_ports {{ {ports} }}\n" if ports else "")
    )

    out.files["/etc/rspamd/local.d/ratelimit.conf"] = render_ratelimit(cfg, domains)

    # ---- paused senders: their mail goes to the hold queue (nothing is lost) --------
    hold_ip, hold_sasl = [HEADER], [HEADER]
    for h in sorted(cfg.holds, key=lambda h: (h.kind, h.value)):
        text = f"HOLD Paused by Mailfilter: {_clean(h.reason) or 'unusual outbound mail'}\n"
        (hold_ip if h.kind == "ip" else hold_sasl).append(f"{h.value} {text}")
    out.files[f"{PF}/outbound_hold.cidr"] = "".join(hold_ip)
    out.files[f"{PF}/outbound_hold_sasl"] = "".join(hold_sasl)
    out.hash_maps.append(f"{PF}/outbound_hold_sasl")
    # Checked at the end of every message on every listener; only listed senders match.
    out.main_cf["smtpd_end_of_data_restrictions"] = (
        f"check_client_access cidr:{PF}/outbound_hold.cidr, check_sasl_access hash:{PF}/outbound_hold_sasl")
    # ---- custom rules (newsletter filter etc.) ------------------------------------
    out.files.update(render_rule_files(cfg.custom_rules, {d.name for d in domains}))
    return out


def _rate(name: str, selector: str, n: int, period: str) -> str:
    return f'  {name} = {{ selector = "{selector}"; bucket = {{ burst = {n}; rate = "{n} / {period}"; }} }}\n'


def render_ratelimit(cfg: GatewayConfig, domains: list[Domain]) -> str:
    dft = cfg.defaults
    from_dom = "from('smtp'):domain.lower"
    hour_own = sorted(d.name for d in domains if d.outbound.per_hour is not None)
    day_own = sorted(d.name for d in domains if d.outbound.per_day is not None)

    def except_(names: list[str]) -> str:
        return f".not_in({', '.join(repr(n) for n in names)})" if names else ""

    lines = [
        "# Managed by Mailfilter (Throttling). Changes here are overwritten.\n",
        "# Only outbound mail is limited (see settings.conf).\n",
        "rates {\n",
        "  # Per sending server (relay IP)\n",
        _rate("mf_relay_ip_minute", "ip", dft.relay_ip_per_minute, "1min"),
        _rate("mf_relay_ip_hour", "ip", dft.relay_ip_per_hour, "1h"),
        "  # Per sender domain (domains with their own limit are below)\n",
        _rate("mf_from_domain_hour", from_dom + except_(hour_own), dft.domain_per_hour, "1h"),
        _rate("mf_from_domain_day", from_dom + except_(day_own), dft.domain_per_day, "1d"),
        "  # Backscatter: limit bounces (empty sender) that relay servers send out\n",
        '  bounce_to = { bucket = { burst = 5; rate = "1 / 5m"; } }\n',
        '  bounce_to_ip = { bucket = { burst = 5; rate = "1 / 5m"; } }\n',
        "  # Per SMTP account\n",
        _rate("mf_user_hour", "user", dft.user_per_hour, "1h"),
    ]
    for d in domains:
        slug = service_name("", d.name)
        if d.outbound.per_hour is not None:
            lines.append(_rate(f"mf_dom_{slug}_hour", f"{from_dom}.equal({d.name!r})", int(d.outbound.per_hour), "1h"))
        if d.outbound.per_day is not None:
            lines.append(_rate(f"mf_dom_{slug}_day", f"{from_dom}.equal({d.name!r})", int(d.outbound.per_day), "1d"))
    lines.append("}\n")
    return "".join(lines)


def splice_master_cf(current: str, block: str) -> str:
    """Put the managed block in master.cf between the markers (added at the end if missing)."""
    managed = f"{BEGIN}\n{block.rstrip()}\n{END}\n" if block.strip() else f"{BEGIN}\n{END}\n"
    if BEGIN in current and END in current:
        head, rest = current.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        return head + managed + tail.lstrip("\n")
    return current.rstrip("\n") + "\n\n" + managed
