"""Command line for the PMG import.

  python3 -m mailfilter_pmg inspect pmg-backup.tgz [--json]   what is in the backup
  python3 -m mailfilter_pmg preview pmg-backup.tgz [--json]   what would change here
  sudo mailfilter-pmg-import apply pmg-backup.tgz [--sections lists,dkim] [--json]
  sudo mailfilter-pmg-import run-pending                        (the web interface's import job)

``apply`` without a selection file takes every item that is ticked by default
in the preview: everything new, nothing that already exists. It writes the
allow/block lists and DKIM keys. Domains, routes, relay networks and TLS
policies live in the web app's database, so import those in the web interface
(``--gateway-json`` writes them to gateway.json instead, for a server without it).
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


def _print_preview(pv) -> None:
    for it in pv.items:
        mark = "x" if it.selected else " "
        note = it.note_en or ""
        probs = "; ".join(p["en"] for p in it.problems)
        print(f"  [{mark}] {it.section:<15} {it.label:<40} {it.status:<11} {note} {probs}".rstrip())
    for p in pv.problems:
        print(f"  ! {p['en']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailfilter_pmg", description="Import from a Proxmox Mail Gateway backup.")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="show what is in the backup")
    inspect.add_argument("backup", help="pmg-backup_*.tgz file or unpacked backup directory")
    inspect.add_argument("--json", action="store_true", help="print the full import plan as JSON")
    inspect.add_argument("--include-secrets", action="store_true", help="include the DKIM private key in JSON")
    prev = sub.add_parser("preview", help="show what an import would change on this server")
    prev.add_argument("backup")
    prev.add_argument("--json", action="store_true")
    prev.add_argument("--root", default="", help=argparse.SUPPRESS)
    app = sub.add_parser("apply", help="import into this server (run as root)")
    app.add_argument("backup")
    app.add_argument("--selection", help="JSON file: {section: [item keys]}")
    app.add_argument("--sections", help="comma separated sections to import (default: all)")
    app.add_argument("--json", action="store_true")
    app.add_argument("--no-reload", action="store_true")
    app.add_argument(
        "--gateway-json", action="store_true",
        help="also write domains, routes, relay networks and TLS policies into gateway.json "
        "(only for a server without the web app; normally import those in the web interface)",
    )
    app.add_argument("--root", default="", help=argparse.SUPPRESS)
    sub.add_parser("run-pending", help="apply uploads queued by the web interface (systemd job)")
    args = parser.parse_args(argv)

    if args.command == "run-pending":
        from .staging import run_pending

        for import_id in run_pending():
            print(f"processed import {import_id}")
        return 0

    selection_file = getattr(args, "selection", None)
    try:
        plan = read_backup(args.backup)
    except (BackupError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command in ("preview", "apply"):
        from mailfilter_config.apply import STATE_FILE, Applier, ApplyError, load_state

        from .importer import GATEWAY_SECTIONS, SECTIONS, apply_gateway_file, apply_server_files, preview

        root = args.root.rstrip("/")
        pv = preview(plan, load_state(root + STATE_FILE), root=root)
        if args.command == "preview":
            if args.json:
                print(json.dumps(pv.to_dict(), indent=2))
            else:
                _print_preview(pv)
            return 0
        if selection_file:
            with open(selection_file, encoding="utf-8") as fh:
                selection = json.load(fh)
        else:
            wanted = set(args.sections.split(",")) if args.sections else set(SECTIONS)
            selection = {}
            for it in pv.items:
                if it.selected and it.section in wanted:
                    selection.setdefault(it.section, []).append(it.key)
        try:
            result = apply_server_files(plan, selection, root=root)
            if args.gateway_json and any(selection.get(x) for x in GATEWAY_SECTIONS):
                gw = apply_gateway_file(plan, selection, root=root, applier=Applier(root=root, reload=not args.no_reload))
                result.ok, result.problems = gw.ok, gw.problems
                result.imported.update(gw.imported)
                result.changed_files += gw.changed_files
        except ApplyError as exc:
            if args.json:
                print(json.dumps({"ok": False, "error": str(exc), "step": exc.step, "output": exc.output}))
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            from dataclasses import asdict

            print(json.dumps(asdict(result), indent=2))
        elif result.ok:
            print("Imported: " + ", ".join(f"{k} {v}" for k, v in result.imported.items()))
        else:
            for p in result.problems:
                print(f"! {p['field']}: {p['en']}", file=sys.stderr)
        return 0 if result.ok else 1

    if args.json:
        json.dump(plan.to_dict(include_secrets=args.include_secrets), sys.stdout, indent=2, default=str)
        print()
    else:
        _print_human(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
