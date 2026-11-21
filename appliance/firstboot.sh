#!/usr/bin/env bash
# First start of a container made from the Mailfilter Proxmox template.
# Run once by mailfilter-firstboot.service. Asks nothing: the network comes from
# Proxmox, the admin login is "admin" with a new password, and the web interface
# uses HTTPS with its own certificate. The console then shows only the address,
# user name and password; the setup wizard in the web interface does the rest.
#
# Optional, before the first start (e.g. with `pct push`): /etc/mailfilter/appliance.conf
#   WEB_PORT=8443        HTTPS port of the web interface (default 443)
#   ADMIN_LOGIN=admin    login of the first admin (default admin)
#
# Failed? Look in /var/log/mailfilter-install.log and run: systemctl restart mailfilter-firstboot
set -Eeuo pipefail
MF_SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ETC=/etc/mailfilter
CONF=$ETC/appliance.conf
issue() { bash "$MF_SRC/appliance/issue.sh" "$@" || true; }

issue preparing
echo "Mailfilter: first boot, preparing this server (takes a few minutes)"

# Settings from appliance.conf: read only the keys we know, never execute the file.
conf() { if [[ -f $CONF ]]; then sed -n "s/^$1=//p" "$CONF" | tail -1 | tr -d '"'"'"' \r'; fi; }
WEB_PORT=$(conf WEB_PORT); WEB_PORT=${WEB_PORT:-443}
ADMIN_LOGIN=$(conf ADMIN_LOGIN); ADMIN_LOGIN=${ADMIN_LOGIN:-admin}
[[ $WEB_PORT =~ ^[0-9]+$ ]] || WEB_PORT=443
LOGIN_RE='^[A-Za-z0-9._@+-]{1,320}$'
[[ $ADMIN_LOGIN =~ $LOGIN_RE ]] || ADMIN_LOGIN="admin"

# Wait for the network from Proxmox (static or DHCP), at most two minutes.
for _ in $(seq 60); do
  ip -4 route show default | grep -q . && break
  ip -6 route show default | grep -q . && break
  sleep 2
done

# The name from Proxmox; a short name gets a placeholder domain until the wizard sets the real one.
NAME=$(hostname -f 2>/dev/null || hostname)
NAME=${NAME,,}
[[ $NAME == *.* ]] || NAME="${NAME:-mailfilter}.localdomain"
[[ $NAME =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || NAME=mailfilter.localdomain

# CrowdSec: this server's own credentials (generalize.sh removed the shared ones)
if command -v cscli >/dev/null; then
  systemctl stop crowdsec crowdsec-firewall-bouncer 2>/dev/null || true
  cscli machines add --auto --force >/dev/null 2>&1 || echo "CrowdSec: could not register the local machine"
  cscli capi register >/dev/null 2>&1 || echo "CrowdSec: could not register with the CrowdSec network (no internet?)"
  BOUNCER=/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
  if [[ -f $BOUNCER ]]; then
    key=$(cscli bouncers add "firewall-$(date +%s)" -o raw 2>/dev/null || true)
    [[ -n $key ]] && sed -i "s|^api_key:.*|api_key: $key|" "$BOUNCER"
  fi
fi

ssh-keygen -A >/dev/null 2>&1 || true

if bash "$MF_SRC/install/install.sh" --non-interactive --hostname "$NAME" --admin-email "admin@$NAME" \
     --admin-login "$ADMIN_LOGIN" --web-mode internal --web-port "$WEB_PORT"; then
  rm -f "$ETC/appliance-firstboot-pending"
  touch "$ETC/setup-wizard-pending"
  issue
  echo "Mailfilter is ready."
else
  issue failed
  echo "Mailfilter: first boot setup failed, see /var/log/mailfilter-install.log" >&2
  exit 1
fi
