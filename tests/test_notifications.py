"""First-document reminder checks. Run with: python tests/test_notifications.py"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from handlers.notifications import send_first_doc_reminders


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeDB:
    def __init__(self, users, memories=None):
        self.users = users
        self.memories = memories or {}
        self.marked = []

    async def get_users_due_first_doc_reminder(self):
        return self.users

    async def get_agent_context(self, tg_id):
        return self.memories.get(tg_id, {})

    async def mark_first_doc_reminder_sent(self, tg_id):
        self.marked.append(tg_id)
        return True


class FirstDocReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_localized_one_time_reminder_and_respects_opt_out(self):
        db = FakeDB([
            {"tg_id": 801, "lang": "kz", "role": "kindergarten", "notifications_enabled": 1},
            {"tg_id": 802, "lang": "ru", "role": "teacher", "notifications_enabled": 0},
        ])
        bot = FakeBot()
        sent = await send_first_doc_reminders(types.SimpleNamespace(bot=bot), db)

        self.assertEqual(sent, 1)
        self.assertIn("алғашқы құжатыңызды", bot.sent[0]["text"])
        button = bot.sent[0]["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "menu_create")
        self.assertEqual(db.marked, [801])

    async def test_respects_agent_reminder_opt_out(self):
        db = FakeDB(
            [{"tg_id": 803, "lang": "ru", "notifications_enabled": 1}],
            memories={803: {"reminders_enabled": False}},
        )
        bot = FakeBot()
        sent = await send_first_doc_reminders(types.SimpleNamespace(bot=bot), db)
        self.assertEqual(sent, 0)
        self.assertEqual(bot.sent, [])
        self.assertEqual(db.marked, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
