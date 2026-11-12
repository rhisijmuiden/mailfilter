"""Mailtrap: test environments whose mail is caught instead of sent."""
import json
import os
import tempfile
import unittest

from mailfilter_config import Applier, ApplyError, GatewayConfig, MailTrap, RelayNetwork, SmtpAccount, render, validate
from mailfilter_config.render import DOVECOT_MAILTRAP, MAILTRAP_JSON, MAILTRAP_USERS, service_name

from .test_config import FakeServer

HASH = "{SSHA512}" + "A" * 80
PF = "/etc/mailfilter/postfix"


def cfg(**kw) -> GatewayConfig:
    return GatewayConfig(
        hostname="mailfilter.rhis.nl",
        relay_networks=[RelayNetwork("185.12.44.10", "Magento")],
        smtp_accounts=[],
        mailtraps=[
            MailTrap("webshop-staging", "Webshop staging", ["10.0.8.21", "10.0.8.0/30", "2a01:4f8:c0c:1a2::50"],
                     password_hash=HASH, smtp_login=True, imap=True, mode="except", allow=["@RHIS.nl", "Tester@Rhis.NL"]),
            MailTrap("crm", "CRM", ["10.1.1.1"], password_hash=HASH),
        ],
        **kw,
    )


def codes(c):
    return [p.code for p in validate(c)]


class MailTrapValidateTest(unittest.TestCase):
    def test_valid_and_normalised(self):
        c = cfg(mailtrap_enabled=True)
        self.assertEqual(validate(c), [])
        t = c.mailtraps[0]
        self.assertEqual(t.login, "trap-webshop-staging")
        self.assertEqual(t.allow, ["@rhis.nl", "tester@rhis.nl"])
        self.assertEqual(t.networks, ["10.0.8.21", "10.0.8.0/30", "2a01:4f8:c0c:1a2::50"])

    def test_relay_network_cannot_be_a_trap(self):
        c = cfg()
        c.mailtraps[1].networks = ["185.12.44.0/24"]
        self.assertIn("mailtrap_network_is_relay", codes(c))

    def test_own_and_broad_networks(self):
        c = cfg(own_addresses=["10.1.1.1"])
        self.assertIn("mailtrap_own_address", codes(c))
        c = cfg()
        c.mailtraps[1].networks = ["127.0.0.1"]
        self.assertIn("mailtrap_own_address", codes(c))
        c.mailtraps[1].networks = ["10.0.0.0/8"]
        self.assertIn("network_too_broad", codes(c))

    def test_duplicates_and_logins(self):
        c = cfg()
        c.mailtraps[1].networks = ["10.0.8.21"]
        self.assertIn("network_duplicate", codes(c))
        c = cfg()
        c.smtp_accounts = [SmtpAccount("trap-crm", HASH, "rhis.nl")]
        self.assertIn("account_duplicate", codes(c))
        c = cfg()
        c.mailtraps[0].password_hash = "nope"
        self.assertIn("account_hash_invalid", codes(c))
        c = cfg()
        c.mailtraps[0].allow = ["not an address"]
        self.assertIn("address_invalid", codes(c))


class MailTrapRenderTest(unittest.TestCase):
    def test_on(self):
        c = cfg(mailtrap_enabled=True)
        validate(c)
        r = render(c, (998, 997))
        cls = service_name("mft_", "webshop-staging")
        self.assertEqual(r.main_cf[cls], "check_client_access static:{FILTER mftrap:webshop-staging}, permit")
        self.assertIn(cls, r.main_cf["smtpd_restriction_classes"])
        cidr = r.files[f"{PF}/mailtrap.cidr"]
        self.assertIn(f"10.0.8.0/30 {cls}\n", cidr)
        self.assertIn(f"2a01:4f8:c0c:1a2::50 {cls}\n", cidr)
        sasl = r.files[f"{PF}/mailtrap_sasl"]
        self.assertIn(f"trap-webshop-staging {cls}\n", sasl)
        # A login without SMTP may only read: refused on SMTP, not relayed
        self.assertIn("trap-crm REJECT 5.7.1", sasl)
        self.assertIn(f"{PF}/mailtrap_sasl", r.hash_maps)
        # Only logins that may send are SMTP users; only IMAP mailboxes are IMAP users
        self.assertIn("trap-webshop-staging:", r.users_block)
        self.assertNotIn("trap-crm", r.users_block)
        self.assertIn("trap-webshop-staging:" + HASH + ":998:997::/var/lib/mailfilter/mailtrap/trap-webshop-staging::",
                      r.files[MAILTRAP_USERS])
        self.assertNotIn("trap-crm", r.files[MAILTRAP_USERS])
        # Treated as outbound by Rspamd (DKIM, no greylisting)
        self.assertIn("10.0.8.21\n", r.files["/etc/mailfilter/rspamd/local_addrs.map"])
        # Not in mynetworks: a trap IP can never relay through permit_mynetworks
        self.assertNotIn("10.0.8.21", r.files[f"{PF}/relay_networks.cidr"])
        box = json.loads(r.files[MAILTRAP_JSON])["boxes"]["webshop-staging"]
        self.assertEqual(box["maildir"], "/var/lib/mailfilter/mailtrap/trap-webshop-staging/Maildir")
        self.assertEqual(box["allow"], ["@rhis.nl", "tester@rhis.nl"])
        # IMAP on 993: Dovecot part and firewall
        self.assertIn("port = 993", r.files[DOVECOT_MAILTRAP])
        self.assertIn("/etc/letsencrypt/live/mailfilter.rhis.nl/fullchain.pem", r.files[DOVECOT_MAILTRAP])
        self.assertIn("993", r.files["/etc/mailfilter/nftables/relay_ports.nft"])
        self.assertIn("/var/lib/mailfilter/mailtrap/trap-crm", r.mailboxes)

    def test_off_refuses_and_closes_imap(self):
        c = cfg(mailtrap_enabled=False)
        validate(c)
        r = render(c)
        self.assertFalse(any(k.startswith("mft_") for k in r.main_cf))
        self.assertIn("10.0.8.21 REJECT 5.7.1 Mailtrap is switched off", r.files[f"{PF}/mailtrap.cidr"])
        self.assertIn("trap-webshop-staging REJECT", r.files[f"{PF}/mailtrap_sasl"])
        self.assertNotIn("port = 993", r.files[DOVECOT_MAILTRAP])
        self.assertNotIn("993", r.files["/etc/mailfilter/nftables/relay_ports.nft"])
        self.assertNotIn("trap-", r.files[MAILTRAP_USERS])
        self.assertNotIn("trap-", r.users_block)

    def test_disabled_mailbox_refuses(self):
        c = cfg(mailtrap_enabled=True)
        c.mailtraps[1].enabled = False
        validate(c)
        r = render(c)
        self.assertIn("10.1.1.1 REJECT", r.files[f"{PF}/mailtrap.cidr"])

    def test_relay_listeners_check_the_mailtrap_first(self):
        c = cfg(mailtrap_enabled=True)
        c.relay_ports = []
        from mailfilter_config import RelayPort
        c.relay_ports.append(RelayPort(2525))
        validate(c)
        block = render(c).master_cf_block
        self.assertIn("smtpd_relay_restrictions=check_client_access,cidr:/etc/mailfilter/postfix/mailtrap.cidr,"
                      "check_sasl_access,hash:/etc/mailfilter/postfix/mailtrap_sasl,permit_mynetworks", block)
        # master.cf -o values end at whitespace
        for line in block.splitlines():
            if line.strip().startswith("-o "):
                self.assertNotIn(" ", line.strip()[3:], line)

    def test_json_roundtrip(self):
        c = cfg(mailtrap_enabled=True)
        again = GatewayConfig.from_json(c.to_json())
        self.assertEqual(again.mailtraps, c.mailtraps)
        self.assertTrue(again.mailtrap_enabled)


class MailTrapApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(f"{self.root}/etc/postfix")
        with open(f"{self.root}/etc/postfix/master.cf", "w") as fh:
            fh.write("smtp inet n - y - 1 postscreen\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_refused_when_postfix_has_no_mailtrap_checks(self):
        server = FakeServer()
        with self.assertRaises(ApplyError) as e:
            Applier(runner=server, root=self.root, reload=False).apply(cfg(mailtrap_enabled=True))
        self.assertEqual(e.exception.step, "mailtrap")

    def test_apply_makes_maildirs_and_checks_dovecot(self):
        server = FakeServer()
        orig = server.__call__

        def runner(cmd):
            cmd = list(cmd)
            if cmd[:3] == ["postconf", "-h", "smtpd_relay_restrictions"]:
                import subprocess
                return subprocess.CompletedProcess(cmd, 0, "check_client_access cidr:/x/mailtrap.cidr\n", "")
            return orig(cmd)

        Applier(runner=runner, root=self.root, reload=False).apply(cfg(mailtrap_enabled=True))
        self.assertTrue(os.path.isdir(f"{self.root}/var/lib/mailfilter/mailtrap/trap-crm/Maildir/new"))
        self.assertIn(["doveconf", "-n"], server.calls)
        self.assertTrue(any(c[:2] == ["postmap", f"hash:{self.root}{PF}/mailtrap_sasl"] for c in server.calls))
