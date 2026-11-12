import json
import unittest

from mailfilter_config import CustomRule, Destination, Domain, GatewayConfig, render, validate
from mailfilter_config.rules import LUA_PATH, RULES, SETTINGS_PATH


def cfg(**rules) -> GatewayConfig:
    return GatewayConfig(domains=[Domain("rhis.nl", [Destination("mail.rhis.nl")]),
                                  Domain("oud.nl", [Destination("mail.oud.nl")], enabled=False)],
                         custom_rules=rules)


class RulesTest(unittest.TestCase):
    def test_every_rule_has_texts_in_both_languages(self):
        for r in RULES:
            for key in ("title", "summary", "details"):
                self.assertTrue(r[key]["en"] and r[key]["nl"], (r["id"], key))
            for o in r["options"]:
                self.assertTrue(o["label"]["en"] and o["label"]["nl"])

    def test_defaults_are_rendered(self):
        files = render(cfg()).files
        settings = json.loads(files[SETTINGS_PATH])["rules"]
        self.assertEqual(set(settings), {r["id"] for r in RULES})
        self.assertTrue(settings["newsletter_no_unsubscribe"]["enabled"])
        self.assertEqual(settings["newsletter_no_unsubscribe"]["options"]["score"], 4.0)
        self.assertIn("MF_NEWSLETTER_NO_UNSUB", files[LUA_PATH])

    def test_choices_and_domain_overrides(self):
        c = cfg(newsletter_subject=CustomRule(True, {"tag": "  [Nieuws]  ", "junk": 1}, {"RHIS.nl": False, "oud.nl": True}))
        self.assertEqual(validate(c), [])
        rule = json.loads(render(c).files[SETTINGS_PATH])["rules"]["newsletter_subject"]
        # disabled domains are not written; unknown options dropped; text trimmed
        self.assertEqual(rule, {"enabled": True, "options": {"tag": "[Nieuws]"}, "domains": {"rhis.nl": False}})

    def test_removed_domain_is_dropped_silently(self):
        c = cfg(newsletter_no_dkim=CustomRule(True, {}, {"weg.nl": True}))
        self.assertEqual(validate(c), [])
        self.assertEqual(c.custom_rules["newsletter_no_dkim"].domains, {})

    def test_problems(self):
        c = cfg(newsletter_no_dkim=CustomRule(True, {"score": "veel"}), bogus=CustomRule(True))
        codes = {p.code for p in validate(c)}
        self.assertEqual(codes, {"rule_score", "rule_unknown"})

    def test_round_trip_through_gateway_json(self):
        c = cfg(newsletter_no_unsubscribe=CustomRule(True, {"score": 5.0, "outbound": True}, {"rhis.nl": False}))
        again = GatewayConfig.from_json(c.to_json())
        self.assertEqual(again.custom_rules, c.custom_rules)
        self.assertEqual(GatewayConfig.from_json('{"domains": []}').custom_rules, {})


if __name__ == "__main__":
    unittest.main()
