#!/usr/bin/env bash
# Starts a built template the way Proxmox would (systemd as init, first boot) and checks
# that it ends up with a working web interface, the console login text and the setup
# wizard. Run by CI after appliance/build.sh.
#
#   sudo appliance/tests/smoke.sh appliance/out/mailfilter-test_amd64.tar.zst
set -Eeuo pipefail
TAR=${1:?template .tar.zst}
CT=mf-ct-smoke-$$; IMG=mf-ct-smoke-$$
fail() { echo "FAIL: $*" >&2; docker exec "$CT" sh -c 'tail -n 100 /var/log/mailfilter-install.log; journalctl -u mailfilter-firstboot --no-pager | tail -n 60' >&2 || true; exit 1; }
cleanup() { docker rm -f "$CT" >/dev/null 2>&1 || true; docker rmi -f "$IMG" >/dev/null 2>&1 || true; }
trap cleanup EXIT
ex() { docker exec "$CT" "$@"; }

zstd -dc "$TAR" | docker import - "$IMG" >/dev/null
# Port 8443 through appliance.conf, as an admin could do with `pct push` before the first start
docker create --name "$CT" --hostname mailfilter --privileged --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock -e container=docker "$IMG" /sbin/init >/dev/null
printf 'WEB_PORT=8443\n' >/tmp/appliance.conf.$$
docker cp /tmp/appliance.conf.$$ "$CT:/etc/mailfilter/appliance.conf"; rm -f /tmp/appliance.conf.$$
docker start "$CT" >/dev/null

echo "Waiting for the first boot setup"
for i in $(seq 180); do
  if ! ex test -e /etc/mailfilter/appliance-firstboot-pending 2>/dev/null && ex test -e /etc/mailfilter/setup-wizard-pending 2>/dev/null; then break; fi
  ex systemctl is-failed -q mailfilter-firstboot 2>/dev/null && fail "first boot setup failed"
  (( i % 10 == 0 )) && echo "  still preparing ($((i * 10))s)"
  sleep 10
done
ex test -e /etc/mailfilter/setup-wizard-pending || fail "first boot did not finish in 30 minutes"

echo "Console text"
ex cat /etc/issue
ex grep -q "Username:  admin" /etc/issue || fail "/etc/issue does not show the user name"
PW=$(ex cat /etc/mailfilter/initial-admin-password)
[[ ${#PW} -ge 10 ]] || fail "no initial password"
ex grep -qF "Password:  $PW" /etc/issue || fail "/etc/issue does not show the password"
ex grep -q ":8443/" /etc/issue || fail "/etc/issue does not show port 8443"

echo "Unique secrets"
ex test -s /etc/mailfilter/secrets.env || fail "no secrets.env"
ex sh -c 'ls /etc/ssh/ssh_host_ed25519_key' >/dev/null || fail "no SSH host key"
ex test -s /etc/machine-id || fail "no machine-id"

echo "Web interface"
J=(-H 'Content-Type: application/json' -H 'X-Requested-With: mailfilter')
api() { ex curl -sk -b /tmp/c -c /tmp/c "${J[@]}" "$@"; }
for _ in $(seq 30); do api -f https://127.0.0.1:8443/api/health >/dev/null 2>&1 && break; sleep 2; done
api -f https://127.0.0.1:8443/api/health >/dev/null || fail "web interface does not answer on 8443"
api -f -X POST https://127.0.0.1:8443/api/auth/login -d "{\"email\":\"admin\",\"password\":\"$PW\"}" >/dev/null || fail "cannot log in as admin"
api -f https://127.0.0.1:8443/api/system/info | grep -q '"setup_pending":true' || fail "setup wizard is not pending"

echo "Setup wizard: server name, own login and password"
JOB=$(api -f -X PUT https://127.0.0.1:8443/api/system/setup/server \
  -d '{"hostname":"mf-smoke.example.org","admin_email":"it@example.org","new_password":"smoke-test-password-1"}' | sed -n 's/.*"id": *"\([^"]*\)".*/\1/p')
[[ -n $JOB ]] || fail "setup server did not start a job"
for _ in $(seq 120); do
  state=$(api https://127.0.0.1:8443/api/system/jobs/"$JOB" 2>/dev/null | sed -n 's/.*"state": *"\([a-z]*\)".*/\1/p' || true)
  [[ $state == "done" || $state == failed ]] && break
  sleep 10
done
[[ $state == "done" ]] || { api https://127.0.0.1:8443/api/system/jobs/"$JOB"/log || true; fail "setup server job: ${state:-no answer}"; }
ex grep -q '^MF_HOSTNAME=mf-smoke.example.org$' /etc/mailfilter/mailfilter.env || fail "host name not saved"
ex postconf -h myhostname | grep -qx mf-smoke.example.org || fail "Postfix has the old host name"
for _ in $(seq 30); do api -f https://127.0.0.1:8443/api/health >/dev/null 2>&1 && break; sleep 2; done
ex rm -f /tmp/c
api -f -X POST https://127.0.0.1:8443/api/auth/login -d '{"email":"it@example.org","password":"smoke-test-password-1"}' >/dev/null || fail "cannot log in with the new login"
api -f -X POST https://127.0.0.1:8443/api/system/setup/finish | grep -q '"pending":false' || fail "finish failed"
ex test ! -e /etc/mailfilter/initial-admin-password || fail "initial password still on disk"
ex grep -q "Password:" /etc/issue && fail "/etc/issue still shows a password"

echo "Services"
for s in postfix rspamd dovecot postgresql redis-server unbound caddy mailfilter-web mailfilter-tracer; do
  ex systemctl is-active -q "$s" || fail "$s is not running"
done
echo "PASS"
