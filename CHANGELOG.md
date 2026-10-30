# Changelog

Neue Versionen oben. `--check-update` und `--update` zeigen die Einträge an,
die neuer sind als die installierte Version – Format daher beibehalten:
`## [x.y.z] – Datum`, darunter Stichpunkte.

## [1.3.0] – 2026-10-30
- Neu: `gmi-sync` überträgt Tags, Zahlungsart und Zahlungsstatus aus der `config.ini` nachträglich auf Rechnungen, die schon per API an GMI gingen (Zeitraum wählbar, `--dry-run`). Braucht einen GMI-API-Schlüssel mit „Nur Upload & Update"

## [1.2.3] – 2026-10-30
- Neu: Zahlungsart für GMI einstellbar (`[gmi] payment_method`, z. B. `cc` = Kreditkarte). In die eigene `config.ini` übernehmen

## [1.2.2] – 2026-10-30
- Fix: Werte in der `config.ini` dürfen jetzt `#` enthalten (z. B. GMI-Tags `#ADNET`); bisher wurde alles ab `#` als Kommentar ignoriert. Kommentare am Zeilenende nur noch mit `;`

## [1.2.1] – 2026-10-30
- Fix: Tesla-Login – Tesla akzeptiert für den App-Zugang nur noch die Rücksprungadresse `tesla://auth/callback`
  (Fehler „redirect_uri … not registered"). Den Code jetzt aus den Browser-Entwicklertools kopieren, Anleitung zeigt `login` an
- Token-Abruf läuft über TLS 1.3, sonst stellt Tesla Tokens aus, die der Rechnungsabruf mit 403 ablehnt

## [1.2.0] – 2026-10-29
- Neu: Benachrichtigungen über Home Assistant (Companion-App oder Webhook), Pushover und WhatsApp (CallMeBot)
- Meldet neue Rechnungen, Fehler (z. B. abgelaufener Tesla-Login) und verfügbare Updates
- Neu: `--notify-test` zum Testen, `--no-notify` für stille Läufe
- Nach dem Update neue Abschnitte `[notify]`, `[notify_ha]`, `[notify_pushover]`, `[notify_whatsapp]` aus `config.example.ini` in die eigene `config.ini` übernehmen

## [1.1.0] – 2026-10-29
- Neu: `--version` zeigt die installierte Version
- Neu: `--check-update` prüft auf GitHub, ob es eine neue Version gibt, und zeigt die Änderungen
- Neu: `--update` installiert die neue Version (per `git pull` oder direktem Download, z. B. in Home Assistant)

## [1.0.0] – 2026-10-29
- Premium-Connectivity-Rechnungen abholen, lokal ablegen und an GetMyInvoices weitergeben (API oder Mail)
- Nur neue Rechnungen oder `--reload`, Zeitraum per `--month`, `--year`, `--from`/`--to`
