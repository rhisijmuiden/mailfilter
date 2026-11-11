"""Root-side helper for RHIS Mailfilter: version info, updates, branch switch, backup and restore.

The web app (running as the unprivileged ``mailfilter`` user) calls it through a
narrow sudoers rule. Long tasks run as background jobs under systemd so they
survive the web app being restarted halfway through an update.
"""
