#!/usr/bin/env bash
# Run inside the build container after `install.sh --image` (see appliance/build.sh).
# Removes everything that must be unique per server, so every container made from the
# template gets its own keys and passwords at its first boot (appliance/firstboot.sh).
#
#   appliance/generalize.sh BRANCH COMMIT
set -Eeuo pipefail
BRANCH=${1:?branch}; COMMIT=${2:?commit}
MF_SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ETC=/etc/mailfilter

echo "Stopping services"
systemctl stop 'mailfilter-*.timer' 'mailfilter-*.service' caddy postfix rspamd dovecot crowdsec \
  crowdsec-firewall-bouncer clamav-daemon clamav-freshclam redis-server unbound 2>/dev/null || true
systemctl stop postgresql

echo "Secrets and keys"
# Database, Rspamd, session and link signing secrets: install.sh makes new ones at the first boot
rm -f "$ETC/secrets.env" "$ETC/link-protection.key" "$ETC/tracer.env" "$ETC/initial-admin-password" "$ETC/initial-admin-login"
rm -f "$ETC"/dkim-*.txt /var/lib/rspamd/dkim/*
rm -f /etc/ssh/ssh_host_*
# Caddy's own certificate authority (internal mode) must differ per server
rm -rf /var/lib/caddy/.local/share/caddy /var/lib/caddy/.config/caddy
rm -f /var/lib/redis/dump.rdb
rm -rf /var/lib/rspamd/*.sqlite /var/lib/rspamd/*.hs* /var/lib/rspamd/rspamd.history 2>/dev/null || true
# CrowdSec: own machine and bouncer credentials (re-registered at the first boot)
rm -f /var/lib/crowdsec/data/crowdsec.db
: >/etc/machine-id
rm -f /var/lib/dbus/machine-id

echo "State, logs and caches"
rm -rf /var/lib/mailfilterctl/* /var/backups/mailfilter/* /var/lib/mailfilter/pmg-import/*
rm -rf "$MF_SRC/frontend/node_modules" /root/.npm /root/.cache /root/.bash_history
rm -rf /var/lib/clamav/*.c[lv]d /var/lib/clamav/*.inc 2>/dev/null || true
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete
find /var/log -type f -exec truncate -s 0 {} +
rm -rf /var/log/journal/*
apt-get clean
rm -rf /var/lib/apt/lists/*
# Debian's Docker image tweaks (no man pages, auto-clean) do not belong in a server
rm -f /etc/apt/apt.conf.d/docker-* /etc/dpkg/dpkg.cfg.d/docker-*
rm -f /etc/.pve-ignore.*

echo "First boot"
install -m 0644 "$MF_SRC/appliance/mailfilter-firstboot.service" /etc/systemd/system/mailfilter-firstboot.service
install -d /etc/systemd/system/ssh.service.d
printf '[Service]\n# Proxmox normally makes host keys when it creates the container; this is the fallback.\nExecStartPre=-/usr/bin/ssh-keygen -A\n' \
  >/etc/systemd/system/ssh.service.d/mailfilter-hostkeys.conf
systemctl enable mailfilter-firstboot.service
printf 'branch=%s\ncommit=%s\nbuilt=%s\n' "$BRANCH" "$COMMIT" "$(date -Is)" >"$ETC/appliance"
touch "$ETC/appliance-firstboot-pending"
bash "$MF_SRC/appliance/issue.sh"
echo "Generalised."
