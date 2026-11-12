"""Cluster: two real nodes, each with its own PostgreSQL database.

Node A uses the normal test database, node B a second one. The transport that
normally does HTTPS calls the other node's FastAPI app with that node's
database switched in, so join, push, sync, leave and removal run end to end.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from mailfilter import db as dbmod
from mailfilter import gateway
from mailfilter.cluster import peer, snapshot, state
from mailfilter.db import session
from mailfilter.models import AppSetting, ClusterNode, Domain, DomainDestination, DomainRoute, RelayNetwork, User
from mailfilter.security import hash_password
from mailfilter.settings_store import get_secret, get_setting, set_secret

from conftest import PASSWORD, Client

URLS = {"a": "https://mf-a.example.net", "b": "https://mf-b.example.net"}
ADDRS = {"a": ["192.0.2.1", "2001:db8::1"], "b": ["192.0.2.2"]}


@pytest.fixture(scope="module")
def db_b():
    url = dbmod.get_engine().url
    name = (url.database or "mailfilter_test") + "_node_b"
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin_engine.dispose()
    engine = create_engine(url.set(database=name), future=True)
    from alembic import command

    from mailfilter.cli import _alembic_config

    original = dbmod.get_engine
    dbmod.get_engine = lambda: engine  # migrations/env.py takes the engine from here
    try:
        command.upgrade(_alembic_config(), "head")
    finally:
        dbmod.get_engine = original
    yield engine
    engine.dispose()


class Nodes:
    """Switches the app between node A and node B."""

    def __init__(self, monkeypatch, engine_b):
        self.current = "a"
        self.makers = {"a": dbmod._sessionmaker(), "b": sessionmaker(bind=engine_b, expire_on_commit=False)}
        self.dkim = {"a": {}, "b": {}}
        self.applied = {"a": [], "b": []}
        self.apply_ok = {"a": True, "b": True}
        monkeypatch.setattr(dbmod, "_sessionmaker", lambda: self.makers[self.current])
        monkeypatch.setattr(state, "own_url", lambda: URLS[self.current])
        monkeypatch.setattr(state, "own_addresses", lambda: ADDRS[self.current])
        monkeypatch.setattr(gateway, "run_helper", self._apply)
        monkeypatch.setattr(snapshot, "run_dkim_helper", self._dkim)
        monkeypatch.setattr(peer, "TRANSPORT", self._transport)
        monkeypatch.setenv("MF_CLUSTER_PUSH", "0")
        with engine_b.begin() as c:
            tables = [r[0] for r in c.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
            c.execute(text("TRUNCATE " + ",".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))

    def _apply(self, config_json):
        self.applied[self.current].append(json.loads(config_json))
        if not self.apply_ok[self.current]:
            return {"ok": False, "step": "postfix check", "output": "broken"}
        cfg = json.loads(config_json)
        for d in cfg["domains"]:  # like mailfilter-config: a new domain gets its own key
            self.dkim[self.current].setdefault(d["name"], {"domain": d["name"], "selector": f"own{self.current}",
                                                           "key": f"KEY-{self.current}-{d['name']}", "txt": ""})
        return {"ok": True, "changed": [], "warnings": []}

    def _dkim(self, req):
        store = self.dkim[self.current]
        if req["action"] == "export":
            return {"ok": True, "keys": list(store.values())}
        for k in req["keys"]:
            store[k["domain"]] = dict(k)
        return {"ok": True, "changed": [k["domain"] for k in req["keys"]]}

    @contextlib.contextmanager
    def on(self, node):
        before, self.current = self.current, node
        try:
            yield
        finally:
            self.current = before

    def _transport(self, url, op, token):
        target = next(n for n, u in URLS.items() if u == url)
        with self.on(target):
            r = Client().post(f"/api/cluster/peer/{op}", content=token,
                              headers={"Content-Type": "application/octet-stream"})
        return r.status_code, r.text

    def admin(self, node, email="admin@rhis.nl"):
        with self.on(node):
            return Client().login(email)

    def call(self, node, method, path, **kw):
        with self.on(node):
            c = self.clients.setdefault(node, None) or self.admin(node)
            self.clients[node] = c
            return getattr(c, method)(path, **kw)

    clients: dict = {}


@pytest.fixture
def nodes(monkeypatch, db_b):
    Nodes.clients = {}
    n = Nodes(monkeypatch, db_b)
    n.dkim["a"]["rhis.nl"] = {"domain": "rhis.nl", "selector": "mf2026", "key": "KEY-a-rhis.nl", "txt": "v=DKIM1"}
    # Node A: an existing server with a domain and users; node B: a fresh install.
    with n.on("a"), session() as db:
        dom = Domain(name="rhis.nl")
        db.add(dom)
        db.flush()
        route = DomainRoute(domain_id=dom.id)
        route.destinations = [DomainDestination(domain_id=dom.id, host="mail.rhis.nl")]
        db.add(route)
        db.add(User(email="admin@rhis.nl", name="Reinder", role="admin", password_hash=hash_password(PASSWORD)))
        cust = User(email="info@rhis.nl", name="Klant", role="customer", password_hash=hash_password(PASSWORD))
        cust.domains = [dom]
        db.add(cust)
        db.add(RelayNetwork(cidr="203.0.113.10/32", description="web01", domain_id=dom.id))
        set_secret(db, "smtp.password", "s3cret")
        db.commit()
    with n.on("b"), session() as db:
        db.add(User(email="admin@rhis.nl", name="Fresh", role="admin", password_hash=hash_password(PASSWORD)))
        db.add(User(email="old@b.nl", name="Only on B", role="admin", password_hash=hash_password(PASSWORD)))
        db.commit()
    return n


def form(n, node):
    with n.on(node), session() as db:
        return {
            "domains": sorted(d.name for d in db.execute(select(Domain)).scalars()),
            "users": sorted(u.email for u in db.execute(select(User)).scalars()),
            "nets": sorted(x.cidr for x in db.execute(select(RelayNetwork)).scalars()),
            "nodes": sorted(x.name for x in db.execute(select(ClusterNode)).scalars()),
            "rev": state.revision(db),
        }


def make_cluster(n):
    r = n.call("a", "post", "/api/cluster/create", json={"name": "mailfilter01"})
    assert r.status_code == 200, r.text
    code = n.call("a", "get", "/api/cluster/join-code").json()["code"]
    r = n.call("b", "post", "/api/cluster/join", json={"code": code, "name": "mailfilter02"})
    assert r.status_code == 200, r.text
    return code


def test_single_server_is_untouched(nodes):
    """Without a cluster nothing is tracked, pushed or exposed."""
    r = nodes.call("a", "post", "/api/gateway/domains", json={"name": "solo.nl", "destinations": [{"host": "mx.solo.nl"}]})
    assert r.status_code == 201, r.text
    with nodes.on("a"), session() as db:
        assert state.revision(db) is None and not state.enabled(db)
    ov = nodes.call("a", "get", "/api/cluster").json()
    assert ov["enabled"] is False and ov["nodes"] == []
    with nodes.on("a"):
        r = Client().post("/api/cluster/peer/hello", content=b"x")
    assert r.status_code == 403 and r.json()["detail"] == "cluster_not_in_cluster"


def test_join_copies_configuration_users_and_dkim(nodes):
    make_cluster(nodes)
    a, b = form(nodes, "a"), form(nodes, "b")
    assert b["domains"] == a["domains"] == ["rhis.nl"]
    assert b["users"] == ["admin@rhis.nl", "info@rhis.nl"]  # B's own users are replaced
    assert b["nets"] == ["203.0.113.10/32"]
    assert a["nodes"] == b["nodes"] == ["mailfilter01", "mailfilter02"]
    assert state.rev_key(a["rev"]) == state.rev_key(b["rev"])
    # Same DKIM key on both nodes; B did not make one of its own.
    assert nodes.dkim["b"]["rhis.nl"]["key"] == nodes.dkim["a"]["rhis.nl"]["key"] == "KEY-a-rhis.nl"
    with nodes.on("b"), session() as db:
        assert get_secret(db, "smtp.password") == "s3cret"  # re-encrypted with B's own key
        cust = db.execute(select(User).where(User.email == "info@rhis.nl")).scalar_one()
        assert cust.domain_names == ["rhis.nl"]
        net = db.execute(select(RelayNetwork)).scalar_one()
        assert net.domain.name == "rhis.nl"
    # The cluster password works on B
    with nodes.on("b"):
        Client().login("info@rhis.nl")
    ov = nodes.call("b", "get", "/api/cluster").json()
    assert ov["enabled"] and [x["state"] for x in ov["nodes"]] == ["self", "ok"]


def test_change_on_either_node_reaches_the_other(nodes):
    make_cluster(nodes)
    nodes.clients.clear()
    r = nodes.call("b", "post", "/api/gateway/domains", json={"name": "klant.nl", "destinations": [{"host": "mx.klant.nl"}]})
    assert r.status_code == 201, r.text
    with nodes.on("b"):
        peer.push_all()
    assert form(nodes, "a")["domains"] == ["klant.nl", "rhis.nl"]
    assert nodes.dkim["a"]["klant.nl"]["key"] == "KEY-b-klant.nl"
    # And back, this time through the timer's sync
    r = nodes.call("a", "post", "/api/gateway/relay-networks", json={"cidr": "198.51.100.7", "description": "web02"})
    assert r.status_code == 201, r.text
    with nodes.on("b"), session() as db:
        assert peer.sync_all(db) == {"mailfilter01": "applied"}
    assert "198.51.100.7" in form(nodes, "b")["nets"]
    with nodes.on("a"), session() as db:
        assert peer.sync_all(db) == {"mailfilter02": "same"}


def test_login_does_not_count_as_a_change(nodes):
    make_cluster(nodes)
    before = form(nodes, "a")["rev"]
    nodes.admin("a", "info@rhis.nl")
    assert form(nodes, "a")["rev"] == before


def test_peer_down_keeps_node_working(nodes, monkeypatch):
    make_cluster(nodes)

    def down(url, op, token):
        raise state.ClusterError("unreachable", "connection refused")

    monkeypatch.setattr(peer, "TRANSPORT", down)
    r = nodes.call("a", "post", "/api/gateway/domains", json={"name": "later.nl", "destinations": [{"host": "mx.later.nl"}]})
    assert r.status_code == 201  # the change works locally
    with nodes.on("a"), session() as db:
        assert peer.sync_all(db) == {"mailfilter02": "error"}
    ov = nodes.call("a", "get", "/api/cluster").json()
    assert ov["nodes"][1]["state"] == "unreachable"
    monkeypatch.setattr(peer, "TRANSPORT", nodes._transport)
    with nodes.on("b"), session() as db:  # B comes back and catches up
        assert peer.sync_all(db) == {"mailfilter01": "applied"}
    assert "later.nl" in form(nodes, "b")["domains"]


def test_refused_configuration_is_not_applied(nodes):
    make_cluster(nodes)
    nodes.apply_ok["b"] = False
    nodes.call("a", "post", "/api/gateway/domains", json={"name": "bad.nl", "destinations": [{"host": "mx.bad.nl"}]})
    with nodes.on("a"):
        peer.push_all()
    assert "bad.nl" not in form(nodes, "b")["domains"]
    with nodes.on("b"), session() as db:
        assert get_setting(db, state.LAST_IMPORT_KEY)["error"] == "apply_failed"
    ov = nodes.call("a", "get", "/api/cluster").json()
    assert ov["nodes"][1]["last_error"].startswith("peer_failed")


def test_sealed_requests(nodes):
    make_cluster(nodes)
    with nodes.on("a"), session() as db:
        key = state.secret(db)
        b_id = next(x.node_id for x in state.peers(db))
    hello = {"from": b_id, "revision": None, "version": "x"}

    def post(token):
        with nodes.on("a"):
            return Client().post("/api/cluster/peer/hello", content=token)

    tok = state.seal(key, "req:hello", hello, nonce="a" * 32)
    assert post(tok).status_code == 200
    assert post(tok).json()["detail"] == "cluster_replay"
    assert post(state.seal("wrong" * 10, "req:hello", hello)).json()["detail"] == "cluster_auth_failed"
    assert post(state.seal(key, "req:push", hello)).json()["detail"] == "cluster_bad_message"
    assert post(state.seal(key, "req:hello", {**hello, "from": "nobody"})).json()["detail"] == "cluster_unknown_node"
    with pytest.raises(state.ClusterError):
        state.parse_join_code("MFC1.bm9wZQ")


def test_remove_node_rotates_secret_and_leave(nodes):
    code = make_cluster(nodes)
    with nodes.on("a"), session() as db:
        old = state.secret(db)
        b_id = next(x.node_id for x in state.peers(db))
    r = nodes.call("a", "delete", f"/api/cluster/nodes/{b_id}")
    assert r.status_code == 200 and [x["name"] for x in r.json()["nodes"]] == ["mailfilter01"]
    with nodes.on("a"), session() as db:
        assert state.secret(db) != old
    # B is told when it next syncs... but A no longer knows it, so it is refused.
    with nodes.on("b"), session() as db:
        assert peer.sync_all(db) == {"mailfilter01": "error"}
    # The old join code no longer works either
    nodes.clients.clear()
    with nodes.on("b"), session() as db:
        state.leave_locally(db)
        db.commit()
    r = nodes.call("b", "post", "/api/cluster/join", json={"code": code, "name": "mailfilter02"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "cluster_auth_failed"
    # B keeps its configuration after leaving
    assert form(nodes, "b")["domains"] == ["rhis.nl"]


def test_leave_tells_the_others(nodes):
    make_cluster(nodes)
    r = nodes.call("b", "post", "/api/cluster/leave")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert form(nodes, "a")["nodes"] == ["mailfilter01"]
    assert form(nodes, "b")["domains"] == ["rhis.nl"]


def test_push_from_removed_member_list_makes_node_leave(nodes):
    make_cluster(nodes)
    with nodes.on("a"), session() as db:
        b = next(iter(state.peers(db)))
        snap = snapshot.export(db)
    snap["cluster"]["nodes"] = [x for x in snap["cluster"]["nodes"] if x["node_id"] != b.node_id]
    snap["revision"] = {"at": "9999-01-01T00:00:00", "node": "x"}
    with nodes.on("b"), session() as db:
        assert peer.receive_snapshot(db, snap, "test") == "removed"
        assert not state.enabled(db)
        assert db.execute(select(AppSetting).where(AppSetting.key == "cluster")).scalar_one().value is None


def test_spf_lists_all_nodes(nodes, monkeypatch):
    make_cluster(nodes)
    from mailfilter.api import dns

    with nodes.on("b"):
        s = dns.get_server()
    assert {"192.0.2.1", "192.0.2.2"} <= set(s.public_v4) and "2001:db8::1" in s.public_v6


def test_lists_and_quarantine_report_are_synced(nodes):
    from mailfilter.models import DigestDomain, SenderListEntry

    make_cluster(nodes)
    with nodes.on("a"), session() as db:
        dom = db.execute(select(Domain).where(Domain.name == "rhis.nl")).scalar_one()
        db.add(SenderListEntry(list="block", recipient="rhis.nl", sender="spam.example", domain_id=dom.id))
        db.add(DigestDomain(domain_id=dom.id, enabled=False))
        db.commit()
        peer.push_all()
    with nodes.on("b"), session() as db:
        e = db.execute(select(SenderListEntry)).scalar_one()
        assert (e.list, e.sender, e.domain.name) == ("block", "spam.example", "rhis.nl")
        assert db.execute(select(DigestDomain)).scalar_one().enabled is False


def test_two_factor_and_policy_are_synced(nodes):
    import time

    from mailfilter import twofactor

    make_cluster(nodes)
    nodes.clients.clear()
    secret = nodes.call("a", "post", "/api/auth/2fa/setup").json()["secret"]
    codes = nodes.call("a", "post", "/api/auth/2fa/enable", json={"code": twofactor.totp(secret)}).json()["codes"]
    assert nodes.call("a", "put", "/api/system/security", json={"require_2fa": "admins"}).status_code == 200
    with nodes.on("a"):
        peer.push_all()
    with nodes.on("b"), session() as db:
        u = db.execute(select(User).where(User.email == "admin@rhis.nl")).scalar_one()
        assert u.two_factor_enabled and twofactor.decrypt_secret(u.totp_secret) == secret
        assert twofactor.get_policy(db) == "admins"
    with nodes.on("b"):
        c = Client()
        r = c.post("/api/auth/login", json={"email": "admin@rhis.nl", "password": PASSWORD})
        assert r.json()["two_factor_required"] is True
        r = c.post("/api/auth/login/2fa", json={"challenge": r.json()["challenge"],
                                                "code": twofactor.totp(secret, time.time() + 30)})
        assert r.status_code == 200, r.text
    # Using a TOTP code is not a configuration change; using a recovery code is.
    before = form(nodes, "b")["rev"]
    with nodes.on("b"), session() as db:
        assert form(nodes, "b")["rev"] == before
        u = db.execute(select(User).where(User.email == "admin@rhis.nl")).scalar_one()
        assert twofactor.use_recovery_code(u, codes[0])
        db.commit()
        peer.push_all()
    with nodes.on("a"), session() as db:
        u = db.execute(select(User).where(User.email == "admin@rhis.nl")).scalar_one()
        assert len(u.recovery_codes) == 9


def test_billing_customers_resellers_and_tariffs_are_synced(nodes):
    from mailfilter.models import BillingCustomer, Reseller

    make_cluster(nodes)
    r = nodes.call("a", "post", "/api/billing/resellers", json={"name": "WebBouw Noord", "ref": "D-2001", "discount_percent": 20})
    rid = r.json()["resellers"][0]["id"]
    nodes.call("a", "post", "/api/billing/customers", json={"name": "RHIS", "ref": "D-1", "reseller_id": rid, "domains": ["rhis.nl"]})
    nodes.call("a", "put", "/api/billing/tariffs", json={"per_mailbox": 0.75})
    nodes.call("a", "post", "/api/users", json={"email": "partner@webbouw.nl", "role": "reseller", "reseller_id": rid})
    with nodes.on("a"):
        peer.push_all()
    with nodes.on("b"), session() as db:
        rs = db.execute(select(Reseller)).scalar_one()
        c = db.execute(select(BillingCustomer)).scalar_one()
        dom = db.execute(select(Domain).where(Domain.name == "rhis.nl")).scalar_one()
        u = db.execute(select(User).where(User.email == "partner@webbouw.nl")).scalar_one()
        assert (rs.name, float(rs.discount_percent), c.ref, c.reseller_id) == ("WebBouw Noord", 20.0, "D-1", rs.id)
        assert dom.billing_customer_id == c.id and u.reseller_id == rs.id
        from mailfilter import usage
        assert usage.load_tariffs(db)["per_mailbox"] == 0.75
