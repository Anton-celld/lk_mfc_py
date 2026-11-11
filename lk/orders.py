"""Заказы ЛК: создание, изменение, отмена, отзывы; очередь отправки в 1С и подтяжка статусов из 1С.

Путь заказа: студент отправляет → заказ сохраняется у нас (sync=queued) → сразу уходит в 1С; если 1С недоступна,
уйдёт при следующей синхронизации → статусы меняет 1С, ЛК их подтягивает и уведомляет студента.
"""
import datetime as dt
import json
import logging
import shutil
from pathlib import Path

from . import catalog, dates, kug, paper, rules
from .db import now
from .onec import OneCError, OneCUnavailable, OneCIndeterminate, file_b64

log = logging.getLogger("lk.orders")
from .catalog import FLOWS, STATUS, can_review, flow_of, is_final  # noqa: E402

STATUS_TEXT = {k: v[0] for k, v in STATUS.items()}
NOTICE = {"work": "Передано исполнителю", "sign": "Передано на подпись", "need": "Требуется действие",
          "ready": "Выполнено — можно забирать", "signed": "Подписано — можно скачать", "done": "Выдано", "rej": "Заявка отклонена"}


class Invalid(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


class Denied(Exception):
    pass


def row_to_order(r):
    o = dict(r)
    o["data"], o["hist"] = json.loads(o["data"]), json.loads(o["hist"])
    return o


class Orders:
    def __init__(self, cfg, db, settings, onec, egrul):
        self.cfg, self.db, self.st, self.onec, self.egrul = cfg, db, settings, onec, egrul
        self.up_dir = Path(cfg["data_dir"]) / "uploads"

    # ---------------- данные из 1С (с запасной копией) ----------------
    async def person(self, pid):
        r = self.db.q("SELECT data, at FROM people WHERE person=?", (pid,), one=True)
        fresh = r and dt.datetime.fromisoformat(r["at"]) > dt.datetime.now() - dt.timedelta(minutes=10)
        if fresh:
            return json.loads(r["data"])
        try:
            p = await self.onec.person(pid)
            if p:
                self.db.x("INSERT OR REPLACE INTO people(person, data, at) VALUES(?,?,?)", (pid, json.dumps(p, ensure_ascii=False), now()))
                return p
        except OneCError as e:
            log.warning("person %s: %s", pid, e)
        if r:
            return json.loads(r["data"])
        raise OneCUnavailable("нет данных о студенте: 1С недоступна")

    async def program(self, pid, prog_id):
        p = await self.person(pid)
        prog = next((x for x in self.programs(p) if str(x["id"]) == str(prog_id)), None)
        if not prog:
            raise Denied("Программа обучения не найдена")
        return p, prog

    def programs(self, person, s=None):
        """Программы студента + периоды справки-вызова: из 1С (учебный план), иначе из загруженного КУГ по группе."""
        groups = ((s or self.st.all()).get("kug") or {}).get("groups") or {}
        out = []
        for x in person.get("programs", []):
            x = dict(x)
            if x.get("calls"):
                x["calls_src"] = "1c"
            elif groups.get(kug.norm_group(x.get("group"))):
                x["calls"], x["calls_src"] = groups[kug.norm_group(x.get("group"))], "kug"
            x["calls"] = [dict(c, reason=catalog.canon_call_reason(c.get("reason"))) for c in x.get("calls") or []]
            out.append(x)
        return out

    async def prikazy(self, pid, prog_id):
        try:
            return await self.onec.prikazy(pid, prog_id)
        except OneCError as e:
            log.warning("prikazy: %s", e)
            return []

    # ---------------- общее ----------------
    def _get(self, oid, person=None):
        r = self.db.q("SELECT * FROM orders WHERE id=?" + (" AND person=?" if person else ""),
                      (oid, person) if person else (oid,), one=True)
        if not r:
            raise Denied("Заявка не найдена")
        return row_to_order(r)

    def final_at(self, o):
        if not is_final(o):
            return None
        return next((h["at"] for h in reversed(o["hist"]) if h["s"] == o["status"]), o["updated"])[:10]

    def view(self, o, s=None):
        """Заказ для интерфейса: + признак архива и сколько дней до него."""
        s = s or self.st.all()
        o = dict(o)
        f = self.final_at(o)
        days = (dt.date.today() - dt.date.fromisoformat(f)).days if f else None
        o["archived"] = f is not None and days >= s["keep_days"]
        o["to_archive"] = max(0, s["keep_days"] - days) if f else None
        o["flow"] = flow_of(o)
        o["status_text"] = STATUS_TEXT.get(o["status"], o["status"])
        if o["flow"] == "app" and o["status"] in ("work", "sign"):
            o["status_text"] = "Передано в дирекцию"
        elif o["flow"] == "ecp" and o["status"] == "sign":
            o["status_text"] = "Передано на подпись"
        o["final"], o["can_review"] = is_final(o), can_review(o)
        until = self.need_until(o, s)
        o["need_until"] = until.isoformat() if until else None
        o["data"] = {k: v for k, v in o["data"].items() if not k.startswith("_")}
        return o

    # ---------------- «Требуется действие»: срок на исправление ----------------
    def need_until(self, o, s=None):
        """Последний день, когда студент ещё может исправить заказ: need_days рабочих дней после дня, когда МФЦ
        поставил «Требуется действие» (сам этот день не считается). None — срока нет."""
        s = s or self.st.all()
        n = int(s.get("need_days") or 0)
        if o["status"] != "need" or n <= 0:
            return None
        since = next((h["at"] for h in reversed(o["hist"]) if h["s"] == "need"), o["updated"])
        return dates.workday_after(dt.date.fromisoformat(since[:10]), n, set(s.get("holidays") or []))

    async def _auto_reject(self, o, s):
        if o["data"].get("_demo_local"):
            return
        until, n = self.need_until(o, s), int(s["need_days"])
        reason = (f"Заявка отклонена автоматически: в течение {n} рабочих {'дня' if n in (2, 3, 4) else 'дней'} "
                  f"(до {dates.fmt(until)} включительно) данные не были исправлены. При необходимости подайте заявку заново.")
        self._set_status(o, "rej", reason)
        o["data"]["_pending"] = "reject" if o["onec_id"] else None
        o["data"]["_auto_rej"] = 1
        self.db.x("UPDATE orders SET data=?, sync='queued' WHERE id=?", (json.dumps(o["data"], ensure_ascii=False), o["id"]))
        self.notice(o["person"], f"{NOTICE['rej']}: {o['title']}", reason, o["id"])
        self.db.audit(None, "system", "order_auto_reject", f"№{o['id']}")
        await self.push(self._get(o["id"]))

    async def auto_reject(self, today=None):
        """Отклонить заказы, которые висят в «Требуется действие» дольше срока. Вызывается при каждой синхронизации."""
        s, today, n = self.st.all(), today or dt.date.today(), 0
        for r in self.db.q("SELECT * FROM orders WHERE status='need'"):
            o = row_to_order(r)
            until = self.need_until(o, s)
            if until and today > until:
                await self._auto_reject(o, s)
                n += 1
        return n

    def notice(self, person, title, body, oid):
        self.db.x("INSERT INTO notices(person, title, body, oid, at) VALUES(?,?,?,?,?)", (person, title, body, oid, now()))

    def _set_status(self, o, status, comment=None):
        o["hist"].append({"s": status, "at": now()})
        self.db.x("UPDATE orders SET status=?, hist=?, comment=COALESCE(?, comment), updated=? WHERE id=?",
                  (status, json.dumps(o["hist"]), comment, now(), o["id"]))
        o["status"] = status

    # ---------------- проверка и печатная форма ----------------
    async def _prepare(self, user, p, exclude=None):
        s = self.st.all()
        person, prog = await self.program(user["person"], p.get("program"))
        svc = next((x for x in self.st.for_student(person, prog, s) if x["id"] == str(p.get("svc"))), None)
        if not svc:
            raise Denied("Эта услуга недоступна для вашей программы обучения")
        foreign = catalog.is_foreign(person)
        prikazy = await self.prikazy(user["person"], prog["id"]) if svc["id"] == "extract" else ()
        tpl = self.st.tpl(svc["t"], s) if svc.get("app") else None
        org = await self.egrul.by_inn(p.get("inn")) if p.get("inn") else None
        data, title, err = rules.validate(svc, p, settings=s, tpl=tpl, prikazy=prikazy, org=org, prog=prog, foreign=foreign)
        # справку-вызов по одному и тому же основанию можно заказывать несколько раз (решение МФЦ, 28.10.2026)
        doc = "call" if svc["id"] == "call" else data.get("kind") if svc["id"] == "study" else None
        if doc:
            data["signer"] = dict(self.st.signer(prog["level"], s, doc), level=prog["level"], doc=doc)
        return s, person, prog, svc, tpl, data, title, err

    def call_duplicate(self, person, prog_id, data, exclude=None):
        """Справку-вызов по тому же основанию второй раз не заказывают (отменённые и отклонённые не в счёт):
        из графика — тот же период графика; вручную — то же основание на пересекающиеся даты."""
        for r in self.db.q("SELECT * FROM orders WHERE person=? AND program=? AND svc='call' AND status NOT IN ('cancel','rej')",
                           (person, prog_id)):
            o = row_to_order(r)
            d = o["data"]
            if o["id"] == exclude:
                continue
            if data.get("call") and d.get("call") == data["call"]:
                return o
            if d.get("reason") == data["reason"] and d.get("from") and data["from"] and \
                    d["from"] <= data["to"] and data["from"] <= d["to"]:
                return o
        return None

    def render(self, o, person, prog, tpl=None, signed=False):
        d = o["data"]
        if d.get("app"):
            return paper.application(tpl or self.st.tpl(o["title"]), person, prog, d, o["created"])
        signer = d.get("signer") or self.st.signer(prog["level"], doc="call" if o.get("svc") == "call" else d.get("kind") or "ecp")
        number = o.get("onec_number") or o["id"]
        if o.get("svc") == "call":
            return paper.call(person, prog, d, signer, number, o["created"])
        return paper.study(person, prog, d, signer, number, o["created"], signed)

    async def preview(self, user, p):
        """Печатная форма до отправки: справка с ЭЦП (обязательно показать перед подтверждением) и бланк заявления."""
        s, person, prog, svc, tpl, data, title, err = await self._prepare(user, p)
        if svc.get("app"):
            err = {}  # бланк можно скачать и с пустыми полями — заполнят от руки
        if err:
            raise Invalid(err)
        if not (svc.get("app") or data.get("ecp") or svc["id"] == "call"):
            raise Denied("Предпросмотр есть у справки с ЭЦП, справки-вызова и у заявлений")
        o = {"id": "_____", "svc": svc["id"], "title": title, "data": data, "created": now()}  # номер присвоит 1С
        return self.render(o, person, prog, tpl)

    # ---------------- студент ----------------
    def _check_files(self, files, keep=()):
        """Ограничения на загрузку — чтобы сканы не съели диск: число файлов, размер каждого, сумма на заказ
        и свободное место на сервере."""
        lim = self.cfg["uploads"]
        n = len(files) + len(keep)
        if n > lim["max_files"]:
            raise Invalid({"files": f"Не больше {lim['max_files']} файлов в одной заявке (сейчас {n})"})
        total = sum(f.get("size", 0) for f in keep)
        for name, data in files:
            if not data.startswith(b"%PDF"):
                raise Invalid({"files": f"Можно прикладывать только PDF: «{name}» — не PDF"})
            if len(data) > lim["max_mb"] * 1024 * 1024:
                raise Invalid({"files": f"Файл «{name}» больше {lim['max_mb']} МБ. Отсканируйте в меньшем разрешении "
                                        "(150–200 dpi, чёрно-белый) или сожмите PDF"})
            total += len(data)
        if total > lim["max_total_mb"] * 1024 * 1024:
            raise Invalid({"files": f"Все файлы заявки вместе — не больше {lim['max_total_mb']} МБ "
                                    f"(сейчас {total / 1048576:.1f} МБ). Сожмите сканы"})
        self.up_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.up_dir).free
        if free - total < lim["min_free_mb"] * 1024 * 1024:
            log.error("uploads: мало места на диске (%d МБ свободно)", free // 1048576)
            raise Invalid({"files": "Сейчас не получается принять файлы: на сервере заканчивается место. "
                                    "Попробуйте позже или обратитесь в МФЦ"})

    def _save_files(self, oid, files, keep=()):
        d = self.up_dir / str(oid)
        d.mkdir(parents=True, exist_ok=True)
        out = [f for f in keep if (d / f["stored"]).exists()]
        for i, (name, data) in enumerate(files):
            stored = f"{len(out) + i + 1:02d}.pdf"
            (d / stored).write_bytes(data)
            out.append({"name": Path(name).name[:120], "stored": stored, "size": len(data)})
        return out

    async def create(self, user, p, files=()):
        s, person, prog, svc, tpl, data, title, err = await self._prepare(user, p)
        if svc.get("app"):
            if files:
                self._check_files(files)
            elif not err:
                err["files"] = "Приложите подписанное заявление (PDF)"
        if err:
            raise Invalid(err)
        if svc.get("app"):
            data["_tpl"] = tpl  # шаблон на момент подачи — чтобы бланк не менялся задним числом
        status = "sign" if data.get("ecp") else "reg"
        t = now()
        oid = self.db.x("INSERT INTO orders(person, program, svc, title, data, status, hist, sync, created, updated) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (user["person"], str(prog["id"]), svc["id"], title, json.dumps(data, ensure_ascii=False), status,
                         json.dumps([{"s": status, "at": t}]), "queued", t, t))
        if files:
            data["files"] = self._save_files(oid, files)
            self.db.x("UPDATE orders SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), oid))
        self.db.audit(user["person"], user["role"], "order_create", f"№{oid} {title}")
        o = self._get(oid)
        await self.push(o)
        return self.view(self._get(oid), s)

    async def update(self, user, oid, p, files=(), keep_files=None):
        o = self._get(oid, user["person"])
        if o["status"] not in ("reg", "need"):
            raise Denied("Изменить можно только заявку в статусе «Зарегистрировано» или «Требуется действие»")
        until = self.need_until(o)
        if until and dt.date.today() > until:
            await self._auto_reject(o, self.st.all())
            raise Denied(f"Срок исправления истёк {dates.fmt(until)} — заявка отклонена автоматически. Подайте её заново.")
        p = dict(p, svc=o["svc"], program=o["program"])
        s, person, prog, svc, tpl, data, title, err = await self._prepare(user, p, exclude=oid)
        old = o["data"].get("files", [])
        keep = [f for f in old if keep_files is None or f["stored"] in keep_files]
        if svc.get("app"):
            if files:
                self._check_files(files, keep)
            if not files and not keep and not err:
                err["files"] = "Приложите подписанное заявление (PDF)"
            data["_tpl"] = o["data"].get("_tpl") or tpl
        if err:
            raise Invalid(err)
        if svc.get("app"):
            data["files"] = self._save_files(oid, files, keep)
        data["_pending"] = "update" if o["onec_id"] else None
        self.db.x("UPDATE orders SET title=?, data=?, sync='queued', updated=? WHERE id=?",
                  (title, json.dumps(data, ensure_ascii=False), now(), oid))
        o = self._get(oid)
        if o["status"] == "need":                     # исправили — заказ возвращается туда, где был до «Требуется действие»
            prev = next((h["s"] for h in reversed(o["hist"]) if h["s"] != "need"), None)
            self._set_status(o, prev or FLOWS[flow_of(o)][0])
        self.db.audit(user["person"], user["role"], "order_edit", f"№{oid}")
        await self.push(o)
        return self.view(self._get(oid), s)

    async def cancel(self, user, oid):
        o = self._get(oid, user["person"])
        if o["status"] != "reg":
            raise Denied("Отменить можно только заявку в статусе «Зарегистрировано»")
        self._set_status(o, "cancel")
        o["data"]["_pending"] = "cancel" if o["onec_id"] else None
        self.db.x("UPDATE orders SET data=?, sync='queued' WHERE id=?", (json.dumps(o["data"], ensure_ascii=False), oid))
        self.db.audit(user["person"], user["role"], "order_cancel", f"№{oid}")
        await self.push(self._get(oid))
        return self.view(self._get(oid))

    def review(self, user, oid, rating, text):
        o = self._get(oid, user["person"])
        if not can_review(o):
            raise Denied("Отзыв можно оставить, когда документ выдан, подписан или заявление передано исполнителю")
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = 0
        if not 1 <= rating <= 5:
            raise Invalid({"rating": "Поставьте оценку от 1 до 5 звёзд"})
        self.db.x("UPDATE orders SET rating=?, review=?, review_at=? WHERE id=?", (rating, str(text or "").strip()[:1000], now(), oid))
        return self.view(self._get(oid))

    def list(self, person, program=None):
        """Заказы студента. С program — только по этой программе (зачётной книжке): программы ведутся раздельно."""
        s = self.st.all()
        if program:
            rows = self.db.q("SELECT * FROM orders WHERE person=? AND program=? ORDER BY id DESC", (person, str(program)))
        else:
            rows = self.db.q("SELECT * FROM orders WHERE person=? ORDER BY id DESC", (person,))
        return [self.view(row_to_order(r), s) for r in rows]

    def get(self, user, oid):
        return self.view(self._get(oid, user["person"]))

    async def blank(self, user, oid):
        o = self._get(oid, user["person"])
        person, prog = await self.program(user["person"], o["program"])
        return self.render(o, person, prog, o["data"].get("_tpl"), signed=o["status"] in ("signed", "done"))

    async def document(self, user, oid):
        o = self._get(oid, user["person"])
        if not (o["data"].get("ecp") and o["status"] in ("signed", "done") and o["onec_id"]):
            raise Denied("Справка ещё не подписана")
        return await self.onec.document(o["onec_id"])

    async def signature(self, user, oid):
        o = self._get(oid, user["person"])
        if not (o["data"].get("ecp") and o["status"] in ("signed", "done") and o["onec_id"]):
            raise Denied("Файл подписи будет доступен после подписания справки")
        return await self.onec.signature(o["onec_id"])

    def file_path(self, user, oid, stored):
        o = self._get(oid, None if user["role"] in ("staff", "admin") else user["person"])
        f = next((x for x in o["data"].get("files", []) if x["stored"] == stored), None)
        if not f:
            raise Denied("Файл не найден")
        return self.up_dir / str(oid) / f["stored"], f["name"]

    # ---------------- обмен с 1С ----------------
    async def payload(self, o):
        person, prog = await self.program(o["person"], o["program"])
        d = o["data"]
        out = {"lk_id": o["id"], "person": o["person"], "program": o["program"], "service": o["svc"], "title": o["title"],
               "created": o["created"], "contacts": {"email": d.get("email"), "phone": d.get("phone")}}
        for k in ("kind", "qty", "org", "place", "boss", "reason", "call", "signer", "foreign"):
            if d.get(k) is not None:
                out[k] = d[k]
        out["flow"] = flow_of(o)
        if o["svc"] in ("call", "income", "income2"):
            out["period"] = {"from": d.get("from"), "to": d.get("to")}
        if o["svc"] == "call":
            out["call_article"] = catalog.CALL_ARTICLE.get(prog.get("level"), "173")
            out["call_purpose"] = catalog.call_purpose(d.get("reason"))
        if d.get("ord"):
            out["prikaz"] = d["ord"]
        if d.get("app"):
            out["fields"] = d.get("fields", {})
            out["files"] = [{"name": f["name"], "content_base64": file_b64(self.up_dir / str(o["id"]) / f["stored"])}
                            for f in d.get("files", [])]
        if d.get("app") or d.get("ecp") or o["svc"] == "call":
            out["print_html"] = self.render(o, person, prog, d.get("_tpl"))
        return out

    async def push(self, o):
        """Отправить заказ (или его изменение/отмену) в 1С. Ошибка связи — не беда: уйдёт при синхронизации."""
        try:
            act = o["data"].get("_pending")
            if not o["onec_id"]:
                if o["status"] == "cancel":
                    self.db.x("UPDATE orders SET sync='sent' WHERE id=?", (o["id"],))
                    return True
                r = await self.onec.create_request(await self.payload(o))
                self.db.x("UPDATE orders SET onec_id=?, onec_number=?, sync='sent', sync_error='' WHERE id=?",
                          (str(r["id"]), str(r.get("number") or ""), o["id"]))
                st = self.onec.status(r.get("status"))
                # Preserve the initial status assigned by the LK at submission.
                # Subsequent changes are received from the 1C polling endpoint.
                if st and st != o["status"]:
                    log.info("1C create returned %s for order %s; retaining initial %s", st, o["id"], o["status"])
            elif act == "update":
                await self.onec.update_request(o["onec_id"], await self.payload(o))
            elif act == "cancel":
                await self.onec.cancel_request(o["onec_id"])
            elif act == "reject":
                await self.onec.reject_request(o["onec_id"], o["comment"])
            if act:
                o["data"]["_pending"] = None
                self.db.x("UPDATE orders SET data=? WHERE id=?", (json.dumps(o["data"], ensure_ascii=False), o["id"]))
            self.db.x("UPDATE orders SET sync='sent', sync_error='' WHERE id=?", (o["id"],))
            return True
        except OneCIndeterminate as e:
            # Не повторять автоматически: 1С могла уже создать заявку.
            log.error("Требуется сверка заявки %s с 1С перед повтором", o["id"])
            self.db.x("UPDATE orders SET sync='error', sync_error=? WHERE id=?", (str(e)[:300], o["id"]))
        except OneCUnavailable as e:
            self.db.x("UPDATE orders SET sync='queued', sync_error=? WHERE id=?", (str(e)[:300], o["id"]))
        except OneCError as e:
            log.warning("push %s: %s", o["id"], e)
            self.db.x("UPDATE orders SET sync='error', sync_error=? WHERE id=?", (str(e)[:300], o["id"]))
        return False

    async def sync(self):
        """Отправить очередь и подтянуть статусы из 1С. Вызывается по расписанию."""
        await self.auto_reject()
        sent = 0
        for r in self.db.q("SELECT * FROM orders WHERE sync='queued' ORDER BY id LIMIT 50"):
            sent += await self.push(row_to_order(r))
        changed = 0
        try:
            since = self.db.setting("requests_since")
            items = await self.onec.requests(since)
        except OneCError as e:
            log.warning("pull: %s", e)
            return sent, 0
        last = since or ""
        for it in items:
            r = self.db.q("SELECT * FROM orders WHERE onec_id=?", (str(it["id"]),), one=True)
            last = max(last, str(it.get("updated") or ""))
            if not r:
                continue
            o = row_to_order(r)
            if o["data"].get("_demo_local"):
                continue  # local simulation must not be overwritten by 1C
            st = self.onec.status(it.get("status"))
            if not st or st == o["status"]:
                continue
            if o["data"].get("_auto_rej") and st == "need":
                continue   # ЛК уже отклонил по сроку — старое «Требуется действие» из 1С не возвращаем
            self._set_status(o, st, it.get("comment"))
            if it.get("number"):
                self.db.x("UPDATE orders SET onec_number=? WHERE id=?", (str(it["number"]), o["id"]))
            if st in NOTICE:
                flow = catalog.flow_of(o)
                directorate_app = flow == "app" and st in ("work", "sign")
                title = ("Передано в дирекцию" if directorate_app else
                         "Передано на подпись" if flow == "ecp" and st == "sign" else NOTICE[st])
                body = it.get("comment") if st in ("need", "rej") and it.get("comment") else {
                    "ready": "Заберите в МФЦ, при себе паспорт или студенческий билет.",
                    "signed": "Скачайте справку и файл электронной подписи (.sig) в карточке заявки.",
                    "done": "Оцените, пожалуйста, работу МФЦ."}.get(st, "")
                if directorate_app:
                    body = "Заявление направлено в дирекцию института для дальнейшего рассмотрения."
                elif flow == "ecp" and st == "sign":
                    body = "Справка подписывается усиленной квалифицированной электронной подписью; бумажная печать не требуется."
                self.notice(o["person"], f"{title}: {o['title']}", body, o["id"])
            changed += 1
        if last:
            self.db.set_setting("requests_since", last)
        return sent, changed

    # ---------------- сотрудник ----------------
    def staff_list(self, status=None, svc=None, q=None, limit=300):
        sql, a = "SELECT * FROM orders WHERE 1=1", []
        if status:
            sql += " AND status=?"
            a.append(status)
        if svc:
            sql += " AND svc=?"
            a.append(svc)
        if q:
            sql += " AND (title LIKE ? OR CAST(id AS TEXT)=? OR onec_number=?)"
            a += [f"%{q}%", q, q]
        s = self.st.all()
        return [self.view(row_to_order(r), s) for r in self.db.q(sql + " ORDER BY id DESC LIMIT ?", (*a, limit))]

    def reviews(self, limit=300):
        rows = self.db.q("SELECT id, title, svc, rating, review, review_at FROM orders WHERE rating IS NOT NULL "
                         "ORDER BY review_at DESC LIMIT ?", (limit,))
        items = [dict(r) for r in rows]
        avg = round(sum(x["rating"] for x in items) / len(items), 2) if items else None
        return {"items": items, "avg": avg, "n": len(items)}

    def purge_uploads(self, oid):
        shutil.rmtree(self.up_dir / str(oid), ignore_errors=True)
