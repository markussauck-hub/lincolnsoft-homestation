#!/usr/bin/env python3
"""Lincolnsoft Homestation – Stationslogbuch

Sammelt aus den Tool-Repos alle Commits, deren Nachricht mit "[log]" beginnt, und schreibt die
neuesten nach ~/server/site/changelog.json. Die Startseite zeigt davon die letzten 3.

    [log] Baumaterialien mit Ø-Kosten je Handelshub      -> landet im Logbuch
    Fix: Tippfehler im Tooltip                            -> bleibt draußen

Läuft per Cron (z. B. alle 30 Minuten), nur Python-Standardbibliothek.
Konfiguration (optional): ~/.config/lincolnsoft/changelog.env
    GITHUB_TOKEN=...   nötig für private Repos (Snake-3); fine-grained, nur "Contents: Read-only"
    LOG_REPOS=owner/repo|TAG[|game];...   überschreibt die Repo-Liste unten
    LOG_OUT=~/server/site/changelog.json
    LOG_MAX=10

    changelog.py            normal laufen
    changelog.py --dry-run  nur anzeigen, nichts schreiben
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULTS = {
    "LOG_REPOS": ";".join([
        "markussauck-hub/lincolnsoft-homestation|HOMESTATION",
        "markussauck-hub/eve-price-checker|PRICE CHECKER",
        "markussauck-hub/eve-invention-helper|INVENTION HELPER",
        "markussauck-hub/eve-pi-planner|PI PLANNER",
        "markussauck-hub/Snake-3|SPIEL|game",
    ]),
    "LOG_OUT": str(HOME / "server/site/changelog.json"),
    "LOG_MAX": "10",
    "GITHUB_TOKEN": "",
}
MARK = re.compile(r"^\s*\[log\]\s*", re.I)


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def load_config(path):
    cfg = dict(DEFAULTS)
    p = Path(path).expanduser()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    cfg[k.strip()] = v.strip()
    for k in DEFAULTS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def commits(slug, token):
    """Die letzten 100 Commits des Standard-Branches (eine API-Anfrage pro Repo)."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{slug}/commits?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "lincolnsoft-changelog"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description="Stationslogbuch für die Lincolnsoft Homestation")
    ap.add_argument("--config", default="~/.config/lincolnsoft/changelog.env")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)

    out = Path(cfg["LOG_OUT"]).expanduser()
    old = {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            pass
    # Einträge je Repo aus dem letzten Lauf – falls ein Repo gerade nicht erreichbar ist, bleiben sie stehen
    keep = {}
    for e in old.get("entries", []):
        keep.setdefault(e.get("repo"), []).append(e)

    entries, failed = [], 0
    for spec in filter(None, cfg["LOG_REPOS"].split(";")):
        slug, tag, *rest = spec.split("|")
        kind = rest[0] if rest else ""
        try:
            data = commits(slug, cfg["GITHUB_TOKEN"])
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            failed += 1
            hint = " (privates Repo? GITHUB_TOKEN setzen)" if getattr(e, "code", None) == 404 else ""
            log(f"{slug}: übersprungen – {e}{hint}")
            entries += keep.get(slug, [])
            continue
        seen = set()
        for c in data:
            subject = c["commit"]["message"].splitlines()[0]
            if not MARK.match(subject):
                continue
            text = MARK.sub("", subject).strip()
            if not text or text in seen:          # gleicher Text z. B. im Merge-Commit eines PRs
                continue
            seen.add(text)
            date = (c["commit"].get("committer") or c["commit"]["author"])["date"]
            entries.append({"date": date, "repo": slug, "tag": tag, "kind": kind,
                            "text": text, "url": c.get("html_url", "")})

    entries.sort(key=lambda e: e["date"], reverse=True)
    entries = entries[: int(cfg["LOG_MAX"])]

    if args.dry_run:
        for e in entries:
            print(f"{e['date'][:10]}  {e['tag']:<17} {e['text']}")
        return 0
    if entries == old.get("entries"):
        return 0                                   # nichts Neues – Datei bleibt unverändert

    doc = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": entries}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    log(f"changelog.json aktualisiert: {len(entries)} Einträge" + (f", {failed} Repo(s) nicht erreichbar" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
