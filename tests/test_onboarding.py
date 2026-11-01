"""Focused first-document onboarding tests. Run with: python tests/test_onboarding.py"""
import asyncio
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import Database
from handlers.main_menu import MainMenuHandler
from handlers.onboarding import OnboardingHandler


class FakeMessage:
    def __init__(self, chat_id=1):
        self.chat_id = chat_id
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


class FakeQuery:
    def __init__(self, data="", user_id=1):
        self.data = data
        self.from_user = types.SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id)
        self.edited = []

    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))


class FakeBot:
    async def send_message(self, **kwargs):
        return kwargs


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database()
        self.db.db_path = os.path.join(self.temp_dir.name, "onboarding.db")
        await self.db.init()
        self.handler = OnboardingHandler(self.db)
        self.context = types.SimpleNamespace(
            user_data={}, args=[], bot=FakeBot(), application=types.SimpleNamespace(user_data={})
        )

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_start_uses_telegram_language_and_shows_four_choices(self):
        user = types.SimpleNamespace(id=701, language_code="kk")
        message = FakeMessage(701)
        update = types.SimpleNamespace(effective_user=user, effective_message=message, message=message)

        await self.handler.start(update, self.context)

        saved = await self.db.get_user(701)
        self.assertEqual(saved["lang"], "kz")
        self.assertEqual(len(message.sent[0][1]["reply_markup"].inline_keyboard), 4)
        self.assertNotIn("ФИО", message.sent[0][0])
        events = await self.db.get_funnel_event_counts()
        self.assertEqual(events[0]["event"], "start")

    async def test_quick_cyclegram_infers_kindergarten_and_starts_document(self):
        await self.db.upsert_user(702, {"lang": "ru"})
        query = FakeQuery("onboard_doc_cycle", 702)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)

        await self.handler.callback(update, self.context)

        user = await self.db.get_user(702)
        self.assertEqual(user["role"], "kindergarten")
        self.assertEqual(self.context.user_data["doc_type"], "kindergarten_cycle_schedule")
        self.assertTrue(query.edited)
        self.assertIn("doc_lang_kz", str(query.edited[-1][1]["reply_markup"]))

    async def test_ambiguous_characteristic_asks_role_in_one_step(self):
        await self.db.upsert_user(703, {"lang": "ru"})
        query = FakeQuery("onboard_doc_characteristic", 703)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)
        await self.handler.callback(update, self.context)
        role_buttons = query.edited[-1][1]["reply_markup"].inline_keyboard[0]
        role_labels = [button.text.lower() for button in role_buttons]
        self.assertTrue(any("школ" in label for label in role_labels))
        self.assertTrue(any("детский сад" in label for label in role_labels))

        query = FakeQuery("onboard_role_teacher", 703)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)
        await self.handler.callback(update, self.context)
        self.assertEqual(self.context.user_data["doc_type"], "characteristic")

    async def test_free_user_menu_shows_only_create_until_first_result(self):
        menu = MainMenuHandler(self.db)
        first = menu._main_keyboard("ru", show_extras=False)
        after = menu._main_keyboard("ru", show_extras=True)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0].callback_data, "menu_create")
        self.assertGreater(len(after), 1)

    async def test_markdown_special_name_is_escaped_in_welcome(self):
        await self.db.upsert_user(704, {"name": "A_B*`[C]", "role": "teacher", "lang": "ru"})
        query = FakeQuery(user_id=704)
        context = types.SimpleNamespace(user_data={}, bot=FakeBot())

        await self.handler._finish_new_onboarding(query, context, 704, "ru")

        text, kwargs = query.edited[0]
        self.assertIn("A\\_B\\*\\`\\[C]", text)
        self.assertEqual(kwargs["parse_mode"], "Markdown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
