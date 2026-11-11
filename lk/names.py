"""Родительный падеж ФИО и названия института — для шапки заявления: «от студента … Иванова Ивана Ивановича».

Лучше, если 1С присылает готовое ФИО в родительном падеже (persons/{id} → fio_rod), как уже присылает fio_dat.
Если не прислала — склоняем сами по правилам русских ФИО (пол — из 1С, иначе по отчеству). Несклоняемые
(Шевченко, Гюго, Нгуен Тхи Лан) остаются как есть.
"""
HUSH = set("гкхжшчщ")
VOWELS = set("аеёиоуыэюя")


def _cap(src, out):
    return out[:1].upper() + out[1:] if src[:1].isupper() else out


FIRST_EXC = {"пётр": "Петра", "петр": "Петра", "лев": "Льва", "павел": "Павла"}


def _first(w, female):
    lw = w.lower()
    if not female and lw in FIRST_EXC:
        return FIRST_EXC[lw]
    if female:
        if lw.endswith("ия"):
            return w[:-1] + "и"
        if lw.endswith("а"):
            return w[:-1] + ("и" if lw[-2:-1] in HUSH else "ы")
        if lw.endswith("я"):
            return w[:-1] + "и"
        if lw.endswith("ь"):
            return w[:-1] + "и"
        return w
    if lw.endswith(("й", "ь")):
        return w[:-1] + "я"
    if lw.endswith("а"):
        return w[:-1] + ("и" if lw[-2:-1] in HUSH else "ы")
    if lw.endswith("я"):
        return w[:-1] + "и"
    if lw[-1:] in VOWELS:
        return w
    return w + "а"


def _middle(w, female):
    lw = w.lower()
    if lw.endswith("ич"):
        return w + "а"
    if lw.endswith("на"):
        return w[:-1] + "ы"
    return w


def _last(w, female):
    lw = w.lower()
    if "-" in w:
        return "-".join(_last(x, female) for x in w.split("-"))
    if lw.endswith(("ых", "их", "ко", "енко", "аго", "яго")) or lw[-1:] in "оеиуэю":
        return w
    if female:
        if lw.endswith(("ова", "ева", "ёва", "ина", "ына")):
            return w[:-1] + "ой"
        if lw.endswith(("ская", "цкая", "ая")):
            return w[:-2] + "ой"
        if lw.endswith("яя"):
            return w[:-2] + "ей"
        if lw.endswith("а"):
            return w[:-1] + ("и" if lw[-2:-1] in HUSH else "ы")
        if lw.endswith("я"):
            return w[:-1] + "и"
        return w                                  # женские на согласный не склоняются
    if lw.endswith(("ский", "цкий", "ий", "ый", "ой")):
        return w[:-2] + "ого"
    if lw.endswith("а"):
        return w[:-1] + ("и" if lw[-2:-1] in HUSH else "ы")
    if lw.endswith("я"):
        return w[:-1] + "и"
    if lw.endswith("ь"):
        return w[:-1] + "я"
    if lw.endswith("й"):
        return w[:-1] + "я"
    return w + "а"


def is_female(person):
    if person.get("gender") in ("f", "m"):
        return person["gender"] == "f"
    parts = str(person.get("fio", "")).split()
    return len(parts) > 2 and parts[-1].lower().endswith("на")


def fio_gen(person):
    """«Иванов Иван Иванович» → «Иванова Ивана Ивановича». Без отчества и не по-русски — осторожно."""
    if person.get("fio_rod"):
        return person["fio_rod"]
    parts = str(person.get("fio", "")).split()
    if not parts:
        return ""
    she = is_female(person)
    russian = len(parts) == 3 and parts[2].lower().endswith(("ич", "на"))
    if not russian:                               # иностранные ФИО: склоняем только мужские на согласный — и то нет; не трогаем
        return " ".join(parts)
    return " ".join([_last(parts[0], she), _first(parts[1], she), _middle(parts[2], she)])


INST_GEN = {"институт": "Института", "колледж": "Колледжа", "аспирантура": "Аспирантуры", "факультет": "Факультета",
            "академия": "Академии", "кафедра": "Кафедры"}


INST_PREP = {"институт": "Институте", "колледж": "Колледже", "аспирантура": "Аспирантуре", "факультет": "Факультете"}


def inst_prep(name):
    """«Институт агробиотехнологии» → «Институте агробиотехнологии» (для «Обучаюсь в …»)."""
    s = str(name or "")
    w, _, rest = s.partition(" ")
    g = INST_PREP.get(w.lower())
    return (g + (" " + rest if rest else "")) if g else s


def inst_gen(name):
    """«Институт агробиотехнологии» → «Института агробиотехнологии» (в шапке: «от студента 3 курса Института …»)."""
    s = str(name or "")
    w, _, rest = s.partition(" ")
    g = INST_GEN.get(w.lower())
    return (g + (" " + rest if rest else "")) if g else s
