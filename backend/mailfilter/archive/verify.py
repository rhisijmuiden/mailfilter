"""Check that the archive is exactly as it was written.

For every domain: each message's entry hash, the link to the message before it
(or the clean-up record / start of the chain), the file's SHA-256 (optional:
it reads every file), and every seal (hash, signature, link to the previous
seal, chain head and envelope digest while its messages are still there).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ArchiveDomain, ArchiveMessage, ArchivePurge, ArchiveSeal
from . import chain, store

MAX_PROBLEMS = 200


@dataclass
class Report:
    ok: bool = True
    started: str = ""
    finished: str = ""
    domains: int = 0
    messages: int = 0
    files: int = 0
    seals: int = 0
    problems: list[dict] = field(default_factory=list)

    def problem(self, domain: str, code: str, seq: int | None = None, detail: str = "") -> None:
        self.ok = False
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append({"domain": domain, "code": code, "seq": seq, "detail": detail})

    def as_dict(self) -> dict:
        return asdict(self)


def verify_message(m: ArchiveMessage, root: Path) -> str | None:
    """None when the message and its file are intact, otherwise a problem code."""
    if chain.entry_hash_of(m) != m.entry_hash:
        return "entry_hash"
    try:
        raw = store.read(root, m.path)
    except FileNotFoundError:
        return "file_missing"
    except Exception:
        return "file_unreadable"
    if store.sha256(raw) != m.sha256 or len(raw) != m.size:
        return "file_changed"
    return None


def verify(db: Session, domains: list[str] | None = None, *, files: bool = True, root: Path | None = None,
           progress=None) -> Report:
    root = root or store.root()
    rep = Report(started=datetime.now(timezone.utc).isoformat())
    keys = chain.public_keys(db)
    q = select(ArchiveDomain.name).order_by(ArchiveDomain.name)
    if domains is not None:
        q = q.where(ArchiveDomain.name.in_(domains))
    for domain in db.execute(q).scalars():
        rep.domains += 1
        dom = db.get(ArchiveDomain, domain)
        purges = {p.through_seq: p.last_hash for p in db.execute(select(ArchivePurge).where(ArchivePurge.domain == domain))
                  .scalars()}
        prev_seq, prev_hash = None, None
        hashes: dict[int, str] = {}
        envelopes: dict[int, list[str]] = {}
        last = None
        for m in db.execute(select(ArchiveMessage).where(ArchiveMessage.domain == domain)
                            .order_by(ArchiveMessage.seq)).scalars().yield_per(2000):
            rep.messages += 1
            if prev_seq is None:
                expected = chain.ZERO if m.seq == 1 else purges.get(m.seq - 1)
                if expected is None:
                    rep.problem(domain, "chain_gap", m.seq, "messages before this one are missing")
                elif m.prev_hash != expected:
                    rep.problem(domain, "chain_link", m.seq)
            elif m.seq != prev_seq + 1:
                rep.problem(domain, "chain_gap", m.seq, f"after #{prev_seq}")
            elif m.prev_hash != prev_hash:
                rep.problem(domain, "chain_link", m.seq)
            if chain.entry_hash_of(m) != m.entry_hash:
                rep.problem(domain, "entry_hash", m.seq)
            if files:
                code = verify_message(m, root)
                rep.files += 1
                if code and code != "entry_hash":
                    rep.problem(domain, code, m.seq, m.path)
            hashes[m.seq] = m.entry_hash
            envelopes[m.seq] = list(m.envelope_to)
            prev_seq, prev_hash, last = m.seq, m.entry_hash, m
            if progress and rep.messages % 5000 == 0:
                progress(rep)
        if last is not None and dom is not None and (dom.last_seq != last.seq or dom.head != last.entry_hash):
            rep.problem(domain, "chain_head", last.seq, "the newest message does not match the chain head")
        prev_seal = chain.ZERO
        for s in db.execute(select(ArchiveSeal).where(ArchiveSeal.domain == domain).order_by(ArchiveSeal.day)).scalars():
            rep.seals += 1
            day = s.day.isoformat()
            if s.prev_seal != prev_seal:
                rep.problem(domain, "seal_link", s.last_seq, day)
            if chain.seal_hash_of(s) != s.seal_hash:
                rep.problem(domain, "seal_hash", s.last_seq, day)
            pub = keys.get(s.key_id)
            if not pub or not chain.check_signature(pub, s.seal_hash, s.signature):
                rep.problem(domain, "seal_signature", s.last_seq, day)
            if s.last_seq in hashes and hashes[s.last_seq] != s.chain_head:
                rep.problem(domain, "seal_chain_head", s.last_seq, day)
            if all(q in envelopes for q in range(s.first_seq, s.last_seq + 1)):
                rows = [(q, envelopes[q]) for q in range(s.first_seq, s.last_seq + 1)]
                if chain.envelope_digest(rows) != s.envelope_digest:
                    rep.problem(domain, "seal_envelope", s.last_seq, day)
            prev_seal = s.seal_hash
    rep.finished = datetime.now(timezone.utc).isoformat()
    return rep
