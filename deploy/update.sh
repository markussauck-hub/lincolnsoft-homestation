#!/usr/bin/env bash
# Lincolnsoft Homestation – Auto-Deploy auf dem Lincolnsoft Server
# Holt neue Commits von GitHub und kopiert site/ nach ~/server/site (Caddy-Webroot).
# Läuft per Cron jede Minute, tut aber nur etwas, wenn es neue Commits gibt.
set -euo pipefail

REPO="$HOME/repos/lincolnsoft-homestation"
TARGET="$HOME/server/site"
BRANCH="main"

# Parallelläufe verhindern
exec 9>"$REPO/.deploy.lock"
flock -n 9 || exit 0

cd "$REPO"
git fetch --quiet origin "$BRANCH"

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" != "$REMOTE" ] || [ "${1:-}" = "--force" ]; then
    git reset --hard --quiet "origin/$BRANCH"
    mkdir -p "$TARGET"
    cp -a site/. "$TARGET/"
    echo "$(date -Is) deployed $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
fi
