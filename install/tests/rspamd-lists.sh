#!/usr/bin/env bash
# Runs the real Rspamd with the installer's configuration (rendered by
# validate-configs.sh) and checks the allow/block lists from the web interface:
# a line "<recipient>|<sender>" in /var/lib/mailfilter/lists/{allow,block}.map.
set -Eeuo pipefail
LISTS=/var/lib/mailfilter/lists
install -d -m 0755 /var/lib/mailfilter "$LISTS"
cat >"$LISTS/allow.map" <<'MAP'
jan@klant.nl|leverancier.nl
*|partner@vriend.example
MAP
cat >"$LISTS/block.map" <<'MAP'
jan@klant.nl|spam@bad.example
klant.nl|evil.example
MAP
chmod 0644 "$LISTS"/*.map

# The CI container has no working DNS. Fail DNS lookups fast, or every scan
# runs into Rspamd's 8 s task timeout before the list rules are checked.
install -d /etc/rspamd/override.d
printf 'dns { nameserver = ["127.0.0.1:53"]; timeout = 0.5s; retransmits = 1; }\n' >/etc/rspamd/override.d/options.inc

rspamd -f -u _rspamd -g _rspamd >/tmp/rspamd.log 2>&1 &
pid=$!
# Stop Rspamd completely on exit: later CI steps start their own instance.
# shellcheck disable=SC2317,SC2329  # called by the EXIT trap
stop_rspamd() {
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do pgrep -x rspamd >/dev/null || return 0; sleep 0.25; done
  pkill -9 -x rspamd || true
}
# shellcheck disable=SC2317,SC2329
cleanup() { stop_rspamd; rm -f /etc/rspamd/override.d/options.inc; }
trap cleanup EXIT
for _ in $(seq 1 60); do
  rspamc -h 127.0.0.1:11334 -P test stat >/dev/null 2>&1 && break
  sleep 1
done
fail=0
indent() { while IFS= read -r line; do printf '     %s\n' "$line"; done <<<"$1"; }
msg=$'From: someone@example.org\nTo: jan@klant.nl\nSubject: test\nMessage-ID: <t@example.org>\n\nhello\n'
expect() { # sender recipient symbol
  local out
  out=$(printf '%s' "$msg" | rspamc -h 127.0.0.1:11334 -i 192.0.2.10 -F "$1" -r "$2" 2>&1 || true)
  if grep -q "Symbol: $3" <<<"$out"; then echo "PASS $1 -> $2: $3"; else echo "FAIL $1 -> $2: expected $3"; indent "$out"; fail=1; fi
}
expect_none() {
  local out
  out=$(printf '%s' "$msg" | rspamc -h 127.0.0.1:11334 -i 192.0.2.10 -F "$1" -r "$2" 2>&1 || true)
  if grep -q "Symbol: MF_LIST_" <<<"$out"; then echo "FAIL $1 -> $2: unexpected list match"; indent "$out"; fail=1; else echo "PASS $1 -> $2: no list"; fi
}
expect spam@bad.example jan@klant.nl MF_LIST_BLOCK_MAILBOX_ADDR
expect Spam@Bad.Example Jan@Klant.nl MF_LIST_BLOCK_MAILBOX_ADDR
expect x@evil.example piet@klant.nl MF_LIST_BLOCK_DOMAIN_DOMAIN
expect factuur@leverancier.nl jan@klant.nl MF_LIST_ALLOW_MAILBOX_DOMAIN
expect partner@vriend.example piet@ander.nl MF_LIST_ALLOW_ALL_ADDR
expect_none spam@bad.example piet@klant.nl
expect_none factuur@leverancier.nl piet@klant.nl
out=$(printf '%s' "$msg" | rspamc -h 127.0.0.1:11334 -i 192.0.2.10 -F spam@bad.example -r jan@klant.nl 2>&1 || true)
if grep -q "Action: reject" <<<"$out"; then echo "PASS blocked sender is refused"; else echo "FAIL blocked sender not refused"; fail=1; fi
# Spam reports (lua.local.d/mailfilter_reports.lua): mail only for the report
# addresses is accepted unfiltered, even from a blocked sender; mail that also
# goes to a real mailbox is filtered as usual.
out=$(printf '%s' "$msg" | rspamc -h 127.0.0.1:11334 -i 192.0.2.10 -F spam@bad.example -r spam@mailfilter01.example.nl 2>&1 || true)
if grep -q "Symbol: MF_SPAM_REPORT" <<<"$out" && grep -q "Action: no action" <<<"$out"; then echo "PASS spam report accepted unfiltered"; else echo "FAIL spam report not accepted"; indent "$out"; fail=1; fi
out=$(printf '%s' "$msg" | rspamc -h 127.0.0.1:11334 -i 192.0.2.10 -F spam@bad.example -r notspam@mailfilter01.example.nl -r jan@klant.nl 2>&1 || true)
if grep -q "Action: reject" <<<"$out" && ! grep -q "Symbol: MF_SPAM_REPORT" <<<"$out"; then echo "PASS report address next to a real mailbox is filtered"; else echo "FAIL report address next to a real mailbox skipped the filter"; indent "$out"; fail=1; fi
if [[ $fail != 0 ]]; then
  echo "--- rspamd log"; tail -40 /tmp/rspamd.log
  echo "--- multimap rules Rspamd loaded"; rspamadm configdump multimap 2>&1 | grep -E '^MF_LIST|selector' | head -30
fi
exit $fail
