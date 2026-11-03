import types
import unittest
from unittest.mock import AsyncMock

from handlers.profile import ProfileHandler


class FakeDatabase:
    async def get_user(self, _user_id):
        return {"lang": "ru", "role": "teacher"}


class StudentAbsenceInputTests(unittest.IsolatedAsyncioTestCase):
    async def _submit(self, value):
        message = types.SimpleNamespace(text=value, reply_text=AsyncMock())
        update = types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=123),
            effective_message=message,
            message=message,
        )
        context = types.SimpleNamespace(user_data={
            "step": "student_absences",
            "new_student": {"name": "Айжан", "class_name": "7А"},
        })
        await ProfileHandler(FakeDatabase(), "").handle_text(update, context)
        return message, context

    async def test_invalid_or_negative_absence_count_keeps_the_step_open(self):
        for value in ("много", "-2"):
            with self.subTest(value=value):
                message, context = await self._submit(value)
                self.assertEqual(context.user_data["step"], "student_absences")
                self.assertNotIn("absences", context.user_data["new_student"])
                self.assertIn("целое неотрицательное число", message.reply_text.await_args.args[0])

    async def test_zero_is_accepted_as_a_valid_absence_count(self):
        message, context = await self._submit("0")

        self.assertEqual(context.user_data["new_student"]["absences"], 0)
        self.assertEqual(context.user_data["step"], "student_behavior")
        message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
