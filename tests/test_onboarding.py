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
from handlers.documents import DocumentHandler, limit_first_document_questions
from handlers.main_menu import MainMenuHandler
from handlers.onboarding import OnboardingHandler
from handlers.rag_base import MON_RK_RULES, MON_RK_RULES_KG
from handlers.texts import t


class FakeMessage:
    def __init__(self, chat_id=1, text=""):
        self.chat_id = chat_id
        self.text = text
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

    async def test_onboarding_does_not_claim_unverified_official_compliance(self):
        for lang in ("ru", "kz"):
            welcome = t(lang, "onboard_1_text")
            workflow = t(lang, "onboard_2_text")
            self.assertNotIn("по приказам МОН РК", welcome)
            self.assertNotIn("МОН ҚР бұйрықтары бойынша", welcome)
            self.assertNotIn("строго по МОН РК", workflow)
            self.assertNotIn("мінсіз болғанша", workflow)
        for rules in (MON_RK_RULES, MON_RK_RULES_KG):
            self.assertIn("не подтвержд", rules)
            self.assertNotIn("Приказ МОН РК №", rules)

    async def test_first_document_quick_path_skips_optional_questions(self):
        question_sets = {
            "lesson_plan": ["subject_class", "topic", "duration", "goals"],
            "kindergarten_cycle_schedule": ["group", "period", "week_topic", "events"],
            "characteristic": ["student_name", "performance", "behavior", "activities", "purpose"],
            "kg_child_characteristic": ["student_name", "performance", "behavior", "activities", "purpose"],
        }
        for doc_type, keys in question_sets.items():
            questions = [{"key": key, "q": key} for key in keys]
            quick = limit_first_document_questions(doc_type, questions, is_first_document=True)
            regular = limit_first_document_questions(doc_type, questions, is_first_document=False)
            self.assertLessEqual(len(quick), 3)
            self.assertEqual(regular, questions)

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

    async def test_question_count_tracks_prefilled_fields_and_backed_answers(self):
        document_handler = DocumentHandler(self.db, "")
        self.context.user_data.update({
            "doc_type": "lesson_plan",
            "doc_lang": "ru",
            "questions": [
                {"key": "subject_class", "q": "Предмет и класс?"},
                {"key": "topic", "q": "Тема?"},
                {"key": "date", "q": "Дата?"},
            ],
            "doc_answers": {"subject_class": "Математика, 7А"},
        })
        message = FakeMessage(705)

        await document_handler._ask_question(message, self.context, "ru", 0, doc_name="КСП")
        self.assertIn("Вопрос 1 из 2", message.sent[-1][0])
        self.context.user_data["doc_answers"]["topic"] = "Дроби"
        await document_handler._ask_question(message, self.context, "ru", 2, doc_name="КСП")
        self.assertIn("Вопрос 2 из 2", message.sent[-1][0])

    async def test_school_user_cannot_start_kindergarten_document(self):
        await self.db.upsert_user(706, {"lang": "ru", "role": "teacher"})
        query = FakeQuery(user_id=706)
        context = types.SimpleNamespace(user_data={})
        document_handler = DocumentHandler(self.db, "")

        await document_handler._start_doc(
            query, context, 706, await self.db.get_user(706), "ru", "kindergarten_cycle_schedule"
        )

        self.assertIn("роли", query.edited[-1][0])
        self.assertEqual(await self.db.get_funnel_event_counts(), [])

    async def test_kindergarten_user_cannot_start_school_document(self):
        await self.db.upsert_user(708, {"lang": "ru", "role": "kindergarten"})
        query = FakeQuery(user_id=708)
        context = types.SimpleNamespace(user_data={})
        document_handler = DocumentHandler(self.db, "")

        await document_handler._start_doc(
            query, context, 708, await self.db.get_user(708), "ru", "lesson_plan"
        )

        self.assertIn("роли", query.edited[-1][0])
        self.assertEqual(await self.db.get_funnel_event_counts(), [])

    async def test_free_user_menu_shows_only_create_until_first_result(self):
        menu = MainMenuHandler(self.db)
        first = menu._main_keyboard("ru", show_extras=False)
        after = menu._main_keyboard("ru", show_extras=True)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0].callback_data, "menu_create")
        self.assertGreater(len(after), 1)
        callbacks = [button.callback_data for row in after for button in row]
        self.assertNotIn("agent_memory_menu", callbacks)

    async def test_markdown_special_name_is_escaped_in_welcome(self):
        await self.db.upsert_user(704, {"name": "A_B*`[C]", "role": "teacher", "lang": "ru"})
        query = FakeQuery(user_id=704)
        context = types.SimpleNamespace(user_data={}, bot=FakeBot())

        await self.handler._finish_new_onboarding(query, context, 704, "ru")

        text, kwargs = query.edited[0]
        self.assertIn("A\\_B\\*\\`\\[C]", text)
        self.assertEqual(kwargs["parse_mode"], "Markdown")

    async def test_optional_profile_is_collected_after_first_document_and_can_be_skipped(self):
        await self.db.upsert_user(707, {"lang": "ru", "role": "teacher"})
        query = FakeQuery("onboard_save_profile", 707)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)
        await self.handler.callback(update, self.context)
        self.assertIn("Название школы", query.edited[-1][0])

        for answer in ("Лицей № 8", "Учитель биологии"):
            message = FakeMessage(707, answer)
            update = types.SimpleNamespace(
                effective_user=types.SimpleNamespace(id=707), effective_message=message, message=message
            )
            await self.handler.handle_text(update, self.context)

        saved = await self.db.get_user(707)
        self.assertEqual(saved["school"], "Лицей № 8")
        self.assertEqual(saved["position"], "Учитель биологии")
        self.assertIsNone(saved["director"])

        query = FakeQuery("onboard_profile_skip_field", 707)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)
        await self.handler.callback(update, self.context)
        self.assertIn("Спасибо", query.edited[-1][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
