"""Даты в заявлениях: вычисляемые поля (окончание академа, отпуска по уходу) и ограничения (рабочие дни, день подачи).

Рабочие дни — понедельник–пятница, кроме праздников из настроек МФЦ (holidays). Список праздников и переносов
на следующий год правительство утверждает осенью — его нужно дополнять в кабинете сотрудника.
"""
import calendar
import datetime as dt

# Нерабочие праздничные дни и переносы. 2027 — только сами праздники: переносы ещё не утверждены.
HOLIDAYS = [
    "2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
    "2026-02-23", "2026-03-09", "2026-05-01", "2026-05-11", "2026-06-12", "2026-11-04", "2026-12-31",
    "2027-01-01", "2027-01-04", "2027-01-05", "2027-01-06", "2027-01-07", "2027-01-08",
    "2027-02-23", "2027-03-08", "2027-05-03", "2027-05-10", "2027-06-14", "2027-11-04",
]


def parse(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def add(d, years=0, months=0, days=0):
    m = d.month - 1 + months + 12 * years
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1])) + dt.timedelta(days=days)


def is_workday(d, holidays):
    return d.weekday() < 5 and d.isoformat() not in holidays


def earliest(rule, today, holidays):
    """Самая ранняя допустимая дата по правилу min. День подачи не считается."""
    if not rule:
        return None
    if rule.get("after_today"):
        return today + dt.timedelta(days=1)
    if rule.get("today"):                      # не раньше дня подачи (сам день подачи можно)
        return today
    n = int(rule.get("workdays") or 0)
    if n:
        d, k = today, 0
        while k < n:   # отсчитываем 5 рабочих дней после дня подачи; уйти можно в 5-й (подал в ср 14.10 → с ср 21.10)
            d += dt.timedelta(days=1)
            if is_workday(d, holidays):
                k += 1
        return d
    return None


def workday_after(d, n, holidays):
    """n-й рабочий день после d (сам d не считается)."""
    k = 0
    while k < n:
        d += dt.timedelta(days=1)
        if is_workday(d, holidays):
            k += 1
    return d


def calc(spec, vals):
    """Значение вычисляемого поля (дата ISO) или ''. {"join": [поля]} — значения полей через пробел."""
    if spec.get("join"):
        return " ".join(x for x in (str(vals.get(k) or "").strip() for k in spec["join"]) if x)
    base = parse(vals.get(spec.get("from")))
    if not base:
        return ""
    y, m, d = spec.get("years", 0), spec.get("months", 0), spec.get("days", 0)
    if spec.get("by"):
        step = (spec.get("map") or {}).get(vals.get(spec["by"]))
        if not step:
            return ""
        y, m, d = y + step.get("years", 0), m + step.get("months", 0), d + step.get("days", 0)
    if spec.get("days_field"):
        try:
            extra = int(str(vals.get(spec["days_field"]) or "").strip())
        except ValueError:
            return ""
        if extra <= 0:
            return ""
        d += extra
    return add(base, y, m, d).isoformat()


def fmt(d):
    return d.strftime("%d.%m.%Y") if d else ""
