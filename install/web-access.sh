#!/usr/bin/env bash
# Change how the web interface is reached, without running the whole installer.
# Used by System > Web access (through mailfilterctl) and for emergencies on the server:
#
#   sudo /opt/mailfilter/install/web-access.sh --mode public     # back to HTTPS with Let's Encrypt
#   sudo /opt/mailfilter/install/web-access.sh --show
#
# Options are the installer's --web-* options; see install.sh --help.
# "--web-from -" and "--trusted-proxy -" clear the list.
# shellcheck disable=SC2034
set -Eeuo pipefail

MF_SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TPL=$MF_SRC/install/templates
# shellcheck source=lib/common.sh
source "$MF_SRC/install/lib/common.sh"
# shellcheck source=lib/web.sh
source "$MF_SRC/install/lib/web.sh"
trap 'die "Stopped at line $LINENO (see $MF_LOG)"' ERR

NONINTERACTIVE=1; WEB_MODE_WARNED=0
WEB_MODE=""; WEB_PORT=""; WEB_URL=""; TRUSTED_PROXIES=(); WEB_FROM=(); TRUSTED_PROXIES_SET=0; WEB_FROM_SET=0
SHOW=0; WEB_CERT_IN=""; WEB_KEY_IN=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --mode|--web-mode) WEB_MODE=$2; shift 2 ;;
    --web-port) WEB_PORT=$2; shift 2 ;;
    --web-url) WEB_URL=$2; shift 2 ;;
    --trusted-proxy) TRUSTED_PROXIES_SET=1; [[ $2 == - ]] || TRUSTED_PROXIES+=("$2"); shift 2 ;;
    --web-from) WEB_FROM_SET=1; [[ $2 == - ]] || WEB_FROM+=("$2"); shift 2 ;;
    --web-cert) WEB_CERT_IN=$2; shift 2 ;;
    --web-key) WEB_KEY_IN=$2; shift 2 ;;
    --show) SHOW=1; shift ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root (sudo)."
ENV_FILE=$MF_ETC/mailfilter.env
[[ -f $ENV_FILE ]] || die "Mailfilter is not installed ($ENV_FILE is missing)"
sv() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
HOSTNAME_FQDN=$(sv MF_HOSTNAME); ADMIN_EMAIL=$(sv MF_ADMIN_EMAIL)
PUBLIC_V4=$(sv MF_PUBLIC_V4); PUBLIC_V6=$(sv MF_PUBLIC_V6)
read -r -a SSH_FROM <<<"$(sv MF_SSH_FROM)"

if [[ $SHOW == 1 ]]; then
  load_saved_web; web_defaults >/dev/null 2>&1
  web_env_lines
  exit 0
fi

# A new mode starts from its own defaults; the same mode keeps what was saved.
OLD_MODE=$(sv MF_WEB_MODE)
if [[ -z $WEB_MODE || $WEB_MODE == "${OLD_MODE:-public}" ]]; then
  load_saved_web
fi
web_defaults
if [[ -n $WEB_CERT_IN || -n $WEB_KEY_IN ]]; then
  [[ $WEB_MODE == internal ]] || die "--web-cert and --web-key only work with --mode internal"
  [[ -s $WEB_CERT_IN && -s $WEB_KEY_IN ]] || die "Certificate or key file not found"
fi

step "Web access: $WEB_MODE"
while IFS='=' read -r k v; do set_env_key "$ENV_FILE" "$k" "$v"; done < <(web_env_lines)
ok "Saved in $ENV_FILE"

step "HTTPS certificate"
web_certificate
if [[ $WEB_MODE == public && $HAVE_CERT == 0 ]]; then
  die "No Let's Encrypt certificate for $HOSTNAME_FQDN, so the web interface cannot switch to public mode. Check that DNS points here and port 80 is reachable from the internet."
fi

step "Firewall"
configure_firewall
ok "Firewall updated"

step "Web server (Caddy)"
configure_web

# The web app reads its address and cookie setting at start.
systemctl restart mailfilter-web >>"$MF_LOG" 2>&1 || warn "mailfilter-web did not restart"
ok "Open the web interface at $WEB_URL"
