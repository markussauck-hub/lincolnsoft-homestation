#!/bin/sh
# Startet den Steelrumble-Server und startet ihn neu, sobald eine neue index.pck ausgeliefert wird
# (sync-tool.sh spiegelt den Branch "deploy" von markussauck-hub/steelrumble nach ~/server/site/steelrumble).
PCK=/site/steelrumble/index.pck
PORT=24556

while [ ! -f "$PCK" ]; do
    echo "warte auf $PCK …"
    sleep 30
done

while true; do
    stamp=$(stat -c %Y "$PCK")
    godot --headless --main-pack "$PCK" -- --server --port=$PORT &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        sleep 30
        now=$(stat -c %Y "$PCK" 2>/dev/null || echo "$stamp")
        if [ "$now" != "$stamp" ]; then
            echo "neue Spielversion – Server startet neu"
            sleep 10   # sync-tool fertig kopieren lassen
            kill "$pid"
            break
        fi
    done
    wait "$pid"
    sleep 2
done
