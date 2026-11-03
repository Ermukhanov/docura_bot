import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers.profile import ProfileHandler
from telegram.constants import ParseMode


class StudentDetailMarkdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_student_detail_escapes_user_values_for_telegram_markdown(self):
        db = SimpleNamespace(get_student_for_teacher=AsyncMock(return_value={
            "name": "A_B*`[C]",
            "class_name": "7_A",
            "behavior": "good_*",
            "absences": 0,
            "parents": "Parent_[Name]",
            "parent_phone": "+7*123",
            "birth_date": "2000_01_01",
            "grades": json.dumps({"Math_*": "5*"}),
            "achievements": json.dumps(["Prize_[one]"]),
        }))
        query = SimpleNamespace(edit_message_text=AsyncMock())

        await ProfileHandler(db, "")._show_student_detail(query, 10, 1, "ru")

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("A\\_B\\*\\`\\[C]", text)
        self.assertIn("7\\_A", text)
        self.assertIn("good\\_\\*", text)
        self.assertIn("Parent\\_\\[Name]", text)
        self.assertIn("Math\\_\\*", text)
        self.assertEqual(query.edit_message_text.await_args.kwargs["parse_mode"], ParseMode.MARKDOWN)
