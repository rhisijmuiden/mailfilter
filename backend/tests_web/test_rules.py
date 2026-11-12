import json

import pytest

from mailfilter import gateway
from mailfilter.db import session
from mailfilter.models import AuditLog, Domain, DomainDestination, DomainRoute
from mailfilter.settings_store import get_setting


@pytest.fixture(autouse=True)
def helper(monkeypatch):
    """Stands in for `sudo mailfilter-config apply --stdin --json`."""
    state = {"result": {"ok": True, "changed": ["/etc/rspamd/mailfilter_rules.json"], "warnings": []}, "calls": []}

    def fake(config_json):
        state["calls"].append(json.loads(config_json))
        return state["result"]

    monkeypatch.setattr(gateway, "run_helper", fake)
    return state


@pytest.fixture
def routed(users):
    """Both test domains are filtered (have a route)."""
    with session() as db:
        for d in db.query(Domain).all():
            route = DomainRoute(domain_id=d.id)
            route.destinations = [DomainDestination(domain_id=d.id, host=f"mail.{d.name}")]
            db.add(route)
        db.commit()


def rule(data, rule_id):
    return next(r for r in data["rules"] if r["id"] == rule_id)


def test_defaults_are_listed_with_both_languages(admin, routed):
    data = admin.get("/api/rules").json()
    r = rule(data, "newsletter_no_unsubscribe")
    assert r["enabled"] is True and r["title"]["nl"] and r["details"]["en"]
    assert {o["id"]: o["value"] for o in r["options"]} == {"score": 4.0, "outbound": False}
    assert rule(data, "newsletter_subject")["enabled"] is False
    assert data["domains"] == ["klant.nl", "rhis.nl"] and data["can_edit_global"] is True


def test_admin_changes_rule_and_it_is_applied(admin, routed, helper):
    r = admin.put("/api/rules/newsletter_no_unsubscribe",
                  json={"enabled": True, "options": {"score": 6, "outbound": True}, "domains": {"klant.nl": False}})
    assert r.status_code == 200, r.text
    sent = helper["calls"][-1]["custom_rules"]["newsletter_no_unsubscribe"]
    assert sent == {"enabled": True, "options": {"score": 6.0, "outbound": True}, "domains": {"klant.nl": False}}
    got = rule(r.json(), "newsletter_no_unsubscribe")
    assert got["domains"] == {"klant.nl": False} and got["options"][0]["value"] == 6.0
    with session() as db:
        assert get_setting(db, "custom_rules")["newsletter_no_unsubscribe"]["options"]["score"] == 6.0
        assert db.query(AuditLog).filter_by(action="custom_rule_updated").count() == 1


def test_bad_score_is_refused_in_both_languages(admin, routed, helper):
    r = admin.put("/api/rules/newsletter_no_dkim", json={"enabled": True, "options": {"score": 99}})
    assert r.status_code == 422
    p = r.json()["detail"]["problems"][0]
    assert p["code"] == "rule_score" and p["nl"]
    assert helper["calls"] == []
    with session() as db:
        assert get_setting(db, "custom_rules") is None


def test_unknown_domain_and_rule(admin, routed):
    r = admin.put("/api/rules/newsletter_no_dkim", json={"enabled": True, "domains": {"elders.nl": True}})
    assert r.status_code == 422 and r.json()["detail"]["problems"][0]["code"] == "rule_domain_unknown"
    assert admin.put("/api/rules/nope", json={"enabled": True}).status_code == 404


def test_server_refusal_keeps_old_choice(admin, routed, helper):
    helper["result"] = {"ok": False, "step": "rspamadm configtest", "output": "syntax error"}
    r = admin.put("/api/rules/newsletter_subject", json={"enabled": True, "options": {"tag": "[NB]"}})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "apply_failed"
    with session() as db:
        assert get_setting(db, "custom_rules") is None


def test_customer_sets_own_domain_only(customer, routed, helper):
    data = customer.get("/api/rules").json()
    assert data["domains"] == ["klant.nl"] and data["can_edit_global"] is False
    assert customer.put("/api/rules/newsletter_no_dkim", json={"enabled": False}).status_code == 403
    assert customer.put("/api/rules/newsletter_no_dkim/domains/rhis.nl", json={"state": "off"}).status_code == 404
    r = customer.put("/api/rules/newsletter_no_dkim/domains/klant.nl", json={"state": "off"})
    assert r.status_code == 200, r.text
    assert rule(r.json(), "newsletter_no_dkim")["domains"] == {"klant.nl": False}
    assert helper["calls"][-1]["custom_rules"]["newsletter_no_dkim"]["domains"] == {"klant.nl": False}
    r = customer.put("/api/rules/newsletter_no_dkim/domains/klant.nl", json={"state": "default"})
    assert rule(r.json(), "newsletter_no_dkim")["domains"] == {}


def test_protection_rules_listed_with_choices_and_lists(admin, routed):
    data = admin.get("/api/rules").json()
    r = rule(data, "attachments_dangerous")
    opts = {o["id"]: o for o in r["options"]}
    assert opts["action"]["value"] == "reject" and {c["id"] for c in opts["action"]["choices"]} == {"reject", "quarantine"}
    assert "exe" in opts["extensions"]["value"]
    assert rule(data, "arc_seal_inbound")["per_domain"] is False
    assert {r["group"] for r in data["rules"]} >= {"newsletter", "phishing", "attachments", "arc"}


def test_list_option_is_normalised_before_storing(admin, routed, helper):
    r = admin.put("/api/rules/phishing_display_name",
                  json={"enabled": True, "options": {"score": 5, "names": "Jan  de Vries\nPiet Jansen, Piet Jansen"}})
    assert r.status_code == 200, r.text
    assert helper["calls"][-1]["custom_rules"]["phishing_display_name"]["options"]["names"] == ["Jan de Vries", "Piet Jansen"]
    with session() as db:
        assert get_setting(db, "custom_rules")["phishing_display_name"]["options"]["names"] == ["Jan de Vries", "Piet Jansen"]


def test_global_only_rule_has_no_domain_choice(admin, customer, routed):
    r = admin.put("/api/rules/arc_seal_inbound/domains/rhis.nl", json={"state": "on"})
    assert r.status_code == 422 and r.json()["detail"]["problems"][0]["code"] == "rule_not_per_domain"


def test_customer_does_not_see_protected_names(admin, customer, routed, helper):
    admin.put("/api/rules/phishing_display_name", json={"enabled": True, "options": {"names": ["Jan de Vries"]}})
    r = rule(customer.get("/api/rules").json(), "phishing_display_name")
    assert [o["id"] for o in r["options"]] == ["score"]
