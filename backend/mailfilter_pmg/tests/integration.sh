#!/usr/bin/env bash
# Imports the test PMG backup with the real Debian 13 Postfix and Rspamd and
# checks that they accept the result. Runs in CI after
# install/tests/validate-configs.sh (which installs and configures them).
set -Eeuo pipefail
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
fail=0
check() { local name=$1; shift; if "$@" >/tmp/check.out 2>&1; then echo "PASS $name"; else echo "FAIL $name"; sed 's/^/     /' /tmp/check.out | head -40; fail=1; fi; }

(cd "$SRC/backend" && python3 -c 'import sys; from mailfilter_pmg.tests import fixtures; sys.stdout.buffer.write(fixtures.backup_tgz())') >/tmp/pmg-backup.tgz
check "preview" "$SRC/backend/bin/mailfilter-pmg-import" preview /tmp/pmg-backup.tgz
check "apply (lists, DKIM, gateway.json)" "$SRC/backend/bin/mailfilter-pmg-import" apply /tmp/pmg-backup.tgz --gateway-json --no-reload
check "domain in transport map" bash -c 'postmap -q shop.example hash:/etc/mailfilter/postfix/transport | grep mx.shop.example >/dev/null'
check "relay network in Postfix map" bash -c 'postmap -q 2001:db8::5 cidr:/etc/mailfilter/postfix/relay_networks.cidr | grep OK >/dev/null'
check "regex block list" grep -qF '/^(?:.*@bad\.example)$/i' /etc/mailfilter/rspamd/block_from_re.map
check "DKIM key for shop.example" test -s /var/lib/rspamd/dkim/shop.example.mf2024.key
check "postfix check" postfix check
check "rspamadm configtest" rspamadm configtest
check "rspamd reads the maps" bash -c 'rspamadm configdump multimap 2>/dev/null | grep -q MF_BLOCK_FROM_RE'
check "second apply changes no lists" bash -c "'$SRC/backend/bin/mailfilter-pmg-import' apply /tmp/pmg-backup.tgz --sections lists --json --no-reload | grep -q '\"lists\": 0'"
exit $fail
