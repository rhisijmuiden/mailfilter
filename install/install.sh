#!/usr/bin/env bash
# Variables like MGMT_V4_ELEMENTS are read by render() through {{NAME}} placeholders.
# shellcheck disable=SC2034
# RHIS Mailfilter installer for a bare Debian 13 (trixie) server.
#
#   git clone https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
#   sudo /opt/mailfilter/install/install.sh
#
# Run with --help for all options. Safe to run again: it re-applies the
# configuration and keeps existing keys, passwords and lists.
set -Eeuo pipefail

MF_SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TPL=$MF_SRC/install/templates
# shellcheck source=lib/common.sh
source "$MF_SRC/install/lib/common.sh"
# shellcheck source=lib/webapp.sh
source "$MF_SRC/install/lib/webapp.sh"

trap 'die "Installer stopped at line $LINENO (see $MF_LOG)"' ERR

usage() {
  cat <<'EOF'
Usage: install.sh [options]

  --hostname NAME         Full host name of this server, e.g. mailfilter01.rhis.nl
  --admin-email ADDRESS   Where alerts and Let's Encrypt notices go
  --domain DOMAIN         First domain to filter, e.g. rhis.nl (optional)
  --destination HOST      Mail server that receives clean mail for --domain, e.g. mail.rhis.nl
  --relay-net CIDR        Server or network allowed to relay out (IPv4 or IPv6). Repeatable
  --relay-port PORT       Extra relay port next to 587 and 465, e.g. 2525. Repeatable
  --ssh-from CIDR         Only allow SSH from this address or network. Repeatable.
                          Without it SSH stays open to the internet (CrowdSec still guards it)
  --skip-certificate      Do not request a Let's Encrypt certificate (DNS not ready yet)
  --non-interactive       Never ask questions; fail when a required value is missing
  --reconfigure           Re-apply everything with the settings saved by the previous
                          install, without questions. Used by updates from the web interface
  -h, --help              Show this help
EOF
}

RELAY_NETS=(); RELAY_PORTS=(); SSH_FROM=(); SKIP_CERT=0; NONINTERACTIVE=0; RECONFIGURE=0
HOSTNAME_FQDN=""; ADMIN_EMAIL=""; FIRST_DOMAIN=""; FIRST_DEST=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --hostname) HOSTNAME_FQDN=$2; shift 2 ;;
    --admin-email) ADMIN_EMAIL=$2; shift 2 ;;
    --domain) FIRST_DOMAIN=$2; shift 2 ;;
    --destination) FIRST_DEST=$2; shift 2 ;;
    --relay-net) RELAY_NETS+=("$2"); shift 2 ;;
    --relay-port) RELAY_PORTS+=("$2"); shift 2 ;;
    --ssh-from) SSH_FROM+=("$2"); shift 2 ;;
    --skip-certificate) SKIP_CERT=1; shift ;;
    --non-interactive) NONINTERACTIVE=1; shift ;;
    --reconfigure) RECONFIGURE=1; NONINTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $1" ;;
  esac
done

if [[ $RECONFIGURE == 1 ]]; then
  [[ -f $MF_ETC/mailfilter.env ]] || die "--reconfigure needs a previous install ($MF_ETC/mailfilter.env is missing)"
  # Read only the keys we need; never execute the file.
  saved() { sed -n "s/^$1=//p" "$MF_ETC/mailfilter.env" | tail -1; }
  [[ -n $HOSTNAME_FQDN ]] || HOSTNAME_FQDN=$(saved MF_HOSTNAME)
  [[ -n $ADMIN_EMAIL ]] || ADMIN_EMAIL=$(saved MF_ADMIN_EMAIL)
  # Relay networks, ports and domains live in gateway.json (mailfilter-config), so they are kept as they are
  [[ ${#SSH_FROM[@]} -gt 0 ]] || read -r -a SSH_FROM <<<"$(saved MF_SSH_FROM)"
fi

printf '\n%sRHIS Mailfilter%s installer\n' "$C_BOLD" "$C_RESET"
printf '%sInstalls an inbound and outbound mail filter gateway on this server.%s\n' "$C_DIM" "$C_RESET"

# ---------------------------------------------------------------------------
step "Checking this server"
[[ $EUID -eq 0 ]] || die "Run this installer as root (sudo)."
install -d -m 0751 "$MF_ETC"; : >>"$MF_LOG"; chmod 0600 "$MF_LOG"
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} == 13 ]] || die "This installer needs Debian 13 (trixie). Found: ${PRETTY_NAME:-unknown}."
ok "Debian 13 ($VERSION_CODENAME)"
for p in 25 465 587 80 443; do
  if ss -Hltn "sport = :$p" | grep . >/dev/null && ! systemctl is-active -q postfix caddy 2>/dev/null; then
    USER_OF_PORT=$(ss -Hltnp "sport = :$p" | awk 'NR==1 {print $NF}')
    if [[ $USER_OF_PORT == *exim* || $USER_OF_PORT == *sendmail* ]]; then
      info "Port $p is used by the default mail server ($USER_OF_PORT); Postfix replaces it."
    else
      warn "Port $p is already in use by another program: $USER_OF_PORT"
    fi
  fi
done
PUBLIC_V4=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}' | head -1 || true)
PUBLIC_V6=$(ip -6 route get 2606:4700:4700::1111 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}' | head -1 || true)
ok "IPv4: ${PUBLIC_V4:-none}   IPv6: ${PUBLIC_V6:-none}"
[[ -n $PUBLIC_V6 ]] || warn "No IPv6 address found. Mailfilter works without it, but IPv6 is recommended."

# ---------------------------------------------------------------------------
step "Basic settings"
ask HOSTNAME_FQDN "Host name of this server" "mailfilter01.$(hostname -d 2>/dev/null || echo example.nl)"
[[ $HOSTNAME_FQDN =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || die "Host name must be a full name like mailfilter01.rhis.nl"
ask ADMIN_EMAIL "Admin email address (alerts, certificate notices)" ""
[[ $ADMIN_EMAIL == *@*.* ]] || die "Admin email address is not valid: $ADMIN_EMAIL"
if [[ -z $FIRST_DOMAIN && $NONINTERACTIVE == 0 ]]; then
  ask FIRST_DOMAIN "First domain to filter (empty to add later in the web interface)" "-"
  [[ $FIRST_DOMAIN == "-" ]] && FIRST_DOMAIN=""
fi
if [[ -n $FIRST_DOMAIN ]]; then ask FIRST_DEST "Mail server that receives clean mail for $FIRST_DOMAIN" "mail.$FIRST_DOMAIN"; fi
for n in "${RELAY_NETS[@]}"; do is_ipv4 "$n" || is_ipv6 "$n" || die "Not a valid IP address or network: $n"; done
for p in "${RELAY_PORTS[@]}"; do
  [[ $p =~ ^[0-9]+$ && $p -ge 1 && $p -le 65535 ]] || die "Not a valid port: $p"
  [[ $p == 25 || $p == 465 || $p == 587 || $p == 80 || $p == 443 ]] && die "Port $p is already used by Mailfilter"
done
for n in "${SSH_FROM[@]}"; do is_ipv4 "$n" || is_ipv6 "$n" || die "Not a valid SSH source: $n"; done
ok "Host name: $HOSTNAME_FQDN"
ok "Admin: $ADMIN_EMAIL"
[[ -n $FIRST_DOMAIN ]] && ok "First domain: $FIRST_DOMAIN -> $FIRST_DEST"

cat >"$MF_ETC/mailfilter.env" <<EOF
# Written by the installer on $(date -I). Read by the Mailfilter services.
MF_HOSTNAME=$HOSTNAME_FQDN
MF_ADMIN_EMAIL=$ADMIN_EMAIL
MF_PUBLIC_V4=$PUBLIC_V4
MF_PUBLIC_V6=$PUBLIC_V6
MF_SOURCE=$MF_SRC
MF_SSH_FROM=${SSH_FROM[*]}
EOF
chmod 0640 "$MF_ETC/mailfilter.env"

# ---------------------------------------------------------------------------
step "Setting the host name"
hostnamectl set-hostname "$HOSTNAME_FQDN"
SHORT=${HOSTNAME_FQDN%%.*}
backup_file /etc/hosts
sed -i "/[[:space:]]$HOSTNAME_FQDN\([[:space:]]\|$\)/d" /etc/hosts
{ [[ -n $PUBLIC_V4 ]] && echo "$PUBLIC_V4 $HOSTNAME_FQDN $SHORT"; [[ -n $PUBLIC_V6 ]] && echo "$PUBLIC_V6 $HOSTNAME_FQDN $SHORT"; } >>/etc/hosts
echo "$HOSTNAME_FQDN" >/etc/mailname
ok "$(hostname -f)"

# ---------------------------------------------------------------------------
step "Installing packages (this takes a few minutes)"
export DEBIAN_FRONTEND=noninteractive
echo "postfix postfix/main_mailer_type select No configuration" | debconf-set-selections
echo "postfix postfix/mailname string $HOSTNAME_FQDN" | debconf-set-selections
run apt-get update
PKGS=(postfix postfix-lmdb rspamd redis-server clamav-daemon clamav-freshclam dovecot-core
      unbound dns-root-data caddy certbot nftables unattended-upgrades postgresql
      python3 python3-venv python3-psycopg python3-dnspython python3-httpx python3-cryptography
      nodejs npm sudo git zstd curl dnsutils ca-certificates jq crowdsec)
run apt-get install -y --no-install-recommends "${PKGS[@]}"
# Postfix replaces Debian's default Exim; clean up what is left of it
if dpkg -l 'exim4*' 2>/dev/null | grep '^[ri]i' >/dev/null; then run apt-get purge -y 'exim4*'; fi
if apt-cache show crowdsec-firewall-bouncer >/dev/null 2>&1; then
  run apt-get install -y --no-install-recommends crowdsec-firewall-bouncer
else
  warn "crowdsec-firewall-bouncer not in the Debian archive; CrowdSec detects attacks but cannot block them yet."
fi
ok "Packages installed"

# ---------------------------------------------------------------------------
step "Local DNS resolver (needed for blocklists)"
render "$TPL/unbound/mailfilter.conf" /etc/unbound/unbound.conf.d/mailfilter.conf
# DNSSEC trust anchor; unbound-anchor exits 1 when it (re)wrote the file, which is fine
install -d -o unbound -g unbound /var/lib/unbound
[[ -s /var/lib/unbound/root.key ]] || cp /usr/share/dns/root.key /var/lib/unbound/root.key
unbound-anchor -a /var/lib/unbound/root.key >>"$MF_LOG" 2>&1 || true
chown unbound:unbound /var/lib/unbound/root.key 2>/dev/null || true
run unbound-checkconf
run systemctl enable --now unbound
run systemctl restart unbound
if systemctl is-active -q systemd-resolved; then
  install -d /etc/systemd/resolved.conf.d
  printf '[Resolve]\nDNS=127.0.0.1 ::1\nDNSStubListener=no\n' >/etc/systemd/resolved.conf.d/mailfilter.conf
  run systemctl restart systemd-resolved
  ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
else
  backup_file /etc/resolv.conf
  printf '# Managed by Mailfilter\nnameserver 127.0.0.1\nnameserver ::1\noptions edns0 trust-ad\n' >/etc/resolv.conf
fi
dig +short +time=3 rhis.nl @127.0.0.1 >/dev/null || die "The local resolver does not answer. Check outbound DNS (port 53) from this server."
ok "unbound answers on 127.0.0.1 and ::1"

# ---------------------------------------------------------------------------
step "Mailfilter lists and maps"
install -d -m 0755 "$MF_ETC/postfix" "$MF_ETC/rspamd"
install -d -m 0750 -o root -g _rspamd /var/lib/rspamd/dkim
install -d -m 0755 "$MF_ETC/nftables"
# Domains, relay networks, extra relay ports and throttling are written by
# mailfilter-config (step "Domains, relay access and throttling" below, and the
# web interface). Here we only make sure every file exists so Postfix can start.
touch "$MF_ETC/relay_networks.list" "$MF_ETC/nftables/relay_ports.nft"
for f in relay_domains transport sender_block tls_policy recipient_verify; do touch "$MF_ETC/postfix/$f"; done
for f in relay_networks.cidr client_block.cidr postscreen_access.cidr; do touch "$MF_ETC/postfix/$f"; done
for f in allow_from allow_domain allow_ip allow_from_re block_from block_domain block_ip block_from_re antivirus_skip dkim_selectors local_addrs; do touch "$MF_ETC/rspamd/$f.map"; done
cp "$TPL/postfix/dnsbl_reply" "$MF_ETC/postfix/dnsbl_reply"
cp "$TPL/postfix/bounce.cf" "$MF_ETC/postfix/bounce.cf"
ok "Lists and maps in $MF_ETC"

# ---------------------------------------------------------------------------
step "Firewall (nftables, IPv4 and IPv6)"
MGMT_V4=(); MGMT_V6=()
for n in "${SSH_FROM[@]}"; do if is_ipv6 "$n"; then MGMT_V6+=("$n"); else MGMT_V4+=("$n"); fi; done
join() { local IFS=,; echo "$*"; }
MGMT_V4_ELEMENTS=""; MGMT_V6_ELEMENTS=""
[[ ${#MGMT_V4[@]} -gt 0 ]] && MGMT_V4_ELEMENTS="elements = { $(join "${MGMT_V4[@]}") }"
[[ ${#MGMT_V6[@]} -gt 0 ]] && MGMT_V6_ELEMENTS="elements = { $(join "${MGMT_V6[@]}") }"
if [[ ${#SSH_FROM[@]} -gt 0 ]]; then
  SSH_RULES=$'    ip saddr @mgmt_v4 tcp dport 22 accept comment "SSH from management"\n    ip6 saddr @mgmt_v6 tcp dport 22 accept comment "SSH from management"'
  # Never lock out the session running this installer
  if [[ -n ${SSH_CLIENT:-} ]]; then
    CUR=${SSH_CLIENT%% *}
    if is_ipv6 "$CUR"; then SSH_RULES+=$'\n'"    ip6 saddr $CUR tcp dport 22 accept comment \"Installer session\""
    else SSH_RULES+=$'\n'"    ip saddr $CUR tcp dport 22 accept comment \"Installer session\""; fi
  fi
else
  SSH_RULES='    tcp dport 22 accept comment "SSH (open, guarded by CrowdSec)"'
fi
render "$TPL/nftables/nftables.conf" /etc/nftables.conf 0755
run nft -c -f /etc/nftables.conf
run systemctl enable nftables
run systemctl restart nftables
ok "Open: 22, 25, 80, 443, 465, 587 (extra relay ports follow below)"

# ---------------------------------------------------------------------------
step "HTTPS certificate (Let's Encrypt)"
install -d -m 0755 /var/www/acme
if [[ $SKIP_CERT == 1 ]]; then
  warn "Skipped. Run the installer again without --skip-certificate once DNS points here."
elif [[ -f /etc/letsencrypt/live/$HOSTNAME_FQDN/fullchain.pem ]]; then
  ok "Certificate already present"
else
  RESOLVED=$(dig +short A "$HOSTNAME_FQDN" @127.0.0.1 | tail -1)
  [[ -n $PUBLIC_V4 && $RESOLVED != "$PUBLIC_V4" ]] && warn "$HOSTNAME_FQDN resolves to '${RESOLVED:-nothing}', this server is $PUBLIC_V4. The certificate request will likely fail."
  render "$TPL/caddy/Caddyfile.acme" /etc/caddy/Caddyfile
  run systemctl restart caddy
  run certbot certonly --webroot -w /var/www/acme -d "$HOSTNAME_FQDN" \
      --email "$ADMIN_EMAIL" --agree-tos --no-eff-email --non-interactive --key-type ecdsa --reuse-key
  ok "Certificate issued for $HOSTNAME_FQDN"
fi
# DANE: the TLSA record holds the hash of the key, so renewals must keep the same key
RENEWAL=/etc/letsencrypt/renewal/$HOSTNAME_FQDN.conf
if [[ -f $RENEWAL ]] && ! grep -q '^reuse_key *= *True' "$RENEWAL"; then
  sed -i '/^reuse_key/d; /^\[renewalparams\]/a reuse_key = True' "$RENEWAL"
  ok "Certificate renewals keep the same key (needed for DANE)"
fi
install -D -m 0755 "$TPL/certbot/deploy-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/mailfilter.sh
if [[ -d /etc/letsencrypt/live ]]; then /etc/letsencrypt/renewal-hooks/deploy/mailfilter.sh 2>/dev/null || true; fi
HAVE_CERT=0; [[ -f /etc/letsencrypt/live/$HOSTNAME_FQDN/fullchain.pem ]] && HAVE_CERT=1

# ---------------------------------------------------------------------------
step "Mail server (Postfix)"
backup_file /etc/postfix/main.cf; backup_file /etc/postfix/master.cf
HOSTNAME=$HOSTNAME_FQDN
render "$TPL/postfix/main.cf" /etc/postfix/main.cf
render "$TPL/postfix/master.cf" /etc/postfix/master.cf
if [[ $HAVE_CERT == 0 ]]; then
  # No certificate yet: use the snakeoil certificate so Postfix can still start.
  run apt-get install -y ssl-cert
  postconf -e "smtpd_tls_chain_files = /etc/ssl/private/ssl-cert-snakeoil.key, /etc/ssl/certs/ssl-cert-snakeoil.pem"
  warn "Postfix uses a temporary self-signed certificate until Let's Encrypt succeeds."
fi
for f in relay_domains transport sender_block tls_policy recipient_verify; do run postmap "hash:$MF_ETC/postfix/$f"; done
# A bare install with "No configuration" has no /etc/aliases; route system mail to the admin.
[[ -f /etc/aliases ]] || printf 'postmaster: root\nabuse: root\nroot: %s\n' "$ADMIN_EMAIL" >/etc/aliases
run newaliases
run postfix check
run systemctl enable postfix
run systemctl restart postfix
ok "Listening on 25 (inbound), 587 and 465 (relay)"

# ---------------------------------------------------------------------------
step "SMTP accounts for relay (Dovecot)"
touch "$MF_ETC/smtp-users"; chown root:dovecot "$MF_ETC/smtp-users"; chmod 0640 "$MF_ETC/smtp-users"
backup_file /etc/dovecot/dovecot.conf
render "$TPL/dovecot/dovecot.conf" /etc/dovecot/dovecot.conf
run doveconf -n
run systemctl enable dovecot
run systemctl restart dovecot
ok "Password check for SMTP accounts is ready"

# ---------------------------------------------------------------------------
step "Virus scanner (ClamAV)"
run systemctl enable --now clamav-freshclam
if [[ ! -s /var/lib/clamav/main.cvd && ! -s /var/lib/clamav/main.cld ]]; then
  info "Downloading virus definitions"
  systemctl stop clamav-freshclam; run freshclam --quiet; systemctl start clamav-freshclam
fi
run systemctl enable clamav-daemon
run systemctl restart clamav-daemon
ok "ClamAV running"

# ---------------------------------------------------------------------------
step "Spam filter (Rspamd)"
run systemctl enable --now redis-server
SECRETS=$MF_ETC/secrets.env
touch "$SECRETS"; chmod 0600 "$SECRETS"
# shellcheck disable=SC1090
source "$SECRETS"
if [[ -z ${RSPAMD_PASSWORD:-} ]]; then RSPAMD_PASSWORD=$(gen_secret 32); echo "RSPAMD_PASSWORD=$RSPAMD_PASSWORD" >>"$SECRETS"; fi
RSPAMD_PASSWORD_HASH=$(rspamadm pw -q -p "$RSPAMD_PASSWORD")
for f in "$TPL"/rspamd/local.d/*; do
  name=$(basename "$f")
  render "$f" "/etc/rspamd/local.d/$name" 0640
  chgrp _rspamd "/etc/rspamd/local.d/$name"
done
touch "$MF_ETC/rspamd/ratelimit_overrides.conf"
if [[ -n $FIRST_DOMAIN ]]; then
  SEL=mf$(date +%Y)
  if [[ ! -f /var/lib/rspamd/dkim/$FIRST_DOMAIN.$SEL.key ]]; then
    rspamadm dkim_keygen -b 2048 -s "$SEL" -d "$FIRST_DOMAIN" -k "/var/lib/rspamd/dkim/$FIRST_DOMAIN.$SEL.key" >"$MF_ETC/dkim-$FIRST_DOMAIN.$SEL.txt"
    chown _rspamd:_rspamd "/var/lib/rspamd/dkim/$FIRST_DOMAIN.$SEL.key"; chmod 0640 "/var/lib/rspamd/dkim/$FIRST_DOMAIN.$SEL.key"
  fi
  grep -q "^$FIRST_DOMAIN " "$MF_ETC/rspamd/dkim_selectors.map" || echo "$FIRST_DOMAIN $SEL" >>"$MF_ETC/rspamd/dkim_selectors.map"
fi
run rspamadm configtest
run systemctl enable rspamd
run systemctl restart rspamd
ok "Rspamd running as milter on 127.0.0.1:11332"

# ---------------------------------------------------------------------------
step "Database (PostgreSQL)"
run systemctl enable --now postgresql
if [[ -z ${DB_PASSWORD:-} ]]; then DB_PASSWORD=$(gen_secret 32); echo "DB_PASSWORD=$DB_PASSWORD" >>"$SECRETS"; fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='mailfilter'" | grep 1 >/dev/null; then
  runuser -u postgres -- psql -q -c "CREATE ROLE mailfilter LOGIN PASSWORD '$DB_PASSWORD'"
else
  # Keeps the role in step with secrets.env, also after restoring a backup onto a new server
  runuser -u postgres -- psql -q -c "ALTER ROLE mailfilter LOGIN PASSWORD '$DB_PASSWORD'"
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='mailfilter'" | grep 1 >/dev/null; then
  runuser -u postgres -- createdb -O mailfilter mailfilter
fi
ok "Database 'mailfilter' ready"

# ---------------------------------------------------------------------------
step "Web app"
WEB_ADMIN_PASSWORD=""
install_webapp

# ---------------------------------------------------------------------------
step "Mail trace log collector"
install -m 0600 /dev/null "$MF_ETC/tracer.env"
printf 'MAILFILTER_DB=host=127.0.0.1 dbname=mailfilter user=mailfilter password=%s\n' "$DB_PASSWORD" >"$MF_ETC/tracer.env"
render "$TPL/systemd/mailfilter-tracer.service" /etc/systemd/system/mailfilter-tracer.service
run systemctl daemon-reload
run systemctl enable mailfilter-tracer
run systemctl restart mailfilter-tracer
ok "Postfix and Rspamd logs flow into the mail trace"

# ---------------------------------------------------------------------------
step "Domains, relay access and throttling"
# Starts /etc/mailfilter/gateway.json on the first run (taking over lists from older
# installs), adds what was given on the command line, and applies it. On a re-run it
# puts back what the templates above overwrote (extra listeners, per-domain queues).
SEED_ARGS=()
for n in "${RELAY_NETS[@]}"; do SEED_ARGS+=(--relay-net "$n"); done
for p in "${RELAY_PORTS[@]}"; do SEED_ARGS+=(--relay-port "$p"); done
[[ -n $FIRST_DOMAIN ]] && SEED_ARGS+=(--domain "$FIRST_DOMAIN" --destination "$FIRST_DEST")
run "$MF_SRC/backend/bin/mailfilter-config" seed "${SEED_ARGS[@]}"
ok "$(jq '.relay_networks | length' "$MF_ETC/gateway.json" 2>/dev/null || echo 0) relay network(s), $(jq '.domains | length' "$MF_ETC/gateway.json" 2>/dev/null || echo 0) domain(s), extra relay ports: $(jq -r '[.relay_ports[].port] | join(", ")' "$MF_ETC/gateway.json" 2>/dev/null || true)"
# The web app (user mailfilter) applies changes through this one command as root, nothing else.
# It goes through systemd-run so it runs outside the web app's sandbox (read-only /etc).
# sudo only stays safe if the web app cannot change the code it runs, so keep it root-owned.
chown -R root:root "$MF_SRC/backend/bin" "$MF_SRC/backend/mailfilter_config"
chmod -R go-w "$MF_SRC/backend/bin" "$MF_SRC/backend/mailfilter_config"
SUDOERS_TMP=$(mktemp)
# Second line: a cluster node exports and imports DKIM keys, so all nodes sign with the same key.
MFC_BIN=$(readlink -f "$MF_SRC/backend/bin/mailfilter-config")
printf '# Managed by Mailfilter\nmailfilter ALL=(root) NOPASSWD: /usr/bin/systemd-run --quiet --pipe --wait --collect --service-type=exec %s apply --stdin --json\nmailfilter ALL=(root) NOPASSWD: /usr/bin/systemd-run --quiet --pipe --wait --collect --service-type=exec %s dkim --stdin --json\n' \
  "$MFC_BIN" "$MFC_BIN" >"$SUDOERS_TMP"
run visudo -cf "$SUDOERS_TMP"
install -m 0440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/mailfilter-config
rm -f "$SUDOERS_TMP"

# ---------------------------------------------------------------------------
step "Import from Proxmox Mail Gateway"
# The web app (sandboxed, /etc read-only) queues uploads here; a root oneshot
# applies them. The web app may start exactly that one unit through sudo.
install -d -o mailfilter -g mailfilter -m 0750 /var/lib/mailfilter/pmg-import
render "$TPL/systemd/mailfilter-pmg-import.service" /etc/systemd/system/mailfilter-pmg-import.service
run systemctl daemon-reload
install -m 0440 "$TPL/sudoers-pmg-import" /etc/sudoers.d/mailfilter-pmg-import
visudo -cqf /etc/sudoers.d/mailfilter-pmg-import || { rm -f /etc/sudoers.d/mailfilter-pmg-import; die "sudoers rule for the PMG import is invalid"; }
ok "PMG backups can be imported in the web interface (or: $MF_SRC/backend/bin/mailfilter-pmg-import)"

# ---------------------------------------------------------------------------
step "Intrusion protection (CrowdSec)"
install -D -m 0644 "$TPL/crowdsec/acquis-mailfilter.yaml" /etc/crowdsec/acquis.d/mailfilter.yaml
for c in crowdsecurity/linux crowdsecurity/sshd crowdsecurity/postfix crowdsecurity/dovecot; do
  cscli collections install "$c" >>"$MF_LOG" 2>&1 || warn "Could not install CrowdSec collection $c"
done
run systemctl enable crowdsec
run systemctl restart crowdsec
if systemctl list-unit-files crowdsec-firewall-bouncer.service >/dev/null 2>&1; then
  systemctl enable --now crowdsec-firewall-bouncer >>"$MF_LOG" 2>&1 || warn "CrowdSec bouncer did not start"
fi
ok "CrowdSec watching SSH, Postfix and Dovecot"

# ---------------------------------------------------------------------------
step "Automatic security updates"
install -m 0644 "$TPL/apt/20auto-upgrades" /etc/apt/apt.conf.d/20auto-upgrades
install -m 0644 "$TPL/apt/52mailfilter-unattended" /etc/apt/apt.conf.d/52mailfilter-unattended
ok "Debian security updates install every night"

# ---------------------------------------------------------------------------
step "Web interface (Caddy)"
if [[ $HAVE_CERT == 1 ]]; then
  render "$TPL/caddy/Caddyfile" /etc/caddy/Caddyfile
  run caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  run systemctl enable caddy
  run systemctl restart caddy
  ok "https://$HOSTNAME_FQDN"
else
  warn "No certificate yet, so the web interface is not published. Re-run the installer after DNS is in place."
fi

# ---------------------------------------------------------------------------
step "Updates and backups"
render "$TPL/mailfilterctl.wrapper" /usr/local/sbin/mailfilterctl 0755
install -d -m 0700 /var/lib/mailfilterctl /var/backups/mailfilter
install -m 0644 "$TPL/systemd/mailfilter-backup.service" /etc/systemd/system/mailfilter-backup.service
install -m 0644 "$TPL/systemd/mailfilter-backup.timer" /etc/systemd/system/mailfilter-backup.timer
install -m 0440 "$TPL/sudoers-mailfilterctl" /etc/sudoers.d/mailfilterctl
visudo -cqf /etc/sudoers.d/mailfilterctl || { rm -f /etc/sudoers.d/mailfilterctl; die "sudoers rule for mailfilterctl is invalid"; }
run systemctl daemon-reload
# Writes the timer schedule from $MF_ETC/backup.json (created with defaults on first run)
run /usr/local/sbin/mailfilterctl backup schedule
head -1 "$MF_SRC/VERSION" >"$MF_ETC/version" 2>/dev/null || echo "0.0.0" >"$MF_ETC/version"
git -c safe.directory="$MF_SRC" -C "$MF_SRC" rev-parse --abbrev-ref HEAD 2>/dev/null >"$MF_ETC/branch" || echo "test" >"$MF_ETC/branch"
ok "Version $(cat "$MF_ETC/version") ($(cat "$MF_ETC/branch")), daily backup at $(jq -r .time "$MF_ETC/backup.json") to /var/backups/mailfilter"

# ---------------------------------------------------------------------------
step "Blocklist monitoring"
install -d -m 0755 /var/lib/mailfilter
for u in mailfilter-blacklist.service mailfilter-blacklist.timer; do
  render "$TPL/systemd/$u" "/etc/systemd/system/$u" 0644
done
run systemctl daemon-reload
run systemctl enable --now mailfilter-blacklist.timer
# First check right away, so the dashboard has a status from the start
systemctl start --no-block mailfilter-blacklist.service >>"$MF_LOG" 2>&1 || true
ok "Own IPv4 and IPv6 addresses are checked against the blocklists every 15 minutes"

# ---------------------------------------------------------------------------
step "Self-test"
FAILED=0
for s in unbound postfix rspamd clamav-daemon redis-server dovecot postgresql nftables crowdsec mailfilter-tracer mailfilter-web mailfilter-backup.timer mailfilter-abuse.timer mailfilter-cluster.timer; do
  if systemctl is-active -q "$s"; then ok "$s"; else warn "$s is not running"; FAILED=1; fi
done
for p in 25 465 587 "${RELAY_PORTS[@]}"; do
  if ss -Hltn "sport = :$p" | grep . >/dev/null; then ok "Port $p listening"; else warn "Nothing listens on port $p"; FAILED=1; fi
done

# ---------------------------------------------------------------------------
printf '\n%s%sDone.%s ' "$C_BOLD" "$C_OK" "$C_RESET"
[[ $FAILED == 0 ]] && echo "Mailfilter is running." || echo "Mailfilter is installed, but check the warnings above."
cat <<EOF

${C_BOLD}DNS records to add${C_RESET} (see also the DNS toolbox in the web interface)
  A     $HOSTNAME_FQDN     ${PUBLIC_V4:-<IPv4>}
EOF
[[ -n $PUBLIC_V6 ]] && echo "  AAAA  $HOSTNAME_FQDN     $PUBLIC_V6"
echo "  PTR   ask your hosting provider: ${PUBLIC_V4:-<IPv4>}${PUBLIC_V6:+ and $PUBLIC_V6} -> $HOSTNAME_FQDN"
if [[ -n $FIRST_DOMAIN ]]; then
  echo "  SPF   add to $FIRST_DOMAIN:   ip4:${PUBLIC_V4:-<IPv4>}${PUBLIC_V6:+ ip6:$PUBLIC_V6}"
  echo "  DKIM  $(tr -s ' \n\t' ' ' <"$MF_ETC/dkim-$FIRST_DOMAIN.$SEL.txt" | sed 's/  */ /g')"
  echo "  MX    $FIRST_DOMAIN -> $HOSTNAME_FQDN  (only at go-live)"
fi
# Also show it on later runs until it is removed: an earlier run may have stopped before this point.
[[ -z $WEB_ADMIN_PASSWORD && -s $MF_ETC/initial-admin-password ]] && WEB_ADMIN_PASSWORD=$(<"$MF_ETC/initial-admin-password")
if [[ -n $WEB_ADMIN_PASSWORD ]]; then
  printf '\n%sWeb interface login%s  https://%s\n' "$C_BOLD" "$C_RESET" "$HOSTNAME_FQDN"
  echo "  User:     $ADMIN_EMAIL"
  echo "  Password: $WEB_ADMIN_PASSWORD   (also in $MF_ETC/initial-admin-password; change it after logging in, then delete that file)"
fi
echo
echo "Log of this install: $MF_LOG"
