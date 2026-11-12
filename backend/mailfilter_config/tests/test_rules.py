import json
import unittest

from mailfilter_config import CustomRule, Destination, Domain, GatewayConfig, render, validate
from mailfilter_config.rules import (ARC_CONF_PATH, ARC_TRUSTED_MAP, LUA_PATH, PROTECTION_LUA_PATH, RULES,
                                     SETTINGS_PATH)


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


class ProtectionRulesTest(unittest.TestCase):
    def test_choice_and_list_options_have_texts(self):
        for r in RULES:
            for o in r["options"]:
                for c in o.get("choices", []):
                    self.assertTrue(c["label"]["en"] and c["label"]["nl"], (r["id"], o["id"], c["id"]))

    def test_protection_defaults_rendered(self):
        files = render(cfg()).files
        data = json.loads(files[SETTINGS_PATH])
        self.assertEqual(data["protected_domains"], ["rhis.nl"])
        rules = data["rules"]
        self.assertTrue(rules["phishing_lookalike"]["enabled"])
        self.assertFalse(rules["external_banner"]["enabled"])
        self.assertIn("exe", rules["attachments_dangerous"]["options"]["extensions"])
        self.assertEqual(rules["attachments_dangerous"]["options"]["action"], "reject")
        self.assertEqual(rules["attachments_macros"]["options"]["action"], "quarantine")
        self.assertIn("MF_LOOKALIKE_DOMAIN", files[PROTECTION_LUA_PATH])
        arc = files[ARC_CONF_PATH]
        self.assertIn("sign_local = true;", arc)
        self.assertIn("sign_inbound = false;", arc)
        self.assertIn(ARC_TRUSTED_MAP, arc)
        self.assertIn("google.com\n", files[ARC_TRUSTED_MAP])

    def test_lists_are_normalised(self):
        c = cfg(attachments_dangerous=CustomRule(True, {"extensions": ".EXE, *.js\nexe", "action": "quarantine"}),
                phishing_lookalike=CustomRule(True, {"extra": ["ING.nl.", "@rabobank.nl"]}),
                phishing_display_name=CustomRule(True, {"names": ["  Jan   de Vries "]}))
        self.assertEqual(validate(c), [])
        self.assertEqual(c.custom_rules["attachments_dangerous"].options["extensions"], ["exe", "js"])
        self.assertEqual(c.custom_rules["phishing_lookalike"].options["extra"], ["ing.nl", "rabobank.nl"])
        self.assertEqual(c.custom_rules["phishing_display_name"].options["names"], ["Jan de Vries"])

    def test_bad_values(self):
        c = cfg(attachments_dangerous=CustomRule(True, {"extensions": ["exe", "not ok!"], "action": "explode"}),
                phishing_lookalike=CustomRule(True, {"extra": ["no domain"]}))
        codes = sorted(p.code for p in validate(c))
        self.assertEqual(codes, ["rule_choice", "rule_list_item", "rule_list_item"])

    def test_arc_switches(self):
        c = cfg(arc_seal_outbound=CustomRule(False), arc_seal_inbound=CustomRule(True),
                arc_trusted_forwarders=CustomRule(False))
        arc = render(c).files[ARC_CONF_PATH]
        self.assertIn("sign_local = false;", arc)
        self.assertIn("sign_authenticated = false;", arc)
        self.assertIn("sign_inbound = true;", arc)
        self.assertIn("adjust_dmarc = false;", arc)
        self.assertNotIn("whitelisted_signers_map", arc)

    def test_global_only_rules_drop_domain_choices(self):
        c = cfg(arc_seal_inbound=CustomRule(True, {}, {"rhis.nl": False}))
        self.assertEqual(validate(c), [])
        self.assertEqual(c.custom_rules["arc_seal_inbound"].domains, {})


if __name__ == "__main__":
    unittest.main()
