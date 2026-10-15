"""Feedback-Übersicht für den Kontakt-Dienst – passwortgeschützte Seite fürs Handy.

    GET  /api/feedback/ansicht?ordner=offen|erledigt&art=fehler|idee|lob|sonstiges
    POST /api/feedback/ansicht   aktion=erledigt|zurueck|loeschen, datei=<name>.txt, ordner=offen|erledigt

Zugang per HTTP-Basic-Auth (der Browser fragt einmal nach Name und Passwort und merkt sie sich):
    FEEDBACK_ADMIN_USER   Benutzername (Standard: admin)
    FEEDBACK_ADMIN_PASS   Passwort – leer = Seite ist abgeschaltet
Erledigte Nachrichten werden nach DATA_DIR/feedback/<tool>/erledigt/ verschoben, gelöscht wird nur dort.
Schutz: falsche Anmeldungen pro IP begrenzt, Aktionen nur per POST von derselben Seite (Origin-Prüfung).
"""
import base64
import hmac
import html
import os
import re
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import parse_qs, urlencode

ADMIN_USER = os.environ.get("FEEDBACK_ADMIN_USER") or "admin"
ADMIN_PASS = os.environ.get("FEEDBACK_ADMIN_PASS", "")
FAIL_MAX, FAIL_WINDOW = 10, 600                  # höchstens 10 Fehlversuche pro IP in 10 Minuten
NAME_RE = re.compile(r"^[0-9A-Za-z_.-]{1,80}\.txt$")
KINDS = {"fehler": ("🐞", "Fehler"), "idee": ("💡", "Idee"), "lob": ("👍", "Lob"), "sonstiges": ("💬", "Sonstiges")}
PATH = "/api/feedback/ansicht"

_fails = defaultdict(deque)
_lock = Lock()


def _blocked(ip):
    now = time.time()
    with _lock:
        q = _fails[ip]
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        if not q:
            _fails.pop(ip, None)
        return len(q) >= FAIL_MAX


def _failed(ip):
    with _lock:
        _fails[ip].append(time.time())


def check_auth(handler, ip):
    """True = angemeldet. Sonst wurde bereits geantwortet (401/403/429)."""
    if not ADMIN_PASS:
        _page(handler, 403, "Nicht eingerichtet",
              "<p>Die Feedback-Übersicht ist abgeschaltet. Zum Einschalten in "
              "<code>~/.config/lincolnsoft/kontakt.env</code> <code>FEEDBACK_ADMIN_PASS=…</code> setzen und "
              "<code>docker compose up -d --force-recreate api</code> ausführen.</p>")
        return False
    if _blocked(ip):
        _page(handler, 429, "Zu viele Versuche", "<p>Zu viele falsche Anmeldungen – bitte in 10 Minuten erneut.</p>")
        return False
    hdr = handler.headers.get("Authorization") or ""
    ok = False
    if hdr.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
            ok = hmac.compare_digest(user.encode(), ADMIN_USER.encode()) & hmac.compare_digest(pw.encode(), ADMIN_PASS.encode())
        except (ValueError, UnicodeDecodeError):
            ok = False
        if not ok:
            _failed(ip)
    if not ok:
        body = "Anmeldung erforderlich".encode()
        handler.send_response(401)
        handler.send_header("WWW-Authenticate", 'Basic realm="Lincolnsoft Feedback", charset="UTF-8"')
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    return ok


def _base(data_dir, app):
    return os.path.join(data_dir, "feedback", app)


def _parse(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    head, _, rest = text.partition("\n\n")
    meta = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    msg, _, tech = rest.partition("\n--- Technische Angaben ---\n")
    return meta, msg.strip(), tech.strip()


def _list(folder):
    try:
        names = [n for n in os.listdir(folder) if NAME_RE.match(n)]
    except FileNotFoundError:
        return []
    return sorted(names, reverse=True)


def _kind_of(name):
    m = re.match(r"^\d{4}-\d\d-\d\d_\d{6}_([a-z]+)", name)
    return m.group(1) if m and m.group(1) in KINDS else "sonstiges"


def show(handler, data_dir, app, app_title, query):
    q = parse_qs(query)
    ordner = "erledigt" if q.get("ordner", [""])[0] == "erledigt" else "offen"
    art = q.get("art", [""])[0]
    art = art if art in KINDS else ""
    base = _base(data_dir, app)
    folder = os.path.join(base, "erledigt") if ordner == "erledigt" else base
    all_open, all_done = _list(base), _list(os.path.join(base, "erledigt"))
    names = all_open if ordner == "offen" else all_done
    counts = {k: sum(1 for n in names if _kind_of(n) == k) for k in KINDS}
    shown = [n for n in names if not art or _kind_of(n) == art]

    def link(**kw):
        p = {"ordner": ordner, "art": art}
        p.update(kw)
        return PATH + "?" + urlencode({k: v for k, v in p.items() if v})

    e = html.escape
    tabs = (f'<nav class="tabs"><a class="{"on" if ordner == "offen" else ""}" href="{e(link(ordner="offen", art=""))}">Offen <b>{len(all_open)}</b></a>'
            f'<a class="{"on" if ordner == "erledigt" else ""}" href="{e(link(ordner="erledigt", art=""))}">Erledigt <b>{len(all_done)}</b></a></nav>')
    chips = f'<nav class="chips"><a class="{"on" if not art else ""}" href="{e(link(art=""))}">Alle</a>' + "".join(
        f'<a class="{"on" if art == k else ""}" href="{e(link(art=k))}">{ic} {lbl} <b>{counts[k]}</b></a>'
        for k, (ic, lbl) in KINDS.items()) + "</nav>"

    cards = []
    for n in shown:
        try:
            meta, msg, tech = _parse(os.path.join(folder, n))
        except OSError:
            continue
        k = _kind_of(n)
        ic, lbl = KINDS[k]
        who = meta.get("Name", "–")
        mail = meta.get("E-Mail", "–")
        mail_html = (f'<a href="mailto:{e(mail)}?subject={e("Re: Dein Feedback zum " + app_title)}">{e(mail)}</a>'
                     if "@" in mail else "")
        actions = ([("erledigt", "✓ Erledigt", "")] if ordner == "offen"
                   else [("zurueck", "↩ Wieder offen", ""), ("loeschen", "🗑 Löschen", "danger")])
        forms = "".join(
            f'<form method="post" action="{PATH}"><input type="hidden" name="aktion" value="{a}">'
            f'<input type="hidden" name="datei" value="{e(n)}"><input type="hidden" name="ordner" value="{ordner}">'
            f'<input type="hidden" name="art" value="{e(art)}"><button class="{cls}"'
            + (' onclick="return confirm(\'Endgültig löschen?\')"' if a == "loeschen" else "") + f'>{t}</button></form>'
            for a, t, cls in actions)
        tech_html = f"<details><summary>Technische Angaben</summary><pre>{e(tech)}</pre></details>" if tech else ""
        cards.append(
            f'<article class="card k-{k}"><header><span class="kind">{ic} {lbl}</span>'
            f'<time>{e(meta.get("Zeit", ""))}</time></header>'
            f'<p class="msg">{e(msg)}</p>'
            f'<p class="who">{e(who)}{" · " + mail_html if mail_html else ""}</p>{tech_html}'
            f'<div class="actions">{forms}</div></article>')
    if not cards:
        cards.append('<p class="empty">Hier ist gerade nichts. 🎉</p>' if ordner == "offen" else '<p class="empty">Noch nichts erledigt.</p>')

    _page(handler, 200, f"Feedback · {app_title}", tabs + chips + "".join(cards))


def act(handler, data_dir, app, raw_body):
    """Aktion ausführen und zurück zur Liste (Post/Redirect/Get)."""
    host = handler.headers.get("Host", "")
    origin = handler.headers.get("Origin") or handler.headers.get("Referer") or ""
    if origin and not re.match(rf"^https?://{re.escape(host)}(/|$)", origin):
        return _page(handler, 403, "Abgelehnt", "<p>Aktion von fremder Seite abgelehnt.</p>")
    f = parse_qs(raw_body.decode("utf-8", "replace"))
    aktion, datei = f.get("aktion", [""])[0], f.get("datei", [""])[0]
    ordner = "erledigt" if f.get("ordner", [""])[0] == "erledigt" else "offen"
    art = f.get("art", [""])[0]
    if not NAME_RE.match(datei) or aktion not in ("erledigt", "zurueck", "loeschen"):
        return _page(handler, 400, "Ungültig", "<p>Ungültige Anfrage.</p>")
    base = _base(data_dir, app)
    done = os.path.join(base, "erledigt")
    try:
        if aktion == "erledigt":
            os.makedirs(done, exist_ok=True)
            try:
                os.chmod(done, 0o777)
            except OSError:
                pass
            os.replace(os.path.join(base, datei), os.path.join(done, datei))
        elif aktion == "zurueck":
            os.replace(os.path.join(done, datei), os.path.join(base, datei))
        elif aktion == "loeschen":
            os.remove(os.path.join(done, datei))      # nur Erledigtes kann gelöscht werden
    except FileNotFoundError:
        pass
    loc = PATH + "?" + urlencode({k: v for k, v in {"ordner": ordner, "art": art if art in KINDS else ""}.items() if v})
    handler.send_response(303)
    handler.send_header("Location", loc)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


CSS = """
:root{--bg:#070b14;--panel:rgba(22,32,54,.7);--line:rgba(140,200,255,.18);--text:#e8eef8;--dim:#9aa8bf;--cyan:#3ee6ff;--ok:#4ee38a;--warn:#ffd21f;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:12px 16px 40px}
h1{font:800 18px/1.2 system-ui;letter-spacing:.08em;text-transform:uppercase;color:var(--cyan);margin:6px 0 14px}
nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
nav a{display:inline-flex;gap:6px;align-items:center;min-height:40px;padding:0 12px;border-radius:12px;border:1px solid var(--line);color:var(--text);text-decoration:none;background:rgba(255,255,255,.04)}
nav a.on{border-color:var(--cyan);background:rgba(62,230,255,.14)}
nav a b{font-weight:600;color:var(--dim);font-size:13px}
.tabs a{flex:1;justify-content:center;font-weight:600}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--dim);border-radius:14px;padding:12px 14px;margin:10px 0}
.k-fehler{border-left-color:var(--bad)}.k-idee{border-left-color:var(--warn)}.k-lob{border-left-color:var(--ok)}.k-sonstiges{border-left-color:var(--cyan)}
.card header{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--dim)}
.kind{font-weight:700;color:var(--text)}
.msg{white-space:pre-wrap;word-break:break-word;margin:8px 0}
.who{font-size:14px;color:var(--dim);margin:0 0 6px}.who a{color:var(--cyan)}
details{font-size:13px;color:var(--dim);margin:4px 0 8px}summary{cursor:pointer;min-height:32px;display:flex;align-items:center}
pre{white-space:pre-wrap;word-break:break-word;background:rgba(0,0,0,.35);border-radius:10px;padding:8px;margin:4px 0 0;font:12px/1.5 ui-monospace,monospace}
.actions{display:flex;gap:8px;justify-content:flex-end}.actions form{margin:0}
button{min-height:42px;padding:0 14px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.08);color:var(--text);font:600 15px system-ui;cursor:pointer}
button.danger{color:#ffc4c4;border-color:rgba(255,107,107,.5)}
.empty{color:var(--dim);text-align:center;padding:30px 0}
code{background:rgba(0,0,0,.4);padding:1px 5px;border-radius:5px}
"""


def _page(handler, code, title, body):
    doc = (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="robots" content="noindex,nofollow"><meta name="theme-color" content="#070b14">'
           f'<title>{html.escape(title)}</title><style>{CSS}</style></head>'
           f'<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>').encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "same-origin")   # sonst schickt der Browser "Origin: null"
    handler.send_header("Content-Length", str(len(doc)))
    handler.end_headers()
    handler.wfile.write(doc)
