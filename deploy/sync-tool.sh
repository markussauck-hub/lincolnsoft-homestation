#!/usr/bin/env bash
# Lincolnsoft Server – Tool aus dem Branch "deploy" eines GitHub-Repos nach ~/server/site/<pfad> spiegeln.
# Der Branch wird von der GitHub Action des Tools gebaut (inkl. Daten-JSONs) und enthält nur die fertige Seite.
#
#   sync-tool.sh <github-repo> <pfad>          z. B.  sync-tool.sh markussauck-hub/eve-price-checker price-checker
#   sync-tool.sh <github-repo> <pfad> --force  erzwingt das Kopieren
#   BRANCH=main sync-tool.sh <github-repo> <pfad>   spiegelt einen anderen Branch (Standard: deploy)
#
# Läuft per Cron jede Minute, tut aber nur etwas, wenn es einen neuen Stand gibt.
set -euo pipefail

REPO_SLUG="${1:?Repo fehlt (z. B. markussauck-hub/eve-price-checker)}"
SUBPATH="${2:?Zielpfad fehlt (z. B. price-checker)}"
FORCE="${3:-}"

# Sicherheitsnetz: Zielpfad nur aus a-z, 0-9 und "-" (verhindert, dass z. B. "." oder "" den Webroot leert)
[[ "$SUBPATH" =~ ^[a-z0-9-]+$ ]] || { echo "Ungültiger Zielpfad: '$SUBPATH'" >&2; exit 1; }

NAME="${REPO_SLUG##*/}"
CACHE="$HOME/repos/deploy-cache/$NAME"
TARGET="$HOME/server/site/$SUBPATH"
BRANCH="${BRANCH:-deploy}"          # anderer Branch per Umgebungsvariable, z. B. BRANCH=main

mkdir -p "$HOME/repos/deploy-cache"

# Parallelläufe verhindern
exec 9>"$HOME/repos/deploy-cache/.$NAME.lock"
flock -n 9 || exit 0

if [ ! -d "$CACHE/.git" ]; then
    git clone -q --depth 1 --branch "$BRANCH" "https://github.com/$REPO_SLUG.git" "$CACHE"
    FORCE="--force"
fi

cd "$CACHE"
git fetch -q --depth 1 origin "$BRANCH"

if [ "$(git rev-parse HEAD)" != "$(git rev-parse FETCH_HEAD)" ] || [ "$FORCE" = "--force" ]; then
    git reset -q --hard FETCH_HEAD
    mkdir -p "$TARGET"
    # in Zielordner kopieren, ohne .git; alte Dateien, die es nicht mehr gibt, entfernen
    find "$TARGET" -mindepth 1 -delete
    tar --exclude=.git -cf - . | tar -xf - -C "$TARGET"
    echo "$(date -Is) $NAME -> /$SUBPATH/: $(git log -1 --pretty=%s)"
fi
