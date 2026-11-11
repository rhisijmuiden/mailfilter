"""Import configuration from a Proxmox Mail Gateway (PMG) into Mailfilter.

The package has no dependencies outside the standard library, so it can read a
PMG backup on any machine. It works in two steps:

1. ``read_backup()`` turns a PMG backup (``pmg-backup_*.tgz``) into an
   :class:`ImportPlan`: a neutral description of everything we found, with
   warnings for the parts Mailfilter cannot take over one-to-one.
2. The web app shows that plan as a preview, the admin ticks what to take over,
   and the apply step writes it into Mailfilter's own tables.
"""

from .backup import BackupError, read_backup
from .model import ImportPlan

__all__ = ["BackupError", "ImportPlan", "read_backup"]
__version__ = "0.1.0"
