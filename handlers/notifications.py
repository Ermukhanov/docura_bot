import os
import asyncio
import logging
import random
import json
from datetime import datetime, timedelta
import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode
from database import Database
from handlers.chat_utils import now_local, LOCAL_TZ

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


SEND_WINDOW_START, SEND_WINDOW_END = 10, 19  # местное время, часы


def _seconds_until_send_window() -> float:
    """0, если сейчас окно отправки; иначе секунд до его начала (с запасом до 10:00)."""
    now = now_local()
    if SEND_WINDOW_START <= now.hour < SEND_WINDOW_END:
        return 0
    start = now.replace(hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0)
    if now.hour >= SEND_WINDOW_END:
        start += timedelta(days=1)
    return max(60.0, (start - now).total_seconds())


def _due_since_last_document(user: dict, days: int) -> bool:
    value = user.get("last_doc_date")
    if not value:
        return True
    try:
        last = datetime.fromisoformat(value)
        if last.tzinfo is None:
            last = last.replace(tzinfo=LOCAL_TZ)
        return (now_local() - last).total_seconds() >= days * 86400
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
        # Напоминания только днём по местному времени: раньше цикл стартовал в момент
        # запуска бота (деплой ночью => сообщения в 3 часа ночи) и шёл по UTC.
        await asyncio.sleep(_seconds_until_send_window())
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
                    is_sunday_evening = now_local().weekday() == 6 and 18 <= now_local().hour < 21
                    auto_week = now_local().strftime("%G-W%V")
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

                    try:
                        await app.bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=keyboard,
                        )
                    except Exception as parse_err:
                        if "parse entities" in str(parse_err).lower() or "can't parse entities" in str(parse_err).lower():
                            await app.bot.send_message(
                                chat_id=tg_id,
                                text=text,
                                reply_markup=keyboard,
                            )
                        else:
                            raise
                    await db.update_notified(tg_id)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Reminder error for {user['tg_id']}: {e}")
        except Exception as e:
            logger.error(f"Reminder scheduler error: {e}")

        # Следующая проверка — завтра в начале окна отправки
        tomorrow = (now_local() + timedelta(days=1)).replace(hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0)
        await asyncio.sleep(max(3600.0, (tomorrow - now_local()).total_seconds()))


async def check_subscription_expirations(app: Application, db: Database):
    """
    Отдельный фоновый цикл: снимает подписку PRO у тех, кто оплачивал сам через
    бота (чек в profile.py), когда истёк срок 30 дней с последней оплаты, и просит
    оплатить заново по полной цене (акция уже использована — см. promo_used).

    ВАЖНО: НЕ трогает подписки без установленного срока (sub_expires IS NULL) —
    это B2B-партнёры (школы/садики), активированные вручную через админку.
    См. database.py get_expired_subscriptions() — она сама фильтрует только
    подписки с явным сроком.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    while True:
        try:
            expired_users = await db.get_expired_subscriptions()
            for user in expired_users:
                try:
                    tg_id = user["tg_id"]
                    lang  = user.get("lang", "ru")

                    await db.deactivate_subscription(tg_id)

                    price = 4990  # полная цена — акция первого месяца одноразовая
                    text = (
                        f"⏳ Срок подписки Docura PRO истёк.\n\n"
                        f"Чтобы продолжить безлимитную генерацию документов, оформите "
                        f"подписку заново — {price} тг/мес."
                    ) if lang == "ru" else (
                        f"⏳ Docura PRO жазылымының мерзімі аяқталды.\n\n"
                        f"Шексіз құжат жасауды жалғастыру үшін жазылымды қайта рәсімдеңіз "
                        f"— {price} тг/ай."
                    )
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                        "⭐ Продлить подписку" if lang == "ru" else "⭐ Жазылымды жалғастыру",
                        callback_data="prof_sub"
                    )]])
                    await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=kb)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Subscription expiration error for {user['tg_id']}: {e}")
        except Exception as e:
            logger.error(f"Subscription expiration scheduler error: {e}")

        # Проверяем раз в час — срок истекает по дате, но не хотим держать людей
        # без доступа сутками, если истечение случилось рано утром
        await asyncio.sleep(3600)


async def monitor_schedules(app: Application, db: Database, anthropic_key: str):
    """
    Фоновый цикл мониторинга расписания: выполняется каждые 6 часов.
    1. Проверяет расписание каждого PRO-пользователя.
    2. Определяет предстоящие занятия по расписанию (на сегодня или завтра).
    3. Обязательно проверяет базу учеников / воспитанников перед генерацией.
    4. Если включена автогенерация (auto_generate == 1) — ИИ-агент САМ формирует
       документ (КСП / циклограмму) и присылает готовый файл Word пользователю без сообщений от него.
    5. Если автогенерация выключена — напоминает о предстоящем уроке и предлагает создать документ в 1 клик.
    """
    logger.info("📅 Цикл мониторинга расписаний каждые 6 часов запущен")
    await asyncio.sleep(60)

    while True:
        try:
            users_with_sched = await db.get_users_with_schedules()
            now = now_local()
            days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

            # Если после 14:00, ориентируемся на завтрашний день, иначе на текущий
            target_idx = (now.weekday() + 1) % 7 if now.hour >= 14 else now.weekday()
            target_day = days_ru[target_idx]

            for user in users_with_sched:
                try:
                    tg_id = user["tg_id"]
                    if not user.get("subscribed"):
                        continue

                    lang = user.get("lang", "ru")
                    is_kg = user.get("role") == "kindergarten"
                    schedule_raw = user.get("schedule_data")
                    if not schedule_raw:
                        continue

                    schedule = json.loads(schedule_raw) if isinstance(schedule_raw, str) else schedule_raw
                    lessons = schedule.get(target_day, []) if isinstance(schedule, dict) else []

                    check_day = target_day
                    if not lessons and target_idx in (5, 6):
                        check_day = "Понедельник"
                        lessons = schedule.get("Понедельник", [])

                    if not lessons:
                        continue

                    first_lesson = next((item for item in lessons if isinstance(item, dict)), {})
                    subject = first_lesson.get("subject") or user.get("subject", "")
                    class_name = first_lesson.get("class") or user.get("classes", "") or user.get("age_group", "")

                    dedup_key = f"sched_mon_{check_day}_{now.strftime('%G_W%V')}"
                    memory = await db.get_agent_context(tg_id)
                    if memory.get("last_sched_mon_key") == dedup_key:
                        continue

                    # Проверяем базу учеников или воспитанников
                    students = await db.get_students(tg_id)
                    class_students = [s["name"] for s in students if not class_name or s.get("class_name") == class_name]
                    if not class_students and students:
                        class_students = [s["name"] for s in students]

                    auto_gen = bool(user.get("auto_generate"))

                    if auto_gen and anthropic_key:
                        # АВТОГЕНЕРАЦИЯ: ИИ агент формирует документ сам и присылает .docx
                        doc_type = "kindergarten_cycle_schedule" if is_kg else "lesson_plan"
                        doc_name = (
                            ("Циклограмма" if lang == "ru" else "Циклограмма") if is_kg
                            else ("Краткосрочный план (КСП)" if lang == "ru" else "Қысқамерзімді жоспар (ҚМЖ)")
                        )

                        client = anthropic.AsyncAnthropic(api_key=anthropic_key)
                        students_ctx = f"Ученики/воспитанники из базы: {', '.join(class_students[:20])}" if class_students else "Ученики: группа по списку"

                        prompt = (
                            f"Составь официальный качественный {doc_name} по стандартам РК для {'детского сада' if is_kg else 'школы'}.\n"
                            f"День недели: {check_day}\n"
                            f"Предмет/направление: {subject}\n"
                            f"Класс/группа: {class_name}\n"
                            f"{students_ctx}\n"
                            f"Организация: {user.get('school', '')}\n"
                            f"Педагог: {user.get('name', '')}\n"
                            f"Язык документа: {lang}\n"
                            f"Верни готовый полный текст документа структурированно по разделам."
                        )

                        msg = await client.messages.create(
                            model="claude-haiku-4-5",
                            max_tokens=2500,
                            messages=[{"role": "user", "content": prompt}]
                        )
                        content = msg.content[0].text

                        from handlers.word_generator import generate_word
                        word_path = generate_word(
                            content=content,
                            title=doc_name,
                            teacher_name=user.get("name", ""),
                            director_name=user.get("director", ""),
                            lang=lang
                        )

                        caption = (
                            f"🤖 *Docura AI — автогенерация по расписанию*\n\n"
                            f"📅 *{check_day}*: {subject} ({class_name})\n"
                            f"👥 Учтены ученики из базы: {len(class_students)} чел.\n\n"
                            f"✅ Ваш документ сформирован и готов к уроку!"
                        ) if lang == "ru" else (
                            f"🤖 *Docura AI — кесте бойынша авто-жасау*\n\n"
                            f"📅 *{check_day}*: {subject} ({class_name})\n"
                            f"👥 Базадағы оқушылар ескерілді: {len(class_students)} адам\n\n"
                            f"✅ Құжат сабаққа дайын!"
                        )

                        with open(word_path, "rb") as f:
                            await app.bot.send_document(
                                chat_id=tg_id,
                                document=f,
                                filename=f"{doc_name}_{now.strftime('%d%m%Y')}.docx",
                                caption=caption,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        try:
                            os.remove(word_path)
                        except:
                            pass

                        await db.save_document(tg_id, doc_type, doc_name, content, 90)
                        await db.update_agent_context(tg_id, {"last_sched_mon_key": dedup_key})
                        logger.info("Auto-generated %s for %s", doc_type, tg_id)

                    else:
                        prompt_text = (
                            f"📅 *По вашему расписанию на {check_day}:*\n"
                            f"• {subject} ({class_name})\n"
                            f"👥 Учеников в базе: {len(class_students)}\n\n"
                            f"Создать документ автоматически?"
                        ) if lang == "ru" else (
                            f"📅 *Кестеңіз бойынша ({check_day}):*\n"
                            f"• {subject} ({class_name})\n"
                            f"👥 Базадағы оқушылар: {len(class_students)}\n\n"
                            f"Құжатты автоматты жасау керек пе?"
                        )
                        call_key = "agent_auto_generate_cycle" if is_kg else "agent_auto_generate_ksp"
                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("Да, создать" if lang == "ru" else "Иә, жасау", callback_data=call_key)],
                            [InlineKeyboardButton("Нет, спасибо" if lang == "ru" else "Жоқ, рақмет", callback_data="agent_skip")]
                        ])
                        await app.bot.send_message(chat_id=tg_id, text=prompt_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                        await db.update_agent_context(tg_id, {"last_sched_mon_key": dedup_key})

                    await asyncio.sleep(0.5)

                except Exception as user_err:
                    logger.warning(f"Error checking schedule for user {user.get('tg_id')}: {user_err}")

        except Exception as e:
            logger.error(f"Schedule monitor loop error: {e}")

        # Цикл каждые 6 часов
        await asyncio.sleep(6 * 3600)

