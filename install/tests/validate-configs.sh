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
# FTP/FTPS tests need pyftpdlib as a test server; they run in the mailfilterctl CI job with the PyPI release,
# because the Debian package hangs on repeated TLS logins. SFTP and encryption are tested here on Debian 13.
apt-get install -y -qq --no-install-recommends postfix postfix-lmdb postfix-ldap rspamd dovecot-core dovecot-imapd unbound dns-root-data caddy nftables ssl-cert ca-certificates python3 \
  sudo systemd python3-pytest zstd git gnupg openssh-client openssh-server >/dev/null
echo "Versions: $(dpkg-query -W -f='${Package} ${Version}, ' postfix rspamd dovecot-core caddy unbound nftables)"

fail=0
check() { local name=$1; shift; if "$@" >/tmp/check.out 2>&1; then echo "PASS $name"; else echo "FAIL $name"; sed 's/^/     /' /tmp/check.out | head -40; fail=1; fi; }

HOSTNAME=mailfilter01.example.nl
MF_SRC=$SRC
install -d /etc/mailfilter/postfix /etc/mailfilter/rspamd /etc/mailfilter/nftables /var/lib/rspamd/dkim
# The same empty files the installer creates; mailfilter-config fills them further down
touch /etc/mailfilter/relay_networks.list /etc/mailfilter/nftables/relay_ports.nft /etc/mailfilter/rspamd/local_addrs.map
for f in relay_domains transport sender_block tls_policy recipient_verify relay_recipients archive_bcc; do touch /etc/mailfilter/postfix/$f; done
touch /etc/mailfilter/postfix/relay_networks.cidr /etc/mailfilter/postfix/client_block.cidr /etc/mailfilter/postfix/postscreen_access.cidr /etc/mailfilter/postfix/cloud_relay.cidr
cp "$TPL/postfix/dnsbl_reply" "$TPL/postfix/bounce.cf" /etc/mailfilter/postfix/
for f in allow_from allow_domain allow_ip allow_from_re block_from block_domain block_ip block_from_re antivirus_skip dkim_selectors arc_trusted; do touch /etc/mailfilter/rspamd/$f.map; done
touch /etc/mailfilter/rspamd/ratelimit_overrides.conf /etc/mailfilter/smtp-users

render "$TPL/postfix/main.cf" /etc/postfix/main.cf
render "$TPL/postfix/master.cf" /etc/postfix/master.cf
for f in relay_domains transport sender_block tls_policy recipient_verify relay_recipients archive_bcc; do postmap hash:/etc/mailfilter/postfix/$f; done
postconf -e "smtpd_tls_chain_files = /etc/ssl/private/ssl-cert-snakeoil.key, /etc/ssl/certs/ssl-cert-snakeoil.pem"
[[ -f /etc/aliases ]] || printf 'postmaster: root\nroot: admin@example.nl\n' >/etc/aliases
newaliases
check "postfix check" postfix check
check "postconf warnings" bash -c '! postconf -n 2>&1 >/dev/null | grep -i warning'
check "bounce templates" postconf -b
# shellcheck disable=SC2016  # expanded by the inner bash
check "DMARC report address goes to the mfdmarc pipe" bash -c 'postconf -xh transport_maps | grep -q "dmarc-reports@mailfilter01.example.nl=mfdmarc:" && postconf -M mfdmarc/unix | grep -q mailfilter-dmarc-ingest'
# shellcheck disable=SC2016
check "DMARC report address lives on the host name" bash -c '[[ $(postconf -xh virtual_mailbox_domains) == mailfilter01.example.nl ]]'
# shellcheck disable=SC2016
check "Spam report addresses go to the mfreport pipe" bash -c 'postconf -xh transport_maps | grep -q "spam@mailfilter01.example.nl=mfreport:" && postconf -xh transport_maps | grep -q "notspam@mailfilter01.example.nl=mfreport:" && postconf -M mfreport/unix | grep -q mailfilter-spam-report && [[ $(postconf -xh mfreport_destination_recipient_limit) == 1 ]]'
# shellcheck disable=SC2016
check "archive copies go to the mfarchive pipe" bash -c 'postconf -xh transport_maps | grep -q "archive.mailfilter.invalid=mfarchive:" && postconf -M mfarchive/unix | grep -q mailfilter-archive-deliver && [[ $(postconf -xh recipient_bcc_maps) == hash:/etc/mailfilter/postfix/archive_bcc ]]'
check "master.cf has the mailfilter-config block" grep -q '^# BEGIN mailfilter-config' /etc/postfix/master.cf

RSPAMD_PASSWORD_HASH=$(rspamadm pw -q -p test)
for f in "$TPL"/rspamd/local.d/*; do render "$f" "/etc/rspamd/local.d/$(basename "$f")"; done
install -d /etc/rspamd/lua.local.d
render "$TPL/rspamd/lua.local.d/mailfilter_reports.lua" /etc/rspamd/lua.local.d/mailfilter_reports.lua
check "rspamadm configtest" rspamadm configtest

# What the installer's "Domains, relay access and throttling" step does, on top of these templates
check "mailfilter-config seed" "$SRC/backend/bin/mailfilter-config" seed --no-reload \
  --relay-net 203.0.113.25 --relay-net 2a02:58:1f:42::/64 --relay-port 2525 \
  --domain example.nl --destination mail.example.nl
check "postconf -M (2525 listener from seed)" bash -c 'postconf -M | grep "^2525" >/dev/null'
check "postfix check after seed" postfix check
check "relay network in Postfix map" bash -c 'postmap -q 2a02:58:1f:42::1 cidr:/etc/mailfilter/postfix/relay_networks.cidr | grep OK >/dev/null'
check "rspamadm configtest after seed" rspamadm configtest
# Rspamd reads its maps as _rspamd, not as root: use the installer's permissions
chmod 0751 /etc/mailfilter
# shellcheck disable=SC2016  # expanded by the inner bash
check "_rspamd can read the Mailfilter maps" bash -c 'for f in /etc/mailfilter/rspamd/*; do runuser -u _rspamd -- cat "$f" >/dev/null || exit 1; done'
check "rspamd quarantine action known" bash -c 'rspamadm configdump actions 2>/dev/null | grep -qi quarantine'

render "$TPL/dovecot/dovecot.conf" /etc/dovecot/dovecot.conf
check "doveconf -n" doveconf -n
# Start Dovecot for real: some errors only show at start-up, not in doveconf
install -d /etc/mailfilter /var/spool/postfix/private
printf 'web01@example.nl:%s\n' "$(doveadm pw -s SHA512-CRYPT -p secret)" >/etc/mailfilter/smtp-users
chown root:dovecot /etc/mailfilter/smtp-users; chmod 0640 /etc/mailfilter/smtp-users
# shellcheck disable=SC2317,SC2329  # called through check()
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

# shellcheck source=install/lib/web.sh
source "$SRC/install/lib/web.sh"
HOSTNAME_FQDN=$HOSTNAME; PUBLIC_V4=203.0.113.25; PUBLIC_V6=2001:db8::25
WEB_MODE=public; TRUSTED_PROXIES=(); WEB_FROM=(); web_defaults; web_firewall_rules
MGMT_V4_ELEMENTS="elements = { 198.51.100.7 }"; MGMT_V6_ELEMENTS=""
SSH_RULES=$'    ip saddr @mgmt_v4 tcp dport 22 accept\n    ip6 saddr @mgmt_v6 tcp dport 22 accept'
render "$TPL/nftables/nftables.conf" /tmp/nftables.conf
check "nft -c (with relay_ports from seed)" bash -c 'grep 2525 /etc/mailfilter/nftables/relay_ports.nft >/dev/null && nft -c -f /tmp/nftables.conf'
: >/etc/mailfilter/nftables/relay_ports.nft
check "nft -c (empty relay_ports, first install)" nft -c -f /tmp/nftables.conf

install -d /etc/letsencrypt/live/$HOSTNAME
cp /etc/ssl/certs/ssl-cert-snakeoil.pem /etc/letsencrypt/live/$HOSTNAME/fullchain.pem
cp /etc/ssl/private/ssl-cert-snakeoil.key /etc/letsencrypt/live/$HOSTNAME/privkey.pem
# Every web access mode (install/lib/web.sh): Caddy and the firewall rules.
for WEB_MODE in public internal proxy http; do
  WEB_PORT=""; WEB_URL=""; TRUSTED_PROXIES=(); WEB_FROM=()
  [[ $WEB_MODE == proxy ]] && TRUSTED_PROXIES=(10.0.0.5 2001:db8:1::/64)
  [[ $WEB_MODE == internal || $WEB_MODE == http ]] && WEB_FROM=(192.168.10.0/24 2001:db8:2::/48)
  web_defaults
  web_caddyfile_vars
  render "$TPL/caddy/Caddyfile" "/tmp/Caddyfile.$WEB_MODE"
  check "caddy validate ($WEB_MODE)" caddy validate --config "/tmp/Caddyfile.$WEB_MODE" --adapter caddyfile
  web_firewall_rules
  render "$TPL/nftables/nftables.conf" "/tmp/nftables.$WEB_MODE.conf"
  check "nft -c ($WEB_MODE web access)" nft -c -f "/tmp/nftables.$WEB_MODE.conf"
done
check "proxy mode trusts only the proxy" grep -q "trusted_proxies static 10.0.0.5 2001:db8:1::/64" /tmp/Caddyfile.proxy
check "internal mode does not use Let's Encrypt" bash -c '! grep -q letsencrypt /tmp/Caddyfile.internal && grep -q "issuer internal" /tmp/Caddyfile.internal'
render "$TPL/caddy/Caddyfile.acme" /tmp/Caddyfile.acme
check "caddy validate (acme)" caddy validate --config /tmp/Caddyfile.acme --adapter caddyfile

MF_SRC=/opt/mailfilter
render "$TPL/mailfilterctl.wrapper" /usr/local/sbin/mailfilterctl 0755
check "mailfilterctl wrapper" sh -n /usr/local/sbin/mailfilterctl
install -m 0440 "$TPL/sudoers-mailfilterctl" /tmp/sudoers-mailfilterctl
check "sudoers rule" visudo -cf /tmp/sudoers-mailfilterctl
install -m 0644 "$TPL"/systemd/mailfilter-backup.* /etc/systemd/system/
printf '#!/bin/sh\n' >/usr/local/sbin/mailfilterctl; chmod 0755 /usr/local/sbin/mailfilterctl
check "systemd units" systemd-analyze verify /etc/systemd/system/mailfilter-backup.service /etc/systemd/system/mailfilter-backup.timer
git config --global user.name ci; git config --global user.email ci@example.org
git config --global --add safe.directory '*'
check "mailfilterctl tests (Debian 13 Python)" python3 -m pytest -q -p no:cacheprovider "$SRC/ops/tests"

exit $fail
