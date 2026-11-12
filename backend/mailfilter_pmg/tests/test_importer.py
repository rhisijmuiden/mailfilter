import io
import json
import os
import tempfile
import unittest
from unittest import mock

from mailfilter_config.apply import Applier
from mailfilter_config.model import Destination, Domain, GatewayConfig, RelayNetwork
from mailfilter_config.tests.test_config import FakeServer

from mailfilter_pmg import read_backup
from mailfilter_pmg.__main__ import main
from mailfilter_pmg import staging
from mailfilter_pmg.importer import apply_gateway_file, apply_server_files, merge_gateway, preview

from . import fixtures



def fake_applier(root="", reload=True):
    return Applier(FakeServer(), root=root, reload=reload)


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.plan = read_backup(io.BytesIO(fixtures.backup_tgz()))
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(f"{self.root}/etc/postfix")
        os.makedirs(f"{self.root}/etc/mailfilter/rspamd")
        from mailfilter_config.render import BEGIN as MASTER_BEGIN, END as MASTER_END

        with open(f"{self.root}/etc/postfix/master.cf", "w") as fh:
            fh.write(f"smtp inet n - y - 1 postscreen\n{MASTER_BEGIN}\n{MASTER_END}\n")
        with open(f"{self.root}/etc/mailfilter/rspamd/allow_from.map", "w") as fh:
            fh.write("factuur@leverancier.nl\n")
        with open(f"{self.root}/etc/mailfilter/rspamd/dkim_selectors.map", "w") as fh:
            fh.write("rhis.nl mf2026\n")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, p):
        with open(self.root + p) as fh:
            return fh.read()

    def items(self, pv, section):
        return {i.key: i for i in pv.items if i.section == section}

    def test_preview_marks_existing_items_and_problems(self):
        current = GatewayConfig(
            domains=[Domain("rhis.nl", [Destination("old.rhis.nl")], customer="RHIS")],
            relay_networks=[RelayNetwork("192.0.2.10/32", "web1")],
        )
        pv = preview(self.plan, current, root=self.root)
        doms = self.items(pv, "domains")
        self.assertEqual(doms["rhis.nl"].status, "exists")
        self.assertFalse(doms["rhis.nl"].selected)
        self.assertTrue(doms["klant.nl"].selected)
        self.assertIn("TLS", doms["klant.nl"].note_en)  # PMG required inbound TLS
        nets = self.items(pv, "relay_networks")
        self.assertFalse(nets["192.0.2.10/32"].selected)
        self.assertTrue(nets["2001:db8::/64"].selected)
        lists = self.items(pv, "lists")
        self.assertFalse(lists["welcome:global:email:factuur@leverancier.nl"].selected)  # already on the map
        self.assertEqual(lists["welcome:user:jan@rhis.nl:domain:partner.example"].status, "unsupported")
        dkim = self.items(pv, "dkim")
        self.assertEqual(dkim["rhis.nl"].status, "exists")  # other selector already there
        self.assertTrue(dkim["shop.example"].selected)
        json.dumps(pv.to_dict())

    def test_preview_shows_validation_problem_on_item(self):
        self.plan.domains[0].transport.host = "not a host"
        pv = preview(self.plan, GatewayConfig(), root=self.root)
        self.assertEqual(self.items(pv, "domains")["rhis.nl"].problems[0]["code"], "destination_invalid")

    def test_apply_defaults(self):
        pv = preview(self.plan, GatewayConfig(), root=self.root)
        selection = {}
        for it in pv.items:
            if it.selected:
                selection.setdefault(it.section, []).append(it.key)
        srv = FakeServer()
        res = apply_gateway_file(self.plan, selection, root=self.root, applier=Applier(srv, root=self.root))
        self.assertTrue(res.ok, res.problems)
        self.assertEqual(res.imported["domains"], 3)
        res = apply_server_files(self.plan, selection, root=self.root)
        self.assertTrue(res.ok)
        state = json.loads(self.read("/etc/mailfilter/gateway.json"))
        self.assertEqual({d["name"] for d in state["domains"]}, {"rhis.nl", "klant.nl", "shop.example"})
        rhis = next(d for d in state["domains"] if d["name"] == "rhis.nl")
        self.assertEqual(rhis["destinations"][0], {"host": "10.0.0.10", "port": 25, "mx": False, "protocol": "smtp"})
        self.assertEqual(len(state["relay_networks"]), 3)
        self.assertEqual(state["address_routes"][0]["destination"]["protocol"], "lmtp")
        self.assertEqual(state["tls_policies"][0]["level"], "encrypt")
        self.assertIn("klant.nl", self.read("/etc/mailfilter/postfix/transport"))
        # lists
        self.assertIn("spammer.example", self.read("/etc/mailfilter/rspamd/block_domain.map"))
        self.assertIn("203.0.113.66", self.read("/etc/mailfilter/rspamd/block_ip.map"))
        self.assertIn("/^(?:.*@bad\\.example)$/i", self.read("/etc/mailfilter/rspamd/block_from_re.map"))
        self.assertIn("192.0.2.0/24", self.read("/etc/mailfilter/rspamd/allow_ip.map"))
        self.assertEqual(self.read("/etc/mailfilter/rspamd/allow_from.map").count("factuur@leverancier.nl"), 1)
        # DKIM: only shop.example (rhis.nl has another selector and was not ticked)
        self.assertIn("BEGIN RSA", self.read("/var/lib/rspamd/dkim/shop.example.mf2024.key"))
        self.assertEqual(oct(os.stat(self.root + "/var/lib/rspamd/dkim/shop.example.mf2024.key").st_mode & 0o777), "0o640")
        self.assertFalse(os.path.exists(self.root + "/var/lib/rspamd/dkim/rhis.nl.mf2024.key"))
        sel = self.read("/etc/mailfilter/rspamd/dkim_selectors.map")
        self.assertIn("rhis.nl mf2026", sel)
        self.assertIn("shop.example mf2024", sel)
        # Second run with the same selection changes nothing in the lists
        res2 = apply_server_files(self.plan, {"lists": selection["lists"]}, root=self.root)
        self.assertEqual(res2.imported["lists"], 0)

    def test_existing_domain_keeps_mailfilter_settings(self):
        with open(self.root + "/etc/mailfilter/gateway.json", "w") as fh:
            fh.write(GatewayConfig(domains=[Domain("rhis.nl", [Destination("old.rhis.nl")], customer="RHIS")]).to_json())
        res = apply_gateway_file(self.plan, {"domains": ["rhis.nl"]}, root=self.root, applier=Applier(FakeServer(), root=self.root))
        self.assertTrue(res.ok)
        d = json.loads(self.read("/etc/mailfilter/gateway.json"))["domains"][0]
        self.assertEqual((d["customer"], d["destinations"][0]["host"]), ("RHIS", "10.0.0.10"))

    def test_invalid_selection_changes_nothing(self):
        self.plan.domains[0].transport.host = "not a host"
        res = apply_gateway_file(self.plan, {"domains": ["rhis.nl"]}, root=self.root, applier=Applier(FakeServer(), root=self.root))
        self.assertFalse(res.ok)
        self.assertFalse(os.path.exists(self.root + "/etc/mailfilter/gateway.json"))

    def test_merge_gateway_for_the_web_app(self):
        current = GatewayConfig(relay_networks=[RelayNetwork("192.0.2.10", "web1")])
        cfg, picked, problems = merge_gateway(
            self.plan, current, {"relay_networks": ["192.0.2.10/32", "2001:db8::/64"], "domains": ["shop.example"]})
        self.assertEqual(problems, [])
        self.assertEqual({k: len(v) for k, v in picked.items()}, {"domains": 1, "address_routes": 0, "relay_networks": 2, "tls_policies": 0})
        self.assertEqual(picked["relay_networks"][0].cidr, "192.0.2.10")
        self.assertEqual([n.cidr for n in cfg.relay_networks], ["192.0.2.10", "2001:db8::/64"])  # no duplicate
        self.assertEqual(cfg.relay_networks[0].description, "web1")  # existing entry kept as it was
        self.assertEqual(cfg.domains[0].destinations[0].port, 2525)
        self.assertEqual(current.domains, [])  # input not modified

    def test_cli_apply(self):
        path = os.path.join(self.root, "b.tgz")
        with open(path, "wb") as fh:
            fh.write(fixtures.backup_tgz())
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("mailfilter_config.apply.Applier", fake_applier):
            rc = main(["apply", path, "--sections", "relay_networks,lists", "--gateway-json", "--json", "--no-reload", "--root", self.root])
        self.assertEqual(rc, 0, out.getvalue())
        res = json.loads(out.getvalue())
        self.assertEqual(res["imported"]["relay_networks"], 3)
        self.assertGreater(res["imported"]["lists"], 0)

    def test_run_pending(self):
        sdir = os.path.join(self.root, "staging")
        os.makedirs(sdir)
        ok_id, bad_id = "a" * 32, "b" * 32
        p = staging.paths(ok_id, sdir)
        with open(p["backup"], "wb") as fh:
            fh.write(fixtures.backup_tgz())
        with open(p["selection"], "w") as fh:
            json.dump({"lists": ["block:global:domain:spammer.example"], "relay_networks": ["2001:db8::/64"]}, fh)
        open(p["request"], "w").close()
        b = staging.paths(bad_id, sdir)
        os.symlink("/etc/hostname", b["backup"])  # planted symlink is not followed
        with open(b["selection"], "w") as fh:
            fh.write("{}")
        open(b["request"], "w").close()
        open(os.path.join(sdir, "../evil.request"), "w").close()

        done = staging.run_pending(sdir, root=self.root, gateway_json=True, applier_factory=lambda: Applier(FakeServer(), root=self.root))
        self.assertEqual(done, [ok_id, bad_id])
        with open(p["result"]) as fh:
            res = json.load(fh)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"]["lists"], 1)
        self.assertEqual(res["imported"]["relay_networks"], 1)
        self.assertIn("2001:db8::/64", self.read("/etc/mailfilter/gateway.json"))
        with open(b["result"]) as fh:
            self.assertFalse(json.load(fh)["ok"])
        self.assertEqual(staging.pending(sdir), [])
        with self.assertRaises(ValueError):
            staging.paths("../../etc/shadow")


if __name__ == "__main__":
    unittest.main()
