# Environment for the screenshot demo. Source it: . tools/screenshots/env.sh
# Needs an empty PostgreSQL database (default: port 5433, user mailfilter/dev, database mailfilter).
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export DEMO_DIR=${DEMO_DIR:-/var/tmp/mfdemo}
mkdir -p "$DEMO_DIR"/etc "$DEMO_DIR"/lists "$DEMO_DIR"/mailtrap "$DEMO_DIR"/archive
[ -f "$DEMO_DIR/etc/version" ] || { echo 1.0.0 > "$DEMO_DIR/etc/version"; echo main > "$DEMO_DIR/etc/branch"; }
[ -f "$DEMO_DIR/queue.json" ] || echo '{"hold":[],"messages":{}}' > "$DEMO_DIR/queue.json"
export MAILFILTER_DB_URL=${MAILFILTER_DB_URL:-postgresql+psycopg://mailfilter:dev@127.0.0.1:5433/mailfilter}
export MF_COOKIE_SECURE=0 APP_SECRET=demo-secret-demo-secret MF_ETC=$DEMO_DIR/etc MF_SOURCE=$REPO
export MF_HOSTNAME=mailfilter01.example.net MF_ADMIN_EMAIL=admin@example.net MF_PUBLIC_V4=203.0.113.10 MF_PUBLIC_V6=2001:db8:10::25
export MF_LISTS_DIR=$DEMO_DIR/lists MF_MAILTRAP_DIR=$DEMO_DIR/mailtrap MF_ARCHIVE_DIR=$DEMO_DIR/archive
export MF_QUEUE_HELPER=$REPO/tools/screenshots/fakes/mf-queuectl FAKE_QUEUE=$DEMO_DIR/queue.json
export MF_CTL_COMMAND=$REPO/tools/screenshots/fakes/mailfilterctl
export MF_CONFIG_HELPER=$REPO/tools/screenshots/fakes/mailfilter-config
