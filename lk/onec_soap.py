"""Конфигурируемый SOAP-транспорт к 1С.

Конкретные операции/поля 1С задаются в `onec.soap.operations` по WSDL.
Транспорт не угадывает контракт. HTTP и SSO сервиса не затрагиваются.
"""
import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Mapping

from .onec import OneC, OneCError, OneCUnavailable, OneCIndeterminate, ssl_context

log = logging.getLogger("lk.onec.soap")


class OneCSoap(OneC):
    """Совместимый с OneC HTTP адаптер, реализующий тот же прикладной API."""

    def __init__(self, cfg):
        self.c = cfg["onec"]
        self.soap = self.c.get("soap") or {}
        self._client = None
        self._http_async = None
        self._http_wsdl = None
        self._lock = asyncio.Lock()
        self._limit = asyncio.Semaphore(int(self.soap.get("max_concurrent", 5)))

    @property
    def configured(self):
        return bool(self.c.get("wsdl"))

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.configured:
            raise OneCUnavailable("адрес SOAP WSDL 1С не указан (MFC_ONEC_WSDL)")
        async with self._lock:
            if self._client is None:
                # В zeep.AsyncClient загрузка WSDL синхронная. Инициализация
                # вынесена из event loop; Basic Auth работает и для WSDL, и для POST.
                try:
                    client, async_http, wsdl_http = await asyncio.to_thread(self._build_client)
                except Exception as exc:
                    raise self._translate_error(exc, "WSDL") from exc
                self._client, self._http_async, self._http_wsdl = client, async_http, wsdl_http
        return self._client

    def _build_client(self):
        try:
            import httpx
            from zeep import AsyncClient
            from zeep.transports import AsyncTransport
        except ImportError as exc:
            raise OneCError("SOAP-зависимости не установлены: pip install -r requirements-soap.txt") from exc
        timeout = float(self.c.get("timeout", 20))
        ctx = ssl_context(self.c.get("ca_file", ""))
        auth = (self.c.get("user", ""), self.c.get("password", "")) if self.c.get("user") else None
        # Не отключать TLS-проверку даже для внутреннего сервера.
        wsdl_http = httpx.Client(auth=auth, verify=ctx, timeout=timeout, trust_env=False)
        async_http = httpx.AsyncClient(auth=auth, verify=ctx, timeout=timeout, trust_env=False)
        try:
            transport = AsyncTransport(
                wsdl_client=wsdl_http, client=async_http,
                timeout=timeout, operation_timeout=timeout,
            )
            client = AsyncClient(wsdl=self.c["wsdl"], transport=transport)
            return client, async_http, wsdl_http
        except Exception:
            # Инициализация выполняется в worker-thread: закрываем оба клиента.
            wsdl_http.close()
            # httpx.AsyncClient создан, но запросы им ещё не выполнялись.
            asyncio.run(async_http.aclose())
            raise

    @staticmethod
    def _translate_error(exc, action):
        if isinstance(exc, OneCError):
            return exc
        # Не включаем URL, XML, учётные данные и SOAP-параметры в ошибки/логи.
        code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if response is not None:
            code = getattr(response, "status_code", code)
        if code in (401, 403):
            return OneCError(f"1С отказала в доступе ({code}) при операции {action}")
        if code is not None and int(code) >= 500:
            return OneCUnavailable(f"1С временно недоступна (HTTP {code})")
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return OneCUnavailable(f"Не удалось связаться с SOAP 1С при операции {action}")
        if type(exc).__module__.startswith("httpx"):
            if code is not None:
                return OneCError(f"SOAP 1С: HTTP {code} при операции {action}")
            return OneCUnavailable(f"SOAP 1С: ошибка транспорта {type(exc).__name__}")
        if type(exc).__name__ in ("TransportError",):
            return OneCUnavailable(f"SOAP 1С: ошибка транспорта при операции {action}")
        # Контракт, SOAP Fault, некорректный XML — это не сетевой сбой.
        return OneCError(f"SOAP 1С: ошибка операции {action} ({type(exc).__name__})")

    @staticmethod
    def _get_path(data, path):
        """Безопасно разрешить путь в словаре/списке, без eval и доступа к атрибутам."""
        cur = data
        for part in path.split("."):
            if isinstance(cur, Mapping):
                if part not in cur:
                    raise OneCError(f"SOAP: отсутствует поле результата {path!r}")
                cur = cur[part]
            elif isinstance(cur, (list, tuple)) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                raise OneCError(f"SOAP: отсутствует поле результата {path!r}")
        return cur

    @classmethod
    def _render(cls, value, context):
        """Аргументы из подписанного серверного вызова, НЕ из произвольных SOAP-заголовков."""
        if isinstance(value, str) and value.startswith("$"):
            return cls._get_path(context, value[1:])
        if isinstance(value, dict):
            return {k: cls._render(v, context) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._render(v, context) for v in value]
        return value

    async def _invoke(self, key, **context):
        spec = (self.soap.get("operations") or {}).get(key)
        if not isinstance(spec, dict) or not isinstance(spec.get("method"), str) or not spec["method"].strip():
            raise OneCError(f"SOAP-операция {key!r} не сопоставлена с методом WSDL")
        action = spec["method"]
        params = self._render(spec.get("args") or {}, context)
        if not isinstance(params, dict):
            raise OneCError(f"SOAP: аргументы операции {key!r} должны быть объектом")
        client = await self._ensure_client()
        try:
            method = getattr(client.service, action)
            async with self._limit:
                response = await method(**params)
            # zeep возвращает объекты CompoundValue, их нужно приводить к типам Python.
            try:
                from zeep.helpers import serialize_object
            except ImportError:
                normalized = response  # тестовый/инъецированный клиент
            else:
                normalized = serialize_object(response)
            path = spec.get("response")
            if path:
                normalized = self._get_path(normalized, path)
            if spec.get("json_response"):
                if not isinstance(normalized, str):
                    raise OneCError(f"SOAP {key}: ожидалась JSON-строка результата")
                try:
                    normalized = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    raise OneCError(f"SOAP {key}: некорректный JSON-ответ") from exc
            if "output_fields" in spec:
                if not isinstance(spec["output_fields"], dict):
                    raise OneCError(f"SOAP {key}: output_fields должен быть объектом")
                normalized = {
                    target: normalized if field == "." else self._get_path(normalized, field)
                    for target, field in spec["output_fields"].items()
                }
            return normalized, spec
        except OneCError:
            raise
        except Exception as exc:
            log.warning("Ошибка SOAP %s: %s", key, type(exc).__name__)
            raise self._translate_error(exc, action) from exc

    @staticmethod
    def _as_dict(value, key):
        if not isinstance(value, Mapping):
            raise OneCError(f"SOAP {key}: ожидался объект с полями результата")
        return dict(value)

    @staticmethod
    def _as_list(value, key, field):
        if isinstance(value, Mapping):
            value = value.get(field)
        if value is None:
            return []
        if not isinstance(value, list):
            raise OneCError(f"SOAP {key}: ожидался список")
        return value

    async def auth_check(self, login, password):
        value, _ = await self._invoke("auth_check", login=login, password=password)
        data = self._as_dict(value, "auth_check")
        return data if data.get("ok") else None

    async def auth_token(self, token):
        value, _ = await self._invoke("auth_token", token=token)
        data = self._as_dict(value, "auth_token")
        return data if data.get("ok") else None

    async def accounts(self, since=None):
        value, _ = await self._invoke("accounts", since=since)
        return self._as_list(value, "accounts", "accounts")

    async def person(self, pid):
        value, _ = await self._invoke("person", id=pid)
        return self._as_dict(value, "person") if value is not None else None

    async def prikazy(self, pid, program=None):
        value, _ = await self._invoke("prikazy", id=pid, program=program)
        return self._as_list(value, "prikazy", "orders")

    async def signers(self):
        value, _ = await self._invoke("signers")
        return self._as_dict(value, "signers") if value is not None else {}

    async def create_request(self, payload):
        try:
            value, _ = await self._invoke("request_create", payload=payload)
        except OneCUnavailable as exc:
            # Тайм-аут мог произойти ПОСЛЕ создания заявки в 1С.
            # Не отправляем повторно из фоновой очереди до сверки с 1С.
            raise OneCIndeterminate("Результат создания заявки в 1С неизвестен: нужна сверка") from exc
        data = self._as_dict(value, "request_create")
        if not data.get("id"):
            raise OneCIndeterminate("Создание заявки не вернуло ID: нужна сверка с 1С")
        return data

    async def update_request(self, rid, payload):
        value, _ = await self._invoke("request_update", id=rid, payload=payload)
        return value

    async def cancel_request(self, rid):
        value, _ = await self._invoke("request_cancel", id=rid)
        return value

    async def reject_request(self, rid, reason):
        value, _ = await self._invoke("request_reject", id=rid, reason=reason)
        return value

    async def requests(self, since=None):
        value, _ = await self._invoke("requests_list", since=since)
        return self._as_list(value, "requests_list", "requests")

    @staticmethod
    def _binary(value, spec, key, default_mime):
        encoding = spec.get("encoding", "bytes")
        if encoding == "base64":
            if not isinstance(value, str):
                raise OneCError(f"SOAP {key}: ожидалась base64-строка")
            try:
                value = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise OneCError(f"SOAP {key}: повреждённый base64") from exc
        if not isinstance(value, bytes):
            raise OneCError(f"SOAP {key}: ожидаются bytes; настройте response/encoding по WSDL")
        return value, spec.get("content_type", default_mime)

    async def document(self, rid):
        value, spec = await self._invoke("request_document", id=rid)
        return self._binary(value, spec, "request_document", "application/pdf")

    async def signature(self, rid):
        value, spec = await self._invoke("request_signature", id=rid)
        return self._binary(value, spec, "request_signature", "application/octet-stream")

    async def close(self):
        async with self._lock:
            if self._http_async is not None:
                await self._http_async.aclose()
            if self._http_wsdl is not None:
                # Синхронный HTTP-клиент (WSDL) закрываем вне event loop.
                await asyncio.to_thread(self._http_wsdl.close)
            self._client = self._http_async = self._http_wsdl = None
