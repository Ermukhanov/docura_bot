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


if __name__ == "__main__":
    unittest.main(verbosity=2)
