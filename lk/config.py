"""Настройки сервиса (config.json рядом с run.py). Всё, что меняется при внедрении, — здесь, а не в коде."""
import copy
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8090,
    "public_url": "",                 # https://… — адрес, по которому ЛК открывают пользователи
    "base_path": "",                  # если ЛК встроен в портал под префиксом, например "/mfc"
    "data_dir": "data",
    "portal": {"enabled": False, "issuer": "student-portal", "audience": "mfc-beta"},
    "demo": False,                    # демо-режим: имитатор 1С и тестовые учётки
    "auth": {
        # onec_check — логин и пароль проверяет 1С (POST auth/check), пароли у нас не хранятся;
        # onec_import — сервис забирает сгенерированные в 1С учётки (GET accounts) и проверяет сам;
        # onec_token — 1С выдаёт одноразовую ссылку входа (…/enter?t=…), сервис проверяет токен в 1С;
        # можно указать несколько через запятую: первый подходящий
        "providers": "onec_check",
        "session_days": 7,
        "session_idle_hours": 12,
        "import_every_min": 15,
    },
    "onec": {
        "transport": "http",          # http | soap — выбор транспорта 1С; НЕ межсервисного HTTP
        "wsdl": "",                  # адрес WSDL внутренней 1С (SOAP)
        "soap": {"max_concurrent": 5, "operations": {}},  # карта WSDL-методов, без угаданных имён
        "base_url": "",               # https://1c.example.ru/univer/hs/mfc/  (HTTP-сервис 1С)
        "user": "",                   # служебная учётка для HTTP-сервиса (Basic)
        "password": "",
        "timeout": 20,
        "sync_every_sec": 60,         # как часто подтягивать статусы заказов и отправлять очередь
        "ca_file": "",                # свой корневой сертификат, если у 1С внутренний
        # пути методов — если в 1С назвали иначе, меняются здесь
        "paths": {
            "auth_check": "auth/check",
            "auth_token": "auth/token",
            "accounts": "accounts",
            "person": "persons/{id}",
            "prikazy": "persons/{id}/orders",
            "signers": "signers",
            "request_create": "requests",
            "request_update": "requests/{id}",
            "request_cancel": "requests/{id}/cancel",
            "request_reject": "requests/{id}/reject",
            "requests_list": "requests",
            "request_document": "requests/{id}/document",
            "request_signature": "requests/{id}/signature",
        },
        # статусы 1С → статусы ЛК (если в 1С свои названия)
        "status_map": {},
    },
    "egrul": {
        "provider": "demo",           # dadata | demo
        "dadata_token": "",
        "cache_days": 30,
    },
    # сканы к заявлениям: файлов в заказе, МБ на файл, МБ на заказ всего; если на диске свободно меньше min_free_mb — не принимаем
    "uploads": {"max_files": 5, "max_mb": 5, "max_total_mb": 15, "min_free_mb": 1024},
    "assistant_url": "",              # ИИ-ассистент сотрудника (адрес уже работающего сервиса)
}


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# Переменные окружения поверх файла настроек: так сервис запускается в контейнере без config.json, а пароли не лежат в файлах
ENV_OVERRIDES = {
    "MFC_HOST": ("host",),
    "MFC_PORT": ("port",),
    "MFC_PUBLIC_URL": ("public_url",),
    "MFC_BASE_PATH": ("base_path",),
    "MFC_DATA_DIR": ("data_dir",),
    "MFC_ONEC_BASE_URL": ("onec", "base_url"),
    "MFC_ONEC_TRANSPORT": ("onec", "transport"),
    "MFC_ONEC_WSDL": ("onec", "wsdl"),
    "MFC_ONEC_LOGIN": ("onec", "user"),
    "MFC_ONEC_TIMEOUT": ("onec", "timeout"),
    "MFC_ONEC_MAX_CONCURRENT": ("onec", "soap", "max_concurrent"),
    "MFC_ONEC_USER": ("onec", "user"),
    "MFC_ONEC_PASSWORD": ("onec", "password"),
    "MFC_ONEC_CA_FILE": ("onec", "ca_file"),
    "MFC_EGRUL_PROVIDER": ("egrul", "provider"),
    "MFC_DADATA_TOKEN": ("egrul", "dadata_token"),
}


def _env_overrides(c):
    for name, keys in ENV_OVERRIDES.items():
        value = os.environ.get(name)
        if value is None or value == "":
            continue
        node = c
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = int(value) if keys[-1] in {"port", "timeout", "max_concurrent"} else value


def load(path=None):
    path = Path(path or os.environ.get("LK_CONFIG") or BASE / "config.json")
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    c = _merge(DEFAULTS, raw)
    _env_overrides(c)
    d = Path(c["data_dir"])
    c["data_dir"] = str(d if d.is_absolute() else BASE / d)
    Path(c["data_dir"]).mkdir(parents=True, exist_ok=True)
    return c
