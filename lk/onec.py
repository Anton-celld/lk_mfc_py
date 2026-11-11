"""Клиент HTTP-сервиса 1С. Методы и формат — в ONEC_API.md; пути и статусы настраиваются в config.json → onec."""
import base64
import json
import logging
import ssl

import aiohttp

log = logging.getLogger("lk.onec")

STATUSES = {"reg", "work", "sign", "need", "ready", "signed", "done", "rej", "cancel"}
# типовые названия статусов в 1С → статусы ЛК (дополняются config.onec.status_map)
STATUS_WORDS = {"зарегистрирован": "reg", "зарегистрирована": "reg", "зарегистрировано": "reg", "регистрация": "reg", "в работе": "work", "передано исполнителю": "work",
                "на подписи": "sign", "на подписании": "sign", "передано на подпись": "sign", "требуется уточнение": "need", "требуется действие": "need",
                "готово": "ready", "готово к выдаче": "ready", "подписано": "signed", "подписана": "signed", "выдано": "done",
                "выдан": "done", "исполнено": "done", "отклонено": "rej", "отказано": "rej", "отменено": "cancel", "аннулировано": "cancel"}


class OneCError(Exception):
    """1С ответила ошибкой (неверные данные и т. п.)."""


class OneCUnavailable(OneCError):
    """1С недоступна — заказ остаётся в очереди и уйдёт позже."""


class OneCIndeterminate(OneCError):
    """Неизвестен итог создания заявки; нельзя повторять автоматически."""


def ssl_context(ca_file=""):
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa
        pass
    if ca_file:
        ctx.load_verify_locations(ca_file)
    return ctx


class OneC:
    def __init__(self, cfg):
        self.c = cfg["onec"]
        self.base = self.c["base_url"].rstrip("/") + "/"
        self._s = None

    @property
    def configured(self):
        return bool(self.c["base_url"])

    def status(self, raw):
        """Статус 1С → статус ЛК."""
        s = str(raw or "").strip()
        m = self.c.get("status_map") or {}
        if s in m:
            return m[s]
        if s in STATUSES:
            return s
        return STATUS_WORDS.get(s.lower())

    async def _session(self):
        if self._s is None or self._s.closed:
            auth = aiohttp.BasicAuth(self.c["user"], self.c["password"]) if self.c.get("user") else None
            self._s = aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=self.c["timeout"]),
                                            connector=aiohttp.TCPConnector(ssl=ssl_context(self.c.get("ca_file"))))
        return self._s

    async def close(self):
        if self._s and not self._s.closed:
            await self._s.close()

    def _url(self, key, **kw):
        return self.base + self.c["paths"][key].format(**{k: str(v) for k, v in kw.items()})

    async def _call(self, method, key, params=None, body=None, raw=False, **kw):
        if not self.configured:
            raise OneCUnavailable("адрес HTTP-сервиса 1С не указан (config.json → onec.base_url)")
        s = await self._session()
        url = self._url(key, **kw)
        try:
            async with s.request(method, url, params=params, json=body) as r:
                data = await r.read()
                if r.status >= 500:
                    raise OneCUnavailable(f"1С: ошибка {r.status}")
                if r.status == 404 and not raw:
                    return None
                if r.status >= 400:
                    try:
                        msg = json.loads(data).get("error") or data[:300].decode("utf-8", "replace")
                    except Exception:  # noqa
                        msg = data[:300].decode("utf-8", "replace")
                    raise OneCError(f"1С отклонила запрос ({r.status}): {msg}")
                if raw:
                    return data, r.headers.get("Content-Type", "application/octet-stream")
                return json.loads(data) if data else {}
        except aiohttp.ClientError as e:
            raise OneCUnavailable(f"нет связи с 1С: {e.__class__.__name__}") from e
        except TimeoutError as e:
            raise OneCUnavailable("1С не ответила вовремя") from e

    # --- вход ---
    async def auth_check(self, login, password):
        r = await self._call("POST", "auth_check", body={"login": login, "password": password})
        return r if r and r.get("ok") else None

    async def auth_token(self, token):
        r = await self._call("POST", "auth_token", body={"token": token})
        return r if r and r.get("ok") else None

    async def accounts(self, since=None):
        r = await self._call("GET", "accounts", params={"since": since} if since else None)
        return (r or {}).get("accounts", [])

    # --- данные ---
    async def person(self, pid):
        return await self._call("GET", "person", id=pid)

    async def prikazy(self, pid, program=None):
        r = await self._call("GET", "prikazy", params={"program": program} if program else None, id=pid)
        return (r or {}).get("orders", [])

    async def signers(self):
        return (await self._call("GET", "signers")) or {}

    # --- заказы ---
    async def create_request(self, payload):
        return await self._call("POST", "request_create", body=payload)

    async def update_request(self, rid, payload):
        return await self._call("PUT", "request_update", body=payload, id=rid)

    async def cancel_request(self, rid):
        return await self._call("POST", "request_cancel", body={}, id=rid)

    async def reject_request(self, rid, reason):
        """ЛК сам отклонил заказ: «Требуется действие» висело дольше срока, студент ничего не исправил."""
        return await self._call("POST", "request_reject", body={"reason": reason, "auto": True}, id=rid)

    async def requests(self, since=None):
        r = await self._call("GET", "requests_list", params={"since": since} if since else None)
        return (r or {}).get("requests", [])

    async def document(self, rid):
        return await self._call("GET", "request_document", raw=True, id=rid)

    async def signature(self, rid):
        """Отсоединённая подпись .sig к подписанному PDF из 1С."""
        return await self._call("GET", "request_signature", raw=True, id=rid)


def file_b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()


def make_onec(cfg):
    """Транспорт 1С выбирается независимо от HTTP API самих микросервисов."""
    transport = str(cfg["onec"].get("transport", "http")).lower()
    if transport == "http":
        return OneC(cfg)
    if transport == "soap":
        from .onec_soap import OneCSoap
        return OneCSoap(cfg)
    raise ValueError("onec.transport должен быть 'http' или 'soap'")
