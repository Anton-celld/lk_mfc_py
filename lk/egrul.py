"""Организация по ИНН (ЕГРЮЛ/ЕГРИП). Рабочий вариант — DaData («Организация по ИНН»), в демо — тестовый справочник.
Название, которое попадёт в справку, берётся только отсюда — студент его не вводит руками."""
import datetime as dt
import json
import logging

import aiohttp

from .rules import inn_ok

log = logging.getLogger("lk.egrul")
DADATA = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/"

DEMO = [
    {"inn": "7700000016", "name": "ООО «Агрохолдинг Пример»", "full": "Общество с ограниченной ответственностью «Агрохолдинг Пример»",
     "address": "г. Москва, ул. Тестовая, д. 1", "status": "ACTIVE"},
    {"inn": "7700000023", "name": "АО «Демо-Банк»", "full": "Акционерное общество «Демо-Банк»", "address": "г. Москва, Образцовый пер., д. 5",
     "status": "ACTIVE"},
    {"inn": "7700000030", "name": "Военный комиссариат (демо)", "full": "Военный комиссариат Тимирязевского района г. Москвы (демо-запись)",
     "address": "г. Москва, ул. Примерная, д. 10", "status": "ACTIVE"},
    {"inn": "7700000062", "name": "ООО «Закрытая Демо-Фирма»", "full": "Общество с ограниченной ответственностью «Закрытая Демо-Фирма»",
     "address": "г. Москва", "status": "LIQUIDATED"},
    {"inn": "770000006648", "name": "ИП Примеров С.В.", "full": "Индивидуальный предприниматель Примеров Сергей Викторович",
     "address": "г. Москва", "status": "ACTIVE"},
]


def _from_dadata(s):
    d = s.get("data") or {}
    name = d.get("name") or {}
    return {"inn": d.get("inn"), "kpp": d.get("kpp"), "ogrn": d.get("ogrn"),
            "name": name.get("short_with_opf") or s.get("value"), "full": name.get("full_with_opf") or s.get("value"),
            "address": (d.get("address") or {}).get("value", ""), "status": (d.get("state") or {}).get("status", "ACTIVE")}


class Egrul:
    def __init__(self, cfg, db):
        self.c, self.db = cfg["egrul"], db

    async def _dadata(self, method, query):
        if not self.c.get("dadata_token"):
            raise RuntimeError("не указан ключ DaData (config.json → egrul.dadata_token)")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post(DADATA + method, json={"query": query, "count": 6},
                              headers={"Authorization": "Token " + self.c["dadata_token"], "Accept": "application/json"}) as r:
                r.raise_for_status()
                return [_from_dadata(x) for x in (await r.json()).get("suggestions", [])]

    async def search(self, q):
        """Подсказки по ИНН или названию (для поля поиска)."""
        q = (q or "").strip()
        if len(q) < 2:
            return []
        if self.c["provider"] == "dadata":
            try:
                return await self._dadata("findById/party" if q.isdigit() and len(q) in (10, 12) else "suggest/party", q)
            except Exception as e:  # noqa
                log.warning("dadata: %s", e)
                return []
        ql = q.lower()
        return [o for o in DEMO if (o["inn"].startswith(q) if q.isdigit() else ql in (o["name"] + " " + o["full"]).lower())][:6]

    async def by_inn(self, inn):
        """Организация по ИНН — для сохранения в заказ. None, если ИНН неверный, не найден или организация ликвидирована."""
        inn = str(inn or "").strip()
        if not inn_ok(inn):
            return None
        r = self.db.q("SELECT data, at FROM orgs WHERE inn=?", (inn,), one=True)
        if r and dt.datetime.fromisoformat(r["at"]) > dt.datetime.now() - dt.timedelta(days=self.c["cache_days"]):
            o = json.loads(r["data"])
        else:
            found = [x for x in await self.search(inn) if x.get("inn") == inn]
            if not found:
                return None
            o = found[0]
            self.db.x("INSERT OR REPLACE INTO orgs(inn, data, at) VALUES(?,?,?)",
                      (inn, json.dumps(o, ensure_ascii=False), dt.datetime.now().isoformat(timespec="seconds")))
        return None if o.get("status") in ("LIQUIDATED", "LIQUIDATING", "BANKRUPT") else o
