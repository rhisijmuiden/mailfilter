"""Build a PMG backup in memory, laid out exactly like ``pmgbackup backup`` makes it."""

from __future__ import annotations

import hashlib
import io
import tarfile

PMG_CONF = """\
section: admin
\tdkim_selector mf2024
\tdkim_sign 1
\temail postmaster@rhis.nl

section: clamav
\tarchiveblockencrypted 1

section: mail
\tbanner ESMTP mx.rhis.nl
\tdnsbl_sites zen.spamhaus.org,bl.spamcop.net
\tgreylist 1
\tmaxsize 52428800
\tmessage_rate_limit 120
\trelay 10.0.0.10
\trelaynomx 1
\tsmarthost smtp.provider.example
\tsmarthostport 587
\tspf 1

section: spam
\tuse_bayes 1

section: spamquar
\thostname pmg.rhis.nl
\tlifetime 14
"""

DOMAINS = """\
#Hoofddomein
rhis.nl 1
klant.nl 1
#Webshop klant
shop.example 1
"""

TRANSPORT = """\
#Exchange klant
klant.nl smtp:[mail.klant.nl]:25
shop.example smtp:mx.shop.example:2525
info@special.example lmtp:inet:10.0.0.20:24
"""

MYNETWORKS = """\
192.0.2.10/32 #Webserver 1
2001:db8::/64 #Webservers IPv6
198.51.100.0/24 #*
not-a-network #oops
"""

TLS_POLICY = "bank.example encrypt\n"
TLS_INBOUND = "klant.nl reject_plaintext_session\n"
DKIM_DOMAINS = "rhis.nl 1\nshop.example 1\n"
DKIM_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfake\n-----END RSA PRIVATE KEY-----\n"
CUSTOM_CF = "# custom\nscore URIBL_BLACK 5\n"


def _hex(value: str) -> str:
    # bytea in hex format, with the backslash doubled by COPY's text escaping
    return "\\\\x" + value.encode("utf-8").hex()


def _copy(table: str, cols: list[str], rows: list[list[str]]) -> str:
    body = "".join("\t".join(r) + "\n" for r in rows)
    return f"COPY {table} ({', '.join(cols)}) FROM stdin;\n{body}\\.\n\n"


def ruledb_sql() -> str:
    groups = [
        ["1", "Blocklist", "Global blocklist", "who"],
        ["2", "Welcomelist", "Global welcomelist", "who"],
        ["3", "Office Hours", "Usual office hours", "when"],
        ["4", "Block", "Block mail", "action"],
        ["5", "Accept", "Accept mail", "action"],
        ["6", "Spam (Level 3)", "", "what"],
        ["7", "Quarantine", "", "action"],
        ["8", "SMTP welcomelist", "", "greylist"],
        ["9", "Directie", "Directie ontvangers", "who"],
        ["10", "Disclaimer", "", "action"],
    ]
    objects = [
        ["1", "1001", "1", _hex("nomail@fromthisdomain.example")],
        ["2", "1001", "2", _hex("mail@fromthisdomain.example")],
        ["3", "1002", "1", _hex("spammer.example")],
        ["4", "1003", "1", _hex("203.0.113.66")],
        ["5", "1000", "1", _hex(".*@bad\\.example")],
        ["6", "1001", "2", _hex("factuur@leverancier.nl")],
        ["7", "1004", "2", _hex("192.0.2.0/24")],
        ["8", "2000", "3", _hex("08:00-16:00")],
        ["9", "4001", "4", "\\N"],
        ["10", "4000", "5", "\\N"],
        ["11", "3000", "6", _hex("3")],
        ["12", "4006", "7", "\\N"],
        ["13", "1003", "8", _hex("198.51.100.7")],
        ["14", "1007", "9", _hex("ceo@rhis.nl")],
        ["15", "4009", "10", "\\N"],
        ["16", "1005", "2", _hex("ldapgroup")],
    ]
    rules = [
        ["1", "Blocklist", "98", "1", "0", "0"],
        ["2", "Welcomelist", "85", "1", "0", "0"],
        ["3", "Quarantine/Mark Spam (Level 3)", "80", "1", "0", "0"],
        ["4", "Disclaimer directie", "50", "1", "1", "0"],
        ["5", "Old test rule", "10", "0", "2", "0"],
    ]
    rulegroups = [
        ["1", "1", "0"],
        ["4", "1", "4"],
        ["2", "2", "0"],
        ["5", "2", "4"],
        ["6", "3", "3"],
        ["7", "3", "4"],
        ["9", "4", "0"],
        ["10", "4", "4"],
    ]
    userprefs = [
        ["jan@rhis.nl", "WL", "nieuwsbrief@vereniging.nl, @partner.example", "1700000000"],
        ["jan@rhis.nl", "BL", "spam@nogmeer.example", "1700000000"],
    ]
    return (
        _copy("objectgroup", ["id", "name", "info", "class"], groups)
        + "SELECT setval('objectgroup_id_seq', max(id)) FROM objectgroup;\n\n"
        + _copy("object", ["id", "objecttype", "objectgroup_id", "value"], objects)
        + _copy("attribut", ["object_id", "name", "value"], [])
        + _copy("objectgroup_attributes", ["objectgroup_id", "name", "value"], [])
        + _copy("rule", ["id", "name", "priority", "active", "direction", "count"], rules)
        + _copy("rule_attributes", ["rule_id", "name", "value"], [])
        + _copy("rulegroup", ["objectgroup_id", "rule_id", "grouptype"], rulegroups)
        + _copy("userprefs", ["pmail", "name", "data", "mtime"], userprefs)
    )


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def config_tar(extra: dict[str, str] | None = None) -> bytes:
    files = {
        "etc/pmg/pmg.conf": PMG_CONF,
        "etc/pmg/domains": DOMAINS,
        "etc/pmg/transport": TRANSPORT,
        "etc/pmg/mynetworks": MYNETWORKS,
        "etc/pmg/tls_policy": TLS_POLICY,
        "etc/pmg/tls_inbound_domains": TLS_INBOUND,
        "etc/pmg/dkim/domains": DKIM_DOMAINS,
        "etc/pmg/dkim/mf2024.private": DKIM_KEY,
        "etc/pmg/user.conf": "root@pam:1:0::root@rhis.nl:root::\n",
        "etc/mail/spamassassin/custom.cf": CUSTOM_CF,
    }
    files.update(extra or {})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in files.items():
            _add(tar, name, text.encode("utf-8"))
    return buf.getvalue()


def backup_tgz(corrupt_checksum: bool = False) -> bytes:
    cfg = config_tar()
    db = ruledb_sql().encode("utf-8")
    version = b"product: pmg-api\nversion: 8.2.3\nbackuptime:1762900000:Tue Nov 11 22:00:00 2026\n"
    md5 = lambda b: hashlib.md5(b).hexdigest()  # noqa: E731
    sums = f"{md5(cfg)}  config_backup.tar\n{'0' * 32 if corrupt_checksum else md5(db)}  Proxmox_ruledb.sql\n{md5(version)}  version.txt\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add(tar, "./Proxmox_ruledb.sql", db)
        _add(tar, "./config_backup.tar", cfg)
        _add(tar, "./version.txt", version)
        _add(tar, "./proxmox_backup_v1.md5", sums.encode())
    return buf.getvalue()
