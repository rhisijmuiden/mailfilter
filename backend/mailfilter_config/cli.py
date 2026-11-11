"""mailfilter-config: apply domains, relay access and throttling to this server.

  mailfilter-config apply   [--state FILE] [--no-reload] [--json]
  mailfilter-config check   [--state FILE] [--json]
  mailfilter-config render  [--state FILE] --out DIR
  mailfilter-config seed    [--relay-net CIDR]... [--relay-port PORT]...
                            [--domain NAME --destination HOST[:PORT]] [--no-reload]

`seed` is what the installer calls: it starts gateway.json from the lists
the installer wrote (when there is no gateway.json yet), adds what was given
on the command line, and applies. The web app writes gateway.json from its
database and calls `apply`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict

from .apply import STATE_FILE, Applier, ApplyError, load_state
from .model import Destination, Domain, GatewayConfig, RelayNetwork, RelayPort
from .render import render, splice_master_cf
from .validate import ConfigError, validate

LEGACY_NETS = "/etc/mailfilter/relay_networks.list"
LEGACY_DOMAINS = "/etc/mailfilter/postfix/relay_domains"
LEGACY_TRANSPORT = "/etc/mailfilter/postfix/transport"


def parse_destination(text: str) -> Destination:
    text = text.strip()
    m = re.fullmatch(r"\[?([^\]\s]+?)\]?(?::(\d+))?", text)
    if not m:
        raise ValueError(f"Not a destination: {text}")
    host, port = m.group(1), int(m.group(2) or 25)
    # A bare IPv6 address contains colons: treat the whole thing as host.
    if text.count(":") > 1 and not text.startswith("["):
        host, port = text, 25
    return Destination(host=host, port=port, mx=False)


def import_legacy(root: str = "") -> GatewayConfig:
    """Build a configuration from the plain lists the installer writes."""
    cfg = GatewayConfig()

    def lines(p: str):
        try:
            with open(root + p, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.split("#", 1)[0].strip()
                    if ln:
                        yield ln
        except FileNotFoundError:
            return

    for ln in lines(LEGACY_NETS):
        cfg.relay_networks.append(RelayNetwork(cidr=ln.split()[0], description="From installer"))
    dests = {}
    for ln in lines(LEGACY_TRANSPORT):
        parts = ln.split(None, 1)
        if len(parts) == 2 and ":" in parts[1]:
            dests[parts[0]] = parse_destination(parts[1].split(":", 1)[1])
    for ln in lines(LEGACY_DOMAINS):
        name = ln.split()[0]
        if name in dests:
            cfg.domains.append(Domain(name=name, destinations=[dests[name]]))
    return cfg


def _print_problems(problems, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": False, "problems": [asdict(p) for p in problems]}, indent=2))
    else:
        for p in problems:
            print(f"✗ {p}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mailfilter-config", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "check", "render", "seed"):
        sp = sub.add_parser(name)
        sp.add_argument("--state", default=STATE_FILE)
        sp.add_argument("--json", action="store_true")
        if name in ("apply", "seed"):
            sp.add_argument("--no-reload", action="store_true", help="write and check, but do not reload services")
        if name == "render":
            sp.add_argument("--out", required=True)
        if name == "seed":
            sp.add_argument("--relay-net", action="append", default=[])
            sp.add_argument("--relay-port", action="append", type=int, default=[])
            sp.add_argument("--domain")
            sp.add_argument("--destination")
    args = ap.parse_args(argv)

    if args.cmd == "seed":
        cfg = load_state(args.state) if os.path.exists(args.state) else import_legacy()
        have_nets = {n.cidr for n in cfg.relay_networks}
        for n in args.relay_net:
            if n not in have_nets:
                cfg.relay_networks.append(RelayNetwork(cidr=n, description="From installer"))
        have_ports = {p.port for p in cfg.relay_ports}
        for p in args.relay_port:
            if p not in have_ports:
                cfg.relay_ports.append(RelayPort(port=p))
        if args.domain and args.domain.lower() not in {d.name for d in cfg.domains}:
            if not args.destination:
                ap.error("--domain needs --destination")
            cfg.domains.append(Domain(name=args.domain, destinations=[parse_destination(args.destination)]))
    else:
        cfg = load_state(args.state)

    if args.cmd == "check":
        problems = validate(cfg)
        if problems:
            _print_problems(problems, args.json)
            return 1
        print(json.dumps({"ok": True}) if args.json else "✓ Configuration is valid")
        return 0

    if args.cmd == "render":
        problems = validate(cfg)
        if problems:
            _print_problems(problems, args.json)
            return 1
        r = render(cfg)
        files = dict(r.files)
        files["/etc/postfix/master.cf (managed block)"] = splice_master_cf("", r.master_cf_block)
        files["/etc/postfix/main.cf (parameters)"] = "".join(f"{k} = {v}\n" for k, v in sorted(r.main_cf.items()))
        for p, content in files.items():
            dest = os.path.join(args.out, p.lstrip("/").replace(" ", "_").replace("(", "").replace(")", ""))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            print(dest)
        return 0

    applier = Applier(reload=not args.no_reload)
    try:
        report = applier.apply(cfg)
    except ConfigError as e:
        _print_problems(e.problems, args.json)
        return 1
    except ApplyError as e:
        if args.json:
            print(json.dumps({"ok": False, "step": e.step, "output": e.output}, indent=2))
        else:
            print(f"✗ {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, **asdict(report)}, indent=2))
    elif report.nothing_changed:
        print("✓ Nothing to change")
    else:
        print(f"✓ Applied ({len(report.changed)} file(s), {len(report.params_set) + len(report.params_removed)} setting(s))")
        for w in report.warnings:
            print(f"! {w}", file=sys.stderr)
    return 0
