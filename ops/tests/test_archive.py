"""Mail archive on disk: locking finished days and removing months whose retention is over."""
import datetime as dt

import pytest

from mailfilterctl import archive
from mailfilterctl.util import CtlError


@pytest.fixture
def arc(env, monkeypatch):
    monkeypatch.setenv("MF_NO_CHATTR", "1")
    base = env / "var/lib/mailfilter-archive"
    for day in ("2026-11-10", "2026-11-12"):
        d = base / "klant.nl/2033-11" / day
        d.mkdir(parents=True)
        (d / "0000000001-abc.eml.gz").write_bytes(b"x")
        (d / ".0000000002-def.eml.gz.99.tmp").write_bytes(b"half")
    old = base / "klant.nl/2025-09/2024-09-01"
    old.mkdir(parents=True)
    (old / "0000000001-aaa.eml.gz").write_bytes(b"y")
    return base


def test_lock_marks_finished_days_only(arc):
    res = archive.lock(dt.date(2026, 11, 12))
    assert res["locked"] == 2
    assert (arc / "klant.nl/2033-11/2026-11-10/.locked").exists()
    assert not (arc / "klant.nl/2033-11/2026-11-10/.0000000002-def.eml.gz.99.tmp").exists()
    assert not (arc / "klant.nl/2033-11/2026-11-12/.locked").exists()  # today stays open
    assert archive.lock(dt.date(2026, 11, 12))["locked"] == 0  # done before
    assert archive.lock(dt.date(2026, 11, 12), force=True)["locked"] == 2


def test_purge_only_after_retention(arc):
    today = dt.date(2026, 11, 12)
    with pytest.raises(CtlError, match="not over"):
        archive.purge("klant.nl", "2033-11", today)
    with pytest.raises(CtlError, match="not over"):
        archive.purge("klant.nl", "2026-11", today)
    with pytest.raises(CtlError, match="valid domain"):
        archive.purge("../etc", "2025-09", today)
    with pytest.raises(CtlError, match="valid month"):
        archive.purge("klant.nl", "2025-9", today)
    assert archive.purge("klant.nl", "2025-09", today)["removed"] == 1
    assert not (arc / "klant.nl/2025-09").exists() and (arc / "klant.nl/2033-11").exists()
    assert archive.purge("klant.nl", "2025-09", today)["removed"] == 0
