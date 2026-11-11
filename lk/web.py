"""Веб-слой МФЦ. В portal_mode маршруты сотрудников не регистрируются. make_app(cfg) — aiohttp-приложение:
его можно запустить отдельно (run.py) или встроить в другое (app.add_subapp("/lk/", make_app(cfg)))."""
import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from . import apps, catalog, kug, rules
from .auth import Auth
from .catalog import LEVELS, STUDY_KINDS, Settings, clean_tpl
from .db import DB, now
from .egrul import Egrul
from .onec import make_onec, OneCError, OneCUnavailable
from .orders import Denied, Invalid, Orders

log = logging.getLogger("lk.web")
STATIC = Path(__file__).resolve().parent.parent / "static"
COOKIE = "lk_s"


def J(data, status=200):
    return web.json_response(data, status=status, dumps=lambda x: json.dumps(x, ensure_ascii=False))


def _ip(r):
    return r.headers.get("X-Forwarded-For", r.remote or "").split(",")[-1].strip()


@web.middleware
async def mw(request, handler):
    # CSRF: изменяющие запросы — только с заголовком X-LK (чужой сайт не может его поставить без CORS)
    if request.method in ("POST", "PUT", "DELETE") and "/api/" in request.path and request.headers.get("X-LK") != "1":
        return J({"error": "Запрос отклонён"}, 403)
    try:
        resp = await handler(request)
    except Invalid as e:
        resp = J({"error": "Проверьте поля формы", "errors": e.errors}, 422)
    except Denied as e:
        resp = J({"error": str(e)}, 403)
    except OneCUnavailable as e:
        log.warning("1C unavailable: %s", e)
        resp = J({"error": "1С сейчас недоступна. Попробуйте через несколько минут"}, 503)
    except OneCError as e:
        log.warning("1C: %s", e)
        resp = J({"error": "1С не приняла запрос. Обратитесь в МФЦ"}, 502)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    if "/api/" in request.path:
        h.setdefault("Cache-Control", "no-store")
    elif request.path.endswith((".html", "/")) or "." not in request.path.rsplit("/", 1)[-1]:
        h.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                                "frame-src 'self' https:; base-uri 'none'; form-action 'self'")
    return resp


def make_app(cfg):
    portal_mode = bool(cfg.get("portal", {}).get("enabled"))
    db = DB(cfg["data_dir"])
    onec = make_onec(cfg)
    st = Settings(db)
    eg = Egrul(cfg, db)
    auth = Auth(cfg, db, onec)
    od = Orders(cfg, db, st, onec, eg)
    app = web.Application(middlewares=[mw], client_max_size=(max(cfg["uploads"]["max_total_mb"], 10) + 2) * 1024 * 1024)
    app.update(cfg=cfg, db=db, onec=onec, settings=st, auth=auth, orders=od)
    cookie_path = (cfg.get("base_path") or "").rstrip("/") + "/"

    def user(r, roles=None):
        u = auth.check(r.cookies.get(COOKIE))
        if not u:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Войдите в личный кабинет"}), content_type="application/json")
        if portal_mode and u["role"] != "student":
            raise web.HTTPForbidden(text=json.dumps({"error": "Раздел доступен только студентам"}), content_type="application/json")
        if roles and u["role"] not in roles:
            raise web.HTTPForbidden(text=json.dumps({"error": "Нет доступа"}), content_type="application/json")
        return u

    def set_cookie(resp, tok):
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="Lax", path=cookie_path,
                        secure=str(cfg.get("public_url", "")).startswith("https://"), max_age=cfg["auth"]["session_days"] * 86400)

    # ---------------- страницы ----------------
    async def enter(r):
        """Вход по одноразовой ссылке из 1С: …/enter?t=ТОКЕН."""
        u, err = await auth.by_token(r.query.get("t"), _ip(r))
        if not u:
            return web.Response(text=f"<!doctype html><meta charset=utf-8><p>{err}. <a href='./'>Войти по логину</a></p>",
                                content_type="text/html", status=403)
        resp = web.HTTPFound("./staff" if u["role"] in ("staff", "admin") else "./")
        set_cookie(resp, auth.start(u, _ip(r)))
        db.audit(u["person"], u["role"], "login", "по ссылке из 1С", _ip(r))
        raise resp

    # ---------------- доверенный вход из портала (только студенты) ----------------
    async def portal_enter(r):
        if not portal_mode:
            raise web.HTTPNotFound()
        from .portal_sso import SSOError, verify_and_consume
        try:
            identity = verify_and_consume(r.query.get("ticket", ""), cfg, db)
        except SSOError as e:
            log.warning("Portal SSO refused: %s", e)
            return web.Response(status=403, text="Недействительная или просроченная ссылка входа")
        tok = auth.start({"person": identity["person"], "name": identity["name"], "role": "student"}, _ip(r))
        db.audit(identity["person"], "student", "portal_sso", "", _ip(r))
        resp = web.HTTPFound("./")
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        set_cookie(resp, tok)
        raise resp

    async def portal_exchange(r):
        """Вход из окна личного кабинета платформы: билет приходит в заголовке Authorization (не в адресе, чтобы он не попал
        в журналы), в ответ ставится сессионная cookie. Заголовок X-Requested-With обязателен: чужой сайт не может его добавить."""
        if not portal_mode:
            raise web.HTTPNotFound()
        if not r.headers.get("X-Requested-With"):
            return J({"error": "Запрос отклонён"}, 403)
        from .portal_sso import SSOError, verify_and_consume
        head = r.headers.get("Authorization", "")
        try:
            identity = verify_and_consume(head[7:].strip() if head[:7].lower() == "bearer " else "", cfg, db)
        except SSOError as e:
            log.warning("Portal SSO refused: %s", e)
            return J({"error": "Недействительный или просроченный билет входа"}, 403)
        tok = auth.start({"person": identity["person"], "name": identity["name"], "role": "student"}, _ip(r))
        db.audit(identity["person"], "student", "portal_sso", "exchange", _ip(r))
        resp = J({"ok": True})
        resp.headers["Cache-Control"] = "no-store"
        set_cookie(resp, tok)
        return resp

    # ---------------- вход ----------------
    async def login(r):
        b = await r.json()
        u, err = await auth.login(b.get("login"), b.get("password"), _ip(r))
        if not u:
            return J({"error": err}, 401)
        if u["role"] == "student":
            try:
                await od.person(u["person"])   # запасная копия данных из 1С — на случай, если 1С ненадолго недоступна
            except OneCError:
                pass
        resp = J({"ok": True, "role": u["role"]})
        set_cookie(resp, auth.start(u, _ip(r)))
        db.audit(u["person"], u["role"], "login", "", _ip(r))
        return resp

    async def logout(r):
        auth.stop(r.cookies.get(COOKIE))
        resp = J({"ok": True})
        resp.del_cookie(COOKIE, path=cookie_path)
        return resp

    async def me(r):
        u = user(r)
        out = {"role": u["role"], "name": u["name"], "demo": bool(cfg.get("demo")),
               "status": {k: list(v) for k, v in catalog.STATUS.items()}, "flows": catalog.FLOWS}
        if u["role"] in ("staff", "admin"):
            out["assistant_url"] = cfg.get("assistant_url") or ""
            return J(out)
        s = st.all()
        p = await od.person(u["person"])
        progs = od.programs(p, s)
        for x in progs:     # у каждой программы (зачётной книжки) — свой набор услуг и свой подписант
            x["services"] = [c["id"] for c in st.for_student(p, x, s)]
            x["kinds"] = catalog.study_kinds(x["level"])
            x["signer"] = st.signer(x["level"], s, "ecp") if "ecp" in x["kinds"] else None
            x["call_article"] = catalog.CALL_ARTICLE.get(x["level"], "173")
        foreign = catalog.is_foreign(p)
        out.update({"person": {k: p.get(k) for k in ("fio", "email", "phone", "birth", "citizenship")}, "foreign": foreign,
                    "foreign_note": catalog.FOREIGN_NOTE if foreign else "", "foreign_apps_note": catalog.FOREIGN_APPS_NOTE if foreign else "",
                    "foreign_qty": catalog.FOREIGN_EXTRACT_QTY, "programs": progs,
                    "levels": LEVELS, "kinds": STUDY_KINDS, "limit": s["limit"], "keep_days": s["keep_days"], "need_days": s["need_days"],
                    "call_reasons": s["call_reasons"], "income_last": rules.income_last_month(__import__("datetime").date.today(), s["income_days"]),
                    "income_days": s["income_days"], "uploads": cfg["uploads"],
                    "services": st.services(s), "holidays": s["holidays"], "directions": s["directions"],
                    "forms": {x["t"]: {"fields": st.tpl(x["t"], s)["fields"]} for x in st.services(s) if x.get("app")}})
        return J(out)

    # ---------------- студент ----------------
    async def prikazy(r):
        u = user(r, ("student",))
        p, _ = await od.program(u["person"], r.query.get("program"))
        foreign = catalog.is_foreign(p)
        items = [dict(x, allowed=catalog.extract_allowed(x, foreign)) for x in await od.prikazy(u["person"], r.query.get("program"))]
        return J({"items": items})

    async def orgs(r):
        user(r)
        return J({"items": await eg.search(r.query.get("q", ""))})

    async def preview(r):
        u = user(r, ("student",))
        return J({"html": await od.preview(u, await r.json())})

    async def read_form(r):
        """Заказ приходит как multipart: поле json + файлы files (для заявлений)."""
        if r.content_type.startswith("multipart/"):
            payload, files, rd = {}, [], await r.multipart()
            async for part in rd:
                if part.name == "json":
                    payload = json.loads(await part.text())
                elif part.name == "files" and part.filename:
                    files.append((part.filename, bytes(await part.read(decode=False))))
            return payload, files
        if r.content_type == "application/x-www-form-urlencoded":
            return json.loads((await r.post()).get("json") or "{}"), []
        return await r.json(), []

    async def orders_list(r):
        u = user(r, ("student",))
        return J({"items": od.list(u["person"], r.query.get("program"))})

    async def order_create(r):
        u = user(r, ("student",))
        p, files = await read_form(r)
        return J({"order": await od.create(u, p, files)})

    async def order_one(r):
        u = user(r, ("student",))
        oid = int(r.match_info["id"])
        if r.method == "GET":
            return J({"order": od.get(u, oid)})
        p, files = await read_form(r)
        return J({"order": await od.update(u, oid, p, files, p.get("keep_files"))})

    async def order_cancel(r):
        u = user(r, ("student",))
        return J({"order": await od.cancel(u, int(r.match_info["id"]))})

    async def order_review(r):
        u = user(r, ("student",))
        b = await r.json()
        return J({"order": od.review(u, int(r.match_info["id"]), b.get("rating"), b.get("text"))})

    async def order_blank(r):
        u = user(r, ("student",))
        return J({"html": await od.blank(u, int(r.match_info["id"]))})

    async def order_doc(r):
        u = user(r, ("student",))
        data, ctype = await od.document(u, int(r.match_info["id"]))
        return web.Response(body=data, content_type=ctype.split(";")[0],
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('Справка.pdf')}"})

    async def order_sig(r):
        u = user(r, ("student",))
        data, ctype = await od.signature(u, int(r.match_info["id"]))
        return web.Response(body=data, content_type=ctype.split(";")[0],
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('Справка.sig')}"})

    async def order_file(r):
        u = user(r)
        path, name = od.file_path(u, int(r.match_info["id"]), r.match_info["f"])
        return web.FileResponse(path, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})

    async def notices(r):
        """Уведомления. С ?program= — только по заказам этой программы (и общие, без заказа)."""
        u = user(r)
        prog = r.query.get("program")
        cond, args = "person=?", [u["person"]]
        if prog:
            cond += " AND (oid IS NULL OR oid IN (SELECT id FROM orders WHERE person=? AND program=?))"
            args += [u["person"], prog]
        if r.method == "POST":
            db.x(f"UPDATE notices SET read=1 WHERE {cond}", tuple(args))
            return J({"ok": True})
        return J({"items": [dict(x) for x in db.q(f"SELECT * FROM notices WHERE {cond} ORDER BY id DESC LIMIT 50", tuple(args))]})

    # ---------------- сотрудник ----------------
    async def staff_orders(r):
        user(r, ("staff", "admin"))
        q = r.query
        items = od.staff_list(q.get("status") or None, q.get("svc") or None, (q.get("q") or "").strip() or None)
        names = {x["person"]: json.loads(x["data"]).get("fio") for x in db.q("SELECT person, data FROM people")}
        for o in items:
            o["fio"] = names.get(o["person"], "")
        return J({"items": items})

    async def staff_reviews(r):
        user(r, ("staff", "admin"))
        return J(od.reviews())

    async def staff_settings(r):
        u = user(r, ("staff", "admin"))
        if r.method == "GET":
            s = st.all()
            return J({"settings": s, "services": st.services(s), "levels": LEVELS, "access_cols": catalog.ACCESS_COLS,
                      "signer_groups": catalog.SIGNER_GROUPS, "signer_docs": catalog.SIGNER_DOCS, "group_docs": catalog.GROUP_DOCS, "locked": {"call": sorted(catalog.NO_CALL)}, "auto": apps.AUTO, "types": apps.TYPES,
                      "defaults": {t: apps.default(t) for t in apps.TPL}, "can_edit": u["role"] == "admin",
                      "tpls": {x["t"]: st.tpl(x["t"], s) for x in st.services(s) if x.get("app")}})
        if u["role"] != "admin":
            return J({"error": "Менять настройки может администратор МФЦ"}, 403)
        b = await r.json()
        key, val = b.get("key"), b.get("value")
        try:
            if key == "signers":
                val = {g: {d: {"fio": str(x.get("fio", ""))[:120], "fio_full": str(x.get("fio_full", ""))[:160],
                               "pos": str(x.get("pos", ""))[:300]} for d, x in v.items() if d in catalog.SIGNER_DOCS}
                       for g, v in val.items() if g in catalog.SIGNER_GROUPS}
            elif key == "access":
                ids = {x["id"] for x in st.services()}
                val = {k: [l for l in v if l in catalog.ACCESS_COLS] for k, v in val.items() if k in ids}
            elif key == "forms":
                titles = {x["t"] for x in st.services() if x.get("app")}
                val = {t: clean_tpl(v) for t, v in val.items() if t in titles}
            elif key == "extra":
                val = [{"id": str(x.get("id") or catalog.new_extra_id())[:20], "t": str(x["t"]).strip()[:200]} for x in val
                       if str(x.get("t", "")).strip().lower().startswith("заявлени")]
            elif key == "holidays":
                import re as _re
                val = sorted({d for d in (str(x).strip() for x in val) if _re.fullmatch(r"\d{4}-\d\d-\d\d", d)})
            elif key in ("limit", "keep_days", "need_days", "income_days"):
                val = int(val)
                lo, hi = {"limit": (1, 3), "keep_days": (0, 90), "need_days": (0, 10), "income_days": (0, 7)}[key]
                if not lo <= val <= hi:
                    raise ValueError(f"значение от {lo} до {hi}")
            else:
                raise ValueError("неизвестная настройка")
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            return J({"error": f"Не сохранено: {e}"}, 422)
        st.save(key, val)
        db.audit(u["person"], u["role"], "settings", key, _ip(r))
        return J({"ok": True})

    async def staff_kug(r):
        """Календарный учебный график: что загружено (GET) и загрузка нового файла .xlsx/.csv (POST, администратор)."""
        u = user(r, ("staff", "admin"))
        if r.method == "POST":
            if u["role"] != "admin":
                return J({"error": "Загружать график может администратор МФЦ"}, 403)
            _, files = await read_form(r)
            if not files:
                return J({"error": "Выберите файл графика (.xlsx или .csv)"}, 422)
            name, data = files[0]
            if len(data) > 10 * 1024 * 1024:
                return J({"error": "Файл больше 10 МБ"}, 422)
            try:
                groups, warn, n = kug.parse(data, name)
            except kug.KugError as e:
                return J({"error": str(e)}, 422)
            st.save("kug", {"file": name[:200], "at": now(), "by": u.get("name") or u["person"], "rows": n, "groups": groups})
            db.audit(u["person"], u["role"], "kug_upload", f"{name}: {n} строк, {len(groups)} групп", _ip(r))
            return J({"ok": True, "rows": n, "groups": len(groups), "warnings": warn[:50], "warn_total": len(warn)})
        k = st.all().get("kug") or {}
        return J({"file": k.get("file"), "at": k.get("at"), "by": k.get("by"), "rows": k.get("rows", 0),
                  "groups": {g: v for g, v in sorted((k.get("groups") or {}).items())}})

    async def staff_kug_template(r):
        user(r, ("staff", "admin"))
        return web.Response(body=kug.template_xlsx(), headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename*=UTF-8''KUG_shablon.xlsx"})

    async def staff_sync(r):
        user(r, ("staff", "admin"))
        sent, changed = await od.sync()
        return J({"sent": sent, "changed": changed})

    async def staff_demo(r):
        """Только демо: «1С» двигает статус заявки, ЛК подтягивает."""
        u = user(r, ("staff", "admin"))
        if not cfg.get("demo"):
            return J({"error": "Только в демо-режиме"}, 403)
        b = await r.json()
        o = od._get(int(b["id"]))
        # Local demonstration: never call or mutate the external 1C service.
        from . import catalog
        flow = catalog.FLOWS[catalog.flow_of(o)]
        target = b.get("to")
        if target not in (None, "", "need", "rej"):
            return J({"error": "Недопустимый статус"}, 400)
        if target in ("need", "rej"):
            if o["status"] not in ("need", "rej"):
                o["data"]["_demo_prev"] = o["status"]
            status = target
            comment = (b.get("comment") or ("Уточните данные заявки." if target == "need" else "Заявка отклонена.")).strip()[:1000]
        else:
            current = o["data"].get("_demo_prev", flow[0]) if o["status"] == "need" else o["status"]
            idx = flow.index(current) if current in flow else -1
            status = flow[min(idx + 1, len(flow) - 1)]
            comment = ""
        o["data"]["_demo_local"] = True
        od.db.x("UPDATE orders SET data=? WHERE id=?", (json.dumps(o["data"], ensure_ascii=False), o["id"]))
        if status != o["status"] or comment != (o.get("comment") or ""):
            od._set_status(o, status, comment)
            if status in ("need", "rej", "ready", "signed", "done"):
                od.notice(o["person"], f"Демо: {catalog.STATUS[status][0]} — {o['title']}", comment or "Статус заявки изменён в режиме симуляции.", o["id"])
        db.audit(u["person"], u["role"], "demo_status", f"№{o['id']} → {status}")
        result = od.view(od._get(o["id"]))
        return J({"ok": True, "order": result, "final": result["final"]})

    async def health(r):
        q = db.q("SELECT COUNT(*) n FROM orders WHERE sync!='sent'", one=True)["n"]
        return J({"ok": True, "onec": onec.configured, "queue": q, "demo": bool(cfg.get("demo"))})

    R = app.router
    R.add_get("/", await_page("index.html"))
    if not portal_mode:
        R.add_get("/staff", await_page("staff.html"))
        R.add_get("/enter", enter)
    else:
        R.add_get("/portal-enter", portal_enter)
        R.add_post("/portal-exchange", portal_exchange)
    R.add_get("/health", health)
    if not portal_mode:
        R.add_post("/api/login", login)
    R.add_post("/api/logout", logout)
    R.add_get("/api/me", me)
    R.add_get("/api/prikazy", prikazy)
    R.add_get("/api/orgs", orgs)
    R.add_post("/api/preview", preview)
    R.add_get("/api/orders", orders_list)
    R.add_post("/api/orders", order_create)
    R.add_get("/api/orders/{id:\\d+}", order_one)
    R.add_post("/api/orders/{id:\\d+}", order_one)
    R.add_post("/api/orders/{id:\\d+}/cancel", order_cancel)
    R.add_post("/api/orders/{id:\\d+}/review", order_review)
    R.add_get("/api/orders/{id:\\d+}/blank", order_blank)
    R.add_get("/api/orders/{id:\\d+}/document", order_doc)
    R.add_get("/api/orders/{id:\\d+}/signature", order_sig)
    R.add_get("/api/orders/{id:\\d+}/files/{f}", order_file)
    R.add_get("/api/notices", notices)
    R.add_post("/api/notices", notices)
    if not portal_mode:
        R.add_get("/api/staff/orders", staff_orders)
        R.add_get("/api/staff/reviews", staff_reviews)
        R.add_get("/api/staff/settings", staff_settings)
        R.add_post("/api/staff/settings", staff_settings)
        R.add_post("/api/staff/sync", staff_sync)
        R.add_get("/api/staff/kug", staff_kug)
        R.add_post("/api/staff/kug", staff_kug)
        R.add_get("/api/staff/kug/template", staff_kug_template)
        R.add_post("/api/staff/demo", staff_demo)
    R.add_static("/static/", STATIC, append_version=True)

    async def background(app):
        async def loop():
            n = 0
            while True:
                try:
                    if n % max(1, cfg["auth"]["import_every_min"] * 60 // cfg["onec"]["sync_every_sec"]) == 0:
                        await auth.import_accounts()
                    await od.sync()
                except Exception:  # noqa
                    log.exception("sync")
                n += 1
                await asyncio.sleep(cfg["onec"]["sync_every_sec"])
        t = asyncio.create_task(loop())
        yield
        t.cancel()
        await onec.close()
    app.cleanup_ctx.append(background)
    return app


def await_page(name):
    async def h(r):
        return web.FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})
    return h
