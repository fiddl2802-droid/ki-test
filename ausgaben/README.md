# Ausgaben-Tracker für das Gemeinschaftskonto

Kleine Webanwendung (Flask + SQLite + Chart.js), die Kontoumsätze aus einem CSV-Export
einliest, automatisch in Kategorien einsortiert und als Dashboard auswertet.

## Installation auf der VM

```bash
cd ausgaben
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Starten

```bash
.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Danach ist die App im Heimnetz unter `http://<IP-der-VM>:8000` erreichbar. Es gibt kein Passwort: Die Startseite fragt nur, welches Konto geöffnet wird, und oben links lässt sich umschalten. Die App ist deshalb nur für das Heimnetz gedacht und darf nicht ins Internet freigegeben werden.
- Die Daten liegen in `instance/gemeinschaft/ausgaben.db` (eigene Datenbank per `DATABASE=/pfad/zur.db`).
- Der Flask-Entwicklungsserver reicht für zwei Personen im Heimnetz. Für Dauerbetrieb
  eignet sich z. B. `.venv/bin/pip install gunicorn` und
  `.venv/bin/gunicorn -b 0.0.0.0:8000 app:app`.

## Dauerbetrieb mit systemd (Beispiel)

`/etc/systemd/system/ausgaben.service`:

```ini
[Unit]
Description=Ausgaben-Tracker
After=network.target

[Service]
WorkingDirectory=/home/ocr/ki-test/ausgaben
ExecStart=/home/ocr/ki-test/ausgaben/.venv/bin/gunicorn -b 0.0.0.0:8000 app:app
Restart=on-failure
User=ocr

[Install]
WantedBy=multi-user.target
```

Dann `sudo systemctl enable --now ausgaben`.

## Benutzung

1. **Import:** CSV aus dem Online-Banking hochladen. Duplikate werden erkannt, überlappende
   Exporte sind also unkritisch.
2. **Buchungen:** Kategorie pro Buchung direkt in der Liste ändern.
3. **Einstellungen:** Regeln anlegen (Suchtext → Kategorie), eigene Kategorien definieren.
   Überweisungen zwischen eigenen Konten bekommen eine Kategorie der Art *Umbuchung*, damit sie
   die Einnahmen und Ausgaben nicht verfälschen.
4. **Auswertung:** letzte 12 Monate oder ein Kalenderjahr.

Der CSV-Parser erkennt Trennzeichen, Zeichensatz, Dezimalkomma und die Spalten (Buchungstag,
Betrag, Empfänger/Auftraggeber, Verwendungszweck) automatisch. Verwendet deine Bank andere
Spaltennamen, ergänze sie in `COLUMN_KEYS` in `importer.py`.
