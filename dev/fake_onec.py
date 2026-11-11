#!/usr/bin/env python3
"""Заглушка HTTP-сервиса 1С для локального стенда (контракт: ONEC_API.md). Только для разработки, все данные вымышлены.

  python dev/fake_onec.py [порт]        # по умолчанию 9998

Любой идентификатор студента получает одну программу обучения; заказы принимаются и остаются в статусе «Зарегистрировано».
Адрес для сервиса mfc: MFC_ONEC_BASE_URL=http://host.docker.internal:9998/
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9998
ORDERS = []


# Демо-студенты стенда (вымышленные): идентификатор в 1С = GUID из портала. Остальные получают обезличенную запись.
DEMO = {
    "20000000-0000-4000-8000-000000000001": ("Ильин Фёдор Олегович", "Ильину Фёдору Олеговичу", "st001.ilin@test.ru", "+7 900 100-00-01", "ДЭ 17-25", "m"),
    "20000000-0000-4000-8000-000000000002": ("Сергеев Александр Игоревич", "Сергееву Александру Игоревичу", "st002.sergeev@test.ru", "+7 900 100-00-02", "ДЭ 17-25", "m"),
    "20000000-0000-4000-8000-000000000003": ("Александров Виктор Андреевич", "Александрову Виктору Андреевичу", "st003.aleksandrov@test.ru", "+7 900 100-00-03", "ДЭ 17-25", "m"),
    "20000000-0000-4000-8000-000000000004": ("Васильева Алина Анатольевна", "Васильевой Алине Анатольевне", "st004.vasileva@test.ru", "+7 900 100-00-04", "ДЭ 17-25", "f"),
    "20000000-0000-4000-8000-000000000005": ("Никитин Антон Михайлович", "Никитину Антону Михайловичу", "st005.nikitin@test.ru", "+7 900 100-00-05", "ДЭ 17-25", "m"),
}


def person(pid):
    fio, dat, email, phone, group, gender = DEMO.get(pid, ("Тестов Тест Тестович", "Тестову Тесту Тестовичу", "test@example.ru", "+7 900 000-00-00", "ЭК-201", "m"))
    num = pid[-6:] if pid in DEMO else "000001"
    return {
        "id": pid, "fio": fio, "fio_dat": dat, "birth": "2005-05-05", "gender": gender,
        "citizenship": "Россия", "email": email, "phone": phone,
        "programs": [{
            "id": "p1", "book": num, "level": "bak", "code": "38.03.01", "name": "Экономика",
            "profile": "Экономика предприятий и организаций АПК", "form": "очной", "course": 2, "basis": "бюджетной основе",
            "group": group, "inst": "Институт экономики и управления АПК", "start": "2025-09-01", "end": "2029-08-31",
            "enroll": {"n": "СК-0001", "date": "2025-08-03"}, "calls": [],
        }],
    }


PRIKAZY = [
    {"n": "3105-ст", "date": "2025-08-03", "type": "О зачислении", "title": "О зачислении на 1 курс"},
    {"n": "2214-ст", "date": "2026-07-03", "type": "О переводе на следующий курс", "title": "О переводе на 2 курс"},
    {"n": "1830-ст", "date": "2026-09-15", "type": "О назначении академической стипендии", "title": "О назначении академической стипендии за 1 семестр 2026/27"},
]


class Handler(BaseHTTPRequestHandler):
    def _send(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].strip("/")
        if m := re.fullmatch(r"persons/([\w.-]+)", path):
            return self._send(person(m.group(1)))
        if re.fullmatch(r"persons/[\w.-]+/orders", path):
            return self._send({"orders": PRIKAZY})
        if path == "signers":
            return self._send({})
        if path == "requests":
            return self._send({"requests": []})
        self._send({"error": "нет такого метода"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.strip("/") == "requests":
            ORDERS.append(body)
            n = 5000 + len(ORDERS)
            return self._send({"id": str(n), "number": f"МФЦ-{n}", "status": "reg"})
        self._send({"ok": True})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Заглушка 1С: http://0.0.0.0:{PORT}/")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
