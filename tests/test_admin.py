import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.admin import AdminHandler, _funnel_step_label


class AdminCallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_funnel_step_is_shown_as_a_readable_stage(self):
        self.assertEqual(
            _funnel_step_label("document_lesson_plan_question_2_of_3"),
            "Краткосрочный план (КСП), вопрос 2 из 3",
        )

    async def test_admin_document_view_sends_stored_document_content(self):
        db = SimpleNamespace(get_document=AsyncMock(return_value={
            "id": 12, "teacher_id": 123, "doc_name": "КСП", "content": "Полный текст документа",
        }))
        query = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))

        await AdminHandler(db=db)._show_document(query, 12)

        query.message.reply_text.assert_awaited_once()
        self.assertIn("Полный текст документа", query.message.reply_text.await_args.args[0])

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
