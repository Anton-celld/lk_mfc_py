"""Проверки по ТЗ на имитаторе 1С: python -m pytest -q"""
import datetime as dt
import json

import pytest

from lk import catalog, config, dates, mock1c, rules
from lk.auth import make_hash, verify_hash
from lk.web import make_app

PDF = b"%PDF-1.4 test"
H = {"X-LK": "1"}


@pytest.fixture
async def env(aiohttp_server, aiohttp_client, tmp_path):
    m = await aiohttp_server(mock1c.make_app())
    cfg = config.load(tmp_path / "none.json")
    cfg["data_dir"] = str(tmp_path)
    cfg["demo"] = True
    cfg["onec"]["base_url"] = str(m.make_url("/"))
    cfg["onec"]["sync_every_sec"] = 3600
    cfg["auth"]["providers"] = "onec_check,onec_token,onec_import"
    app = make_app(cfg)
    c = await aiohttp_client(app)
    return {"c": c, "m": m, "app": app, "cfg": cfg}


async def login(c, who="st100001"):
    r = await c.post("/api/login", json={"login": who, "password": mock1c.DEMO_PASSWORD}, headers=H)
    assert r.status == 200, await r.text()
    return (await r.json())["role"]


async def order(c, p, files=None):
    from aiohttp import FormData
    f = FormData(default_to_multipart=True)
    f.add_field("json", json.dumps(p, ensure_ascii=False))
    for name, data in files or []:
        f.add_field("files", data, filename=name, content_type="application/pdf")
    r = await c.post("/api/orders", data=f, headers=H)
    return r.status, await r.json()


PR = "Прохождение промежуточной аттестации"
BASE = {"program": "p1", "email": "a@b.ru", "phone": "+7 900 000-00-01"}


# ---------------- правила ----------------
def test_inn():
    assert rules.inn_ok("7700000016") and rules.inn_ok("770000006648")
    assert not rules.inn_ok("7700000011") and not rules.inn_ok("12345")


def test_qty():
    assert rules.qty_error(3, 3) == ""
    assert "Превышен лимит" in rules.qty_error(4, 3)
    assert rules.qty_error(0, 3)


def test_hash_formats():
    h = make_hash("секрет", 1000)
    assert verify_hash(h, "секрет") and not verify_hash(h, "нет")
    import hashlib
    assert verify_hash("sha256$s1$" + hashlib.sha256(b"s1pw").hexdigest(), "pw")


# ---------------- вход ----------------
async def test_login_and_roles(env):
    c = env["c"]
    r = await c.post("/api/login", json={"login": "st100001", "password": "wrong"}, headers=H)
    assert r.status == 401
    assert await login(c) == "student"
    me = await (await c.get("/api/me")).json()
    assert me["person"]["fio"] == "Иванов Иван Иванович" and len(me["programs"]) == 2
    assert (await c.get("/api/staff/orders")).status == 403
    await c.post("/api/logout", headers=H)
    assert await login(c, "mfc01") == "staff"


async def test_csrf(env):
    r = await env["c"].post("/api/login", json={"login": "st100001", "password": mock1c.DEMO_PASSWORD})
    assert r.status == 403


async def test_token_link(env):
    r = await env["c"].get("/enter?t=demo-st100002", allow_redirects=False)
    assert r.status == 302
    me = await (await env["c"].get("/api/me")).json()
    assert me["person"]["fio"].startswith("Петрова")


async def test_import_accounts(env):
    auth = env["app"]["auth"]
    assert await auth.import_accounts() == 7
    env["app"]["auth"].providers = ["onec_import"]
    u, err = await auth.login("st100003", mock1c.DEMO_PASSWORD)
    assert u and u["person"] == "100003"


async def test_bruteforce_lock(env):
    c = env["c"]
    for _ in range(8):
        await c.post("/api/login", json={"login": "st100001", "password": "x"}, headers=H)
    r = await c.post("/api/login", json={"login": "st100001", "password": mock1c.DEMO_PASSWORD}, headers=H)
    assert "Слишком много" in (await r.json())["error"]


# ---------------- ТЗ: справка об обучении ----------------
async def test_study_kinds_and_limit(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "study", "kind": "gerb", "qty": 1})
    assert s == 422 and "org" in j["errors"]                     # гербовая без организации
    s, j = await order(c, {**BASE, "svc": "study", "kind": "gerb", "qty": 1, "inn": "7700000062"})
    assert s == 422 and "ликвидирована" in j["errors"]["org"]   # ликвидированная
    s, j = await order(c, {**BASE, "svc": "study", "kind": "gerb", "qty": 5, "inn": "7700000016"})
    assert s == 422 and "Превышен лимит" in j["errors"]["qty"]   # лимит 3
    s, j = await order(c, {**BASE, "svc": "study", "kind": "gerb", "qty": 3, "inn": "7700000016"})
    assert s == 200, j
    o = j["order"]
    assert o["data"]["org"]["full"].startswith("Общество") and o["status"] == "reg" and o["onec_id"]
    # по месту требования и с ЭЦП — без организации: даже если ИНН прислали, он не учитывается
    s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 2, "inn": "7700000016"})
    assert s == 200 and j["order"]["data"]["place"] == "по месту требования" and "org" not in j["order"]["data"]
    assert j["order"]["title"] == "Справка о подтверждении обучения по месту требования (бумажный экземпляр)"
    s, j = await order(c, {**BASE, "svc": "study", "kind": "ecp", "inn": "7700000016"})
    assert s == 200 and "org" not in j["order"]["data"] and j["order"]["title"] == "Справка о подтверждении обучения с ЭЦП"


async def test_ecp_preview_and_sign(env):
    c = env["c"]
    await login(c)
    r = await c.post("/api/preview", json={**BASE, "svc": "study", "kind": "ecp"}, headers=H)
    html = (await r.json())["html"].replace("&nbsp;", " ")
    assert "С.А. Захарова" in html and "Иванов Иван Иванович (дата рождения 14.03.2004 г.)" in html
    assert "является студентом третьего курса бакалавриата в 2026-2027 учебном году" in html or "2025-2026" in html
    assert "Приказ о зачислении от 3 августа 2024 г. № СК-1234" in html and "Основа обучения: бюджетная" in html
    assert "Форма обучения по основной образовательной программе: очная" in html and "Здесь будет отметка" in html
    s, j = await order(c, {**BASE, "svc": "study", "kind": "ecp"})
    assert j["order"]["status"] == "sign" and j["order"]["data"]["signer"]["fio"] == "С.А. Захарова"
    req = list(env["m"].app["reqs"].values())[-1]
    assert req["body"]["kind"] == "ecp" and "С.А. Захарова" in req["body"]["print_html"]


async def test_signer_per_level(env):
    c = env["c"]
    await login(c, "mfcadmin")
    # по умолчанию (решение МФЦ): СПО гербовая — Захарова, по МТ — Меликов; аспирантура по МТ — Пахомова, гербовая — Макаров
    await c.post("/api/logout", headers=H)
    for login_, prog, kind, who, extra in (("st100004", "p5", "umu", "А.В. Меликов", {"qty": 1}),
                                           ("st100004", "p5", "gerb", "С.А. Захарова", {"qty": 1, "inn": "7700000016"}),
                                           ("st100004", "p5", "ecp", "А.В. Меликов", {}),
                                           ("st100003", "p4", "umu", "Е.В. Пахомова", {"qty": 1}),
                                           ("st100003", "p4", "gerb", "С.С. Макаров", {"qty": 1, "inn": "7700000016"}),
                                           ("st100001", "p2", "gerb", "С.А. Захарова", {"qty": 1, "inn": "7700000016"})):
        await login(c, login_)
        s_, j = await order(c, {"program": prog, "email": "a@b.ru", "svc": "study", "kind": kind, **extra})
        assert s_ == 200 and j["order"]["data"]["signer"]["fio"] == who, (login_, kind, j)
        html = (await (await c.get(f"/api/orders/{j['order']['id']}/blank")).json())["html"].replace("&nbsp;", " ")
        assert who in html
        await c.post("/api/logout", headers=H)
    # администратор меняет подписанта магистратуры (группа бак+спец+маг) для справки с ЭЦП
    await login(c, "mfcadmin")
    r = await c.post("/api/staff/settings", json={"key": "signers", "value": {
        "vo": {"ecp": {"fio": "И.И. Магистров", "fio_full": "Магистров Иван Иванович", "pos": "Директор"}}, "bak": {"x": {}}}}, headers=H)
    assert r.status == 200
    await c.post("/api/logout", headers=H)
    await login(c, "st100002")
    r = await c.post("/api/preview", json={"program": "p3", "email": "a@b.ru", "svc": "study", "kind": "ecp"}, headers=H)
    html = (await r.json())["html"].replace("&nbsp;", " ")
    assert "И.И. Магистров" in html and "второго курса магистратуры" in html
    s_, j = await order(c, {"program": "p3", "email": "a@b.ru", "svc": "study", "kind": "umu", "qty": 1})
    assert j["order"]["data"]["signer"]["fio"] == "С.А. Захарова"       # другие виды — по умолчанию
    await c.post("/api/logout", headers=H)
    await login(c, "st100003")
    me = await (await c.get("/api/me")).json()
    assert me["programs"][0]["kinds"] == ["gerb", "umu"] and me["programs"][0]["signer"] is None   # аспирантам ЭЦП нет
    s_, j = await order(c, {"program": "p4", "email": "a@b.ru", "svc": "study", "kind": "ecp"})
    assert s_ == 422 and "ЭЦП не выдаётся" in j["errors"]["kind"]
    s_, j = await order(c, {"program": "p4", "email": "a@b.ru", "svc": "study", "kind": "umu", "qty": 1})
    html = (await (await c.get(f"/api/orders/{j['order']['id']}/blank")).json())["html"].replace("&nbsp;", " ")
    assert "Е.В. Пахомова" in html and "аспирантом второго курса аспирантуры" in html and "Научная специальность" in html


# ---------------- ТЗ: доходы, выписки ----------------
async def test_income_month(env):
    c = env["c"]
    await login(c)
    from lk.rules import income_last_month
    # последний месяц — прошлый; текущий — только за 4 дня до 1-го числа следующего
    assert income_last_month(dt.date(2026, 10, 26)) == "2026-09"
    assert income_last_month(dt.date(2026, 10, 27)) == "2026-09"
    assert income_last_month(dt.date(2026, 10, 28)) == "2026-10"
    assert income_last_month(dt.date(2026, 2, 24), 4) == "2026-01" and income_last_month(dt.date(2026, 2, 25), 4) == "2026-02"
    assert income_last_month(dt.date(2026, 2, 26), 2) == "2026-01" and income_last_month(dt.date(2026, 2, 27), 2) == "2026-02"
    assert income_last_month(dt.date(2027, 1, 10)) == "2026-12" and income_last_month(dt.date(2026, 10, 30), 0) == "2026-09"
    last = income_last_month(dt.date.today())
    y, m = int(last[:4]), int(last[5:])
    nxt = f"{y + (m == 12)}-{m % 12 + 1:02d}"
    s, j = await order(c, {**BASE, "svc": "income", "from": "2026-01", "to": nxt, "qty": 1})
    assert s == 422 and "не позже" in j["errors"]["to"]
    s, j = await order(c, {**BASE, "svc": "income", "from": "2026-01", "to": last, "qty": 1})
    assert s == 200
    me = await (await c.get("/api/me")).json()
    assert me["income_last"] == last


async def test_extract_from_prikazy(env):
    c = env["c"]
    await login(c)
    items = (await (await c.get("/api/prikazy?program=p1")).json())["items"]
    assert len(items) == 4
    assert len((await (await c.get("/api/prikazy?program=p2")).json())["items"]) == 2
    assert all(x["allowed"] for x in items)                        # гражданам РФ — любые приказы
    s, j = await order(c, {**BASE, "svc": "extract", "ord": "000", "qty": 1})
    assert s == 422
    s, j = await order(c, {**BASE, "svc": "extract", "ord": "3105-ст", "qty": 1})
    assert s == 200 and j["order"]["title"] == "Выписка из приказа № 3105-ст"


# ---------------- ТЗ: заявления ----------------
async def test_application_flow(env):
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на академический отпуск")
    p = {**BASE, "svc": sid, "fields": {"Причина": "по медицинским показаниям", "ДатаНачала": "2027-03-01", "Комментарий": "Справку ВК № 1"}}
    r = await c.post("/api/preview", json=p, headers=H)
    html = (await r.json())["html"].replace("&nbsp;", " ")
    assert "Прошу предоставить мне академический отпуск по медицинским показаниям с 01.03.2027 г. по 29.02.2028 г." in html
    assert "ЭК-301" in html and "[" not in html and 'class="fill' not in html     # без подчёркиваний, как в макете
    assert "Иванов Иван Иванович</span></div>" in html                          # внизу — полное ФИО владельца ЛК
    s, j = await order(c, p)
    assert s == 422 and "files" in j["errors"]
    s, j = await order(c, p, [("скан.jpg", b"\xff\xd8 jpeg")])
    assert s == 422 and "PDF" in j["errors"]["files"]
    s, j = await order(c, {**p, "fields": {**p["fields"], "Причина": "чужое"}}, [("скан.pdf", PDF)])
    assert s == 422 and "Причина" in j["errors"]
    s, j = await order(c, p, [("заявление.pdf", PDF), ("справка.pdf", PDF)])
    assert s == 200 and len(j["order"]["data"]["files"]) == 2
    req = list(env["m"].app["reqs"].values())[-1]["body"]
    assert len(req["files"]) == 2 and req["fields"]["Причина"] == "по медицинским показаниям" and "ЗАЯВЛЕНИЕ" in req["print_html"]


def fill_vals(fields, level):
    """Первое значение каждого поля; выпадашки направлений — по уже выбранному институту/направлению."""
    v = {}
    for x in fields:
        if x["type"] == "calc":
            continue
        if x["type"] == "select":
            o = catalog.src_opts(x, v, level)
            v[x["k"]] = o[0] if o else ""
        else:
            v[x["k"]] = "2027-06-01" if x["type"] == "date" else "16" if x["type"] == "number" else "Тест"
    return v


async def test_all_19_templates_render(env):
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    apps_ = [x for x in me["services"] if x.get("app") and x["id"] in me["programs"][0]["services"]]
    assert len(apps_) == 19                                         # бакалавриат: по перечню 2026–2027 (+ варианты отпусков)
    for a in apps_:
        f = me["forms"][a["t"]]["fields"]
        vals = fill_vals(f, me["programs"][0]["level"])
        r = await c.post("/api/preview", json={**BASE, "svc": a["id"], "fields": vals}, headers=H)
        html = (await r.json())["html"].replace("&nbsp;", " ")
        assert 'class="blank"' not in html and "[" not in html and "{" not in html, a["t"]


# ---------------- доступ по уровням ----------------
async def test_access_matrix(env):
    c = env["c"]
    await login(c, "mfc01")
    r = await c.post("/api/staff/settings", json={"key": "limit", "value": 5}, headers=H)
    assert r.status == 403                                        # оператор не меняет настройки
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    s = (await (await c.get("/api/staff/settings")).json())["settings"]
    acc = s["access"]
    acc["call"] = ["mag"]
    assert (await c.post("/api/staff/settings", json={"key": "access", "value": acc}, headers=H)).status == 200
    await c.post("/api/logout", headers=H)
    await login(c)
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016"})
    assert s == 403


# ---------------- статусы из 1С, отзыв, архив ----------------
async def test_status_review_archive(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 1})
    oid = j["order"]["id"]
    r = await c.post(f"/api/orders/{oid}/review", json={"rating": 5}, headers=H)
    assert r.status == 403                                        # ещё не выдано
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    for _ in range(3):                                            # reg → sign → ready → done
        await c.post("/api/staff/demo", json={"id": oid}, headers=H)
    await c.post("/api/logout", headers=H)
    await login(c)
    o = (await (await c.get(f"/api/orders/{oid}")).json())["order"]
    assert o["status"] == "done" and not o["archived"] and o["to_archive"] == 7
    n = (await (await c.get("/api/notices")).json())["items"]
    assert any("Выполнено" in x["title"] for x in n) and any("Передано на подпись" in x["title"] for x in n)
    r = await c.post(f"/api/orders/{oid}/review", json={"rating": 0}, headers=H)
    assert r.status == 422
    r = await c.post(f"/api/orders/{oid}/review", json={"rating": 4, "text": "Долго ждал в очереди"}, headers=H)
    assert (await r.json())["order"]["review"] == "Долго ждал в очереди"
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    rv = await (await c.get("/api/staff/reviews")).json()
    assert rv["n"] == 1 and rv["avg"] == 4
    await c.post("/api/staff/settings", json={"key": "keep_days", "value": 0}, headers=H)
    await c.post("/api/logout", headers=H)
    await login(c)
    assert (await (await c.get(f"/api/orders/{oid}")).json())["order"]["archived"]


async def test_need_fix_and_cancel(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016"})
    oid = j["order"]["id"]
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    await c.post("/api/staff/demo", json={"id": oid, "to": "need", "comment": "Укажите ФИО руководителя"}, headers=H)
    await c.post("/api/logout", headers=H)
    await login(c)
    o = (await (await c.get(f"/api/orders/{oid}")).json())["order"]
    assert o["status"] == "need" and o["comment"] == "Укажите ФИО руководителя"
    from aiohttp import FormData
    f = FormData(default_to_multipart=True)
    f.add_field("json", json.dumps({"email": "a@b.ru", "reason": PR, "call": "p1-w", "inn": "7700000016"}))
    r = await c.post(f"/api/orders/{oid}", data=f, headers=H)
    assert (await r.json())["order"]["status"] == "reg"          # вернулся туда, где был до «Требуется действие»
    s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 1})
    r = await c.post(f"/api/orders/{j['order']['id']}/cancel", headers=H)
    assert (await r.json())["order"]["status"] == "cancel"
    assert list(env["m"].app["reqs"].values())[-1]["status"] == "cancel"


async def test_1c_down_queue(env, tmp_path):
    c = env["c"]
    await login(c)                                                # при входе данные студента из 1С сохраняются
    od = env["app"]["orders"]
    good = env["cfg"]["onec"]["base_url"]
    od.onec.base = "http://127.0.0.1:9/"                         # 1С «упала»
    s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 1})
    assert s == 200 and j["order"]["sync"] == "queued" and not j["order"]["onec_id"]
    od.onec.base = good
    sent, _ = await od.sync()
    assert sent == 1
    assert (await (await c.get(f"/api/orders/{j['order']['id']}")).json())["order"]["onec_id"]


async def test_ecp_document(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "study", "kind": "ecp"})
    oid = j["order"]["id"]
    assert (await c.get(f"/api/orders/{oid}/document")).status == 403
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    await c.post("/api/staff/demo", json={"id": oid}, headers=H)
    await c.post("/api/logout", headers=H)
    await login(c)
    r = await c.get(f"/api/orders/{oid}/document")
    assert r.status == 200 and (await r.read()).startswith(b"%PDF")
    r = await c.get(f"/api/orders/{oid}/signature")
    assert r.status == 200 and b"DEMO DETACHED SIGNATURE" in await r.read()
    r = await c.post(f"/api/orders/{oid}/review", json={"rating": 5}, headers=H)
    assert r.status == 200                                        # отзыв и по подписанной ЭЦП


async def test_other_student_cannot_see(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 1})
    await c.post("/api/logout", headers=H)
    await login(c, "st100002")
    assert (await c.get(f"/api/orders/{j['order']['id']}")).status == 403


# ---------------- правила дат в заявлениях ----------------
async def sid_of(c, title):
    me = await (await c.get("/api/me")).json()
    return next(x["id"] for x in me["services"] if x["t"] == title)


async def prev(c, sid, fields, program="p1"):
    r = await c.post("/api/preview", json={**BASE, "program": program, "svc": sid, "fields": fields}, headers=H)
    return (await r.json())["html"].replace("&nbsp;", " ")


def test_dates_rules():
    from lk import dates
    hol = set(dates.HOLIDAYS)
    wed = dt.date(2026, 10, 14)                                   # среда
    assert dates.earliest({"workdays": 5}, wed, hol) == dt.date(2026, 10, 21)   # подал в ср 14.10: чт, пт, пн, вт, ср → можно с ср 21.10
    assert dates.earliest({"workdays": 5}, dt.date(2026, 10, 30), hol) == dt.date(2026, 11, 9)  # 4 ноября не считается
    assert dates.earliest({"after_today": 1}, wed, hol) == dt.date(2026, 10, 15)
    assert dates.calc({"from": "a", "years": 1, "days": -1}, {"a": "2026-11-01"}) == "2027-10-31"
    m = {"полутора": {"months": 18}, "трёх": {"years": 3}}
    assert dates.calc({"from": "b", "by": "x", "map": m, "days": -1}, {"b": "2026-05-10", "x": "полутора"}) == "2027-11-09"
    assert dates.calc({"from": "b", "by": "x", "map": m, "days": -1}, {"b": "2026-05-10", "x": "трёх"}) == "2029-05-09"
    assert dates.calc({"from": "e", "days_field": "n"}, {"e": "2026-12-01", "n": "16"}) == "2026-12-17"


async def test_academ_min_5_workdays(env):
    c = env["c"]
    await login(c)
    sid = await sid_of(c, "Заявление на академический отпуск")
    s, j = await order(c, {**BASE, "svc": sid, "fields": {"Причина": "семейным обстоятельствам",
                                                          "ДатаНачала": (dt.date.today() + dt.timedelta(days=2)).isoformat()}},
                       [("з.pdf", PDF)])
    assert s == 422 and "рабочих дней" in j["errors"]["ДатаНачала"]


async def test_dismissal_not_today(env):
    c = env["c"]
    await login(c)
    for title in ("Заявление на отчисление по собственному желанию", "Заявление на отчисление в связи с переводом в другой вуз"):
        sid = await sid_of(c, title)
        f = {"ДатаНачала": dt.date.today().isoformat(), "Организация": "ФГБОУ ВО X", "ДатаСправки": "2026-10-01", "НомерДок": "1"}
        s, j = await order(c, {**BASE, "svc": sid, "fields": f}, [("з.pdf", PDF)])
        assert s == 422 and "кроме дня подачи" in j["errors"]["ДатаНачала"], title
        f["ДатаНачала"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        s, j = await order(c, {**BASE, "svc": sid, "fields": f}, [("з.pdf", PDF)])
        assert s == 200, title


async def test_child_leave_dates(env):
    c = env["c"]
    await login(c, "st100002")
    sid = await sid_of(c, "Заявление на предоставление отпуска по уходу за ребёнком")
    html = await prev(c, sid, {"ИнформацияОЗапросе": "до полутора лет", "ДатаРождения": "2026-05-10", "ДатаНачала": "2026-07-20",
                               "Причина": "Копию свидетельства о рождении"}, "p3")
    assert "до полутора лет с 20.07.2026 г. по 09.11.2027 г." in html and "10.05.2026" not in html   # дата рождения в бланк не печатается
    sid = await sid_of(c, "Заявление на продление отпуска по уходу за ребёнком")
    html = await prev(c, sid, {"ДатаРождения": "2026-05-10"}, "p3")
    assert "до трех лет по 09.05.2029 г." in html
    sid = await sid_of(c, "Заявление на продление отпуска по беременности и родам")
    html = await prev(c, sid, {"ДатаНачала": "2026-12-17", "Причина": "осложнениями в родах", "Комментарий": "справка",
                               "НомерПисьма": "77", "ДатаПисьма": "2026-11-25"}, "p3")
    assert "по 17.12.2026 г. в связи с осложнениями в родах" in html and "Справку № 77 от 25.11.2026 г. прилагаю." in html


async def test_wording_as_templates(env):
    c = env["c"]
    await login(c, "st100002")
    sid = await sid_of(c, "Заявление на выход из академического отпуска")
    html = await prev(c, sid, {"ДатаОкончания": "2026-11-02", "Причина": "семейным обстоятельствам", "Комментарий": "нет"}, "p3")
    assert "вышедшим" in html and "находился" in html and "Петрова Анна Сергеевна" in html   # как в макете: «от студента»
    assert "+7 900 000-00-01" in html and "Телефон:" not in html   # в шапке — только значение, как в макете


async def test_academ_extension_and_caption(env):
    c = env["c"]
    await login(c)
    sid = await sid_of(c, "Заявление на продление академического отпуска")
    html = await prev(c, sid, {"ТекущееОкончание": "2026-11-01", "Причина": "семейным обстоятельствам", "Комментарий": "x"}, "p1")
    assert "с 02.11.2026 г. по 01.11.2027 г." in html
    sid = await sid_of(c, "Заявление на академический отпуск")
    html = await prev(c, sid, {"ДатаНачала": "2026-11-02", "Причина": "семейным обстоятельствам"}, "p1")
    assert '<small>ознакомлен (-а)</small>' in html and "____" not in html.split("ознакомлен")[0][-300:]


async def test_need_auto_reject(env, tmp_path):
    """«Требуется действие» 2 рабочих дня без исправлений → автоматически «Отклонено», в 1С уходит отказ."""
    import sqlite3
    c = env["c"]
    assert dates.workday_after(dt.date(2026, 10, 14), 2, set()) == dt.date(2026, 10, 16)       # ср → до пт включительно
    assert dates.workday_after(dt.date(2026, 10, 30), 2, {"2026-11-04"}) == dt.date(2026, 11, 3)
    await login(c)
    ids = []
    for _ in range(2):
        s, j = await order(c, {**BASE, "svc": "study", "kind": "umu", "qty": 1})
        ids.append(j["order"]["id"])
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    for oid in ids:
        await c.post("/api/staff/demo", json={"id": oid, "to": "need", "comment": "Уточните данные"}, headers=H)
    await c.post("/api/logout", headers=H)
    await login(c)
    o = (await (await c.get(f"/api/orders/{ids[0]}")).json())["order"]
    assert o["need_until"] == dates.workday_after(dt.date.today(), 2, set(dates.HOLIDAYS)).isoformat()
    # первый заказ: «Требуется действие» поставили 10 дней назад — срок прошёл
    con = sqlite3.connect(tmp_path / "lk.db")
    hist, = con.execute("SELECT hist FROM orders WHERE id=?", (ids[0],)).fetchone()
    old = (dt.date.today() - dt.timedelta(days=10)).isoformat() + "T10:00:00"
    con.execute("UPDATE orders SET hist=? WHERE id=?", (json.dumps([dict(h, at=old) for h in json.loads(hist)]), ids[0]))
    con.commit()
    con.close()
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    await c.post("/api/staff/sync", headers=H)
    await c.post("/api/staff/sync", headers=H)   # повторный опрос 1С не возвращает «Требуется действие»
    await c.post("/api/logout", headers=H)
    await login(c)
    o1 = (await (await c.get(f"/api/orders/{ids[0]}")).json())["order"]
    o2 = (await (await c.get(f"/api/orders/{ids[1]}")).json())["order"]
    assert o1["status"] == "rej" and "автоматически" in o1["comment"] and o1["need_until"] is None
    assert o2["status"] == "need"                                    # у второго срок ещё идёт
    reqs = env["m"].app["reqs"]
    assert reqs[o1["onec_id"]]["status"] == "rej" and "автоматически" in reqs[o1["onec_id"]]["comment"]
    notes = (await (await c.get("/api/notices")).json())["items"]
    assert any("автоматически" in n["body"] for n in notes)
    # исправить после отклонения нельзя
    from aiohttp import FormData
    f = FormData(default_to_multipart=True)
    f.add_field("json", json.dumps({"email": "a@b.ru", "kind": "umu", "qty": 1}))
    r = await c.post(f"/api/orders/{ids[0]}", data=f, headers=H)
    assert r.status == 403



async def test_call_reason_schedule_and_duplicate(env):
    """Справка-вызов: основание из списка, даты — из графика 1С, всегда 1 экземпляр; повтор нельзя."""
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "call", "inn": "7700000016"})
    assert s == 422 and "основание" in j["errors"]["reason"]
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016", "qty": 5, "boss": "X",
                           "from": "2020-01-01", "to": "2020-01-02"})
    assert s == 200, j
    d = j["order"]["data"]
    assert (d["reason"], d["from"], d["to"], d["qty"]) == ("Прохождение промежуточной аттестации", "2027-01-11", "2027-01-31", 1)
    assert d["boss"] == "X"                                   # ФИО руководителя остаётся
    first = j["order"]
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016"})
    assert s == 200                                           # по тому же основанию — можно ещё раз (решение МФЦ 28.10)
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-s", "inn": "7700000016"})     # другое основание — можно
    assert s == 200
    s, j = await order(c, {**BASE, "program": "p2", "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016"})
    assert s == 422                                                                          # чужой период не подставить
    # изменение самого заказа дублем не считается; после отмены можно заказать снова
    from aiohttp import FormData
    f = FormData(default_to_multipart=True)
    f.add_field("json", json.dumps({**BASE, "reason": PR, "call": "p1-w", "inn": "7700000016"}))
    r = await c.post(f"/api/orders/{first['id']}", data=f, headers=H)
    assert r.status == 200, await r.text()
    await c.post(f"/api/orders/{first['id']}/cancel", headers=H)
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-w", "inn": "7700000016"})
    assert s == 200
    me = await (await c.get("/api/me")).json()
    assert me["programs"][0]["calls"] and me["call_reasons"]


async def test_kug_upload(env):
    """КУГ из Excel: администратор загружает файл, у группы без графика в 1С даты справки-вызова подставляются сами."""
    import io
    import openpyxl
    from aiohttp import FormData
    from lk import kug
    c = env["c"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["График на 2026/27 уч. год"])
    ws.append(["№", "Группа", "Основание", "Начало", "Окончание"])
    ws.append([1, "ТТЗ-301, ТТЗ-302", "Промежуточная аттестация (зимняя сессия)", dt.date(2026, 11, 23), dt.date(2026, 12, 12)])
    ws.append([2, "ттз 301", "Промежуточная аттестация (летняя сессия)", "17.05.2027", "05.06.2027"])
    ws.append([3, "ТТЗ-301", "ГИА", "01.06.2027", "01.05.2027"])                     # ошибка: окончание раньше
    buf = io.BytesIO()
    wb.save(buf)

    def form(data, name):
        f = FormData(default_to_multipart=True)
        f.add_field("files", data, filename=name)
        return f
    await login(c, "mfc01")                                                            # оператор — только смотреть
    assert (await c.post("/api/staff/kug", data=form(buf.getvalue(), "kug.xlsx"), headers=H)).status == 403
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    r = await c.post("/api/staff/kug", data=form(b"x,y\n1,2", "kug.csv"), headers=H)
    assert r.status == 422 and "заголовков" in (await r.json())["error"]
    r = await c.post("/api/staff/kug", data=form(b"x", "kug.xls"), headers=H)
    assert r.status == 422 and ".xlsx" in (await r.json())["error"]
    r = await c.post("/api/staff/kug", data=form(buf.getvalue(), "kug.xlsx"), headers=H)
    j = await r.json()
    assert r.status == 200 and j["rows"] == 2 and j["groups"] == 2 and "строка 5" in j["warnings"][0]
    g = (await (await c.get("/api/staff/kug")).json())["groups"]
    assert len(g["ТТЗ301"]) == 2 and g["ТТЗ302"][0]["from"] == "2026-11-23"
    t = await c.get("/api/staff/kug/template")
    assert t.status == 200 and kug.parse(await t.read(), "t.xlsx")[2] == 3                # шаблон сам читается
    # CSV тоже можно (в т. ч. из Excel: ; и cp1251)
    csv_ = "Группа;Основание;Начало;Окончание\nТТЗ-301;Промежуточная аттестация (зимняя сессия);23.11.2026;12.12.2026\n".encode("cp1251")
    assert kug.parse(csv_, "k.csv")[0]["ТТЗ301"][0]["to"] == "2026-12-12"
    await c.post("/api/logout", headers=H)
    await login(c)                                                                     # Иванов: p2 = ТТЗ-301
    me = await (await c.get("/api/me")).json()
    p2 = next(p for p in me["programs"] if p["id"] == "p2")
    p1 = next(p for p in me["programs"] if p["id"] == "p1")
    assert p2["calls_src"] == "kug" and len(p2["calls"]) == 2 and p1["calls_src"] == "1c"
    s, j = await order(c, {**BASE, "program": "p2", "svc": "call", "reason": PR, "call": p2["calls"][0]["id"], "inn": "7700000016",
                           "boss": "Сидоров С.С."})
    assert s == 200 and (j["order"]["data"]["from"], j["order"]["data"]["to"]) == ("2026-11-23", "2026-12-12")



async def test_status_flows_and_income2(env):
    """Маршруты статусов: бумажные reg→sign→ready→done; о доходах / форма № 2 / справка о периоде обучения — через
    «Передано исполнителю»; заявления заканчиваются на «Передано исполнителю»; ЭЦП — sign→signed."""
    c = env["c"]
    await login(c)

    async def go(p, files=None):
        s, j = await order(c, {**BASE, **p}, files)
        assert s == 200, j
        return j["order"]
    paper_ = await go({"svc": "extract", "ord": "СК-1234", "qty": 1})
    inc2 = await go({"svc": "income2", "from": "2026-01", "to": "2026-09", "qty": 1})
    s, j = await order(c, {**BASE, "svc": "income2", "from": "2026-01", "to": "2099-01", "qty": 1})
    assert s == 422 and "не позже" in j["errors"]["to"]                    # заполнение как у обычной о доходах
    assert inc2["title"] == "Справка о стипендии и иных доходах (по форме № 2)" and inc2["flow"] == "long"
    forms = (await (await c.get("/api/me")).json())["forms"]

    def fill(t):                                   # заполнить все поля заявления чем-нибудь допустимым
        return {f["k"]: f["opts"][0] if f["type"] == "select" else "2027-03-01" if f["type"] == "date" else
                "5" if f["type"] == "number" else "x" for f in forms[t]["fields"] if f["type"] != "calc"}
    app = await go({"svc": await sid_of(c, "Заявление на обновление персональных данных"), "fields": fill("Заявление на обновление персональных данных")}, [("з.pdf", PDF)])
    t = "Заявление на справку о периоде обучения"
    per = await go({"svc": await sid_of(c, t), "fields": fill(t)}, [("з.pdf", PDF)])
    assert (paper_["flow"], app["flow"], per["flow"]) == ("paper", "app", "long")
    await c.post("/api/logout", headers=H)
    await login(c, "mfcadmin")
    seen = {}
    for o in (paper_, inc2, app, per):
        st = [o["status"]]
        for _ in range(6):
            x = (await (await c.post("/api/staff/demo", json={"id": o["id"]}, headers=H)).json())["order"]
            if x["status"] != st[-1]:
                st.append(x["status"])
        seen[o["flow"] + ("-" + o["svc"] if o["flow"] == "long" else "")] = st
    assert seen["paper"] == ["reg", "sign", "ready", "done"]
    assert seen["long-income2"] == ["reg", "work", "sign", "ready", "done"]
    assert seen["app"] == ["reg", "work"]
    await c.post("/api/logout", headers=H)
    await login(c)
    a = (await (await c.get(f"/api/orders/{app['id']}")).json())["order"]
    assert a["final"] and a["can_review"] and a["status_text"] == "Передано в дирекцию" and a["to_archive"] == 7
    # Для обычных заявлений фактический статус «sign» тоже является конечной передачей в дирекцию.
    assert catalog.is_final({"status": "sign", "data": {"flow": "app", "app": 1}})
    assert catalog.can_review({"status": "sign", "data": {"flow": "app", "app": 1}})
    # Исключение — заявление на справку о периоде обучения: оно идёт по полному маршруту.
    assert not catalog.is_final({"status": "sign", "data": {"flow": "long", "app": 1}})


# ---------------- перечень услуг 2026–2027, иностранцы, раздельные программы ----------------
def _ids(me, prog=0):
    return {x["t"] for x in me["services"] if x["id"] in me["programs"][prog]["services"]}


async def test_access_by_level_list(env):
    c = env["c"]
    await login(c, "st100004")                                   # СПО
    t = _ids(await (await c.get("/api/me")).json())
    assert "Справка-вызов" not in t and "Заявление на академический отпуск" not in t
    assert {"Справка о подтверждении обучения", "Выписка из приказа", "Заявление на дубликат свидетельства о регистрации"} <= t
    for gone in ("Заявление о регистрации по месту пребывания", "Заявление на выдачу выписки из домовой книги",
                 "Заявление на смену пароля от личного кабинета студента", "Заявление на перевод в другую учебную группу",
                 "Заявление на пересдачу дисциплины с целью повышения оценки"):
        assert gone not in t                                    # убраны из ЛК 28.10.2026
    await c.post("/api/logout", headers=H)
    await login(c, "st100003")                                   # аспирантура
    t = _ids(await (await c.get("/api/me")).json())
    assert {"Заявление на перевод на другую научную специальность", "Заявление на перевод на другую кафедру",
            "Заявление на академический отпуск", "Заявление на обновление персональных данных"} <= t
    assert "Справка-вызов" not in t and "Заявление на досрочную сдачу сессии" not in t and "Заявление на ускоренное обучение" not in t
    me = await (await c.get("/api/me")).json()
    for a in [x for x in me["services"] if x.get("app") and x["id"] in me["programs"][0]["services"]]:
        f = me["forms"][a["t"]]["fields"]
        vals = fill_vals(f, me["programs"][0]["level"])
        html = (await (await c.post("/api/preview", json={"program": "p4", "email": "a@b.ru", "phone": "+7 900", "svc": a["id"], "fields": vals},
                                    headers=H)).json())["html"].replace("&nbsp;", " ")
        assert 'class="blank"' not in html and "[" not in html, a["t"]
    s, j = await order(c, {"program": "p4", "email": "a@b.ru", "svc": "call", "reason": "Прохождение промежуточной аттестации",
                           "from": "2027-01-11", "to": "2027-01-20", "inn": "7700000016"})
    assert s == 403                                              # вызов аспиранту не положен по перечню
    await c.post("/api/logout", headers=H)
    await login(c)                                               # Иванов: бакалавриат + специалитет
    me = await (await c.get("/api/me")).json()
    assert [p["level"] for p in me["programs"]] == ["bak", "spec"] and me["levels"]["spec"] == "Специалитет"
    assert "Заявление на смену ФИО" not in _ids(me)              # заменено «обновлением персональных данных»


async def test_foreign_student(env):
    c = env["c"]
    await login(c, "st100005")
    me = await (await c.get("/api/me")).json()
    assert me["foreign"] and "Управлении международного сотрудничества" in me["foreign_note"]
    t = _ids(me)
    assert "Справка о подтверждении обучения" not in t and "Заявление на академический отпуск" not in t
    assert {"Справка-вызов", "Справка о стипендии и иных доходах", "Заявление на справку о периоде обучения", "Выписка из приказа",
            "Заявление на перевод на другую основу обучения"} <= t
    assert "Заявление на пересдачу дисциплины с целью повышения оценки" not in t
    B = {"program": "p6", "email": "a@b.ru"}
    items = (await (await c.get("/api/prikazy?program=p6")).json())["items"]
    assert {x["n"]: x["allowed"] for x in items} == {"СК-1777": False, "3120-ст": True, "455-пр": True}
    s, j = await order(c, {**B, "svc": "extract", "ord": "СК-1777", "qty": 1})
    assert s == 422 and "международного сотрудничества" in j["errors"]["ord"]
    s, j = await order(c, {**B, "svc": "extract", "ord": "3120-ст", "qty": 1})
    assert s == 200 and j["order"]["data"]["qty"] == 2 and j["order"]["data"]["foreign"] == 1   # всегда 2 экземпляра
    s, j = await order(c, {**B, "svc": "study", "kind": "ecp"})
    assert s == 403


async def test_programs_are_separate(env):
    c = env["c"]
    await login(c)
    s, j = await order(c, {**BASE, "svc": "income", "from": "2026-01", "to": "2026-03", "qty": 1})
    assert s == 200
    s, j = await order(c, {**BASE, "program": "p2", "svc": "income", "from": "2026-01", "to": "2026-03", "qty": 1})
    assert s == 200
    a = (await (await c.get("/api/orders?program=p1")).json())["items"]
    b = (await (await c.get("/api/orders?program=p2")).json())["items"]
    assert {o["program"] for o in a} == {"p1"} and {o["program"] for o in b} == {"p2"} and len(a) == len(b) == 1
    env["app"]["db"].x("INSERT INTO notices(person, title, body, oid, at) VALUES('100001','t','b',?, '2026-10-01')", (b[0]["id"],))
    n1 = (await (await c.get("/api/notices?program=p1")).json())["items"]
    n2 = (await (await c.get("/api/notices?program=p2")).json())["items"]
    assert not n1 and len(n2) == 1


async def test_call_print_form_and_preview(env):
    c = env["c"]
    await login(c)
    B = {**BASE, "program": "p2"}                                   # специалитет, заочная, графика в 1С нет — даты вручную
    r = await c.post("/api/preview", json={**B, "svc": "call", "reason": "Прохождение промежуточной аттестации", "from": "2026-11-02",
                                           "to": "2026-11-26", "inn": "7700000016"}, headers=H)
    assert r.status == 200, await r.text()
    html = (await r.json())["html"].replace("&nbsp;", " ")
    for t in ("СПРАВКА-ВЫЗОВ", "№ 1368", ">173<", "Иванову Ивану Ивановичу,", ">заочной<", "на 3 курсе", "прохождения промежуточной аттестации",
              ">02.11.2026<", ">26.11.2026<", ">25<", "А007-00115-77/01130110", "23.05.03 - Эксплуатация", "специальности",
              "Захарова С.А.", "<u>обучающемуся</u>"):
        assert t in html, t
    assert "<img" not in html                                       # без герба, печати и подписи
    s, j = await order(c, {**B, "svc": "call", "reason": "Прохождение промежуточной аттестации", "from": "2026-11-02", "to": "2026-11-26",
                           "inn": "7700000016"})
    assert s == 200 and j["order"]["data"]["signer"]["fio"] == "С.А. Захарова"
    s, j = await order(c, {**B, "svc": "call", "reason": "Прохождение промежуточной аттестации", "from": "2026-11-20", "to": "2026-12-05",
                           "inn": "7700000016"})
    assert s == 200                                                  # то же основание повторно — можно
    s, j = await order(c, {**B, "svc": "call", "reason": "Прохождение промежуточной аттестации", "from": "2027-05-10", "to": "2027-05-30",
                           "inn": "7700000016"})
    assert s == 200                                                  # следующая сессия — можно
    req = list(env["m"].app["reqs"].values())[-1]["body"]
    assert req["call_article"] == "173" and req["call_purpose"] == "прохождения промежуточной аттестации" and "СПРАВКА-ВЫЗОВ" in req["print_html"]


async def test_accusative_and_capital(env):
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на продление сессии")
    html = (await (await c.post("/api/preview", json={**BASE, "svc": sid, "fields": {"Причина": "болезнью",
                                                      "Комментарий": "медицинская справка № 5"}}, headers=H)).json())["html"].replace("&nbsp;", " ")
    assert "Медицинскую справку № 5 прилагаю." in html
    f = next(x for x in me["forms"]["Заявление на продление сессии"]["fields"] if x["k"] == "Комментарий")
    assert f["acc"] and f["l"] == "Приложенные документы (указать в винительном падеже)" and "note" not in f


async def test_upload_limits(env):
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    assert me["uploads"]["max_mb"] == 5 and me["uploads"]["max_total_mb"] == 15
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на отчисление по собственному желанию")
    big = b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 10)
    s, j = await order(c, {**BASE, "svc": sid, "fields": {"ДатаНачала": "2027-06-01"}}, [("a.pdf", big)])
    assert s == 422 and "больше 5 МБ" in j["errors"]["files"]
    mid = b"%PDF-1.4\n" + b"0" * (4 * 1024 * 1024)
    s, j = await order(c, {**BASE, "svc": sid, "fields": {"ДатаНачала": "2027-06-01"}}, [(f"{i}.pdf", mid) for i in range(4)])
    assert s == 422 and "вместе" in j["errors"]["files"]
    s, j = await order(c, {**BASE, "svc": sid, "fields": {"ДатаНачала": "2027-06-01"}}, [(f"{i}.pdf", b"%PDF-1.4 x") for i in range(6)])
    assert s == 422 and "Не больше 5 файлов" in j["errors"]["files"]


async def test_call_never_for_spo_asp(env):
    """Справки-вызова у СПО и аспирантуры по положению нет: даже если включить в матрице доступа."""
    c = env["c"]
    await login(c, "mfcadmin")
    s = (await (await c.get("/api/staff/settings")).json())
    assert s["locked"]["call"] == ["asp", "spo"] and "call" not in s["settings"]["signers"]["spo"] | s["settings"]["signers"]["asp"]
    acc = s["settings"]["access"]
    acc["call"] = ["spo", "bak", "spec", "mag", "asp"]
    assert (await c.post("/api/staff/settings", json={"key": "access", "value": acc}, headers=H)).status == 200
    await c.post("/api/logout", headers=H)
    for who, prog in (("st100004", "p5"), ("st100003", "p4")):
        await login(c, who)
        me = await (await c.get("/api/me")).json()
        assert "call" not in me["programs"][0]["services"]
        s_, j = await order(c, {"program": prog, "email": "a@b.ru", "svc": "call", "reason": "Прохождение промежуточной аттестации",
                                "from": "2027-01-11", "to": "2027-01-20", "inn": "7700000016"})
        assert s_ == 403
        await c.post("/api/logout", headers=H)


async def test_personal_data_template(env):
    """«Обновление персональных данных» — по макету «смена ФИО»: шапка со старым ФИО, что меняется / причина / приложения — списками."""
    c = env["c"]
    await login(c, "st100002")
    me = await (await c.get("/api/me")).json()
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на обновление персональных данных")
    f = {x["k"]: x for x in me["forms"]["Заявление на обновление персональных данных"]["fields"]}
    assert f["Что"]["type"] == f["Причина"]["type"] == f["Комментарий"]["type"] == "select"
    html = (await (await c.post("/api/preview", json={"program": "p3", "email": "a@b.ru", "phone": "+7 900", "svc": sid, "fields": {
        "Что": "фамилии и имени", "НоваяФамилия": "Смирнова", "НовоеИмя": "Анна", "НовоеОтчество": "лишнее", "Причина": "регистрацией брака",
        "Комментарий": "копию паспорта и копию свидетельства о заключении брака"}}, headers=H)).json())["html"].replace("&nbsp;", " ")
    assert "Прошу считать меня по фамилии и имени Смирнова Анна в связи с регистрацией брака." in html and "лишнее" not in html
    assert all("паспорта" in o for o in f["Комментарий"]["opts"])          # копия паспорта — всегда
    assert f["НовоеИмя"]["show"] == {"by": "Что", "has": "имен"}
    assert "Копию паспорта и копию свидетельства о заключении брака прилагаю." in html and "Петрова Анна Сергеевна" in html and "от студента 2 курса" in html


def test_names_genitive():
    from lk.names import fio_gen, inst_gen, inst_prep
    cases = {("Иванов Иван Иванович", "m"): "Иванова Ивана Ивановича", ("Петрова Анна Сергеевна", "f"): "Петровой Анны Сергеевны",
             ("Сидоров Пётр Алексеевич", "m"): "Сидорова Петра Алексеевича", ("Кузнецова Мария Олеговна", "f"): "Кузнецовой Марии Олеговны",
             ("Чайковский Андрей Игоревич", ""): "Чайковского Андрея Игоревича", ("Шевченко Ольга Игоревна", ""): "Шевченко Ольги Игоревны",
             ("Белых Игорь Петрович", ""): "Белых Игоря Петровича", ("Сорока Никита Ильич", ""): "Сороки Никиты Ильича",
             ("Нгуен Тхи Лан", "f"): "Нгуен Тхи Лан"}
    for (fio, g), want in cases.items():
        assert fio_gen({"fio": fio, "gender": g}) == want
    assert fio_gen({"fio": "Иванов Иван Иванович", "fio_rod": "из 1С"}) == "из 1С"
    assert inst_gen("Институт агробиотехнологии") == "Института агробиотехнологии" and inst_gen("Колледж") == "Колледжа"
    assert inst_prep("Институт агробиотехнологии") == "Институте агробиотехнологии"


async def test_head_in_genitive(env):
    c = env["c"]
    await login(c, "st100002")
    me = await (await c.get("/api/me")).json()
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на продление сессии")
    html = (await (await c.post("/api/preview", json={"program": "p3", "email": "a@b.ru", "phone": "+7 900", "svc": sid,
                                                      "fields": {"Причина": "болезнью"}}, headers=H)).json())["html"].replace("&nbsp;", " ")
    head = html.split("<h4>")[0]
    assert "Петровой Анны Сергеевны" in head and "Института агробиотехнологии" in head and "от студента 2 курса" in head
    assert "Петрова Анна Сергеевна" in html.split("<h4>")[1]          # подпись внизу — в именительном


# ---------------- пачка 3 (28.10.2026): иные обстоятельства, перевод по КЦП ----------------
async def test_other_reason_free_text(env):
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    sid = next(x["id"] for x in me["services"] if x["t"] == "Заявление на досрочную сдачу сессии")
    f = next(x for x in me["forms"]["Заявление на досрочную сдачу сессии"]["fields"] if x["k"] == "Причина")
    assert f["other"] == "иными обстоятельствами" in f["opts"] and "иными уважительными причинами" not in f["opts"]
    s, j = await order(c, {**BASE, "svc": sid, "fields": {"Причина": "иными обстоятельствами"}})
    assert s == 422 and "Причина_иное" in j["errors"]
    html = (await (await c.post("/api/preview", json={**BASE, "svc": sid, "fields": {"Причина": "иными обстоятельствами",
                                                      "Причина_иное": "участием в олимпиаде"}}, headers=H)).json())["html"].replace("&nbsp;", " ")
    assert "в связи с иными обстоятельствами (участием в олимпиаде)" in html
    ak = next(x for x in me["forms"]["Заявление на академический отпуск"]["fields"] if x["k"] == "Причина")
    assert ak["opts"] == ["по медицинским показаниям", "по состоянию здоровья", "в связи с прохождением военной службы", "по семейным обстоятельствам",
                          "по иным обстоятельствам"] and ak["other"] == "по иным обстоятельствам"


async def test_transfer_directions_by_level(env):
    c = env["c"]
    await login(c)                                               # p1 — бакалавриат
    me = await (await c.get("/api/me")).json()
    t = "Заявление на перевод на другое направление подготовки/форму обучения"
    sid = next(x["id"] for x in me["services"] if x["t"] == t)
    fl = me["forms"][t]["fields"]
    assert [x.get("src") for x in fl[:4]] == ["inst", None, "dir", "prof"]
    bs = catalog.src_opts(dict(fl[2], dep=None), {}, "bak")
    assert any(x.startswith("35.03.04 ") for x in bs) and any(x.startswith("36.05.01 ") for x in bs)
    assert not any(".04." in x[:6] for x in bs)                  # бакалавру магистерских направлений не показываем
    ok = {"НовыйФакультет": "Институт агробиотехнологии", "НовыйКурс": "2", "НоваяСпециальность": "35.03.04 Агрономия",
          "НоваяСпециализация": "Агробизнес", "НоваяОснова": "бюджетной основе", "НоваяФормаОбучения": "очной"}
    html = (await (await c.post("/api/preview", json={**BASE, "svc": sid, "fields": ok}, headers=H)).json())["html"].replace("&nbsp;", " ")
    assert "в Институт агробиотехнологии на 2 курс, направление 35.03.04 Агрономия, профиль Агробизнес" in html
    s, j = await order(c, {**BASE, "svc": sid, "fields": {**ok, "НоваяСпециальность": "35.04.04 Агрономия"}})
    assert s == 422 and "НоваяСпециальность" in j["errors"]      # магистратура — не для бакалавра
    s, j = await order(c, {**BASE, "svc": sid, "fields": {**ok, "НовыйФакультет": "Институт зоотехнии и биологии"}})
    assert s == 422 and "НоваяСпециальность" in j["errors"]      # направление не из выбранного института
    s, j = await order(c, {**BASE, "svc": sid, "fields": {**ok, "НоваяСпециализация": "Генетика животных"}})
    assert s == 422 and "НоваяСпециализация" in j["errors"]
    fv = {"НовыйФакультет": "Институт экономики и управления АПК", "НоваяСпециальность": "38.03.01 Экономика",
          "НоваяСпециализация": "Экономика цифрового предприятия"}
    ff = next(x for x in fl if x["k"] == "НоваяФормаОбучения")
    assert catalog.src_opts(ff, {**fv, "НовыйКурс": "1"}, "bak") == ["очной"]   # на 1-й курс: заочной и очно-заочной по КЦП нет
    assert catalog.src_opts(ff, {**fv, "НовыйКурс": "3"}, "bak") == ["очной", "очно-заочной", "заочной"]   # старшие курсы — все
    s, j = await order(c, {**BASE, "svc": sid, "fields": {**ok, **fv, "НовыйКурс": "1", "НоваяФормаОбучения": "заочной"}})
    assert s == 422 and "НоваяФормаОбучения" in j["errors"]
    s, j = await order(c, {**BASE, "svc": sid, "fields": {**ok, **fv, "НовыйКурс": "3", "НоваяФормаОбучения": "заочной"}})
    assert "НоваяФормаОбучения" not in (j.get("errors") or {})
    assert "directions" in me and len(me["directions"]["bs"]) == 33 and len(me["directions"]["mag"]) == 28


def test_spo_has_no_transfer():
    acc = catalog.DEFAULT_ACCESS
    sid = next(x["id"] for x in catalog.BASE_SERVICES if x["t"] == "Заявление на перевод на другое направление подготовки/форму обучения")
    assert "spo" not in acc[sid] and catalog.directions("spo") == []


def test_academ_min_date_message():
    tpl = {"fields": [{"k": "ДатаНачала", "l": "Начало", "type": "date", "req": 1, "min": {"workdays": 5}}]}
    today = dt.date(2026, 10, 28)
    _, _, E = rules.validate({"id": "a", "t": "Заявление на академический отпуск", "app": True},
                             {"email": "a@b.ru", "fields": {"ДатаНачала": "2026-10-30"}}, settings={"holidays": []}, tpl=tpl, today=today)
    assert E["ДатаНачала"].startswith("Срок начала отпуска устанавливается не ранее чем через пять рабочих дней") and "2026" not in E["ДатаНачала"]


async def test_schedule_call_reasons_are_official(env):
    """Периоды из 1С/КУГ («Промежуточная аттестация (зимняя сессия)») студенту показываются одной из шести формулировок."""
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    rs = {x["reason"] for p in me["programs"] for x in p.get("calls", [])}
    assert rs and rs <= set(catalog.CALL_REASONS), rs


async def test_call_all_reasons_and_schedule(env):
    """Все шесть оснований; по основанию с периодом в графике даты берутся из графика, по остальным — вручную."""
    c = env["c"]
    await login(c)
    me = await (await c.get("/api/me")).json()
    assert me["call_reasons"] == catalog.CALL_REASONS
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "inn": "7700000016"})
    assert s == 422 and "call" in j["errors"]                     # у p1 две сессии — нужно выбрать период
    s, j = await order(c, {**BASE, "svc": "call", "reason": "Прохождение итоговой аттестации", "inn": "7700000016"})
    assert s == 422 and "from" in j["errors"] and "to" in j["errors"]   # в графике нет — даты вручную
    s, j = await order(c, {**BASE, "svc": "call", "reason": "Прохождение итоговой аттестации", "from": "2027-05-10",
                           "to": "2027-05-20", "inn": "7700000016"})
    assert s == 200 and j["order"]["data"]["from"] == "2027-05-10"
    s, j = await order(c, {**BASE, "svc": "call", "reason": PR, "call": "p1-s", "from": "2026-01-01", "to": "2026-01-02", "inn": "7700000016"})
    assert s == 200 and (j["order"]["data"]["from"], j["order"]["data"]["to"]) == ("2027-06-07", "2027-06-27")   # даты — из графика


def test_maternity_leave_by_pdr():
    t = "Заявление на предоставление отпуска по беременности и родам"
    svc = {"id": "a", "t": t, "app": True}
    tpl = __import__("lk.apps", fromlist=["default"]).default(t)
    today = dt.date(2026, 10, 28)
    base = {"НомерПисьма": "910", "ДатаПисьма": "2026-10-27"}
    for kind, a, b in (("одноплодная", "2026-10-28", "2027-03-17"), ("многоплодная", "2026-10-14", "2027-04-26")):
        d, _, E = rules.validate(svc, {"email": "a@b.ru", "fields": {**base, "Беременность": kind, "Срок": "30", "ПДР": "2027-01-06"}},
                                 settings={"holidays": []}, tpl=tpl, today=today)
        assert not E and (d["fields"]["ДатаНачала"], d["fields"]["ДатаОкончания"]) == (a, b), (kind, E, d["fields"])
    _, _, E = rules.validate(svc, {"email": "a@b.ru", "fields": {**base, "Беременность": "одноплодная", "Срок": "29", "ПДР": "2026-10-27"}},
                             settings={"holidays": []}, tpl=tpl, today=today)
    assert E["Срок"] == "Не менее 30 недель" and E["ПДР"] == "Не раньше даты подачи заявления"
    _, _, E = rules.validate(svc, {"email": "a@b.ru", "fields": {**base, "Беременность": "многоплодная", "Срок": "28", "ПДР": "2027-01-06"}},
                             settings={"holidays": []}, tpl=tpl, today=today)
    assert not E                                                 # многоплодная — с 28 недель
    _, _, E = rules.validate(svc, {"email": "a@b.ru", "fields": {**base, "Беременность": "многоплодная", "Срок": "27", "ПДР": "2027-01-06"}},
                             settings={"holidays": []}, tpl=tpl, today=today)
    assert E["Срок"] == "Не менее 28 недель"


def test_maternity_extension_form():
    t = "Заявление на продление отпуска по беременности и родам"
    tpl = __import__("lk.apps", fromlist=["default"]).default(t)
    assert [f["k"] for f in tpl["fields"]] == ["ДатаНачала", "Причина", "Комментарий", "НомерПисьма", "ДатаПисьма"]
    assert tpl["fields"][1]["opts"] == ["установлением диагноза многоплодной беременности", "осложнениями в родах"]
    assert "[Комментарий] № [НомерПисьма] от [ДатаПисьма] г. прилагаю." in tpl["body"] and tpl["fields"][2]["acc"]
