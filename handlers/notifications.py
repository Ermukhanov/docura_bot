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
from handlers.rag_base import ensure_ai_disclaimer

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


TARGET_HOUR = 18  # Каждый день ровно в 18:00 по местному времени


def _seconds_until_daily_18():
    """Вычисляет количество секунд до ближайших 18:00 по местному времени (UTC+5)."""
    now = now_local()
    target = now.replace(hour=TARGET_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _due_since_last_active_or_notified(user: dict, min_days: int = 3) -> bool:
    """Проверяет, что пользователь не проявлял активности 3+ дней и ему не слали напоминание 3+ дней."""
    now = now_local()
    notified_str = user.get("notified_at")
    if notified_str:
        try:
            last_n = datetime.fromisoformat(notified_str)
            if last_n.tzinfo is None:
                last_n = last_n.replace(tzinfo=LOCAL_TZ)
            if (now - last_n).total_seconds() < min_days * 86400:
                return False
        except Exception:
            pass

    active_str = user.get("last_active_at") or user.get("last_doc_date") or user.get("created_at")
    if not active_str:
        return True
    try:
        last_a = datetime.fromisoformat(active_str)
        if last_a.tzinfo is None:
            last_a = last_a.replace(tzinfo=LOCAL_TZ)
        return (now - last_a).total_seconds() >= min_days * 86400
    except Exception:
        return True


def _tomorrow_event_info(schedule_json: str, is_kg: bool):
    """Возвращает информацию о первом занятии/событии на завтрашний день."""
    try:
        schedule = json.loads(schedule_json) if schedule_json else {}
        if not isinstance(schedule, dict):
            return None
        days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        now = now_local()
        tomorrow_idx = (now.weekday() + 1) % 7
        tomorrow_day = days_ru[tomorrow_idx]

        items = schedule.get(tomorrow_day, [])
        if not items and tomorrow_idx in (5, 6):
            tomorrow_day = "Понедельник"
            items = schedule.get("Понедельник", [])
        if not items or not isinstance(items, list):
            return None

        first = next((x for x in items if isinstance(x, dict)), None)
        if not first:
            return None

        subject = first.get("subject", "")
        class_name = first.get("class", "")
        if not subject and not class_name:
            return None
        event_str = f"{subject} ({class_name})".strip(" ()")
        return {"event": event_str, "day": tomorrow_day, "subject": subject, "class": class_name}
    except Exception:
        return None


async def send_reminders(app: Application, db: Database, concierge=None):
    """
    Ежедневный планировщик удержания и уведомлений (18:00 по местному времени):
    1. Проверяет пользователей со включёнными уведомлениями (notifications_enabled).
    2. Если у пользователя завтра событие по расписанию:
       «Завтра у вас [событие]. Подготовить черновики документов?» с кнопками «Да / Нет».
    3. Если пользователь не писал боту 3+ дней:
       «Не заходили неделю. Сделать отчёт за 1 минуту?» (не чаще 1 раза в 3 дня).
    4. Раз в 2 недели: рассылка новостей и обновлений шаблонов.
    Все сообщения тёплые, короткие, на казахском/русском, заканчиваются вопросом.
    """
    logger.info("🔔 Ежедневный планировщик уведомлений (18:00) активен")
    while True:
        wait_seconds = _seconds_until_daily_18()
        await asyncio.sleep(wait_seconds)

        try:
            logger.info("⏰ Запуск ежедневного сканирования базы в 18:00")
            users = await db.get_users_for_notification()

            for user in users:
                try:
                    tg_id = user["tg_id"]
                    if user.get("notifications_enabled") == 0:
                        continue

                    memory = await db.get_agent_context(tg_id)
                    if memory.get("reminders_enabled") is False:
                        continue

                    lang = user.get("lang", "ru")
                    is_kg = user.get("role") == "kindergarten"
                    name_parts = (user.get("name") or "").split()
                    name = name_parts[0] if name_parts else ("коллега" if lang == "ru" else "әріптес")

                    # 1. Проверяем расписание на завтра
                    schedule_json = await db.get_schedule(tg_id)
                    event_info = _tomorrow_event_info(schedule_json, is_kg)

                    if event_info:
                        event_text = event_info["event"]
                        text = (
                            f"Сәлеметсіз бе, {name}! Ертең сізде {event_text}. Құжаттардың жобасын алдын ала дайындап қояйық па?"
                            if lang == "kz" else
                            f"Здравствуйте, {name}! Завтра у вас {event_text}. Подготовить черновики документов?"
                        )
                        call_key = "agent_auto_generate_cycle" if is_kg else "agent_auto_generate_ksp"
                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("Да" if lang == "ru" else "Иә", callback_data=call_key),
                                InlineKeyboardButton("Нет" if lang == "ru" else "Жоқ", callback_data="agent_skip")
                            ]
                        ])
                        await app.bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            reply_markup=keyboard,
                        )
                        await db.update_notified(tg_id)
                        await asyncio.sleep(0.3)
                        continue

                    # 2. Если не заходил 3+ дней — сообщение с выгодой
                    if _due_since_last_active_or_notified(user, min_days=3):
                        text = (
                            f"Сәлеметсіз бе, {name}! Біраз уақыт кірмедіңіз. 1 минутта есеп немесе жоспар дайындап берейін бе?"
                            if lang == "kz" else
                            f"Здравствуйте, {name}! Не заходили неделю. Сделать отчёт за 1 минуту?"
                        )
                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("Да, сделать" if lang == "ru" else "Иә, жасау", callback_data="menu_create"),
                                InlineKeyboardButton("Позже" if lang == "ru" else "Кейінірек", callback_data="agent_remind_later")
                            ]
                        ])
                        await app.bot.send_message(
                            chat_id=tg_id,
                            text=text,
                            reply_markup=keyboard,
                        )
                        await db.update_notified(tg_id)
                        await asyncio.sleep(0.3)
                        continue

                except Exception as user_err:
                    logger.warning(f"Error notifying user {user.get('tg_id')}: {user_err}")

            # 3. Раз в 2 недели: полезная рассылка новостей и шаблонов (только тем, кто не отключал)
            try:
                broadcast_users = await db.get_users_for_broadcast(days=14)
                for b_user in broadcast_users:
                    try:
                        b_id = b_user["tg_id"]
                        b_lang = b_user.get("lang", "ru")
                        b_name_parts = (b_user.get("name") or "").split()
                        b_name = b_name_parts[0] if b_name_parts else ("коллега" if b_lang == "ru" else "әріптес")
                        is_kg = b_user.get("role") == "kindergarten"

                        if b_lang == "kz":
                            news_text = (
                                f"Қайырлы күн, {b_name}! Бізде жаңа мүмкіндіктер мен жаңартылған шаблондар қосылды:\n"
                                f"• BilimClass журналымен ыңғайлы жұмыс\n"
                                f"• Презентациялар мен жаңа үлгідегі құжаттар\n\n"
                                f"Жаңа шаблондарды байқап көреміз бе?"
                            )
                            btn_text = "✨ Жаңа шаблондарды ашу"
                        else:
                            news_text = (
                                f"Добрый день, {b_name}! Мы обновили шаблоны и добавили полезные функции:\n"
                                f"• Улучшенная интеграция с BilimClass\n"
                                f"• Готовые интерактивные презентации и отчёты\n\n"
                                f"Хотите посмотреть новые шаблоны?"
                            )
                            btn_text = "✨ Посмотреть шаблоны"

                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton(btn_text, callback_data="menu_create")]
                        ])
                        await app.bot.send_message(chat_id=b_id, text=news_text, reply_markup=kb)
                        await db.update_broadcast_sent(b_id)
                        await asyncio.sleep(0.3)
                    except Exception as b_err:
                        logger.warning(f"Broadcast error for {b_user.get('tg_id')}: {b_err}")
            except Exception as bc_err:
                logger.error(f"Broadcast check error: {bc_err}")

        except Exception as e:
            logger.error(f"Daily scheduler error: {e}")

        # Небольшая пауза (60 секунд), чтобы не сработать повторно в ту же минуту
        await asyncio.sleep(60)


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
                        content = msg.content[0].text if msg.content else ""
                        content = ensure_ai_disclaimer(content, lang)

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

                        try:
                            with open(word_path, "rb") as f:
                                await app.bot.send_document(
                                    chat_id=tg_id,
                                    document=f,
                                    filename=f"{doc_name}_{now.strftime('%d%m%Y')}.docx",
                                    caption=caption,
                                    parse_mode=ParseMode.MARKDOWN
                                )
                        except Exception:
                            with open(word_path, "rb") as f:
                                await app.bot.send_document(
                                    chat_id=tg_id,
                                    document=f,
                                    filename=f"{doc_name}_{now.strftime('%d%m%Y')}.docx",
                                    caption=caption.replace("*", "")
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
                        try:
                            await app.bot.send_message(chat_id=tg_id, text=prompt_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            await app.bot.send_message(chat_id=tg_id, text=prompt_text.replace("*", ""), reply_markup=kb)
                        await db.update_agent_context(tg_id, {"last_sched_mon_key": dedup_key})

                    await asyncio.sleep(0.5)

                except Exception as user_err:
                    logger.warning(f"Error checking schedule for user {user.get('tg_id')}: {user_err}")

        except Exception as e:
            logger.error(f"Schedule monitor loop error: {e}")

        # Цикл каждые 6 часов
        await asyncio.sleep(6 * 3600)

