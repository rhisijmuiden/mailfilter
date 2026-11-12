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
# No systemd here: make the runtime directory it would create, log to a file we can show.
install -d -o _rspamd -g _rspamd /run/rspamd /var/lib/rspamd
# (not in /tmp: protected_regular stops root from opening a file another user owns there)
printf 'type = "file";\nfilename = "/run/rspamd/test.log";\nlevel = "info";\n' >/etc/rspamd/override.d/logging.inc
rspamd -f -u _rspamd -g _rspamd >/tmp/rspamd.out 2>&1 &
up=0
for _ in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/11333) 2>/dev/null; then up=1; break; fi
  sleep 1
done
check "rspamd scanner started" bash -c "[ $up = 1 ] || { cat /tmp/rspamd.out; tail -60 /run/rspamd/test.log; exit 1; }"
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
check "newsletter without unsubscribe scored" bash -c 'grep -q MF_NEWSLETTER_NO_UNSUB /tmp/scan-bad.json || { cat /tmp/scan-bad.json; grep -i "mailfilter_rules\\|error" /run/rspamd/test.log | tail -30; exit 1; }'
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
# rspamc may print more after the JSON reply (stderr is in the same file): read the first object only.
action_is() { python3 -c 'import json,sys; t=open(sys.argv[1]).read(); a=json.JSONDecoder().raw_decode(t[t.index("{"):])[0].get("action"); sys.exit(a != sys.argv[2])' "$1" "$2" || { head -c 4000 "$1"; exit 1; }; }
check "lookalike domain recognised" bash -c "$(declare -f has); has /tmp/scan-ph1.json MF_LOOKALIKE_DOMAIN"
check "link to an IP address recognised" bash -c "$(declare -f has); has /tmp/scan-ph1.json MF_SUSPICIOUS_LINK"
check "fake sender name recognised" bash -c "$(declare -f has); has /tmp/scan-ph2.json MF_DISPLAYNAME_SPOOF"
check "dangerous attachment refused" bash -c "$(declare -f has action_is); has /tmp/scan-exe.json MF_ATTACHMENT_BLOCKED && action_is /tmp/scan-exe.json reject"
check "macro document quarantined" bash -c "$(declare -f has action_is); has /tmp/scan-macro.json MF_ATTACHMENT_MACRO && action_is /tmp/scan-macro.json quarantine"
scan2 /tmp/ph-name.eml directeur.rhis@freemail.example >/tmp/scan-banner.json 2>&1 || true
# The rewritten message: banner first, then the original text, both readable after decoding.
banner_ok() { python3 - "$1" <<'PY' || { head -c 3000 "$1"; exit 1; }
import quopri, re, sys
# rspamc shows the new body but not the milter's header changes (Content-Transfer-Encoding),
# so decode the quoted-printable body ourselves.
raw = open(sys.argv[1], "rb").read().replace(b"\r\n", b"\n")
body = quopri.decodestring(raw.split(b"\n\n", 1)[1]).decode("utf-8", "replace")
print(repr(body[:300]))
sys.exit(not (body.startswith("Waarschuwing: dit bericht") and "Ben je even beschikbaar" in body))
PY
}
check "external banner added above the original text" bash -c "$(declare -f banner_ok); banner_ok /tmp/scan-banner.eml"
check "external banner header added" bash -c "$(declare -f has); has /tmp/scan-banner.json X-Mailfilter-External"
check "no ARC seal on mail that got a banner" bash -c '! grep -qi "^ARC-Seal:.*rhis" /tmp/scan-banner.eml || { cat /tmp/scan-banner.eml; exit 1; }'
echo "== relay domain (outbound only), SMTP account and sender lock"
python3 - <<'PY'
import json; d = json.load(open("/tmp/gateway.json"))
d["domains"].append({"name": "example.com", "kind": "relay", "sender_lock": True, "destinations": []})
d["relay_networks"].append({"cidr": "127.0.0.1", "description": "test client", "customer": "example.com"})
d["smtp_accounts"] = [{"username": "web@example.com", "domain": "example.com",
                       "password_hash": "{SSHA512}8Q5yQ0g5Zx8Y1nYz6m3xI9b0Y4i4b9cR2qVZ5o8hM3k0f2s7Vt6n1aWb4dE5gH6jK7lM8nP9qR0sT1uV2wX3yZ4AbCdEfGhIjKl"}]
json.dump(d, open("/tmp/relay.json", "w"))
PY
printf 'web01@example.nl:{PLAIN}secret\n' >/etc/mailfilter/smtp-users
check "apply with relay domain" "$MFC" apply --state /tmp/relay.json --no-reload
check "postfix check with sender lock" postfix check
check "relay domain does not receive mail" bash -c '! postmap -q example.com hash:/etc/mailfilter/postfix/relay_domains && ! postmap -q example.com hash:/etc/mailfilter/postfix/transport'
check "locked relay IP points to its class" bash -c 'postmap -q 127.0.0.1 cidr:/etc/mailfilter/postfix/sender_lock.cidr | grep -q "^mfl_example_com_"'
check "locked account points to its class" bash -c 'postmap -q web@example.com hash:/etc/mailfilter/postfix/sender_lock_sasl | grep -q "^mfl_example_com_"'
check "restriction class declared" bash -c 'postconf -h smtpd_restriction_classes | grep -q "^mfl_example_com_"'
check "hand-made SMTP account kept, web account added" bash -c 'grep -q "^web01@example.nl:" /etc/mailfilter/smtp-users && grep -q "^web@example.com:{SSHA512}" /etc/mailfilter/smtp-users'
check "dovecot accepts the account file" doveconf -n
check "DKIM key made for the relay domain" bash -c 'grep -q "^example.com mf" /etc/mailfilter/rspamd/dkim_selectors.map'
# A real SMTP session on the 2525 relay port: no milter and no DNS-based sender checks in this container.
postconf -e smtpd_milters= non_smtpd_milters= smtpd_sender_restrictions= >/dev/null
postfix start >/dev/null 2>&1 || true
smtp_try() { python3 - "$1" <<'PY'
import smtplib, sys, time
for _ in range(20):
    try:
        s = smtplib.SMTP("127.0.0.1", 2525, timeout=20); break
    except OSError:
        time.sleep(0.5)
else:
    print("no SMTP listener"); sys.exit(2)
s.ehlo("test.example")
try:
    s.sendmail(sys.argv[1], ["someone@voorbeeld.nl"], f"From: <{sys.argv[1]}>\r\nSubject: t\r\n\r\nhi\r\n")
    print("accepted"); sys.exit(0)
except smtplib.SMTPResponseException as e:
    print(e.smtp_code, e.smtp_error.decode()); sys.exit(1)
except smtplib.SMTPDataError as e:
    print(e); sys.exit(1)
PY
}
check "own domain may be sent from a locked IP" smtp_try news@example.com
check "bounce (empty sender) may be sent from a locked IP" smtp_try ""
check "other domain refused from a locked IP" bash -c "$(declare -f smtp_try); out=\$(smtp_try boss@example.org) ; echo \"\$out\"; echo \"\$out\" | grep -q 'may only send mail as @example.com'"
echo "== Mailtrap: mail from a test environment is caught, never sent"
id mailfilter >/dev/null 2>&1 || useradd --system --home-dir /var/lib/mailfilter --shell /usr/sbin/nologin mailfilter
install -d -o mailfilter -g mailfilter /var/lib/mailfilter /var/lib/mailfilter/mailtrap
# The pipe runs the command from the web app's venv; here it comes from the source tree.
install -d /var/lib/mailfilter/venv/bin
printf '#!/bin/sh\nPYTHONPATH=%s/backend exec python3 -m mailfilter.mailtrap.deliver "$@"\n' "$SRC" >/var/lib/mailfilter/venv/bin/mailfilter-trap-deliver
chmod 0755 /var/lib/mailfilter/venv/bin/mailfilter-trap-deliver
chmod -R o+rX "$SRC/backend" 2>/dev/null || true
# The test environment: its own address on this container
ip addr add 192.0.2.50/32 dev lo 2>/dev/null || true
TRAP_HASH=$(doveadm pw -s SSHA512 -p trapsecret)
trap_state() { python3 - "$1" "$TRAP_HASH" <<'PY'
import json, sys
d = json.load(open("/tmp/relay.json"))
d["mailtrap_enabled"] = sys.argv[1] == "on"
d["tls_cert"] = "/etc/ssl/certs/ssl-cert-snakeoil.pem"
d["tls_key"] = "/etc/ssl/private/ssl-cert-snakeoil.key"
d["mailtraps"] = [{"slug": "staging", "name": "Staging", "networks": ["192.0.2.50"], "password_hash": sys.argv[2],
                   "smtp_login": True, "imap": True, "mode": "except", "allow": ["tester@example.com"]}]
json.dump(d, open("/tmp/trap.json", "w"))
PY
}
trap_state on
check "apply with a Mailtrap mailbox" "$MFC" apply --state /tmp/trap.json --no-reload
check "postfix check with Mailtrap" postfix check
check "postconf has no warnings with Mailtrap" bash -c '! postconf -n 2>&1 >/dev/null | grep -i warning'
check "trap address points to its class" bash -c 'postmap -q 192.0.2.50 cidr:/etc/mailfilter/postfix/mailtrap.cidr | grep -q "^mft_staging"'
check "trap login points to its class" bash -c 'postmap -q trap-staging hash:/etc/mailfilter/postfix/mailtrap_sasl | grep -q "^mft_staging"'
check "mftrap pipe in master.cf" bash -c 'postconf -M mftrap/unix | grep -q mailfilter-trap-deliver'
check "Maildir made for the mailbox" test -d /var/lib/mailfilter/mailtrap/trap-staging/Maildir/new
check "dovecot accepts the Mailtrap IMAP config" bash -c 'doveconf -n | grep -q "port = 993"'
check "firewall opens IMAPS" grep -q 993 /etc/mailfilter/nftables/relay_ports.nft
postfix reload >/dev/null 2>&1 || postfix start >/dev/null 2>&1 || true
sleep 2
trap_send() { python3 - "$@" <<'PY'
import smtplib, sys, time
for _ in range(20):
    try:
        s = smtplib.SMTP("127.0.0.1", 2525, timeout=20, source_address=("192.0.2.50", 0)); break
    except OSError:
        time.sleep(0.5)
else:
    print("no SMTP listener"); sys.exit(2)
s.ehlo("staging.example")
try:
    s.sendmail("shop@example.com", sys.argv[1:], "From: Shop <shop@example.com>\r\nTo: klant@voorbeeld.nl\r\n"
               "Subject: Je bestelling {{ order.id }}\r\n\r\nBekijk http://staging.example.local:8080/o/1\r\n")
    print("accepted"); sys.exit(0)
except smtplib.SMTPResponseException as e:
    print(e.smtp_code, e.smtp_error.decode()); sys.exit(1)
except smtplib.SMTPRecipientsRefused as e:
    print(e); sys.exit(1)
PY
}
check "test environment may hand in mail" trap_send klant@voorbeeld.nl
# shellcheck disable=SC2016  # expanded by the inner bash
check "mail is stored in the Mailtrap mailbox" bash -c 'for _ in $(seq 1 30); do f=$(ls /var/lib/mailfilter/mailtrap/trap-staging/Maildir/new/* 2>/dev/null | head -1); [ -n "$f" ] && break; sleep 1; done
  [ -n "$f" ] || { postqueue -p; exit 1; }
  grep -q "^X-Mailfilter-Trap: staging" "$f" && grep -q "^X-Mailfilter-Envelope-To: klant@voorbeeld.nl" "$f" && grep -q "Je bestelling" "$f" || { cat "$f"; exit 1; }
  [ "$(stat -c %U "$f")" = mailfilter ]'
check "nothing left in the queue (never sent on)" bash -c 'for _ in $(seq 1 10); do postqueue -j | grep -q . || exit 0; sleep 1; done; postqueue -p; exit 1'
# Dovecot for real: log in over IMAPS with the mailbox login and read the caught mail
pkill -x dovecot 2>/dev/null || true
sleep 1
dovecot -F -o log_path=/dev/stderr >/tmp/dovecot-trap.log 2>&1 &
imap_read() { python3 - <<'PY' || { cat /tmp/dovecot-trap.log; exit 1; }
import imaplib, ssl, sys, time
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
for _ in range(20):
    try:
        m = imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=ctx); break
    except OSError:
        time.sleep(0.5)
else:
    print("no IMAPS listener"); sys.exit(1)
m.login("trap-staging", "trapsecret")
typ, data = m.select("INBOX", readonly=True)
print(typ, data)
sys.exit(not (typ == "OK" and int(data[0]) >= 1))
PY
}
check "IMAP login reads the caught mail" imap_read
smtp_imap_refused() { python3 - <<'PY'
import imaplib, ssl, sys
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
m = imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=ctx)
try:
    m.login("web01@example.nl", "secret"); print("SMTP account could read mail"); sys.exit(1)
except imaplib.IMAP4.error as e:
    print("refused:", e); sys.exit(0)
PY
}
check "SMTP accounts cannot log in on IMAP" smtp_imap_refused
pkill -x dovecot 2>/dev/null || true
trap_state off
check "apply with Mailtrap switched off" "$MFC" apply --state /tmp/trap.json --no-reload
postfix reload >/dev/null 2>&1 || true
sleep 2
# shellcheck disable=SC2016
check "switched off: test environment is refused, not relayed" bash -c "$(declare -f trap_send); out=\$(trap_send klant@voorbeeld.nl); echo \"\$out\"; echo \"\$out\" | grep -q 'Mailtrap is switched off'"
check "switched off: IMAP closed" bash -c '! doveconf -n | grep -q "port = 993"'
postsuper -d ALL >/dev/null 2>&1 || true
postfix stop >/dev/null 2>&1 || true
pkill -x rspamd || true
rm -f /etc/rspamd/override.d/worker-normal.inc /etc/rspamd/override.d/options.inc /etc/rspamd/override.d/logging.inc
exit $fail
