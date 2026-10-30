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
APP_PASSWORD='dein-passwort' .venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Danach ist die App im Heimnetz unter `http://<IP-der-VM>:8000` erreichbar.

- `APP_PASSWORD` ist optional. Ist es gesetzt, verlangt die App ein gemeinsames Passwort.
- Die Daten liegen in `instance/ausgaben.db` (eigene Datenbank per `DATABASE=/pfad/zur.db`).
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
Environment=APP_PASSWORD=dein-passwort
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
