import io
import json
import tarfile
import unittest

from mailfilter_pmg import BackupError, read_backup
from mailfilter_pmg.__main__ import main
from mailfilter_pmg.parsers import decode_bytea, parse_copy_dump, parse_mynetworks, parse_transport

from . import fixtures


def plan_from_fixture(**kw):
    return read_backup(io.BytesIO(fixtures.backup_tgz(**kw)))


class ParserTests(unittest.TestCase):
    def test_transport_brackets_mean_no_mx(self):
        rows, errors = parse_transport("a.nl smtp:[10.0.0.1]:25\nb.nl smtp:mx.b.nl:2525\nc.nl smtp:[ipv6:2001:db8::1]:25\n")
        self.assertEqual(errors, [])
        a, b, c = (r["transport"] for r in rows)
        self.assertEqual((a.host, a.use_mx), ("10.0.0.1", False))
        self.assertEqual((b.host, b.port, b.use_mx), ("mx.b.nl", 2525, True))
        self.assertEqual(c.host, "2001:db8::1")

    def test_mynetworks_normalises_and_reports_bad_lines(self):
        rows, errors = parse_mynetworks("192.0.2.1/24 #x\n2001:DB8::/64\nbogus\n")
        self.assertEqual([r["cidr"] for r in rows], ["192.0.2.0/24", "2001:db8::/64"])
        self.assertEqual(errors, ["bogus"])

    def test_copy_dump_and_bytea(self):
        sql = "COPY object (id, value) FROM stdin;\n1\t\\\\x6162\n2\t\\N\n\\.\n"
        rows = parse_copy_dump(sql)["object"]
        self.assertEqual(decode_bytea(rows[0]["value"]), "ab")
        self.assertIsNone(rows[1]["value"])

    def test_bytea_escape_format(self):
        self.assertEqual(decode_bytea("a\\\\b\\101"), "a\\bA")


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_from_fixture()

    def test_version(self):
        self.assertEqual(self.plan.source_version, "8.2.3")
        self.assertIn("2026", self.plan.backup_time)

    def test_domains_and_transports(self):
        d = {x.name: x for x in self.plan.domains}
        self.assertEqual(set(d), {"rhis.nl", "klant.nl", "shop.example"})
        self.assertEqual(d["rhis.nl"].comment, "Hoofddomein")
        # rhis.nl has no own transport -> PMG default relay, without MX lookup
        self.assertEqual((d["rhis.nl"].transport.host, d["rhis.nl"].transport.use_mx), ("10.0.0.10", False))
        self.assertEqual(d["klant.nl"].transport.host, "mail.klant.nl")
        self.assertTrue(d["klant.nl"].require_tls_inbound)
        self.assertEqual(d["shop.example"].transport.port, 2525)
        self.assertTrue(d["rhis.nl"].dkim_sign and d["shop.example"].dkim_sign)
        self.assertFalse(d["klant.nl"].dkim_sign)
        self.assertEqual([a.match for a in self.plan.address_transports], ["info@special.example"])
        self.assertEqual(self.plan.address_transports[0].transport.protocol, "lmtp")

    def test_relay_networks(self):
        nets = {n.cidr: n.comment for n in self.plan.relay_networks}
        self.assertEqual(nets, {"192.0.2.10/32": "Webserver 1", "2001:db8::/64": "Webservers IPv6", "198.51.100.0/24": ""})
        self.assertTrue(any("not-a-network" in w.message for w in self.plan.warnings))

    def test_lists(self):
        got = {(e.list, e.scope, e.kind, e.value) for e in self.plan.list_entries}
        self.assertIn(("block", "global", "domain", "spammer.example"), got)
        self.assertIn(("block", "global", "ip", "203.0.113.66"), got)
        self.assertIn(("block", "global", "regex", ".*@bad\\.example"), got)
        self.assertIn(("welcome", "global", "email", "factuur@leverancier.nl"), got)
        self.assertIn(("welcome", "global", "network", "192.0.2.0/24"), got)
        self.assertIn(("welcome", "smtp", "ip", "198.51.100.7"), got)
        self.assertIn(("welcome", "user:jan@rhis.nl", "domain", "partner.example"), got)
        self.assertIn(("block", "user:jan@rhis.nl", "email", "spam@nogmeer.example"), got)
        values = {e.value for e in self.plan.list_entries}
        self.assertNotIn("nomail@fromthisdomain.example", values)  # PMG placeholder
        self.assertNotIn("ldapgroup", values)
        self.assertNotIn("ceo@rhis.nl", values)  # belongs to a custom group, not a list

    def test_rules(self):
        rules = {r.name: r for r in self.plan.rules}
        self.assertTrue(rules["Blocklist"].is_default)
        self.assertEqual(rules["Disclaimer directie"].handled_by, "")
        self.assertEqual(rules["Disclaimer directie"].direction, "out")
        self.assertIn("Disclaimer", rules["Disclaimer directie"].actions)
        self.assertIn("ceo@rhis.nl", rules["Disclaimer directie"].from_groups[0])
        self.assertEqual(rules["Old test rule"].handled_by, "Not active in PMG, skipped")
        self.assertEqual(self.plan.summary()["rules_to_review"], 1)

    def test_settings(self):
        s = self.plan.settings
        self.assertEqual(s["smtp.max_message_size"], 52428800)
        self.assertEqual(s["antispam.dnsbl_sites"], ["zen.spamhaus.org", "bl.spamcop.net"])
        self.assertEqual(s["outbound.smarthost"], "smtp.provider.example")
        self.assertEqual(s["quarantine.spam_lifetime_days"], 14)
        self.assertTrue(s["antivirus.block_encrypted_archives"])
        self.assertEqual(self.plan.admin_email, "postmaster@rhis.nl")

    def test_dkim_key_hidden_by_default(self):
        self.assertIn("BEGIN RSA PRIVATE KEY", self.plan.dkim.private_key_pem)
        self.assertEqual(self.plan.dkim.selector, "mf2024")
        self.assertEqual(self.plan.to_dict()["dkim"]["private_key_pem"], "(present, hidden)")
        self.assertIn("BEGIN", self.plan.to_dict(include_secrets=True)["dkim"]["private_key_pem"])

    def test_warnings(self):
        codes = {w.code for w in self.plan.warnings}
        self.assertIn("custom_rules", codes)
        self.assertIn("custom_spamassassin", codes)
        self.assertIn("ldap_object", codes)
        self.assertIn("skipped_user", codes)
        self.assertNotIn("checksum", codes)

    def test_checksum_mismatch_is_reported(self):
        codes = {w.code for w in plan_from_fixture(corrupt_checksum=True).warnings}
        self.assertIn("checksum", codes)

    def test_plan_is_json_serialisable(self):
        json.dumps(self.plan.to_dict())


class BadInputTests(unittest.TestCase):
    def test_not_a_tar(self):
        with self.assertRaises(BackupError):
            read_backup(io.BytesIO(b"hello"))

    def test_inner_tar_uploaded(self):
        with self.assertRaisesRegex(BackupError, "whole pmg-backup"):
            read_backup(io.BytesIO(fixtures.config_tar()))

    def test_unrelated_tar(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("readme.txt")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"hi"))
        buf.seek(0)
        with self.assertRaisesRegex(BackupError, "no PMG backup"):
            read_backup(buf)

    def test_path_traversal_names_are_harmless(self):
        # Members are only read into memory, never written; odd names are just ignored
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in (("../../etc/passwd", b"x"), ("./config_backup.tar", fixtures.config_tar())):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        self.assertEqual(len(read_backup(buf).domains), 3)


class CliTests(unittest.TestCase):
    def test_inspect(self):
        import contextlib
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".tgz") as f:
            f.write(fixtures.backup_tgz())
            f.flush()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["inspect", f.name]), 0)
            self.assertIn("klant.nl", out.getvalue())
            self.assertIn("MANUAL CHECK", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["inspect", f.name, "--json"])
            self.assertEqual(len(json.loads(out.getvalue())["domains"]), 3)


if __name__ == "__main__":
    unittest.main()
