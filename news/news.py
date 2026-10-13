#!/usr/bin/env python3
"""
Lincolnsoft Homestation – News-Bot
==================================

Holt News aus festen Feeds, lässt für NEUE Einträge von einer KI (Gemini oder
Claude, eigener API-Key) kurze Teaser auf Deutsch und Englisch schreiben und legt
das Ergebnis als JSON in den Webroot. Zwei Kanäle mit eigenen Feeds, Dateien und Bildern:

    eve       -> news.json            (EVE Online)
    rimworld  -> news-rimworld.json   (RimWorld)

    python3 news.py                 # normaler Lauf (Cron, stündlich)
    python3 news.py --dry-run       # zeigt, was passieren würde, schreibt nichts, ruft keine KI
    python3 news.py --no-ai         # ohne KI: Titel + Feed-Text (englisch) übernehmen
    python3 news.py --rebuild       # alle Einträge neu von der KI schreiben lassen
    python3 news.py --test-ai       # ein KI-Testaufruf (prüft Key/Modell)
    python3 news.py --channel rimworld   # nur einen Kanal (Standard: alle)

Konfiguration: news.env (siehe news.env.example), Standard-Pfad ~/.config/lincolnsoft/news.env
Nur Standardbibliothek.
"""

import argparse
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULTS = {
    "NEWS_PROVIDER": "gemini",                       # gemini | claude | none
    "GEMINI_MODEL": "gemini-flash-latest",
    # Ausweichmodelle, wenn das Hauptmodell überlastet ist (HTTP 503) – mit ";" getrennt, leer = keins
    "GEMINI_FALLBACK_MODELS": "gemini-flash-lite-latest",
    "CLAUDE_MODEL": "claude-haiku-5-5",
    # Reihenfolge zählt: spezifische Feeds zuerst (bessere Quellen-Bezeichnung), der Sammel-Feed
    # /rss zuletzt als Auffangnetz – doppelte URLs werden zusammengeführt.
    # Optionaler dritter Teil = Titel-Filter (Regex), z. B. für gemischte Feeds fremder Seiten.
    "NEWS_FEEDS": (
        "EVE News|https://www.eveonline.com/rss/news;"
        "Dev-Blog|https://www.eveonline.com/rss/dev-blogs;"
        "Patch Notes|https://www.eveonline.com/rss/patch-notes;"
        "EVE Online|https://www.eveonline.com/rss;"
        r"Massively OP|https://massivelyop.com/tag/eve-online/feed/|\bEVE\b|New Eden|\bCCP\b;"
        r"Mein-MMO|https://mein-mmo.de/feed/|EVE Online|New Eden|\bCCP Games\b;"
        r"GameStar|https://www.gamestar.de/news/rss/news.rss|EVE Online|New Eden|\bCCP Games\b"
    ),
    "NEWS_OUT": str(HOME / "server/site/news.json"),
    "NEWS_STATE": str(HOME / "repos/news-state.json"),
    "NEWS_IMG_DIR": str(HOME / "server/site/news-img"),   # Artikelbilder lokal (Besucher laden nichts von CCP)
    "NEWS_IMAGES": "1",                                     # 0 = keine Bilder holen
    "NEWS_MAX_ITEMS": "12",          # so viele Einträge landen in news.json
    "NEWS_MAX_NEW_PER_RUN": "8",     # Obergrenze KI-Aufrufe pro Lauf
    "NEWS_MAX_AGE_DAYS": "60",       # ältere Einträge werden ignoriert
    # --- Kanal RimWorld (gleiche Bedeutung wie oben, Präfix RIM_) ---
    "RIM_FEEDS": (
        "Ludeon Blog|https://ludeon.com/blog/feed/;"
        "Steam News|https://store.steampowered.com/feeds/news/app/294100/;"
        r"Mein-MMO|https://mein-mmo.de/feed/|RimWorld;"
        r"GameStar|https://www.gamestar.de/news/rss/news.rss|RimWorld"
    ),
    "RIM_OUT": str(HOME / "server/site/news-rimworld.json"),
    "RIM_STATE": str(HOME / "repos/news-rimworld-state.json"),
    "RIM_IMG_DIR": str(HOME / "server/site/news-img-rim"),  # eigener Ordner: jeder Kanal räumt nur seine Bilder auf
    "RIM_MAX_ITEMS": "8",
    "RIM_MAX_NEW_PER_RUN": "6",
    "RIM_MAX_AGE_DAYS": "365",       # RimWorld-News sind seltener
}

# Kanal -> Präfix der Einstellungen + Text für die KI
CHANNELS = {
    "eve": {
        "prefix": "NEWS_", "feeds": "NEWS_FEEDS",
        "intro": "Du schreibst Kurzmeldungen für die Startseite eines privaten EVE-Online-Fanprojekts.",
        "categories": "Update, Event, Patch, Dev-Blog, Community, Esports, Sonstiges",
        "terms": "EVE-Begriffe (Nullsec, Capsuleer, Skill, Citadel usw.)",
        "scale": "5 = großes Update/Expansion, 3 = normales Event, 1 = Kleinkram",
    },
    "rimworld": {
        "prefix": "RIM_", "feeds": "RIM_FEEDS",
        "intro": "Du schreibst Kurzmeldungen für die Startseite eines privaten Fanprojekts über das Spiel RimWorld (Ludeon Studios).",
        "categories": "Update, DLC, Patch, Dev-Blog, Community, Mods, Sonstiges",
        "terms": "RimWorld-Begriffe (Colonist, Storyteller, Raid, Ideology, Biotech, Anomaly, Odyssey usw.)",
        "scale": "5 = neues DLC/großes Update, 3 = normaler Patch oder Bericht, 1 = Kleinkram",
    },
}
USER_AGENT = "Lincolnsoft-Homestation-NewsBot/1.0 (+https://lincolnsoft.de)"


# --------------------------------------------------------------------------- Konfiguration
def load_config(path):
    cfg = dict(DEFAULTS)
    p = Path(path).expanduser()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(cfg):                       # Umgebungsvariablen schlagen die Datei
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def log(msg):
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}", flush=True)


# --------------------------------------------------------------------------- HTTP
def http(url, data=None, headers=None, timeout=60):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- Feeds
def strip_html(s, limit=1500):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)          # RFC 822 (RSS)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))   # ISO 8601 (Atom / JSON Feed)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def first_image(*htmls):
    for h in htmls:
        m = re.search(r'<img[^>]+src="([^"]+)"', h or "", re.I)
        if m:
            return html.unescape(m.group(1))
    return None


def parse_feed(raw, source):
    """RSS 2.0, Atom oder JSON Feed -> Liste von dicts."""
    items = []
    text = raw.decode("utf-8", "replace").lstrip("﻿").strip()

    if text.startswith("{"):                                    # JSON Feed
        data = json.loads(text)
        entries = data.get("items") or data.get("articles") or []
        for e in entries:
            body = e.get("content_html") or e.get("content_text") or e.get("summary") or e.get("description") or ""
            items.append({
                "url": e.get("url") or e.get("external_url") or e.get("link") or e.get("id"),
                "title": strip_html(e.get("title") or "", 300),
                "summary": strip_html(e.get("summary") or body),
                "date": parse_date(e.get("date_published") or e.get("date_modified") or e.get("pubDate") or e.get("date")),
                "image": e.get("image") or e.get("banner_image") or first_image(body),
                "source": source,
            })
        return [i for i in items if i["url"] and i["title"]]

    root = ET.fromstring(text)
    ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/",
          "content": "http://purl.org/rss/1.0/modules/content/"}

    for it in root.iter("item"):                               # RSS 2.0
        desc = it.findtext("description") or ""
        content = it.findtext("content:encoded", namespaces=ns) or ""
        img = None
        enc = it.find("enclosure")
        if enc is not None and (enc.get("type") or "").startswith("image"):
            img = enc.get("url")
        for tag in ("media:content", "media:thumbnail"):
            m = it.find(tag, ns)
            if img is None and m is not None:
                img = m.get("url")
        items.append({
            "url": (it.findtext("link") or it.findtext("guid") or "").strip(),
            "title": strip_html(it.findtext("title") or "", 300),
            "summary": strip_html(content or desc),
            "date": parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
            "image": img or first_image(content, desc),
            "source": source,
        })

    for e in root.iter("{http://www.w3.org/2005/Atom}entry"):  # Atom
        link = None
        for l in e.findall("atom:link", ns):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href")
                break
        body = e.findtext("atom:content", namespaces=ns) or e.findtext("atom:summary", namespaces=ns) or ""
        items.append({
            "url": link or e.findtext("atom:id", namespaces=ns),
            "title": strip_html(e.findtext("atom:title", namespaces=ns) or "", 300),
            "summary": strip_html(body),
            "date": parse_date(e.findtext("atom:published", namespaces=ns) or e.findtext("atom:updated", namespaces=ns)),
            "image": first_image(body),
            "source": source,
        })

    return [i for i in items if i["url"] and i["title"]]


def fetch_feeds(cfg, verbose=False, key="NEWS_FEEDS"):
    out, failed = [], []
    for part in cfg[key].split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, rest = part.partition("|")
        url, _, flt = rest.partition("|")                      # optional: Name|URL|Titel-Filter (Regex)
        try:
            items = parse_feed(http(url.strip(), timeout=30), name.strip())
            if flt.strip():
                rx = re.compile(flt.strip())
                items = [i for i in items if rx.search(i["title"])]
            if verbose:
                log(f"Feed {name.strip()}: {len(items)} Einträge")
            out.extend(items)
        except Exception as ex:                                 # ein kaputter Feed stoppt nicht alles
            failed.append(f"{name.strip()} ({ex})")
    if failed and (verbose or not out):                        # im Normalbetrieb nur loggen, wenn gar nichts kam
        log("Feed-Fehler: " + "; ".join(failed))
    return out


# --------------------------------------------------------------------------- Bilder
OG_RE = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\']', re.I),
]
IMG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}


def find_article_image(url):
    """og:image / twitter:image der Artikelseite."""
    page = http(url, timeout=20)[:600_000].decode("utf-8", "replace")
    for rx in OG_RE:
        m = rx.search(page)
        if m:
            img = html.unescape(m.group(1)).strip()
            return urllib.parse.urljoin(url, img)
    return None


def cache_image(img_url, img_dir):
    """Bild einmal herunterladen und lokal ablegen -> relativer Pfad für die Seite."""
    import hashlib
    u = urllib.parse.urlsplit(img_url)
    if u.scheme not in ("http", "https"):
        return None
    if u.hostname and u.hostname.endswith("ctfassets.net"):          # CCPs Bild-CDN: kleinere Version anfordern
        q = urllib.parse.parse_qs(u.query)
        q.update({"w": ["960"], "fm": ["jpg"], "q": ["75"]})
        img_url = urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode(q, doseq=True)))
    req = urllib.request.Request(img_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = r.read(4_000_001)
    if ctype not in IMG_TYPES or len(data) > 4_000_000 or len(data) < 500:
        return None
    name = hashlib.sha1(img_url.encode()).hexdigest()[:16] + IMG_TYPES[ctype]
    d = Path(img_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return f"{d.name}/{name}"


def get_image(cfg, item, img_dir):
    if cfg.get("NEWS_IMAGES", "1") == "0":
        return None
    try:
        src = item.get("image") or find_article_image(item["url"])
        return cache_image(src, img_dir) if src else None
    except Exception as ex:
        log(f"  Bild für '{item['title']}' nicht geladen: {type(ex).__name__}: {ex}")
        return None


# --------------------------------------------------------------------------- KI
PROMPT = """{intro}
Grundlage sind NUR die folgenden Feed-Einträge – erfinde keine Fakten, Zahlen oder Daten hinzu.

{entries}

Antworte ausschließlich mit einem JSON-Array, ein Objekt pro Eintrag, in genau diesem Format:
[
  {{
    "id": <Nummer des Eintrags>,
    "de": {{"title": "deutscher Titel, max. 90 Zeichen", "teaser": "2–3 Sätze auf Deutsch, eigene Worte, max. 320 Zeichen"}},
    "en": {{"title": "English title, max 90 chars", "teaser": "2–3 sentences in English, own words, max 320 chars"}},
    "category": "eines von: {categories}",
    "importance": 1-5
  }}
]

Regeln: Teaser in eigenen Worten zusammenfassen, keine Sätze aus dem Text übernehmen. {terms}
und Eigennamen nicht übersetzen. importance {scale}."""


def build_prompt(items, channel="eve"):
    parts = []
    for n, it in enumerate(items, 1):
        parts.append(f"### Eintrag {n}\nQuelle: {it['source']}\nTitel: {it['title']}\nText: {(it['summary'] or '(kein Text)')[:1200]}")
    ch = CHANNELS[channel]
    return PROMPT.format(entries="\n\n".join(parts), intro=ch["intro"], categories=ch["categories"],
                         terms=ch["terms"], scale=ch["scale"])


def ask_gemini(cfg, prompt):
    """Hauptmodell; ist es überlastet (503), der Reihe nach die Ausweichmodelle."""
    models = [cfg["GEMINI_MODEL"]] + [m.strip() for m in cfg.get("GEMINI_FALLBACK_MODELS", "").split(";") if m.strip()]
    for n, model in enumerate(models):
        try:
            return ask_gemini_model(cfg, prompt, model)
        except urllib.error.HTTPError as ex:
            if ex.code != 503 or n == len(models) - 1:
                raise
            log(f"  {model} überlastet (503) – weiche aus auf {models[n + 1]}")


def ask_gemini_model(cfg, prompt, model):
    key = cfg.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY fehlt")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    res = json.loads(http(url, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }, {"x-goog-api-key": key}))
    return res["candidates"][0]["content"]["parts"][0]["text"]


def ask_claude(cfg, prompt):
    key = cfg.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt")
    res = json.loads(http("https://api.anthropic.com/v1/messages", {
        "model": cfg["CLAUDE_MODEL"],
        "max_tokens": 6000,
        "temperature": 0.4,
        "messages": [{"role": "user", "content": prompt}],
    }, {"x-api-key": key, "anthropic-version": "2023-06-01"}))
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")


def clean_entry(data):
    for lang in ("de", "en"):
        d = data.get(lang) or {}
        if not d.get("title") or not d.get("teaser"):
            raise ValueError(f"Antwort ohne {lang}-Titel/Teaser")
        d["title"] = strip_html(d["title"], 120)
        d["teaser"] = strip_html(d["teaser"], 420)
    try:
        data["importance"] = max(1, min(5, int(data.get("importance", 2))))
    except (TypeError, ValueError):
        data["importance"] = 2
    data["category"] = strip_html(str(data.get("category") or "Sonstiges"), 30)
    return data


def parse_ai_json(text):
    """-> Liste von Einträgen (akzeptiert Array oder Einzelobjekt, auch in ```json-Blöcken)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    data = json.loads(m.group(0) if m else text)
    return data if isinstance(data, list) else [data]


def summarize_batch(cfg, items, channel="eve"):
    """Alle neuen Einträge in EINER KI-Anfrage. -> {url: gen} (fehlende Einträge fehlen im Ergebnis)."""
    provider = cfg["NEWS_PROVIDER"].lower()
    if provider == "none":
        return {it["url"]: fallback(it) for it in items}
    ask = ask_gemini if provider == "gemini" else ask_claude
    prompt = build_prompt(items, channel)
    last = None
    for attempt in range(2):
        try:
            out = {}
            for e in parse_ai_json(ask(cfg, prompt)):
                try:
                    n = int(e.get("id", 0))
                    if 1 <= n <= len(items):
                        out[items[n - 1]["url"]] = clean_entry(e) | {"ai": provider}
                except (ValueError, TypeError) as ex:
                    log(f"  KI-Antwort für Eintrag {e.get('id')} unbrauchbar: {ex}")
            if not out:
                raise ValueError("keine verwertbaren Einträge in der Antwort")
            return out
        except urllib.error.HTTPError as ex:
            last = f"HTTP {ex.code}: {ex.read()[:300].decode('utf-8', 'replace').strip()}"
            log(f"  KI-Versuch {attempt + 1}/2 fehlgeschlagen: {last}")
            if ex.code != 503 and ex.code < 500:
                break                   # 400/401/403/404/429: Wiederholen bringt nichts (429 = Kontingent)
        except Exception as ex:
            last = f"{type(ex).__name__}: {ex}"
            log(f"  KI-Versuch {attempt + 1}/2 fehlgeschlagen: {last}")
        if attempt == 0:
            time.sleep(20)
    raise RuntimeError(last)


def fallback(item):
    t = item["summary"][:300] + ("…" if len(item["summary"]) > 300 else "")
    return {"de": {"title": item["title"], "teaser": t}, "en": {"title": item["title"], "teaser": t},
            "category": item["source"], "importance": 2, "ai": None}


# --------------------------------------------------------------------------- Hauptlauf
def main():
    ap = argparse.ArgumentParser(description="Lincolnsoft Homestation News-Bot")
    ap.add_argument("--config", default=os.environ.get("NEWS_CONFIG", "~/.config/lincolnsoft/news.env"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--test-ai", action="store_true", help="ein einzelner KI-Testaufruf, zeigt Antwort oder Fehler")
    ap.add_argument("--channel", choices=["all", *CHANNELS], default="all")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.no_ai:
        cfg["NEWS_PROVIDER"] = "none"
    if args.test_ai:
        prov = cfg["NEWS_PROVIDER"].lower()
        model = cfg["GEMINI_MODEL"] if prov == "gemini" else cfg["CLAUDE_MODEL"]
        log(f"KI-Test: Anbieter={prov}, Modell={model}")
        try:
            res = summarize_batch(cfg, [{"url": "test", "source": "Test", "title": "Halloween event returns to New Eden",
                                         "summary": "Spooky combat sites and new SKINs are available until early November."}])
            print(json.dumps(res["test"], ensure_ascii=False, indent=2))
            log("KI-Test OK")
            return 0
        except Exception as ex:
            log(f"KI-Test FEHLGESCHLAGEN: {ex}")
            return 1
    names = list(CHANNELS) if args.channel == "all" else [args.channel]
    rc = 0
    for name in names:
        try:
            rc |= run_channel(cfg, name, args)
        except Exception as ex:                       # ein Kanal darf den anderen nicht mitreißen
            log(f"[{name}] Fehler: {type(ex).__name__}: {ex}")
            rc = 1
    return rc


def run_channel(cfg, name, args):
    ch = CHANNELS[name]
    P = ch["prefix"]
    img_dir = cfg[P + "IMG_DIR"]
    state_path, out_path = Path(cfg[P + "STATE"]), Path(cfg[P + "OUT"])
    max_items, max_new = int(cfg[P + "MAX_ITEMS"]), int(cfg[P + "MAX_NEW_PER_RUN"])
    cutoff = time.time() - int(cfg[P + "MAX_AGE_DAYS"]) * 86400

    state = {}
    if state_path.exists() and not args.rebuild:
        state = json.loads(state_path.read_text(encoding="utf-8"))

    feed, titles = {}, set()
    for it in fetch_feeds(cfg, verbose=args.dry_run, key=ch["feeds"]):
        if it["date"] and it["date"].timestamp() < cutoff:
            continue
        key = re.sub(r"\W+", " ", it["title"]).strip().lower()
        if it["url"] in feed or key in titles:                  # gleiche Meldung in zwei Feeds (z. B. Blog + Steam) -> einmal
            continue
        titles.add(key)
        feed[it["url"]] = it
    if not feed:
        log(f"[{name}] Keine Feed-Einträge – {out_path.name} bleibt unverändert.")
        return 0

    newest = sorted(feed.values(), key=lambda i: i["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:max_items]
    todo = [i for i in newest if i["url"] not in state or state[i["url"]].get("ai") is None and cfg["NEWS_PROVIDER"] != "none"]

    if args.dry_run:
        log(f"[{name}] DRY-RUN: {len(newest)} Einträge, davon {len(todo)} neu/ohne KI-Text (max. {max_new} pro Lauf):")
        for i in newest:
            mark = "NEU" if i in todo else "ok "
            log(f"  [{mark}] {i['date'] and i['date'].date()} {i['source']}: {i['title']}")
        return 0

    batch = todo[:max_new]
    try:
        gens = summarize_batch(cfg, batch, name) if batch else {}
    except Exception as ex:
        log(f"[{name}] KI-Fehler: {ex} – nutze Feed-Text, nächster Lauf versucht es erneut")
        gens = {}
    done = sum(1 for it in batch if it["url"] in gens)
    for it in batch:
        gen = gens.get(it["url"]) or fallback(it)
        prev_img = (state.get(it["url"]) or {}).get("image")
        img = prev_img if prev_img and (Path(img_dir) / Path(prev_img).name).exists() else get_image(cfg, it, img_dir)
        state[it["url"]] = {
            **gen,
            "url": it["url"], "source": it["source"], "image": img, "img_checked": True,
            "date": it["date"].isoformat() if it["date"] else None,
            "orig_title": it["title"],
        }

    # Bereits verarbeitete Einträge ohne Bildprüfung (ältere Versionen) einmalig nachrüsten
    for it in newest:
        e = state.get(it["url"])
        if e is not None and not e.get("img_checked"):
            if not e.get("image"):
                e["image"] = get_image(cfg, it, img_dir)
            e["img_checked"] = True

    # JSON: neueste Einträge, Top-Story = wichtigste der letzten 7 Tage (sonst neueste)
    entries = [state[i["url"]] for i in newest if i["url"] in state]
    if not entries:
        log(f"[{name}] Noch keine Einträge mit Text – {out_path.name} bleibt unverändert.")
        return 0
    week = time.time() - 7 * 86400
    recent = [e for e in entries if e["date"] and datetime.fromisoformat(e["date"]).timestamp() >= week] or entries[:1]
    top = max(recent, key=lambda e: (e["importance"], e["date"] or ""))
    ordered = [top] + [e for e in entries if e is not top]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": cfg["NEWS_PROVIDER"],
        "items": [{k: e.get(k) for k in ("url", "source", "date", "image", "category", "importance", "de", "en")} for e in ordered],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_path)                                       # atomar: Seite sieht nie eine halbe Datei

    # State schlank halten: nur, was noch in den Feeds auftaucht
    state = {u: v for u, v in state.items() if u in feed}
    used = {Path(v["image"]).name for v in state.values() if v.get("image")}
    imgs = Path(img_dir)
    if imgs.is_dir():
        for f in imgs.iterdir():
            # nur eigene Dateien (16 Hex-Zeichen + Bildendung) – schützt bei falsch gesetztem NEWS_IMG_DIR
            if f.is_file() and f.name not in used and re.fullmatch(r"[0-9a-f]{16}\.(jpg|png|webp|gif|avif)", f.name):
                f.unlink()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    if done or todo:
        log(f"[{name}] {out_path.name}: {len(ordered)} Einträge, {done} neu verarbeitet (KI: {cfg['NEWS_PROVIDER']}), Top: {top['de']['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
