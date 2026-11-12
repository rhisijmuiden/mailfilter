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
                     "newsletter_subject": {"enabled": True, "options": {"tag": "[Nieuwsbrief]"}, "domains": {}},
                     "phishing_display_name": {"enabled": True, "options": {"names": ["Reinder de Directeur"]}, "domains": {}},
                     "external_banner": {"enabled": True, "options": {"language": "nl"}, "domains": {}},
                     "arc_seal_inbound": {"enabled": True, "options": {}, "domains": {}}}
json.dump(d, open("/tmp/rules.json", "w"))
PY
check "apply with custom rules" "$MFC" apply --state /tmp/rules.json --no-reload
check "rules Lua and settings written" bash -c 'test -f /etc/rspamd/lua.local.d/mailfilter_rules.lua && grep -q "\"score\": 5.0" /etc/rspamd/mailfilter_rules.json'
check "rspamadm configtest with rules" rspamadm configtest
check "protection Lua and ARC config written" bash -c 'test -f /etc/rspamd/lua.local.d/mailfilter_protection.lua && grep -q "sign_inbound = true;" /etc/rspamd/local.d/arc.conf && grep -q google.com /etc/mailfilter/rspamd/arc_trusted.map && grep -q "\"rhis.nl\"" /etc/rspamd/mailfilter_rules.json'
# A scanning worker next to the milter proxy, only for this test.
mkdir -p /etc/rspamd/override.d
printf 'enabled = true;\nbind_socket = "127.0.0.1:11333";\n' >/etc/rspamd/override.d/worker-normal.inc
printf 'dns { nameserver = ["1.1.1.1:53"]; timeout = 1s; retransmits = 1; }\n' >/etc/rspamd/override.d/options.inc
rspamd -u _rspamd -g _rspamd
for _ in $(seq 1 30); do rspamc -h 127.0.0.1:11333 stat >/dev/null 2>&1 && break; sleep 1; done
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
check "newsletter without unsubscribe scored" bash -c 'grep -q MF_NEWSLETTER_NO_UNSUB /tmp/scan-bad.json || { cat /tmp/scan-bad.json; exit 1; }'
check "newsletter header and subject tag added" bash -c 'grep -q "X-Mailfilter-Newsletter" /tmp/scan-bad.json && grep -q "Nieuwsbrief" /tmp/scan-bad.json || { cat /tmp/scan-bad.json; exit 1; }'
check "newsletter with unsubscribe not scored" bash -c 'grep -q "\"MF_NEWSLETTER\"" /tmp/scan-good.json && ! grep -q MF_NEWSLETTER_NO_UNSUB /tmp/scan-good.json || { cat /tmp/scan-good.json; exit 1; }'
echo "== phishing protection and attachment policy in a real Rspamd scan"
cat >/tmp/ph-lookalike.eml <<'EML'
From: Reinder <reinder@rhls.nl>
To: info@rhis.nl
Subject: Factuur betalen
Message-ID: <3@rhls.nl>
Date: Wed, 12 Nov 2026 10:00:00 +0100
Content-Type: text/plain; charset=utf-8

Wil je deze factuur vandaag nog betalen? Zie http://185.12.3.4/factuur
EML
cat >/tmp/ph-name.eml <<'EML'
From: "Reinder de Directeur" <directeur.rhis@freemail.example>
To: info@rhis.nl
Subject: Even snel
Message-ID: <4@freemail.example>
Date: Wed, 12 Nov 2026 10:00:00 +0100
Content-Type: text/plain; charset=utf-8

Ben je even beschikbaar voor een betaling?
EML
attach() { # file name, content type, base64 content
  printf 'From: Leverancier <factuur@supplier.example>\nTo: info@rhis.nl\nSubject: Factuur\nMessage-ID: <%s@supplier.example>\nDate: Wed, 12 Nov 2026 10:00:00 +0100\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary="b1"\n\n--b1\nContent-Type: text/plain; charset=utf-8\n\nZie bijlage.\n--b1\nContent-Type: %s; name="%s"\nContent-Disposition: attachment; filename="%s"\nContent-Transfer-Encoding: base64\n\n%s\n--b1--\n' "$RANDOM" "$2" "$1" "$1" "$3"
}
attach factuur.pdf.exe application/octet-stream "$(printf 'MZ\220\0\3\0\0\0 this is not a real program' | base64 -w0)" >/tmp/att-exe.eml
attach offerte.docm application/vnd.ms-word.document.macroEnabled.12 "$(printf 'PK\3\4 word/vbaProject.bin' | base64 -w0)" >/tmp/att-macro.eml
scan2() { rspamc -h 127.0.0.1:11333 -j --ip 198.51.100.7 --from "$2" --rcpt info@rhis.nl "$1"; }
scan2 /tmp/ph-lookalike.eml reinder@rhls.nl >/tmp/scan-ph1.json 2>&1 || true
scan2 /tmp/ph-name.eml directeur.rhis@freemail.example >/tmp/scan-ph2.json 2>&1 || true
scan2 /tmp/att-exe.eml factuur@supplier.example >/tmp/scan-exe.json 2>&1 || true
scan2 /tmp/att-macro.eml factuur@supplier.example >/tmp/scan-macro.json 2>&1 || true
rspamc -h 127.0.0.1:11333 --mime --ip 198.51.100.7 --from directeur.rhis@freemail.example --rcpt info@rhis.nl /tmp/ph-name.eml >/tmp/scan-banner.eml 2>&1 || true
has() { grep -q "$2" "$1" || { cat "$1"; exit 1; }; }
action_is() { python3 -c 'import json,sys; a=json.load(open(sys.argv[1])).get("action"); sys.exit(a != sys.argv[2])' "$1" "$2" || { cat "$1"; exit 1; }; }
check "lookalike domain recognised" bash -c "$(declare -f has); has /tmp/scan-ph1.json MF_LOOKALIKE_DOMAIN"
check "link to an IP address recognised" bash -c "$(declare -f has); has /tmp/scan-ph1.json MF_SUSPICIOUS_LINK"
check "fake sender name recognised" bash -c "$(declare -f has); has /tmp/scan-ph2.json MF_DISPLAYNAME_SPOOF"
check "dangerous attachment refused" bash -c "$(declare -f has action_is); has /tmp/scan-exe.json MF_ATTACHMENT_BLOCKED && action_is /tmp/scan-exe.json reject"
check "macro document quarantined" bash -c "$(declare -f has action_is); has /tmp/scan-macro.json MF_ATTACHMENT_MACRO && action_is /tmp/scan-macro.json quarantine"
check "external banner added to the message" bash -c "$(declare -f has); has /tmp/scan-banner.eml 'Waarschuwing: dit bericht' && has /tmp/scan-banner.eml X-Mailfilter-External && has /tmp/scan-banner.eml 'Ben je even beschikbaar'"
check "no ARC seal on mail that got a banner" bash -c '! grep -qi "^ARC-Seal:.*rhis" /tmp/scan-banner.eml || { cat /tmp/scan-banner.eml; exit 1; }'
pkill -x rspamd || true
rm -f /etc/rspamd/override.d/worker-normal.inc /etc/rspamd/override.d/options.inc
exit $fail
