import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake server: file system root, /etc/mailfilter, state and backup folders under tmp_path."""
    root = tmp_path / "root"
    (root / "etc/mailfilter").mkdir(parents=True)
    (root / "var/lib/rspamd/dkim").mkdir(parents=True)
    monkeypatch.setenv("MF_ROOT", str(root))
    monkeypatch.setenv("MF_ETC", str(root / "etc/mailfilter"))
    monkeypatch.setenv("MF_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MF_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MF_BACKUP_SKIP", "database,redis")
    monkeypatch.setenv("MF_JOBS_INLINE", "1")
    monkeypatch.setenv("MF_RECONFIGURE_CMD", "true")
    monkeypatch.setenv("MF_NO_RECONFIGURE", "1")
    return root
