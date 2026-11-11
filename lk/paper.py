"""Печатные формы: справка об обучении (её студент видит перед подтверждением ЭЦП) и бланки заявлений по шаблонам."""
import datetime as dt
import html
import re

from . import names
from .apps import AUTO_ALIASES
from .catalog import CALL_ARTICLE, call_purpose

MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
HEAD = ("<div class=\"hdr\"><div class=\"hdr-min\">МИНИСТЕРСТВО СЕЛЬСКОГО ХОЗЯЙСТВА РОССИЙСКОЙ ФЕДЕРАЦИИ</div>"
        "Федеральное государственное бюджетное образовательное учреждение высшего образования<br>«Российский государственный "
        "аграрный университет – МСХА имени К.А. Тимирязева»<br>(ФГБОУ ВО РГАУ-МСХА имени К.А. Тимирязева)</div>"
        "<div class=\"hdr-addr\">127434, Москва г, ул Тимирязевская, д. 49&nbsp; Тел.: (499) 976-04-80&nbsp; "
        "Эл.почта: info@rgau-msha.ru&nbsp; Сайт: http://www.timacad.ru</div>")
ORD_GEN = ["первого", "второго", "третьего", "четвёртого", "пятого", "шестого", "седьмого"]
LEVEL_GEN = {"bak": "бакалавриата", "spec": "специалитета", "mag": "магистратуры", "asp": "аспирантуры",
             "spo": "по программе среднего профессионального образования"}
ACCREDITATION = ("ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ ОБРАЗОВАТЕЛЬНОЕ УЧРЕЖДЕНИЕ ВЫСШЕГО ОБРАЗОВАНИЯ «РОССИЙСКИЙ "
                 "ГОСУДАРСТВЕННЫЙ АГРАРНЫЙ УНИВЕРСИТЕТ – МСХА ИМЕНИ К.А. ТИМИРЯЗЕВА» имеет свидетельство о государственной "
                 "аккредитации, выданное Федеральной службой по надзору в сфере образования и науки регистрационный номер "
                 "№ А007-00115-77/01130110 от 05.03.2021. Срок действия государственной аккредитации: бессрочно")
PARAM_RE = re.compile(r"\[([^\]\s]+)\]")
e = html.escape


def fmt(d):
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if d and re.match(r"\d{4}-\d\d-\d\d", d) else (d or "")


def long_date(d):
    d = dt.date.fromisoformat(d[:10])
    return f"«{d.day}» {MONTHS_G[d.month - 1]} {d.year} г."


def short(fio):
    a, *r = str(fio).split()
    return a + " " + "".join(x[0] + "." for x in r)


def auto_values(person, prog, phone):
    v = {"ФИО": person["fio"], "Курс": prog.get("course"), "Группа": prog.get("group"), "Институт": prog.get("inst"),
         "Телефон": phone, "Специальность": f"{prog.get('code', '')} {prog.get('name', '')}".strip(),
         "Специализация": prog.get("profile"), "ФормаОбучения": prog.get("form"), "Основа": prog.get("basis")}
    for a, b in AUTO_ALIASES.items():
        v[a] = v[b]
    v["Факультет"] = names.inst_prep(prog.get("inst"))     # «Обучаюсь в Институте …»
    return v


GENDER_RE = re.compile(r"\{([^{}|]*)\|([^{}|]*)\}")
SIGN_RE = re.compile(r"\{_(.*?)_\}")


def female(person):
    if person.get("gender") in ("f", "m"):
        return person["gender"] == "f"
    parts = str(person.get("fio", "")).split()
    return len(parts) > 2 and parts[-1].endswith("на")


def fill(text, vals, she=False):
    """[Параметр] → значение (как в бланке 1С — без подчёркивания); пусто → линия для записи от руки.
    {он|она} → форма по полу."""
    text = GENDER_RE.sub(lambda m: m.group(2) if she else m.group(1), text)

    def rep(m):
        v = vals.get(m.group(1))
        if v in (None, ""):
            return '<span class="blank">_______________</span>'
        return e(fmt(str(v)))
    out = PARAM_RE.sub(rep, e(text))
    # {_подсказка_} — линия для подписи от руки, под ней мелким шрифтом подсказка
    return SIGN_RE.sub(lambda m: f'<span class="sl"><span class="sl-l"></span><small>{m.group(1)}</small></span>', out)


def application(tpl, person, prog, data, date):
    """Бланк заявления как макет 1С: шапка справа, «ЗАЯВЛЕНИЕ», текст, внизу дата и подписант — владелец ЛК (полное ФИО)."""
    she = female(person)
    v = auto_values(person, prog, data.get("phone"))
    v.update({k: x for k, x in (data.get("fields") or {}).items() if x})
    for f in tpl.get("fields") or []:          # «иными обстоятельствами» + что вписал студент: «… (переезд семьи)»
        t = v.get(f["k"] + "_иное")
        if f.get("other") and v.get(f["k"]) == f["other"] and t:
            v[f["k"]] = f"{f['other']} ({t})"
    # шапка «от кого» — в родительном падеже: «от студента 3 курса Института … Иванова Ивана Ивановича»
    vh = dict(v, ФИО=names.fio_gen(person), Институт=names.inst_gen(prog.get("inst")))
    line = lambda s, vals=v: fill(s, vals, she).replace("\n", "<br>")  # noqa: E731
    def para(s):
        out = fill(s, v, she)
        if s.lstrip().startswith("[") and out[:1].isalpha():   # абзац начинается со значения поля — с заглавной
            out = out[0].upper() + out[1:]
        out = re.sub(r"(\d{2}\.\d{2}\.\d{4}) г\.", "\\1&nbsp;г.", out)     # «г.» не переносится отдельно от даты
        return f'<p class="ind">{out}</p>'
    body = "".join(para(s) for s in tpl["body"].split("\n") if s.strip())
    return (f'<div class="app" style="font-family:\'Times New Roman\',Times,serif;font-size:14pt;line-height:1.5"><div class="to">{line(tpl["to"])}<br>{line(tpl["head"], vh)}</div><h4>ЗАЯВЛЕНИЕ</h4>{body}'
            f'<div class="sig"><span>{fmt(str(date)[:10])}</span><span>{e(person["fio"])}</span></div></div>')


def edu_year(d):
    d = dt.date.fromisoformat(str(d)[:10])
    y = d.year if d.month >= 9 else d.year - 1
    return f"{y}-{y + 1}"


def nominative(form):
    """«очной» → «очная», «очно-заочной» → «очно-заочная», «бюджетной основе» → «бюджетная»."""
    w = str(form or "").split(" ")[0]
    return re.sub(r"ой$", "ая", w)


def word_date(d):
    d = dt.date.fromisoformat(str(d)[:10])
    return f"{d.day} {MONTHS_G[d.month - 1]} {d.year} г."


def ecp_stamp(signer, signed, data):
    if not signed:
        return '<div class="ecp-stamp wait">Здесь будет отметка об электронной подписи — её ставит подписант после проверки</div>'
    c = data.get("cert") or signer.get("cert") or {}
    return ('<div class="ecp-stamp"><b>ДОКУМЕНТ ПОДПИСАН<br>УСИЛЕННОЙ КВАЛИФИЦИРОВАННОЙ<br>ЭЛЕКТРОННОЙ ПОДПИСЬЮ</b>'
            f'<span>Сертификат: {e(c.get("serial") or "—")}</span><span>Владелец: {e(signer.get("fio_full") or signer["fio"])}</span>'
            f'<span>Действует с {e(fmt(c.get("from") or ""))} по {e(fmt(c.get("to") or ""))}.</span></div>')


def study(person, prog, data, signer, number, date, signed=False):
    """Справка о подтверждении обучения — по образцу справки с ЭЦП из 1С (номер и дату присваивает 1С)."""
    kind, org = data.get("kind"), data.get("org")
    place = org["full"] if org else data.get("place") or "по месту требования"
    lvl = prog.get("level")
    course = int(prog.get("course") or 0)
    ordw = ORD_GEN[course - 1] if 0 < course <= len(ORD_GEN) else f"{course}-го"
    noun = "аспирантом" if lvl == "asp" else "студентом"
    dir_label = "Научная специальность" if lvl == "asp" else "Направление подготовки (специальность)"
    enroll = prog.get("enroll") or {}
    rows = [e(prog.get("inst", "")), f'{dir_label}: {e(prog.get("code", ""))} {e(prog.get("name", ""))}',
            f'Форма обучения по основной образовательной программе: {e(nominative(prog.get("form")))}',
            f'Основа обучения: {e(nominative(prog.get("basis")))}']
    if enroll.get("n"):
        rows.append(f'Приказ о зачислении от {word_date(enroll["date"])} № {e(enroll["n"])}')
    if prog.get("start"):
        rows.append(f'Дата начала обучения: {fmt(prog["start"])} г.')
    if prog.get("end"):
        rows.append(f'Предполагаемая дата окончания обучения: {fmt(prog["end"])} г.')
    head = ('<div class="note-paper">[ гербовый бланк ]</div>' if kind == "gerb" else "") + HEAD
    out = (f'{head}<div class="ref-top"><div><div class="ref-title"><b>СПРАВКА</b><br><b>о подтверждении обучения</b></div>'
           f'<div class="ref-n"><span>№ {e(str(number))}</span><span>от {fmt(str(date)[:10])}</span></div>'
           f'<div class="ref-n sm"><span>На №</span><span>от</span></div></div>'
           f'<div><div class="ref-to">Для представления</div><div class="ref-n"><span>{e(place[:1].upper() + place[1:])}</span></div>'
           f'<div class="ref-n sm"><span>&nbsp;</span></div></div></div>'
           f'<p>{e(person["fio"])}'
           + (f' (дата рождения {fmt(person["birth"])} г.)' if person.get("birth") else "")
           + f' является {noun} {ordw} курса {LEVEL_GEN.get(lvl, "")} в {edu_year(date)} учебном году.</p>'
           + "".join(f"<p>{r}</p>" for r in rows) + '<div class="ref-lines"></div>')
    mid = ecp_stamp(signer, signed, data) if kind == "ecp" else '<div class="ref-mp">М.П.<span class="sl-l"></span></div>'
    out += (f'<div class="ref-sig"><div>{e(signer["pos"])}</div>{mid}<div class="ref-fio">{e(signer["fio"])}</div></div>')
    return out


def call(person, prog, data, signer, number, date):
    """Справка-вызов по форме приказа Минобрнауки № 1368 (без герба, печати и подписи — их ставят на бумаге)."""
    d = dt.date.fromisoformat(str(date)[:10])
    lvl = prog.get("level")
    a, b = data.get("from") or "", data.get("to") or ""
    days = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1 if a and b else ""
    org = data.get("org") or {}
    edu = "среднего профессионального" if lvl == "spo" else "высшего"
    by = "специальности" if lvl in ("spo", "spec", "asp") else "направлению подготовки"
    by_line = " / ".join(f"<u>{w}</u>" if w == by else w for w in ("профессии", "специальности", "направлению подготовки"))

    def u(v, cap="", cls=""):
        return (f'<span class="fl{(" " + cls) if cls else ""}"><span class="fv">{v or "&nbsp;"}</span>'
                + (f'<small>{cap}</small>' if cap else "") + '</span>')
    return (
        '<div class="call">'
        '<div class="call-appr">УТВЕРЖДЕНА<br>приказом Министерства образования<br>и науки Российской Федерации<br>от 19 декабря 2013 г. № 1368<br>'
        '(в ред. Приказов Минобрнауки России<br>от 02.03.2015 № 134, от 26.05.2015 № 525)</div>'
        '<h4>СПРАВКА-ВЫЗОВ</h4>'
        f'<div class="call-c">От "<u>&nbsp;{d.day:02d}&nbsp;</u>" <u>&nbsp;&nbsp;{d.month:02d}&nbsp;&nbsp;</u> 20<u>{d.strftime("%y")}</u> г. № <u>&nbsp;{e(str(number))}&nbsp;</u></div>'
        '<div class="call-c">дающая право на предоставление гарантий и компенсаций<br>работникам, совмещающим работу с получением образования</div>'
        f'<div class="call-r"><span>Работодателю</span>{u(e(org.get("name") or org.get("full") or ""), "(полное наименование организации-работодателя/фамилия, имя, отчество работодателя – физического лица)")}</div>'
        f'<div class="call-r"><span>В соответствии со статьей</span>{u(CALL_ARTICLE.get(lvl, "173"), "173/173.1/174/176 (указать нужное)", "short")}<span>Трудового кодекса Российской Федерации</span></div>'
        f'<div class="call-r">{u(e(person.get("fio_dat") or person["fio"]) + ",", "фамилия, имя, отчество (в дательном падеже)")}</div>'
        '<p class="call-p">допущенному к вступительным испытаниям/слушателю подготовительного отделения, образовательной организации '
        'высшего образования/<u>обучающемуся</u> (подчеркнуть нужное)</p>'
        f'<div class="call-r"><span>по</span>{u(e(str(prog.get("form") or "")), "очной/очно-заочной/заочной (указать нужное)")}<span>форме обучения на {e(str(prog.get("course") or ""))} курсе,</span></div>'
        '<div class="call-c">предоставляются гарантии и компенсации для</div>'
        f'<div class="call-r">{u(e(call_purpose(data.get("reason"))), "прохождения вступительных испытаний/промежуточной аттестации/государственной итоговой аттестации/итоговой аттестации/подготовки и защиты выпускной квалификационной работы и/или сдачи итоговых государственных экзаменов/завершения диссертации на соискание ученой степени кандидата наук (указать нужное)*")}</div>'
        f'<div class="call-r"><span>с</span>{u(fmt(a), "число, месяц, год", "mid")}<span>по</span>{u(fmt(b), "число, месяц, год", "mid")}<span>продолжительностью</span></div>'
        f'<div class="call-r">{u(str(days), "(количество)", "short")}<span>календарных дней.</span></div>'
        f'<p class="call-acc">{ACCREDITATION}**</p>'
        f'<div class="call-r"><span>по образовательной программе</span>{u(edu, "среднего/профессионального/высшего")}</div>'
        f'<div class="call-r"><span>образования по {by_line}</span></div>'
        f'<div class="call-r">{u(e(prog.get("code", "")) + " - " + e(prog.get("name", "")), "код и наименование профессии/специальности/направления подготовки (указать нужное)")}</div>'
        f'<div class="call-sig"><div>{e(signer["pos"])}<br>М.П.</div><div>{u("", "подпись")}</div><div>{u(e(short_fio(signer)), "фамилия, имя, отчество")}</div></div>'
        '<div class="call-fn">* Основание предоставления гарантий и компенсаций для работников, осваивающих программы подготовки кадров '
        'высшей квалификации, не заполняется.<br>** Не заполняется для работников, допущенных к вступительным испытаниям; работников, '
        'являющихся слушателями подготовительных отделений образовательных организаций высшего образования; работников, осваивающих '
        'программы подготовки кадров высшей квалификации; работников, совмещающих работу с обучением по не имеющим государственной '
        'аккредитации программам бакалавриата, программам специалитета, программам магистратуры, образовательным программам среднего '
        'профессионального образования, а также для работников, совмещающих работу с освоением не имеющих государственной аккредитации '
        'образовательных программ основного общего или среднего общего образования.</div></div>')


def short_fio(signer):
    """«Захарова Светлана Алексеевна» → «Захарова С.А.» (как в образце справки-вызова)."""
    full = str(signer.get("fio_full") or "").split()
    if len(full) >= 2:
        return full[0] + " " + "".join(x[0] + "." for x in full[1:])
    return signer.get("fio", "")
