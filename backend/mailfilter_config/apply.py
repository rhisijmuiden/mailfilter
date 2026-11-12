"""Writes a GatewayConfig to the server, checks it, and rolls back on failure.

Order: validate -> render -> back up -> write files -> postmap -> postconf ->
`postfix check` + `rspamadm configtest` -> reload. If a check fails, every
file and main.cf parameter is put back as it was and nothing is reloaded, so
a bad change never reaches the running mail flow.
"""
from __future__ import annotations

import fcntl
import shutil
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from .model import GatewayConfig
from .render import (LDAP_DIR, DOVECOT_MAILTRAP, MAILTRAP_JSON, MAILTRAP_USERS, MANAGED_PARAM_PREFIXES, MANAGED_PARAMS, SMTP_USERS,
                     render, splice_master_cf, splice_users)
from .validate import check

STATE_FILE = "/etc/mailfilter/gateway.json"
DKIM_DIR = "/var/lib/rspamd/dkim"
DKIM_MAP = "/etc/mailfilter/rspamd/dkim_selectors.map"
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
            # Errors first: doveconf -n, for one, prints the whole configuration before them.
            raise ApplyError(step, ((res.stderr or "") + "\n" + (res.stdout or "")).strip())
        return res.stdout or ""

    def read(self, p: str) -> str | None:
        try:
            with open(self.path(p), encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def write(self, p: str, content: str, owner: tuple[str, str, int] | None = None) -> None:
        """Atomic write that keeps owner and mode; `owner` (user, group, mode) sets them
        before the file appears, for files with secrets in them."""
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
            if owner:
                _chown(tmp, *owner)
            else:
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
            if name.startswith(MANAGED_PARAM_PREFIXES) or name in MANAGED_PARAMS:
                params[name] = value
        return params

    def fill_server_facts(self, cfg: GatewayConfig) -> None:
        """Hostname and own IPs are needed to spot mail loops."""
        if not cfg.hostname:
            res = self.run_cmd(("postconf", "-h", "myhostname"))
            if res.returncode == 0:
                cfg.hostname = res.stdout.strip()
        # Always add this server's own addresses, also when the caller gave some.
        res = self.run_cmd(("ip", "-j", "addr", "show"))
        if res.returncode == 0:
            try:
                local = [a["local"] for i in json.loads(res.stdout)
                         for a in i.get("addr_info", []) if a.get("local")]
            except (ValueError, KeyError, TypeError):
                local = []
            cfg.own_addresses = list(dict.fromkeys([*cfg.own_addresses, *local]))

    # ---- main entry --------------------------------------------------------------
    def apply(self, cfg: GatewayConfig, save_state: bool = True) -> Report:
        self.fill_server_facts(cfg)
        check(cfg)
        r = render(cfg, _mail_ids())
        files = dict(r.files)
        files[MASTER_CF] = splice_master_cf(self.read(MASTER_CF) or "", r.master_cf_block)
        users_now = self.read(SMTP_USERS)
        if users_now is not None or r.users_block:
            files[SMTP_USERS] = splice_users(users_now or "", r.users_block)
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
                self._ensure_mailboxes(r.mailboxes)
                return report
            if cfg.mailtraps:
                self._check_mailtrap_support()
            if any(t.enabled for t in cfg.cloud_tenants):
                self._check_cloud_support()
            if r.secret_files:
                self._check_ldap_support()
            owners = {p: ("root", "postfix", 0o640) for p in r.secret_files}
            # Holds password hashes and LDAP passwords; the web app reads it once on first start.
            owners[STATE_FILE] = ("root", "mailfilter", 0o640)

            try:
                for p in changed:
                    self.write(p, files[p], owners.get(p))
                    if p in (SMTP_USERS, MAILTRAP_USERS):  # password hashes: only Dovecot may read them
                        _chown(self.path(p), "root", "dovecot", 0o640)
                    elif p == MAILTRAP_JSON:  # read by the mftrap pipe and the web app
                        _chown(self.path(p), "root", "mailfilter", 0o640)
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
                for p in r.secret_files:
                    self._check_ldap_table(p)
                if DOVECOT_MAILTRAP in changed:
                    self.run("doveconf", "doveconf", "-n")
            except Exception:
                self._rollback(old_files, old_params, r.hash_maps, set(to_set) | set(to_remove))
                raise

            report.changed, report.params_set, report.params_removed = changed, to_set, to_remove
            self._remove_stale_ldap_maps(set(r.secret_files), report)
            self._ensure_dkim_keys(cfg, report)
            self._ensure_mailboxes(r.mailboxes)
            if self.reload:
                self._reload([*r.relay_ports, *r.open_ports], changed, report)
        return report

    def _ensure_dkim_keys(self, cfg: GatewayConfig, report: Report) -> None:
        """Give every domain a DKIM key, like the installer does for the first one.

        Key: /var/lib/rspamd/dkim/<domain>.<selector>.key (owner _rspamd, 0640),
        selector in dkim_selectors.map, DNS record in /etc/mailfilter/dkim-<domain>.<selector>.txt
        (group mailfilter, for the DNS toolbox). Existing keys are never replaced;
        a failure is a warning, it does not stop mail routing."""
        current = self.read(DKIM_MAP) or ""
        have = {ln.split()[0] for ln in current.splitlines() if ln.strip() and not ln.startswith("#")}
        selector = f"mf{datetime.now(timezone.utc).year}"
        added = []
        for d in cfg.domains:
            if not d.enabled or d.name in have:
                continue
            key = f"{DKIM_DIR}/{d.name}.{selector}.key"
            txt = f"/etc/mailfilter/dkim-{d.name}.{selector}.txt"
            if not os.path.exists(self.path(key)):
                res = self.run_cmd(("rspamadm", "dkim_keygen", "-b", "2048", "-s", selector, "-d", d.name,
                                    "-k", self.path(key)))
                if res.returncode != 0:
                    report.warnings.append(f"DKIM key for {d.name} could not be created: {(res.stderr or '').strip()}")
                    continue
                self.write(txt, res.stdout or "")
                _chown(self.path(key), "_rspamd", "_rspamd", 0o640)
                _chown(self.path(txt), "root", "mailfilter", 0o640)
            added.append(f"{d.name} {selector}\n")
        if added:
            self.write(DKIM_MAP, current + ("" if not current or current.endswith("\n") else "\n") + "".join(added))
            report.changed.append(DKIM_MAP)

    def _check_mailtrap_support(self) -> None:
        """The Mailtrap checks live in main.cf/master.cf from the installer. Without
        them a Mailtrap login could relay like a normal SMTP account, so refuse."""
        res = self.run_cmd(("postconf", "-h", "smtpd_relay_restrictions"))
        if res.returncode == 0 and "mailtrap" not in (res.stdout or ""):
            raise ApplyError("mailtrap",
                             "This server's Postfix configuration has no Mailtrap checks yet. "
                             "Update Mailfilter (or run the installer again) and try again.")

    def _check_ldap_support(self) -> None:
        """Postfix reads LDAP tables through the postfix-ldap package (installed by the installer)."""
        res = self.run_cmd(("postconf", "-m"))
        if res.returncode == 0 and "ldap" not in (res.stdout or "").split():
            raise ApplyError("ldap",
                             "Postfix on this server cannot read LDAP yet (package postfix-ldap). "
                             "Update Mailfilter (or run the installer again) and try again.")

    def _check_ldap_table(self, path: str) -> None:
        """A table Postfix cannot read stops every SMTP session, so read it once first.
        The address is outside the table's domain: nothing is sent to the LDAP server."""
        res = self.run_cmd(("postmap", "-q", "mailfilter-check@invalid.invalid", f"ldap:{self.path(path)}"))
        err = "\n".join(ln for ln in (res.stderr or "").splitlines() if "fatal" in ln or "error" in ln.lower())
        if err:
            raise ApplyError("ldap", err)

    def _remove_stale_ldap_maps(self, keep: set[str], report: Report) -> None:
        """LDAP tables of domains that no longer use LDAP: Postfix no longer reads them,
        and they hold a password, so they go."""
        try:
            names = os.listdir(self.path(LDAP_DIR))
        except FileNotFoundError:
            return
        for n in names:
            p = f"{LDAP_DIR}/{n}"
            if n.endswith(".cf") and p not in keep:
                self.remove(p)
                report.changed.append(p)

    def _check_cloud_support(self) -> None:
        """Cloud mail tenants need the cloud_relay.cidr check in main.cf (from the installer)."""
        res = self.run_cmd(("postconf", "-h", "smtpd_relay_restrictions"))
        if res.returncode == 0 and "cloud_relay" not in (res.stdout or ""):
            raise ApplyError("cloud",
                             "This server's Postfix configuration has no Cloud mail checks yet. "
                             "Update Mailfilter (or run the installer again) and try again.")

    def _ensure_mailboxes(self, dirs: list[str]) -> None:
        """Maildir for every Mailtrap mailbox, owned by the mailfilter user (the mftrap
        pipe writes, the web app and Dovecot read as that user). Never removes one."""
        for d in dirs:
            for sub in ("cur", "new", "tmp"):
                path = self.path(f"{d}/Maildir/{sub}")
                if not os.path.isdir(path):
                    os.makedirs(path, exist_ok=True)
            for p in (d, f"{d}/Maildir", *(f"{d}/Maildir/{s}" for s in ("cur", "new", "tmp"))):
                _chown(self.path(p), "mailfilter", "mailfilter", 0o750)
        if dirs:
            _chown(self.path(os.path.dirname(dirs[0])), "mailfilter", "mailfilter", 0o750)

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
        if DOVECOT_MAILTRAP in changed:
            # Opening or closing port 993 needs a restart; SMTP logins pause for a moment.
            self.run("dovecot restart", "systemctl", "restart", "dovecot")
            report.reloaded.append("dovecot")
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


def _mail_ids() -> tuple[int, int]:
    """uid/gid of the mailfilter user, for Dovecot's IMAP userdb."""
    try:
        import pwd
        pw = pwd.getpwnam("mailfilter")
        return pw.pw_uid, pw.pw_gid
    except (KeyError, ImportError):
        return 0, 0


def _chown(path: str, user: str, group: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
        if os.geteuid() == 0:
            shutil.chown(path, user, group)
    except (OSError, LookupError):
        pass


def load_state(path: str = STATE_FILE) -> GatewayConfig:
    try:
        with open(path, encoding="utf-8") as fh:
            return GatewayConfig.from_json(fh.read())
    except FileNotFoundError:
        return GatewayConfig()
