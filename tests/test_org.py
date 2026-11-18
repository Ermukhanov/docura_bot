"""B2B: садик подключает педагогов одной ссылкой."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import Database


class OrgTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database()
        self.db.db_path = os.path.join(self.tmp.name, "t.db")
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_join_limits_and_toggle(self):
        code = await self.db.create_org("Садик Балапан", 2)
        self.assertEqual((await self.db.join_org(code, 1))[0], "ok")
        self.assertEqual((await self.db.join_org(code, 1))[0], "already")
        self.assertEqual((await self.db.join_org(code, 2))[0], "ok")
        self.assertEqual((await self.db.join_org(code, 3))[0], "full")
        self.assertEqual((await self.db.join_org("nope", 4))[0], "not_found")
        u = await self.db.get_user(1)
        self.assertEqual((u["subscribed"], u["tier"], u["sub_expires"]), (1, "b2b", None))

        await self.db.save_document(1, "lesson_plan", "КСП", "текст", 90)
        org = await self.db.get_org(code)
        self.assertEqual((org["members"], org["docs_30d"]), (2, 1))

        self.assertEqual(await self.db.set_org_active(code, False), 2)
        self.assertFalse((await self.db.get_user(2))["subscribed"])
        self.assertEqual((await self.db.join_org(code, 5))[0], "inactive")
        await self.db.set_org_active(code, True)
        self.assertEqual((await self.db.get_user(2))["tier"], "b2b")
        # просроченные B2B автоматика не трогает
        self.assertEqual(await self.db.get_expired_subscriptions(), [])
