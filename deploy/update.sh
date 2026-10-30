#!/usr/bin/env bash
# Lincolnsoft Homestation – Auto-Deploy auf dem Lincolnsoft Server
# Läuft per Cron jede Minute. Tut nur etwas, wenn es etwas zu tun gibt:
#   1. neue Commits auf main  -> site/ nach ~/server/site kopieren
#   2. deploy/server/Caddyfile bzw. docker-compose.yml geändert -> prüfen, sichern, übernehmen
#   3. Kontakt-Dienst (deploy/kontakt) geändert -> Container "api" neu starten
#   4. alle 30 Min. das Stationslogbuch (changelog.json) aktualisieren
#   4b. stündlich die Gratis-Spiele (free-games.json) aktualisieren
#   5. RimWorld-News einmalig sofort holen, solange es news-rimworld.json noch nicht gibt
#   6. Tools aus deploy/tools.list per sync-tool.sh spiegeln (z. B. /asset-radar/)
#
#   update.sh           normaler Lauf
#   update.sh --force   site/ auf jeden Fall kopieren
set -euo pipefail

REPO="$HOME/repos/lincolnsoft-homestation"
TARGET="$HOME/server/site"
SERVER="$HOME/server"
STATE="$HOME/repos/.homestation-deploy"
BRANCH="main"

log() { echo "$(date -Is) $*"; }

# Prüft eine neue Server-Datei, sichert die alte und übernimmt sie.
# Schlägt die Prüfung fehl, bleibt alles beim Alten; dieselbe kaputte Version wird nicht jede Minute erneut versucht.
sync_compose() {
    local src="$REPO/deploy/server/docker-compose.yml" dst="$SERVER/docker-compose.yml"
    [ -f "$src" ] || return 0
    cmp -s "$src" "$dst" && return 0
    local h; h="$(sha1sum "$src" | cut -c1-12)"
    [ "$(cat "$STATE/compose.rejected" 2>/dev/null)" = "$h" ] && return 0
    # Konfig-Dateien der Dienste müssen existieren (leer = Funktion "noch nicht aktiv")
    mkdir -p "$HOME/.config/lincolnsoft"
    [ -f "$HOME/.config/lincolnsoft/kontakt.env" ] || install -m 600 /dev/null "$HOME/.config/lincolnsoft/kontakt.env"
    # Gemini-Schlüssel für den Foto-Coach-Proxy in Caddy (leer = Proxy antwortet mit Fehler von Google)
    [ -f "$HOME/.config/lincolnsoft/gemini.env" ] || install -m 600 /dev/null "$HOME/.config/lincolnsoft/gemini.env"
    # Admin-Schlüssel fürs Steelrumble-Vorschlagsboard (leer = Board ohne Freigaben)
    [ -f "$HOME/.config/lincolnsoft/steelrumble.env" ] || install -m 600 /dev/null "$HOME/.config/lincolnsoft/steelrumble.env"
    mkdir -p "$SERVER/data/steelrumble"
    cp "$src" "$SERVER/.docker-compose.new.yml"
    if ! (cd "$SERVER" && docker compose -f .docker-compose.new.yml config -q) 2>"$STATE/compose.err"; then
        log "docker-compose.yml NICHT übernommen (Prüfung fehlgeschlagen): $(tr '\n' ' ' < "$STATE/compose.err")"
        echo "$h" > "$STATE/compose.rejected"; rm -f "$SERVER/.docker-compose.new.yml"; return 0
    fi
    cp "$dst" "$dst.bak" 2>/dev/null || true
    mv "$SERVER/.docker-compose.new.yml" "$dst"
    if (cd "$SERVER" && docker compose up -d --quiet-pull) >"$STATE/compose.out" 2>&1; then
        log "docker-compose.yml übernommen, Container aktualisiert"
    else
        log "docker compose up FEHLER – stelle alte Version wieder her: $(tail -3 "$STATE/compose.out" | tr '\n' ' ')"
        cp "$dst.bak" "$dst"; (cd "$SERVER" && docker compose up -d) >/dev/null 2>&1 || true
        echo "$h" > "$STATE/compose.rejected"
    fi
}

sync_caddy() {
    local src="$REPO/deploy/server/Caddyfile" dst="$SERVER/Caddyfile"
    [ -f "$src" ] || return 0
    cmp -s "$src" "$dst" && return 0
    local h; h="$(sha1sum "$src" | cut -c1-12)"
    [ "$(cat "$STATE/caddy.rejected" 2>/dev/null)" = "$h" ] && return 0
    if ! docker run --rm -v "$src:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >"$STATE/caddy.err" 2>&1; then
        log "Caddyfile NICHT übernommen (Prüfung fehlgeschlagen): $(grep -iv '"level":"info"' "$STATE/caddy.err" | tail -2 | tr '\n' ' ')"
        echo "$h" > "$STATE/caddy.rejected"; return 0
    fi
    cp "$dst" "$dst.bak" 2>/dev/null || true
    cat "$src" > "$dst"            # Inhalt ersetzen statt Datei tauschen: der Container sieht so die neue Version
    if (cd "$SERVER" && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile) >"$STATE/caddy.out" 2>&1; then
        log "Caddyfile übernommen und neu geladen"
    else
        log "Caddy reload FEHLER – stelle alte Version wieder her: $(tail -2 "$STATE/caddy.out" | tr '\n' ' ')"
        cat "$dst.bak" > "$dst"; (cd "$SERVER" && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile) >/dev/null 2>&1 || true
        echo "$h" > "$STATE/caddy.rejected"
    fi
}

restart_api_if_changed() {
    [ -d "$REPO/deploy/kontakt" ] || return 0
    local h; h="$(cat "$REPO"/deploy/kontakt/*.py | sha1sum | cut -c1-12)"
    [ "$(cat "$STATE/api.hash" 2>/dev/null)" = "$h" ] && return 0
    if (cd "$SERVER" && docker compose ps --status running --services 2>/dev/null | grep -qx api); then
        (cd "$SERVER" && docker compose restart api) >/dev/null 2>&1 && log "Kontakt-Dienst (api) neu gestartet"
    fi
    echo "$h" > "$STATE/api.hash"
}

# alle 30 Minuten, im Hintergrund (darf den Deploy nicht aufhalten)
changelog() {
    [ -x "$REPO/deploy/changelog.py" ] || return 0
    if [ -z "$(find "$STATE/changelog.stamp" -mmin -30 2>/dev/null)" ]; then
        touch "$STATE/changelog.stamp"
        nohup "$REPO/deploy/changelog.py" >> "$HOME/repos/changelog.log" 2>&1 &
    fi
}

# stündlich, im Hintergrund
freegames() {
    [ -x "$REPO/deploy/freegames.py" ] || return 0
    if [ -z "$(find "$STATE/freegames.stamp" -mmin -60 2>/dev/null)" ]; then
        touch "$STATE/freegames.stamp"
        nohup "$REPO/deploy/freegames.py" >> "$HOME/repos/freegames.log" 2>&1 &
    fi
}

# RimWorld-Kanal: beim ersten Mal nicht bis zum nächsten 3-Stunden-Lauf warten (genau ein Versuch)
rimworld_first_run() {
    [ -f "$TARGET/news-rimworld.json" ] && return 0
    [ -f "$HOME/.config/lincolnsoft/news.env" ] || return 0
    [ -f "$STATE/rimworld.init" ] && return 0
    touch "$STATE/rimworld.init"
    log "RimWorld-News: erster Lauf gestartet"
    nohup python3 "$REPO/news/news.py" --channel rimworld >> "$HOME/repos/news.log" 2>&1 &
}

main() {
    mkdir -p "$STATE"
    # Parallelläufe verhindern
    exec 9>"$REPO/.deploy.lock"
    flock -n 9 || exit 0

    cd "$REPO"
    git fetch --quiet origin "$BRANCH"
    local LOCAL REMOTE
    LOCAL="$(git rev-parse HEAD)"
    REMOTE="$(git rev-parse "origin/$BRANCH")"

    if [ "$LOCAL" != "$REMOTE" ] || [ "${1:-}" = "--force" ]; then
        local SELF_BEFORE; SELF_BEFORE="$(sha1sum deploy/update.sh | cut -c1-12)"
        git reset --hard --quiet "origin/$BRANCH"
        mkdir -p "$TARGET"
        cp -a site/. "$TARGET/"
        log "deployed $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
        # update.sh selbst geändert? Dann den Rest (Compose, Caddy …) gleich mit dem NEUEN Code erledigen –
        # sonst läuft dieser Durchgang noch mit der alten, bereits eingelesenen Version.
        if [ -z "${UPDATE_REEXEC:-}" ] && [ "$(sha1sum deploy/update.sh | cut -c1-12)" != "$SELF_BEFORE" ]; then
            log "update.sh geändert – mache mit der neuen Version weiter"
            exec 9>&-
            UPDATE_REEXEC=1 exec "$REPO/deploy/update.sh"
        fi
    fi

    # Server-Konfiguration: erst Container (der Kontakt-Dienst muss existieren), dann Caddy
    sync_compose
    sync_caddy
    restart_api_if_changed
    changelog
    freegames
    rimworld_first_run
    sync_tools
}

# Tools aus deploy/tools.list spiegeln – im Hintergrund, damit ein langsamer GitHub-Abruf den Deploy nicht aufhält.
# sync-tool.sh sperrt selbst gegen Parallelläufe und kopiert nur, wenn es einen neuen Stand gibt.
sync_tools() {
    local list="$REPO/deploy/tools.list" repo path branch
    [ -f "$list" ] || return 0
    while read -r repo path branch _; do
        case "$repo" in ''|'#'*) continue ;; esac
        BRANCH="${branch:-deploy}" nohup "$REPO/deploy/sync-tool.sh" "$repo" "$path" >> "$HOME/repos/sync-tools.log" 2>&1 &
    done < "$list"
}

# Alles steht in Funktionen: Bash liest die Datei vollständig ein, bevor etwas läuft –
# git reset darf das Skript also während des Laufs austauschen.
main "$@"
exit 0
