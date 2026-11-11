"""Одноразовый HMAC SSO-билет от доверенного личного кабинета студента.
Секрет через окружение; никакой передачи пароля студента.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time


class SSOError(ValueError):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret():
    secret = os.environ.get("MFC_PORTAL_SSO_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise SSOError("SSO-секрет не настроен или слишком короткий (минимум 32 байта)")
    return secret.encode("utf-8")


def sign_ticket(person, name, config, *, nonce=None, now=None):
    """Вспомогательный пример подписи для доверенного портала; НЕ вызывать в браузере."""
    ts = int(time.time() if now is None else now)
    if not re.fullmatch(r"[\w.-]{1,128}", str(person), re.UNICODE):
        raise SSOError("Недопустимый идентификатор 1С")
    claim = {"iss": config["portal"]["issuer"], "aud": config["portal"]["audience"],
             "sub": str(person), "name": str(name)[:200], "role": "student",
             "iat": ts, "exp": ts + 60, "nonce": nonce or secrets.token_urlsafe(24)}
    body = _b64(json.dumps(claim, ensure_ascii=False, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature


def verify_and_consume(ticket, config, db, *, now=None):
    if not isinstance(ticket, str) or len(ticket) > 4096 or ticket.count(".") != 1:
        raise SSOError("Неверный формат билета")
    body, provided = ticket.split(".")
    if not body or not provided:
        raise SSOError("Пустой билет")
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    try:
        actual = _unb64(provided)
        if not hmac.compare_digest(expected, actual):
            raise SSOError("Неверная подпись")
        data = json.loads(_unb64(body))
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        raise SSOError("Повреждённый билет") from e
    ts = int(time.time() if now is None else now)
    if data.get("iss") != config["portal"]["issuer"] or data.get("aud") != config["portal"]["audience"]:
        raise SSOError("Билет для другого приложения")
    if data.get("role") != "student":
        raise SSOError("Недопустимая роль")
    if not isinstance(data.get("iat"), int) or not isinstance(data.get("exp"), int) or not ts - 30 <= data["iat"] <= ts + 10 or not ts < data["exp"] <= data["iat"] + 60:
        raise SSOError("Билет истёк")
    person, nonce = data.get("sub"), data.get("nonce")
    if not isinstance(person, str) or not re.fullmatch(r"[\w.-]{1,128}", person, re.UNICODE):
        raise SSOError("Неверный ID студента")
    if not isinstance(nonce, str) or not 16 <= len(nonce) <= 96:
        raise SSOError("Неверный nonce")
    db.x("CREATE TABLE IF NOT EXISTS portal_sso_nonces (nonce TEXT PRIMARY KEY, exp INTEGER NOT NULL)")
    db.x("DELETE FROM portal_sso_nonces WHERE exp < ?", (ts - 60,))
    try:
        db.x("INSERT INTO portal_sso_nonces(nonce, exp) VALUES (?, ?)", (nonce, data["exp"]))
    except Exception as e:
        # UNIQUE гарантирует, что даже два параллельных входа не используют nonce повторно.
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError):
            raise SSOError("Билет уже использован") from e
        raise
    return {"person": person, "name": str(data.get("name") or "Студент")[:200]}
