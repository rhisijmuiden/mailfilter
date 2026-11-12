"""DKIM key export/import for cluster nodes (mailfilter-config dkim)."""
import os
import tempfile
import unittest

from mailfilter_config.apply import DKIM_MAP, Applier
from mailfilter_config.dkim_sync import handle, key_path, txt_path

KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----\n"


class DkimSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ap = Applier(root=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        self.ap.write(DKIM_MAP, "# selectors\nrhis.nl mf2026\n")
        self.ap.write(key_path("rhis.nl", "mf2026"), KEY)
        self.ap.write(txt_path("rhis.nl", "mf2026"), "mf2026._domainkey IN TXT ( \"v=DKIM1\" )\n")
        out = handle({"action": "export"}, self.ap)
        self.assertEqual([k["domain"] for k in out["keys"]], ["rhis.nl"])

        other = Applier(root=os.path.join(self.tmp.name, "b"))
        other.write(DKIM_MAP, "rhis.nl own\nklant.nl mf2026\n")
        res = handle({"action": "import", "keys": out["keys"]}, other)
        self.assertEqual(res["changed"], ["rhis.nl"])
        self.assertEqual(other.read(DKIM_MAP), "rhis.nl mf2026\nklant.nl mf2026\n")
        self.assertEqual(other.read(key_path("rhis.nl", "mf2026")), KEY)
        # Importing the same again changes nothing
        self.assertEqual(handle({"action": "import", "keys": out["keys"]}, other)["changed"], [])

    def test_refuses_bad_input(self):
        for bad in ({"domain": "../etc", "selector": "x", "key": KEY},
                    {"domain": "rhis.nl", "selector": "a/b", "key": KEY},
                    {"domain": "rhis.nl", "selector": "x", "key": "not a key"}):
            with self.assertRaises(ValueError):
                handle({"action": "import", "keys": [bad]}, self.ap)
        with self.assertRaises(ValueError):
            handle({"action": "delete"}, self.ap)

    def test_rotation_actions(self):
        import subprocess

        calls = []

        def runner(cmd):
            calls.append(cmd)
            with open(cmd[cmd.index("-k") + 1], "w") as fh:
                fh.write(KEY)
            sel = cmd[cmd.index("-s") + 1]
            return subprocess.CompletedProcess(cmd, 0, f'{sel}._domainkey IN TXT ( "v=DKIM1; k=rsa; " "p=NEW" ) ;\n', "")

        ap = Applier(runner=runner, root=self.tmp.name)
        ap.write(DKIM_MAP, "# selectors\nrhis.nl mf2026\n")
        ap.write(key_path("rhis.nl", "mf2026"), KEY)
        out = handle({"action": "generate", "domain": "rhis.nl", "selector": "mf202611"}, ap)
        self.assertIn("p=NEW", out["txt"])
        # A second generate does not make another key
        handle({"action": "generate", "domain": "rhis.nl", "selector": "mf202611"}, ap)
        self.assertEqual(len(calls), 1)
        st = handle({"action": "status"}, ap)["domains"]
        self.assertEqual(st[0]["active"], "mf2026")
        self.assertEqual(sorted(k["selector"] for k in st[0]["keys"]), ["mf2026", "mf202611"])
        # The active key cannot be retired; switching first makes it possible
        with self.assertRaises(ValueError):
            handle({"action": "retire", "domain": "rhis.nl", "selector": "mf2026"}, ap)
        self.assertTrue(handle({"action": "select", "domain": "rhis.nl", "selector": "mf202611"}, ap)["changed"])
        self.assertFalse(handle({"action": "select", "domain": "rhis.nl", "selector": "mf202611"}, ap)["changed"])
        self.assertEqual(ap.read(DKIM_MAP), "# selectors\nrhis.nl mf202611\n")
        self.assertTrue(handle({"action": "retire", "domain": "rhis.nl", "selector": "mf2026"}, ap)["removed"])
        self.assertIsNone(ap.read(key_path("rhis.nl", "mf2026")))
        with self.assertRaises(ValueError):
            handle({"action": "select", "domain": "rhis.nl", "selector": "gone"}, ap)
        with self.assertRaises(ValueError):
            handle({"action": "generate", "domain": "rhis.nl", "selector": "../x"}, ap)


if __name__ == "__main__":
    unittest.main()
