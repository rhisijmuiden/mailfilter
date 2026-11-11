# Shared helpers for the Mailfilter installer. Sourced by install.sh.
# shellcheck shell=bash disable=SC2034

MF_ETC=/etc/mailfilter
MF_LOG=/var/log/mailfilter-install.log

if [[ -t 1 ]]; then
  C_RESET=$'\e[0m'; C_BOLD=$'\e[1m'; C_DIM=$'\e[2m'
  C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_ACC=$'\e[38;5;209m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_OK=""; C_WARN=""; C_ERR=""; C_ACC=""
fi

STEP_NO=0
step() { STEP_NO=$((STEP_NO + 1)); printf '\n%s[%02d]%s %s%s%s\n' "$C_ACC" "$STEP_NO" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; log "STEP $STEP_NO: $*"; }
info() { printf '     %s\n' "$*"; log "INFO: $*"; }
ok()   { printf '     %s✓%s %s\n' "$C_OK" "$C_RESET" "$*"; log "OK: $*"; }
warn() { printf '     %s!%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; log "WARN: $*"; }
die()  { printf '\n%s✗ %s%s\n' "$C_ERR" "$*" "$C_RESET" >&2; log "FATAL: $*"; exit 1; }
log()  { printf '%s %s\n' "$(date -Is)" "$*" >>"$MF_LOG" 2>/dev/null || true; }

# run CMD...: runs a command, sends its output to the install log, dies with a hint on failure.
run() {
  log "RUN: $*"
  if ! "$@" >>"$MF_LOG" 2>&1; then
    die "Command failed: $* (see $MF_LOG)"
  fi
}

# ask VAR "Question" "default": prompt unless the variable is already set or we are non-interactive.
ask() {
  local var=$1 question=$2 default=${3:-} answer
  if [[ -n ${!var:-} ]]; then return; fi
  if [[ ${NONINTERACTIVE:-0} == 1 ]]; then
    [[ -n $default ]] || die "Missing required value for $var (use the matching --option)"
    printf -v "$var" '%s' "$default"; return
  fi
  read -r -p "     $question${default:+ [$default]}: " answer
  printf -v "$var" '%s' "${answer:-$default}"
}

# render TEMPLATE DEST [MODE]: copy a template, replacing {{NAME}} with the value of $NAME.
render() {
  local src=$1 dest=$2 mode=${3:-0644} content name value
  [[ -f $src ]] || die "Template not found: $src"
  content=$(<"$src")
  while [[ $content =~ \{\{([A-Z0-9_]+)\}\} ]]; do
    name=${BASH_REMATCH[1]}
    [[ -v $name ]] || die "Template $src uses {{$name}} but it is not set"
    value=${!name}
    content=${content//\{\{$name\}\}/$value}
  done
  install -D -m "$mode" /dev/null "$dest"
  printf '%s\n' "$content" >"$dest"
  log "RENDER: $src -> $dest"
}

# backup_file PATH: keep the original config the first time we overwrite it.
backup_file() {
  local f=$1
  if [[ -f $f && ! -f $f.mailfilter-orig ]]; then cp -a "$f" "$f.mailfilter-orig"; fi
}

gen_secret() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-32}"; }

is_ipv4() { [[ $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ ]]; }
is_ipv6() { [[ $1 == *:* && $1 =~ ^[0-9a-fA-F:]+(/[0-9]{1,3})?$ ]]; }
