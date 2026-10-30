"""SQL-Auswertungen für das Dashboard. Umbuchungen (kind = transfer) zählen nicht mit."""
from datetime import date

NOT_TRANSFER = "COALESCE(c.kind, '') != 'transfer'"
UNCATEGORIZED = ("Unkategorisiert", "#94a3b8")


def month_range(period: str, today: date | None = None) -> list[str]:
    """Monate ('YYYY-MM') des Zeitraums: 'last12' oder ein Kalenderjahr."""
    today = today or date.today()
    if period.isdigit() and len(period) == 4:
        return [f"{period}-{m:02d}" for m in range(1, 13)]
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return months[::-1]


def bounds(months: list[str]) -> tuple[str, str]:
    """Halboffenes Datumsintervall [start, end) für die Monatsliste."""
    start = f"{months[0]}-01"
    y, m = map(int, months[-1].split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, f"{y}-{m:02d}-01"


def available_years(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(booking_date, 1, 4) AS y FROM transactions ORDER BY y DESC"
    ).fetchall()
    return [r["y"] for r in rows]


def monthly(conn, months, start, end) -> list[dict]:
    rows = conn.execute(
        f"""SELECT substr(t.booking_date, 1, 7) AS month,
                   SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END) AS income,
                   SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents ELSE 0 END) AS expense
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.booking_date >= ? AND t.booking_date < ? AND {NOT_TRANSFER}
            GROUP BY month""",
        (start, end),
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
        f"""SELECT COALESCE(c.name, ?) AS name, COALESCE(c.color, ?) AS color,
                   SUM({total}) AS total
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.booking_date >= ? AND t.booking_date < ? AND {sign} AND {NOT_TRANSFER}
            GROUP BY name, color ORDER BY total DESC""",
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
