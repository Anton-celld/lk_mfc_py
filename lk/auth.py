"""Единый вход в ЛК студента и кабинет сотрудника. Учётные данные генерирует 1С; способ, которым сервис
их получает, подключается настройкой config.auth.providers (см. ONEC_API.md, раздел «Вход»):

  onec_check  — логин и пароль проверяет 1С; у нас пароли не хранятся (рекомендуется)
  onec_import — сервис забирает из 1С сгенерированные учётки (логин + хэш пароля) и проверяет сам
  onec_token  — 1С выдаёт одноразовую ссылку входа …/enter?t=ТОКЕН

Роли: student — ЛК студента; staff — кабинет сотрудника МФЦ; admin — сотрудник + настройки МФЦ.
"""
import base64
import datetime as dt
import hashlib
import hmac
import logging
import secrets
import time

from .db import now
from .onec import OneCError

log = logging.getLogger("lk.auth")
ROLES = {"student", "staff", "admin"}
FAIL_WINDOW, FAIL_MAX = 600, 8


def verify_hash(stored, password):
    """Хэш пароля из 1С. Форматы: pbkdf2_sha256$итерации$соль$хэш_base64 (как в Django) и sha256$соль$хэш_hex."""
    try:
        algo, *rest = str(stored).split("$")
        if algo == "pbkdf2_sha256":
            it, salt, h = rest
            got = base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(it))).decode()
            return hmac.compare_digest(got, h)
        if algo == "sha256":
            salt, h = rest
            return hmac.compare_digest(hashlib.sha256((salt + password).encode()).hexdigest(), h.lower())
    except (ValueError, TypeError):
        pass
    return False


def make_hash(password, it=260000):
    """Для тестов и имитатора 1С — тот же формат, что ждём от 1С."""
    salt = secrets.token_urlsafe(12)
    h = base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), it)).decode()
    return f"pbkdf2_sha256${it}${salt}${h}"


def _norm(r):
    """Ответ 1С о пользователе → {person, role, name}."""
    role = r.get("role") if r.get("role") in ROLES else "student"
    return {"person": str(r["person"]), "role": role, "name": r.get("name") or ""}


class Auth:
    def __init__(self, cfg, db, onec):
        self.cfg, self.db, self.onec = cfg, db, onec
        self.providers = [p.strip() for p in cfg["auth"]["providers"].split(",") if p.strip()]

    # --- защита от подбора ---
    def locked(self, *keys):
        since = time.time() - FAIL_WINDOW
        self.db.x("DELETE FROM login_fail WHERE at < ?", (since,))
        return any(self.db.q("SELECT COUNT(*) n FROM login_fail WHERE key=? AND at>=?", (k, since), one=True)["n"] >= FAIL_MAX
                   for k in keys if k)

    def fail(self, *keys):
        for k in keys:
            if k:
                self.db.x("INSERT INTO login_fail(key, at) VALUES(?, ?)", (k, time.time()))

    # --- проверка логина и пароля ---
    async def login(self, login, password, ip=""):
        login = (login or "").strip().lower()[:80]
        if not login or not password:
            return None, "Введите логин и пароль"
        if self.locked("ip:" + ip, "login:" + login):
            return None, "Слишком много попыток. Подождите 10 минут"
        user = None
        try:
            if "onec_check" in self.providers:
                r = await self.onec.auth_check(login, password)
                user = _norm(r) if r else None
            if not user and "onec_import" in self.providers:
                a = self.db.q("SELECT * FROM accounts WHERE login=? AND active=1", (login,), one=True)
                if a and verify_hash(a["hash"], password):
                    user = {"person": a["person"], "role": a["role"], "name": a["name"]}
        except OneCError as e:
            log.warning("login via 1C: %s", e)
            if "onec_import" not in self.providers:
                return None, "Сервис входа временно недоступен. Попробуйте через несколько минут"
        if not user:
            self.fail("ip:" + ip, "login:" + login)
            return None, "Неверный логин или пароль"
        return user, None

    async def by_token(self, token, ip=""):
        if "onec_token" not in self.providers or not token:
            return None, "Ссылка для входа недействительна"
        if self.locked("ip:" + ip):
            return None, "Слишком много попыток. Подождите 10 минут"
        try:
            r = await self.onec.auth_token(token)
        except OneCError:
            return None, "Сервис входа временно недоступен"
        if not r:
            self.fail("ip:" + ip)
            return None, "Ссылка для входа устарела или уже использована"
        return _norm(r), None

    async def import_accounts(self):
        """onec_import: забрать из 1С новые и изменённые учётки."""
        if "onec_import" not in self.providers:
            return 0
        since = self.db.setting("accounts_since")
        rows = await self.onec.accounts(since)
        for a in rows:
            self.db.x("INSERT OR REPLACE INTO accounts(login, hash, person, role, name, active, updated) VALUES(?,?,?,?,?,?,?)",
                      (str(a["login"]).lower(), a["password_hash"], str(a["person"]), a.get("role") if a.get("role") in ROLES else "student",
                       a.get("name", ""), 0 if a.get("active") is False else 1, now()))
        if rows:
            self.db.set_setting("accounts_since", max(str(a.get("updated", "")) for a in rows) or now())
        return len(rows)

    # --- сессии: в базе только хэш токена ---
    def start(self, user, ip=""):
        tok = secrets.token_urlsafe(32)
        self.db.x("INSERT INTO sessions(tok, person, role, name, created, seen, ip) VALUES(?,?,?,?,?,?,?)",
                  (hashlib.sha256(tok.encode()).hexdigest(), user["person"], user["role"], user["name"], now(), now(), ip))
        return tok

    def check(self, tok):
        if not tok:
            return None
        h = hashlib.sha256(tok.encode()).hexdigest()
        s = self.db.q("SELECT * FROM sessions WHERE tok=?", (h,), one=True)
        if not s:
            return None
        a = self.cfg["auth"]
        t = dt.datetime.now()
        if (t - dt.datetime.fromisoformat(s["created"]) > dt.timedelta(days=a["session_days"])
                or t - dt.datetime.fromisoformat(s["seen"]) > dt.timedelta(hours=a["session_idle_hours"])):
            self.db.x("DELETE FROM sessions WHERE tok=?", (h,))
            return None
        self.db.x("UPDATE sessions SET seen=? WHERE tok=?", (now(), h))
        return {"person": s["person"], "role": s["role"], "name": s["name"]}

    def stop(self, tok):
        if tok:
            self.db.x("DELETE FROM sessions WHERE tok=?", (hashlib.sha256(tok.encode()).hexdigest(),))
