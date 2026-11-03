"""Focused first-document onboarding tests. Run with: python tests/test_onboarding.py"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import Database
from handlers.documents import CAT_DOCS_KG, DocumentHandler, _parse_monitoring_submission, limit_first_document_questions
from handlers.main_menu import MainMenuHandler
from handlers.onboarding import OnboardingHandler
from handlers.profile import students_section_intro
from handlers.rag_base import MON_RK_RULES, MON_RK_RULES_KG
from handlers.texts import t
from telegram.error import NetworkError


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
            "calendar_plan": ["subject_class", "period", "textbook", "hours_per_week"],
            "monthly_report": ["period", "classes", "performance", "extra"],
        }
        for doc_type, keys in question_sets.items():
            questions = [{"key": key, "q": key} for key in keys]
            quick = limit_first_document_questions(doc_type, questions, is_first_document=True)
            regular = limit_first_document_questions(doc_type, questions, is_first_document=False)
            self.assertLessEqual(len(quick), 3)
            self.assertEqual(regular, questions)

    async def test_kindergarten_menu_shows_one_development_card_under_children(self):
        category_by_type = {
            doc_type: category
            for category, doc_types in CAT_DOCS_KG.items()
            for doc_type in doc_types
        }

        self.assertEqual(category_by_type["kg_individual_development_card"], "kg_children")
        self.assertNotIn("development_monitoring", category_by_type)
        self.assertNotIn("kg_monitoring", category_by_type)

    async def test_student_section_intro_uses_school_and_kindergarten_terms(self):
        for lang in ("ru", "kz"):
            kindergarten_intro = students_section_intro(lang, is_kg=True).lower()
            school_intro = students_section_intro(lang, is_kg=False).lower()
            self.assertNotIn("оценк", kindergarten_intro)
            self.assertNotIn("бағал", kindergarten_intro)
            if lang == "ru":
                self.assertIn("учен", school_intro)
                self.assertIn("воспитан", kindergarten_intro)
            else:
                self.assertIn("оқушы", school_intro)
                self.assertIn("тәрбиеленуш", kindergarten_intro)

    async def test_start_uses_telegram_language_and_shows_four_choices(self):
        user = types.SimpleNamespace(id=701, language_code="kk")
        message = FakeMessage(701)
        update = types.SimpleNamespace(effective_user=user, effective_message=message, message=message)

        await self.handler.start(update, self.context)

        saved = await self.db.get_user(701)
        self.assertEqual(saved["lang"], "kz")
        self.assertEqual(len(message.sent[0][1]["reply_markup"].inline_keyboard), 4)
        self.assertIn("ҚМЖ үзіндісі", message.sent[0][0])
        self.assertIn("ресми үлгі емес", message.sent[0][0])
        self.assertNotIn("ФИО", message.sent[0][0])
        events = await self.db.get_funnel_event_counts()
        self.assertEqual(events[0]["event"], "start")

    async def test_russian_start_shows_labeled_demo_document_fragment(self):
        user = types.SimpleNamespace(id=711, language_code="ru")
        message = FakeMessage(711)
        update = types.SimpleNamespace(effective_user=user, effective_message=message, message=message)

        await self.handler.start(update, self.context)

        text = message.sent[0][0]
        self.assertIn("Пример фрагмента КСП", text)
        self.assertIn("не официальный образец", text)
        self.assertEqual(len(message.sent[0][1]["reply_markup"].inline_keyboard), 4)

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

    async def test_quick_cyclegram_continues_if_callback_ack_network_fails(self):
        await self.db.upsert_user(709, {"lang": "ru"})
        query = FakeQuery("onboard_doc_cycle", 709)
        query.answer = AsyncMock(side_effect=NetworkError("temporary connection failure"))
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)

        await self.handler.callback(update, self.context)

        user = await self.db.get_user(709)
        self.assertEqual(user["role"], "kindergarten")
        self.assertEqual(self.context.user_data["doc_type"], "kindergarten_cycle_schedule")

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

    async def test_quick_other_choice_opens_role_specific_categories(self):
        await self.db.upsert_user(710, {"lang": "ru"})
        self.context.user_data["onboard_quick_doc"] = "other"
        query = FakeQuery("onboard_role_teacher", 710)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)

        await self.handler.callback(update, self.context)

        self.assertTrue(query.edited)
        keyboard = query.edited[-1][1]["reply_markup"].inline_keyboard
        callbacks = [button.callback_data for row in keyboard for button in row]
        self.assertIn("cat_planning", callbacks)
        self.assertNotIn("cat_kg_planning", callbacks)

    async def test_invalid_document_category_returns_to_role_categories(self):
        query = FakeQuery()
        await DocumentHandler(self.db, "")._show_doc_list(query, "ru", "unknown", {"role": "kindergarten"})

        self.assertTrue(query.edited)
        keyboard = query.edited[-1][1]["reply_markup"].inline_keyboard
        callbacks = [button.callback_data for row in keyboard for button in row]
        self.assertIn("cat_kg_planning", callbacks)

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

    async def test_monitoring_submission_maps_five_rows_to_table_columns(self):
        submitted = """ФИО: Айгуль Тестова
Год рождения: 2021
Возраст: 5
Группа: Ботақан
Физические: начальный | промежуточный | итоговый | III
Коммуникативные: 1 | 2 | 3 | II
Познавательные: 1 | 2 | 3 | I
Творческие: 1 | 2 | 3 | III
Социальные: 1 | 2 | 3 | II"""

        parsed = _parse_monitoring_submission(submitted)

        self.assertEqual(parsed["child_name"], "Айгуль Тестова")
        self.assertEqual(parsed["birth_year"], "2021")
        self.assertEqual(parsed["age"], "5")
        self.assertEqual(parsed["group"], "Ботақан")
        self.assertEqual(len(parsed["rows"]), 5)
        self.assertEqual(parsed["rows"][0], ["начальный", "промежуточный", "итоговый", "III"])

    async def test_monitoring_language_choice_offers_observations_or_blank_form(self):
        document_handler = DocumentHandler(self.db, "")
        document_handler._generate_monitoring = AsyncMock()
        await self.db.upsert_user(707, {"lang": "kz", "role": "kindergarten"})
        self.context.user_data.update({"doc_type": "development_monitoring"})
        query = FakeQuery("doc_lang_kz", 707)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)

        await document_handler.callback(update, self.context)

        self.assertEqual(self.context.user_data["doc_lang"], "kz")
        self.assertTrue(query.edited[-1][0].startswith("Бала бойынша"))
        buttons = [button for row in query.edited[-1][1]["reply_markup"].inline_keyboard for button in row]
        self.assertEqual(
            [button.callback_data for button in buttons],
            ["doc_monitoring_data_yes", "doc_monitoring_data_no", "doc_cancel"],
        )
        document_handler._generate_monitoring.assert_not_awaited()

    async def test_blank_monitoring_choice_disables_database_child_prefill(self):
        document_handler = DocumentHandler(self.db, "")
        document_handler._generate_monitoring = AsyncMock()
        self.context.user_data["doc_answers"] = {"rows": []}
        query = FakeQuery("doc_monitoring_data_no", 708)
        update = types.SimpleNamespace(callback_query=query, effective_user=query.from_user)

        await document_handler.callback(update, self.context)

        self.assertEqual(self.context.user_data["doc_answers"]["children"], "пустой")
        document_handler._generate_monitoring.assert_awaited_once()

    async def test_monitoring_reprompts_instead_of_generating_partial_observations(self):
        document_handler = DocumentHandler(self.db, "")
        document_handler._generate_monitoring = AsyncMock()
        await self.db.upsert_user(709, {"lang": "ru", "role": "kindergarten"})
        self.context.user_data.update({
            "step": "monitoring_data",
            "doc_answers": {},
        })
        invalid_submissions = [
            "only one | partial | row | here",
            "\n".join(f"Area {index}: a | b | c | III" for index in range(6)),
        ]
        for submission in invalid_submissions:
            message = FakeMessage(709, submission)
            update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=709), effective_message=message)
            await document_handler.handle_text(update, self.context)
            self.assertIn("ровно 5 строк", message.sent[-1][0])
        self.assertEqual(self.context.user_data["step"], "monitoring_data")
        document_handler._generate_monitoring.assert_not_awaited()

    async def test_valid_monitoring_submission_generates_once_and_clears_input_step(self):
        document_handler = DocumentHandler(self.db, "")
        document_handler._generate_monitoring = AsyncMock()
        await self.db.upsert_user(710, {"lang": "ru", "role": "kindergarten"})
        self.context.user_data.update({"step": "monitoring_data", "doc_answers": {}})
        message = FakeMessage(710, """ФИО: Айгуль Тестова
Физические: начальный | промежуточный | итоговый | III
Коммуникативные: 1 | 2 | 3 | II
Познавательные: 1 | 2 | 3 | I
Творческие: 1 | 2 | 3 | III
Социальные: 1 | 2 | 3 | II""")
        update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=710), effective_message=message)

        await document_handler.handle_text(update, self.context)

        self.assertEqual(self.context.user_data["doc_answers"]["child_name"], "Айгуль Тестова")
        self.assertEqual(self.context.user_data["step"], None)
        document_handler._generate_monitoring.assert_awaited_once()

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

    async def test_first_document_menu_text_stays_focused_until_first_result(self):
        menu = MainMenuHandler(self.db)

        before = menu._build_menu_text({"name": "Айгуль", "role": "teacher", "free_used": 0}, "ru")
        after = menu._build_menu_text({"name": "Айгуль", "role": "teacher", "free_used": 1}, "ru")

        self.assertIn("Создать документ", before)
        self.assertNotIn("персональный ИИ-методист", before)
        self.assertNotIn("Расписание", before)
        self.assertIn("персональный ИИ-методист", after)

    async def test_main_menu_escapes_special_names_for_telegram_markdown(self):
        text = MainMenuHandler(self.db)._build_menu_text(
            {"name": "A_B*`[C]", "role": "teacher", "free_used": 0}, "ru"
        )

        self.assertIn("A\\_B\\*\\`\\[C]", text)

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
