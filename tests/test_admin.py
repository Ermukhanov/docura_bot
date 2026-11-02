import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.admin import AdminHandler


class AdminCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthorized_callback_is_answered_once(self):
        query = SimpleNamespace(data="admin_stats", answer=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123456),
        )

        with patch("handlers.admin.ADMIN_IDS", set()):
            await AdminHandler(db=None).callback(update, SimpleNamespace(user_data={}))

        query.answer.assert_awaited_once_with("⛔ Нет доступа", show_alert=True)

    async def test_sample_delete_callback_is_answered_once(self):
        query = SimpleNamespace(data="admin_sample_del_42", answer=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123456),
        )
        db = SimpleNamespace(delete_sample=AsyncMock())
        handler = AdminHandler(db=db)
        handler._show_samples_list = AsyncMock()

        with patch("handlers.admin.is_admin", return_value=True):
            await handler.callback(update, SimpleNamespace(user_data={}))

        query.answer.assert_awaited_once_with()
        db.delete_sample.assert_awaited_once_with(42)
        handler._show_samples_list.assert_awaited_once_with(query)


if __name__ == "__main__":
    unittest.main()
