"""Хранилище ЛК (SQLite). Источник правды по людям, программам и приказам — 1С; здесь — заказы и их ход,
очередь отправки в 1С, отзывы, настройки МФЦ, сессии и журнал."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person TEXT NOT NULL, program TEXT NOT NULL, svc TEXT NOT NULL, title TEXT NOT NULL,
  data TEXT NOT NULL,                 -- JSON: вид справки, организация, период, поля заявления, файлы…
  status TEXT NOT NULL, hist TEXT NOT NULL, comment TEXT DEFAULT '',
  onec_id TEXT, onec_number TEXT, sync TEXT DEFAULT 'queued',   -- queued | sent | error
  sync_error TEXT DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL,
  rating INTEGER, review TEXT, review_at TEXT
);
CREATE INDEX IF NOT EXISTS orders_person ON orders(person);
CREATE INDEX IF NOT EXISTS orders_sync ON orders(sync);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(tok TEXT PRIMARY KEY, person TEXT, role TEXT, name TEXT, created TEXT, seen TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS accounts(login TEXT PRIMARY KEY, hash TEXT, person TEXT, role TEXT, name TEXT, active INTEGER, updated TEXT);
CREATE TABLE IF NOT EXISTS people(person TEXT PRIMARY KEY, data TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT, title TEXT, body TEXT, oid INTEGER, at TEXT, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS orgs(inn TEXT PRIMARY KEY, data TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, person TEXT, role TEXT, action TEXT, detail TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS login_fail(key TEXT, at REAL);
"""


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "lk.db"
        con = self.con()
        con.executescript(SCHEMA)
        con.close()

    def con(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def q(self, sql, args=(), one=False):
        con = self.con()
        try:
            cur = con.execute(sql, args)
            rows = cur.fetchall()
        finally:
            con.close()
        return (rows[0] if rows else None) if one else rows

    def x(self, sql, args=()):
        con = self.con()
        try:
            with con:
                cur = con.execute(sql, args)
                return cur.lastrowid
        finally:
            con.close()

    # --- настройки МФЦ: одна JSON-запись на ключ ---
    def setting(self, k, default=None):
        r = self.q("SELECT v FROM settings WHERE k=?", (k,), one=True)
        return json.loads(r["v"]) if r else default

    def set_setting(self, k, v):
        self.x("INSERT INTO settings(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (k, json.dumps(v, ensure_ascii=False)))

    def audit(self, person, role, action, detail="", ip=""):
        self.x("INSERT INTO audit(at, person, role, action, detail, ip) VALUES(?,?,?,?,?,?)",
               (now(), person, role, action, str(detail)[:500], ip))
