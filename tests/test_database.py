"""Focused persistence tests. Run with: python tests/test_database.py"""
import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import Database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database()
        self.db.db_path = os.path.join(self.temp_dir.name, "test.db")
        await self.db.init()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_user_upsert_is_atomic_and_empty_update_is_a_noop(self):
        await self.db.upsert_user(101, {})
        self.assertIsNone(await self.db.get_user(101))

        await self.db.upsert_user(101, {"name": "Айгуль", "lang": "ru"})
        await self.db.upsert_user(101, {"school": "Школа № 4"})
        user = await self.db.get_user(101)

        self.assertEqual(user["name"], "Айгуль")
        self.assertEqual(user["lang"], "ru")
        self.assertEqual(user["school"], "Школа № 4")

    async def test_admin_user_list_paginates_and_filters_in_database(self):
        for user_id in range(201, 226):
            await self.db.upsert_user(user_id, {
                "name": str(user_id), "role": "kindergarten" if user_id % 2 else "teacher",
                "subscribed": 1 if user_id % 3 else 0,
                "tier": "max" if user_id % 3 else None,
            })

        first_page = await self.db.get_all_users(limit=10, offset=0)
        second_page = await self.db.get_all_users(limit=10, offset=10)
        kindergartens = await self.db.get_all_users(limit=30, filt="kg")

        self.assertEqual(len(first_page), 10)
        self.assertEqual(len(second_page), 10)
        self.assertTrue(set(u["tg_id"] for u in first_page).isdisjoint(u["tg_id"] for u in second_page))
        self.assertTrue(all(u["role"] == "kindergarten" for u in kindergartens))
        with self.assertRaises(ValueError):
            await self.db.get_all_users(filt="invalid")

    async def test_unregistered_audience_excludes_named_profiles_and_notification_opt_outs(self):
        await self.db.upsert_user(250, {"lang": "ru"})
        await self.db.upsert_user(251, {"name": "Зарегистрирован", "lang": "ru"})
        await self.db.upsert_user(252, {"lang": "kz", "notifications_enabled": 0})

        audience = await self.db.get_unregistered_users()
        listed = await self.db.get_all_users(limit=20, filt="unregistered")

        self.assertEqual([user["tg_id"] for user in audience], [250])
        self.assertEqual([user["tg_id"] for user in listed], [252, 250])

    async def test_user_funnel_events_and_last_activity_are_available_for_admin_detail(self):
        await self.db.upsert_user(253, {"name": "Учитель"})
        await self.db.log_funnel_event(253, "start")
        await self.db.log_funnel_event(253, "doc_type_chosen", "lesson_plan")
        await self.db.update_last_active(253)

        events = await self.db.get_user_funnel_events(253)
        user = await self.db.get_user(253)

        self.assertEqual([event["event"] for event in events], ["doc_type_chosen", "start"])
        self.assertEqual(events[0]["doc_type"], "lesson_plan")
        self.assertTrue(user["last_active_at"])

    async def test_new_document_sample_archives_previous_active_version(self):
        await self.db.add_sample("lesson_plan", "ru", "old content", "Old", 1)
        await self.db.add_sample("lesson_plan", "ru", "new content", "New", 1)

        active = await self.db.get_samples_for_type("lesson_plan", "ru", limit=10)
        history = await self.db.get_all_samples(limit=10)

        self.assertEqual([sample["content"] for sample in active], ["new content"])
        self.assertEqual(len(history), 2)
        archived = next(sample for sample in history if sample["content"] == "old content")
        self.assertEqual(archived["is_active"], 0)

    async def test_user_upsert_rejects_unknown_sql_identifier(self):
        with self.assertRaises(ValueError):
            await self.db.upsert_user(202, {"name=role": "admin"})
        self.assertIsNone(await self.db.get_user(202))

    async def test_student_update_validates_fields_and_empty_update(self):
        await self.db.add_student(303, {"name": "Дана", "class_name": "7А"})
        student = (await self.db.get_students(303))[0]

        await self.db.update_student(student["id"], {})
        await self.db.update_student(student["id"], {"notes": "Добавлена"})
        updated = await self.db.get_student(student["id"])
        self.assertEqual(updated["notes"], "Добавлена")

        with self.assertRaises(ValueError):
            await self.db.update_student(student["id"], {"notes=class_name": "8Б"})
        unchanged = await self.db.get_student(student["id"])
        self.assertEqual(unchanged["class_name"], "7А")

    async def test_student_records_are_scoped_to_their_owner(self):
        await self.db.add_student(404, {"name": "Әлия", "class_name": "5Б"})
        student = (await self.db.get_students(404))[0]
        student_id = student["id"]

        self.assertIsNone(await self.db.get_student_for_teacher(student_id, 405))
        self.assertFalse(await self.db.update_student_for_teacher(student_id, 405, {"name": "Бөтен"}))
        self.assertFalse(await self.db.delete_student_for_teacher(student_id, 405))
        self.assertEqual((await self.db.get_student_for_teacher(student_id, 404))["name"], "Әлия")

        self.assertTrue(await self.db.update_student_for_teacher(student_id, 404, {"name": "Әлия А."}))
        self.assertTrue(await self.db.delete_student_for_teacher(student_id, 404))
        self.assertIsNone(await self.db.get_student_for_teacher(student_id, 404))

    async def test_funnel_events_are_recorded_and_counted_by_type(self):
        await self.db.log_funnel_event(501, "start")
        await self.db.log_funnel_event(501, "doc_type_chosen", "lesson_plan")
        await self.db.save_document(501, "lesson_plan", "КСП", "готовый документ", 90)

        counts = await self.db.get_funnel_event_counts()
        self.assertEqual(
            {(row["event"], row["doc_type"]): row["count"] for row in counts},
            {("start", ""): 1, ("doc_type_chosen", "lesson_plan"): 1,
             ("doc_generated", "lesson_plan"): 1},
        )
        with self.assertRaises(ValueError):
            await self.db.log_funnel_event(501, "unknown")

    async def test_document_and_generation_event_are_saved_together(self):
        document_id = await self.db.save_document(502, "characteristic", "Характеристика", "текст", 95)
        document = await self.db.get_document(document_id)
        self.assertEqual(document["teacher_id"], 502)
        counts = await self.db.get_funnel_event_counts()
        self.assertIn(
            {"event": "doc_generated", "doc_type": "characteristic", "count": 1}, counts
        )

    async def test_first_document_reminder_is_due_once_only_for_eligible_users(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)).isoformat()
        await self.db.upsert_user(601, {"name": "Кандидат", "created_at": old})
        await self.db.upsert_user(602, {"name": "Уже создал", "created_at": old})
        await self.db.save_document(602, "lesson_plan", "КСП", "content", 0)
        await self.db.upsert_user(603, {"name": "Отключил", "created_at": old, "notifications_enabled": 0})

        due = await self.db.get_users_due_first_doc_reminder()
        self.assertEqual([user["tg_id"] for user in due], [601])
        self.assertTrue(await self.db.mark_first_doc_reminder_sent(601))
        self.assertFalse(await self.db.mark_first_doc_reminder_sent(601))
        self.assertEqual(await self.db.get_users_due_first_doc_reminder(), [])

    async def test_general_reminders_skip_users_without_a_first_document(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)).isoformat()
        await self.db.upsert_user(701, {"name": "Без документа", "created_at": old})
        await self.db.upsert_user(702, {"name": "С документом", "created_at": old, "free_used": 1})
        await self.db.save_document(702, "lesson_plan", "КСП", "готово", 90)

        reminder_ids = {user["tg_id"] for user in await self.db.get_users_for_notification()}
        broadcast_ids = {user["tg_id"] for user in await self.db.get_users_for_broadcast()}
        self.assertNotIn(701, reminder_ids)
        self.assertNotIn(701, broadcast_ids)
        self.assertIn(702, reminder_ids)
        self.assertIn(702, broadcast_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
