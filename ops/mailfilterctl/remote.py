"""Copies of the local backups on another server, over FTP, FTPS or SCP (SFTP).

After every backup the new full or incremental step is uploaded; ``sync``
also catches up on steps that failed to upload earlier and removes old
chains remotely. Restoring from the remote location downloads the full
backup and the incrementals it needs into the local backup folder and then
runs the normal restore, so this also works on a freshly installed server.

Files are encrypted with AES-256 (GnuPG, symmetric) before they leave the
server, because a backup holds passwords and DKIM keys. Only manifest.json
(dates, sizes, checksums, version) is stored in plain text so the remote
backups can be listed without the passphrase.

Remote layout: <path>/<chain>/<NNN>/{files.tar.zst.gpg, database.dump.gpg,
redis.rdb.gpg, manifest.json}. manifest.json is uploaded last and marks a
complete step.

Settings: /etc/mailfilter/backup-remote.json (plain) and
/etc/mailfilter/backup-remote.secret.json (0600: FTP password and the
encryption passphrase). SCP uses the key pair in /etc/mailfilter/backup-ssh/.
"""
from __future__ import annotations

import ftplib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from . import backup, paths
from .util import CtlError, have, iso, now, read_json, run, sha256, write_json

PROTOCOLS = ("ftp", "ftps", "scp")
DEFAULT_PORTS = {"ftp": 21, "ftps": 21, "scp": 22}
DEFAULT = {
    "enabled": False,
    "protocol": "scp",
    "host": "",
    "port": 22,
    "username": "",
    "path": "mailfilter-backups",
    "verify_tls": True,
    "encrypt": True,
    "keep_full": 4,
}
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+$|^\[?[0-9A-Fa-f:]+\]?$|^[0-9.]+$")
PATH_RE = re.compile(r"^/?[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*/?$")
USER_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")
TIMEOUT = 30


# ---------------------------------------------------------------- settings

def _cfg_path() -> Path:
    return paths.etc() / "backup-remote.json"


def _secret_path() -> Path:
    return paths.etc() / "backup-remote.secret.json"


def ssh_dir() -> Path:
    return paths.etc() / "backup-ssh"


def config() -> dict:
    cfg = dict(DEFAULT)
    cfg.update(read_json(_cfg_path(), {}) or {})
    return cfg


def _secrets() -> dict:
    return read_json(_secret_path(), {}) or {}


def _write_secrets(data: dict) -> None:
    write_json(_secret_path(), data)
    os.chmod(_secret_path(), 0o600)


def public_key() -> str:
    pub = ssh_dir() / "id_ed25519.pub"
    return pub.read_text().strip() if pub.exists() else ""


def _ensure_ssh_key() -> None:
    d = ssh_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    if not (d / "id_ed25519").exists():
        if not have("ssh-keygen"):
            raise CtlError("ssh-keygen is missing; install openssh-client")
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"mailfilter-backup@{socket.getfqdn()}",
             "-f", d / "id_ed25519"])


def host_key_fingerprints() -> list[str]:
    kh = ssh_dir() / "known_hosts"
    if not kh.exists() or not kh.read_text().strip() or not have("ssh-keygen"):
        return []
    p = run(["ssh-keygen", "-l", "-f", kh], check=False)
    return [" ".join(line.split()[1:2] + line.split()[-1:]) for line in p.stdout.splitlines() if line.strip()]


def show(reveal: bool = False) -> dict:
    cfg = config()
    sec = _secrets()
    out = {**cfg, "has_password": bool(sec.get("password")), "has_passphrase": bool(sec.get("passphrase")),
           "public_key": public_key(), "host_keys": host_key_fingerprints(),
           "status": read_json(paths.state() / "remote-status.json")}
    if reveal:
        out["passphrase"] = sec.get("passphrase", "")
    return out


def set_config(data: dict) -> dict:
    """Change settings. ``password`` / ``passphrase``: None keeps, "" clears, a value replaces."""
    cfg = config()
    for key in DEFAULT:
        if key in data and data[key] is not None:
            cfg[key] = data[key]
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["verify_tls"] = bool(cfg["verify_tls"])
    cfg["encrypt"] = bool(cfg["encrypt"])
    if cfg["protocol"] not in PROTOCOLS and not (cfg["protocol"] == "file" and os.environ.get("MF_REMOTE_ALLOW_FILE")):
        raise CtlError("Choose FTP, FTPS or SCP")
    try:
        cfg["port"] = int(cfg["port"] or DEFAULT_PORTS.get(cfg["protocol"], 22))
        cfg["keep_full"] = int(cfg["keep_full"])
    except (TypeError, ValueError) as e:
        raise CtlError("Port and number of backups to keep must be numbers") from e
    if not 1 <= cfg["port"] <= 65535:
        raise CtlError("Port must be between 1 and 65535")
    if not 1 <= cfg["keep_full"] <= 100:
        raise CtlError("Keep between 1 and 100 full backups remotely")
    cfg["host"] = str(cfg["host"]).strip()
    cfg["username"] = str(cfg["username"]).strip()
    cfg["path"] = str(cfg["path"]).strip() or DEFAULT["path"]
    if cfg["protocol"] != "file":
        if cfg["enabled"] or cfg["host"]:
            if not HOST_RE.match(cfg["host"]):
                raise CtlError("Fill in a valid server name or IP address")
            if not USER_RE.match(cfg["username"]):
                raise CtlError("Fill in a valid username")
        if not PATH_RE.match(cfg["path"]) or ".." in cfg["path"].split("/"):
            raise CtlError("Folder may only contain letters, digits, dots, dashes, underscores and slashes")

    sec = _secrets()
    if data.get("password") is not None:
        sec["password"] = str(data["password"])
    if data.get("passphrase") is not None:
        if data["passphrase"] and len(str(data["passphrase"])) < 12:
            raise CtlError("The encryption passphrase must be at least 12 characters")
        sec["passphrase"] = str(data["passphrase"])
    if cfg["encrypt"] and not sec.get("passphrase"):
        sec["passphrase"] = secrets.token_urlsafe(24)
    if cfg["protocol"] == "scp":
        _ensure_ssh_key()
    old = config()
    if (old["host"], old["port"]) != (cfg["host"], cfg["port"]):
        forget_host_key()
    write_json(_cfg_path(), cfg)
    _write_secrets(sec)
    return show()


def forget_host_key() -> dict:
    kh = ssh_dir() / "known_hosts"
    kh.unlink(missing_ok=True)
    return {"message": "Host key forgotten; the next connection test stores the new one"}


# ---------------------------------------------------------------- transports

class Transport:
    """Minimal file operations on the remote location. Paths are relative to the base folder."""

    def __init__(self, cfg: dict, password: str):
        self.cfg = cfg
        self.password = password
        self.base = cfg["path"].rstrip("/")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def full(self, rel: str) -> str:
        return f"{self.base}/{rel}" if rel else self.base

    # implemented by subclasses
    def connect(self): ...
    def close(self): ...
    def makedirs(self, rel: str): ...
    def put(self, local: Path, rel: str): ...
    def get(self, rel: str, local: Path): ...
    def listdir(self, rel: str) -> list[str]: ...
    def rmtree(self, rel: str): ...


class FileTransport(Transport):
    """Local folder; only for tests (MF_REMOTE_ALLOW_FILE=1)."""

    def p(self, rel):
        return Path(self.full(rel))

    def makedirs(self, rel):
        self.p(rel).mkdir(parents=True, exist_ok=True)

    def put(self, local, rel):
        tmp = self.p(rel + ".part")
        shutil.copyfile(local, tmp)
        os.replace(tmp, self.p(rel))

    def get(self, rel, local):
        shutil.copyfile(self.p(rel), local)

    def listdir(self, rel):
        d = self.p(rel)
        return sorted(x.name for x in d.iterdir()) if d.is_dir() else []

    def rmtree(self, rel):
        shutil.rmtree(self.p(rel), ignore_errors=True)


class _LenientSSLSocket(ssl.SSLSocket):
    """FTPS data connection whose TLS shutdown may fail without failing the transfer.

    ftplib calls unwrap() after every transfer. When the server closes the data connection
    first (common for an empty listing, or right after a short file) OpenSSL can report
    SHUTDOWN_WHILE_IN_INIT or an unexpected EOF. The transfer itself is still confirmed by
    the server's 226 reply, and uploads are verified by checksum, so the shutdown is best effort.
    """

    def unwrap(self):
        try:
            return super().unwrap()
        except (ssl.SSLError, OSError):
            return None


class FtpTransport(Transport):
    def connect(self):
        c = self.cfg
        try:
            if c["protocol"] == "ftps":
                ctx = ssl.create_default_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                if not c["verify_tls"]:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                ctx.sslsocket_class = _LenientSSLSocket
                self.ftp = ftplib.FTP_TLS(context=ctx, timeout=TIMEOUT)
            else:
                self.ftp = ftplib.FTP(timeout=TIMEOUT)
            self.ftp.connect(c["host"].strip("[]"), c["port"])
            self.ftp.login(c["username"], self.password)
            if c["protocol"] == "ftps":
                self.ftp.prot_p()  # encrypt the data connections too
        except ftplib.error_perm as e:
            raise CtlError(f"The FTP server refused the login: {e}") from e
        except ssl.SSLCertVerificationError as e:
            raise CtlError(f"The server's certificate is not valid: {e.verify_message}") from e
        except (OSError, ftplib.Error, EOFError) as e:
            reason = str(e) or ("the server closed the connection" if isinstance(e, EOFError) else type(e).__name__)
            raise CtlError(f"Could not connect to {c['host']}:{c['port']}: {reason}") from e

    def close(self):
        try:
            self.ftp.quit()
        except Exception:  # noqa: BLE001 - closing a broken connection is best effort
            self.ftp.close()

    def makedirs(self, rel):
        path = ""
        for part in PurePosixPath(self.full(rel)).parts:
            path = part if not path else (path.rstrip("/") + "/" + part)
            try:
                self.ftp.mkd(path)
            except ftplib.error_perm:
                pass  # usually "already exists"

    def put(self, local, rel):
        target = self.full(rel)
        with open(local, "rb") as f:
            self.ftp.storbinary(f"STOR {target}.part", f)
        try:
            self.ftp.delete(target)
        except ftplib.error_perm:
            pass
        self.ftp.rename(f"{target}.part", target)

    def get(self, rel, local):
        with open(local, "wb") as f:
            self.ftp.retrbinary(f"RETR {self.full(rel)}", f.write)

    def listdir(self, rel):
        try:
            names = self.ftp.nlst(self.full(rel))
        except ftplib.error_perm:
            return []
        return sorted({PurePosixPath(n).name for n in names} - {".", ".."})

    def rmtree(self, rel):
        target = self.full(rel)
        try:
            self.ftp.delete(target)
            return
        except ftplib.error_perm:
            pass
        for name in self.listdir(rel):
            self.rmtree(f"{rel}/{name}")
        try:
            self.ftp.rmd(target)
        except ftplib.error_perm:
            pass


class SftpTransport(Transport):
    """SCP/SFTP with OpenSSH's sftp client, key authentication and a pinned host key."""

    def connect(self):
        if not have("sftp"):
            raise CtlError("sftp is missing; install openssh-client")
        kh = ssh_dir() / "known_hosts"
        if not kh.exists() or not kh.read_text().strip():
            raise CtlError("The server's host key is not known yet. Use Test connection first.")
        self._run(["pwd"])

    def _cmd(self):
        c = self.cfg
        host = f"[{c['host'].strip('[]')}]" if ":" in c["host"] else c["host"]
        return ["sftp", "-b", "-", "-i", str(ssh_dir() / "id_ed25519"), "-P", str(c["port"]),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={ssh_dir() / 'known_hosts'}", "-o", f"ConnectTimeout={TIMEOUT}",
                f"{c['username']}@{host}"]

    def _run(self, commands: list[str], check=True) -> list[str]:
        p = subprocess.run(self._cmd(), input="\n".join(commands) + "\n", capture_output=True, text=True,
                           timeout=3600)
        if check and p.returncode != 0:
            err = (p.stderr or p.stdout).strip().splitlines()
            msg = err[-1] if err else f"sftp exited with {p.returncode}"
            if "Permission denied" in (p.stderr or ""):
                msg = "The server refused the SSH key. Add the public key to ~/.ssh/authorized_keys on the server."
            elif "Host key verification failed" in (p.stderr or ""):
                msg = "The server's host key changed. If that is expected, forget the host key and test again."
            raise CtlError(f"SFTP failed: {msg}")
        return [line for line in p.stdout.splitlines() if not line.startswith("sftp>")]

    @staticmethod
    def q(path: str) -> str:
        return '"' + path.replace('"', "") + '"'

    def makedirs(self, rel):
        cmds, path = [], ""
        for part in PurePosixPath(self.full(rel)).parts:
            path = part if not path else (path.rstrip("/") + "/" + part)
            cmds.append(f"-mkdir {self.q(path)}")
        self._run(cmds)

    def put(self, local, rel):
        t = self.full(rel)
        self._run([f"put {self.q(str(local))} {self.q(t + '.part')}", f"-rm {self.q(t)}",
                   f"rename {self.q(t + '.part')} {self.q(t)}"])

    def get(self, rel, local):
        self._run([f"get {self.q(self.full(rel))} {self.q(str(local))}"])

    def listdir(self, rel):
        out = self._run([f"-ls -1a {self.q(self.full(rel))}"], check=False)
        return sorted({PurePosixPath(n.strip()).name for n in out if n.strip()} - {".", ".."})

    def rmtree(self, rel):
        for step in self.listdir(rel):
            files = self.listdir(f"{rel}/{step}")
            self._run([f"-rm {self.q(self.full(f'{rel}/{step}/{f}'))}" for f in files] +
                      [f"-rmdir {self.q(self.full(f'{rel}/{step}'))}"], check=False)
        self._run([f"-rm {self.q(self.full(f'{rel}/state'))}", f"-rmdir {self.q(self.full(rel))}"], check=False)


def transport(cfg: dict | None = None) -> Transport:
    cfg = cfg or config()
    pw = _secrets().get("password", "")
    if cfg["protocol"] in ("ftp", "ftps"):
        return FtpTransport(cfg, pw)
    if cfg["protocol"] == "scp":
        return SftpTransport(cfg, pw)
    if cfg["protocol"] == "file" and os.environ.get("MF_REMOTE_ALLOW_FILE"):
        return FileTransport(cfg, pw)
    raise CtlError("No remote location configured")


# ---------------------------------------------------------------- encryption

def _gpg(args: list, passphrase: str) -> None:
    if not have("gpg"):
        raise CtlError("gpg is missing; install gnupg")
    with tempfile.TemporaryDirectory(prefix="mf-gpg-") as home:
        os.chmod(home, 0o700)
        run(["gpg", "--homedir", home, "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback",
             "--passphrase-fd", "0", *args], input=passphrase, timeout=7200)


def encrypt(src: Path, dest: Path, passphrase: str) -> None:
    _gpg(["--symmetric", "--cipher-algo", "AES256", "--s2k-digest-algo", "SHA512", "--compress-algo", "none",
          "-o", dest, src], passphrase)


def decrypt(src: Path, dest: Path, passphrase: str) -> None:
    try:
        _gpg(["--decrypt", "-o", dest, src], passphrase)
    except CtlError as e:
        if "bad session key" in str(e).lower() or "decryption failed" in str(e).lower():
            raise CtlError("The encryption passphrase is not correct for this backup") from e
        raise


# ---------------------------------------------------------------- operations

NET_ERRORS = (OSError, ftplib.Error, EOFError, subprocess.SubprocessError)


def _net(fn):
    """Turn network and protocol errors into a CtlError the web interface can show."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except NET_ERRORS as e:
            raise CtlError(f"Remote location: {e}") from e
    return wrapper


def _status(**kw) -> None:
    write_json(paths.state() / "remote-status.json", {"at": iso(now()), **kw})


@_net
def test(log=print) -> dict:
    """Check the connection: host key (SCP), login, create the folder, write and remove a file."""
    cfg = config()
    if not cfg["host"] and cfg["protocol"] != "file":
        raise CtlError("Fill in the server first")
    learned = []
    if cfg["protocol"] == "scp":
        _ensure_ssh_key()
        kh = ssh_dir() / "known_hosts"
        if not kh.exists() or not kh.read_text().strip():
            if not have("ssh-keyscan"):
                raise CtlError("ssh-keyscan is missing; install openssh-client")
            p = run(["ssh-keyscan", "-T", "15", "-p", str(cfg["port"]), "-t", "ed25519,ecdsa,rsa",
                     cfg["host"].strip("[]")], check=False, timeout=60)
            keys = "\n".join(line for line in p.stdout.splitlines() if line and not line.startswith("#"))
            if not keys:
                raise CtlError(f"No SSH server answered on {cfg['host']}:{cfg['port']}")
            kh.write_text(keys + "\n")
            os.chmod(kh, 0o600)
            learned = host_key_fingerprints()
            log(f"Stored host key: {', '.join(learned)}")
    probe = f".mailfilter-test-{secrets.token_hex(4)}"
    with tempfile.NamedTemporaryFile() as f, transport(cfg) as t:
        f.write(b"Mailfilter connection test\n")
        f.flush()
        t.makedirs("")
        t.put(Path(f.name), probe)
        if probe not in t.listdir(""):
            raise CtlError("Uploading worked, but the file could not be found afterwards")
        t.rmtree(probe)
    msg = f"Connected to {cfg['host'] or cfg['path']}: login, upload and delete work"
    log(msg)
    return {"message": msg, "ok": True, "host_keys_learned": learned, "host_keys": host_key_fingerprints()}


@_net
def list_remote(t: Transport | None = None) -> list[dict]:
    """Complete remote backups (with manifest), newest first."""
    def _list(t: Transport):
        out = []
        for chain in t.listdir(""):
            if not backup.CHAIN_ID.match(chain):
                continue
            for step in t.listdir(chain):
                if not re.fullmatch(r"[0-9]{3}", step):
                    continue
                with tempfile.TemporaryDirectory() as d:
                    try:
                        t.get(f"{chain}/{step}/manifest.json", Path(d) / "m.json")
                        m = json.loads((Path(d) / "m.json").read_text())
                    except (CtlError, OSError, ValueError, ftplib.Error):
                        continue
                m["size"] = sum(f["size"] for f in m.get("remote_files", m["files"]).values())
                out.append(m)
        return sorted(out, key=lambda m: m["id"], reverse=True)

    if t is not None:
        return _list(t)
    with transport() as t2:
        return _list(t2)


def _upload_step(t: Transport, step_dir: Path, manifest: dict, log) -> dict:
    cfg = config()
    passphrase = _secrets().get("passphrase", "")
    rel = f"{manifest['chain']}/{manifest['step']:03d}"
    t.makedirs(rel)
    remote_files = {}
    with tempfile.TemporaryDirectory(prefix="mf-up-", dir=step_dir.parent) as tmp:
        for name in manifest["files"]:
            src = step_dir / name
            if cfg["encrypt"]:
                dest = Path(tmp) / (name + ".gpg")
                encrypt(src, dest, passphrase)
            else:
                dest = src
            log(f"Uploading {rel}/{dest.name} ({dest.stat().st_size} bytes)")
            t.put(dest, f"{rel}/{dest.name}")
            remote_files[dest.name] = {"sha256": sha256(dest), "size": dest.stat().st_size, "source": name}
        m = {**manifest, "remote_files": remote_files, "encrypted": cfg["encrypt"], "uploaded": iso(now())}
        mp = Path(tmp) / "manifest.json"
        mp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
        t.put(mp, f"{rel}/manifest.json")  # last: marks the step complete
    return m


def sync(log=print, step=None) -> dict:
    """Upload every local step that is not on the remote yet, then remove old chains remotely."""
    step = step or (lambda text, *_, **__: log(text))
    cfg = config()
    if not cfg["enabled"]:
        raise CtlError("The remote location is switched off")
    uploaded, removed = [], []
    try:
        with transport(cfg) as t:
            step(f"Connecting to {cfg['host'] or cfg['path']}", "remote_connect", host=cfg["host"] or cfg["path"])
            t.makedirs("")
            have_remote = {m["id"] for m in list_remote(t)}
            chains = backup._chains()[-cfg["keep_full"]:]
            for chain in chains:
                for sd in backup._steps(chain):
                    m = read_json(sd / "manifest.json")
                    if not m or m["id"] in have_remote:
                        continue
                    step(f"Uploading {m['id']}", "remote_upload", id=m["id"])
                    _upload_step(t, sd, m, log)
                    uploaded.append(m["id"])
            remote_chains = sorted({m["chain"] for m in list_remote(t)})
            newest_local = chains[-1].name if chains else None
            for chain in remote_chains[:-cfg["keep_full"]] if len(remote_chains) > cfg["keep_full"] else []:
                if chain == newest_local:
                    continue
                step(f"Removing old remote backup {chain}", "remote_prune", chain=chain)
                t.rmtree(chain)
                removed.append(chain)
    except CtlError as e:
        _status(ok=False, error=str(e))
        raise
    except NET_ERRORS as e:
        _status(ok=False, error=str(e))
        raise CtlError(f"Remote copy failed: {e}") from e
    _status(ok=True, uploaded=uploaded, removed=removed)
    msg = f"Remote copy up to date ({len(uploaded)} uploaded, {len(removed)} old removed)"
    log(msg)
    return {"message": msg, "uploaded": uploaded, "removed": removed}


def _check_remote_manifest(rm: dict, step_id: str, must_encrypt: bool) -> None:
    """A downloaded manifest decides file names on this server, and restoring runs
    as root: only accept the names Mailfilter itself writes. With encryption on,
    refuse unencrypted backups, so the remote server cannot hand us its own files."""
    try:
        ok = rm.get("id") == step_id and isinstance(rm.get("remote_files"), dict) and isinstance(rm.get("files"), dict)
        encrypted = bool(rm.get("encrypted"))
        for rname, meta in (rm.get("remote_files") or {}).items():
            src = meta.get("source")
            ok = ok and src in backup.DATA_FILES and rname == (src + ".gpg" if encrypted else src)
        ok = ok and all(name in backup.DATA_FILES for name in rm.get("files") or {})
    except (AttributeError, TypeError):
        ok = False
    if not ok:
        raise CtlError(f"The remote copy of {step_id} has an unexpected manifest; it will not be downloaded")
    if must_encrypt and not encrypted:
        raise CtlError(f"The remote copy of {step_id} is not encrypted while encryption is switched on; "
                       "it will not be downloaded")


def fetch(backup_id: str, log=print, step=None) -> None:
    """Download a remote backup and the steps it builds on into the local backup folder."""
    step = step or (lambda text, *_, **__: log(text))
    m = backup.BACKUP_ID.match(backup_id or "")
    if not m:
        raise CtlError(f"Not a valid backup id: {backup_id}")
    chain_id, number = m["chain"], int(m["step"])
    passphrase = _secrets().get("passphrase", "")
    loc = backup.location()
    loc.mkdir(parents=True, exist_ok=True)
    os.chmod(loc, 0o700)
    chain = loc / chain_id
    chain.mkdir(exist_ok=True)
    try:
        with transport() as t:
            for i in range(number + 1):
                sd = chain / f"{i:03d}"
                if (sd / "manifest.json").exists():
                    continue
                rel = f"{chain_id}/{i:03d}"
                step(f"Downloading {chain_id}.{i:03d}", "remote_download", id=f"{chain_id}.{i:03d}")
                work = Path(tempfile.mkdtemp(prefix=f".tmp-{i:03d}-", dir=chain))
                try:
                    t.get(f"{rel}/manifest.json", work / "manifest.json")
                    rm = json.loads((work / "manifest.json").read_text())
                    _check_remote_manifest(rm, f"{chain_id}.{i:03d}", must_encrypt=config()["encrypt"])
                    for rname, meta in rm["remote_files"].items():
                        t.get(f"{rel}/{rname}", work / rname)
                        if sha256(work / rname) != meta["sha256"]:
                            raise CtlError(f"{rel}/{rname} was damaged during the transfer")
                        if rm.get("encrypted"):
                            if not passphrase:
                                raise CtlError("Fill in the encryption passphrase of these backups first")
                            decrypt(work / rname, work / meta["source"], passphrase)
                            (work / rname).unlink()
                    local_m = {k: v for k, v in rm.items() if k not in ("remote_files", "encrypted", "uploaded")}
                    write_json(work / "manifest.json", local_m)
                    os.rename(work, sd)
                    os.chmod(sd, 0o700)
                except BaseException:
                    shutil.rmtree(work, ignore_errors=True)
                    raise
    except NET_ERRORS as e:
        raise CtlError(f"Download failed: {e}") from e


def restore_from_remote(backup_id: str, components=None, safety_backup: bool = True, log=print, step=None) -> dict:
    fetch(backup_id, log=log, step=step)
    return backup.restore(backup_id, components=components, safety_backup=safety_backup, log=log, step=step)
