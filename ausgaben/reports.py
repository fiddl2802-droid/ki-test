"""SQL-Auswertungen für das Dashboard. Umbuchungen (kind = transfer) zählen nicht mit.

Ein Haushaltsmonat beginnt am Wechsel-Tag `day` (1 = Kalendermonat) und trägt den
Namen des Monats, in dem er beginnt: Bei day=5 gehört der 31.01. und der 02.02. zu "Januar".
"""
from datetime import date, timedelta

NOT_TRANSFER = "COALESCE(c.kind, '') != 'transfer'"
UNCATEGORIZED = ("Unkategorisiert", "#94a3b8")


def shift(day: int) -> str:
    """SQLite-Modifier, der ein Datum so verschiebt, dass der Monatsname stimmt."""
    return f"-{day - 1} days"


def month_range(period: str, today: date | None = None, day: int = 1) -> list[str]:
    """Haushaltsmonate ('YYYY-MM') des Zeitraums: 'last12' oder ein Jahr."""
    if period.isdigit() and len(period) == 4:
        return [f"{period}-{m:02d}" for m in range(1, 13)]
    current = (today or date.today()) - timedelta(days=day - 1)
    months = []
    y, m = current.year, current.month
    for _ in range(12):
        months.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return months[::-1]


def bounds(months: list[str], day: int = 1) -> tuple[str, str]:
    """Halboffenes Datumsintervall [start, end) für die Monatsliste."""
    start = f"{months[0]}-{day:02d}"
    y, m = map(int, months[-1].split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, f"{y}-{m:02d}-{day:02d}"


def available_years(conn, day: int = 1) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT strftime('%Y', booking_date, ?) AS y
           FROM transactions ORDER BY y DESC""",
        (shift(day),),
    ).fetchall()
    return [r["y"] for r in rows]


def monthly(conn, months, start, end, day: int = 1) -> list[dict]:
    rows = conn.execute(
        f"""SELECT strftime('%Y-%m', t.booking_date, ?) AS month,
                   SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END) AS income,
                   SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents ELSE 0 END) AS expense
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.booking_date >= ? AND t.booking_date < ? AND {NOT_TRANSFER}
            GROUP BY month""",
        (shift(day), start, end),
    ).fetchall()
    by_month = {r["month"]: r for r in rows}
    result = []
    for m in months:
        r = by_month.get(m)
        income = r["income"] if r else 0
        expense = r["expense"] if r else 0
        result.append({"month": m, "income": income, "expense": expense,
                       "saldo": income - expense})
    return result


def by_category(conn, start, end, income: bool = False) -> list[dict]:
    sign = "t.amount_cents > 0" if income else "t.amount_cents < 0"
    total = "t.amount_cents" if income else "-t.amount_cents"
    rows = conn.execute(
        f"""SELECT COALESCE(CAST(c.id AS TEXT), 'none') AS cat_id,
                   COALESCE(c.name, ?) AS name, COALESCE(c.color, ?) AS color,
                   SUM({total}) AS total
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.booking_date >= ? AND t.booking_date < ? AND {sign} AND {NOT_TRANSFER}
            GROUP BY cat_id, name, color ORDER BY total DESC""",
        (*UNCATEGORIZED, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def top_payees(conn, start, end, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        f"""SELECT t.payee AS name, SUM(-t.amount_cents) AS total
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.booking_date >= ? AND t.booking_date < ? AND t.amount_cents < 0
                  AND t.payee != '' AND {NOT_TRANSFER}
            GROUP BY t.payee ORDER BY total DESC LIMIT ?""",
        (start, end, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _drill_where(start, end, income, cat, payee):
    where = ["t.booking_date >= ?", "t.booking_date < ?",
             "t.amount_cents > 0" if income else "t.amount_cents < 0", NOT_TRANSFER]
    params = [start, end]
    if cat == "none":
        where.append("t.category_id IS NULL")
    elif cat is not None:
        where.append("t.category_id = ?")
        params.append(int(cat))
    if payee is not None:
        where.append("t.payee = ?")
        params.append(payee)
    return " AND ".join(where), params


def payees_in(conn, start, end, income, cat) -> list[dict]:
    """Empfänger innerhalb einer Kategorie, für das Aufklappen im Dashboard."""
    where, params = _drill_where(start, end, income, cat, None)
    sign = "t.amount_cents" if income else "-t.amount_cents"
    rows = conn.execute(
        f"""SELECT t.payee AS name, SUM({sign}) AS total, COUNT(*) AS n
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE {where} GROUP BY t.payee ORDER BY total DESC""", params).fetchall()
    return [dict(r) for r in rows]


def bookings_in(conn, start, end, income, cat=None, payee=None) -> list[dict]:
    """Einzelbuchungen, die in eine Summe des Dashboards eingeflossen sind."""
    where, params = _drill_where(start, end, income, cat, payee)
    rows = conn.execute(
        f"""SELECT t.id, t.booking_date, t.amount_cents, t.payee, t.purpose,
                   COALESCE(c.name, ?) AS category
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE {where} ORDER BY t.booking_date DESC, t.id DESC""",
        (UNCATEGORIZED[0], *params)).fetchall()
    return [dict(r) for r in rows]


def summary(monthly_rows: list[dict]) -> dict:
    income = sum(r["income"] for r in monthly_rows)
    expense = sum(r["expense"] for r in monthly_rows)
    saldo = income - expense
    return {
        "income": income,
        "expense": expense,
        "saldo": saldo,
        "savings_rate": round(saldo / income * 100, 1) if income else None,
        "avg_expense": round(expense / len(monthly_rows)) if monthly_rows else 0,
    }
