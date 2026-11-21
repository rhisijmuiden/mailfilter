#!/usr/bin/env bash
# Build the Proxmox LXC template (CT template) of Mailfilter.
#
#   sudo appliance/build.sh [--branch main|test] [--out DIR]
#
# Needs Docker and about 10 GB of free disk. Starts a Debian 13 container with
# systemd, runs the normal installer in it (install.sh --image), removes
# everything that must be unique per server (appliance/generalize.sh) and packs
# the file system as mailfilter-<production|test>_amd64.tar.zst. In Proxmox it
# goes into the CT templates of a storage (Download from URL, or Upload).
set -Eeuo pipefail

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Run as root on a checkout owned by another user (CI): let git read it.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'
BRANCH=""; OUT=$SRC/appliance/out; KEEP=0
REPO_URL=${MF_REPO_URL:-https://github.com/rhisijmuiden/mailfilter.git}
while [[ $# -gt 0 ]]; do
  case $1 in
    --branch) BRANCH=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --keep) KEEP=1; shift ;;   # keep the build container for debugging
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo): unpacking the file system keeps its owners" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is needed" >&2; exit 1; }
command -v zstd >/dev/null || { echo "zstd is needed" >&2; exit 1; }

[[ -n $BRANCH ]] || BRANCH=$(git -C "$SRC" rev-parse --abbrev-ref HEAD)
[[ $BRANCH == main || $BRANCH == test ]] || { echo "The template follows main or test, not '$BRANCH' (use --branch)" >&2; exit 1; }
VERSION=$(head -1 "$SRC/VERSION")
CHANNEL=$([[ $BRANCH == main ]] && echo production || echo test)
# A fixed name per channel, so a "latest" download URL keeps working for every new version.
NAME=mailfilter-$CHANNEL
COMMIT=$(git -C "$SRC" rev-parse HEAD)
CT=mf-ct-build-$$
IMG=mf-ct-base-$$
WORK=$(mktemp -d /var/tmp/mf-ct.XXXXXX)
cleanup() {
  [[ $KEEP == 1 ]] || docker rm -f "$CT" >/dev/null 2>&1 || true
  docker rmi -f "$IMG" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Source: $COMMIT as branch $BRANCH (version $VERSION)"
# A clean clone of exactly this commit, on the branch the template follows, with GitHub as
# origin, so Info > Check for updates works from the first boot.
git clone --quiet --no-local "$SRC" "$WORK/mailfilter"
git -C "$WORK/mailfilter" checkout --quiet -B "$BRANCH" "$COMMIT"
git -C "$WORK/mailfilter" remote set-url origin "$REPO_URL"
git -C "$WORK/mailfilter" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
git -C "$WORK/mailfilter" update-ref "refs/remotes/origin/$BRANCH" "$COMMIT"
git -C "$WORK/mailfilter" branch --quiet --set-upstream-to="origin/$BRANCH" "$BRANCH"
# Branches other than the template's own stay out of the image.
git -C "$WORK/mailfilter" for-each-ref --format='%(refname)' refs/remotes/origin | grep -v "refs/remotes/origin/$BRANCH$" | xargs -r -n1 git -C "$WORK/mailfilter" update-ref -d
git -C "$WORK/mailfilter" for-each-ref --format='%(refname:short)' refs/heads | grep -vx "$BRANCH" | xargs -r git -C "$WORK/mailfilter" branch -q -D

say "Base system (Debian 13)"
docker build --pull -q -t "$IMG" -f "$SRC/appliance/Dockerfile" "$WORK"

say "Booting it with systemd"
docker run -d --name "$CT" --hostname mailfilter --privileged --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp -e container=docker "$IMG" /sbin/init >/dev/null
for _ in $(seq 60); do
  state=$(docker exec "$CT" systemctl is-system-running 2>/dev/null || true)
  [[ $state == running || $state == degraded ]] && break
  sleep 2
done
echo "systemd: ${state:-unknown}"

say "Installing Mailfilter (install.sh --image)"
if ! docker exec -e TERM=dumb "$CT" bash /opt/mailfilter/install/install.sh --image; then
  docker exec "$CT" tail -n 80 /var/log/mailfilter-install.log || true
  exit 1
fi

say "Removing everything that must differ per server"
docker exec "$CT" bash /opt/mailfilter/appliance/generalize.sh "$BRANCH" "$COMMIT"
docker stop -t 60 "$CT" >/dev/null

say "Packing the file system"
mkdir -p "$WORK/rootfs" "$OUT"
docker export "$CT" | tar -C "$WORK/rootfs" --numeric-owner -xpf -
R=$WORK/rootfs
# Docker leaves its own files; Proxmox writes hostname, hosts and resolv.conf at every start.
rm -f "$R/.dockerenv"
echo mailfilter >"$R/etc/hostname"
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost ip6-localhost ip6-loopback\nff02::1\t\tip6-allnodes\nff02::2\t\tip6-allrouters\n' >"$R/etc/hosts"
: >"$R/etc/resolv.conf"
rm -rf "${R:?}/tmp/"* "$R/run/"* 2>/dev/null || true
chmod 1777 "$R/tmp"
TAR=$OUT/${NAME}_amd64.tar.zst
tar --numeric-owner --xattrs -C "$R" -cf - . | zstd -q -T0 -19 -f -o "$TAR"
(cd "$OUT" && sha256sum "$(basename "$TAR")" >"$(basename "$TAR").sha256")
say "Done: $TAR ($(du -h "$TAR" | cut -f1))"
