"""Воронка и опрос ушедших."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import Database, free_limit_for


class FunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database()
        self.db.db_path = os.path.join(self.tmp.name, "t.db")
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_report_counts_each_stage(self):
        await self.db.log_funnel_event(9, "start")
        await self.db.upsert_user(1, {"lang": "ru"})
        await self.db.upsert_user(2, {"lang": "ru", "role": "teacher", "funnel_step": "registration_question_2"})
        await self.db.log_funnel_event(2, "role_chosen", "teacher")
        await self.db.upsert_user(3, {"lang": "kz", "role": "teacher", "name": "Айгуль"})
        await self.db.log_funnel_event(3, "doc_type_chosen", "lesson_plan")
        await self.db.save_document(3, "lesson_plan", "КСП", "текст", 90)
        await self.db.log_funnel_event(2, "generation_failed", "lesson_plan")
        r = await self.db.get_funnel_report(30)
        self.assertEqual((r["started"], r["lang"], r["role"], r["chose_doc"], r["doc1"], r["doc2"]), (4, 3, 2, 1, 1, 0))
        self.assertEqual(r["problems"].get("generation_failed"), 1)
        steps = {row["step"]: row["c"] for row in r["stuck"]}
        self.assertEqual(steps.get("registration_question_2"), 1)
        self.assertNotIn(3, [row for row in r["stuck"]])

    async def test_feedback_bonus_only_once_and_winback_targets_users_without_docs(self):
        import aiosqlite
        for uid in (10, 11):
            await self.db.upsert_user(uid, {"lang": "ru"})
        await self.db.save_document(11, "lesson_plan", "КСП", "т", 90)
        async with aiosqlite.connect(self.db.db_path) as c:
            await c.execute("UPDATE users SET created_at=datetime('now','-3 days')")
            await c.commit()
        cands = [u["tg_id"] for u in await self.db.get_winback_candidates()]
        self.assertEqual(cands, [10])
        await self.db.mark_winback_sent(10)
        self.assertEqual(await self.db.get_winback_candidates(), [])
        self.assertTrue(await self.db.save_feedback(10, "long"))
        self.assertFalse(await self.db.save_feedback(10, "text", "долго"))
        user = await self.db.get_user(10)
        self.assertEqual(free_limit_for(user) - free_limit_for({}), 2)
        r = await self.db.get_funnel_report(None)
        self.assertEqual({x["reason"]: x["c"] for x in r["feedback"]}, {"long": 1, "text": 1})
        self.assertEqual(r["feedback_texts"][0]["text"], "долго")


if __name__ == "__main__":
    unittest.main()
