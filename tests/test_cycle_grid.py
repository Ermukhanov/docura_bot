"""Циклограмма: план от ИИ попадает в таблицу, пустые поля без «None»."""
import asyncio
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from handlers.documents import cycle_grid_from_ai, cycle_grid_text, profile_missing  # noqa: E402


class FakeClient:
    def __init__(self, text):
        self.messages = types.SimpleNamespace(create=self._create)
        self.text = text

    async def _create(self, **kwargs):
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self.text)])


def test_grid_parsed_and_validated():
    rows = [[f"b{b}d{d}" for d in range(5)] for b in range(5)]
    grid = asyncio.run(cycle_grid_from_ai(FakeClient("Вот план:\n" + json.dumps({"rows": rows})), {"week_topic": "Қыс"}, {}, "kz"))
    assert grid == rows
    assert "Серуен" in cycle_grid_text(grid, "kz")
    bad = asyncio.run(cycle_grid_from_ai(FakeClient('{"rows": [["a"]]}'), {}, {}, "ru"))
    assert bad is None


def test_profile_missing():
    assert profile_missing({"name": "None", "school": "Сад", "director": "Х"})
    assert not profile_missing({"name": "Айгуль", "school": "Сад №5", "director": "Сауле Б."})
