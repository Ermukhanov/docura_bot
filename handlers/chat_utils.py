"""
Общие помощники для чата: местное время и индикатор «печатает…».

ВРЕМЯ. Railway (и почти любой хостинг) работает в UTC, а Казахстан — UTC+5.
datetime.now() на сервере давал время на 5 часов раньше, поэтому в 2–3 часа ночи
по Актобе бот видел «21–22 часа» и здоровался «Добрый вечер»/«Добрый день».
Теперь всё, что зависит от времени суток, считается через now_local().
Часовой пояс можно изменить переменной окружения BOT_TZ (по умолчанию Asia/Almaty, UTC+5).
"""

import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from telegram.constants import ChatAction

try:
    from zoneinfo import ZoneInfo
    try:
        LOCAL_TZ = ZoneInfo(os.getenv("BOT_TZ", "Asia/Almaty"))
    except Exception:
        LOCAL_TZ = timezone(timedelta(hours=5))
except Exception:  # на очень старых окружениях без zoneinfo
    LOCAL_TZ = timezone(timedelta(hours=5))


def now_local() -> datetime:
    """Текущее время в Казахстане (aware-datetime)."""
    return datetime.now(LOCAL_TZ)


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    if 18 <= hour < 23:
        return "evening"
    return "night"


# Приветствия по времени суток. Ночью «Добрый день/вечер» неуместны — нейтральное «Здравствуйте».
GREETINGS = {
    "ru": {"morning": "Доброе утро", "day": "Добрый день", "evening": "Добрый вечер", "night": "Здравствуйте"},
    "kz": {"morning": "Қайырлы таң", "day": "Қайырлы күн", "evening": "Қайырлы кеш", "night": "Сәлеметсіз бе"},
}


def greeting_word(lang: str, when: datetime | None = None) -> str:
    when = when or now_local()
    table = GREETINGS.get(lang, GREETINGS["ru"])
    return table[part_of_day(when.hour)]


@asynccontextmanager
async def typing_action(bot, chat_id, action: str = ChatAction.TYPING, interval: float = 4.0):
    """Показывает «печатает…» всё время, пока выполняется блок.

    Telegram гасит индикатор через ~5 секунд, поэтому он переотправляется в цикле.
    Любые ошибки сети игнорируются — индикатор не должен ломать ответ.

        async with typing_action(context.bot, chat_id):
            reply = await call_ai(...)
    """
    stop = asyncio.Event()

    async def _loop():
        while not stop.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action=action)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            task.cancel()
