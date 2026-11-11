"""Минимальные проверки Python-интеграции МФЦ с порталом."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lk.config import DEFAULTS
from lk.db import DB
from lk.portal_sso import SSOError, sign_ticket, verify_and_consume


class TestTicket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(self.tmp.name)
        self.c = {"portal": {"issuer": "student-portal", "audience": "mfc-beta"}}
        self.patch = patch.dict(os.environ, {"MFC_PORTAL_SSO_SECRET": "secure-test-key-replace-with-random-64-characters-only"})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_ticket_one_time(self):
        ticket = sign_ticket("STUD123", "Студент", self.c, now=100000)
        user = verify_and_consume(ticket, self.c, self.db, now=100001)
        self.assertEqual(user["person"], "STUD123")
        with self.assertRaisesRegex(SSOError, "уже использован"):
            verify_and_consume(ticket, self.c, self.db, now=100001)

    def test_expired(self):
        ticket = sign_ticket("STUD123", "Студент", self.c, now=100000)
        with self.assertRaises(SSOError):
            verify_and_consume(ticket, self.c, self.db, now=100061)

    def test_tamper(self):
        ticket = sign_ticket("STUD123", "Студент", self.c, now=100000)
        body, sig = ticket.split(".")
        with self.assertRaises(SSOError):
            verify_and_consume(body + "x." + sig, self.c, self.db, now=100001)

    def test_not_staff(self):
        ticket = sign_ticket("STUD123", "Студент", self.c, now=100000)
        import base64, json, hmac, hashlib
        raw, _ = ticket.split(".")
        d = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        d["role"] = "staff"
        body = base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(os.environ["MFC_PORTAL_SSO_SECRET"].encode(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        with self.assertRaises(SSOError):
            verify_and_consume(body + "." + sig, self.c, self.db, now=100001)


class TestPortalApp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import copy
        from aiohttp.test_utils import TestClient, TestServer
        from lk.web import make_app
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.dict(os.environ, {"MFC_PORTAL_SSO_SECRET": "secure-test-key-replace-with-random-64-characters-only"})
        self.p.start()
        self.c = copy.deepcopy(DEFAULTS)
        self.c["data_dir"] = self.tmp.name
        self.c["portal"]["enabled"] = True
        self.c["public_url"] = "https://example.test/mfc-beta/"
        self.c["base_path"] = "/mfc-beta"
        self.client = TestClient(TestServer(make_app(self.c)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.p.stop()
        self.tmp.cleanup()

    async def test_student_only_route(self):
        self.assertEqual((await self.client.get('/api/login')).status, 404)
        self.assertEqual((await self.client.get('/staff')).status, 404)
        self.assertEqual((await self.client.post('/api/staff/demo', headers={'X-LK':'1'})).status, 404)
        self.assertEqual((await self.client.get('/api/orders')).status, 401)

    async def test_sso_creates_student_session(self):
        ticket = sign_ticket('S1', 'Студент', self.c)
        resp = await self.client.get('/portal-enter?ticket=' + ticket, allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.cookies['lk_s']['path'], '/mfc-beta/')
        self.assertTrue(resp.cookies['lk_s']['secure'])
        token = resp.cookies['lk_s'].value
        row = self.client.server.app['db'].q('SELECT person,role FROM sessions', one=True)
        self.assertEqual((row['person'],row['role']),('S1','student'))
        reuse = await self.client.get('/portal-enter?ticket=' + ticket, allow_redirects=False)
        self.assertEqual(reuse.status, 403)

    async def test_exchange_header_ticket(self):
        ticket = sign_ticket('S2', 'Студент', self.c)
        auth = {'Authorization': 'Bearer ' + ticket}
        # без X-Requested-With запрос отклоняется до проверки билета: билет не расходуется
        self.assertEqual((await self.client.post('/portal-exchange', headers=auth)).status, 403)
        resp = await self.client.post('/portal-exchange', headers={**auth, 'X-Requested-With': 'portal'})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.cookies['lk_s']['path'], '/mfc-beta/')
        row = self.client.server.app['db'].q('SELECT person,role FROM sessions', one=True)
        self.assertEqual((row['person'], row['role']), ('S2', 'student'))
        # билет одноразовый, подделка и отсутствие билета отклоняются
        for headers in ({**auth, 'X-Requested-With': 'portal'}, {'Authorization': 'Bearer abc.def', 'X-Requested-With': 'portal'}, {'X-Requested-With': 'portal'}):
            self.assertEqual((await self.client.post('/portal-exchange', headers=headers)).status, 403)
        self.assertEqual((await self.client.get('/portal-exchange')).status, 405)


class TestPrefix(unittest.IsolatedAsyncioTestCase):
    """Сервис под префиксом (шлюз платформы передаёт путь /mfc/... без изменений)"""

    async def asyncSetUp(self):
        import copy
        from aiohttp.test_utils import TestClient, TestServer
        from portal_run import build_app
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.dict(os.environ, {"MFC_PORTAL_SSO_SECRET": "secure-test-key-replace-with-random-64-characters-only"})
        self.p.start()
        self.c = copy.deepcopy(DEFAULTS)
        self.c.update(data_dir=self.tmp.name, public_url="http://localhost:5173/mfc/", base_path="/mfc")
        self.c["portal"]["enabled"] = True
        self.client = TestClient(TestServer(build_app(self.c)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.p.stop()
        self.tmp.cleanup()

    async def test_routes_under_prefix(self):
        self.assertEqual((await self.client.get('/mfc/health')).status, 200)
        self.assertEqual((await self.client.get('/mfc/')).status, 200)
        self.assertEqual((await self.client.get('/health')).status, 404)
        redirect = await self.client.get('/mfc', allow_redirects=False)
        self.assertEqual((redirect.status, redirect.headers['Location']), (302, '/mfc/'))
        self.assertEqual((await self.client.get('/mfc/api/me')).status, 401)

    async def test_exchange_under_prefix(self):
        ticket = sign_ticket('S3', 'Студент', self.c)
        resp = await self.client.post('/mfc/portal-exchange', headers={'Authorization': 'Bearer ' + ticket, 'X-Requested-With': 'portal'})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.cookies['lk_s']['path'], '/mfc/')
        self.assertFalse(resp.cookies['lk_s']['secure'])   # стенд по http; в production public_url https → Secure


if __name__ == '__main__':
    unittest.main()
