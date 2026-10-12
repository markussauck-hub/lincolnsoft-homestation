# Lincolnsoft Homestation

Startseite für **https://lincolnsoft.de**, gehostet auf dem Lincolnsoft Server (netcup VPS, Docker + Caddy).

## Aufbau

| Pfad | Inhalt |
|---|---|
| `site/` | Alles, was öffentlich ausgeliefert wird (wird nach `~/server/site` kopiert) |
| `site/index.html` | Die Startseite – eine einzelne Datei, Fonts (Orbitron, Fira Code) eingebettet |
| `deploy/update.sh` | Deploy-Skript für den Server (Cron, jede Minute) |

## Deployment

Push auf `main` → spätestens nach einer Minute live.

Auf dem Server:

- Repo liegt in `~/repos/lincolnsoft-homestation` (Zugriff per read-only Deploy-Key)
- Cronjob ruft `deploy/update.sh` auf; das Skript macht nur etwas, wenn es neue Commits gibt
- Log: `~/repos/homestation-deploy.log`
- Sofort deployen: `~/repos/lincolnsoft-homestation/deploy/update.sh --force`

Hinweis: Dateien, die aus `site/` gelöscht werden, bleiben in `~/server/site` liegen und müssen dort von Hand entfernt werden.

## Tools unter lincolnsoft.de

| Pfad | Repo | Quelle |
|---|---|---|
| `/price-checker/` | markussauck-hub/eve-price-checker | Branch `deploy` (von der GitHub Action gebaut) |
| `/invention-helper/` | markussauck-hub/eve-invention-helper | Branch `deploy` (von der GitHub Action gebaut) |

`deploy/sync-tool.sh <repo> <pfad>` spiegelt den Branch `deploy` nach `~/server/site/<pfad>` (Cron, jede Minute).
GitHub Pages läuft parallel weiter.

## News-Bot

`news/news.py` holt stündlich die offiziellen EVE-Feeds (News, Dev-Blogs, Patch Notes), lässt für **neue** Einträge
von einer KI (Gemini oder Claude, eigener API-Key) kurze Teaser auf Deutsch und Englisch schreiben und legt
`news.json` in den Webroot. Die Seite lädt sie und zeigt sie im Zeitungsraster (DE/EN-Umschalter).

- Config: `~/.config/lincolnsoft/news.env` (Vorlage: `news/news.env.example`, `chmod 600`, nie ins Repo)
- Test: `python3 news/news.py --dry-run`
- Ohne KI: `python3 news/news.py --no-ai`
- Log: `~/repos/news.log`
