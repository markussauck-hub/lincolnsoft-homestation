# Steelrumble – dedizierter Spielserver (Godot ohne Grafik)
# Das Spiel selbst kommt nicht ins Image: run.sh startet ~/server/site/steelrumble/index.pck,
# also genau die Version, die auch im Browser unter /steelrumble/ läuft.
FROM debian:bookworm-slim

ARG GODOT_VERSION=4.7.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates wget unzip libfontconfig1 \
 && wget -q "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-stable/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip" -O /tmp/godot.zip \
 && unzip -q /tmp/godot.zip -d /tmp/godot \
 && mv /tmp/godot/Godot_v${GODOT_VERSION}-stable_linux.x86_64 /usr/local/bin/godot \
 && rm -rf /tmp/godot /tmp/godot.zip /var/lib/apt/lists/*

COPY run.sh /usr/local/bin/steelrumble-run
COPY entrypoint.sh /usr/local/bin/steelrumble-entry
RUN chmod 755 /usr/local/bin/steelrumble-run /usr/local/bin/steelrumble-entry && useradd -m game
ENV HOME=/home/game
# Startet als root, übergibt den Datenordner (Volume) an "game" und läuft dann ohne Root-Rechte.
ENTRYPOINT ["steelrumble-entry"]
CMD ["steelrumble-run"]
