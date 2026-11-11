"""Command line for the DNS toolbox: ``mailfilter-dns <command>``.

The web app calls the same functions directly; this CLI is for the systemd
timer (blacklist) and for the admin on the server. Output is JSON with
``--json``, otherwise short readable text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from . import blacklist, dkim, dmarc, domaincheck, golive, lookup, publish, spf
from .cloudflare import Cloudflare, CloudflareError
from .config import ETC, ServerConfig, read_env
from .resolver import DnsError, DnsResolver


def _token() -> str:
    return os.environ.get("CLOUDFLARE_API_TOKEN") or read_env(ETC / "secrets.env").get("CLOUDFLARE_API_TOKEN", "")


def _out(args, data, text: str | None = None) -> None:
    if args.json or text is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def _plan_text(p: publish.Plan) -> str:
    lines = [f"Zone {p.zone_name} ({p.domain})"]
    for c in p.changes:
        b = (c.before or {}).get("content")
        a = (c.after or {}).get("content")
        prio = (c.after or c.before or {}).get("priority")
        pre = f"{prio} " if prio is not None else ""
        lines.append(f"  {c.action:<7} {c.type:<4} {c.name}")
        if b is not None:
            lines.append(f"     now: {pre if c.type == 'MX' else ''}{b}")
        if a is not None and c.action != "none":
            lines.append(f"     new: {pre if c.type == 'MX' else ''}{a}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mailfilter-dns", description="Mailfilter DNS toolbox")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--nameserver", action="append", help="resolver to use (default: system, unbound on the server)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="check a domain's mail DNS for Mailfilter")
    p.add_argument("domain")
    p = sub.add_parser("mx", help="MX lookup for any domain")
    p.add_argument("domain")
    p.add_argument("--no-probe", action="store_true", help="skip the port 25 / STARTTLS test")

    p = sub.add_parser("spf-gen", help="generate an SPF record")
    p.add_argument("--include", action="append", default=[], help="domain, or m365 / google / mailfilter")
    p.add_argument("--ip4", action="append", default=[])
    p.add_argument("--ip6", action="append", default=[])
    p.add_argument("--mx", action="store_true")
    p.add_argument("--a", action="store_true")
    p.add_argument("--all", default="~", choices=["~", "-", "?"])
    p = sub.add_parser("spf-merge", help="add Mailfilter to an existing SPF value")
    p.add_argument("domain")
    p = sub.add_parser("dmarc-gen", help="generate a DMARC record")
    p.add_argument("--policy", default="none", choices=dmarc.POLICIES)
    p.add_argument("--sp", choices=dmarc.POLICIES)
    p.add_argument("--pct", type=int, default=100)
    p.add_argument("--rua", action="append")
    p.add_argument("--ruf", action="append")
    p.add_argument("--adkim", default="r", choices=dmarc.ALIGNMENT)
    p.add_argument("--aspf", default="r", choices=dmarc.ALIGNMENT)

    p = sub.add_parser("blacklist", help="check this server's addresses against blocklists")
    p.add_argument("--update", action="store_true", help=f"write {blacklist.STATUS_FILE}")
    p.add_argument("--ip", action="append", help="address to check (default: this server's)")

    sub.add_parser("cf-verify", help="check the Cloudflare API token")
    p = sub.add_parser("publish", help="publish a record to Cloudflare (shows the plan; --apply writes)")
    p.add_argument("what", choices=["spf", "dkim", "dmarc", "server-spf"])
    p.add_argument("domain", nargs="?")
    p.add_argument("--value", help="DMARC value, or full SPF value instead of merging")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("undo", help="undo a published batch from the journal")
    p.add_argument("batch")

    p = sub.add_parser("golive", help="one-time switch of a domain's MX to Mailfilter")
    p.add_argument("action", choices=["preflight", "plan", "apply", "rollback", "status"])
    p.add_argument("domain")
    p.add_argument("--force", action="store_true", help="apply even when blocking checks fail")

    args = ap.parse_args(argv)
    res = DnsResolver(args.nameserver)
    server = ServerConfig.load()
    actor = f"cli:{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'root'}"

    try:
        if args.cmd == "check":
            r = domaincheck.check_domain(args.domain, server, res)
            _out(args, r.to_dict(), "\n".join(f"{i.status:<8} {i.code:<8} {i.value}" + (f"  [{', '.join(i.problems)}]" if i.problems else "") + (f"\n         fix: {i.fix}" if i.fix else "") for i in r.items))
        elif args.cmd == "mx":
            r = lookup.mx_lookup(args.domain, res, probe=None if args.no_probe else lookup.default_probe)
            text = [f"{m.priority:>3} {m.host}  " + " ".join(a.ip for a in m.addresses) +
                    f"  smtp={m.smtp_ok} tls={m.starttls} ptr={m.ptr_ok}" for m in r.mx]
            text += [f"SPF:   {r.spf or '-'}", f"DMARC: {r.dmarc or '-'}"]
            text += [f"{n['level']}: {n['code']} {n['host']}" for n in r.notes]
            _out(args, r.to_dict(), "\n".join(text))
        elif args.cmd == "spf-gen":
            inc = [server.spf_name if i == "mailfilter" else i for i in args.include]
            v = spf.generate(includes=inc, ip4=args.ip4, ip6=args.ip6, mx=args.mx, a=args.a, all_qualifier=args.all)
            _out(args, {"value": v, "lookups": spf.count_lookups("example.invalid", res, v).lookups}, v)
        elif args.cmd == "spf-merge":
            cur, _ = spf.find_spf(res.txt(args.domain))
            v = spf.merge(cur, [server.spf_include])
            _out(args, {"current": cur, "new": v}, f"now: {cur or '-'}\nnew: {v}")
        elif args.cmd == "dmarc-gen":
            v = dmarc.generate(dmarc.DmarcOptions(args.policy, args.sp, args.pct, args.rua, args.ruf, args.adkim, args.aspf))
            _out(args, {"value": v}, v)
        elif args.cmd == "blacklist":
            ips = args.ip or server.addresses
            if not ips:
                print("No addresses: set MF_PUBLIC_V4 / MF_PUBLIC_V6 in /etc/mailfilter/mailfilter.env", file=sys.stderr)
                return 1
            data = blacklist.update(ips, res) if args.update else blacklist.check(ips, res, previous=blacklist.load())
            text = [f"{x['ip']} LISTED on {x['name']} ({x['zone']}) since {x['since']} - {x['delist']}" for x in data["listed"]]
            text += [f"unusable from this server: {z}" for z in data["unusable"]]
            _out(args, data, "\n".join(text) or "Not listed anywhere.")
            return 2 if data["listed"] else 0
        elif args.cmd == "cf-verify":
            cf = Cloudflare(_token())
            st = cf.verify_token()
            zones = [z.name for z in cf.zones()]
            _out(args, {"token": st, "zones": zones}, f"Token {st.get('status')}; zones: {', '.join(zones) or '-'}")
        elif args.cmd == "publish":
            cf = Cloudflare(_token())
            target = server.spf_name if args.what == "server-spf" else args.domain
            if not target:
                ap.error("domain required")
            zone = cf.zone_for(target)
            if zone is None:
                raise ValueError(f"no Cloudflare zone for {target}")
            plan = publish.Plan(zone.id, zone.name, target)
            if args.what == "spf":
                plan.changes.append(publish.plan_spf_value(cf, zone, target, args.value) if args.value
                                    else publish.plan_spf(cf, zone, target, [server.spf_include]))
            elif args.what == "server-spf":
                plan.changes.append(publish.plan_server_spf(cf, zone, server))
            elif args.what == "dkim":
                key = dkim.local_key(target)
                if key is None:
                    raise ValueError(f"no DKIM key for {target}")
                plan.changes.append(publish.plan_dkim(cf, zone, key))
            else:
                plan.changes.append(publish.plan_dmarc(cf, zone, target, args.value or dmarc.generate(dmarc.DmarcOptions())))
            print(_plan_text(plan)) if not args.json else None
            if args.apply and plan.needed:
                done = publish.apply(cf, plan, actor=actor)
                _out(args, done, f"Applied, batch {done[0]['batch']} (undo: mailfilter-dns undo {done[0]['batch']})")
            elif args.json:
                _out(args, plan.to_dict())
        elif args.cmd == "undo":
            done = publish.undo_batch(Cloudflare(_token()), args.batch, actor=actor)
            _out(args, done, f"Undone: {len(done)} change(s)")
        elif args.cmd == "golive":
            if args.action == "status":
                _out(args, golive.state(args.domain) or {"status": "not_live"})
                return 0
            if args.action == "preflight":
                pf = golive.preflight(args.domain, server, res)
                _out(args, pf.to_dict(), "\n".join(f"{'ok ' if c.ok else 'NO '} {c.code:<12} {c.detail}" + ("" if c.blocking else " (warning)") for c in pf.checks))
                return 0 if pf.ok else 1
            cf = Cloudflare(_token())
            if args.action == "rollback":
                st = golive.rollback(cf, args.domain, actor=actor)
                _out(args, st, f"Rolled back {args.domain}; the old MX records are back.")
                return 0
            plan = golive.plan(cf, args.domain, server)
            if args.action == "plan":
                _out(args, plan.to_dict(), _plan_text(plan))
                return 0
            pf = golive.preflight(args.domain, server, res)
            if not pf.ok and not args.force:
                _out(args, pf.to_dict(), "Pre-flight checks failed; fix them or use --force:\n" +
                     "\n".join(f"  {c.code}: {c.detail}" for c in pf.checks if c.blocking and not c.ok))
                return 1
            st = golive.go_live(cf, plan, actor=actor)
            _out(args, st, f"{args.domain} is live: MX now points to {server.hostname}")
    except (CloudflareError, DnsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
