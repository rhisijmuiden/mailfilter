# Web app: Python API (FastAPI) in a venv plus the React frontend, built on
# this server. Sourced by install.sh; uses its helpers and variables.
# shellcheck shell=bash disable=SC2034

MF_VAR=/var/lib/mailfilter
MF_VENV=$MF_VAR/venv

install_webapp() {
  if ! id mailfilter >/dev/null 2>&1; then
    run useradd --system --home-dir "$MF_VAR" --shell /usr/sbin/nologin mailfilter
  fi
  install -d -o mailfilter -g mailfilter -m 0750 "$MF_VAR"
  # The app reads its settings and secrets from /etc/mailfilter.
  chgrp mailfilter "$MF_ETC" "$MF_ETC/mailfilter.env" "$MF_ETC/secrets.env"
  # 0751: services such as Rspamd (user _rspamd) must reach their maps in here; the
  # secrets themselves stay 0640, and the directory cannot be listed by others.
  chmod 0751 "$MF_ETC"; chmod 0640 "$MF_ETC/mailfilter.env" "$MF_ETC/secrets.env"
  if ! grep -q '^APP_SECRET=' "$MF_ETC/secrets.env"; then echo "APP_SECRET=$(gen_secret 48)" >>"$MF_ETC/secrets.env"; fi

  info "Python packages for the web app"
  [[ -x $MF_VENV/bin/python ]] || run python3 -m venv "$MF_VENV"
  run "$MF_VENV/bin/pip" install --quiet --upgrade pip
  run "$MF_VENV/bin/pip" install --quiet --upgrade "$MF_SRC/backend"
  run "$MF_VENV/bin/mailfilter-admin" migrate
  ok "Database schema up to date"

  info "Building the web interface"
  (cd "$MF_SRC/frontend" && run npm ci --no-audit --no-fund --loglevel=error && run npm run build)
  ok "Web interface built"

  # The web app may run only the queue helper as root (quarantine release/block).
  local sudoers=/etc/sudoers.d/mailfilter
  printf '# Managed by Mailfilter.\nmailfilter ALL=(root) NOPASSWD: %s\n' "$MF_SRC/backend/bin/mf-queuectl" >"$sudoers.tmp"
  chmod 0440 "$sudoers.tmp"
  if visudo -cqf "$sudoers.tmp"; then mv "$sudoers.tmp" "$sudoers"; else rm -f "$sudoers.tmp"; warn "Could not install $sudoers"; fi
  chmod 0755 "$MF_SRC/backend/bin/mf-queuectl"

  install -m 0644 "$TPL/systemd/mailfilter-web.service" /etc/systemd/system/mailfilter-web.service
  run systemctl daemon-reload
  run systemctl enable mailfilter-web
  run systemctl restart mailfilter-web

  # Every minute: pause relay IPs and SMTP accounts that suddenly send spam or a flood of mail
  install -m 0644 "$TPL/systemd/mailfilter-abuse.service" /etc/systemd/system/mailfilter-abuse.service
  install -m 0644 "$TPL/systemd/mailfilter-abuse.timer" /etc/systemd/system/mailfilter-abuse.timer
  run systemctl daemon-reload
  run systemctl enable --now mailfilter-abuse.timer

  # Every minute: cluster nodes check each other and exchange configuration (idle on a single server)
  install -m 0644 "$TPL/systemd/mailfilter-cluster.service" /etc/systemd/system/mailfilter-cluster.service
  install -m 0644 "$TPL/systemd/mailfilter-cluster.timer" /etc/systemd/system/mailfilter-cluster.timer
  run systemctl daemon-reload
  run systemctl enable --now mailfilter-cluster.timer

  if ! "$MF_VENV/bin/mailfilter-admin" has-users; then
    local pw
    pw=$("$MF_VENV/bin/mailfilter-admin" create-admin "$ADMIN_EMAIL" --name Admin)
    install -m 0600 /dev/null "$MF_ETC/initial-admin-password"
    printf '%s\n' "$pw" >"$MF_ETC/initial-admin-password"
    WEB_ADMIN_PASSWORD=$pw
    ok "Admin account $ADMIN_EMAIL created"
  fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then ok "Web app running"; return; fi
    sleep 1
  done
  warn "The web app did not answer yet; check: journalctl -u mailfilter-web"
}
