"""In-memory Cloudflare API for tests, plugged into httpx.MockTransport."""

from __future__ import annotations

import json
import uuid

import httpx

from mailfilter_dns.cloudflare import Cloudflare


class FakeCloudflare:
    def __init__(self, zones: list[str], records: list[dict] | None = None, token: str = "good"):
        # "good" acts as a user token, "acct" as an account-owned token
        self.token = token
        self.zones = {f"z-{z}": z for z in zones}
        self.records: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        for r in records or []:
            self.add(r)

    def add(self, r: dict) -> dict:
        rec = {"id": uuid.uuid4().hex, "ttl": 1, **r}
        zone = next(zid for zid, name in self.zones.items() if rec["name"] == name or rec["name"].endswith("." + name))
        rec["zone_id"] = zone
        self.records[rec["id"]] = rec
        return rec

    def find(self, name: str, type: str) -> list[dict]:
        return [r for r in self.records.values() if r["name"] == name and r["type"] == type]

    def client(self) -> Cloudflare:
        return Cloudflare(self.token, client=httpx.Client(transport=httpx.MockTransport(self.handle)))

    def _ok(self, result, info=None):
        body = {"success": True, "errors": [], "result": result}
        if info:
            body["result_info"] = info
        return httpx.Response(200, json=body)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        auth = req.headers.get("authorization")
        if auth not in ("Bearer good", "Bearer acct"):
            return httpx.Response(403, json={"success": False, "errors": [{"code": 9109, "message": "Invalid access token"}]})
        parts = req.url.path.split("/")[3:]  # after /client/v4
        q = dict(req.url.params)
        if parts == ["user", "tokens", "verify"]:
            if auth == "Bearer acct":
                return httpx.Response(401, json={"success": False, "errors": [{"code": 1000, "message": "Invalid API Token"}]})
            return self._ok({"id": "t", "status": "active"})
        if parts == ["accounts", "acc1", "tokens", "verify"] and auth == "Bearer acct":
            return self._ok({"id": "t", "status": "active"})
        if parts == ["zones"]:
            zs = [{"id": i, "name": n, "status": "active", "account": {"id": "acc1", "name": "RHIS"}}
                  for i, n in self.zones.items() if not q.get("name") or q["name"] == n]
            return self._ok(zs, {"page": 1, "total_pages": 1})
        if len(parts) >= 3 and parts[0] == "zones" and parts[2] == "dns_records":
            zid = parts[1]
            if req.method == "GET":
                rs = [r for r in self.records.values() if r["zone_id"] == zid
                      and (not q.get("name.exact") or r["name"] == q["name.exact"]) and (not q.get("type") or r["type"] == q["type"])]
                return self._ok(rs, {"page": 1, "total_pages": 1})
            if req.method == "POST":
                new = json.loads(req.content)
                for r in self.records.values():
                    if r["zone_id"] == zid and r["type"] == new["type"] and r["name"] == new["name"] and r["content"] == new["content"]:
                        return httpx.Response(400, json={"success": False, "errors": [{"code": 81058, "message": "An identical record already exists."}]})
                return self._ok(self.add(new))
            rid = parts[3]
            if req.method == "PUT":
                rec = {**json.loads(req.content), "id": rid, "zone_id": zid}
                self.records[rid] = rec
                return self._ok(rec)
            if req.method == "DELETE":
                self.records.pop(rid)
                return self._ok({"id": rid})
        return httpx.Response(404, json={"success": False, "errors": [{"code": 7003, "message": "not found"}]})
