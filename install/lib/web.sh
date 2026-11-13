# How the web interface is reached, the firewall and the HTTPS certificate.
# Sourced by install.sh and install/web-access.sh (System > Web access).
# shellcheck shell=bash disable=SC2034
#
# Modes (--web-mode, saved as MF_WEB_MODE in mailfilter.env):
#   public    directly on the internet: HTTPS with a Let's Encrypt certificate (default)
#   internal  behind a firewall or in a DMZ: HTTPS with Caddy's own certificate
#             (or --web-cert/--web-key), no Let's Encrypt request
#   proxy     behind a reverse proxy that does HTTPS: plain HTTP on --web-port,
#             only reachable from --trusted-proxy, whose X-Forwarded-* headers are trusted
#   http      plain HTTP without encryption, for a closed management network only

WEB_MODES=(public internal proxy http)
WEB_CERT_FILE=/etc/mailfilter/web-cert.pem
WEB_KEY_FILE=/etc/mailfilter/web-key.pem

# split_nets "a b,c" -> prints one network per line ("-" and empty mean none)
split_nets() {
  local x
  # shellcheck disable=SC2086  # splitting on spaces and commas is the point
  for x in ${1//,/ }; do [[ $x == - ]] || printf '%s\n' "$x"; done
}

# ask_web_mode: choose the mode (interactive menu unless given or non-interactive) and its details.
ask_web_mode() {
  local answer nets
  if [[ -z ${WEB_MODE:-} ]]; then
    if [[ ${NONINTERACTIVE:-0} == 1 ]]; then
      WEB_MODE=public
    else
      cat <<'EOF'
     How is the web interface (management) reached?
       1) Directly on the internet: HTTPS with a free Let's Encrypt certificate (recommended)
       2) Behind a firewall or in a DMZ: HTTPS with a self-made certificate, no Let's Encrypt
       3) Behind a reverse proxy: the proxy does HTTPS, Mailfilter answers it over HTTP
       4) Plain HTTP, no encryption (only on a closed management network)
EOF
      read -r -p "     Choice [1]: " answer
      case ${answer:-1} in
        1) WEB_MODE=public ;; 2) WEB_MODE=internal ;; 3) WEB_MODE=proxy ;; 4) WEB_MODE=http ;;
        *) die "Choose 1, 2, 3 or 4" ;;
      esac
    fi
  fi
  if [[ $WEB_MODE == proxy && ${#TRUSTED_PROXIES[@]} -eq 0 && ${NONINTERACTIVE:-0} == 0 ]]; then
    read -r -p "     IP address or network of the reverse proxy (more: separate with spaces): " nets
    mapfile -t TRUSTED_PROXIES < <(split_nets "$nets")
  fi
  if [[ $WEB_MODE == proxy && -z ${WEB_URL:-} && ${NONINTERACTIVE:-0} == 0 ]]; then
    ask WEB_URL "Address users open in the browser (on the proxy)" "https://$HOSTNAME_FQDN"
  fi
  if [[ ($WEB_MODE == internal || $WEB_MODE == http) && ${#WEB_FROM[@]} -eq 0 && ${NONINTERACTIVE:-0} == 0 && ${WEB_FROM_ASKED:-0} == 0 ]]; then
    read -r -p "     Allow the web interface only from (networks, separated by spaces; empty = everyone): " nets
    mapfile -t WEB_FROM < <(split_nets "$nets")
  fi
  web_defaults
}

# web_defaults: fill in what was not chosen and check everything.
web_defaults() {
  local n
  [[ " ${WEB_MODES[*]} " == *" ${WEB_MODE:-} "* ]] || die "Unknown web mode '${WEB_MODE:-}' (public, internal, proxy or http)"
  if [[ -z ${WEB_PORT:-} ]]; then
    case $WEB_MODE in public|internal) WEB_PORT=443 ;; proxy) WEB_PORT=8080 ;; http) WEB_PORT=80 ;; esac
  fi
  [[ $WEB_PORT =~ ^[0-9]+$ && $WEB_PORT -ge 1 && $WEB_PORT -le 65535 ]] || die "Not a valid web port: $WEB_PORT"
  if [[ $WEB_MODE == public || $WEB_MODE == internal ]]; then
    WEB_PORT=443  # HTTPS modes always use 443 (and 80 for the redirect)
  else
    case $WEB_PORT in 25|465|587|993|8000|11332|11333|11334) die "Port $WEB_PORT is used by Mailfilter itself" ;; esac
  fi
  if [[ $WEB_MODE == proxy ]]; then
    [[ ${#TRUSTED_PROXIES[@]} -gt 0 ]] || die "Behind a reverse proxy: give its address with --trusted-proxy"
  else
    TRUSTED_PROXIES=()
  fi
  [[ $WEB_MODE == public || $WEB_MODE == proxy ]] && WEB_FROM=()
  for n in "${TRUSTED_PROXIES[@]}" "${WEB_FROM[@]}"; do is_ipv4 "$n" || is_ipv6 "$n" || die "Not a valid IP address or network: $n"; done
  # Only behind a reverse proxy can the address differ from the host name.
  case $WEB_MODE in
    public|internal) WEB_URL="https://$HOSTNAME_FQDN" ;;
    http) WEB_URL="http://$HOSTNAME_FQDN"; [[ $WEB_PORT == 80 ]] || WEB_URL+=":$WEB_PORT" ;;
    proxy) [[ -n ${WEB_URL:-} ]] || WEB_URL="https://$HOSTNAME_FQDN" ;;
  esac
  local url_re='^https?://[^/[:space:]"]+(/[^[:space:]"]*)?$'
  [[ $WEB_URL =~ $url_re ]] || die "Not a valid web address: $WEB_URL"
  WEB_URL=${WEB_URL%/}
  COOKIE_SECURE=1; [[ $WEB_URL == http://* ]] && COOKIE_SECURE=0
  if [[ ${WEB_MODE_WARNED:-0} == 0 ]]; then
    WEB_MODE_WARNED=1
    case $WEB_MODE in
      http) warn "The web interface uses plain HTTP: passwords travel unencrypted. Only use this on a closed management network." ;;
      internal) info "The web interface uses a self-made certificate: browsers warn once until you trust it." ;;
    esac
    case $WEB_MODE in
      internal|http) warn "Not reachable over HTTPS from the internet: MTA-STS policies, BIMI logos, link protection and the buttons in quarantine report mails will not work from outside." ;;
    esac
  fi
}

# web_env_lines: the web settings for mailfilter.env.
web_env_lines() {
  cat <<EOF
MF_WEB_MODE=$WEB_MODE
MF_WEB_PORT=$WEB_PORT
MF_WEB_URL=$WEB_URL
MF_TRUSTED_PROXIES=${TRUSTED_PROXIES[*]}
MF_WEB_FROM=${WEB_FROM[*]}
MF_COOKIE_SECURE=$COOKIE_SECURE
EOF
}

# load_saved_web: the web settings of the previous install (mailfilter.env), unless given.
load_saved_web() {
  local f=$MF_ETC/mailfilter.env
  [[ -f $f ]] || return 0
  sv() { sed -n "s/^$1=//p" "$f" | tail -1; }
  [[ -n ${WEB_MODE:-} ]] || WEB_MODE=$(sv MF_WEB_MODE)
  [[ -n ${WEB_MODE:-} ]] || WEB_MODE=public  # installs from before this option
  [[ -n ${WEB_PORT:-} ]] || WEB_PORT=$(sv MF_WEB_PORT)
  [[ -n ${WEB_URL:-} ]] || WEB_URL=$(sv MF_WEB_URL)
  [[ ${#TRUSTED_PROXIES[@]} -gt 0 || ${TRUSTED_PROXIES_SET:-0} == 1 ]] || mapfile -t TRUSTED_PROXIES < <(split_nets "$(sv MF_TRUSTED_PROXIES)")
  [[ ${#WEB_FROM[@]} -gt 0 || ${WEB_FROM_SET:-0} == 1 ]] || mapfile -t WEB_FROM < <(split_nets "$(sv MF_WEB_FROM)")
  WEB_FROM_ASKED=1
}

# nft_accept PORTS COMMENT NETS...: accept rules for these ports, from everyone or from NETS only.
nft_accept() {
  local ports=$1 comment=$2; shift 2
  local v4=() v6=() n IFS_OLD
  if [[ $# -eq 0 ]]; then
    printf '    tcp dport { %s } accept comment "%s"\n' "$ports" "$comment"
    return
  fi
  for n in "$@"; do if is_ipv6 "$n"; then v6+=("$n"); else v4+=("$n"); fi; done
  IFS_OLD=$IFS; IFS=,
  [[ ${#v4[@]} -gt 0 ]] && printf '    ip saddr { %s } tcp dport { %s } accept comment "%s"\n' "${v4[*]}" "$ports" "$comment"
  [[ ${#v6[@]} -gt 0 ]] && printf '    ip6 saddr { %s } tcp dport { %s } accept comment "%s"\n' "${v6[*]}" "$ports" "$comment"
  IFS=$IFS_OLD
}

# web_firewall_rules: WEB_RULES for templates/nftables/nftables.conf.
web_firewall_rules() {
  case $WEB_MODE in
    public)   WEB_RULES=$(nft_accept "80, 443" "Web interface, Let's Encrypt and MTA-STS") ;;
    internal) WEB_RULES=$(nft_accept "80, 443" "Web interface (HTTPS, own certificate)" "${WEB_FROM[@]}") ;;
    proxy)    WEB_RULES=$(nft_accept "$WEB_PORT" "Web interface from the reverse proxy" "${TRUSTED_PROXIES[@]}") ;;
    http)     WEB_RULES=$(nft_accept "$WEB_PORT" "Web interface (plain HTTP)" "${WEB_FROM[@]}") ;;
  esac
}

# configure_firewall: nftables with mail ports, the web interface (per mode) and SSH.
configure_firewall() {
  local n cur
  MGMT_V4=(); MGMT_V6=()
  for n in "${SSH_FROM[@]}"; do if is_ipv6 "$n"; then MGMT_V6+=("$n"); else MGMT_V4+=("$n"); fi; done
  MGMT_V4_ELEMENTS=""; MGMT_V6_ELEMENTS=""
  [[ ${#MGMT_V4[@]} -gt 0 ]] && MGMT_V4_ELEMENTS="elements = { $(IFS=,; echo "${MGMT_V4[*]}") }"
  [[ ${#MGMT_V6[@]} -gt 0 ]] && MGMT_V6_ELEMENTS="elements = { $(IFS=,; echo "${MGMT_V6[*]}") }"
  if [[ ${#SSH_FROM[@]} -gt 0 ]]; then
    SSH_RULES=$'    ip saddr @mgmt_v4 tcp dport 22 accept comment "SSH from management"\n    ip6 saddr @mgmt_v6 tcp dport 22 accept comment "SSH from management"'
    # Never lock out the session running this installer
    if [[ -n ${SSH_CLIENT:-} ]]; then
      cur=${SSH_CLIENT%% *}
      if is_ipv6 "$cur"; then SSH_RULES+=$'\n'"    ip6 saddr $cur tcp dport 22 accept comment \"Installer session\""
      else SSH_RULES+=$'\n'"    ip saddr $cur tcp dport 22 accept comment \"Installer session\""; fi
    fi
  else
    SSH_RULES='    tcp dport 22 accept comment "SSH (open, guarded by CrowdSec)"'
  fi
  web_firewall_rules
  render "$TPL/nftables/nftables.conf" /etc/nftables.conf 0755
  run nft -c -f /etc/nftables.conf
  run systemctl enable nftables
  run systemctl restart nftables
}

# web_certificate: the Let's Encrypt certificate, only when directly on the internet.
# Sets HAVE_CERT (1 when a Let's Encrypt certificate is there).
web_certificate() {
  local resolved renewal
  install -d -m 0755 /var/www/acme
  if [[ $WEB_MODE != public ]]; then
    info "No Let's Encrypt certificate needed ($WEB_MODE)."
  elif [[ ${SKIP_CERT:-0} == 1 ]]; then
    warn "Skipped. Run the installer again without --skip-certificate once DNS points here."
  elif [[ -f /etc/letsencrypt/live/$HOSTNAME_FQDN/fullchain.pem ]]; then
    ok "Certificate already present"
  else
    resolved=$(dig +short A "$HOSTNAME_FQDN" @127.0.0.1 | tail -1)
    [[ -n ${PUBLIC_V4:-} && $resolved != "$PUBLIC_V4" ]] && warn "$HOSTNAME_FQDN resolves to '${resolved:-nothing}', this server is $PUBLIC_V4. The certificate request will likely fail."
    render "$TPL/caddy/Caddyfile.acme" /etc/caddy/Caddyfile
    run systemctl restart caddy
    run certbot certonly --webroot -w /var/www/acme -d "$HOSTNAME_FQDN" \
        --email "$ADMIN_EMAIL" --agree-tos --no-eff-email --non-interactive --key-type ecdsa --reuse-key
    ok "Certificate issued for $HOSTNAME_FQDN"
  fi
  # DANE: the TLSA record holds the hash of the key, so renewals must keep the same key
  renewal=/etc/letsencrypt/renewal/$HOSTNAME_FQDN.conf
  if [[ -f $renewal ]] && ! grep -q '^reuse_key *= *True' "$renewal"; then
    sed -i '/^reuse_key/d; /^\[renewalparams\]/a reuse_key = True' "$renewal"
    ok "Certificate renewals keep the same key (needed for DANE)"
  fi
  install -D -m 0755 "$TPL/certbot/deploy-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/mailfilter.sh
  if [[ -d /etc/letsencrypt/live ]]; then /etc/letsencrypt/renewal-hooks/deploy/mailfilter.sh 2>/dev/null || true; fi
  HAVE_CERT=0; [[ -f /etc/letsencrypt/live/$HOSTNAME_FQDN/fullchain.pem ]] && HAVE_CERT=1
  return 0
}

# web_caddyfile_vars: CADDY_GLOBAL, APP_PROXY and WEB_SITES for templates/caddy/Caddyfile.
web_caddyfile_vars() {
  local names_http names_https tls ip
  APP_PROXY="reverse_proxy 127.0.0.1:8000"
  case $WEB_MODE in
    public)
      CADDY_GLOBAL=$'\t# The certificate for the web interface comes from certbot (Postfix uses the same\n\t# files). Caddy only gets certificates itself for mta-sts.<domain> names, on\n\t# demand and only for relay domains (the web app answers the "ask").\n\tauto_https disable_redirects\n\ton_demand_tls {\n\t\task http://127.0.0.1:8000/api/mta-sts/allowed\n\t}'
      WEB_SITES=$(cat <<EOF
http://$HOSTNAME_FQDN {
	handle /.well-known/acme-challenge/* {
		root * /var/www/acme
		file_server
	}
	handle {
		redir https://{host}{uri} permanent
	}
}

https://$HOSTNAME_FQDN {
	tls /etc/letsencrypt/live/$HOSTNAME_FQDN/fullchain.pem /etc/letsencrypt/live/$HOSTNAME_FQDN/privkey.pem {
		protocols tls1.2 tls1.3
	}
	header Strict-Transport-Security "max-age=63072000; includeSubDomains"
	import mailfilter_app
}

# MTA-STS policies: https://mta-sts.<domain>/.well-known/mta-sts.txt
https:// {
	tls {
		on_demand
		protocols tls1.2 tls1.3
	}
	header -Server
	handle /.well-known/mta-sts.txt {
		rewrite * /api/mta-sts/policy
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		respond 404
	}
}
EOF
)
      ;;
    internal)
      CADDY_GLOBAL=$'\tauto_https disable_redirects'
      names_http="http://$HOSTNAME_FQDN"; names_https="https://$HOSTNAME_FQDN"
      for ip in ${PUBLIC_V4:-}; do names_http+=", http://$ip"; names_https+=", https://$ip"; done
      for ip in ${PUBLIC_V6:-}; do names_http+=", http://[$ip]"; names_https+=", https://[$ip]"; done
      if [[ -s $WEB_CERT_FILE && -s $WEB_KEY_FILE ]]; then
        tls=$'\ttls '"$WEB_CERT_FILE $WEB_KEY_FILE"$' {\n\t\tprotocols tls1.2 tls1.3\n\t}'
      else
        # Caddy makes its own certificate authority (no internet needed); browsers warn until it is trusted.
        tls=$'\ttls {\n\t\tissuer internal\n\t\tprotocols tls1.2 tls1.3\n\t}'
      fi
      WEB_SITES=$(cat <<EOF
$names_http {
	redir https://{host}{uri} permanent
}

$names_https {
$tls
	import mailfilter_app
}
EOF
)
      ;;
    proxy)
      CADDY_GLOBAL=$'\tauto_https off'
      # The reverse proxy in front: its X-Forwarded-For and X-Forwarded-Proto are passed on
      # (Caddy 2.6 in Debian 13 has trusted_proxies on reverse_proxy, not on the server).
      APP_PROXY=$'reverse_proxy 127.0.0.1:8000 {\n\t\t\ttrusted_proxies '"${TRUSTED_PROXIES[*]}"$'\n\t\t}'
      WEB_SITES=$(printf 'http://:%s {\n\timport mailfilter_app\n}' "$WEB_PORT")
      ;;
    http)
      CADDY_GLOBAL=$'\tauto_https off'
      WEB_SITES=$(printf '# Plain HTTP: only on a closed management network.\nhttp://:%s {\n\timport mailfilter_app\n}' "$WEB_PORT")
      ;;
  esac
}

# configure_web: the Caddyfile and uvicorn's trusted proxies; restarts Caddy (and the web app when asked).
configure_web() {
  # Own certificate for internal mode (--web-cert/--web-key); Caddy runs as user caddy.
  if [[ -n ${WEB_CERT_IN:-} && -n ${WEB_KEY_IN:-} ]]; then
    install -m 0644 "$WEB_CERT_IN" "$WEB_CERT_FILE"
    install -m 0640 -g caddy "$WEB_KEY_IN" "$WEB_KEY_FILE"
    ok "Own certificate installed"
  fi
  web_caddyfile_vars
  render "$TPL/caddy/Caddyfile" /etc/caddy/Caddyfile
  run caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  # uvicorn takes X-Forwarded-* only from Caddy, plus the reverse proxy in proxy mode.
  local allow=127.0.0.1
  [[ ${#TRUSTED_PROXIES[@]} -gt 0 ]] && allow+=",$(IFS=,; echo "${TRUSTED_PROXIES[*]}")"
  printf '# Managed by Mailfilter (web access).\nFORWARDED_ALLOW_IPS=%s\n' "$allow" >"$MF_ETC/web.env"
  chmod 0644 "$MF_ETC/web.env"
  run systemctl enable caddy
  run systemctl restart caddy
  ok "Web interface: $WEB_URL ($WEB_MODE)"
}

# set_env_key FILE KEY VALUE: replace or add KEY=VALUE.
set_env_key() {
  local f=$1 k=$2 v=$3
  if grep -q "^$k=" "$f"; then
    local tmp; tmp=$(mktemp)
    awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$f" >"$tmp"
    cat "$tmp" >"$f"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >>"$f"
  fi
}
