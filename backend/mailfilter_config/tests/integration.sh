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
  "tls_policies": [{"destination": ".bank.nl", "level": "secure"}],
  "holds": [{"kind": "ip", "value": "203.0.113.25", "reason": "spam burst"}, {"kind": "account", "value": "webshop@rhis.nl", "reason": "volume spike"}]
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
check "paused IP is held" bash -c 'postmap -q 203.0.113.25 cidr:/etc/mailfilter/postfix/outbound_hold.cidr | grep -q "^HOLD Paused by Mailfilter"'
check "paused account is held" bash -c 'postmap -q webshop@rhis.nl hash:/etc/mailfilter/postfix/outbound_hold_sasl | grep -q "^HOLD"'
check "hold check runs for every message" bash -c 'postconf -h smtpd_end_of_data_restrictions | grep -q check_sasl_access'
check "rspamadm configtest" rspamadm configtest
check "rspamd per-domain rate loaded" bash -c 'rspamadm configdump ratelimit | grep -q "equal"'
check "DKIM key made for new domain" bash -c 'ls /var/lib/rspamd/dkim/rhis.nl.mf*.key && grep -q "^rhis.nl mf" /etc/mailfilter/rspamd/dkim_selectors.map && grep -q "v=DKIM1" /etc/mailfilter/dkim-rhis.nl.mf*.txt'
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
echo "== custom rules: newsletter filter in a real Rspamd scan"
python3 - <<'PY'
import json; d = json.load(open("/tmp/gateway.json"))
d["custom_rules"] = {"newsletter_no_unsubscribe": {"enabled": True, "options": {"score": 5}, "domains": {}},
                     "newsletter_subject": {"enabled": True, "options": {"tag": "[Nieuwsbrief]"}, "domains": {}}}
json.dump(d, open("/tmp/rules.json", "w"))
PY
check "apply with custom rules" "$MFC" apply --state /tmp/rules.json --no-reload
check "rules Lua and settings written" bash -c 'test -f /etc/rspamd/lua.local.d/mailfilter_rules.lua && grep -q "\"score\": 5.0" /etc/rspamd/mailfilter_rules.json'
check "rspamadm configtest with rules" rspamadm configtest
# A scanning worker next to the milter proxy, only for this test.
mkdir -p /etc/rspamd/override.d
printf 'enabled = true;\nbind_socket = "127.0.0.1:11333";\n' >/etc/rspamd/override.d/worker-normal.inc
printf 'dns { nameserver = ["1.1.1.1:53"]; timeout = 1s; retransmits = 1; }\n' >/etc/rspamd/override.d/options.inc
# No systemd here: make the runtime directory it would create, log to a file we can show.
install -d -o _rspamd -g _rspamd /run/rspamd /var/lib/rspamd
install -o _rspamd -g _rspamd -m 0644 /dev/null /tmp/rspamd.log
printf 'type = "file";\nfilename = "/tmp/rspamd.log";\nlevel = "info";\n' >/etc/rspamd/override.d/logging.inc
rspamd -f -u _rspamd -g _rspamd >/tmp/rspamd.out 2>&1 &
up=0
for _ in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/11333) 2>/dev/null; then up=1; break; fi
  sleep 1
done
check "rspamd scanner started" bash -c "[ $up = 1 ] || { cat /tmp/rspamd.out; tail -60 /tmp/rspamd.log; exit 1; }"
cat >/tmp/nl-bad.eml <<'EML'
From: Winkel <news@shop.example>
To: info@rhis.nl
Subject: Grote uitverkoop
Precedence: bulk
X-Mailer: MailerLite
Message-ID: <1@shop.example>
Date: Wed, 12 Nov 2026 10:00:00 +0100
Content-Type: text/plain; charset=utf-8

Alles moet weg! Bestel nu op https://shop.example/sale
EML
cat >/tmp/nl-good.eml <<'EML'
From: Winkel <news@shop.example>
To: info@rhis.nl
Subject: Nieuws
List-Unsubscribe: <https://shop.example/u/1>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
Message-ID: <2@shop.example>
Date: Wed, 12 Nov 2026 10:00:00 +0100
Content-Type: text/plain; charset=utf-8

Ons nieuws van deze maand.
EML
scan() { rspamc -h 127.0.0.1:11333 -j --ip 198.51.100.7 --from news@shop.example --rcpt info@rhis.nl "$1"; }
scan /tmp/nl-bad.eml >/tmp/scan-bad.json 2>&1 || true
scan /tmp/nl-good.eml >/tmp/scan-good.json 2>&1 || true
check "newsletter without unsubscribe scored" bash -c 'grep -q MF_NEWSLETTER_NO_UNSUB /tmp/scan-bad.json || { cat /tmp/scan-bad.json; grep -i "mailfilter_rules\\|error" /tmp/rspamd.log | tail -30; exit 1; }'
check "newsletter header and subject tag added" bash -c 'grep -q "X-Mailfilter-Newsletter" /tmp/scan-bad.json && grep -q "Nieuwsbrief" /tmp/scan-bad.json || { cat /tmp/scan-bad.json; exit 1; }'
check "newsletter with unsubscribe not scored" bash -c 'grep -q "\"MF_NEWSLETTER\"" /tmp/scan-good.json && ! grep -q MF_NEWSLETTER_NO_UNSUB /tmp/scan-good.json || { cat /tmp/scan-good.json; exit 1; }'
pkill -x rspamd || true
rm -f /etc/rspamd/override.d/worker-normal.inc /etc/rspamd/override.d/options.inc /etc/rspamd/override.d/logging.inc
exit $fail
