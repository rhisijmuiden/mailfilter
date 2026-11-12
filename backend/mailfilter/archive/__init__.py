"""Mail archive: an unchangeable, searchable copy of all mail per domain.

- ingest: the mfarchive pipe stores each message (gzip file + database row in a hash chain)
- maintain: nightly envelope recipients, signed day seals, locking on disk, retention clean-up
- verify: proves the archive is exactly as written
- store / chain: files on disk, hashes and signatures
"""

from .ingest import usage_by_domain

__all__ = ["usage_by_domain"]
