#!/usr/bin/env python3
"""Студенческий МФЦ как отдельный Python-сервис для личного кабинета (платформа микросервисов или портал Yii2)."""
import argparse
import logging
import os

from aiohttp import web

from lk.config import load
from lk.web import make_app


def build_app(cfg):
    """Приложение под префиксом base_path (например /mfc): шлюз платформы передаёт путь без изменений.
    Без base_path приложение работает в корне (за прокси, который сам убирает префикс)."""
    app = make_app(cfg)
    base = (cfg.get("base_path") or "").rstrip("/")
    if not base:
        return app

    async def to_slash(request):
        raise web.HTTPFound(base + "/")

    root = web.Application()
    root.router.add_get(base, to_slash)   # раньше подприложения: иначе путь без слеша достаётся ему и даёт 404
    root.add_subapp(base, app)
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="portal_config.json")
    args = ap.parse_args()
    cfg = load(args.config)
    cfg.setdefault("portal", {})["enabled"] = True
    cfg["demo"] = False
    # HTTPS обязателен: cookie сессии помечается Secure. Только для локального стенда: MFC_ALLOW_HTTP=true
    if not cfg.get("public_url", "").startswith("https://") and os.environ.get("MFC_ALLOW_HTTP") != "true":
        raise SystemExit("Для входа из портала задайте HTTPS public_url (для локального стенда MFC_ALLOW_HTTP=true)")
    from lk.portal_sso import _secret
    _secret()
    logging.basicConfig(level=logging.INFO)
    web.run_app(build_app(cfg), host=cfg["host"], port=cfg["port"], access_log=None)


if __name__ == "__main__":
    main()
