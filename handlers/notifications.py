import asyncio
import logging
import random
from telegram.ext import Application
from telegram.constants import ParseMode
from database import Database

logger = logging.getLogger(__name__)

async def send_reminders(app: Application, db: Database, concierge=None):
    """Отправляет напоминания пользователям которые давно не создавали документы.
    Текст теперь пишет разговорный агент (приветствие, мягкое напоминание, и — для
    PRO — проактивное предложение по завтрашнему расписанию). Если агент не настроен
    (нет CONCIERGE_API_KEY), используется прежний статичный текст — бот не ломается."""
    while True:
        try:
            users = await db.get_users_for_notification()
            for user in users:
                try:
                    tg_id = user["tg_id"]
                    lang  = user.get("lang", "ru")

                    if concierge is not None:
                        text, pending = await concierge.build_reminder(user, lang)
                        if pending:
                            state = await concierge._load_state(tg_id)
                            state["pending"] = pending
                            await concierge._save_state(tg_id, state)
                    else:
                        name = (user.get("name") or "").split()[0]
                        variants = {
                            "ru": [
                                f"{name}, на этой неделе ещё не создавали документы. Нужна помощь? 📄",
                                f"{name}, быстро создайте нужный документ — это займёт меньше минуты 🚀",
                                f"{name}, не забудьте про документы! Я помогу сделать всё быстро ✅",
                            ],
                            "kz": [
                                f"{name}, осы аптада әлі құжат жасамадыңыз. Көмек керек пе? 📄",
                                f"{name}, қажетті құжатты жылдам жасаңыз — бір минуттан аз уақыт кетеді 🚀",
                                f"{name}, құжаттарды ұмытпаңыз! Мен жылдам көмектесемін ✅",
                            ],
                        }
                        text = random.choice(variants.get(lang, variants["ru"]))

                    await app.bot.send_message(
                        chat_id=tg_id,
                        text=text,
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
