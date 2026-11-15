"""Cluster in backup mode: a primary and a standby that takes over when it is down.

Uses the two-node setup of test_cluster.py (each node its own database)."""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import select

from mailfilter.cluster import failover, peer, state
from mailfilter.db import session
from mailfilter.models import ClusterNode
from mailfilter.settings_store import get_setting, set_secret, set_setting
from mailfilter_dns import publish
from mailfilter_dns.cloudflare import DnsRecord, Zone

from conftest import Client
from test_cluster import db_b, form, nodes  # noqa: F401 - fixtures


class FakeCloudflare:
    """Holds the records of one zone, rhis.nl."""

    ids = itertools.count(1)

    def __init__(self):
        self.recs: list[DnsRecord] = []

    def zone_for(self, name):
        return Zone("z1", "rhis.nl") if name.endswith("rhis.nl") else None

    def records(self, zone, *, name=None, type=None):
        return [DnsRecord(r.type, r.name, r.content, r.ttl, r.priority, r.proxied, r.id) for r in self.recs
                if (name is None or r.name == name) and (type is None or r.type == type)]

    def create(self, zone, rec):
        rec = DnsRecord(rec.type, rec.name, rec.content, rec.ttl, rec.priority, rec.proxied, f"r{next(self.ids)}")
        self.recs.append(rec)
        return rec

    def update(self, zone, rec):
        self.delete(zone, rec.id)
        self.recs.append(rec)
        return rec

    def delete(self, zone, record_id):
        self.recs = [r for r in self.recs if r.id != record_id]

    def ips(self, name):
        return sorted(r.content for r in self.recs if r.name == name and r.type in ("A", "AAAA"))


@pytest.fixture
def cf(monkeypatch, tmp_path):
    fake = FakeCloudflare()
    monkeypatch.setattr(failover, "make_cloudflare", lambda token: fake)
    monkeypatch.setattr(publish, "JOURNAL", tmp_path / "journal.jsonl")
    return fake


def make_backup(n):
    r = n.call("a", "post", "/api/cluster/create", json={"name": "mailfilter01", "mode": "standby"})
    assert r.status_code == 200, r.text
    code = n.call("a", "get", "/api/cluster/join-code").json()["code"]
    r = n.call("b", "post", "/api/cluster/join", json={"code": code, "name": "mailfilter02"})
    assert r.status_code == 200, r.text
    return r.json()


def roles(n, node):
    with n.on(node), session() as db:
        return {x.name: x.role for x in db.execute(select(ClusterNode)).scalars()}


def test_backup_mode_roles_and_read_only_standby(nodes):
    ov = make_backup(nodes)
    assert ov["mode"] == "standby" and ov["role"] == "standby" and ov["primary"] == "mailfilter01"
    assert roles(nodes, "a") == roles(nodes, "b") == {"mailfilter01": "primary", "mailfilter02": "standby"}
    # A change on the backup is refused with a clear reason...
    r = nodes.call("b", "post", "/api/gateway/domains", json={"name": "x.nl", "destinations": [{"host": "mx.x.nl"}]})
    assert r.status_code == 409 and r.json()["detail"] == {"code": "cluster_standby_readonly", "detail": "mailfilter01"}
    assert "x.nl" not in form(nodes, "b")["domains"]
    # ...one on the primary reaches it.
    r = nodes.call("a", "post", "/api/gateway/domains", json={"name": "klant.nl", "destinations": [{"host": "mx.klant.nl"}]})
    assert r.status_code == 201
    with nodes.on("a"):
        peer.push_all()
    assert "klant.nl" in form(nodes, "b")["domains"]
    # The backup cannot hand out join codes; the banner endpoint knows its role.
    assert nodes.call("b", "get", "/api/cluster/join-code").status_code == 409
    st = nodes.call("b", "get", "/api/cluster/status").json()
    assert st == {"enabled": True, "mode": "standby", "role": "standby", "primary": "mailfilter01",
                  "primary_url": "https://mf-a.example.net", "failover_active": False}


def test_switch_existing_cluster_to_backup_mode_and_back(nodes):
    nodes.call("a", "post", "/api/cluster/create", json={"name": "mailfilter01"})
    code = nodes.call("a", "get", "/api/cluster/join-code").json()["code"]
    nodes.call("b", "post", "/api/cluster/join", json={"code": code, "name": "mailfilter02"})
    assert roles(nodes, "b") == {"mailfilter01": "member", "mailfilter02": "member"}
    r = nodes.call("a", "put", "/api/cluster/mode", json={"mode": "standby"})
    assert r.status_code == 200 and r.json()["mode"] == "standby"
    with nodes.on("a"):
        peer.push_all()
    assert roles(nodes, "b") == {"mailfilter01": "primary", "mailfilter02": "standby"}
    with nodes.on("b"), session() as db:
        assert state.mode(db) == "standby"
    assert nodes.call("b", "put", "/api/cluster/mode", json={"mode": "active"}).status_code == 409
    nodes.call("a", "put", "/api/cluster/mode", json={"mode": "active"})
    with nodes.on("a"):
        peer.push_all()
    assert roles(nodes, "b") == {"mailfilter01": "member", "mailfilter02": "member"}


def test_promote_backup(nodes):
    make_backup(nodes)
    r = nodes.call("b", "post", "/api/cluster/promote")
    assert r.status_code == 200 and r.json()["role"] == "primary"
    with nodes.on("b"):
        peer.push_all()
    assert roles(nodes, "a") == {"mailfilter01": "standby", "mailfilter02": "primary"}
    # Now B is where changes are made
    r = nodes.call("b", "post", "/api/gateway/domains", json={"name": "nieuw.nl", "destinations": [{"host": "mx.nieuw.nl"}]})
    assert r.status_code == 201, r.text
    assert nodes.call("a", "post", "/api/gateway/domains",
                      json={"name": "oud.nl", "destinations": [{"host": "mx.oud.nl"}]}).status_code == 409


def _age(n, node, key, minutes):
    with n.on(node), session() as db:
        st = dict(get_setting(db, state.FAILOVER_STATE_KEY) or {})
        st[key] = (state.now() - timedelta(minutes=minutes)).isoformat()
        set_setting(db, state.FAILOVER_STATE_KEY, st)
        db.commit()


def _check(n, node):
    with n.on(node), session() as db:
        peer.sync_all(db)
        return failover.check(db)


def test_failover_takes_over_and_hands_back(nodes, cf, monkeypatch):
    make_backup(nodes)
    with nodes.on("a"), session() as db:
        set_secret(db, "cloudflare_token", "tok")
        db.commit()
    r = nodes.call("a", "put", "/api/cluster/failover",
                   json={"enabled": True, "hostname": "smtp.rhis.nl", "after_minutes": 3, "back_minutes": 5})
    assert r.status_code == 200, r.text
    assert nodes.call("a", "post", "/api/cluster/failover/point-primary").status_code == 200
    assert cf.ips("smtp.rhis.nl") == ["192.0.2.1", "2001:db8::1"]
    with nodes.on("a"):
        peer.push_all()
    assert _check(nodes, "b") == "ok"

    # The primary goes down: no HTTPS, no port 25.
    real = peer.TRANSPORT

    def down(url, op, token):
        if url == "https://mf-a.example.net":
            raise state.ClusterError("unreachable", "connection refused")
        return real(url, op, token)

    monkeypatch.setattr(peer, "TRANSPORT", down)
    monkeypatch.setattr(failover, "PROBE", lambda addrs: False)
    with nodes.on("b"), session() as db:  # last contact long ago
        p = state.primary(db)
        p.last_seen_at = state.now() - timedelta(minutes=10)
        db.commit()
    assert _check(nodes, "b") == "waiting"
    assert cf.ips("smtp.rhis.nl") == ["192.0.2.1", "2001:db8::1"]
    _age(nodes, "b", "down_since", 4)
    assert _check(nodes, "b") == "took_over"
    assert cf.ips("smtp.rhis.nl") == ["192.0.2.2"]
    assert _check(nodes, "b") == "active"
    assert nodes.call("b", "get", "/api/cluster/status").json()["failover_active"] is True

    # The primary is back: hand back after back_minutes.
    monkeypatch.setattr(peer, "TRANSPORT", real)
    assert _check(nodes, "b") == "handing_back"
    assert cf.ips("smtp.rhis.nl") == ["192.0.2.2"]
    _age(nodes, "b", "up_since", 6)
    assert _check(nodes, "b") == "handed_back"
    assert cf.ips("smtp.rhis.nl") == ["192.0.2.1", "2001:db8::1"]
    # The primary sees the standby's state through hello
    with nodes.on("a"), session() as db:
        peer.sync_all(db)
    ov = nodes.call("a", "get", "/api/cluster").json()
    assert ov["nodes"][1]["failover"]["active"] is False


def test_failover_off_does_nothing(nodes, cf, monkeypatch):
    make_backup(nodes)
    monkeypatch.setattr(failover, "PROBE", lambda addrs: False)
    assert _check(nodes, "b") == "off"
    assert _check(nodes, "a") == "off"
    assert cf.recs == []


def test_failover_name_must_not_be_a_node(nodes):
    make_backup(nodes)
    r = nodes.call("a", "put", "/api/cluster/failover", json={"enabled": True, "hostname": "mf-a.example.net"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "cluster_failover_is_node_name"


def test_mx_for_live_domains(nodes, cf):
    make_backup(nodes)
    with nodes.on("a"), session() as db:
        set_secret(db, "cloudflare_token", "tok")
        db.commit()
    cf.recs.append(DnsRecord("MX", "rhis.nl", "mf-a.example.net", 1, 10, None, "mx1"))
    r = nodes.call("a", "post", "/api/cluster/mx/plan")
    assert r.status_code == 200 and [p["domain"] for p in r.json()["plans"]] == ["rhis.nl"]
    r = nodes.call("a", "post", "/api/cluster/mx/apply")
    assert r.status_code == 200, r.text
    mx = sorted((x.priority, x.content) for x in cf.recs if x.type == "MX")
    assert mx == [(10, "mf-a.example.net"), (20, "mf-b.example.net")]


def test_customer_sees_banner_status(nodes):
    make_backup(nodes)
    with nodes.on("b"):
        c = Client().login("info@rhis.nl")
        assert c.get("/api/cluster/status").json()["role"] == "standby"
