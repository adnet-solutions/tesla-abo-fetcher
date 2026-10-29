# Changelog

Neue Versionen oben. `--check-update` und `--update` zeigen die Einträge an,
die neuer sind als die installierte Version – Format daher beibehalten:
`## [x.y.z] – Datum`, darunter Stichpunkte.

## [1.1.0] – 2026-10-29
- Neu: `--version` zeigt die installierte Version
- Neu: `--check-update` prüft auf GitHub, ob es eine neue Version gibt, und zeigt die Änderungen
- Neu: `--update` installiert die neue Version (per `git pull` oder direktem Download, z. B. in Home Assistant)

## [1.0.0] – 2026-10-29
- Premium-Connectivity-Rechnungen abholen, lokal ablegen und an GetMyInvoices weitergeben (API oder Mail)
- Nur neue Rechnungen oder `--reload`, Zeitraum per `--month`, `--year`, `--from`/`--to`
