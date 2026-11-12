"""Read a PMG backup file into an :class:`ImportPlan`.

A PMG backup (``pmgbackup backup`` or *Backup/Restore > Backup now* in the PMG
web interface) is a ``.tgz`` that holds:

* ``config_backup.tar`` with ``/etc/pmg`` (domains, transports, networks, DKIM...)
* ``Proxmox_ruledb.sql`` with the rule database (welcome/blocklists, rules)
* ``version.txt`` and ``proxmox_backup_v1.md5``
* optionally ``Proxmox_statdb.sql`` (statistics, ignored here)

Nothing is extracted to disk: members are read into memory with size limits,
so a crafted archive cannot write files or fill the disk.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from typing import BinaryIO

from .model import AddressTransport, Dkim, Domain, ImportPlan, RelayNetwork, TlsPolicy, Transport
from .parsers import (
    parse_copy_dump,
    parse_domains,
    parse_mynetworks,
    parse_pmg_conf,
    parse_tls_inbound_domains,
    parse_tls_policy,
    parse_transport,
)
from .ruledb import apply_ruledb

MAX_MEMBER_BYTES = 64 * 1024 * 1024  # a single file we read; the rule DB is usually < 1 MB
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

RULEDB = "Proxmox_ruledb.sql"
CONFIG_TAR = "config_backup.tar"
VERSION = "version.txt"
CHECKSUMS = "proxmox_backup_v1.md5"

# Files in /etc/pmg we know about but do not take over, with the reason shown to the admin
NOT_IMPORTED = {
    "user.conf": ("PMG user accounts are not imported; create the accounts in Mailfilter (Users).",
                  "Gebruikers uit PMG worden niet overgenomen; maak de accounts aan in Mailfilter (Gebruikers)."),
    "ldap.conf": ("LDAP profiles are not supported by Mailfilter and were skipped.",
                  "LDAP-profielen worden niet ondersteund door Mailfilter en zijn overgeslagen."),
    "fetchmailrc": ("Fetchmail (POP3/IMAP fetching) is not supported by Mailfilter and was skipped.",
                    "Fetchmail (ophalen via POP3/IMAP) wordt niet ondersteund door Mailfilter en is overgeslagen."),
    "pbs.conf": ("Proxmox Backup Server targets are not imported; set up backups in Mailfilter (Backups).",
                 "Proxmox Backup Server-doelen worden niet overgenomen; stel backups in Mailfilter in (Backups)."),
    "cluster.conf": ("PMG cluster settings are not imported; Mailfilter clustering is set up separately.",
                     "PMG-clusterinstellingen worden niet overgenomen; clustering van Mailfilter stel je apart in."),
}


class BackupError(Exception):
    """The file is not a PMG backup we can read."""


def _norm(name: str) -> str:
    return name.lstrip("./").lstrip("/")


def _read_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if not member.isfile():
        raise BackupError(f"{member.name} is not a regular file")
    if member.size > MAX_MEMBER_BYTES:
        raise BackupError(f"{member.name} is too large ({member.size} bytes)")
    f = tar.extractfile(member)
    if f is None:
        raise BackupError(f"cannot read {member.name}")
    return f.read()


def _outer_files(source: str | os.PathLike | BinaryIO) -> dict[str, bytes]:
    """The top-level files of the backup, from a .tgz/.tar or an unpacked directory."""
    wanted = {RULEDB, CONFIG_TAR, VERSION, CHECKSUMS}
    if isinstance(source, (str, os.PathLike)) and Path(source).is_dir():
        out = {}
        for name in wanted:
            p = Path(source) / name
            if p.is_file():
                if p.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise BackupError(f"{name} is too large")
                out[name] = p.read_bytes()
        return out

    try:
        if isinstance(source, (str, os.PathLike)):
            if Path(source).stat().st_size > MAX_ARCHIVE_BYTES:
                raise BackupError("the backup file is too large")
            tar = tarfile.open(source, mode="r:*")
        else:
            tar = tarfile.open(fileobj=source, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise BackupError(f"not a readable .tgz/.tar file: {exc}") from exc

    out = {}
    with tar:
        for member in tar:
            name = _norm(member.name)
            if name in wanted:
                if name == CONFIG_TAR and member.size > MAX_ARCHIVE_BYTES:
                    raise BackupError("config_backup.tar is too large")
                if name != CONFIG_TAR and member.size > MAX_MEMBER_BYTES:
                    raise BackupError(f"{name} is too large")
                f = tar.extractfile(member) if member.isfile() else None
                if f is not None:
                    out[name] = f.read()
            elif name == "etc/pmg/pmg.conf":
                # Someone uploaded config_backup.tar itself instead of the .tgz
                raise BackupError(
                    "this looks like config_backup.tar; please upload the whole pmg-backup_*.tgz file"
                )
    return out


def _config_files(data: bytes) -> dict[str, bytes]:
    """Files from config_backup.tar, keyed by path relative to /etc/pmg (plus spamassassin files)."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = _norm(member.name)
                if name.startswith("etc/pmg/"):
                    files[name[len("etc/pmg/") :]] = _read_member(tar, member)
                elif name.startswith("etc/mail/spamassassin/"):
                    files["spamassassin/" + name.rsplit("/", 1)[-1]] = _read_member(tar, member)
    except tarfile.TarError as exc:
        raise BackupError(f"config_backup.tar is damaged: {exc}") from exc
    return files


def _text(data: bytes | None) -> str:
    if data is None:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _flag(section: dict[str, str], key: str, default: bool) -> bool:
    if key not in section:
        return default
    return section[key].strip() not in ("0", "", "no", "false")


def _num(section: dict[str, str], key: str, default: int | None = None) -> int | None:
    try:
        return int(section[key])
    except (KeyError, ValueError):
        return default


def _map_settings(conf: dict[str, dict[str, str]]) -> dict:
    """PMG settings that have a Mailfilter counterpart, with PMG's defaults filled in."""
    mail = conf.get("mail", {})
    spam = conf.get("spam", {})
    spamquar = conf.get("spamquar", {})
    virusquar = conf.get("virusquar", {})
    clamav = conf.get("clamav", {})
    s: dict = {
        "smtp.max_message_size": _num(mail, "maxsize", 10485760),
        "smtp.banner": mail.get("banner", "ESMTP Proxmox"),
        "smtp.hide_received": _flag(mail, "hide_received", False),
        "smtp.tls_inbound": _flag(mail, "tls", False),
        "smtp.smtputf8": _flag(mail, "smtputf8", True),
        "smtp.reject_unknown_client": _flag(mail, "rejectunknown", False),
        "smtp.reject_unknown_sender_domain": _flag(mail, "rejectunknownsender", False),
        "smtp.helo_checks": _flag(mail, "helotests", False),
        "smtp.verify_recipients": mail.get("verifyreceivers", ""),
        "smtp.ndr_on_block": _flag(mail, "ndr_on_block", False),
        "antispam.greylisting": _flag(mail, "greylist", True),
        "antispam.greylisting_ipv6": _flag(mail, "greylist6", False),
        "antispam.spf": _flag(mail, "spf", True),
        "antispam.dnsbl_sites": [x for x in mail.get("dnsbl_sites", "").replace(";", ",").split(",") if x.strip()],
        "antispam.dnsbl_threshold": _num(mail, "dnsbl_threshold", 1),
        "antispam.bayes": _flag(spam, "use_bayes", False),
        "antispam.rbl_checks": _flag(spam, "rbl_checks", True),
        "antispam.max_scan_size": _num(spam, "maxspamsize", 262144),
        "throttle.client_message_rate_per_minute": _num(mail, "message_rate_limit", 0),
        "throttle.client_connection_rate_per_minute": _num(mail, "conn_rate_limit", 0),
        "throttle.client_connection_count": _num(mail, "conn_count_limit", 50),
        "quarantine.spam_lifetime_days": _num(spamquar, "lifetime", 7),
        "quarantine.virus_lifetime_days": _num(virusquar, "lifetime", 7),
        "antivirus.block_encrypted_archives": _flag(clamav, "archiveblockencrypted", False),
        "antivirus.max_scan_size": _num(clamav, "maxscansize", 100000000),
        "queue.lifetime_days": _num(mail, "queue-lifetime", 5),
    }
    if mail.get("smarthost"):
        s["outbound.smarthost"] = mail["smarthost"]
        s["outbound.smarthost_port"] = _num(mail, "smarthostport", 25)
    return s


def read_backup(source: str | os.PathLike | BinaryIO) -> ImportPlan:
    """Read a PMG backup and describe everything that can be imported."""
    outer = _outer_files(source)
    if CONFIG_TAR not in outer and RULEDB not in outer:
        raise BackupError("no PMG backup found: the file has no config_backup.tar or Proxmox_ruledb.sql")

    plan = ImportPlan()

    # version.txt: "product: pmg-api\nversion: 8.1.4\nbackuptime:1700000000:Tue Nov ..."
    for line in _text(outer.get(VERSION)).splitlines():
        key, _, value = line.partition(":")
        if key == "product":
            plan.source_product = value.strip()
        elif key == "version":
            plan.source_version = value.strip()
        elif key == "backuptime":
            plan.backup_time = value.partition(":")[2].strip() or value.strip()

    for line in _text(outer.get(CHECKSUMS)).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in outer and hashlib.md5(outer[parts[1]]).hexdigest() != parts[0]:
            plan.warn("checksum", f"{parts[1]} does not match the checksum in the backup; the file may be damaged.",
                      f"{parts[1]} klopt niet met de controlesom in de backup; het bestand is mogelijk beschadigd.")

    files = _config_files(outer[CONFIG_TAR]) if CONFIG_TAR in outer else {}
    if not files:
        plan.warn("no_config", "The backup has no /etc/pmg configuration; only the rule database was read.",
                  "De backup bevat geen configuratie uit /etc/pmg; alleen de regeldatabase is gelezen.")

    conf = parse_pmg_conf(_text(files.get("pmg.conf")))
    plan.raw_config = conf
    plan.settings = _map_settings(conf)
    plan.admin_email = conf.get("admin", {}).get("email", "")
    plan.hostname = conf.get("spamquar", {}).get("hostname", "")

    def bad_lines(fname: str, errors: list[str]) -> None:
        for line in errors:
            plan.warn("parse_error", f"Line in {fname} could not be read and was skipped: {line!r}",
                      f"Regel in {fname} kon niet worden gelezen en is overgeslagen: {line!r}")

    # Relay domains + transports
    domains, errors = parse_domains(_text(files.get("domains")))
    bad_lines("domains", errors)
    transports, errors = parse_transport(_text(files.get("transport")))
    bad_lines("transport", errors)
    by_match = {t["match"]: t["transport"] for t in transports}

    mail = conf.get("mail", {})
    default_transport = None
    if mail.get("relay"):
        default_transport = Transport(
            host=mail["relay"],
            port=_num(mail, "relayport", 25) or 25,
            protocol=mail.get("relayprotocol", "smtp"),
            use_mx=not _flag(mail, "relaynomx", False),
            comment="PMG default relay",
        )

    tls_inbound = {d["domain"] for d in parse_tls_inbound_domains(_text(files.get("tls_inbound_domains")))[0]}

    dkim_domains = [d["domain"] for d in parse_domains(_text(files.get("dkim/domains")))[0]]
    seen = set()
    for d in domains:
        name = d["domain"]
        if name in seen:
            continue
        seen.add(name)
        transport = by_match.pop(name, None) or default_transport
        if transport is None:
            plan.warn("no_transport", f"Domain {name} has no transport and PMG has no default relay; set one after import.",
                      f"Domein {name} heeft geen transport en PMG heeft geen standaard-relay; stel er een in na de import.")
        plan.domains.append(
            Domain(
                name=name,
                comment=d["comment"],
                transport=transport,
                dkim_sign=name in dkim_domains,
                require_tls_inbound=name in tls_inbound,
            )
        )
    for match, transport in by_match.items():
        plan.address_transports.append(AddressTransport(match=match, transport=transport))

    # Trusted networks = who may relay
    nets, errors = parse_mynetworks(_text(files.get("mynetworks")))
    bad_lines("mynetworks", errors)
    plan.relay_networks = [RelayNetwork(**n) for n in nets]

    policies, errors = parse_tls_policy(_text(files.get("tls_policy")))
    bad_lines("tls_policy", errors)
    plan.tls_policies = [TlsPolicy(**p) for p in policies]

    # DKIM: one selector + key for all signing domains
    admin = conf.get("admin", {})
    selector = admin.get("dkim_selector", "")
    if selector or dkim_domains:
        key = files.get(f"dkim/{selector}.private") if selector else None
        plan.dkim = Dkim(
            selector=selector,
            sign_all_mail=_flag(admin, "dkim_sign_all_mail", False),
            private_key_pem=_text(key) if key else None,
            domains=dkim_domains,
        )
        if not _flag(admin, "dkim_sign", False):
            plan.warn("dkim_off", "DKIM signing was switched off in PMG; check DKIM after importing the key.",
                      "DKIM-ondertekening stond uit in PMG; controleer DKIM nadat je de sleutel hebt overgenomen.")
        if selector and not key:
            plan.warn("dkim_key_missing", f"The DKIM key for selector '{selector}' is not in the backup; a new key is needed.",
                      f"De DKIM-sleutel voor selector '{selector}' zit niet in de backup; er is een nieuwe sleutel nodig.")

    # Rule database
    if RULEDB in outer:
        apply_ruledb(plan, parse_copy_dump(_text(outer[RULEDB])))
    else:
        plan.warn("no_ruledb", "The backup has no rule database; welcome/blocklists were not imported.",
                  "De backup bevat geen regeldatabase; welcome- en blocklists zijn niet overgenomen.")

    for fname, (reason, reason_nl) in NOT_IMPORTED.items():
        if files.get(fname, b"").strip():
            plan.warn(f"skipped_{fname.split('.')[0]}", reason, reason_nl)
    custom_sa = _text(files.get("spamassassin/custom.cf"))
    if any(line.strip() and not line.lstrip().startswith("#") for line in custom_sa.splitlines()):
        plan.warn(
            "custom_spamassassin",
            "PMG has custom SpamAssassin rules (custom.cf). Mailfilter uses Rspamd; these were not converted.",
            "PMG heeft eigen SpamAssassin-regels (custom.cf). Mailfilter gebruikt Rspamd; deze zijn niet omgezet.",
        )

    return plan
