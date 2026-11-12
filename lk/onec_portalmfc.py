"""Адаптация опубликованного контракта 1С PortalMfc для внутреннего API ЛК.

Основание: portal-mfc-api.md, версия документа от разработчиков 1С.
Точный WSDL и XSD должны быть проверены на тестовой базе: они не входят в репозиторий.
"""
import base64
import binascii
import datetime as dt
import re
import uuid
from collections.abc import Mapping

from .onec import OneCError
from .onec_soap import OneCSoap


# Подтверждённые имена SOAP-операций и параметров. Форму XDTO-ответов
# уточняют интеграционные тесты по фактическому WSDL.
PORTAL_OPERATIONS = {
    "person": {"method": "GetMfcPerson", "args": {"PersonId": "$id"}},
    "prikazy": {"method": "GetMfcOrders", "args": {"PersonId": "$id", "Program": "$program"}},
    "request_create": {"method": "CreateMfcRequest", "args": {"Request": "$payload"}},
    "request_update": {"method": "UpdateMfcRequest", "args": {"Id": "$id", "Request": "$payload"}},
    "request_cancel": {"method": "CancelMfcRequest", "args": {"Id": "$id"}},
    "request_reject": {"method": "RejectMfcRequest", "args": {"Id": "$id", "Reason": "$reason", "Auto": "$auto"}},
    "requests_list": {"method": "GetMfcRequests", "args": {"Since": "$since"}},
    "request_document": {"method": "GetMfcDocument", "args": {"Id": "$id"}},
    "request_signature": {"method": "GetMfcSignature", "args": {"Id": "$id"}},
}


class PortalMfc(OneCSoap):
    """Контрактный адаптер PortalMfc, не меняющий HTTP-транспорт OneC."""

    def __init__(self, cfg):
        super().__init__(cfg)
        # Операции штатного PortalMfc фиксированы. Настройки позволяют уточнить
        # только XDTO-обёртки/параметры после получения настоящего WSDL.
        self.soap = dict(self.soap)
        self.soap["operations"] = {**PORTAL_OPERATIONS, **self.soap.get("operations", {})}
        self.type_uids = self.soap.get("type_uids") or {}

    @staticmethod
    def _date(value):
        return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value

    @staticmethod
    def _items(value, singular):
        """Zeep и XDTO могут вернуть список напрямую либо через элемент-обёртку."""
        if value is None:
            return []
        if isinstance(value, Mapping) and singular in value:
            value = value[singular]
        if isinstance(value, Mapping) and "item" in value:
            value = value["item"]
        if isinstance(value, list):
            return value
        if isinstance(value, Mapping):
            return [value]
        raise OneCError(f"PortalMfc: неверный формат списка {singular}")

    @staticmethod
    def _validate_errors(value, operation):
        if not isinstance(value, Mapping):
            raise OneCError(f"PortalMfc {operation}: ожидался ответ с Error")
        # Некоторые биндинги заворачивают общую структуру в OperationResult.
        result_key = operation + "Result"
        if result_key in value and isinstance(value[result_key], Mapping):
            value = value[result_key]
        elif "Result" in value and isinstance(value["Result"], Mapping) and "Error" in value["Result"]:
            value = value["Result"]
        if "Error" not in value:
            raise OneCError(f"PortalMfc {operation}: в ответе нет поля Error; проверьте WSDL/XDTO")
        errors = PortalMfc._items(value.get("Error"), "Error")
        if errors:
            item = errors[0]
            if not isinstance(item, Mapping):
                raise OneCError(f"PortalMfc {operation}: некорректное поле Error")
            code = str(item.get("Code", "?"))[:12]
            # Сообщение ошибки 1С, не исходные XML и SOAP-параметры.
            reason = str(item.get("Description", "Ошибка 1С"))[:240]
            raise OneCError(f"PortalMfc {operation}: ошибка {code}: {reason}")
        return value

    async def _portal(self, key, **context):
        value, spec = await self._invoke(key, **context)
        return self._validate_errors(value, spec["method"])

    @staticmethod
    def _field(value, *names):
        for name in names:
            if name in value:
                return value[name]
        return None

    @staticmethod
    def _collection(value, *names, item):
        for name in names:
            if name in value:
                return PortalMfc._items(value[name], item)
        raise OneCError(f"PortalMfc: ожидается поле {'/'.join(names)}")

    @classmethod
    def _request(cls, raw):
        if not isinstance(raw, Mapping):
            raise OneCError("PortalMfc: ответ Request должен содержать поля")
        if not raw.get("Id"):
            raise OneCError("PortalMfc: ответ Request не содержит Id")
        return {
            "id": str(raw["Id"]), "number": str(raw.get("Number") or ""),
            "status": str(raw.get("Status") or ""),
            "comment": str(raw.get("Comment") or ""),
            "updated": cls._date(raw.get("Updated")) or "",
        }

    @staticmethod
    def _mapped_fields(raw, names):
        return {target: PortalMfc._date(raw[src]) for src, target in names.items() if src in raw and raw[src] is not None}

    @classmethod
    def _person(cls, raw):
        if not isinstance(raw, Mapping):
            raise OneCError("PortalMfc: ответ Person должен содержать поля")
        fields = {
            "Id": "id", "Fio": "fio", "Birth": "birth", "Gender": "gender",
            "Citizenship": "citizenship", "Foreign": "foreign", "Email": "email", "Phone": "phone",
        }
        result = cls._mapped_fields(raw, fields)
        if not result.get("id"):
            raise OneCError("PortalMfc: в Person отсутствует Id")
        programs = []
        pfields = {
            "Id": "id", "Book": "book", "Level": "level", "Code": "code", "Name": "name",
            "Profile": "profile", "Form": "form", "Basis": "basis", "Course": "course",
            "Group": "group", "Inst": "inst", "Start": "start", "End": "end",
        }
        for program in cls._items(raw.get("Programs"), "Program"):
            if not isinstance(program, Mapping) or not program.get("Id"):
                raise OneCError("PortalMfc: неверная программа/код зачётки")
            p = cls._mapped_fields(program, pfields)
            if isinstance(program.get("Enroll"), Mapping):
                p["enroll"] = cls._mapped_fields(program["Enroll"], {"N": "n", "Date": "date"})
            programs.append(p)
        result["programs"] = programs
        return result

    def _type_uid(self, payload):
        service = str(payload.get("service") or "")
        kind = str(payload.get("kind") or "")
        key = f"{service}:{kind}" if kind else service
        uid = self.type_uids.get(key) or (self.type_uids.get(service) if not kind else None)
        if not uid:
            raise OneCError(f"PortalMfc: не настроен TypeUID для услуги {key!r}")
        try:
            return str(uuid.UUID(str(uid)))
        except ValueError as exc:
            raise OneCError(f"PortalMfc: неверный UUID TypeUID для услуги {key!r}") from exc

    @staticmethod
    def _object(value, fields):
        if not isinstance(value, Mapping):
            return None
        data = {target: value[key] for key, target in fields.items() if value.get(key) is not None}
        return data or None

    def _create_payload(self, payload):
        """Перевести внутреннюю заявку ЛК в реальные поля XDTO Request."""
        if not isinstance(payload, Mapping):
            raise OneCError("PortalMfc: заявка должна быть объектом")
        for key in ("lk_id", "person", "program", "service"):
            if payload.get(key) in (None, ""):
                raise OneCError(f"PortalMfc: обязательное поле заявки {key} отсутствует")
        try:
            lk_id = int(payload["lk_id"])
        except (TypeError, ValueError) as exc:
            raise OneCError("PortalMfc: некорректный LkId") from exc
        if lk_id <= 0:
            raise OneCError("PortalMfc: LkId должен быть положительным")
        result = {
            "LkId": lk_id, "PersonId": str(payload["person"]),
            "Program": str(payload["program"]), "Service": str(payload["service"]),
            "TypeUID": self._type_uid(payload),
        }
        fields = {
            "kind": "Kind", "qty": "Qty", "place": "Place", "reason": "Reason",
            "title": "Title", "created": "Created", "call": "CallId",
            "call_article": "CallArticle", "call_purpose": "CallPurpose",
            "foreign": "Foreign", "print_html": "PrintHtml",
        }
        result.update({target: payload[k] for k, target in fields.items() if payload.get(k) is not None})
        contacts = payload.get("contacts") or {}
        if isinstance(contacts, Mapping):
            if contacts.get("email") is not None:
                result["Email"] = contacts["email"]
            if contacts.get("phone") is not None:
                result["Phone"] = contacts["phone"]
        org = self._object(payload.get("org"), {
            "inn": "Inn", "kpp": "Kpp", "ogrn": "Ogrn", "name": "Name",
            "full": "Full", "address": "Address",
        })
        if org:
            result["Org"] = org
        period = payload.get("period") or {}
        if isinstance(period, Mapping):
            if period.get("from"):
                result["PeriodFrom"] = period["from"]
            if period.get("to"):
                result["PeriodTo"] = period["to"]
        prikaz = self._object(payload.get("prikaz"), {"n": "N", "date": "Date", "title": "Title"})
        if prikaz:
            result["Prikaz"] = prikaz
        if payload.get("fields") is not None:
            f = payload["fields"]
            if not isinstance(f, Mapping):
                raise OneCError("PortalMfc: Fields должен быть объектом ЛК")
            result["Fields"] = [{"Key": str(k), "Value": str(v)} for k, v in f.items()]
        if payload.get("files") is not None:
            files = payload["files"]
            if not isinstance(files, list):
                raise OneCError("PortalMfc: Files должен быть списком ЛК")
            result["Files"] = [{"Name": f["name"], "Content": f["content_base64"]} for f in files]
        signer = self._object(payload.get("signer"), {"pos": "Pos", "fio_full": "FioFull"})
        if signer:
            result["Signer"] = signer
        return result

    async def person(self, pid):
        reply = await self._portal("person", id=pid)
        data = self._field(reply, "Person")
        if data is None:
            raise OneCError("PortalMfc GetMfcPerson: ответ без Person")
        return self._person(data)

    async def prikazy(self, pid, program=None):
        if not program:
            raise OneCError("PortalMfc GetMfcOrders: не выбран код зачётки")
        reply = await self._portal("prikazy", id=pid, program=program)
        raw = self._collection(reply, "Orders", "Order", item="Order")
        return [self._mapped_fields(x, {"N": "n", "Date": "date", "Type": "type", "Title": "title"}) for x in raw]

    async def signers(self):
        # Операция получения подписантов отсутствует в PortalMfc;
        # настройки подписантов ЛК остаются локальными.
        return {}

    async def create_request(self, payload):
        # Описанная 1С идемпотентность по PersonId + LkId допускает повтор
        # после тайм-аута с ТЕМ ЖЕ payload. Ошибки связи не переводим в
        # OneCIndeterminate, чтобы очередь повторила неизменный заказ.
        reply = await self._portal("request_create", payload=self._create_payload(payload))
        data = reply.get("Request")
        if data is None:
            raise OneCError("PortalMfc CreateMfcRequest: ответ без Request; требуется сверка")
        return self._request(data)

    async def update_request(self, rid, payload):
        reply = await self._portal("request_update", id=rid, payload=self._create_payload(payload))
        return self._request(reply.get("Request"))

    async def cancel_request(self, rid):
        reply = await self._portal("request_cancel", id=rid)
        return self._request(reply.get("Request"))

    async def reject_request(self, rid, reason):
        reply = await self._portal("request_reject", id=rid, reason=reason or "", auto=True)
        return self._request(reply.get("Request"))

    async def requests(self, since=None):
        reply = await self._portal("requests_list", since=since)
        raw = self._collection(reply, "Requests", "Request", item="Request")
        return [self._request(x) for x in raw]

    async def _file(self, key, rid, mime):
        reply = await self._portal(key, id=rid)
        file = reply.get("File") if isinstance(reply.get("File"), Mapping) else reply
        if not isinstance(file, Mapping) or file.get("Content") is None:
            raise OneCError(f"PortalMfc {key}: ответ без Content")
        value = file["Content"]
        if isinstance(value, str):
            try:
                value = base64.b64decode(re.sub(r"\s+", "", value), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise OneCError(f"PortalMfc {key}: повреждённый base64") from exc
        if not isinstance(value, bytes):
            raise OneCError(f"PortalMfc {key}: некорректный тип Content")
        return value, mime

    async def document(self, rid):
        return await self._file("request_document", rid, "application/pdf")

    async def signature(self, rid):
        return await self._file("request_signature", rid, "application/octet-stream")
