"""Command line: ``python3 -m mailfilter_pmg inspect pmg-backup_2026_11_11.tgz``.

Prints what would be imported, without changing anything. ``--json`` gives the
full plan (DKIM key hidden unless ``--include-secrets``).
"""

from __future__ import annotations

import argparse
import json
import sys

from .backup import BackupError, read_backup


def _print_human(plan) -> None:
    print(f"PMG backup: {plan.source_product} {plan.source_version}  ({plan.backup_time})")
    print()
    for key, value in plan.summary().items():
        print(f"  {key.replace('_', ' '):<18} {value}")
    print()
    print("Domains:")
    for d in plan.domains:
        t = d.transport
        dest = f"{t.protocol}:{'' if t.use_mx else '['}{t.host}{'' if t.use_mx else ']'}:{t.port}" if t else "(none)"
        extra = " dkim" if d.dkim_sign else ""
        print(f"  {d.name:<35} -> {dest}{extra}  {d.comment}")
    print("Relay networks (may send through Mailfilter):")
    for n in plan.relay_networks:
        print(f"  {n.cidr:<35} {n.comment}")
    if plan.rules:
        print("Rules:")
        for r in plan.rules:
            state = r.handled_by or "MANUAL CHECK"
            print(f"  [{'on ' if r.active else 'off'}] {r.priority:>3} {r.name:<40} {state}")
    if plan.warnings:
        print("Warnings:")
        for w in plan.warnings:
            print(f"  - {w.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailfilter_pmg", description="Read a Proxmox Mail Gateway backup.")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="show what would be imported")
    inspect.add_argument("backup", help="pmg-backup_*.tgz file or unpacked backup directory")
    inspect.add_argument("--json", action="store_true", help="print the full import plan as JSON")
    inspect.add_argument("--include-secrets", action="store_true", help="include the DKIM private key in JSON")
    args = parser.parse_args(argv)

    try:
        plan = read_backup(args.backup)
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(plan.to_dict(include_secrets=args.include_secrets), sys.stdout, indent=2, default=str)
        print()
    else:
        _print_human(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
