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
