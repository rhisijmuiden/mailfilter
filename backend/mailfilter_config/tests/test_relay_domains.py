"""Relay domains (outbound only), SMTP accounts and the sender lock."""
import os
import tempfile
import unittest

from mailfilter_config import Applier, Destination, Domain, GatewayConfig, RelayNetwork, SmtpAccount, render, validate
from mailfilter_config.render import USERS_BEGIN, USERS_END, service_name, splice_users

from .test_config import FakeServer

HASH = "{SSHA512}" + "A" * 80


def cfg() -> GatewayConfig:
    return GatewayConfig(
        hostname="mailfilter.rhis.nl",
        domains=[
            Domain(name="rhis.nl", destinations=[Destination("mail.rhis.nl")]),
            Domain(name="Webshop-DeVries.nl", kind="relay", sender_lock=True),
            Domain(name="nieuwsbrief.rhis.nl", kind="relay"),
        ],
        relay_networks=[RelayNetwork("185.12.44.10", "Magento", "webshop-devries.nl"),
                        RelayNetwork("2a01:4f8:c0c:1a2::10", "Magento v6", "webshop-devries.nl"),
                        RelayNetwork("203.0.113.9", "Newsletter", "nieuwsbrief.rhis.nl")],
        smtp_accounts=[SmtpAccount("smtp@webshop-devries.nl", HASH, "webshop-devries.nl"),
                       SmtpAccount("news", HASH, "nieuwsbrief.rhis.nl"),
                       SmtpAccount("old", HASH, "rhis.nl", enabled=False)],
    )


class RelayDomainTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.assertEqual(validate(self.cfg), [])
        self.r = render(self.cfg)

    def test_relay_domain_gets_no_inbound_mail(self):
        relay_domains = self.r.files["/etc/mailfilter/postfix/relay_domains"]
        transport = self.r.files["/etc/mailfilter/postfix/transport"]
        self.assertIn("rhis.nl OK", relay_domains)
        self.assertNotIn("webshop-devries.nl", relay_domains + transport)
        self.assertNotIn("nieuwsbrief.rhis.nl", relay_domains + transport)
        self.assertFalse(any(k.startswith(service_name("mfq_", "webshop-devries.nl")) for k in self.r.main_cf))

    def test_relay_domain_keeps_outbound_limits(self):
        d = next(d for d in self.cfg.domains if d.kind == "relay")
        d.outbound.per_hour = 100
        self.assertIn("equal('webshop-devries.nl')", render(self.cfg).files["/etc/rspamd/local.d/ratelimit.conf"])

    def test_relay_domain_has_no_destination(self):
        self.cfg.domains[1].destinations = [Destination("mail.example.nl")]
        self.assertIn("relay_has_destination", {p.code for p in validate(self.cfg)})
        self.cfg.domains[1].destinations = []
        self.cfg.domains[0].destinations = []
        self.assertIn("destination_missing", {p.code for p in validate(self.cfg)})

    def test_unknown_kind(self):
        self.cfg.domains[1].kind = "other"
        self.assertIn("kind_invalid", {p.code for p in validate(self.cfg)})

    def test_sender_lock(self):
        cls = service_name("mfl_", "webshop-devries.nl")
        self.assertEqual(self.r.main_cf["smtpd_restriction_classes"], cls)
        self.assertIn("inline:{webshop-devries.nl=OK, <>=OK}", self.r.main_cf[cls])
        self.assertIn("REJECT 5.7.1", self.r.main_cf[cls])
        cidr = self.r.files["/etc/mailfilter/postfix/sender_lock.cidr"]
        self.assertIn(f"185.12.44.10 {cls}", cidr)
        self.assertIn(f"2a01:4f8:c0c:1a2::10 {cls}", cidr)
        self.assertNotIn("203.0.113.9", cidr)  # nieuwsbrief.rhis.nl is not locked
        sasl = self.r.files["/etc/mailfilter/postfix/sender_lock_sasl"]
        self.assertIn(f"smtp@webshop-devries.nl {cls}", sasl)
        self.assertNotIn("news", sasl)
        eod = self.r.main_cf["smtpd_end_of_data_restrictions"]
        self.assertLess(eod.index("outbound_hold.cidr"), eod.index("sender_lock.cidr"))  # HOLD first, it does not stop

    def test_no_lock_no_classes(self):
        for d in self.cfg.domains:
            d.sender_lock = False
        r = render(self.cfg)
        self.assertNotIn("smtpd_restriction_classes", r.main_cf)  # postconf -X removes it
        self.assertNotIn("sender_lock", r.main_cf["smtpd_end_of_data_restrictions"])

    def test_accounts_block(self):
        self.assertEqual(self.r.users_block, f"news:{HASH}\nsmtp@webshop-devries.nl:{HASH}\n")  # disabled left out

    def test_account_checks(self):
        self.cfg.smtp_accounts += [SmtpAccount("bad name", HASH, "rhis.nl"), SmtpAccount("x", "plain", "rhis.nl"),
                                   SmtpAccount("y", HASH, "unknown.nl"), SmtpAccount("NEWS", HASH, "rhis.nl")]
        codes = {p.code for p in validate(self.cfg)}
        self.assertTrue({"account_invalid", "account_hash_invalid", "account_domain_unknown", "account_duplicate"} <= codes)

    def test_splice_users_keeps_hand_made_lines(self):
        manual = "web01@example.nl:{SHA512-CRYPT}$6$abc\n"
        out = splice_users(manual, "a:{SSHA512}x\n")
        self.assertTrue(out.startswith(manual))
        again = splice_users(out, "b:{SSHA512}y\n")
        self.assertEqual(again, f"{manual}{USERS_BEGIN}\nb:{{SSHA512}}y\n{USERS_END}\n")
        self.assertEqual(splice_users("", ""), f"{USERS_BEGIN}\n{USERS_END}\n")

    def test_json_roundtrip(self):
        self.assertEqual(GatewayConfig.from_json(self.cfg.to_json()), self.cfg)
        # old state files without kind/accounts still load
        legacy = {"domains": [{"name": "rhis.nl", "destinations": [{"host": "mail.rhis.nl"}]}]}
        self.assertEqual(GatewayConfig.from_dict(legacy).domains[0].kind, "transport")


class ApplyAccountsTest(unittest.TestCase):
    def test_apply_writes_accounts_and_keeps_manual_lines(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(f"{root}/etc/postfix")
            os.makedirs(f"{root}/etc/mailfilter")
            open(f"{root}/etc/postfix/master.cf", "w").close()
            with open(f"{root}/etc/mailfilter/smtp-users", "w") as fh:
                fh.write("web01@example.nl:{SHA512-CRYPT}$6$abc\n")
            Applier(FakeServer(), root=root).apply(cfg())
            with open(f"{root}/etc/mailfilter/smtp-users") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("web01@example.nl:"))
            self.assertIn(f"smtp@webshop-devries.nl:{HASH}", text)
            self.assertEqual(os.stat(f"{root}/etc/mailfilter/smtp-users").st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main()


class CustomerNetworkLock(unittest.TestCase):
    """A relay network of a customer is locked to all of that customer's domains."""

    def test_network_senders_get_their_own_class(self):
        cfg = GatewayConfig(
            domains=[Domain(name="klant.nl", destinations=[Destination(host="mail.klant.nl")])],
            relay_networks=[RelayNetwork(cidr="192.0.2.10", customer="klant.nl", senders=["Klant2.nl", "klant.nl"]),
                            RelayNetwork(cidr="198.51.100.1")],
        )
        self.assertEqual(validate(cfg), [])
        r = render(cfg)
        cls = service_name("mfl_", "klant.nl+klant2.nl")
        self.assertIn(cls, r.main_cf["smtpd_restriction_classes"])
        self.assertIn("inline:{klant.nl=OK, klant2.nl=OK, <>=OK}", r.main_cf[cls])
        self.assertIn("@klant.nl or @klant2.nl", r.main_cf[cls])
        lock = r.files["/etc/mailfilter/postfix/sender_lock.cidr"]
        self.assertIn(f"192.0.2.10 {cls}", lock)
        self.assertNotIn("198.51.100.1", lock)

    def test_bad_sender_domain_is_refused(self):
        cfg = GatewayConfig(relay_networks=[RelayNetwork(cidr="192.0.2.10", senders=["a.nl, OK}, permit"])])
        self.assertIn("sender_domain_invalid", [p.code for p in validate(cfg)])
