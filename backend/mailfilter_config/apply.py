"""Writes a GatewayConfig to the server, checks it, and rolls back on failure.

Order: validate -> render -> back up -> write files -> postmap -> postconf ->
`postfix check` + `rspamadm configtest` -> reload. If a check fails, every
file and main.cf parameter is put back as it was and nothing is reloaded, so
a bad change never reaches the running mail flow.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .model import GatewayConfig
from .render import MANAGED_PARAM_PREFIXES, render, splice_master_cf
from .validate import check

STATE_FILE = "/etc/mailfilter/gateway.json"
MASTER_CF = "/etc/postfix/master.cf"
LOCK_FILE = "/run/mailfilter-config.lock"


class ApplyError(RuntimeError):
    def __init__(self, step: str, output: str):
        self.step = step
        self.output = output
        super().__init__(f"{step} failed, nothing was changed:\n{output}")


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def default_runner(cmd: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(cmd), 127, "", f"{cmd[0]}: command not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(cmd), 124, "", f"{cmd[0]}: timed out")


@dataclass
class Report:
    changed: list[str] = field(default_factory=list)
    params_set: dict[str, str] = field(default_factory=dict)
    params_removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reloaded: list[str] = field(default_factory=list)

    @property
    def nothing_changed(self) -> bool:
        return not (self.changed or self.params_set or self.params_removed)


class Applier:
    def __init__(self, runner: Runner = default_runner, root: str = "", reload: bool = True):
        self.run_cmd = runner
        self.root = root.rstrip("/")  # for tests: write below a temporary directory
        self.reload = reload

    # ---- helpers ---------------------------------------------------------------
    def path(self, p: str) -> str:
        return self.root + p

    def run(self, step: str, *cmd: str) -> str:
        res = self.run_cmd(cmd)
        if res.returncode != 0:
            raise ApplyError(step, (res.stdout or "") + (res.stderr or ""))
        return res.stdout or ""

    def read(self, p: str) -> str | None:
        try:
            with open(self.path(p), encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def write(self, p: str, content: str) -> None:
        dest = self.path(p)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            st = os.stat(dest)
            mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
        except FileNotFoundError:
            mode, uid, gid = 0o644, -1, -1
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".mfc-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(tmp, mode)
            if uid != -1 and os.geteuid() == 0:
                os.chown(tmp, uid, gid)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def remove(self, p: str) -> None:
        try:
            os.unlink(self.path(p))
        except FileNotFoundError:
            pass

    @contextmanager
    def lock(self):
        path = self.path(LOCK_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def managed_params(self) -> dict[str, str]:
        out = self.run("postconf", "postconf", "-n")
        params = {}
        for line in out.splitlines():
            name, _, value = line.partition(" = ")
            if name.startswith(MANAGED_PARAM_PREFIXES):
                params[name] = value
        return params

    def fill_server_facts(self, cfg: GatewayConfig) -> None:
        """Hostname and own IPs are needed to spot mail loops."""
        if not cfg.hostname:
            res = self.run_cmd(("postconf", "-h", "myhostname"))
            if res.returncode == 0:
                cfg.hostname = res.stdout.strip()
        if not cfg.own_addresses:
            res = self.run_cmd(("ip", "-j", "addr", "show"))
            if res.returncode == 0:
                try:
                    cfg.own_addresses = [a["local"] for i in json.loads(res.stdout)
                                         for a in i.get("addr_info", []) if a.get("local")]
                except (ValueError, KeyError, TypeError):
                    pass

    # ---- main entry --------------------------------------------------------------
    def apply(self, cfg: GatewayConfig, save_state: bool = True) -> Report:
        self.fill_server_facts(cfg)
        check(cfg)
        r = render(cfg)
        files = dict(r.files)
        files[MASTER_CF] = splice_master_cf(self.read(MASTER_CF) or "", r.master_cf_block)
        if save_state:
            files[STATE_FILE] = cfg.to_json()

        report = Report()
        with self.lock():
            baseline = self._postconf_warnings()
            old_files = {p: self.read(p) for p in files}
            old_params = self.managed_params()
            changed = [p for p, c in files.items() if old_files[p] != c]
            to_remove = sorted(set(old_params) - set(r.main_cf))
            to_set = {k: v for k, v in r.main_cf.items() if old_params.get(k) != v}
            if not changed and not to_remove and not to_set:
                return report

            try:
                for p in changed:
                    self.write(p, files[p])
                for m in r.hash_maps:
                    self.run("postmap", "postmap", f"hash:{self.path(m)}")
                if to_remove:
                    self.run("postconf", "postconf", "-X", *to_remove)
                if to_set:
                    self.run("postconf", "postconf", "-e", *(f"{k}={v}" for k, v in to_set.items()))
                self.run("postfix check", "postfix", "check")
                new_warnings = self._postconf_warnings() - baseline
                if new_warnings:
                    raise ApplyError("postconf", "\n".join(sorted(new_warnings)))
                self.run("rspamadm configtest", "rspamadm", "configtest")
            except Exception:
                self._rollback(old_files, old_params, r.hash_maps, set(to_set) | set(to_remove))
                raise

            report.changed, report.params_set, report.params_removed = changed, to_set, to_remove
            if self.reload:
                self._reload(r.relay_ports, changed, report)
        return report

    def _postconf_warnings(self) -> set[str]:
        res = self.run_cmd(("postconf", "-n"))
        return {ln for ln in (res.stderr or "").splitlines() if "warning" in ln.lower()}

    def _rollback(self, old_files, old_params, hash_maps, touched_params) -> None:
        for p, content in old_files.items():
            if content is None:
                self.remove(p)
            else:
                self.write(p, content)
        for m in hash_maps:
            if old_files.get(m) is not None:
                self.run_cmd(("postmap", f"hash:{self.path(m)}"))
        drop = [k for k in touched_params if k not in old_params]
        if drop:
            self.run_cmd(("postconf", "-X", *drop))
        restore = [f"{k}={v}" for k, v in old_params.items() if k in touched_params]
        if restore:
            self.run_cmd(("postconf", "-e", *restore))

    def _reload(self, ports: list[int], changed: list[str], report: Report) -> None:
        self.run("postfix reload", "postfix", "reload")
        report.reloaded.append("postfix")
        if any(p.startswith("/etc/rspamd/") for p in changed):
            # Map files (local_addrs) are picked up by Rspamd on its own; config needs a reload.
            self.run("rspamd reload", "systemctl", "reload", "rspamd")
            report.reloaded.append("rspamd")
        nft_file = "/etc/mailfilter/nftables/relay_ports.nft"
        if nft_file in changed:
            res = self.run_cmd(("nft", "list", "set", "inet", "mailfilter", "relay_ports"))
            if res.returncode == 0:
                self.run("firewall", "nft", "-f", self.path(nft_file))
                report.reloaded.append("nftables")
            elif ports:
                report.warnings.append(
                    "The firewall has no relay_ports set yet; re-run the installer to open "
                    + ", ".join(map(str, ports)) + ".")


def load_state(path: str = STATE_FILE) -> GatewayConfig:
    try:
        with open(path, encoding="utf-8") as fh:
            return GatewayConfig.from_json(fh.read())
    except FileNotFoundError:
        return GatewayConfig()
