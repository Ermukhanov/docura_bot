import unittest
from datetime import datetime, timezone
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from telegram import Chat, Message, Update, User
from telegram.error import NetworkError

import bot


class ErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_details_go_to_admin_only(self):
        user_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=123, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Teacher"),
            text="Create document",
        )
        update = Update(update_id=1, message=user_message)
        admin_send = AsyncMock()
        context = SimpleNamespace(
            error=NetworkError("private transport detail"),
            bot=SimpleNamespace(send_message=admin_send),
        )

        with (
            patch.object(bot, "ADMIN_IDS", {999}),
            patch.object(bot, "_last_admin_alert", 0),
            patch.object(bot.time, "time", return_value=1000),
            patch.object(bot.logger, "error"),
            patch.object(Message, "reply_text", new_callable=AsyncMock) as client_send,
        ):
            await bot.on_error(update, context)

        client_send.assert_awaited_once()
        self.assertNotIn("private transport detail", client_send.await_args.args[0])
        admin_send.assert_awaited_once()
        self.assertEqual(admin_send.await_args.args[0], 999)
        self.assertIn("private transport detail", admin_send.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
