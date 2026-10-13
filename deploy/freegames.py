#!/usr/bin/env python3
"""Lincolnsoft Homestation – Free-Games-Bot

Sammelt Spiele, die man gerade dauerhaft geschenkt bekommt, und schreibt sie nach
~/server/site/free-games.json (für /free-games/ und die Kachel auf der Startseite).

Quellen (keine KI nötig, beides strukturierte Daten):
  - Epic Games Store direkt: aktuelle Gratis-Spiele + Vorschau „demnächst gratis“
  - GamerPower-API: GOG, Steam, itch.io (und Epic als Ersatz, falls Epic direkt ausfällt)
    Bedingung von GamerPower: sichtbarer Link „Daten: GamerPower.com“ auf der Seite.

Cover-Bilder werden einmal heruntergeladen und lokal abgelegt (free-img/), Besucher laden nichts von fremden Servern.
Läuft stündlich über deploy/update.sh. Nur Python-Standardbibliothek.

    freegames.py            normaler Lauf
    freegames.py --dry-run  nur anzeigen, nichts schreiben
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
OUT = HOME / "server/site/free-games.json"
IMG_DIR = HOME / "server/site/free-img"
UA = "Lincolnsoft-Homestation-FreeGames/1.0 (+https://lincolnsoft.de)"
EPIC_URL = "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=de&country=DE&allowCountries=DE"
GP_URL = "https://www.gamerpower.com/api/giveaways?type=game&platform=pc&sort-by=date"
SHOPS = {                                   # Text in GamerPowers "platforms" -> Kürzel
    "epic games store": "epic", "gog": "gog", "steam": "steam", "itch.io": "itch",
}
IMG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def log(msg):
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}", flush=True)


def get(url, timeout=30, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, image/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read(), (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def parse_dt(s):
    if not s or s in ("N/A", "null"):
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)      # GamerPower: ohne Zeitzone, als UTC lesen


def norm(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


# --------------------------------------------------------------------------- Epic
def epic_slug(el):
    for m in (el.get("offerMappings") or []) + ((el.get("catalogNs") or {}).get("mappings") or []):
        if m.get("pageSlug"):
            return m["pageSlug"]
    slug = (el.get("productSlug") or el.get("urlSlug") or "").split("/")[0]
    return slug or None


def epic_image(el):
    imgs = {i.get("type"): i.get("url") for i in el.get("keyImages") or []}
    for t in ("OfferImageWide", "DieselStoreFrontWide", "featuredMedia", "Thumbnail", "OfferImageTall"):
        if imgs.get(t):
            return imgs[t]
    return None


def free_windows(promo_groups):
    """[(start, end)] aller 0-%-Angebote (= gratis) in promotionalOffers/upcomingPromotionalOffers."""
    out = []
    for grp in promo_groups or []:
        for o in grp.get("promotionalOffers") or []:
            if (o.get("discountSetting") or {}).get("discountPercentage") == 0:
                out.append((parse_dt(o.get("startDate")), parse_dt(o.get("endDate"))))
    return out


def fetch_epic(now):
    raw, _ = get(EPIC_URL)
    els = json.loads(raw)["data"]["Catalog"]["searchStore"]["elements"]
    current, upcoming = [], []
    for el in els:
        title = (el.get("title") or "").strip()
        promos = el.get("promotions") or {}
        slug = epic_slug(el)
        if not title or not slug or title.lower().startswith("mystery game"):
            continue
        price = ((el.get("price") or {}).get("totalPrice") or {})
        orig = price.get("originalPrice") or 0
        base = {
            "title": title, "shop": "epic", "url": f"https://store.epicgames.com/de/p/{slug}",
            "img_src": epic_image(el),
            "worth": f"{orig / 100:.2f} €".replace(".", ",") if orig else None,
        }
        for start, end in free_windows(promos.get("promotionalOffers")):
            if (not start or start <= now) and (not end or end > now):
                current.append(base | {"start": iso(start), "end": iso(end)})
        for start, end in free_windows(promos.get("upcomingPromotionalOffers")):
            if start and start > now:
                upcoming.append(base | {"start": iso(start), "end": iso(end)})
    return current, upcoming


# --------------------------------------------------------------------------- GamerPower
def clean_title(t):
    """'Dual Chroma (itch.io) Giveaway' -> 'Dual Chroma', 'Dwarven Realms Key' -> 'Dwarven Realms'."""
    t = re.sub(r"\s+(Giveaway|Key|Steam Key|Game Key)$", "", t.strip(), flags=re.I)
    t = re.sub(r"\s*\((Steam|GOG|Epic Games|Epic Games Store|itch\.io|IndieGala|DRM-Free)\)", "", t, flags=re.I)
    return re.sub(r"\s+(Giveaway|Key)$", "", t.strip(), flags=re.I).strip()


def fetch_gamerpower(now):
    raw, _ = get(GP_URL)
    out = []
    for g in json.loads(raw):
        if (g.get("status") or "").lower() != "active" or (g.get("type") or "").lower() not in ("game", "early access"):
            continue
        plats = [p.strip().lower() for p in (g.get("platforms") or "").split(",")]
        shop = next((SHOPS[p] for p in plats if p in SHOPS), None)
        if not shop:
            continue
        end = parse_dt(g.get("end_date"))
        if end and end <= now:
            continue
        worth = g.get("worth") if g.get("worth") not in (None, "", "N/A") else None
        url = g.get("open_giveaway_url") or g.get("gamerpower_url")
        if not url or not url.startswith("https://"):
            continue
        out.append({
            "title": clean_title(g.get("title") or ""),
            "shop": shop, "url": url, "img_src": g.get("image") or g.get("thumbnail"),
            "worth": worth, "start": None, "end": iso(end), "via": "gamerpower",
        })
    return out


# --------------------------------------------------------------------------- Bilder
def cache_image(src):
    """Cover einmal laden -> 'free-img/<hash>.jpg' (oder None)."""
    if not src or not src.startswith("https://"):
        return None
    url = src
    host = urllib.parse.urlsplit(src).hostname or ""
    if host.endswith("epicgames.com"):                         # Epic-CDN: verkleinerte Version anfordern
        url = src + ("&" if "?" in src else "?") + "w=640&h=360&resize=1&quality=medium"
    name = hashlib.sha1(src.encode()).hexdigest()[:16]
    for ext in IMG_TYPES.values():
        if (IMG_DIR / (name + ext)).exists():
            return f"{IMG_DIR.name}/{name}{ext}"
    for u in dict.fromkeys([url, src]):                        # falls die verkleinerte Version scheitert: Original
        try:
            data, ctype = get(u, timeout=30, limit=4_000_001)
        except Exception:
            continue
        if ctype in IMG_TYPES and 500 < len(data) <= 4_000_000:
            IMG_DIR.mkdir(parents=True, exist_ok=True)
            (IMG_DIR / (name + IMG_TYPES[ctype])).write_bytes(data)
            return f"{IMG_DIR.name}/{name}{IMG_TYPES[ctype]}"
    return None


def main():
    ap = argparse.ArgumentParser(description="Free-Games-Bot für die Lincolnsoft Homestation")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            pass

    sources, current, upcoming, failed = [], [], [], []
    try:
        cur, upcoming = fetch_epic(now)
        current += cur
        sources.append("epic")
    except Exception as ex:
        failed.append(f"Epic: {type(ex).__name__}: {ex}")
    try:
        gp = fetch_gamerpower(now)
        have = {norm(g["title"]) for g in current}
        epic_ok = "epic" in sources
        for g in gp:
            if g["shop"] == "epic" and epic_ok:                # Epic kommt direkt (genauer: Start/Ende, Vorschau)
                continue
            if norm(g["title"]) not in have:
                current.append(g)
                have.add(norm(g["title"]))
        sources.append("gamerpower")
    except Exception as ex:
        failed.append(f"GamerPower: {type(ex).__name__}: {ex}")

    if failed:
        log("Quelle nicht erreichbar – " + "; ".join(failed))
    if not sources:
        return 1                                               # nichts erreichbar: alte Datei bleibt stehen

    # Bisher bekannte Spiele einer gerade ausgefallenen Quelle nicht verlieren (sofern noch nicht abgelaufen)
    if "epic" not in sources:
        upcoming = [u for u in old.get("upcoming", []) if parse_dt(u.get("start")) and parse_dt(u["start"]) > now]
        current += [g for g in old.get("current", []) if g.get("shop") == "epic" and (not g.get("end") or parse_dt(g["end"]) > now)]

    order = {"epic": 0, "gog": 1, "steam": 2, "itch": 3}
    current.sort(key=lambda g: (g.get("end") or "9999", order.get(g["shop"], 9), g["title"]))
    upcoming.sort(key=lambda g: (g.get("start") or "", g["title"]))

    if args.dry_run:
        for g in current:
            print(f"JETZT  {g['shop']:<6} bis {g.get('end') or 'offen':<21} {g['title']}  {g['url']}")
        for g in upcoming:
            print(f"BALD   {g['shop']:<6} ab  {g.get('start'):<21} {g['title']}")
        return 0

    for g in current + upcoming:
        src = g.pop("img_src", None)
        g["image"] = g.get("image") or cache_image(src)
        g.pop("via", None)

    doc = {"generated": iso(now), "sources": sources, "current": current, "upcoming": upcoming}
    same = old.get("current") == current and old.get("upcoming") == upcoming
    if not same:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(OUT)
        log(f"free-games.json: {len(current)} jetzt gratis, {len(upcoming)} demnächst")

        # Cover, die nicht mehr gebraucht werden, aufräumen (nur eigene Dateien: 16 Hex-Zeichen)
        used = {Path(g["image"]).name for g in current + upcoming if g.get("image")}
        if IMG_DIR.is_dir():
            for f in IMG_DIR.iterdir():
                if f.is_file() and f.name not in used and re.fullmatch(r"[0-9a-f]{16}\.(jpg|png|webp)", f.name):
                    f.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
