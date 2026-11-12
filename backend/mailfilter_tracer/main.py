"""Mailfilter log collector: follows the systemd journal and fills the mail trace database.

Run: python3 -m mailfilter_tracer            (follow the journal, as a service)
     python3 -m mailfilter_tracer --file F   (import an old mail.log once)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from .parser import parse_journal, parse_syslog
from .tracker import Tracker

log = logging.getLogger("mailfilter-tracer")
FLUSH_SECONDS = 2.0
FLUSH_EVENTS = 500
PRUNE_EVERY = 3600


def read_env(path: str) -> dict:
    env = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def database_url() -> str:
    if os.environ.get("MAILFILTER_DB"):
        return os.environ["MAILFILTER_DB"]
    secrets = read_env("/etc/mailfilter/secrets.env")
    return f"host=127.0.0.1 dbname=mailfilter user=mailfilter password={secrets.get('DB_PASSWORD', '')}"


class Collector:
    def __init__(self, store):
        self.store = store
        self.tracker = Tracker(loader=store.load)
        self.dirty: dict = {}
        self.pending = 0
        self.cursor: str | None = None

    def handle(self, ev, cursor=None) -> None:
        if cursor:
            self.cursor = cursor
        if ev is None:
            return
        msg = self.tracker.apply(ev)
        if msg is not None:
            self.dirty[msg.queue_id] = msg
            self.pending += 1

    def flush(self) -> None:
        if not self.dirty and not self.cursor:
            return
        msgs = list(self.dirty.values())
        self.store.save(msgs, self.cursor)
        for m in msgs:
            # Finished messages leave memory; deferred ones come back from the database when retried.
            if m.done or m.status == "deferred":
                self.tracker.forget(m.queue_id)
        self.dirty.clear()
        self.pending = 0


def follow(collector: Collector, keep_days: int) -> None:
    cmd = ["journalctl", "--follow", "--output=json", "--facility=mail", "--no-pager"]
    cursor = collector.store.cursor()
    cmd += [f"--after-cursor={cursor}"] if cursor else ["--since=-1d"]
    log.info("Reading the journal %s", "from the saved position" if cursor else "from the last 24 hours")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_flush = last_prune = time.monotonic()
    while not stop:
        ready, _, _ = select.select([proc.stdout], [], [], FLUSH_SECONDS)
        if ready:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                collector.handle(parse_journal(entry), entry.get("__CURSOR"))
            except Exception:  # one odd line must never stop the collector
                log.exception("Could not process: %s", entry.get("MESSAGE"))
        now = time.monotonic()
        if collector.pending >= FLUSH_EVENTS or now - last_flush >= FLUSH_SECONDS:
            collector.flush()
            last_flush = now
        if now - last_prune >= PRUNE_EVERY:
            removed = collector.store.prune(keep_days)
            if removed:
                log.info("Removed %d trace records older than %d days", removed, keep_days)
            last_prune = now
    collector.flush()
    proc.terminate()
    if not stop:
        sys.exit("journalctl stopped unexpectedly")


def import_file(collector: Collector, path: str, year: int | None) -> None:
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            collector.handle(parse_syslog(line, year))
            if collector.pending >= FLUSH_EVENTS:
                collector.flush()
    collector.cursor = None
    collector.flush()
    log.info("Imported %s (%d lines)", path, n)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mailfilter-tracer", description=__doc__.splitlines()[0])
    ap.add_argument("--file", help="import a classic mail.log instead of following the journal")
    ap.add_argument("--year", type=int, help="year for --file lines that have no year")
    ap.add_argument("--keep-days", type=int, default=int(os.environ.get("MAILFILTER_TRACE_DAYS", "90")),
                    help="how long to keep trace records (default 90)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from .store import Store
    store = Store(database_url())
    store.migrate()
    collector = Collector(store)
    if args.file:
        import_file(collector, args.file, args.year)
    else:
        follow(collector, args.keep_days)


if __name__ == "__main__":
    main()
