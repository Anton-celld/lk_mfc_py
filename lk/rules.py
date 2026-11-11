"""Проверка заказа по правилам ТЗ. Всё проверяется на сервере — браузеру не доверяем."""
import datetime as dt
import re

import calendar

from . import apps, dates
from .catalog import FOREIGN_EXTRACT_QTY, STUDY_KINDS, extract_allowed, src_opts

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
STUDY_TITLE = {"ecp": "Справка о подтверждении обучения с ЭЦП",
               "gerb": "Справка о подтверждении обучения с гербовой печатью (бумажный экземпляр)",
               "umu": "Справка о подтверждении обучения по месту требования (бумажный экземпляр)"}


OTHER = "_иное"


def nmin_of(f, vals):
    """Минимум числового поля: число или {"by": поле, "map": {вариант: минимум}} (срок беременности: 30 / 28 недель)."""
    x = f.get("nmin")
    if isinstance(x, dict):
        x = (x.get("map") or {}).get(vals.get(x.get("by") or ""))
    return int(x) if x is not None else None


def workdays_msg(n):
    words = {5: "пять", 3: "три", 10: "десять"}
    return (f"Срок начала отпуска устанавливается не ранее чем через {words.get(n, n)} рабочих дней с момента подачи заявления, "
            "исключая день подачи, в соответствии со сроком рассмотрения заявления по Положению")


def income_last_month(today, open_days=4):
    """Последний месяц, за который можно заказать справку о доходах: прошлый. Текущий открывается
    за open_days дней до 1-го числа следующего месяца (31 октября при open_days=4 — с 28 октября)."""
    last = calendar.monthrange(today.year, today.month)[1]
    if open_days and last - today.day + 1 <= open_days:
        return f"{today.year}-{today.month:02d}"
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y}-{m:02d}"


def inn_ok(s):
    """Контрольные цифры ИНН: 10 цифр — организация, 12 — ИП или физлицо."""
    if not re.fullmatch(r"\d{10}|\d{12}", s or ""):
        return False
    d = [int(x) for x in s]

    def c(w):
        return sum(a * b for a, b in zip(w, d)) % 11 % 10
    if len(s) == 10:
        return c([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    return c([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10] and c([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11]


def qty_error(n, limit):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return "Укажите количество экземпляров — от 1"
    if n > limit:
        return (f"Превышен лимит: в одной заявке можно не больше {limit} бумажных экземпляров (вы указали {n}). "
                "Если нужно больше, подайте ещё одну заявку.")
    return ""


def month_name(k):
    y, m = k.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def _date(v):
    try:
        return dt.date.fromisoformat(v) if DATE_RE.match(v or "") else None
    except ValueError:
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def validate(svc, p, *, settings, tpl=None, prikazy=(), today=None, org=None, prog=None, foreign=False):
    """svc — услуга из каталога, p — что прислал студент. org — организация, уже найденная по ИНН на сервере.
    Возвращает (данные заказа, заголовок, ошибки по полям)."""
    today = today or dt.date.today()
    E, data = {}, {}
    email = str(p.get("email") or "").strip()[:120]
    if not EMAIL_RE.match(email):
        E["email"] = "Укажите почту — на неё придут уведомления о заявке"
    data["email"], data["phone"] = email, str(p.get("phone") or "").strip()[:30]
    sid, title = svc["id"], svc["t"]

    def need_org(required):
        if org:
            data["org"] = org
        elif p.get("inn"):
            E["org"] = "Организация с таким ИНН не найдена в ЕГРЮЛ или ликвидирована"
        elif required:
            E["org"] = "Выберите организацию по ИНН"

    def need_qty():
        n = _int(p.get("qty"))
        err = qty_error(n, settings["limit"])
        if err:
            E["qty"] = err
        data["qty"] = n

    if sid == "study":
        kind = p.get("kind")
        from .catalog import NO_ECP
        if kind == "ecp" and (prog or {}).get("level") in NO_ECP:
            E["kind"] = "Аспирантам справка с ЭЦП не выдаётся — выберите гербовую или по месту требования"
            kind = "umu"
        if kind not in STUDY_KINDS:
            E["kind"] = "Выберите вид справки"
            kind = "umu"
        k = STUDY_KINDS[kind]
        data["kind"] = kind
        if k["org"] != "none":           # организация по ИНН — только у гербовой
            need_org(k["org"] == "required")
        if k["qty"]:
            need_qty()
        if not data.get("org"):
            data["place"] = "по месту требования"
        title = STUDY_TITLE[kind]
        data["ecp"], data["paper"] = (0, 0) if kind == "ecp" else (0, 1)
        if kind == "ecp":
            data["ecp"] = 1
    elif sid == "call":
        # основание — всегда одно из шести (список МФЦ); если по нему есть период в графике учебного процесса
        # (1С или загруженный КУГ) — даты берутся из графика, иначе студент указывает их сам
        reason = str(p.get("reason") or "").strip()
        if reason not in (settings.get("call_reasons") or []):
            E["reason"] = "Выберите основание вызова из списка"
        sched = [x for x in (prog or {}).get("calls") or [] if str(x.get("to") or "") >= today.isoformat() and x.get("reason") == reason]
        a = b = None
        if sched:
            it = next((x for x in sched if str(x.get("id")) == str(p.get("call") or "")), None)
            if not it and len(sched) == 1:
                it = sched[0]
            if not it:
                E["call"] = "Выберите период по графику учебного процесса"
            else:
                a, b = _date(it["from"]), _date(it["to"])
                data["call"] = str(it["id"])
        else:
            a, b = _date(p.get("from")), _date(p.get("to"))
            if not a:
                E["from"] = "Укажите дату начала"
            if not b:
                E["to"] = "Укажите дату окончания"
            if a and b and b < a:
                E["to"] = "Дата окончания раньше даты начала"
            elif a and b and (b - a).days > 365:
                E["to"] = "Период вызова не может быть длиннее года"
        need_org(True)
        data["boss"] = str(p.get("boss") or "").strip()[:160]
        data.update({"reason": reason, "from": a.isoformat() if a else "", "to": b.isoformat() if b else "", "qty": 1, "paper": 1})
    elif sid in ("income", "income2"):
        cur = income_last_month(today, settings.get("income_days", 4))
        a, b = str(p.get("from") or ""), str(p.get("to") or "")
        if not MONTH_RE.match(a):
            E["from"] = "Выберите месяц"
        if not MONTH_RE.match(b):
            E["to"] = "Выберите месяц"
        elif b > cur:
            E["to"] = (f"Последний месяц периода — не позже месяца «{month_name(cur)}»: за текущий месяц заявку на справку можно подать "
                       f"только в последние {settings.get('income_days', 4)} дня месяца, когда доходы за него уже известны")
        if MONTH_RE.match(a) and MONTH_RE.match(b) and a > b:
            E["from"] = "Начало периода позже окончания"
        need_qty()
        data.update({"from": a, "to": b, "paper": 1})
    elif sid == "extract":
        num = str(p.get("ord") or "")
        o = next((x for x in prikazy if x["n"] == num), None)
        if not o:
            E["ord"] = "Выберите приказ из списка"
        elif not extract_allowed(o, foreign):
            E["ord"] = ("Выписку из этого приказа иностранным гражданам оформляет Управление международного сотрудничества "
                        "(27 учебный корпус)")
        else:
            data["ord"] = {k: o[k] for k in ("n", "date", "title", "type") if o.get(k)}
            title = f"Выписка из приказа № {o['n']}"
        if foreign:                              # 2 экземпляра: студенту и в Управление международного сотрудничества
            data["qty"], data["foreign"] = FOREIGN_EXTRACT_QTY, 1
        else:
            need_qty()
        data["paper"] = 1
    elif svc.get("app"):
        fields, vals = (tpl or {}).get("fields", []), p.get("fields") or {}
        holidays = set(settings.get("holidays") or [])
        clean = {}
        for f in fields:
            if f["type"] == "calc":
                continue
            if f.get("show") and f["show"].get("has", "") not in clean.get(f["show"].get("by"), ""):
                clean[f["k"]] = ""                      # поле не показано (например, имя не менялось) — не нужно
                continue
            v = str(vals.get(f["k"]) or "").strip()
            low = dates.earliest(f.get("min"), today, holidays) if f["type"] == "date" else None
            if low and _date(v) and _date(v) < low:
                E[f["k"]] = ("Не раньше даты подачи заявления" if f["min"].get("today") else
                             f"Любой день, кроме дня подачи заявления: не раньше {dates.fmt(low)}" if f["min"].get("after_today") else
                             workdays_msg(f["min"]["workdays"]))
            if f["type"] == "date" and v and not _date(v):
                E[f["k"]] = "Неверная дата"
            elif f["type"] == "select" and v and v not in src_opts(f, clean, (prog or {}).get("level"), settings):
                E[f["k"]] = "Выберите вариант из списка"
            elif f["type"] == "number" and v and _int(v) is None:
                E[f["k"]] = "Нужно число"
            elif f["type"] == "number" and v and nmin_of(f, clean) is not None and _int(v) < nmin_of(f, clean):
                E[f["k"]] = f"Не менее {nmin_of(f, clean)}" + (f" {f['unit']}" if f.get("unit") else "")
            if f.get("req") and not v:
                E[f["k"]] = "Заполните поле"
            if f.get("acc") and v:
                v = apps.accusative(v)          # «справка» → «справку»: перед словом «прилагаю»
            clean[f["k"]] = v[:1000]
            if f.get("other"):                  # «иные обстоятельства» — студент пишет какие, в бланк идёт в скобках
                ko = f["k"] + OTHER
                t = str(vals.get(ko) or "").strip()[:300]
                if v == f["other"] and not t:
                    E[ko] = "Укажите, какие именно " + ("причины" if "причин" in f["other"] else "обстоятельства")
                clean[ko] = t if v == f["other"] else ""
        for f in fields:                                # вычисляемые даты: окончание академа, отпуска и т. п.
            if f["type"] == "calc":
                clean[f["k"]] = dates.calc(f.get("calc") or {}, clean)
                src = (f.get("calc") or {}).get("days_field") or (f.get("calc") or {}).get("from")
                if not clean[f["k"]] and clean.get(src) and src not in E:
                    E[src] = "Проверьте значение — из него не получается рассчитать дату"
        data["fields"], data["app"] = clean, 1
        if foreign:
            data["foreign"] = 1
    else:
        E["svc"] = "Неизвестная услуга"
    from .catalog import FLOWS, LONG_APPS, LONG_SERVICES   # noqa: E402 — маршрут статусов заказа
    data["flow"] = ("ecp" if data.get("ecp") else
                    ("long" if svc["t"] in LONG_APPS else "app") if data.get("app") else
                    "long" if sid in LONG_SERVICES else "paper")
    assert data["flow"] in FLOWS
    return data, title, E
