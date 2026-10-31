#!/bin/sh
# Startet die Steelrumble-Spielräume (je ein Server-Prozess, Raum 1 bis 4 auf Port 24556 bis 24559) und startet
# alle neu, sobald eine neue index.pck ausgeliefert wird (sync-tool.sh spiegelt den Branch "deploy" von
# markussauck-hub/steelrumble nach ~/server/site/steelrumble). Stürzt ein Raum ab, wird nur er neu gestartet.
# Konten, Vorschlagsboard und Raumstatus liegen im gemeinsamen Datenordner (Volume), den alle Räume teilen.
PCK=/site/steelrumble/index.pck
PORTS="24556 24557 24558 24559"

while [ ! -f "$PCK" ]; do
    echo "warte auf $PCK …"
    sleep 30
done

start_room() {   # $1 = Raumnummer, $2 = Port
    godot --headless --main-pack "$PCK" -- --server --port="$2" --room="$1" &
    eval "pid_$1=$!"
}

while true; do
    stamp=$(stat -c %Y "$PCK")
    n=1
    for port in $PORTS; do
        start_room "$n" "$port"
        n=$((n + 1))
    done
    while true; do
        sleep 30
        now=$(stat -c %Y "$PCK" 2>/dev/null || echo "$stamp")
        if [ "$now" != "$stamp" ]; then
            echo "neue Spielversion – alle Räume starten neu"
            sleep 10   # sync-tool fertig kopieren lassen
            break
        fi
        n=1
        for port in $PORTS; do
            eval "pid=\$pid_$n"
            if ! kill -0 "$pid" 2>/dev/null; then
                echo "Raum $n läuft nicht mehr – starte neu"
                start_room "$n" "$port"
            fi
            n=$((n + 1))
        done
    done
    n=1
    for port in $PORTS; do
        eval "kill \$pid_$n" 2>/dev/null
        n=$((n + 1))
    done
    wait
    sleep 2
done
