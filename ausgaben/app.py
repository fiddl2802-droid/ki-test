"""Ausgaben-Tracker für das Gemeinschaftskonto (Flask + SQLite)."""
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import (Flask, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)

import importer
import reports

BASE_DIR = Path(__file__).parent
PAGE_SIZE = 100
MONTH_NAMES = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
               "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    color TEXT NOT NULL DEFAULT '#64748b'
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    booking_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    payee TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(booking_date);
"""

DEFAULT_CATEGORIES = [
    ("Gehalt", "income", "#16a34a"),
    ("Einzahlung Partner", "income", "#0f766e"),
    ("Sonstige Einnahmen", "income", "#65a30d"),
    ("Umbuchung / Sparen", "transfer", "#94a3b8"),
    ("Wohnen & Miete", "expense", "#2563eb"),
    ("Hauskredit", "expense", "#1e40af"),
    ("Haus & Garten", "expense", "#a16207"),
    ("Strom & Gas", "expense", "#0891b2"),
    ("Internet & Handy", "expense", "#7c3aed"),
    ("Versicherungen", "expense", "#4f46e5"),
    ("Lebensmittel", "expense", "#ea580c"),
    ("Restaurant & Café", "expense", "#d97706"),
    ("Mobilität & Auto", "expense", "#0d9488"),
    ("Shopping", "expense", "#db2777"),
    ("Gesundheit", "expense", "#dc2626"),
    ("Freizeit & Reisen", "expense", "#9333ea"),
    ("Abos", "expense", "#be185d"),
    ("Bargeld", "expense", "#78716c"),
    ("Sonstiges", "expense", "#64748b"),
]
DEFAULT_RULES = {
    "Lebensmittel": ["REWE", "EDEKA", "ALDI", "LIDL", "KAUFLAND", "NETTO", "PENNY", "NORMA",
                     "GLOBUS MARKTHALLE", "SPAR FIL", "HELLOFRESH", "FRISTO", "BACKEREI",
                     "BAECKEREI", "BÄCKEREI", "DM DROGERIE", "DM-DROGERIE", "DM.DE"],
    "Restaurant & Café": ["MCDONALD", "STARBUCKS", "LIEFERANDO", "TAKEAWAY", "RESTAURANT",
                          "PIZZA", "SUSHI"],
    "Mobilität & Auto": ["DEUTSCHE BAHN", "DB VERTRIEB", "SHELL", "ARAL", "ESSO", "AVIA",
                         "TANKSTELLE", "AUTOROUTE"],
    "Abos": ["NETFLIX", "SPOTIFY", "DISNEY", "AMAZON PRIME", "APPLE.COM"],
    "Shopping": ["AMAZON", "ZALANDO", "IKEA", "OTTO PAYMENTS", "KLARNA"],
    "Haus & Garten": ["BAUHAUS", "GLOBUS BAUMARKT", "HORNBACH", "TOOM"],
    "Hauskredit": ["BAUSPARKASSE"],
    "Internet & Handy": ["VODAFONE", "TELEKOM", "TELEFONICA", "1&1"],
    "Strom & Gas": ["FUXX", "NETZE BW", "STADTWERKE", "ENBW"],
    "Versicherungen": ["ALLIANZ", "HUK", "DEVK", "VERSICHERUNG"],
    "Wohnen & Miete": ["GEMEINDE", "RUNDFUNK", "BEITRAGSSERVICE"],
    "Gesundheit": ["APOTHEKE", "DOCMORRIS"],
    "Freizeit & Reisen": ["VAKANTIEPARK", "BOOKING.COM", "AIRBNB", "RYANAIR", "LUFTHANSA"],
    "Bargeld": ["GELDAUTOMAT", "BARGELDAUSZAHLUNG"],
    "Gehalt": ["GEHALT", "LOHN"],
}


def create_app() -> Flask:
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    Path(app.instance_path).mkdir(exist_ok=True)
    app.config["DATABASE"] = os.environ.get(
        "DATABASE", str(Path(app.instance_path) / "ausgaben.db"))
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    app.config["APP_PASSWORD"] = os.environ.get("APP_PASSWORD", "")
    app.config["SECRET_KEY"] = load_secret_key(Path(app.instance_path) / "secret_key")
    app.permanent_session_lifetime = timedelta(days=30)

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        init_db()

    register_filters(app)
    register_security(app)
    register_routes(app)
    return app


def load_secret_key(path: Path) -> str:
    if env := os.environ.get("SECRET_KEY"):
        return env
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        db.executemany("INSERT INTO categories (name, kind, color) VALUES (?, ?, ?)",
                       DEFAULT_CATEGORIES)
        ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM categories")}
        db.executemany(
            "INSERT INTO rules (pattern, category_id) VALUES (?, ?)",
            [(p, ids[name]) for name, patterns in DEFAULT_RULES.items() for p in patterns],
        )
    db.commit()


def apply_rules(db: sqlite3.Connection) -> int:
    """Kategorisiert nur Buchungen ohne Kategorie; manuelle Zuordnungen bleiben.

    Regeln für Einnahme-Kategorien greifen nur bei Eingängen, Regeln für
    Ausgabe-Kategorien nur bei Abgängen, Umbuchungen bei beiden.
    """
    rules = db.execute(
        """SELECT r.pattern, r.category_id, c.kind FROM rules r
           JOIN categories c ON c.id = r.category_id ORDER BY r.id"""
    ).fetchall()
    if not rules:
        return 0
    updates = []
    for row in db.execute(
            "SELECT id, payee, purpose, amount_cents FROM transactions WHERE category_id IS NULL"):
        haystack = f"{row['payee']} {row['purpose']}".lower()
        for rule in rules:
            if rule["kind"] == "income" and row["amount_cents"] <= 0:
                continue
            if rule["kind"] == "expense" and row["amount_cents"] >= 0:
                continue
            if rule["pattern"].lower() in haystack:
                updates.append((rule["category_id"], row["id"]))
                break
    db.executemany("UPDATE transactions SET category_id = ? WHERE id = ?", updates)
    return len(updates)


def register_filters(app: Flask) -> None:
    @app.template_filter("eur")
    def eur(cents):
        text = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{'-' if cents < 0 else ''}{text} €"

    @app.template_filter("datum")
    def datum(iso):
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"

    app.jinja_env.globals["csrf_token"] = csrf_token


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def register_security(app: Flask) -> None:
    @app.before_request
    def guard():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Ungültiges Formular-Token, bitte Seite neu laden.")
        needs_login = app.config["APP_PASSWORD"] and not session.get("auth")
        if needs_login and request.endpoint not in ("login", "static"):
            return redirect(url_for("login"))


def register_routes(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if secrets.compare_digest(password, app.config["APP_PASSWORD"]):
                session.permanent = True
                session["auth"] = True
                return redirect(url_for("dashboard"))
            flash("Falsches Passwort.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        db = get_db()
        period = request.args.get("period", "last12")
        months = reports.month_range(period)
        start, end = reports.bounds(months)
        monthly = reports.monthly(db, months, start, end)
        expense_cats = reports.by_category(db, start, end)
        income_cats = reports.by_category(db, start, end, income=True)
        payees = reports.top_payees(db, start, end)
        summary = reports.summary(monthly)

        total_expense = summary["expense"] or 1
        for cat in expense_cats:
            cat["share"] = round(cat["total"] / total_expense * 100, 1)
            cat["avg"] = round(cat["total"] / len(months))

        euros = lambda cents: round(cents / 100, 2)  # noqa: E731
        chart = {
            "labels": [f"{MONTH_NAMES[int(m[5:]) - 1]} {m[2:4]}" for m in months],
            "income": [euros(r["income"]) for r in monthly],
            "expense": [euros(r["expense"]) for r in monthly],
            "saldo": [euros(r["saldo"]) for r in monthly],
            "categories": {
                "labels": [c["name"] for c in expense_cats],
                "values": [euros(c["total"]) for c in expense_cats],
                "colors": [c["color"] for c in expense_cats],
            },
            "payees": {
                "labels": [p["name"] for p in payees],
                "values": [euros(p["total"]) for p in payees],
            },
        }
        return render_template(
            "dashboard.html", period=period, years=reports.available_years(db),
            summary=summary, expense_cats=expense_cats, income_cats=income_cats,
            chart=chart, has_data=any(r["income"] or r["expense"] for r in monthly),
        )

    @app.route("/transactions")
    def transactions():
        db = get_db()
        q = request.args.get("q", "").strip()
        cat = request.args.get("cat", "")
        page = max(request.args.get("page", 1, type=int), 1)

        where, params = [], []
        if q:
            where.append("(t.payee LIKE ? OR t.purpose LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if cat == "none":
            where.append("t.category_id IS NULL")
        elif cat.isdigit():
            where.append("t.category_id = ?")
            params.append(int(cat))
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        total = db.execute(f"SELECT COUNT(*) FROM transactions t {clause}", params).fetchone()[0]
        rows = db.execute(
            f"""SELECT t.* FROM transactions t {clause}
                ORDER BY t.booking_date DESC, t.id DESC LIMIT ? OFFSET ?""",
            (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
        ).fetchall()
        categories = db.execute("SELECT * FROM categories ORDER BY kind, name").fetchall()
        return render_template(
            "transactions.html", rows=rows, categories=categories, q=q, cat=cat,
            page=page, pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1), total=total,
        )

    @app.post("/transactions/<int:tx_id>/category")
    def set_category(tx_id):
        db = get_db()
        value = request.form.get("category_id", "")
        category_id = int(value) if value.isdigit() else None
        db.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (category_id, tx_id))
        db.commit()
        return redirect(request.referrer or url_for("transactions"))

    @app.route("/import", methods=["GET", "POST"])
    def import_csv():
        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                flash("Bitte eine CSV-Datei auswählen.", "error")
                return redirect(url_for("import_csv"))
            try:
                parsed, errors, pending = importer.parse_csv(upload.read())
            except importer.CsvImportError as exc:
                flash(str(exc), "error")
                return redirect(url_for("import_csv"))
            db = get_db()
            before = db.total_changes
            db.executemany(
                """INSERT OR IGNORE INTO transactions
                   (booking_date, amount_cents, payee, purpose, hash)
                   VALUES (:booking_date, :amount_cents, :payee, :purpose, :hash)""",
                parsed,
            )
            added = db.total_changes - before
            categorized = apply_rules(db)
            db.commit()
            flash(f"{added} neue Buchungen importiert, {len(parsed) - added} bereits vorhanden, "
                  f"{errors} Zeilen nicht lesbar, {pending} offene (noch nicht gebuchte) übersprungen. "
                  f"{categorized} automatisch kategorisiert.", "ok")
            return redirect(url_for("transactions", cat="none" if added else ""))
        return render_template("import.html")

    @app.route("/settings")
    def settings():
        db = get_db()
        categories = db.execute("SELECT * FROM categories ORDER BY kind, name").fetchall()
        rules = db.execute(
            """SELECT r.id, r.pattern, c.name AS category FROM rules r
               JOIN categories c ON c.id = r.category_id ORDER BY c.name, r.pattern"""
        ).fetchall()
        return render_template("settings.html", categories=categories, rules=rules)

    @app.post("/settings/categories")
    def add_category():
        name = request.form.get("name", "").strip()
        kind = request.form.get("kind", "expense")
        color = request.form.get("color", "#64748b")
        if not name or kind not in ("income", "expense", "transfer"):
            flash("Name und Art der Kategorie angeben.", "error")
        else:
            try:
                get_db().execute(
                    "INSERT INTO categories (name, kind, color) VALUES (?, ?, ?)",
                    (name, kind, color))
                get_db().commit()
            except sqlite3.IntegrityError:
                flash("Diese Kategorie gibt es schon.", "error")
        return redirect(url_for("settings"))

    @app.post("/settings/categories/<int:cat_id>/delete")
    def delete_category(cat_id):
        get_db().execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        get_db().commit()
        return redirect(url_for("settings"))

    @app.post("/settings/rules")
    def add_rule():
        pattern = request.form.get("pattern", "").strip()
        category_id = request.form.get("category_id", type=int)
        if not pattern or category_id is None:
            flash("Suchtext und Kategorie angeben.", "error")
        else:
            get_db().execute("INSERT INTO rules (pattern, category_id) VALUES (?, ?)",
                             (pattern, category_id))
            get_db().commit()
        return redirect(url_for("settings"))

    @app.post("/settings/rules/<int:rule_id>/delete")
    def delete_rule(rule_id):
        get_db().execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        get_db().commit()
        return redirect(url_for("settings"))

    @app.post("/settings/apply-rules")
    def run_rules():
        db = get_db()
        count = apply_rules(db)
        db.commit()
        flash(f"{count} Buchungen kategorisiert.", "ok")
        return redirect(url_for("settings"))


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
