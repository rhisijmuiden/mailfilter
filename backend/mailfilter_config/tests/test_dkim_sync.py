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


if __name__ == "__main__":
    unittest.main()
