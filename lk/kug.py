"""Календарный учебный график (КУГ) из Excel: периоды, на которые выдаётся справка-вызов, по группам.

Откуда ЛК берёт даты справки-вызова (по порядку):
  1. из 1С — programs[].calls (учебный план / график в 1С), если 1С их присылает;
  2. из загруженного в кабинете сотрудника файла КУГ — по группе студента;
  3. если нет ни того, ни другого — студент выбирает основание из списка МФЦ и вводит даты сам.

Формат файла (.xlsx или .csv; старый .xls нужно пересохранить в .xlsx). Первый лист, строка заголовков где-то
в первых 15 строках, колонки по смыслу (порядок любой, лишние колонки не мешают):
  Группа      — одна или несколько через запятую: «ЭК-301, ЭК-302»
  Основание   — как увидит студент: «Промежуточная аттестация (зимняя сессия)»
  Начало      — дата (ячейка-дата или ДД.ММ.ГГГГ)
  Окончание   — дата
Реальный формат КУГ университета подставляется здесь же: достаточно поправить HEADERS или функцию rows().
"""
import csv
import datetime as dt
import hashlib
import io
import re

HEADERS = {
    "group": ("групп",),
    "reason": ("основан", "вид периода", "период", "вид", "наименование"),
    "from": ("начал", "дата с", "с "),
    "to": ("оконч", "дата по", "по "),
}
MAX_ROWS = 20000


class KugError(ValueError):
    pass


def norm_group(g):
    """«аиз 101», «АИЗ-101», «АИЗ–101» — одна группа."""
    return re.sub(r"[\s\-–—_.]+", "", str(g or "")).upper().replace("Ё", "Е")


def _date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v or "").strip()
    for f in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s[:10], f).date()
        except ValueError:
            pass
    if re.fullmatch(r"\d{5}(\.0+)?", s):                       # число-дата Excel из CSV
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))
    return None


def _table(data, filename):
    name = (filename or "").lower()
    if name.endswith(".xls"):
        raise KugError("Старый формат .xls не поддерживается — откройте файл в Excel и сохраните как .xlsx")
    if name.endswith(".csv") or not data[:2] == b"PK":
        text = data.decode("utf-8-sig", errors="replace")
        if "�" in text:
            text = data.decode("cp1251", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t") if text.strip() else csv.excel
        return [row for row in csv.reader(io.StringIO(text), dialect)]
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover
        raise KugError("На сервере не установлен openpyxl: pip install openpyxl") from e
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa
        raise KugError(f"Не удалось открыть файл Excel: {e}") from e
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True, max_row=MAX_ROWS)]


def _columns(head):
    cols = {}
    cells = [str(c or "").strip().lower() + " " for c in head]
    for key, words in HEADERS.items():
        for i, c in enumerate(cells):
            if i not in cols.values() and any(c.startswith(w) or w in c for w in words):
                cols[key] = i
                break
    return cols


def parse(data, filename=""):
    """Файл → ({группа: [периоды]}, предупреждения по строкам, число строк)."""
    rows = _table(data, filename)
    hi = next((i for i, r in enumerate(rows[:15]) if len(_columns(r)) == 4), None)
    if hi is None:
        raise KugError("Не нашли строку заголовков: нужны колонки «Группа», «Основание», «Начало», «Окончание». "
                       "Скачайте шаблон и заполните по нему.")
    cols, groups, warn, n = _columns(rows[hi]), {}, [], 0
    for ln, r in enumerate(rows[hi + 1:], start=hi + 2):
        cell = lambda k: r[cols[k]] if cols[k] < len(r) else None  # noqa: E731
        if not any(c not in (None, "") for c in r):
            continue
        gs = [(norm_group(g), g.strip()) for g in re.split(r"[,;\n]", str(cell("group") or "")) if norm_group(g)]
        reason = re.sub(r"\s+", " ", str(cell("reason") or "")).strip()[:200]
        a, b = _date(cell("from")), _date(cell("to"))
        why = ("нет группы" if not gs else "нет основания" if not reason else "неверная дата начала" if not a
               else "неверная дата окончания" if not b else "окончание раньше начала" if b < a else "")
        if why:
            warn.append(f"строка {ln}: {why}")
            continue
        for g, label in gs:
            pid = "kug-" + hashlib.sha1(f"{g}|{reason}|{a}|{b}".encode()).hexdigest()[:10]
            lst = groups.setdefault(g, [])
            if not any(x["id"] == pid for x in lst):
                lst.append({"id": pid, "reason": reason, "from": a.isoformat(), "to": b.isoformat(), "group": label})
        n += 1
    if not n:
        raise KugError("В файле нет ни одной правильной строки" + (": " + "; ".join(warn[:5]) if warn else ""))
    for lst in groups.values():
        lst.sort(key=lambda x: x["from"])
    return groups, warn, n


def template_xlsx():
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "КУГ"
    ws.append(["Группа", "Основание", "Начало", "Окончание"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in (["ЭК-301, ЭК-302", "Промежуточная аттестация (зимняя сессия)", dt.date(2027, 1, 11), dt.date(2027, 1, 31)],
                ["ЭК-301, ЭК-302", "Промежуточная аттестация (летняя сессия)", dt.date(2027, 6, 7), dt.date(2027, 6, 27)],
                ["АГМ-201", "Государственная итоговая аттестация", dt.date(2027, 5, 24), dt.date(2027, 6, 30)]):
        ws.append(row)
    for r in ws.iter_rows(min_row=2, min_col=3, max_col=4):
        for c in r:
            c.number_format = "DD.MM.YYYY"
    for col, w in zip("ABCD", (22, 48, 14, 14)):
        ws.column_dimensions[col].width = w
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
