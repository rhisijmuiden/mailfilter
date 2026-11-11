#!/usr/bin/env bash
# Applies a sample configuration with the real Debian 13 tools and checks
# that Postfix, Rspamd and nftables accept the result. Runs in CI after
# install/tests/validate-configs.sh (which installs and configures them):
#   docker run --rm --cap-add=NET_ADMIN -v "$PWD":/src debian:trixie \
#     bash -c '/src/install/tests/validate-configs.sh && /src/backend/mailfilter_config/tests/integration.sh'
set -Eeuo pipefail
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
apt-get install -y -qq --no-install-recommends python3 iproute2 >/dev/null
fail=0
check() { local name=$1; shift; if "$@" >/tmp/check.out 2>&1; then echo "PASS $name"; else echo "FAIL $name"; sed 's/^/     /' /tmp/check.out | head -40; fail=1; fi; }

echo "== unit tests"
check "unit tests" bash -c "cd '$SRC/backend' && python3 -m unittest discover -s mailfilter_config/tests -t ."

# The firewall set the installer creates
nft add table inet mailfilter
nft add set inet mailfilter relay_ports '{ type inet_service; }'

cat >/tmp/gateway.json <<'JSON'
{
  "domains": [
    {"name": "rhis.nl", "customer": "rhis", "tls": "encrypt",
     "destinations": [{"host": "mail.rhis.nl", "port": 25}, {"host": "2a02:58:1f:42::25", "port": 2525}],
     "queue": {"concurrency": 3, "rate_delay": null}, "outbound": {"per_hour": 5000, "per_day": null}},
    {"name": "voorbeeld.nl", "verify_recipients": false,
     "destinations": [{"host": "mx.voorbeeld.nl", "port": 25, "mx": true}]}
  ],
  "relay_networks": [
    {"cidr": "203.0.113.25", "description": "Webserver 1", "customer": "rhis"},
    {"cidr": "2a02:58:1f:42::/64", "description": "Webservers IPv6"}
  ],
  "relay_ports": [{"port": 2525, "tls": "may"}, {"port": 10465, "tls": "wrapper"}],
  "destination_policies": [{"domain": "gmail.com", "queue": {"concurrency": 5, "rate_delay": 1}}],
  "address_routes": [{"address": "info@rhis.nl", "destination": {"host": "10.0.0.9", "port": 24, "protocol": "lmtp"}}],
  "tls_policies": [{"destination": ".bank.nl", "level": "secure"}]
}
JSON
MFC="$SRC/backend/bin/mailfilter-config"
echo "== apply"
check "mailfilter-config check" "$MFC" check --state /tmp/gateway.json
check "mailfilter-config apply" "$MFC" apply --state /tmp/gateway.json --no-reload
check "postfix check" postfix check
check "postconf has no warnings" bash -c '! postconf -n 2>&1 >/dev/null | grep -i warning'
check "per-domain queue in master.cf" bash -c 'postconf -M | grep -q "^mfq_rhis_nl_"'
check "extra listener 10465 with implicit TLS" bash -c 'postconf -P "10465/inet/smtpd_tls_wrappermode" | grep -q yes'
check "queue limit in main.cf" bash -c 'postconf -n | grep -q "^mfq_rhis_nl_.*_destination_concurrency_limit = 3"'
check "transport lookup" bash -c 'postmap -q rhis.nl hash:/etc/mailfilter/postfix/transport | grep -q "^mfq_rhis_nl_.*:\[mail.rhis.nl\]:25$"'
check "relay network lookup (IPv6)" bash -c 'postmap -q 2a02:58:1f:42::99 cidr:/etc/mailfilter/postfix/relay_networks.cidr | grep -q OK'
check "rspamadm configtest" rspamadm configtest
check "rspamd per-domain rate loaded" bash -c 'rspamadm configdump ratelimit | grep -q "equal"'
check "firewall relay ports" bash -c 'nft -f /etc/mailfilter/nftables/relay_ports.nft && nft list set inet mailfilter relay_ports | grep -q 10465'

echo "== a broken change is rolled back"
cp /etc/postfix/master.cf /tmp/master.before
sed 's/"rhis.nl"/"rhis..nl"/' /tmp/gateway.json >/tmp/bad.json
check "invalid domain refused" bash -c "! '$MFC' apply --state /tmp/bad.json --no-reload"
check "master.cf unchanged" cmp /etc/postfix/master.cf /tmp/master.before
check "apply again is a no-op" bash -c "'$MFC' apply --state /tmp/gateway.json --no-reload | grep -q 'Nothing to change'"

echo "== removing a domain removes its queue"
python3 - <<'PY'
import json; d = json.load(open("/tmp/gateway.json")); d["domains"] = d["domains"][1:]; json.dump(d, open("/tmp/less.json", "w"))
PY
check "apply smaller config" "$MFC" apply --state /tmp/less.json --no-reload
check "queue removed from master.cf" bash -c '! postconf -M | grep -q "^mfq_rhis_nl_"'
check "queue limit removed from main.cf" bash -c '! postconf -n | grep -q "^mfq_rhis_nl_"'
check "postfix check after removal" postfix check
exit $fail
