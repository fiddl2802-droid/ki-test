"""CSV-Import für Kontoauszüge deutscher Banken."""
import csv
import hashlib
import io
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Mögliche Spaltenüberschriften je Feld, in Reihenfolge der Präferenz.
COLUMN_KEYS = {
    "date": ["buchungstag", "buchungsdatum", "buchung", "datum", "valuta"],
    "amount": ["betrag", "umsatz", "amount"],
    "payee": ["begünstigter", "beguenstigter", "empfänger", "empfaenger",
              "auftraggeber", "zahlungspartner", "name"],
    "purpose": ["verwendungszweck", "buchungstext", "beschreibung"],
}
DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%Y-%m-%d")
# Noch nicht gebuchte Umsätze haben statt eines Datums z. B. "offen".
PENDING_MARKERS = ("offen", "--", "vorgemerkt")
# Banken ohne eigene Empfängerspalte schreiben den Namen in den Buchungstext.
PAYEE_IN_TEXT = re.compile(
    r"(?:Auftraggeber|Empfänger):\s*(.+?)\s*(?=Kto/IBAN:|BLZ/BIC:|Buchungstext:|Ref\.|$)")
PAYEE_CARD_TEXT = re.compile(
    r"Buchungstext:\s*(.+?)(?:,|\s{2,}|\s+Karte Nr\.|\s+Ref\.|$)")
MAX_HEADER_SEARCH = 30


class CsvImportError(Exception):
    pass


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
    if "Ã" in text:
        # UTF-8 wurde irrtümlich als cp1252 gelesen und erneut als UTF-8 gespeichert.
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def extract_payee(text: str) -> str:
    for pattern in (PAYEE_IN_TEXT, PAYEE_CARD_TEXT):
        match = pattern.search(text)
        if match:
            return clean(match.group(1))[:80]
    return ""


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:MAX_HEADER_SEARCH])
    counts = {d: sample.count(d) for d in (";", "\t", ",")}
    return max(counts, key=counts.get)


def find_column(header: list[str], keys: list[str]) -> int | None:
    cells = [c.strip().lower() for c in header]
    for key in keys:
        for i, cell in enumerate(cells):
            if cell == key:
                return i
        for i, cell in enumerate(cells):
            if key in cell:
                return i
    return None


def parse_amount(value: str) -> int:
    s = value.replace("€", "").replace("EUR", "").replace(" ", "").replace("+", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation:
        raise ValueError(f"Betrag nicht lesbar: {value!r}")


def parse_date(value: str) -> str:
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Datum nicht lesbar: {value!r}")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_csv(raw: bytes) -> tuple[list[dict], int, int]:
    """Liefert (Buchungen, Anzahl fehlerhafter Zeilen, Anzahl offener Buchungen)."""
    text = decode(raw)
    rows = list(csv.reader(io.StringIO(text), delimiter=detect_delimiter(text)))

    cols = None
    header_idx = None
    for idx, row in enumerate(rows[:MAX_HEADER_SEARCH]):
        date_col = find_column(row, COLUMN_KEYS["date"])
        amount_col = find_column(row, COLUMN_KEYS["amount"])
        if date_col is not None and amount_col is not None:
            cols = {
                "date": date_col,
                "amount": amount_col,
                "payee": find_column(row, COLUMN_KEYS["payee"]),
                "purpose": find_column(row, COLUMN_KEYS["purpose"]),
            }
            header_idx = idx
            break
    if cols is None:
        raise CsvImportError(
            "Keine Kopfzeile mit Datum und Betrag gefunden. "
            "Ist das ein CSV-Export des Online-Bankings?"
        )

    def cell(row, key):
        i = cols[key]
        return row[i] if i is not None and i < len(row) else ""

    transactions, errors, pending = [], 0, 0
    seen = Counter()
    for row in rows[header_idx + 1:]:
        if not any(c.strip() for c in row):
            continue
        if not cell(row, "amount").strip():
            continue  # Fußzeilen wie "Alter Kontostand"
        if cell(row, "date").strip().lower() in PENDING_MARKERS:
            pending += 1
            continue
        try:
            date = parse_date(cell(row, "date"))
            cents = parse_amount(cell(row, "amount"))
        except ValueError:
            errors += 1
            continue
        raw_purpose = cell(row, "purpose")
        payee = clean(cell(row, "payee")) or extract_payee(raw_purpose)
        purpose = clean(raw_purpose)
        key = f"{date}|{cents}|{payee}|{purpose}"
        seen[key] += 1
        digest = hashlib.sha256(f"{key}|{seen[key]}".encode()).hexdigest()
        transactions.append({
            "booking_date": date, "amount_cents": cents,
            "payee": payee, "purpose": purpose, "hash": digest,
        })
    return transactions, errors, pending
