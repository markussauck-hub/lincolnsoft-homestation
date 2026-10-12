#!/usr/bin/env python3
"""
Lincolnsoft Homestation – News-Bot
==================================

Holt EVE-Online-News aus festen Feeds, lässt für NEUE Einträge von einer KI
(Gemini oder Claude, eigener API-Key) kurze Teaser auf Deutsch und Englisch
schreiben und legt das Ergebnis als news.json in den Webroot.

    python3 news.py                 # normaler Lauf (Cron, stündlich)
    python3 news.py --dry-run       # zeigt, was passieren würde, schreibt nichts, ruft keine KI
    python3 news.py --no-ai         # ohne KI: Titel + Feed-Text (englisch) übernehmen
    python3 news.py --rebuild       # alle Einträge neu von der KI schreiben lassen

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
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULTS = {
    "NEWS_PROVIDER": "gemini",                       # gemini | claude | none
    "GEMINI_MODEL": "gemini-flash-latest",
    "CLAUDE_MODEL": "claude-haiku-5-5",
    # Reihenfolge zählt: spezifische Feeds zuerst (bessere Quellen-Bezeichnung), der Sammel-Feed
    # /rss zuletzt als Auffangnetz – doppelte URLs werden zusammengeführt.
    "NEWS_FEEDS": (
        "EVE News|https://www.eveonline.com/rss/news;"
        "Dev-Blog|https://www.eveonline.com/rss/dev-blogs;"
        "Patch Notes|https://www.eveonline.com/rss/patch-notes;"
        "EVE Online|https://www.eveonline.com/rss"
    ),
    "NEWS_OUT": str(HOME / "server/site/news.json"),
    "NEWS_STATE": str(HOME / "repos/news-state.json"),
    "NEWS_MAX_ITEMS": "12",          # so viele Einträge landen in news.json
    "NEWS_MAX_NEW_PER_RUN": "8",     # Obergrenze KI-Aufrufe pro Lauf
    "NEWS_MAX_AGE_DAYS": "60",       # ältere Einträge werden ignoriert
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


def fetch_feeds(cfg, verbose=False):
    out, failed = [], []
    for part in cfg["NEWS_FEEDS"].split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, url = part.partition("|")
        try:
            items = parse_feed(http(url.strip(), timeout=30), name.strip())
            if verbose:
                log(f"Feed {name.strip()}: {len(items)} Einträge")
            out.extend(items)
        except Exception as ex:                                 # ein kaputter Feed stoppt nicht alles
            failed.append(f"{name.strip()} ({ex})")
    if failed and (verbose or not out):                        # im Normalbetrieb nur loggen, wenn gar nichts kam
        log("Feed-Fehler: " + "; ".join(failed))
    return out


# --------------------------------------------------------------------------- KI
PROMPT = """Du schreibst Kurzmeldungen für die Startseite eines privaten EVE-Online-Fanprojekts.
Grundlage ist NUR dieser Feed-Eintrag – erfinde keine Fakten, Zahlen oder Daten hinzu.

Quelle: {source}
Titel: {title}
Text: {summary}

Antworte ausschließlich mit JSON in genau diesem Format:
{{
  "de": {{"title": "deutscher Titel, max. 90 Zeichen", "teaser": "2–3 Sätze auf Deutsch, eigene Worte, max. 320 Zeichen"}},
  "en": {{"title": "English title, max 90 chars", "teaser": "2–3 sentences in English, own words, max 320 chars"}},
  "category": "eines von: Update, Event, Patch, Dev-Blog, Community, Esports, Sonstiges",
  "importance": 1-5
}}

Regeln: Teaser in eigenen Worten zusammenfassen, keine Sätze aus dem Text übernehmen. EVE-Begriffe
(Nullsec, Capsuleer, Skill, Citadel usw.) und Eigennamen nicht übersetzen. importance 5 = großes
Update/Expansion, 3 = normales Event, 1 = Kleinkram."""


def ask_gemini(cfg, prompt):
    key = cfg.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY fehlt")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['GEMINI_MODEL']}:generateContent"
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
        "max_tokens": 800,
        "temperature": 0.4,
        "messages": [{"role": "user", "content": prompt}],
    }, {"x-api-key": key, "anthropic-version": "2023-06-01"}))
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")


def parse_ai_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
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


def summarize(cfg, item):
    provider = cfg["NEWS_PROVIDER"].lower()
    if provider == "none":
        return fallback(item)
    prompt = PROMPT.format(source=item["source"], title=item["title"], summary=item["summary"] or "(kein Text)")
    ask = ask_gemini if provider == "gemini" else ask_claude
    last = None
    for attempt in range(3):
        try:
            return parse_ai_json(ask(cfg, prompt)) | {"ai": provider}
        except urllib.error.HTTPError as ex:
            last = f"HTTP {ex.code}: {ex.read()[:300].decode('utf-8', 'replace')}"
            if ex.code in (400, 401, 403, 404):
                break                                       # Konfigurationsfehler – nicht weiter versuchen
        except Exception as ex:
            last = str(ex)
        time.sleep(5 * (attempt + 1))
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
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.no_ai:
        cfg["NEWS_PROVIDER"] = "none"
    state_path, out_path = Path(cfg["NEWS_STATE"]), Path(cfg["NEWS_OUT"])
    max_items, max_new = int(cfg["NEWS_MAX_ITEMS"]), int(cfg["NEWS_MAX_NEW_PER_RUN"])
    cutoff = time.time() - int(cfg["NEWS_MAX_AGE_DAYS"]) * 86400

    state = {}
    if state_path.exists() and not args.rebuild:
        state = json.loads(state_path.read_text(encoding="utf-8"))

    feed = {}
    for it in fetch_feeds(cfg, verbose=args.dry_run):
        if it["date"] and it["date"].timestamp() < cutoff:
            continue
        feed.setdefault(it["url"], it)                          # gleiche URL in zwei Feeds -> einmal
    if not feed:
        log("Keine Feed-Einträge – news.json bleibt unverändert.")
        return 0

    newest = sorted(feed.values(), key=lambda i: i["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:max_items]
    todo = [i for i in newest if i["url"] not in state or state[i["url"]].get("ai") is None and cfg["NEWS_PROVIDER"] != "none"]

    if args.dry_run:
        log(f"DRY-RUN: {len(newest)} Einträge, davon {len(todo)} neu/ohne KI-Text (max. {max_new} pro Lauf):")
        for i in newest:
            mark = "NEU" if i in todo else "ok "
            log(f"  [{mark}] {i['date'] and i['date'].date()} {i['source']}: {i['title']}")
        return 0

    done = 0
    for it in todo[:max_new]:
        try:
            gen = summarize(cfg, it)
            done += 1
        except Exception as ex:
            log(f"KI-Fehler bei '{it['title']}': {ex} – nutze Feed-Text")
            gen = fallback(it)
        state[it["url"]] = {
            **gen,
            "url": it["url"], "source": it["source"], "image": it["image"],
            "date": it["date"].isoformat() if it["date"] else None,
            "orig_title": it["title"],
        }

    # news.json: neueste Einträge, Top-Story = wichtigste der letzten 7 Tage (sonst neueste)
    entries = [state[i["url"]] for i in newest if i["url"] in state]
    if not entries:
        log("Noch keine Einträge mit Text – news.json bleibt unverändert.")
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
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    if done or todo:
        log(f"news.json: {len(ordered)} Einträge, {done} neu von der KI ({cfg['NEWS_PROVIDER']}), Top: {top['de']['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
