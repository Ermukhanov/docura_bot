"""Focused persistence tests. Run with: python tests/test_database.py"""
import asyncio
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
