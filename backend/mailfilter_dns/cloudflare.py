"""Minimal Cloudflare API v4 client for the DNS records Mailfilter manages.

Needs an API token with the permissions ``Zone:Read`` and ``DNS:Edit`` for
the zones Mailfilter may change. Nothing else is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .resolver import norm

API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    def __init__(self, message: str, status: int | None = None, codes: list[int] | None = None):
        super().__init__(message)
        self.status = status
        self.codes = codes or []


@dataclass
class Zone:
    id: str
    name: str
    status: str = "active"


@dataclass
class DnsRecord:
    type: str
    name: str
    content: str
    ttl: int = 1  # 1 = automatic
    priority: int | None = None
    proxied: bool | None = None
    id: str | None = None
    comment: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "DnsRecord":
        content = d.get("content", "")
        if d.get("type") == "TXT":
            content = txt_value(content)
        elif d.get("type") == "TLSA" and isinstance(d.get("data"), dict):
            x = d["data"]
            content = f"{x.get('usage')} {x.get('selector')} {x.get('matching_type')} {str(x.get('certificate', '')).lower()}"
        return cls(
            type=d["type"],
            name=norm(d["name"]),
            content=content,
            ttl=d.get("ttl", 1),
            priority=d.get("priority"),
            proxied=d.get("proxied"),
            id=d.get("id"),
            comment=d.get("comment"),
        )

    def body(self) -> dict[str, Any]:
        b: dict[str, Any] = {"type": self.type, "name": self.name, "content": self.content, "ttl": self.ttl}
        if self.type == "TXT":
            b["content"] = txt_quote(self.content)
        elif self.type == "TLSA":
            usage, selector, mtype, cert = self.content.split(None, 3)
            b.pop("content")
            b["data"] = {"usage": int(usage), "selector": int(selector), "matching_type": int(mtype),
                         "certificate": cert.replace(" ", "").lower()}
        if self.priority is not None:
            b["priority"] = self.priority
        if self.type in ("A", "AAAA", "CNAME"):
            b["proxied"] = bool(self.proxied)  # mail names must never be proxied
        if self.comment:
            b["comment"] = self.comment[:100]
        return b

    def snapshot(self) -> dict[str, Any]:
        return {"type": self.type, "name": self.name, "content": self.content, "ttl": self.ttl,
                "priority": self.priority, "proxied": self.proxied, "id": self.id}


def txt_value(content: str) -> str:
    """Cloudflare returns TXT content either bare or as quoted 255-byte chunks."""
    c = content.strip()
    if c.startswith('"') and c.endswith('"'):
        import re

        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', c)
        return "".join(p.replace('\\"', '"') for p in parts)
    return c


def txt_quote(value: str) -> str:
    """Quote a TXT value in 255-character strings, as Cloudflare recommends."""
    value = value.replace('"', '\\"')
    chunks = [value[i:i + 255] for i in range(0, len(value), 255)] or [""]
    return " ".join(f'"{c}"' for c in chunks)


class Cloudflare:
    def __init__(self, token: str, *, client: httpx.Client | None = None, base_url: str = API, timeout: float = 15.0):
        if not token:
            raise CloudflareError("no Cloudflare API token configured")
        self._c = client or httpx.Client(timeout=timeout)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def close(self) -> None:
        self._c.close()

    def _req(self, method: str, path: str, **kw) -> Any:
        try:
            r = self._c.request(method, self._base + path, headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise CloudflareError(f"cannot reach Cloudflare: {exc}") from exc
        try:
            data = r.json()
        except ValueError:
            raise CloudflareError(f"Cloudflare answered HTTP {r.status_code} without JSON", r.status_code)
        if not data.get("success", False):
            errs = data.get("errors") or []
            msg = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errs) or f"HTTP {r.status_code}"
            raise CloudflareError(msg, r.status_code, [e.get("code") for e in errs])
        return data

    def _paged(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out, page = [], 1
        while True:
            data = self._req("GET", path, params={**params, "page": page, "per_page": 100})
            out.extend(data.get("result") or [])
            info = data.get("result_info") or {}
            if page >= int(info.get("total_pages") or 1):
                return out
            page += 1

    # --- token and zones ---------------------------------------------------

    def verify_token(self) -> dict[str, Any]:
        """Return the token status (``active`` when usable).

        User tokens (My Profile > API Tokens) verify at /user/tokens/verify.
        Account tokens (Manage Account > API Tokens) do not: that endpoint
        says "Invalid API Token" for them. They verify at
        /accounts/<id>/tokens/verify, and the account id is found from the
        zones the token can see, so nobody has to type it in.
        """
        try:
            return self._req("GET", "/user/tokens/verify")["result"]
        except CloudflareError as first:
            if first.status is None:
                raise
            try:
                zones = self._req("GET", "/zones", params={"per_page": 5})["result"] or []
            except CloudflareError:
                raise first
            if not zones:
                raise first
            account = (zones[0].get("account") or {}).get("id")
            if account:
                try:
                    res = self._req("GET", f"/accounts/{account}/tokens/verify")["result"]
                    return {**res, "account_id": account, "kind": "account"}
                except CloudflareError:
                    pass
            # The token can read zones, which is what Mailfilter needs
            return {"status": "active", "account_id": account, "kind": "account"}

    def zones(self) -> list[Zone]:
        return [Zone(z["id"], z["name"], z.get("status", "")) for z in self._paged("/zones", {})]

    def zone_for(self, name: str) -> Zone | None:
        """The zone that holds ``name``: example.com for mail.sub.example.com."""
        labels = norm(name).split(".")
        for i in range(len(labels) - 1):
            cand = ".".join(labels[i:])
            res = self._req("GET", "/zones", params={"name": cand})["result"]
            if res:
                z = res[0]
                return Zone(z["id"], z["name"], z.get("status", ""))
        return None

    # --- records -----------------------------------------------------------

    def records(self, zone: Zone, *, name: str | None = None, type: str | None = None) -> list[DnsRecord]:
        params: dict[str, Any] = {}
        if name:
            params["name.exact"] = norm(name)
        if type:
            params["type"] = type
        recs = [DnsRecord.from_api(d) for d in self._paged(f"/zones/{zone.id}/dns_records", params)]
        # Filter here as well, so the result is right whatever the API does with the name filter
        if name:
            recs = [r for r in recs if r.name == norm(name)]
        if type:
            recs = [r for r in recs if r.type == type]
        return recs

    def create(self, zone: Zone, rec: DnsRecord) -> DnsRecord:
        try:
            return DnsRecord.from_api(self._req("POST", f"/zones/{zone.id}/dns_records", json=rec.body())["result"])
        except CloudflareError as exc:
            # 81057/81058: the record is already there. That is the result we wanted.
            if not {81057, 81058} & set(exc.codes):
                raise
            same = [r for r in self.records(zone, name=rec.name, type=rec.type)
                    if r.content == rec.content and (rec.priority is None or r.priority == rec.priority)]
            if not same:
                raise
            return same[0]

    def update(self, zone: Zone, rec: DnsRecord) -> DnsRecord:
        if not rec.id:
            raise CloudflareError("cannot update a record without id")
        return DnsRecord.from_api(
            self._req("PUT", f"/zones/{zone.id}/dns_records/{rec.id}", json=rec.body())["result"]
        )

    def delete(self, zone: Zone, record_id: str) -> None:
        self._req("DELETE", f"/zones/{zone.id}/dns_records/{record_id}")
