import json
import time

import pytest

from mailfilter import gateway
from mailfilter import links as lp
from mailfilter.api import links as links_api
from mailfilter.db import session
from mailfilter.models import AuditLog, Domain, DomainDestination, DomainRoute, LinkClick
from mailfilter.settings_store import get_setting, set_setting


KEY = b"k" * 64


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(lp, "link_key", lambda: KEY)
    # No DNS or HTTP in tests unless a test asks for it.
    monkeypatch.setattr(lp, "blocklists", lambda host: [])
    monkeypatch.setattr(lp, "expand", lambda url, hops=5: None)
    monkeypatch.setattr(links_api, "_later", lambda fn, *a: None)
    state = {"calls": []}

    def fake(config_json):
        state["calls"].append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    return state


@pytest.fixture
def routed(users):
    with session() as db:
        for d in db.query(Domain).all():
            route = DomainRoute(domain_id=d.id)
            route.destinations = [DomainDestination(domain_id=d.id, host=f"mail.{d.name}")]
            db.add(route)
        db.commit()


def link(url, f=0, rcpt="jan@klant.nl", domains=("klant.nl",)):
    return lp.make_link({"u": url, "m": "<1@shop.example>", "q": "4AbC", "r": rcpt, "d": list(domains),
                         "t": int(time.time()) - 60, "f": f}, host="mailfilter.test")


def path(full):
    return full.split("mailfilter.test", 1)[1]


def set_rule(**options):
    with session() as db:
        rules = get_setting(db, "custom_rules") or {}
        rules["link_protection"] = {"enabled": True, "options": options, "domains": {}}
        set_setting(db, "custom_rules", rules)
        db.commit()


def clicks():
    with session() as db:
        return db.query(LinkClick).order_by(LinkClick.id).all()


BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Firefox/140.0", "Accept-Language": "nl-NL,nl;q=0.9"}


def test_token_round_trip_and_tampering():
    full = link("https://www.shop.example/sale?a=1&b=2")
    token = full.rsplit("/", 1)[1]
    assert lp.read_token(token)["u"] == "https://www.shop.example/sale?a=1&b=2"
    payload, mac = token.split(".")
    other = lp.make_link({"u": "https://evil.example/"}, host="x").rsplit("/", 1)[1].split(".")[0]
    assert lp.read_token(f"{other}.{mac}") is None
    assert lp.read_token(payload) is None
    assert lp.read_token(lp.make_link({"u": "javascript:alert(1)"}, host="x").rsplit("/", 1)[1]) is None


def test_safe_link_redirects_and_is_logged(client, routed):
    r = client.get(path(link("https://www.shop.example/sale")), headers=BROWSER, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://www.shop.example/sale"
    assert r.headers["referrer-policy"] == "no-referrer"
    c = clicks()[0]
    assert (c.level, c.host, c.recipient, c.domains, c.queue_id, c.scanner) == (
        "safe", "www.shop.example", "jan@klant.nl", ["klant.nl"], "4AbC", False)


def test_forged_link_is_refused_not_redirected(client):
    r = client.get("/l/eyJ1IjoiaHR0cHM6Ly9ldmlsLmV4YW1wbGUvIn0.AAAAAAAAAAAAAAAAAAAAAA", headers=BROWSER,
                   follow_redirects=False)
    assert r.status_code == 404 and "Deze link werkt niet" in r.text and not clicks()


def test_lookalike_warns_and_continue_anyway(client, routed):
    full = link("https://rhls.nl/factuur")
    r = client.get(path(full), headers=BROWSER)
    assert r.status_code == 200 and "Pas op met deze link" in r.text and "rhls.nl lijkt op rhis.nl" in r.text
    assert 'method="post"' in r.text
    c = clicks()[0]
    assert c.level == "warn" and not c.continued
    r = client.post(path(full), content=f"click={c.id}", headers={**BROWSER, "Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://rhls.nl/factuur"
    assert clicks()[0].continued is True


def test_no_continue_when_switched_off(client, routed):
    set_rule(allow_continue=False)
    full = link("https://www.shop.example/x", f=lp.FLAG_LINK)
    r = client.get(path(full), headers={**BROWSER, "Accept-Language": "en"})
    assert "Be careful with this link" in r.text and "already suspicious when the mail arrived" in r.text
    assert 'method="post"' not in r.text
    r = client.post(path(full), content="click=1", follow_redirects=False)
    assert r.status_code == 200 and "location" not in r.headers


def test_admin_block_list_blocks_old_mail_too(admin, client, routed, setup):
    full = link("https://login.ing-veilig.example/inloggen")
    assert client.get(path(full), headers=BROWSER, follow_redirects=False).status_code == 302
    r = admin.post("/api/links/block", json={"domain": "ing-veilig.example"})
    assert r.status_code == 200, r.text
    assert setup["calls"][-1]["custom_rules"]["link_protection"]["options"]["block"] == ["ing-veilig.example"]
    r = client.get(path(full), headers=BROWSER, follow_redirects=False)
    assert r.status_code == 200 and "Deze link is geblokkeerd" in r.text and 'method="post"' not in r.text
    r = client.post(path(full), content="click=2", follow_redirects=False)
    assert "location" not in r.headers
    with session() as db:
        assert db.query(AuditLog).filter_by(action="link_domain_blocked").count() == 1
    assert admin.post("/api/links/block", json={"domain": "rhis.nl"}).status_code == 422


def test_blocklist_hit_blocks(client, routed, monkeypatch):
    monkeypatch.setattr(lp, "blocklists", lambda host: [("Spamhaus DBL", "phishing")])
    r = client.get(path(link("https://ing-veilig.example/")), headers=BROWSER)
    assert "Deze link is geblokkeerd" in r.text and "Spamhaus DBL kent deze site als phishing" in r.text


def test_own_domains_are_safe_and_ip_links_warn(client, routed):
    r = client.get(path(link("https://intranet.rhis.nl/x", f=lp.FLAG_SENDER)), headers=BROWSER, follow_redirects=False)
    assert r.status_code == 302
    r = client.get(path(link("http://185.12.3.4/login")), headers=BROWSER)
    assert "kaal IP-adres" in r.text


def test_scanners_are_marked(client, routed):
    client.get(path(link("https://www.shop.example/")), headers={"User-Agent": "Mozilla/5.0 (compatible; BingPreview/1.0b)"},
               follow_redirects=False)
    assert clicks()[0].scanner is True


def test_shortener_destination_is_checked(client, routed, monkeypatch):
    monkeypatch.setattr(lp, "expand", lambda url, hops=5: "https://rhls.nl/x")
    r = client.get(path(link("https://bit.ly/abc")), headers=BROWSER)
    assert "Verkorte link: hij gaat naar rhls.nl" in r.text and "rhls.nl lijkt op rhis.nl" in r.text
    monkeypatch.setattr(lp, "expand", lambda url, hops=5: None)
    r = client.get(path(link("https://bit.ly/abc")), headers=BROWSER)
    assert "waar hij echt heen gaat is onbekend" in r.text


def test_list_is_scoped_for_customers(admin, customer, client, routed):
    client.get(path(link("https://a.example/", rcpt="jan@klant.nl", domains=("klant.nl",))), headers=BROWSER)
    client.get(path(link("https://rhls.nl/", rcpt="piet@rhis.nl", domains=("rhis.nl",))), headers=BROWSER)
    client.get(path(link("https://b.example/")), headers={"User-Agent": "curl/8"})
    a = admin.get("/api/links").json()
    assert a["total"] == 2 and a["counts"] == {"safe": 1, "warn": 1, "block": 0, "continued": 0} and a["can_block"]
    assert admin.get("/api/links?scanners=true").json()["total"] == 3
    assert admin.get("/api/links?level=warn").json()["items"][0]["host"] == "rhls.nl"
    c = customer.get("/api/links").json()
    assert [i["host"] for i in c["items"]] == ["a.example"] and not c["can_block"] and c["items"][0]["ip"] is None
    assert customer.post("/api/links/block", json={"domain": "x.example"}).status_code == 403


def test_test_tool(admin, routed):
    r = admin.post("/api/links/test", json={"url": "rhls.nl/login"}).json()
    assert r["level"] == "warn" and r["texts"]["nl"] == ["rhls.nl lijkt op rhis.nl, maar is het niet."]


def test_org_domain_and_lookalike():
    assert lp.org_domain("www.shop.co.uk") == "shop.co.uk"
    assert lp.org_domain("a.b.rhis.nl") == "rhis.nl"
    assert lp.lookalike("rhis.nl.login-secure.com", ["rhis.nl"]) == "rhis.nl"
    assert lp.lookalike("rnicrosoft.com", ["microsoft.com"]) == "microsoft.com"
    assert lp.lookalike("shop.example", ["rhis.nl"]) is None


REAL_BLOCKLISTS = lp.blocklists  # saved before the fixture replaces it


def test_blocklist_answers(monkeypatch):
    answers = {"evil.example.dbl.spamhaus.org": ["127.0.1.4"], "evil.example.multi.surbl.org": ["127.0.0.24"],
               "evil.example.multi.uribl.com": ["127.0.0.1"], "www.evil.example.dbl.spamhaus.org": ["127.255.255.254"]}
    monkeypatch.setattr(lp, "_a", lambda name: answers.get(name, []))
    assert REAL_BLOCKLISTS("www.evil.example") == [("SURBL", "malware"), ("SURBL", "phishing"), ("Spamhaus DBL", "phishing")]
    assert REAL_BLOCKLISTS("185.12.3.4") == []
