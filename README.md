# tesla-abo-fetcher

Holt die Abo-Rechnungen für **Tesla Premium Connectivity** aus dem Tesla-Konto, legt sie lokal ab
und/oder schiebt sie direkt in GetMyInvoices (per API oder per Mail an die GMI-Importadresse).
Nur Python 3.8+ Standardbibliothek, keine Pakete nötig.

**Abgedeckt:** Premium-Connectivity-Rechnungen (Tesla-Optionscode `$CPF1`) für eine VIN.
**Nicht abgedeckt:** Supercharger-, Kauf-, Service- und andere Abo-Rechnungen (z. B. FSD).

> Die Tesla-Schnittstelle (`ownership.tesla.com`, Client `ownerapi`) ist inoffiziell –
> dieselbe, die die Tesla-App nutzt. Tesla kann sie jederzeit ändern.

## Dateien

| Datei | Zweck |
|---|---|
| `tesla_pc_invoices.py` | das Skript |
| `config.example.ini` | Vorlage für `config.ini` |
| `CHANGELOG.md` | Änderungen je Version |
| `LICENSE` | MIT-Lizenz |
| `tests/run_tests.py` | End-to-End-Test gegen nachgebaute Tesla-/GMI-/SMTP-/GitHub-Server (`python3 tests/run_tests.py`) |

`config.ini`, `state.json` (Tesla-Token!) und der Rechnungsordner stehen in `.gitignore`
und gehören nicht ins Repo.

## Einrichtung (einmalig)

```bash
git clone https://github.com/adnet-solutions/tesla-abo-fetcher.git
cd tesla-abo-fetcher
cp config.example.ini config.ini
chmod 600 config.ini
nano config.ini            # VIN, GMI-API-Key bzw. Mail-Daten eintragen
python3 tesla_pc_invoices.py login
```

**Login:** Das Skript zeigt eine Tesla-Adresse. Im Browser öffnen, normal anmelden (inkl. 2FA).
Danach landest du auf einer Seite „Page Not Found" – das ist korrekt. Die komplette Adresse
aus der Adresszeile (`https://auth.tesla.com/void/callback?code=...`) kopieren und im Terminal einfügen.
Der Login funktioniert auch auf einem anderen Rechner: dann einfach die `state.json` mitnehmen.

Alternativ einen Refresh-Token aus einer Token-App (z. B. „Auth app for Tesla") übernehmen:
`python3 tesla_pc_invoices.py set-token <REFRESH_TOKEN>`

Das Skript erneuert den Token bei jedem Lauf selbst und speichert ihn in `state.json`.

**Altbestand:** Wenn ältere Rechnungen schon in GMI liegen, die vorher als erledigt markieren,
sonst werden sie beim ersten Lauf mit hochgeladen:

```bash
python3 tesla_pc_invoices.py list                         # was liegt bei Tesla?
python3 tesla_pc_invoices.py run --to 2026-09 --mark-only # bis inkl. Sept. als erledigt merken
```

## Benutzung

```bash
python3 tesla_pc_invoices.py run                        # nur neue Rechnungen (Standard)
python3 tesla_pc_invoices.py run --dry-run              # zeigen, was passieren würde
python3 tesla_pc_invoices.py run --reload               # alle nochmal
python3 tesla_pc_invoices.py run --reload --year 2025   # alle aus 2025 nochmal
python3 tesla_pc_invoices.py run --reload --month 2026-08
python3 tesla_pc_invoices.py run --from 2026-01 --to 2026-06
python3 tesla_pc_invoices.py run --no-local --gmi api   # nur an GMI, nichts lokal
python3 tesla_pc_invoices.py run --local --gmi none     # nur lokal speichern
python3 tesla_pc_invoices.py list --year 2026           # Übersicht mit Status
```

„Neu" wird pro Ziel gemerkt (lokal / GMI-API / GMI-Mail). Wer später z. B. von Mail auf API
umstellt, bekommt beim nächsten Lauf alle bisher nicht per API gesendeten Rechnungen –
vorher ggf. `--mark-only` nutzen. `--reload` lädt in GMI erneut hoch; GMI meldet doppelte
Belege zwar meist als Duplikat, sauberer ist aber, es gezielt mit `--month` zu verwenden.

Exit-Codes: `0` ok, `1` einzelne Rechnungen fehlgeschlagen, `2` Konfigurations-/Login-Fehler.

## Automatisch auf dem Linux-Server

Cron (`crontab -e`), täglich um 7:12 – kostet nichts, wenn nichts Neues da ist:

```
12 7 * * * cd /opt/tesla-abo-fetcher && /usr/bin/python3 tesla_pc_invoices.py run >> run.log 2>&1
47 8 * * 1 cd /opt/tesla-abo-fetcher && /usr/bin/python3 tesla_pc_invoices.py --check-update >> run.log 2>&1
```

Die zweite Zeile schaut jeden Montag nach Updates und meldet sich (siehe Benachrichtigungen) nur,
wenn es eins gibt. Empfohlen ist der Betrieb auf einem separaten Linux-Rechner/Container statt
in Home Assistant: Der Tesla-Token landet so nicht in den HA-Backups, und `--update` kann
`git pull` nutzen. **Wichtig:** Nur an einer Stelle laufen lassen – Tesla tauscht den
Refresh-Token bei jeder Nutzung aus, zwei Installationen mit derselben `state.json` werfen sich
gegenseitig raus.

## Benachrichtigungen

Aufs Handy über **Home Assistant**, **Pushover** und/oder **WhatsApp** – in `config.ini`:

```ini
[notify]
channels = ha, pushover     ; beliebig kombinierbar, leer = aus
events = new, error, update
```

| Ereignis | Wann | Beispiel |
|---|---|---|
| `new` | neue Rechnungen verarbeitet | „Tesla: 1 neue Rechnung – 14.09.2026: 9,99 EUR -> lokal, GMI" |
| `error` | Fehler, z. B. Tesla-Login abgelaufen (wird als wichtig/durchstellend gesendet) | „Tesla-Login abgelaufen … bitte neu anmelden" |
| `update` | `--check-update` findet eine neue Version | „Update 1.3.0 – Installieren mit --update" |

Keine Meldung bei Läufen ohne neue Rechnungen, bei `--dry-run`, `--mark-only` und `--no-notify`.
Fällt ein Kanal aus, gehen die anderen trotzdem raus, und der Lauf selbst läuft weiter.

Testen: `python3 tesla_pc_invoices.py --notify-test`

**Home Assistant – direkt aufs iPhone** (empfohlen): In HA unter Profil > Sicherheit einen
langlebigen Zugangs-Token erstellen, in `[notify_ha]` die HA-Adresse, den Token und den
Notify-Dienst der Companion-App eintragen (`notify.mobile_app_…`, zu finden unter
Entwicklerwerkzeuge > Aktionen). Fehler kommen als „zeitkritisch" und gehen damit auch durch
den Fokus-Modus.

**Home Assistant – per Webhook** (wenn HA selbst entscheiden soll, z. B. TTS oder Dashboard):
`webhook_id` setzen, Token/Service leer lassen und in HA:

```yaml
alias: Tesla-Rechnungen Meldung
triggers:
  - trigger: webhook
    webhook_id: tesla_abo_fetcher        # = webhook_id in config.ini
    allowed_methods: [POST]
    local_only: true
actions:
  - action: notify.mobile_app_iphone
    data:
      title: "{{ trigger.json.title }}"
      message: "{{ trigger.json.message }}"
```

**Pushover**: User Key von der Startseite pushover.net, unter „Create an Application" ein
App-Token anlegen. Beides in `[notify_pushover]`.

**WhatsApp**: Läuft über den kostenlosen Dienst CallMeBot (nur an die eigene Nummer, nur private
Nutzung, inoffiziell – eine offizielle WhatsApp-API für Privatleute gibt es nicht). Einrichtung:
+34 623 78 95 95 als Kontakt speichern, per WhatsApp „I allow callmebot to send me messages"
schicken, den API-Key aus der Antwort mit deiner Nummer in `[notify_whatsapp]` eintragen.
Hinweis: Der Nachrichtentext läuft dabei über den Server von CallMeBot.

## Automatisch in Home Assistant

1. Ordner `tesla-abo-fetcher` nach `/config/tesla-abo-fetcher/` kopieren (Samba/File-Editor).
2. Login einmal ausführen – im Terminal-Add-on (SSH) oder auf einem anderen Rechner und dann
   `state.json` mitkopieren.
3. In `configuration.yaml`:

```yaml
shell_command:
  tesla_pc_invoices: "python3 /config/tesla-abo-fetcher/tesla_pc_invoices.py -c /config/tesla-abo-fetcher/config.ini run"
```

4. Automation (täglich 7:12, bei Fehler Benachrichtigung aufs Handy):

```yaml
alias: Tesla Connectivity-Rechnungen abholen
triggers:
  - trigger: time
    at: "07:12:00"
actions:
  - action: shell_command.tesla_pc_invoices
    response_variable: result
  - if:
      - condition: template
        value_template: "{{ result.returncode != 0 }}"
    then:
      - action: notify.notify
        data:
          title: Tesla-Rechnungen
          message: "{{ result.stdout[-400:] }}"
mode: single
```

`notify.notify` ggf. durch deinen Handy-Dienst ersetzen (z. B. `notify.mobile_app_...`).
Bei Exit-Code 2 mit „neu anmelden" einfach `login` erneut ausführen.

## Version und Updates

```bash
python3 tesla_pc_invoices.py --version        # installierte Version
python3 tesla_pc_invoices.py --check-update   # gibt's was Neues? zeigt Version + Änderungen
python3 tesla_pc_invoices.py --update         # neue Version installieren
python3 tesla_pc_invoices.py --update --force # aktuelle Version neu installieren
```

Die Kurzformen `-version`, `-checkupdate` und `-update` gehen auch.

- **Mit Git geklont** (Linux-Server): `--update` macht ein `git pull --ff-only`.
- **Ohne Git kopiert** (z. B. Home Assistant): `--update` lädt Skript, README, CHANGELOG und
  `config.example.ini` direkt von GitHub. Das alte Skript bleibt als
  `tesla_pc_invoices.py.<version>.bak` liegen. Ein fehlerhaft heruntergeladenes Skript wird
  nicht installiert.
- `config.ini`, `state.json` und die Rechnungen werden nie angefasst.
- Neue Einstellungen stehen nach einem Update in `config.example.ini` – bei Bedarf in die
  eigene `config.ini` übernehmen.

`--check-update` endet mit Exit-Code `3`, wenn ein Update da ist. Damit kann Home Assistant
Bescheid geben, z. B. wöchentlich:

```yaml
shell_command:
  tesla_abo_check_update: "python3 /config/tesla-abo-fetcher/tesla_pc_invoices.py -c /config/tesla-abo-fetcher/config.ini --check-update"
```

Die Automation dazu wie oben, nur mit `result.returncode == 3` als Bedingung.

Änderungen je Version: [CHANGELOG.md](CHANGELOG.md).

## Fehlersuche

`--debug` zeigt die Rohantwort von Tesla. Falls Tesla das Format ändert oder den Zugang dichtmacht
(die Schnittstelle ist inoffiziell), sieht man dort, was zurückkommt.

## Lizenz und Haftung

MIT-Lizenz, siehe [LICENSE](LICENSE). Privates Hilfsskript ohne Gewähr und ohne Support.
Kein offizielles Produkt von Tesla, GetMyInvoices, Pushover oder CallMeBot; es nutzt eine
inoffizielle Tesla-Schnittstelle, die sich jederzeit ändern kann.
