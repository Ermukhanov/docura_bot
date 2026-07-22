import asyncio
import logging
import random
import json
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode
from database import Database

logger = logging.getLogger(__name__)


def _smart_reminder(user: dict, lang: str):
    """Возвращает персональное напоминание и задержку после последнего документа."""
    doc_type = (user.get("last_doc_type") or "").lower()
    if "lesson_plan" in doc_type or "calendar_plan" in doc_type:
        return ("Нужен новый КСП на следующую неделю?" if lang == "ru" else "Жаңа аптаға ҚМЖ керек пе?", 6,
                [("Создать КСП" if lang == "ru" else "ҚМЖ жасау", "doc_lesson_plan"),
                 ("Другой документ" if lang == "ru" else "Басқа құжат", "menu_create")])
    if "kindergarten_cycle_schedule" in doc_type:
        return ("Новая неделя — нужна циклограмма?" if lang == "ru" else "Жаңа апта — циклограмма керек пе?", 6,
                [("Создать циклограмму" if lang == "ru" else "Циклограмма жасау", "doc_kindergarten_cycle_schedule"),
                 ("Другой документ" if lang == "ru" else "Басқа құжат", "menu_create")])
    if "characteristic" in doc_type or "kg_child_characteristic" in doc_type:
        return ("Нужны документы по ученикам?" if lang == "ru" else "Оқушылар бойынша құжат керек пе?", 14,
                [("Создать документ" if lang == "ru" else "Құжат жасау", "menu_create"),
                 ("Не напоминать" if lang == "ru" else "Еске салмау", "agent_reminders_off")])
    if "monthly_report" in doc_type or "kg_monthly_report" in doc_type:
        return ("Конец месяца — нужен отчёт?" if lang == "ru" else "Ай соңы — есеп керек пе?", 25,
                [("Создать отчёт" if lang == "ru" else "Есеп жасау", "menu_create"),
                 ("Позже" if lang == "ru" else "Кейін", "agent_remind_later")])
    return None


def _due_since_last_document(user: dict, days: int) -> bool:
    value = user.get("last_doc_date")
    if not value:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(value)).total_seconds() >= days * 86400
    except (TypeError, ValueError):
        return True


def _monday_lessons(schedule_json: str):
    try:
        schedule = json.loads(schedule_json) if schedule_json else {}
        lessons = schedule.get("Понедельник", []) if isinstance(schedule, dict) else []
        return [lesson for lesson in lessons if isinstance(lesson, dict)]
    except (TypeError, ValueError):
        return []

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
                    memory = await db.get_agent_context(tg_id)
                    # Пустая память сохраняет прежнее поведение; отключение действует
                    # только после явного выбора пользователя.
                    if memory.get("reminders_enabled") is False and "reminders_enabled" in memory:
                        continue

                    # Бесплатный план сохраняет прежние универсальные напоминания.
                    # Персональные сценарии доступны пользователям PRO.
                    smart = _smart_reminder(user, lang) if user.get("subscribed") else None
                    if smart and not _due_since_last_document(user, smart[1]):
                        continue

                    # PRO-предложение автогенерации: один раз в воскресный вечер,
                    # только при включённой настройке и сохранённом расписании.
                    is_sunday_evening = datetime.now().weekday() == 6 and datetime.now().hour >= 18
                    auto_week = datetime.now().strftime("%G-W%V")
                    if (is_sunday_evening and user.get("subscribed") and user.get("auto_generate")
                            and memory.get("auto_generation_prompt_week") != auto_week):
                        schedule_json = await db.get_schedule(tg_id)
                        if user.get("role") == "kindergarten" and schedule_json:
                            text = ("Новая неделя начинается.\nСоздать циклограмму автоматически?" if lang == "ru"
                                    else "Жаңа апта басталады.\nЦиклограмманы автоматты түрде жасау керек пе?")
                            keyboard = [[InlineKeyboardButton("Да, создать" if lang == "ru" else "Иә, жасау", callback_data="agent_auto_generate_cycle")],
                                        [InlineKeyboardButton("Нет спасибо" if lang == "ru" else "Жоқ, рақмет", callback_data="agent_skip")]]
                        else:
                            lessons = _monday_lessons(schedule_json)
                            if not lessons:
                                continue
                            first = lessons[0]
                            subject, class_name = first.get("subject", ""), first.get("class", "")
                            text = (f"Завтра у вас {subject} в {class_name}.\nСоздать КСП автоматически?" if lang == "ru"
                                    else f"Ертең сізде {subject}, {class_name}.\nҚМЖ-ны автоматты түрде жасау керек пе?")
                            keyboard = [[InlineKeyboardButton("Да, создать" if lang == "ru" else "Иә, жасау", callback_data="agent_auto_generate_ksp")],
                                        [InlineKeyboardButton("Нет спасибо" if lang == "ru" else "Жоқ, рақмет", callback_data="agent_skip")]]
                        await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
                        await db.update_agent_context(tg_id, {"auto_generation_prompt_week": auto_week})
                        await asyncio.sleep(0.5)
                        continue

                    if smart:
                        text, _, buttons = smart
                        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)] for label, callback in buttons])
                    elif concierge is not None:
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

                    if not smart:
                        keyboard = None

                    await app.bot.send_message(
                        chat_id=tg_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard,
                    )
                    await db.update_notified(tg_id)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Reminder error for {user['tg_id']}: {e}")
        except Exception as e:
            logger.error(f"Reminder scheduler error: {e}")

        # Проверять раз в сутки
        await asyncio.sleep(86400)
