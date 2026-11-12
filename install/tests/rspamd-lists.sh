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

rspamd -f -u _rspamd -g _rspamd >/tmp/rspamd.log 2>&1 &
pid=$!
# Stop Rspamd completely on exit: later CI steps start their own instance.
# shellcheck disable=SC2317,SC2329  # called by the EXIT trap
stop_rspamd() {
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do pgrep -x rspamd >/dev/null || return 0; sleep 0.25; done
  pkill -9 -x rspamd || true
}
trap stop_rspamd EXIT
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
[[ $fail == 0 ]] || { echo "--- rspamd log"; tail -40 /tmp/rspamd.log; }
exit $fail
