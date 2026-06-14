import asyncio
import logging
from telegram.ext import Application
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

logger = logging.getLogger(__name__)

async def send_reminders(app: Application, db: Database):
    """Отправляет напоминания пользователям которые давно не создавали документы"""
    while True:
        try:
            users = await db.get_users_for_notification()
            for user in users:
                try:
                    tg_id = user["tg_id"]
                    lang  = user.get("lang", "ru")
                    name  = (user.get("name") or "").split()[0]
                    await app.bot.send_message(
                        chat_id=tg_id,
                        text=t(lang, "notif_reminder", name=name),
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await db.update_notified(tg_id)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Reminder error for {user['tg_id']}: {e}")
        except Exception as e:
            logger.error(f"Reminder scheduler error: {e}")

        # Проверять раз в сутки
        await asyncio.sleep(86400)
