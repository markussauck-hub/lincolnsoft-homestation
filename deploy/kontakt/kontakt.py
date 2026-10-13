#!/usr/bin/env python3
"""Lincolnsoft Homestation – Kontaktformular-Dienst

Nimmt das Formular von /impressum.html entgegen und schickt es per Gmail (App-Passwort) an dich weiter.
Nichts wird gespeichert: keine Datenbank, keine Nachricht im Log, IP-Adressen nur kurz im Arbeitsspeicher
für die Sperre gegen Massen-Anfragen.

    GET  /api/kontakt/token   -> {"t": "..."}   signierter Zeitstempel (Formular holt ihn beim Laden)
    POST /api/kontakt         -> {"ok": true} oder {"ok": false, "error": "..."}

Spam-Schutz ohne Captcha: verstecktes Feld (Honeypot), Mindestzeit zwischen Laden und Absenden,
Sperre pro IP (3 Nachrichten / 10 Min.), Tageslimit gesamt.

Konfiguration per Umgebung (docker compose env_file):
    SMTP_USER   Gmail-Adresse, über die gesendet wird
    SMTP_PASS   Google-App-Passwort (leer = Testmodus: Mail wird nur ins Log geschrieben)
    MAIL_TO     Empfänger (Standard: SMTP_USER)
    SECRET      zufälliger Schlüssel für die Zeitstempel (z. B. `openssl rand -hex 32`)
    DAILY_MAX   Nachrichten pro Tag gesamt (Standard 30)
"""
import hashlib
import hmac
import json
import os
import re
import smtplib
import ssl
import time
from collections import defaultdict, deque
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_TO = os.environ.get("MAIL_TO") or SMTP_USER
SECRET = (os.environ.get("SECRET") or os.urandom(32).hex()).encode()
DAILY_MAX = int(os.environ.get("DAILY_MAX", "30"))
PORT = int(os.environ.get("PORT", "8080"))

MIN_SECONDS, MAX_SECONDS = 4, 2 * 3600          # Formular frühestens nach 4 s, spätestens nach 2 h absenden
PER_IP, PER_IP_WINDOW = 3, 600
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")

lock = Lock()
recent = defaultdict(deque)                       # IP -> Zeitpunkte (nur Arbeitsspeicher)
day = {"date": "", "count": 0}


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def make_token():
    ts = str(int(time.time()))
    return ts + "." + hmac.new(SECRET, ts.encode(), hashlib.sha256).hexdigest()[:32]


def token_age(tok):
    try:
        ts, sig = str(tok).split(".", 1)
        good = hmac.new(SECRET, ts.encode(), hashlib.sha256).hexdigest()[:32]
        return time.time() - int(ts) if hmac.compare_digest(sig, good) else None
    except (ValueError, TypeError):
        return None


def clean(s, limit):
    s = str(s or "").replace("\r", "").strip()
    return s[:limit]


def allow(ip):
    now = time.time()
    with lock:
        q = recent[ip]
        while q and now - q[0] > PER_IP_WINDOW:
            q.popleft()
        for k in [k for k, v in recent.items() if not v]:   # leere Einträge sofort vergessen
            del recent[k]
        today = time.strftime("%Y-%m-%d")
        if day["date"] != today:
            day.update(date=today, count=0)
        if len(q) >= PER_IP or day["count"] >= DAILY_MAX:
            return False
        q.append(now)
        recent[ip] = q
        day["count"] += 1
        return True


def send(name, email, message):
    msg = EmailMessage()
    msg["Subject"] = f"[lincolnsoft.de] Kontakt von {name}"
    msg["From"] = f"Lincolnsoft Kontaktformular <{SMTP_USER or 'kontakt@lincolnsoft.de'}>"
    msg["To"] = MAIL_TO or "kontakt@lincolnsoft.de"
    msg["Reply-To"] = email
    msg.set_content(f"Name:   {name}\nE-Mail: {email}\n\n{message}\n\n-- \nGesendet über das Kontaktformular auf lincolnsoft.de\n")
    if not SMTP_PASS:
        log("TESTMODUS (kein SMTP_PASS) – diese Mail würde gesendet:\n" + msg.as_string())
        return
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


class Handler(BaseHTTPRequestHandler):
    server_version = "kontakt"
    sys_version = ""

    def log_message(self, *args):                 # Standard-Zugriffslog (mit IP) abschalten
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/kontakt/token":
            return self.reply(200, {"t": make_token()})
        self.reply(404, {"ok": False})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/kontakt":
            return self.reply(404, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 20000:
                return self.reply(413, {"ok": False, "error": "Nachricht zu lang."})
            data = json.loads(self.rfile.read(n))
        except (ValueError, OSError):
            return self.reply(400, {"ok": False, "error": "Ungültige Anfrage."})

        if clean(data.get("website"), 200):        # Honeypot ausgefüllt -> Bot; so tun, als hätte es geklappt
            log("verworfen: Honeypot")
            return self.reply(200, {"ok": True})
        age = token_age(data.get("t"))
        if age is None or age > MAX_SECONDS:
            return self.reply(400, {"ok": False, "error": "Formular abgelaufen – bitte Seite neu laden."})
        if age < MIN_SECONDS:
            log("verworfen: zu schnell")
            return self.reply(200, {"ok": True})

        name = clean(data.get("name"), 100).replace("\n", " ")
        email = clean(data.get("email"), 200).replace("\n", "")
        message = clean(data.get("message"), 5000)
        if not name or not EMAIL_RE.match(email) or len(message) < 10:
            return self.reply(422, {"ok": False, "error": "Bitte Name, gültige E-Mail und eine Nachricht (mind. 10 Zeichen) angeben."})

        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if not allow(ip):
            return self.reply(429, {"ok": False, "error": "Gerade zu viele Nachrichten – bitte später noch einmal."})
        try:
            send(name, email, message)
        except Exception as e:                    # Inhalt der Nachricht landet nie im Log, nur der Fehler
            log(f"Versand fehlgeschlagen: {type(e).__name__}: {e}")
            return self.reply(502, {"ok": False, "error": "Versand gerade nicht möglich – bitte später erneut."})
        log("gesendet")
        self.reply(200, {"ok": True})


if __name__ == "__main__":
    if not os.environ.get("SECRET"):
        log("Hinweis: SECRET nicht gesetzt – zufälliger Schlüssel, offene Formulare werden beim Neustart ungültig")
    log(f"Kontakt-Dienst auf Port {PORT}" + ("" if SMTP_PASS else " (TESTMODUS ohne SMTP_PASS)"))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
