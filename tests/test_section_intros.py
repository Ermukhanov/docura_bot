import os
import tempfile
import types
import unittest

from database import Database
from handlers.agent import AgentHandler
from handlers.profile import ProfileHandler
from telegram.error import NetworkError


class FailingMessage:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    async def reply_text(self, *_args, **_kwargs):
        raise NetworkError("temporary Telegram connection failure")


class FakeQuery:
    def __init__(self, user_id):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.message = FailingMessage(user_id)


class SectionIntroDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database()
        self.db.db_path = os.path.join(self.temp_dir.name, "sections.db")
        await self.db.init()
        await self.db.upsert_user(901, {"name": "Учитель", "role": "teacher", "lang": "ru"})

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_schedule_intro_remains_pending_if_telegram_send_fails(self):
        with self.assertRaises(NetworkError):
            await AgentHandler(self.db, "").show_schedule_menu(FakeQuery(901), None, "ru")

        self.assertFalse(await self.db.has_seen_section(901, "schedule"))

    async def test_students_intro_remains_pending_if_telegram_send_fails(self):
        with self.assertRaises(NetworkError):
            await ProfileHandler(self.db, "")._show_students(FakeQuery(901), 901, "ru")

        self.assertFalse(await self.db.has_seen_section(901, "students"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
