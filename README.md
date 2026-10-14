# Lincolnsoft Homestation

Startseite für **https://lincolnsoft.de**, gehostet auf dem Lincolnsoft Server (netcup VPS, Docker + Caddy).

## Aufbau

| Pfad | Inhalt |
|---|---|
| `site/` | Alles, was öffentlich ausgeliefert wird (wird nach `~/server/site` kopiert) |
| `site/index.html` | Die Startseite – eine einzelne Datei, Fonts (Orbitron, Fira Code) eingebettet |
| `deploy/update.sh` | Deploy-Skript für den Server (Cron, jede Minute) |

## Deployment

Push auf `main` → spätestens nach einer Minute live. `deploy/update.sh` läuft per Cron jede Minute und

1. kopiert bei neuen Commits `site/` nach `~/server/site`,
2. übernimmt Änderungen an `deploy/server/Caddyfile` und `deploy/server/docker-compose.yml` nach `~/server/`
   – vorher geprüft (`caddy validate` bzw. `docker compose config`), alte Version als `*.bak`; schlägt die Prüfung
   oder der Neustart fehl, bleibt bzw. kommt die alte Version zurück (Meldung im Deploy-Log),
3. startet den Kontakt-Dienst (`api`) neu, wenn sich `deploy/kontakt/` geändert hat,
4. aktualisiert alle 30 Min. das Stationslogbuch (`deploy/changelog.py`).

Auf dem Server:

- Repo liegt in `~/repos/lincolnsoft-homestation` (Zugriff per read-only Deploy-Key)
- Log: `~/repos/homestation-deploy.log`
- Sofort deployen: `~/repos/lincolnsoft-homestation/deploy/update.sh --force`
- Caddy/Compose also **nur im Repo ändern** – Änderungen direkt in `~/server/` überschreibt der nächste Lauf.

Hinweis: Dateien, die aus `site/` gelöscht werden, bleiben in `~/server/site` liegen und müssen dort von Hand entfernt werden.

## Free Games

`deploy/freegames.py` (stündlich über `update.sh`) sammelt Spiele, die man dauerhaft geschenkt bekommt:
Epic Games Store direkt (inkl. „demnächst gratis“) und die GamerPower-API für GOG, Steam und itch.io.
Ergebnis: `~/server/site/free-games.json` + Cover in `free-img/`, angezeigt auf `/free-games/` und als Anzahl
auf der Kachel. GamerPower verlangt einen sichtbaren Quellenlink (steht auf der Seite). Log: `~/repos/freegames.log`,
Test: `deploy/freegames.py --dry-run`.

## Kontaktformular und Aufrufzähler

Container `api` (`deploy/kontakt/kontakt.py`, Python-Standardbibliothek), Caddy leitet `/api/*` dorthin.

- `/impressum.html` → `POST /api/kontakt` → Mail über Gmail an dich. Nichts wird gespeichert.
- Spam-Schutz: Honeypot, Mindestzeit 4 s, 3 Nachrichten / 10 Min. pro IP (nur im RAM), 30 / Tag gesamt.
- Konfiguration: `~/.config/lincolnsoft/kontakt.env` (Vorlage `deploy/kontakt/kontakt.env.example`, `chmod 600`).
  Ohne `SMTP_PASS` meldet das Formular „noch nicht aktiv – bitte per E-Mail“.
  Nach dem Eintragen: `cd ~/server && docker compose up -d --force-recreate api`
- Zähler: `POST /api/zaehler/snake-3` (nur Tageszahl), ansehen: `curl -s https://lincolnsoft.de/api/zaehler`

## Tools unter lincolnsoft.de

| Pfad | Repo | Quelle |
|---|---|---|
| `/price-checker/` | markussauck-hub/eve-price-checker | Branch `deploy` (von der GitHub Action gebaut) |
| `/invention-helper/` | markussauck-hub/eve-invention-helper | Branch `deploy` (von der GitHub Action gebaut) |
| `/pi-planner/` | markussauck-hub/eve-pi-planner | Branch `deploy` (von der GitHub Action gebaut) |
| `/snake-3/` | markussauck-hub/snake-3-play | Branch `main` (fertiger Unity-WebGL-Build) – `BRANCH=main` im Cronjob |

`deploy/sync-tool.sh <repo> <pfad>` spiegelt den Branch `deploy` nach `~/server/site/<pfad>` (Cron, jede Minute).
GitHub Pages läuft parallel weiter.

## Foto-Coach (Gemini-Proxy)

Die Web-App unter `/fotocoach/` schickt Fotos zur Motiv-Analyse an Google Gemini. Damit auf den Handys kein
Schlüssel nötig ist, hängt Caddy ihn serverseitig an: `/fotocoach/api/gemini/v1beta/models/<gemini-…>:generateContent`
→ `generativelanguage.googleapis.com`. Alles andere unter dem Pfad bekommt 403 (die App erkennt daran den Proxy).

- Schlüssel: `~/.config/lincolnsoft/gemini.env` mit `GEMINI_API_KEY=…` (`chmod 600`, nie ins Repo; legt `update.sh` leer an).
  Am besten ein Google-Projekt **ohne** Abrechnungskonto – dann kann nichts kosten.
- Nach dem Eintragen: `cd ~/server && docker compose up -d --force-recreate caddy`
- Test: `curl -s -o /dev/null -w '%{http_code}\n' https://lincolnsoft.de/fotocoach/api/gemini/ping` → `403`
- Kein Limit pro Nutzer: wer die URL kennt, nutzt das (kostenlose) Kontingent mit.

## News-Bot

`news/news.py` holt alle 3 Stunden für zwei Kanäle (EVE → `news.json`, RimWorld → `news-rimworld.json`) die Feeds (News, Dev-Blogs, Patch Notes), lässt für **neue** Einträge
von einer KI (Gemini oder Claude, eigener API-Key) kurze Teaser auf Deutsch und Englisch schreiben und legt
`news.json` in den Webroot. Die Seite lädt sie und zeigt sie im Zeitungsraster (DE/EN-Umschalter).

- Config: `~/.config/lincolnsoft/news.env` (Vorlage: `news/news.env.example`, `chmod 600`, nie ins Repo)
- Test: `python3 news/news.py --dry-run`
- Ohne KI: `python3 news/news.py --no-ai`
- Log: `~/repos/news.log`

## Stationslogbuch

`deploy/changelog.py` sammelt aus den Tool-Repos alle Commits, deren Nachricht mit **`[log]`** beginnt, und schreibt
die neuesten nach `~/server/site/changelog.json`. Die Startseite zeigt die letzten 3 (feste Einträge im HTML dienen als Fallback).

```
[log] Baumaterialien mit Ø-Kosten je Handelshub   -> erscheint im Logbuch
Fix: Tippfehler im Tooltip                         -> erscheint nicht
```

- Cron: `*/30 * * * * ~/repos/lincolnsoft-homestation/deploy/changelog.py >> ~/repos/changelog.log 2>&1`
- Private Repos (Snake-3) brauchen `GITHUB_TOKEN` in `~/.config/lincolnsoft/changelog.env`
  (fine-grained Token, nur Repo Snake-3, Berechtigung „Contents: Read-only“), `chmod 600`
- Test: `deploy/changelog.py --dry-run`
