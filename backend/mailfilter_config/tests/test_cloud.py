"""Microsoft 365 / Google Workspace tenants relaying through the Mailfilter."""
import json
import os
import subprocess
import tempfile
import unittest

from mailfilter_config import Applier, ApplyError, CloudTenant, Destination, Domain, GatewayConfig, render, validate
from mailfilter_config import cloud
from mailfilter_config.render import BEGIN, END

from .test_config import FakeServer

TENANT = "8c1f2b6e-0d3a-4c55-9e1b-6f0a2d7be42a"


def cfg() -> GatewayConfig:
    return GatewayConfig(
        hostname="mailfilter.rhis.nl",
        domains=[Domain(name="rhis.nl", destinations=[Destination("rhis-nl.mail.protection.outlook.com")]),
                 Domain(name="bakkerij.nl", destinations=[Destination(h) for h in cloud.GOOGLE_DESTINATIONS]),
                 Domain(name="other.nl", destinations=[Destination("mail.other.nl")])],
        cloud_tenants=[CloudTenant("m365", "RHIS.nl", TENANT.upper()),
                       CloudTenant("google", "bakkerij.nl")],
    )


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.assertEqual(validate(self.cfg), [])
        self.r = render(self.cfg)

    def test_normalised(self):
        self.assertEqual(self.cfg.cloud_tenants[0].domain, "rhis.nl")
        self.assertEqual(self.cfg.cloud_tenants[0].tenant_id, TENANT)

    def test_provider_servers_relay_only_for_own_domains(self):
        cidr = self.r.files[cloud.CIDR_MAP]
        self.assertIn("40.107.0.0/16 mfc_m365\n", cidr)
        self.assertIn("2a01:111:f400::/48 mfc_m365\n", cidr)
        self.assertIn("74.125.0.0/16 mfc_google\n", cidr)
        self.assertEqual(self.r.main_cf["mfc_m365"], "check_sender_access inline:{rhis.nl=OK, <>=OK}")
        self.assertEqual(self.r.main_cf["mfc_google"], "check_sender_access inline:{bakkerij.nl=OK, <>=OK}")
        self.assertNotIn("REJECT", self.r.main_cf["mfc_m365"])  # other senders: normal inbound mail
        self.assertIn("mfc_m365", self.r.main_cf["smtpd_restriction_classes"])

    def test_provider_servers_are_not_trusted_relay_networks(self):
        self.assertNotIn("40.107.0.0/16", self.r.files["/etc/mailfilter/postfix/relay_networks.cidr"])
        self.assertNotIn("40.107.0.0/16", self.r.files["/etc/mailfilter/rspamd/local_addrs.map"])

    def test_rspamd_settings(self):
        s = json.loads(self.r.files[cloud.SETTINGS_PATH])
        self.assertEqual(s["m365"], {"rhis.nl": TENANT})
        self.assertEqual(s["google"], ["bakkerij.nl"])
        self.assertIn("other.nl", s["local_domains"])
        self.assertIn("40.107.0.0/16", s["networks"]["m365"])
        self.assertIn("X-MS-Exchange-CrossTenant-Id", self.r.files[cloud.LUA_PATH])

    def test_no_tenants_no_classes(self):
        self.cfg.cloud_tenants = []
        r = render(self.cfg)
        self.assertNotIn("mfc_m365", r.main_cf)
        self.assertNotIn("smtpd_restriction_classes", r.main_cf)
        self.assertNotIn("mfc_", r.files[cloud.CIDR_MAP])

    def test_disabled_tenant(self):
        self.cfg.cloud_tenants[1].enabled = False
        r = render(self.cfg)
        self.assertNotIn("mfc_google", r.main_cf)
        self.assertEqual(json.loads(r.files[cloud.SETTINGS_PATH])["google"], [])

    def test_validation(self):
        c = cfg()
        c.cloud_tenants[0].tenant_id = "nope"
        c.cloud_tenants.append(CloudTenant("m365", "unknown.nl", TENANT))
        c.cloud_tenants.append(CloudTenant("google", "bakkerij.nl"))
        c.cloud_tenants.append(CloudTenant("yahoo", "other.nl"))
        codes = {p.code for p in validate(c)}
        self.assertEqual(codes, {"tenant_id_invalid", "cloud_domain_missing", "cloud_duplicate", "cloud_provider_invalid"})

    def test_roundtrip(self):
        again = GatewayConfig.from_json(self.cfg.to_json())
        self.assertEqual(again.cloud_tenants, self.cfg.cloud_tenants)

    def test_detect_provider(self):
        self.assertEqual(cloud.detect_provider(["rhis-nl.mail.protection.outlook.com."]), "m365")
        self.assertEqual(cloud.detect_provider(["ALT1.ASPMX.L.GOOGLE.COM"]), "google")
        self.assertEqual(cloud.detect_provider(["smtp.google.com"]), "google")
        self.assertIsNone(cloud.detect_provider(["mx.transip.email"]))
        self.assertEqual(cloud.m365_destination("rhis.nl"), "rhis-nl.mail.protection.outlook.com")


class CloudApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(f"{self.root}/etc/postfix")
        with open(f"{self.root}/etc/postfix/master.cf", "w") as fh:
            fh.write(f"smtp inet n - y - 1 postscreen\n{BEGIN}\n{END}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_refused_without_installer_support(self):
        with self.assertRaises(ApplyError) as e:
            Applier(FakeServer(), root=self.root).apply(cfg())
        self.assertEqual(e.exception.step, "cloud")

    def test_applied_with_installer_support(self):
        srv = FakeServer()

        def run(cmd):
            if list(cmd)[:3] == ["postconf", "-h", "smtpd_relay_restrictions"]:
                return subprocess.CompletedProcess(cmd, 0, "permit_mynetworks, check_client_access "
                                                   "cidr:/etc/mailfilter/postfix/cloud_relay.cidr\n", "")
            return srv(cmd)

        Applier(run, root=self.root).apply(cfg())
        with open(self.root + cloud.CIDR_MAP) as fh:
            self.assertIn("mfc_m365", fh.read())
        self.assertIn("mfc_m365", srv.params)


if __name__ == "__main__":
    unittest.main()
