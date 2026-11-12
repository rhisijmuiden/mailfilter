"""Turns a validated GatewayConfig into the files and settings Postfix,
Rspamd and nftables read. Pure functions: nothing here touches the server.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .model import Domain, GatewayConfig, QueuePolicy
from .rules import render_files as render_rule_files

ETC = "/etc/mailfilter"
PF = f"{ETC}/postfix"

# Every main.cf parameter this package sets starts with one of these, so
# stale ones (from a removed domain) can be found and deleted.
MANAGED_PARAM_PREFIXES = ("mfq_", "mfo_", "mfl_", "mft_")
# Standard Postfix parameters this package owns completely (always set).
MANAGED_PARAMS = ("smtpd_end_of_data_restrictions", "smtpd_restriction_classes")
SMTP_USERS = f"{ETC}/smtp-users"
# Accounts made in the web interface live between these lines of the Dovecot
# passwd-file; lines outside them (added by hand) are left alone.
USERS_BEGIN = "# BEGIN mailfilter-config accounts (managed by the web interface, do not edit)"
USERS_END = "# END mailfilter-config accounts"
# Mailtrap: where caught mail is stored (one Maildir per mailbox login), the
# settings the mftrap pipe reads, the IMAP users and Dovecot's IMAP part.
MAILTRAP_DIR = "/var/lib/mailfilter/mailtrap"
MAILTRAP_JSON = f"{ETC}/mailtrap.json"
MAILTRAP_USERS = f"{ETC}/mailtrap-users"
DOVECOT_MAILTRAP = f"{ETC}/dovecot-mailtrap.conf"
IMAP_PORT = 993
# Same order as main.cf: the Mailtrap is checked before anything that permits relaying.
MAILTRAP_CHECKS = f"check_client_access,cidr:{PF}/mailtrap.cidr,check_sasl_access,hash:{PF}/mailtrap_sasl"  # no spaces: master.cf -o values end at whitespace
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
    users_block: str = ""  # Dovecot passwd-file lines for SMTP accounts
    open_ports: list[int] = field(default_factory=list)  # other ports to open in the firewall (IMAP)
    mailboxes: list[str] = field(default_factory=list)  # Mailtrap Maildirs that must exist


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
        f"smtpd_client_restrictions={MAILTRAP_CHECKS},permit_mynetworks,permit_sasl_authenticated,reject",
        "smtpd_helo_restrictions=",
        f"smtpd_relay_restrictions={MAILTRAP_CHECKS},permit_mynetworks,permit_sasl_authenticated,reject",
        f"smtpd_recipient_restrictions={MAILTRAP_CHECKS},permit_mynetworks,permit_sasl_authenticated,reject",
        "milter_macro_daemon_name=ORIGINATING",
    ]
    return "\n".join([f"{port:<10} inet  n       -       y       -       -       smtpd"] + [f"  -o {o}" for o in opts])


def render(cfg: GatewayConfig, mail_ids: tuple[int, int] = (0, 0)) -> Rendered:
    """Expects a configuration that passed validate.check()."""
    out = Rendered()
    domains: list[Domain] = sorted((d for d in cfg.domains if d.enabled), key=lambda d: d.name)
    # Only transport domains receive mail; relay domains are outbound only.
    inbound = [d for d in domains if d.kind == "transport"]

    relay_domains, transport, tls_policy, verify = [HEADER], [HEADER], [HEADER], [HEADER]
    master: list[str] = []

    # ---- inbound: one queue (transport) per domain -------------------------------
    for d in inbound:
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
    # Mailtrap senders are checked like outbound mail (DKIM, no greylisting), as they would be in production.
    for t in cfg.mailtraps:
        for c in t.networks:
            local_addrs.append(f"{c}\n")
    out.files[f"{ETC}/relay_networks.list"] = "".join(plain)
    out.files[f"{PF}/relay_networks.cidr"] = "".join(cidr)
    out.files[f"{ETC}/rspamd/local_addrs.map"] = "".join(local_addrs)

    # ---- nftables: open the extra relay ports (and IMAP for the Mailtrap) ----------
    if cfg.mailtrap_enabled and any(t.enabled and t.imap for t in cfg.mailtraps):
        out.open_ports.append(IMAP_PORT)
    ports = ", ".join(str(p) for p in [*out.relay_ports, *out.open_ports])
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
    eod = f"check_client_access cidr:{PF}/outbound_hold.cidr, check_sasl_access hash:{PF}/outbound_hold_sasl"

    # ---- sender lock: linked relay IPs / accounts may only send as their domain -----
    # HOLD above does not end the checks, so a paused sender is still locked too.
    # Each locked domain gets a restriction class: OK for its own (sub)domains and
    # the empty bounce sender, otherwise a clear rejection.
    lock_ip, lock_sasl, classes = [HEADER], [HEADER], []
    locked = {d.name for d in domains if d.sender_lock}

    def lock_class(names: list[str]) -> str:
        cls = service_name("mfl_", "+".join(names))
        if cls not in classes:
            classes.append(cls)
            allowed = ", ".join(f"{n}=OK" for n in names)
            shown = " or ".join(f"@{n}" for n in names)
            out.main_cf[cls] = (f"check_sender_access inline:{{{allowed}, <>=OK}}, "
                                f"check_sender_access static:{{REJECT 5.7.1 This server may only send mail as {shown}}}")
        return cls

    for name in sorted(locked):
        lock_class([name])
    # Customer networks are always locked to that customer's own domains.
    for n in sorted(nets, key=lambda n: n.cidr):
        names = sorted(set(n.senders)) or ([n.customer] if n.customer in locked else [])
        if names:
            lock_ip.append(f"{n.cidr} {lock_class(names)}\n")
    for a in sorted(cfg.smtp_accounts, key=lambda a: a.username):
        if a.enabled and a.domain in locked:
            lock_sasl.append(f"{a.username} {lock_class([a.domain])}\n")
    out.files[f"{PF}/sender_lock.cidr"] = "".join(lock_ip)
    out.files[f"{PF}/sender_lock_sasl"] = "".join(lock_sasl)
    out.hash_maps.append(f"{PF}/sender_lock_sasl")
    classes += render_mailtrap(cfg, out, mail_ids)
    if classes:
        # Left out when empty: `postconf -n` prints an empty value without " = ", so
        # setting "" would look changed on every apply. Unset, it is removed (managed).
        out.main_cf["smtpd_restriction_classes"] = ", ".join(classes)
        eod += f", check_client_access cidr:{PF}/sender_lock.cidr, check_sasl_access hash:{PF}/sender_lock_sasl"
    out.main_cf["smtpd_end_of_data_restrictions"] = eod

    # ---- SMTP accounts (Dovecot passwd-file block) -----------------------------
    # Mailtrap logins that may send are in here too; mailtrap_sasl sends their mail to the trap.
    logins = [(a.username, a.password_hash) for a in cfg.smtp_accounts if a.enabled]
    logins += [(t.login, t.password_hash) for t in cfg.mailtraps
               if cfg.mailtrap_enabled and t.enabled and t.smtp_login and t.password_hash]
    out.users_block = "".join(f"{u}:{h}\n" for u, h in sorted(logins))
    # ---- custom rules (newsletter filter etc.) ------------------------------------
    out.files.update(render_rule_files(cfg.custom_rules, {d.name for d in domains}))
    return out


def render_mailtrap(cfg: GatewayConfig, out: Rendered, mail_ids: tuple[int, int]) -> list[str]:
    """Mailtrap: senders whose mail is caught instead of sent. Returns the restriction classes.

    main.cf and master.cf check mailtrap.cidr and mailtrap_sasl before anything
    that permits relaying. A match gives the mailbox's class: FILTER the whole
    message to the mftrap pipe (it stores the mail) and permit. When the
    Mailtrap or the mailbox is off the sender gets a refusal instead, so a test
    environment can never fall through to normal relaying."""
    classes: list[str] = []
    cidr, sasl = [HEADER], [HEADER]
    users, boxes = [HEADER], {}
    off = "REJECT 5.7.1 Mailtrap is switched off: mail from this test environment is not sent"
    uid, gid = mail_ids
    for t in sorted(cfg.mailtraps, key=lambda t: t.slug):
        on = cfg.mailtrap_enabled and t.enabled
        action = service_name("mft_", t.slug) if on else off
        if on:
            classes.append(action)
            out.main_cf[action] = f"check_client_access static:{{FILTER mftrap:{t.slug}}}, permit"
        label = _comment_line(t.name or t.slug)
        for c in t.networks:
            cidr.append(label + f"{c} {action}\n")
        if t.login:
            # A login that may only read (IMAP) must never send: refuse it on SMTP.
            sasl.append(f"{t.login} {action if t.smtp_login else 'REJECT 5.7.1 This login can only read the Mailtrap'}\n")
            if on and t.imap and t.password_hash:
                users.append(f"{t.login}:{t.password_hash}:{uid}:{gid}::{MAILTRAP_DIR}/{t.login}::\n")
        boxes[t.slug] = {"name": t.name, "login": t.login, "enabled": t.enabled, "mode": t.mode,
                         "allow": t.allow if t.mode == "except" else [], "retention_days": int(t.retention_days),
                         "max_messages": int(t.max_messages), "maildir": f"{MAILTRAP_DIR}/{t.login}/Maildir"}
        out.mailboxes.append(f"{MAILTRAP_DIR}/{t.login}")
    out.files[f"{PF}/mailtrap.cidr"] = "".join(cidr)
    out.files[f"{PF}/mailtrap_sasl"] = "".join(sasl)
    out.hash_maps.append(f"{PF}/mailtrap_sasl")
    out.files[MAILTRAP_USERS] = "".join(users)
    out.files[MAILTRAP_JSON] = json.dumps({"enabled": cfg.mailtrap_enabled, "boxes": boxes}, indent=2) + "\n"
    out.files[DOVECOT_MAILTRAP] = render_dovecot_mailtrap(cfg, IMAP_PORT in out.open_ports)
    return classes


def render_dovecot_mailtrap(cfg: GatewayConfig, imap: bool) -> str:
    """Included by /etc/dovecot/dovecot.conf. Only when a mailbox allows IMAP does
    Dovecot open port 993; logins come from mailtrap-users (userdb and passdb), so
    SMTP accounts cannot read anything."""
    if not imap:
        return "# Managed by Mailfilter (Mailtrap). No mailbox allows IMAP, so Dovecot serves no mailboxes.\n"
    host = cfg.hostname or "localhost"
    cert = cfg.tls_cert or f"/etc/letsencrypt/live/{host}/fullchain.pem"
    key = cfg.tls_key or f"/etc/letsencrypt/live/{host}/privkey.pem"
    return f"""# Managed by Mailfilter (Mailtrap). Changes here are overwritten.
# Testers read their Mailtrap mailbox with a mail program: IMAP over TLS on port {IMAP_PORT} only.
protocols {{
  imap = yes
}}
service imap-login {{
  inet_listener imap {{
    port = 0
  }}
  inet_listener imaps {{
    port = {IMAP_PORT}
    ssl = yes
  }}
}}
# Not "required": Postfix's SASL socket needs auth_allow_cleartext, which Dovecot 2.4 refuses next to it.
# There is no plain IMAP listener, so IMAP is TLS-only all the same.
ssl = yes
ssl_server_cert_file = {cert}
ssl_server_key_file = {key}
ssl_min_protocol = TLSv1.2

mail_driver = maildir
mail_path = ~/Maildir

protocol imap {{
  passdb mailtrap {{
    driver = passwd-file
    passwd_file_path = {MAILTRAP_USERS}
  }}
}}
userdb mailtrap {{
  driver = passwd-file
  passwd_file_path = {MAILTRAP_USERS}
}}
"""


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


def splice_users(current: str, block: str) -> str:
    """Put the web interface's SMTP accounts in the passwd-file between the markers."""
    managed = f"{USERS_BEGIN}\n{block}{USERS_END}\n"
    if USERS_BEGIN in current and USERS_END in current:
        head, rest = current.split(USERS_BEGIN, 1)
        _, tail = rest.split(USERS_END, 1)
        return head + managed + tail.lstrip("\n")
    return (current.rstrip("\n") + "\n" if current.strip() else "") + managed


def splice_master_cf(current: str, block: str) -> str:
    """Put the managed block in master.cf between the markers (added at the end if missing)."""
    managed = f"{BEGIN}\n{block.rstrip()}\n{END}\n" if block.strip() else f"{BEGIN}\n{END}\n"
    if BEGIN in current and END in current:
        head, rest = current.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        return head + managed + tail.lstrip("\n")
    return current.rstrip("\n") + "\n\n" + managed
