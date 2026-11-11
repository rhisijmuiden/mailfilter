"""Small DNS layer on top of dnspython.

Everything in this package asks DNS questions through a ``Resolver`` so tests
can swap in a ``StaticResolver`` with canned answers. On the server the
``DnsResolver`` uses the local unbound resolver that the installer sets up
(127.0.0.1), which is also what the DNS blocklists require: Spamhaus and others
refuse queries that come through public resolvers such as 1.1.1.1 or 8.8.8.8.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable, Protocol


class DnsError(Exception):
    """A lookup failed for a reason other than "the name has no such record"."""


@dataclass(frozen=True)
class MxRecord:
    priority: int
    host: str  # without trailing dot, lower case


class Resolver(Protocol):
    def txt(self, name: str) -> list[str]: ...
    def mx(self, name: str) -> list[MxRecord]: ...
    def a(self, name: str) -> list[str]: ...
    def aaaa(self, name: str) -> list[str]: ...
    def ptr(self, ip: str) -> list[str]: ...


def norm(name: str) -> str:
    """Lower case, no trailing dot, IDN converted to its ASCII (xn--) form."""
    name = name.strip().rstrip(".").lower()
    if not name:
        return name
    try:
        return name.encode("idna").decode("ascii")
    except UnicodeError:
        return name


def reverse_name(ip: str) -> str:
    """``192.0.2.1`` -> ``1.2.0.192`` and IPv6 to its nibble form, without suffix."""
    addr = ipaddress.ip_address(ip)
    rev = addr.reverse_pointer  # 1.2.0.192.in-addr.arpa / ...ip6.arpa
    return rev.rsplit(".", 2)[0]


class DnsResolver:
    """Real resolver. ``nameservers`` defaults to the system configuration."""

    def __init__(self, nameservers: Iterable[str] | None = None, timeout: float = 5.0):
        import dns.resolver

        self._dns = dns
        self._r = dns.resolver.Resolver(configure=nameservers is None)
        if nameservers is not None:
            self._r.nameservers = list(nameservers)
        self._r.lifetime = timeout
        self._r.timeout = timeout

    def _query(self, name: str, rdtype: str):
        import dns.exception
        import dns.resolver

        try:
            return list(self._r.resolve(name, rdtype, search=False))
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.resolver.NoNameservers as exc:
            raise DnsError(f"{name} {rdtype}: no name server answered (SERVFAIL)") from exc
        except dns.exception.Timeout as exc:
            raise DnsError(f"{name} {rdtype}: timeout") from exc

    def txt(self, name: str) -> list[str]:
        out = []
        for rr in self._query(name, "TXT"):
            out.append(b"".join(rr.strings).decode("utf-8", "replace"))
        return out

    def mx(self, name: str) -> list[MxRecord]:
        recs = [MxRecord(rr.preference, norm(rr.exchange.to_text())) for rr in self._query(name, "MX")]
        return sorted(recs, key=lambda r: (r.priority, r.host))

    def a(self, name: str) -> list[str]:
        return sorted(rr.address for rr in self._query(name, "A"))

    def aaaa(self, name: str) -> list[str]:
        return sorted(rr.address for rr in self._query(name, "AAAA"))

    def ptr(self, ip: str) -> list[str]:
        name = ipaddress.ip_address(ip).reverse_pointer
        return sorted(norm(rr.target.to_text()) for rr in self._query(name, "PTR"))


class StaticResolver:
    """Resolver with fixed answers, for tests and dry runs.

    ``records`` maps ``(name, type)`` to a list of values. MX values are
    ``(priority, host)`` tuples; PTR keys use the IP address as the name.
    A value of ``DnsError`` raises that error.
    """

    def __init__(self, records: dict[tuple[str, str], object] | None = None):
        self.records: dict[tuple[str, str], object] = {}
        for (name, rtype), vals in (records or {}).items():
            self.records[(norm(name) if rtype != "PTR" else name, rtype)] = vals

    def _get(self, name: str, rtype: str):
        key = (norm(name) if rtype != "PTR" else name, rtype)
        val = self.records.get(key, [])
        if isinstance(val, Exception):
            raise val
        return list(val)  # type: ignore[arg-type]

    def txt(self, name):
        return self._get(name, "TXT")

    def mx(self, name):
        return sorted((MxRecord(p, norm(h)) for p, h in self._get(name, "MX")), key=lambda r: (r.priority, r.host))

    def a(self, name):
        return self._get(name, "A")

    def aaaa(self, name):
        return self._get(name, "AAAA")

    def ptr(self, ip):
        return [norm(h) for h in self._get(ip, "PTR")]
