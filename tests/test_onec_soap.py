"""Контрактные проверки SOAP-адаптера без доступа к внутреннему WSDL/1С."""
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lk.config import load
from lk.onec import OneC, OneCError, OneCIndeterminate, OneCUnavailable, make_onec
from lk.onec_soap import OneCSoap


def cfg(tmp_path):
    c = load(tmp_path / "nothing.json")
    c["onec"]["wsdl"] = "https://1c.internal.invalid/mfc?wsdl"
    return c


def test_http_still_default(tmp_path):
    c = cfg(tmp_path)
    assert isinstance(make_onec(c), OneC)
    assert not isinstance(make_onec(c), OneCSoap)


def test_select_soap_and_invalid(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["transport"] = "soap"
    s = make_onec(c)
    assert isinstance(s, OneCSoap) and s.configured
    c["onec"]["transport"] = "bogus"
    with pytest.raises(ValueError):
        make_onec(c)


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("MFC_ONEC_TRANSPORT", "soap")
    monkeypatch.setenv("MFC_ONEC_WSDL", "https://internal.invalid/1cws?wsdl")
    monkeypatch.setenv("MFC_ONEC_LOGIN", "user")
    monkeypatch.setenv("MFC_ONEC_PASSWORD", "secret")
    monkeypatch.setenv("MFC_ONEC_MAX_CONCURRENT", "4")
    c = load(tmp_path / "none.json")
    assert (c["onec"]["transport"], c["onec"]["wsdl"], c["onec"]["user"]) == ("soap", "https://internal.invalid/1cws?wsdl", "user")
    assert c["onec"]["password"] == "secret"
    assert c["onec"]["soap"]["max_concurrent"] == 4


@pytest.mark.asyncio
async def test_missing_wsdl(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["wsdl"] = ""
    obj = OneCSoap(c)
    with pytest.raises(OneCUnavailable):
        await obj._ensure_client()


@pytest.mark.asyncio
async def test_no_guessed_method(tmp_path):
    s = OneCSoap(cfg(tmp_path))
    with pytest.raises(OneCError, match="не сопоставлена"):
        await s.create_request({"lk_id": 1})


@pytest.mark.asyncio
async def test_mapping_and_outputs(tmp_path):
    c = cfg(tmp_path)
    ops = {
        "person": {"method": "PersonById", "args": {"PersonId": "$id"}},
        "prikazy": {"method": "Orders", "args": {"User": "$id", "Program": "$program"}},
        "request_create": {"method": "Submit", "args": {"Order": "$payload"}},
        "requests_list": {"method": "List", "args": {"Since": "$since"}, "response": "data"},
        "request_document": {"method": "Document", "args": {"Id": "$id"}, "response": "file.payload", "encoding": "base64"},
        "request_signature": {"method": "Signature", "args": {"Id": "$id"}, "response": "file"},
    }
    c["onec"]["soap"]["operations"] = ops
    calls = {}
    def method(name, result):
        async def call(**kwargs):
            calls[name] = kwargs
            return result
        return call
    svc = SimpleNamespace(
        PersonById=method("person", {"fio": "Иванов"}),
        Orders=method("orders", {"orders": [{"id": "p1"}]}),
        Submit=method("create", {"id": "777", "status": "reg"}),
        List=method("list", {"data": {"requests": [{"id": "777"}]}}),
        Document=method("pdf", {"file": {"payload": base64.b64encode(b"%PDF").decode()}}),
        Signature=method("sig", {"file": b"sigbytes"}),
    )
    s = OneCSoap(c)
    s._client = SimpleNamespace(service=svc)
    assert (await s.person("123"))["fio"] == "Иванов"
    assert calls["person"] == {"PersonId": "123"}
    assert (await s.prikazy("123", "p1"))[0]["id"] == "p1"
    assert (await s.create_request({"lk_id": 23}))["id"] == "777"
    assert calls["create"] == {"Order": {"lk_id": 23}}
    assert (await s.requests("2026-11-10"))[0]["id"] == "777"
    assert (await s.document("777")) == (b"%PDF", "application/pdf")
    assert (await s.signature("777")) == (b"sigbytes", "application/octet-stream")
    await s.close()


@pytest.mark.asyncio
async def test_soap_create_timeout_not_retried(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["soap"]["operations"] = {"request_create": {"method": "Submit", "args": {"Order": "$payload"}}}
    async def timeout(**kwargs):
        raise TimeoutError("connection lost after sending")
    s = OneCSoap(c)
    s._client = SimpleNamespace(service=SimpleNamespace(Submit=timeout))
    with pytest.raises(OneCIndeterminate, match="сверка"):
        await s.create_request({"lk_id": 5})


@pytest.mark.asyncio
async def test_soap_return_without_id_requires_reconciliation(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["soap"]["operations"] = {"request_create": {"method": "Submit"}}
    s = OneCSoap(c)
    s._client = SimpleNamespace(service=SimpleNamespace(Submit=AsyncMock(return_value={})))
    with pytest.raises(OneCIndeterminate):
        await s.create_request({"lk_id": 10})


def test_no_private_attribute_access():
    with pytest.raises(OneCError):
        OneCSoap._get_path({"data": 1}, "data.__class__")


def test_error_messages_redacted():
    err = OneCSoap._translate_error(TimeoutError("password=123"), "Person")
    assert "123" not in str(err)


@pytest.mark.asyncio
async def test_soap_mapping_external_response_fields_and_json(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["soap"]["operations"] = {
        "request_create": {
            "method": "Submit", "args": {"RequestId": "$payload.lk_id"},
            "json_response": True,
            "output_fields": {"id": "RequestID", "status": "State"},
        },
    }
    s = OneCSoap(c)
    s._client = SimpleNamespace(service=SimpleNamespace(Submit=AsyncMock(return_value='{ "RequestID": "123", "State": "reg" }')))
    assert (await s.create_request({"lk_id": 7})) == {"id": "123", "status": "reg"}
    assert s._client.service.Submit.await_args.kwargs == {"RequestId": 7}


@pytest.mark.asyncio
async def test_soap_status_mapping_preserved(tmp_path):
    c = cfg(tmp_path)
    c["onec"]["status_map"] = {"Поступило": "reg"}
    assert OneCSoap(c).status("Поступило") == "reg"
    assert OneCSoap(c).status("Передано на подпись") == "sign"
