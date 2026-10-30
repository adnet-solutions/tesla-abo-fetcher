#!/usr/bin/env python3
"""
tesla_pc_invoices.py – Tesla Premium-Connectivity-Rechnungen abholen
und lokal ablegen und/oder an GetMyInvoices (API oder Mail) weitergeben.

Nur Python-Standardbibliothek (Python >= 3.8), läuft auf jedem Linux-Server
und im Home-Assistant-Container.

Befehle:
  login                 Einmalig bei Tesla anmelden (Browser + URL einfügen)
  set-token TOKEN       Alternativ: vorhandenen Refresh-Token übernehmen
  list                  Verfügbare Rechnungen anzeigen (mit Status)
  run                   Neue Rechnungen verarbeiten (Standard)

Wartung:
  --version             installierte Version anzeigen
  --check-update        auf GitHub nach neuer Version schauen
  --update [--force]    neue Version von GitHub installieren
  --notify-test         Testnachricht an HA / Pushover / WhatsApp
  --no-notify           für diesen Lauf keine Benachrichtigungen

Wichtige Optionen für list/run:
  --month 2026-09       nur dieser Monat
  --year 2026           nur dieses Jahr
  --from 2026-01 --to 2026-06   Zeitraum (Monate inklusive)
  --reload              auch bereits erledigte Rechnungen erneut verarbeiten
  --local / --no-local  lokale Ablage an/aus (überschreibt config.ini)
  --gmi api|mail|none   Weitergabe an GMI (überschreibt config.ini)
  --mark-only           nichts senden, nur als erledigt markieren
  --dry-run             nur anzeigen, was passieren würde
"""

import argparse
import base64
import configparser
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

VERSION = "1.2.2"
DEFAULT_REPO = "adnet-solutions/tesla-abo-fetcher"
SCRIPT_DIR = Path(__file__).resolve().parent

OWNERAPI_CLIENT_ID = "ownerapi"
# Seit 2026 ist für ownerapi nur noch diese Rücksprungadresse registriert.
OWNERAPI_REDIRECT_URI = "tesla://auth/callback"
OWNERAPI_SCOPES = "openid email offline_access"
PC_OPTION_CODE = "$CPF1"  # Premium Connectivity

GMI_DEFAULT_BASE = "https://api.getmyinvoices.com/accounts/v3"


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

class Fail(Exception):
    """Fehler mit verständlicher Meldung für den Nutzer."""


DEBUG = False


def log(msg):
    print(msg, flush=True)


def debug(msg):
    if DEBUG:
        print(f"[debug] {msg}", flush=True)


def http(method, url, *, headers=None, json_body=None, form=None, expect="json", timeout=60, ssl_context=None):
    data = None
    hdrs = {"Accept": "application/json" if expect == "json" else "*/*"}
    hdrs.update(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    debug(f"{method} {url.split('?')[0]}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read()
        raise HttpError(e.code, body.decode("utf-8", "replace")[:500], url) from None
    except urllib.error.URLError as e:
        raise Fail(f"Verbindung zu {urllib.parse.urlsplit(url).netloc} fehlgeschlagen: {e.reason}") from None
    debug(f"-> {status}, {len(body)} Bytes")
    if expect == "json":
        try:
            return json.loads(body.decode("utf-8") or "null")
        except ValueError:
            raise Fail(f"Unerwartete Antwort (kein JSON) von {url.split('?')[0]}: {body[:200]!r}") from None
    return body


class HttpError(Fail):
    def __init__(self, code, body, url):
        self.code = code
        self.body = body
        super().__init__(f"HTTP {code} von {url.split('?')[0]}: {body}")


def parse_date(value):
    """Versucht, ein Rechnungsdatum robust in ein date zu wandeln."""
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    for fmt in ("%m/%d/%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    if s.isdigit():  # Unix-Zeitstempel (s oder ms)
        n = int(s)
        if n > 10**11:
            n //= 1000
        return dt.datetime.utcfromtimestamp(n).date()
    return None


def parse_month(value, arg):
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", value or "")
    if not m or not 1 <= int(m[2]) <= 12:
        raise Fail(f"{arg} erwartet JJJJ-MM, z. B. 2026-09 (bekommen: {value!r})")
    return int(m[1]), int(m[2])


def atomic_write(path, data: bytes, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


LOGIN_HELP = """
Tesla-Login – so geht's (Chrome, Edge, Brave oder Firefox, NICHT Safari):

1) Neuen Browser-Tab öffnen, die Entwicklertools aufmachen
   (Mac: Cmd+Option+I, Windows/Linux: F12), Reiter "Network"/"Netzwerk" wählen
   und das Häkchen "Preserve log"/"Log beibehalten" setzen.

2) In diesem Tab folgende Adresse öffnen und mit dem Tesla-Konto anmelden:

{url}

3) Nach dem Login passiert scheinbar nichts, oder der Browser fragt, mit welcher
   App er "tesla://" öffnen soll (abbrechen). Das ist richtig so.

4) In der Netzwerk-Liste den Eintrag suchen, der mit "tesla://auth/callback?code="
   beginnt (oder den letzten Eintrag "authorize" mit Status 302 anklicken und unter
   Headers -> Response Headers den Wert von "Location" nehmen).
   Rechtsklick -> Copy -> Copy URL bzw. den Location-Wert kopieren und hier einfügen.
   Der Code ist nur wenige Minuten gültig.
"""


def extract_code(pasted, expected_state):
    """Holt den Login-Code aus der eingefügten Adresse (oder nimmt einen nackten Code)."""
    pasted = pasted.strip().strip('"\'')
    if pasted.lower().startswith("location:"):
        pasted = pasted.split(":", 1)[1].strip()
    if "code=" not in pasted and re.fullmatch(r"[A-Za-z0-9._~\-]{20,}", pasted):
        log("Hinweis: nur der Code eingefügt, State-Prüfung wird übersprungen.")
        return pasted
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query or pasted.split("?", 1)[-1])
    if q.get("error"):
        raise Fail(f"Tesla meldet: {q['error'][0]} {q.get('error_description', [''])[0]}".strip())
    code = (q.get("code") or [None])[0]
    if not code:
        raise Fail("In der eingefügten Adresse steckt kein 'code='. Bitte 'login' nochmal starten.")
    if (q.get("state") or [expected_state])[0] != expected_state:
        raise Fail("State passt nicht – die Adresse stammt aus einem anderen Login-Versuch. "
                   "Bitte 'login' neu starten und nur den neuen Link verwenden.")
    return code


def tls13_context():
    ctx = ssl.create_default_context()
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    except (AttributeError, ValueError):  # sehr altes OpenSSL ohne TLS 1.3
        debug("TLS 1.3 nicht verfügbar, nutze Standard")
    return ctx


# --------------------------------------------------------------------------
# Konfiguration und Status
# --------------------------------------------------------------------------

class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)  # "#" erlaubt, z. B. für GMI-Tags
        if not self.path.exists():
            raise Fail(f"Konfiguration nicht gefunden: {self.path}\n"
                       f"Kopiere config.example.ini nach config.ini und trage deine Daten ein.")
        self.cp.read(self.path, encoding="utf-8")

    def get(self, section, key, default=""):
        return self.cp.get(section, key, fallback=default).strip()

    def bool(self, section, key, default=False):
        return self.cp.getboolean(section, key, fallback=default)

    def resolve(self, p):
        p = Path(os.path.expanduser(p))
        return p if p.is_absolute() else (self.path.parent / p)


class State:
    """state.json: Tokens + welche Rechnung schon wohin geliefert wurde."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"tokens": {}, "invoices": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        atomic_write(self.path, json.dumps(self.data, indent=2, ensure_ascii=False).encode())

    @property
    def tokens(self):
        return self.data.setdefault("tokens", {})

    def inv(self, inv_id):
        return self.data.setdefault("invoices", {}).setdefault(inv_id, {})

    def done(self, inv_id, target):
        return target in self.data.get("invoices", {}).get(inv_id, {})

    def mark(self, inv_id, target, **extra):
        rec = self.inv(inv_id)
        rec[target] = dt.datetime.now().isoformat(timespec="seconds")
        rec.update({k: v for k, v in extra.items() if v})


# --------------------------------------------------------------------------
# Tesla
# --------------------------------------------------------------------------

class Tesla:
    def __init__(self, cfg: Config, state: State):
        self.cfg = cfg
        self.state = state
        self.auth_base = cfg.get("tesla", "auth_base", "https://auth.tesla.com").rstrip("/")
        self.own_base = cfg.get("tesla", "ownership_base", "https://ownership.tesla.com").rstrip("/")
        self.vin = cfg.get("tesla", "vin").upper()
        self.country = cfg.get("tesla", "country", "DE")
        self.language = cfg.get("tesla", "language", "de")
        self.locale = cfg.get("tesla", "locale", "de_DE")
        self.user_agent = cfg.get("tesla", "user_agent", f"tesla-pc-invoices/{VERSION}")

    # --- Login -------------------------------------------------------------

    def login_interactive(self):
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        st = secrets.token_urlsafe(16)
        params = {
            "client_id": OWNERAPI_CLIENT_ID,
            "redirect_uri": OWNERAPI_REDIRECT_URI,
            "response_type": "code",
            "scope": OWNERAPI_SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": st,
            "locale": self.locale.replace("_", "-"),
        }
        url = f"{self.auth_base}/oauth2/v3/authorize?{urllib.parse.urlencode(params)}"
        log(LOGIN_HELP.format(url=url))
        code = extract_code(input("Adresse: "), st)
        tok = self._token({
            "grant_type": "authorization_code",
            "client_id": OWNERAPI_CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": OWNERAPI_REDIRECT_URI,
        })
        self._store(tok)
        log("\nLogin erfolgreich, Token gespeichert.")

    def set_refresh_token(self, token):
        self.state.tokens.clear()
        self.state.tokens["refresh_token"] = token.strip()
        self.state.save()
        self.access_token()  # sofort prüfen + rotieren
        log("Refresh-Token übernommen und geprüft.")

    def _store(self, tok):
        if "access_token" not in tok:
            raise Fail(f"Tesla hat keinen Token geliefert: {tok}")
        t = self.state.tokens
        t["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):  # Tesla rotiert den Refresh-Token
            t["refresh_token"] = tok["refresh_token"]
        t["expires_at"] = int(dt.datetime.now().timestamp()) + int(tok.get("expires_in", 28800)) - 300
        self.state.save()

    def _token(self, form):
        """Token-Abruf: formularkodiert und über TLS 1.3. Über ältere TLS-Versionen
        stellt Tesla Tokens aus, die ownership.tesla.com mit 403 ablehnt."""
        try:
            return http("POST", f"{self.auth_base}/oauth2/v3/token", form=form,
                        headers={"User-Agent": self.user_agent}, ssl_context=tls13_context())
        except HttpError as e:
            if form["grant_type"] == "authorization_code":
                if "redirect" in e.body.lower():
                    raise Fail("Tesla lehnt die Rücksprungadresse ab. Bitte Skript aktualisieren (--update).") from None
                raise Fail(f"Tesla hat den Login-Code nicht akzeptiert (HTTP {e.code}). Der Code ist nur "
                           f"wenige Minuten gültig und nur einmal verwendbar – 'login' bitte neu starten.\n"
                           f"Antwort: {e.body[:200]}") from None
            raise

    def access_token(self):
        t = self.state.tokens
        if t.get("access_token") and t.get("expires_at", 0) > dt.datetime.now().timestamp():
            return t["access_token"]
        if not t.get("refresh_token"):
            raise Fail("Noch kein Tesla-Login vorhanden. Bitte zuerst: tesla_pc_invoices.py login")
        try:
            tok = self._token({
                "grant_type": "refresh_token",
                "client_id": OWNERAPI_CLIENT_ID,
                "refresh_token": t["refresh_token"],
                "scope": OWNERAPI_SCOPES,
            })
        except HttpError as e:
            if e.code in (400, 401, 403):
                raise Fail("Tesla-Login abgelaufen oder ungültig. Bitte neu anmelden: tesla_pc_invoices.py login") from None
            raise
        self._store(tok)
        return t["access_token"]

    # --- Rechnungen ----------------------------------------------------------

    def _get(self, path, params, expect="json"):
        if not self.vin:
            raise Fail("In config.ini fehlt [tesla] vin = ...")
        q = {"deviceLanguage": self.language, "deviceCountry": self.country,
             "httpLocale": self.locale, "vin": self.vin, **params}
        url = f"{self.own_base}{path}?{urllib.parse.urlencode(q)}"
        hdrs = {"Authorization": f"Bearer {self.access_token()}", "User-Agent": self.user_agent}
        try:
            return http("GET", url, headers=hdrs, expect=expect)
        except HttpError as e:
            if e.code == 401:  # Token evtl. serverseitig ungültig -> einmal erneuern
                self.state.tokens.pop("access_token", None)
                hdrs["Authorization"] = f"Bearer {self.access_token()}"
                return http("GET", url, headers=hdrs, expect=expect)
            raise

    def invoices(self):
        raw = self._get("/mobile-app/subscriptions/invoices", {"optionCode": PC_OPTION_CODE})
        debug("Rohantwort Rechnungsliste:\n" + json.dumps(raw, indent=2, ensure_ascii=False)[:4000])
        items = raw
        if isinstance(raw, dict):
            for key in ("data", "invoices", "Invoices", "response", "results"):
                if isinstance(raw.get(key), list):
                    items = raw[key]
                    break
                if isinstance(raw.get(key), dict):
                    inner = raw[key]
                    for k2 in ("invoices", "Invoices", "data"):
                        if isinstance(inner.get(k2), list):
                            items = inner[k2]
                            break
        if not isinstance(items, list):
            raise Fail("Rechnungsliste hat ein unbekanntes Format. Mit --debug starten und die Ausgabe prüfen.")

        out = []
        for it in items:
            g = lambda *keys: next((it[k] for k in keys if it.get(k) not in (None, "")), None)
            inv_id = g("InvoiceId", "invoiceId", "id", "Id", "uuid", "contentId")
            if not inv_id:
                debug(f"Eintrag ohne ID übersprungen: {it}")
                continue
            date_raw = g("InvoiceDate", "invoiceDate", "date", "BillingDate", "billingDate", "createdAt")
            out.append({
                "id": str(inv_id),
                "date": parse_date(date_raw),
                "date_raw": date_raw,
                "file_name": g("InvoiceFileName", "invoiceFileName", "fileName"),
                "amount": g("Amount", "amount", "TotalAmount", "totalAmount", "Total", "total"),
                "currency": g("Currency", "currency", "CurrencyCode", "currencyCode") or "EUR",
                "number": g("InvoiceNumber", "invoiceNumber", "DocumentNumber"),
            })
        out.sort(key=lambda x: (x["date"] or dt.date.min, x["id"]))
        return out

    def download(self, inv_id):
        pdf = self._get(f"/mobile-app/documents/invoices/{urllib.parse.quote(inv_id)}", {}, expect="bytes")
        if not pdf.startswith(b"%PDF"):
            raise Fail(f"Rechnung {inv_id}: Antwort ist kein PDF ({pdf[:120]!r})")
        return pdf


# --------------------------------------------------------------------------
# Ziele: lokal, GMI-API, GMI-Mail
# --------------------------------------------------------------------------

def file_name_for(cfg, inv):
    pattern = cfg.get("output", "filename", "Tesla_PremiumConnectivity_{date}_{id}.pdf")
    d = inv["date"]
    name = pattern.format(
        date=d.isoformat() if d else "unbekannt",
        year=d.year if d else "0000", month=f"{d.month:02d}" if d else "00",
        id=inv["id"][:8], id_full=inv["id"], number=inv["number"] or inv["id"][:8],
    )
    return re.sub(r"[^\w.\-]+", "_", name)


def save_local(cfg, inv, pdf):
    directory = cfg.resolve(cfg.get("output", "directory", "rechnungen"))
    if cfg.bool("output", "year_subfolders", True) and inv["date"]:
        directory = directory / str(inv["date"].year)
    path = directory / file_name_for(cfg, inv)
    atomic_write(path, pdf, mode=0o644)
    return path


def send_gmi_api(cfg, inv, pdf, fname):
    key = cfg.get("gmi", "api_key")
    if not key:
        raise Fail("GMI per API gewählt, aber [gmi] api_key fehlt.")
    base = cfg.get("gmi", "api_base", GMI_DEFAULT_BASE).rstrip("/")
    body = {
        "fileName": fname,
        "fileContent": base64.b64encode(pdf).decode(),
        "documentType": cfg.get("gmi", "document_type", "INCOMING_INVOICE"),
        "runOCR": cfg.bool("gmi", "run_ocr", True),
    }
    if inv["date"]:
        body["documentDate"] = inv["date"].isoformat()
    if inv["number"]:
        body["documentNumber"] = str(inv["number"])
    tags = [t.strip() for t in cfg.get("gmi", "tags", "Tesla, Premium Connectivity").split(",") if t.strip()]
    if tags:
        body["tags"] = tags
    if cfg.get("gmi", "company_id"):
        body["companyId"] = int(cfg.get("gmi", "company_id"))
    if cfg.get("gmi", "payment_status"):
        body["paymentStatus"] = cfg.get("gmi", "payment_status")
    note = cfg.get("gmi", "note", "Automatisch importiert: Tesla Premium Connectivity")
    if note:
        body["note"] = note
    headers = {"X-API-KEY": key, "User-Agent": cfg.get("gmi", "user_agent", f"tesla-pc-invoices/{VERSION}")}
    res = http("POST", f"{base}/documents", headers=headers, json_body=body)
    debug(f"GMI-Antwort: {res}")
    if isinstance(res, dict) and (res.get("error") or res.get("errors")):
        raise Fail(f"GMI hat abgelehnt: {res}")
    return (res or {}).get("documentUid") if isinstance(res, dict) else None


def send_mail(cfg, inv, pdf, fname):
    to = cfg.get("mail", "to")
    host = cfg.get("mail", "smtp_host")
    if not to or not host:
        raise Fail("GMI per Mail gewählt, aber [mail] to oder smtp_host fehlt.")
    sender = cfg.get("mail", "from") or cfg.get("mail", "smtp_user")
    d = inv["date"].strftime("%d.%m.%Y") if inv["date"] else "?"
    msg = EmailMessage()
    msg["Subject"] = cfg.get("mail", "subject", "Tesla Premium Connectivity Rechnung {date}").format(
        date=d, id=inv["id"][:8])
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(sender.split("@")[-1] if "@" in sender else None))
    amount = f"{inv['amount']} {inv['currency']}" if inv["amount"] else "siehe PDF"
    msg.set_content(f"Tesla Premium Connectivity\nRechnungsdatum: {d}\nBetrag: {amount}\n"
                    f"VIN: {cfg.get('tesla', 'vin')}\n\nAutomatisch versendet von tesla_pc_invoices.py")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=fname)

    port = int(cfg.get("mail", "smtp_port", "587"))
    security = cfg.get("mail", "smtp_security", "starttls").lower()
    user, pw = cfg.get("mail", "smtp_user"), cfg.get("mail", "smtp_password")
    ctx = ssl.create_default_context()
    if security == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
    else:
        smtp = smtplib.SMTP(host, port, timeout=60)
    with smtp:
        if security == "starttls":
            smtp.starttls(context=ctx)
        if user:
            smtp.login(user, pw)
        smtp.send_message(msg)


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def filter_invoices(invs, args):
    def in_range(d):
        if args.month:
            y, m = parse_month(args.month, "--month")
            return d is not None and (d.year, d.month) == (y, m)
        if args.year:
            return d is not None and d.year == int(args.year)
        lo = parse_month(args.from_, "--from") if args.from_ else None
        hi = parse_month(args.to, "--to") if args.to else None
        if (lo or hi) and d is None:
            return False
        if lo and (d.year, d.month) < lo:
            return False
        if hi and (d.year, d.month) > hi:
            return False
        return True
    return [i for i in invs if in_range(i["date"])]


def active_targets(cfg, args):
    local = cfg.bool("output", "local", True) if args.local is None else args.local
    gmi = (args.gmi or cfg.get("output", "gmi", "none")).lower()
    if gmi not in ("api", "mail", "none"):
        raise Fail(f"Ungültiger Wert für gmi: {gmi!r} (erlaubt: api, mail, none)")
    targets = []
    if local:
        targets.append("local")
    if gmi == "api":
        targets.append("gmi_api")
    elif gmi == "mail":
        targets.append("gmi_mail")
    if not targets:
        raise Fail("Kein Ziel aktiv: weder lokale Ablage noch GMI. Nichts zu tun.")
    return targets


def cmd_list(cfg, state, tesla, args):
    invs = filter_invoices(tesla.invoices(), args)
    if not invs:
        log("Keine Rechnungen im gewählten Zeitraum gefunden.")
        return 0
    log(f"{'Datum':<11} {'Betrag':>10}  {'ID':<10} Status")
    for i in invs:
        rec = state.data.get("invoices", {}).get(i["id"], {})
        status = ", ".join(t for t in ("local", "gmi_api", "gmi_mail") if t in rec) or "neu"
        amt = f"{i['amount']} {i['currency']}" if i["amount"] else "-"
        log(f"{(i['date'] or '?')!s:<11} {amt:>10}  {i['id'][:8]:<10} {status}")
    log(f"\n{len(invs)} Rechnung(en).")
    return 0


def cmd_run(cfg, state, tesla, args, notifier=None):
    targets = active_targets(cfg, args)
    invs = filter_invoices(tesla.invoices(), args)
    log(f"{len(invs)} Rechnung(en) im Zeitraum, Ziele: {', '.join(targets)}"
        f"{' (reload)' if args.reload else ''}{' (dry-run)' if args.dry_run else ''}")

    errors = 0
    processed = 0
    done_lines, error_lines = [], []
    for inv in invs:
        todo = [t for t in targets if args.reload or not state.done(inv["id"], t)]
        if not todo:
            continue
        label = f"{inv['date'] or '?'} ({inv['id'][:8]})"
        if args.dry_run:
            log(f"  würde verarbeiten: {label} -> {', '.join(todo)}")
            continue
        if args.mark_only:
            for t in todo:
                state.mark(inv["id"], t, date=str(inv["date"] or ""))
            state.save()
            log(f"  markiert: {label} -> {', '.join(todo)}")
            continue
        try:
            pdf = tesla.download(inv["id"])
            fname = file_name_for(cfg, inv)
            for t in todo:
                if t == "local":
                    path = save_local(cfg, inv, pdf)
                    state.mark(inv["id"], t, date=str(inv["date"] or ""), file=str(path))
                    log(f"  gespeichert: {label} -> {path}")
                elif t == "gmi_api":
                    uid = send_gmi_api(cfg, inv, pdf, fname)
                    state.mark(inv["id"], t, date=str(inv["date"] or ""), gmi_uid=uid)
                    log(f"  an GMI (API): {label}{f' -> {uid}' if uid else ''}")
                elif t == "gmi_mail":
                    send_mail(cfg, inv, pdf, fname)
                    state.mark(inv["id"], t, date=str(inv["date"] or ""))
                    log(f"  an GMI (Mail): {label}")
                state.save()  # nach jedem Ziel sichern, damit nichts doppelt geht
            processed += 1
            done_lines.append(f"{fmt_date(inv['date'])}: {fmt_amount(inv)} -> {', '.join(TARGET_LABELS[t] for t in todo)}")
        except (Fail, smtplib.SMTPException, OSError) as e:
            errors += 1
            log(f"  FEHLER bei {label}: {e}")
            error_lines.append(f"{fmt_date(inv['date'])}: {e}")

    if not args.dry_run:
        log(f"Fertig: {processed} verarbeitet, {errors} Fehler.")
    if notifier and not args.dry_run and not args.mark_only:
        if processed:
            n = "1 neue Rechnung" if processed == 1 else f"{processed} neue Rechnungen"
            notifier.send("new", f"Tesla: {n}", "\n".join(done_lines))
        if errors:
            notifier.send("error", "Tesla-Rechnungen: Fehler",
                          f"{errors} Rechnung(en) nicht verarbeitet:\n" + "\n".join(error_lines)[:900],
                          important=True)
    return 1 if errors else 0


TARGET_LABELS = {"local": "lokal", "gmi_api": "GMI", "gmi_mail": "GMI (Mail)"}


def fmt_date(d):
    return d.strftime("%d.%m.%Y") if d else "?"


def fmt_amount(inv):
    if inv.get("amount") in (None, ""):
        return "Betrag siehe PDF"
    try:
        return f"{float(inv['amount']):.2f}".replace(".", ",") + f" {inv['currency']}"
    except (TypeError, ValueError):
        return f"{inv['amount']} {inv['currency']}"


# --------------------------------------------------------------------------
# Benachrichtigungen: Home Assistant, Pushover, WhatsApp (CallMeBot)
# --------------------------------------------------------------------------

NOTIFY_CHANNELS = ("ha", "pushover", "whatsapp")
NOTIFY_EVENTS = ("new", "error", "update")


class Notifier:
    def __init__(self, cfg, enabled=True):
        self.cfg = cfg
        raw = cfg.get("notify", "channels", "") if cfg else ""
        self.channels = [c.strip().lower() for c in raw.split(",") if c.strip()]
        for c in self.channels:
            if c not in NOTIFY_CHANNELS:
                raise Fail(f"Unbekannter Benachrichtigungskanal {c!r} (erlaubt: {', '.join(NOTIFY_CHANNELS)})")
        ev = cfg.get("notify", "events", "new, error, update") if cfg else ""
        self.events = {e.strip().lower() for e in ev.split(",") if e.strip()}
        self.enabled = enabled

    def send(self, event, title, message, important=False, force=False):
        """Schickt an alle Kanäle. Fehler beim Senden brechen nie den Lauf ab."""
        if not self.channels or not (force or (self.enabled and event in self.events)):
            return 0
        ok = 0
        for ch in self.channels:
            try:
                getattr(self, f"_{ch}")(title, message, important)
                debug(f"Benachrichtigung über {ch} gesendet")
                ok += 1
            except Exception as e:  # noqa: BLE001 – Benachrichtigung darf nie stören
                log(f"  Benachrichtigung über {ch} fehlgeschlagen: {e}")
        return ok

    def _ha(self, title, message, important):
        c = self.cfg
        base = c.get("notify_ha", "url").rstrip("/")
        if not base:
            raise Fail("[notify_ha] url fehlt")
        ctx = None
        if not c.bool("notify_ha", "verify_ssl", True):
            ctx = ssl._create_unverified_context()
        webhook = c.get("notify_ha", "webhook_id")
        if webhook:
            http("POST", f"{base}/api/webhook/{urllib.parse.quote(webhook)}", expect="bytes", ssl_context=ctx,
                 json_body={"title": title, "message": message, "important": important,
                            "source": "tesla-abo-fetcher"})
            return
        token, service = c.get("notify_ha", "token"), c.get("notify_ha", "service")
        if not token or not service:
            raise Fail("[notify_ha] braucht token + service oder webhook_id")
        domain, _, name = service.partition(".")
        if not name:
            domain, name = "notify", service
        body = {"title": title, "message": message}
        if important and name.startswith("mobile_app_"):
            body["data"] = {"push": {"interruption-level": "time-sensitive"}}  # iOS: durchstellen
        http("POST", f"{base}/api/services/{domain}/{name}", json_body=body, ssl_context=ctx,
             headers={"Authorization": f"Bearer {token}"})

    def _pushover(self, title, message, important):
        c = self.cfg
        token, user = c.get("notify_pushover", "app_token"), c.get("notify_pushover", "user_key")
        if not token or not user:
            raise Fail("[notify_pushover] app_token und user_key fehlen")
        form = {"token": token, "user": user, "title": title, "message": message,
                "priority": c.get("notify_pushover", "priority_error" if important else "priority", "1" if important else "0")}
        if c.get("notify_pushover", "device"):
            form["device"] = c.get("notify_pushover", "device")
        url = c.get("notify_pushover", "api_url", "https://api.pushover.net/1/messages.json")
        res = http("POST", url, form=form)
        if not isinstance(res, dict) or res.get("status") != 1:
            raise Fail(f"Pushover meldet: {res}")

    def _whatsapp(self, title, message, important):
        c = self.cfg
        phone, key = c.get("notify_whatsapp", "phone"), c.get("notify_whatsapp", "apikey")
        if not phone or not key:
            raise Fail("[notify_whatsapp] phone und apikey fehlen")
        text = f"*{title}*\n{message}"
        url = c.get("notify_whatsapp", "api_url", "https://api.callmebot.com/whatsapp.php")
        q = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
        body = http("GET", f"{url}?{q}", expect="bytes").decode("utf-8", "replace")
        debug(f"CallMeBot: {body[:300]}")
        if re.search(r"(?i)apikey is invalid|invalid apikey|not activated", body):
            raise Fail("CallMeBot lehnt ab – API-Key/Telefonnummer prüfen")


def notify_test(notifier):
    if not notifier.channels:
        log("Keine Benachrichtigungskanäle in config.ini ([notify] channels) eingetragen.")
        return 2
    n = notifier.send("test", "Tesla-Rechnungen: Test",
                      f"Testnachricht von tesla-abo-fetcher {VERSION}. Wenn du das liest, passt alles.", force=True)
    log(f"Test an {n} von {len(notifier.channels)} Kanal/Kanälen erfolgreich gesendet.")
    return 0 if n == len(notifier.channels) else 1


# --------------------------------------------------------------------------
# Updates aus GitHub
# --------------------------------------------------------------------------

# Dateien, die ein Update ersetzt. config.ini, state.json und Rechnungen
# werden nie angefasst.
UPDATE_FILES = ["tesla_pc_invoices.py", "config.example.ini", "README.md", "CHANGELOG.md"]


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


class Updater:
    def __init__(self, cfg=None):
        get = (lambda k, d: cfg.get("update", k, d)) if cfg else (lambda k, d: d)
        self.repo = get("repo", DEFAULT_REPO)
        self.branch = get("branch", "main")
        self.token = get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")
        self.api = os.environ.get("TPI_GITHUB_API", "https://api.github.com").rstrip("/")

    def fetch(self, path):
        url = (f"{self.api}/repos/{self.repo}/contents/{urllib.parse.quote(path)}"
               f"?ref={urllib.parse.quote(self.branch)}")
        hdrs = {"Accept": "application/vnd.github.raw", "User-Agent": f"tesla-abo-fetcher/{VERSION}"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        try:
            return http("GET", url, headers=hdrs, expect="bytes")
        except HttpError as e:
            if e.code == 404:
                raise FileNotFoundError(path) from None
            if e.code in (401, 403):
                raise Fail(f"GitHub verweigert den Zugriff (HTTP {e.code}). Bei privatem Repo oder "
                           f"Rate-Limit in config.ini unter [update] github_token setzen.") from None
            raise

    def remote(self):
        try:
            script = self.fetch("tesla_pc_invoices.py")
        except FileNotFoundError:
            raise Fail(f"Skript im Repo {self.repo} (Branch {self.branch}) nicht gefunden. "
                       f"Bei privatem Repo [update] github_token setzen.") from None
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', script.decode("utf-8", "replace"), re.M)
        if not m:
            raise Fail("Im Skript auf GitHub ist keine VERSION zu finden.")
        return m[1], script

    def changes_since(self, local_version):
        """Einträge aus CHANGELOG.md, die neuer sind als die lokale Version."""
        try:
            text = self.fetch("CHANGELOG.md").decode("utf-8", "replace")
        except FileNotFoundError:
            return []
        out, cur = [], None
        for line in text.splitlines():
            m = re.match(r"^##\s*\[?v?(\d+\.\d+\.\d+)\]?(.*)$", line)
            if m:
                cur = None
                if version_tuple(m[1]) > version_tuple(local_version):
                    cur = [f"{m[1]}{m[2]}".strip()]
                    out.append(cur)
            elif cur is not None and line.strip():
                cur.append(line.rstrip())
        return out


def cmd_check_update(upd, notifier=None):
    remote_version, _ = upd.remote()
    log(f"Installiert: {VERSION}")
    log(f"Auf GitHub:  {remote_version}  ({upd.repo}, Branch {upd.branch})")
    if version_tuple(remote_version) <= version_tuple(VERSION):
        log("Du bist auf dem aktuellen Stand.")
        return 0
    log(f"\nUpdate verfügbar: {VERSION} -> {remote_version}")
    changes = upd.changes_since(VERSION)
    for entry in changes:
        log(f"\n  {entry[0]}")
        for line in entry[1:]:
            log(f"    {line}")
    if notifier:
        details = "\n".join(l.strip() for e in changes for l in e[1:])[:700]
        notifier.send("update", f"Tesla-Rechnungen: Update {remote_version}",
                      f"Installiert: {VERSION}, neu: {remote_version}\n{details}\n"
                      f"Installieren mit --update")
    log("\nInstallieren mit: python3 tesla_pc_invoices.py --update")
    return 3


def cmd_update(upd, force=False):
    remote_version, script = upd.remote()
    newer = version_tuple(remote_version) > version_tuple(VERSION)
    if not newer and not force:
        log(f"Bereits aktuell ({VERSION}). Mit --force trotzdem neu installieren.")
        return 0

    # 1) Git-Checkout: einfach pullen
    if (SCRIPT_DIR / ".git").exists() and shutil.which("git"):
        log(f"Git-Checkout erkannt, hole {upd.branch} ...")
        res = subprocess.run(["git", "-C", str(SCRIPT_DIR), "pull", "--ff-only", "origin", upd.branch],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise Fail("git pull fehlgeschlagen (lokale Änderungen?):\n" + (res.stderr or res.stdout).strip())
        log(res.stdout.strip() or "ok")
        log(f"Update fertig: {VERSION} -> {remote_version}")
        return 0

    # 2) Kopie ohne Git (z. B. Home Assistant): Dateien direkt laden
    try:
        compile(script, "tesla_pc_invoices.py", "exec")
    except SyntaxError as e:
        raise Fail(f"Heruntergeladenes Skript ist fehlerhaft, Update abgebrochen: {e}") from None
    files = {"tesla_pc_invoices.py": script}
    for name in UPDATE_FILES[1:]:
        try:
            files[name] = upd.fetch(name)
        except FileNotFoundError:
            debug(f"{name} nicht im Repo, übersprungen")
    own = SCRIPT_DIR / "tesla_pc_invoices.py"
    backup = own.with_name(f"tesla_pc_invoices.py.{VERSION}.bak")
    shutil.copy2(own, backup)
    for name, data in files.items():
        mode = 0o755 if name.endswith(".py") else 0o644
        atomic_write(SCRIPT_DIR / name, data, mode=mode)
        log(f"  aktualisiert: {name}")
    log(f"Update fertig: {VERSION} -> {remote_version} (Sicherung: {backup.name})")
    for entry in upd.changes_since(VERSION):
        log(f"  {entry[0]}")
        for line in entry[1:]:
            log(f"    {line}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Tesla Premium-Connectivity-Rechnungen abholen und an GetMyInvoices weitergeben.")
    p.add_argument("-c", "--config", default=str(SCRIPT_DIR / "config.ini"), help="Pfad zur config.ini")
    p.add_argument("--debug", action="store_true", help="ausführliche Ausgabe")
    p.add_argument("-v", "--version", "-version", action="version", version=f"tesla-abo-fetcher {VERSION}",
                   help="installierte Version anzeigen")
    p.add_argument("--check-update", "--checkupdate", "-checkupdate", dest="check_update", action="store_true",
                   help="auf GitHub nach neuer Version schauen (Exit-Code 3 = Update verfügbar)")
    p.add_argument("--update", "-update", dest="update", action="store_true",
                   help="neue Version von GitHub installieren")
    p.add_argument("--force", action="store_true", help="mit --update: auch ohne neue Version neu installieren")
    p.add_argument("--notify-test", "-notifytest", dest="notify_test", action="store_true",
                   help="Testnachricht an alle eingerichteten Kanäle schicken")
    p.add_argument("--no-notify", dest="no_notify", action="store_true",
                   help="für diesen Lauf keine Benachrichtigungen")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("login", help="einmalig bei Tesla anmelden")
    st = sub.add_parser("set-token", help="vorhandenen Tesla-Refresh-Token übernehmen")
    st.add_argument("token")

    def add_filters(sp):
        g = sp.add_argument_group("Zeitraum")
        g.add_argument("--month", metavar="JJJJ-MM", help="nur dieser Monat, z. B. 2026-09")
        g.add_argument("--year", type=int, metavar="JJJJ", help="nur dieses Jahr, z. B. 2026")
        g.add_argument("--from", dest="from_", metavar="JJJJ-MM", help="ab Monat JJJJ-MM (inklusive)")
        g.add_argument("--to", metavar="JJJJ-MM", help="bis Monat JJJJ-MM (inklusive)")

    ls = sub.add_parser("list", help="Rechnungen anzeigen")
    add_filters(ls)

    rn = sub.add_parser("run", help="neue Rechnungen verarbeiten (Standard)")
    add_filters(rn)
    rn.add_argument("--reload", "--all", dest="reload", action="store_true",
                    help="auch bereits erledigte Rechnungen erneut verarbeiten")
    lg = rn.add_mutually_exclusive_group()
    lg.add_argument("--local", dest="local", action="store_true", default=None, help="lokal speichern")
    lg.add_argument("--no-local", dest="local", action="store_false", help="nicht lokal speichern")
    rn.add_argument("--gmi", choices=["api", "mail", "none"], help="Weitergabe an GMI")
    rn.add_argument("--mark-only", action="store_true",
                    help="nichts herunterladen/senden, nur als erledigt markieren")
    rn.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts tun")
    rn.add_argument("--no-notify", dest="no_notify", action="store_true", default=argparse.SUPPRESS,
                    help="für diesen Lauf keine Benachrichtigungen")
    return p


def main(argv=None):
    global DEBUG
    parser = build_parser()
    args = parser.parse_args(argv)
    DEBUG = args.debug
    if args.check_update or args.update or args.notify_test:
        try:
            cfg = None
            if Path(args.config).exists():
                cfg = Config(args.config)
            notifier = Notifier(cfg, enabled=not args.no_notify)
            if args.notify_test:
                if not cfg:
                    raise Fail(f"Konfiguration nicht gefunden: {args.config}")
                return notify_test(notifier)
            upd = Updater(cfg)
            return cmd_update(upd, args.force) if args.update else cmd_check_update(upd, notifier)
        except Fail as e:
            log(f"FEHLER: {e}")
            return 2
    if not args.cmd:
        args = parser.parse_args((argv or sys.argv[1:]) + ["run"])
        DEBUG = args.debug
    if getattr(args, "month", None) and (args.year or args.from_ or args.to):
        parser.error("--month nicht zusammen mit --year/--from/--to verwenden")
    if getattr(args, "year", None) and (args.from_ or args.to):
        parser.error("--year nicht zusammen mit --from/--to verwenden")

    notifier = None
    try:
        cfg = Config(args.config)
        notifier = Notifier(cfg, enabled=not args.no_notify and args.cmd == "run")
        state = State(cfg.resolve(cfg.get("output", "state_file", "state.json")))
        tesla = Tesla(cfg, state)
        if args.cmd == "login":
            tesla.login_interactive()
            return 0
        if args.cmd == "set-token":
            tesla.set_refresh_token(args.token)
            return 0
        if args.cmd == "list":
            return cmd_list(cfg, state, tesla, args)
        return cmd_run(cfg, state, tesla, args, notifier)
    except Fail as e:
        log(f"FEHLER: {e}")
        if notifier and not getattr(args, "dry_run", False):
            notifier.send("error", "Tesla-Rechnungen: Fehler", str(e)[:900], important=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
