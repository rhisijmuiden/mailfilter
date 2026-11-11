#!/usr/bin/env bash
# Renders every template with sample values and checks it with the real
# tools from Debian 13. Runs in a plain debian:trixie container (no systemd):
#   docker run --rm -v "$PWD":/src debian:trixie /src/install/tests/validate-configs.sh
# shellcheck disable=SC2034
set -Eeuo pipefail
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TPL=$SRC/install/templates
MF_LOG=/tmp/validate.log
# shellcheck source=install/lib/common.sh
source "$SRC/install/lib/common.sh"
MF_LOG=/tmp/validate.log

export DEBIAN_FRONTEND=noninteractive
echo "postfix postfix/main_mailer_type select No configuration" | debconf-set-selections
apt-get update -qq
apt-get install -y -qq --no-install-recommends postfix postfix-lmdb rspamd dovecot-core unbound dns-root-data caddy nftables ssl-cert ca-certificates >/dev/null
echo "Versions: $(dpkg-query -W -f='${Package} ${Version}, ' postfix rspamd dovecot-core caddy unbound nftables)"

fail=0
check() { local name=$1; shift; if "$@" >/tmp/check.out 2>&1; then echo "PASS $name"; else echo "FAIL $name"; sed 's/^/     /' /tmp/check.out | head -40; fail=1; fi; }

HOSTNAME=mailfilter01.example.nl
EXTRA_RELAY_LISTENERS="2525      inet  n       -       y       -       -       smtpd
  -o syslog_name=postfix/port-2525
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_client_restrictions=permit_mynetworks,permit_sasl_authenticated,reject"
install -d /etc/mailfilter/postfix /etc/mailfilter/rspamd /var/lib/rspamd/dkim
printf '203.0.113.25 OK\n2a02:58:1f:42::/64 OK\n' >/etc/mailfilter/postfix/relay_networks.cidr
printf '127.0.0.0/8\n::1\n203.0.113.25\n2a02:58:1f:42::/64\n' >/etc/mailfilter/rspamd/local_addrs.map
for f in relay_domains transport sender_block tls_policy; do touch /etc/mailfilter/postfix/$f; done
touch /etc/mailfilter/postfix/client_block.cidr /etc/mailfilter/postfix/postscreen_access.cidr
cp "$TPL/postfix/dnsbl_reply" "$TPL/postfix/bounce.cf" /etc/mailfilter/postfix/
for f in allow_from allow_domain block_from block_domain block_ip antivirus_skip dkim_selectors; do touch /etc/mailfilter/rspamd/$f.map; done
touch /etc/mailfilter/rspamd/ratelimit_overrides.conf /etc/mailfilter/smtp-users

render "$TPL/postfix/main.cf" /etc/postfix/main.cf
render "$TPL/postfix/master.cf" /etc/postfix/master.cf
for f in relay_domains transport sender_block tls_policy; do postmap hash:/etc/mailfilter/postfix/$f; done
postconf -e "smtpd_tls_chain_files = /etc/ssl/private/ssl-cert-snakeoil.key, /etc/ssl/certs/ssl-cert-snakeoil.pem"
[[ -f /etc/aliases ]] || printf 'postmaster: root\nroot: admin@example.nl\n' >/etc/aliases
newaliases
check "postfix check" postfix check
check "postconf warnings" bash -c '! postconf -n 2>&1 >/dev/null | grep -i warning'
check "bounce templates" postconf -b
check "postconf -M (2525 listener)" bash -c 'postconf -M | grep -q "^2525"'

RSPAMD_PASSWORD_HASH=$(rspamadm pw -q -p test)
for f in "$TPL"/rspamd/local.d/*; do render "$f" "/etc/rspamd/local.d/$(basename "$f")"; done
check "rspamadm configtest" rspamadm configtest
check "rspamd quarantine action known" bash -c 'rspamadm configdump actions 2>/dev/null | grep -qi quarantine'

render "$TPL/dovecot/dovecot.conf" /etc/dovecot/dovecot.conf
check "doveconf -n" doveconf -n
# Start Dovecot for real: some errors only show at start-up, not in doveconf
install -d /etc/mailfilter /var/spool/postfix/private
printf 'web01@example.nl:%s\n' "$(doveadm pw -s SHA512-CRYPT -p secret)" >/etc/mailfilter/smtp-users
chown root:dovecot /etc/mailfilter/smtp-users; chmod 0640 /etc/mailfilter/smtp-users
# shellcheck disable=SC2329  # called through check()
dovecot_runs() {
  dovecot -F -o log_path=/dev/stderr >/tmp/dovecot.log 2>&1 &
  local pid=$! ok=1
  for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -S /var/spool/postfix/private/auth ]] && { ok=0; break; }; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null || ok=1
  cat /tmp/dovecot.log
  kill "$pid" 2>/dev/null || true
  return "$ok"
}
check "dovecot starts and opens the Postfix auth socket" dovecot_runs

render "$TPL/unbound/mailfilter.conf" /etc/unbound/unbound.conf.d/mailfilter.conf
install -d /var/lib/unbound
[[ -s /var/lib/unbound/root.key ]] || cp /usr/share/dns/root.key /var/lib/unbound/root.key
check "unbound-checkconf" unbound-checkconf

MGMT_V4_ELEMENTS="elements = { 198.51.100.7 }"; MGMT_V6_ELEMENTS=""
SSH_RULES=$'    ip saddr @mgmt_v4 tcp dport 22 accept\n    ip6 saddr @mgmt_v6 tcp dport 22 accept'
EXTRA_RELAY_PORTS_NFT=", 2525"
render "$TPL/nftables/nftables.conf" /tmp/nftables.conf
check "nft -c" nft -c -f /tmp/nftables.conf

install -d /etc/letsencrypt/live/$HOSTNAME
cp /etc/ssl/certs/ssl-cert-snakeoil.pem /etc/letsencrypt/live/$HOSTNAME/fullchain.pem
cp /etc/ssl/private/ssl-cert-snakeoil.key /etc/letsencrypt/live/$HOSTNAME/privkey.pem
render "$TPL/caddy/Caddyfile" /tmp/Caddyfile
check "caddy validate" caddy validate --config /tmp/Caddyfile --adapter caddyfile
render "$TPL/caddy/Caddyfile.acme" /tmp/Caddyfile.acme
check "caddy validate (acme)" caddy validate --config /tmp/Caddyfile.acme --adapter caddyfile

exit $fail
