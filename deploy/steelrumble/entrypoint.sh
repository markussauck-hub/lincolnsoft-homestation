#!/bin/sh
# Datenordner des Spiels (Volume für das Vorschlagsboard) gehört dem Spielnutzer – egal, wem er auf dem Host gehört.
# Danach ohne Root-Rechte weiter.
DATA=/home/game/.local/share/godot
mkdir -p "$DATA"
chown -R game:game /home/game/.local
exec setpriv --reuid=game --regid=game --init-groups "$@"
