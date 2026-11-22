import os
import asyncio
import logging
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


def _inactive_reminder_text(name: str, lang: str) -> str:
    if lang == "kz":
        return f"Сәлеметсіз бе, {name}! Біраз уақыт кірмедіңіз. 1 минутта есеп немесе жоспар дайындап берейін бе?"
    return f"Здравствуйте, {name}! Не заходили несколько дней. Сделать отчёт за 1 минуту?"


# Конец четвертей в школах РК (примерно): за неделю до них — СОР/СОЧ и анализ.
QUARTER_ENDS = [(10, 31), (12, 26), (3, 20), (5, 25)]


def daily_tip(user: dict, memory: dict, lang: str, is_kg: bool, has_schedule: bool, has_students: bool, now):
    """Короткая подсказка дня под конкретного педагога: что полезно сделать именно сегодня."""
    ru = lang != "kz"
    T = lambda r, k: r if ru else k
    day, wd, month = now.day, now.weekday(), now.month
    last_day = ((now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    near_quarter_end = any(m == month and 0 <= d - day <= 7 for m, d in QUARTER_ENDS)

    if is_kg:
        if wd == 4:
            return (T("Пятница — время подготовить циклограмму на следующую неделю. Сделаем за пару минут?",
                      "Жұма — келесі аптаға циклограмма дайындайтын уақыт. Бірнеше минутта жасайық па?"),
                    [(T("📅 Циклограмма", "📅 Циклограмма"), "doc_kindergarten_cycle_schedule")])
        if last_day - day <= 3:
            return (T("Конец месяца близко — нужен отчёт воспитателя?", "Ай соңы жақын — тәрбиеші есебі керек пе?"),
                    [(T("📊 Отчёт за месяц", "📊 Айлық есеп"), "doc_kg_monthly_report")])
        pool = [
            (T("Завтрашнее занятие можно расписать по шагам за минуту — технологическая карта ОУД.",
               "Ертеңгі ҰОҚ-ты бір минутта қадамдап жазуға болады — технологиялық карта."),
             [(T("📖 Техкарта ОУД", "📖 ҰОҚ техкартасы"), "doc_kg_activity_summary")]),
            (T("Хотите поблагодарить родителей или сообщить новость группы? Письмо готово за минуту.",
               "Ата-аналарға алғыс айтқыңыз немесе топ жаңалығын хабарлағыңыз келе ме? Хат бір минутта дайын."),
             [(T("📩 Письмо родителям", "📩 Ата-аналарға хат"), "doc_kg_parent_letter")]),
            (T("Характеристика воспитанника по данным из базы — без ручного набора.",
               "Тәрбиеленуші мінездемесі — базадағы деректер бойынша, қолмен термей."),
             [(T("📝 Характеристика", "📝 Мінездеме"), "doc_kg_child_characteristic")]),
        ]
        if not has_students and user.get("subscribed"):
            pool.insert(0, (T("Добавьте список группы — имена детей будут подставляться в документы сами.",
                              "Топ тізімін қосыңыз — балалардың аттары құжаттарға өзі қойылады."),
                            [(T("👶 Добавить детей", "👶 Балаларды қосу"), "prof_students")]))
    else:
        if near_quarter_end:
            return (T("Конец четверти близко — СОР/СОЧ и их анализ можно сделать сейчас.",
                      "Тоқсан соңы жақын — БЖБ/ТЖБ мен талдауын қазір жасауға болады."),
                    [(T("📝 СОР / СОЧ", "📝 БЖБ / ТЖБ"), "doc_sor_soch"),
                     (T("📈 Анализ СОР / СОЧ", "📈 БЖБ / ТЖБ талдауы"), "doc_sor_soch_analysis")])
        if last_day - day <= 3:
            return (T("Конец месяца — отчёт учителя за пару минут?", "Ай соңы — мұғалім есебін бірнеше минутта жасайық па?"),
                    [(T("📊 Отчёт учителя", "📊 Мұғалім есебі"), "doc_monthly_report")])
        if wd == 6 or wd == 0:
            return (T("Новая неделя — КСП на ближайшие уроки готовы за минуту каждый.",
                      "Жаңа апта — жақын сабақтарға ҚМЖ әрқайсысы бір минутта."),
                    [(T("📋 Сделать КСП", "📋 ҚМЖ жасау"), "doc_lesson_plan")])
        pool = [
            (T("Провели контрольную? Анализ с выводами и таблицей — за пару минут.",
               "Бақылау жұмысын өткіздіңіз бе? Қорытындысы мен кестесі бар талдау — бірнеше минутта."),
             [(T("📈 Анализ контрольной", "📈 Бақылау талдауы"), "doc_control_analysis")]),
            (T("Нужно написать родителям? Письмо или благодарность — за минуту, с именем ученика из базы.",
               "Ата-анаға жазу керек пе? Хат немесе алғыс — бір минутта, оқушының аты базадан."),
             [(T("📩 Письмо родителям", "📩 Ата-анаға хат"), "doc_parent_letter"),
              (T("🏆 Благодарность", "🏆 Алғыс хат"), "doc_gratitude_letter")]),
            (T("Характеристика ученика по оценкам и достижениям из базы — без ручного набора.",
               "Оқушы мінездемесі — базадағы бағалар мен жетістіктер бойынша."),
             [(T("📝 Характеристика", "📝 Мінездеме"), "doc_characteristic")]),
            (T("Конспект завтрашнего урока с заданиями — быстрее, чем писать вручную.",
               "Ертеңгі сабақтың тапсырмалары бар конспектісі — қолмен жазудан тезірек."),
             [(T("📖 Конспект урока", "📖 Сабақ конспектісі"), "doc_lesson_summary")]),
        ]
        if not has_schedule and user.get("subscribed"):
            pool.insert(0, (T("Загрузите расписание — каждый вечер буду присылать уроки на завтра и делать КСП в 1 нажатие.",
                              "Кестені жүктеңіз — әр кеш ертеңгі сабақтарды жіберіп, ҚМЖ-ны бір басумен жасаймын."),
                            [(T("📅 Загрузить расписание", "📅 Кестені жүктеу"), "agent_schedule")]))
        if not has_students and user.get("subscribed"):
            pool.insert(0, (T("Добавьте учеников списком — имена, оценки и родители будут подставляться в документы сами.",
                              "Оқушыларды тізіммен қосыңыз — аттары, бағалары мен ата-аналары құжаттарға өзі қойылады."),
                            [(T("👥 Добавить учеников", "👥 Оқушыларды қосу"), "prof_students")]))
    # Каждый день — другая подсказка (по кругу), а настройка базы/расписания — через день, не каждый день.
    idx = now.timetuple().tm_yday % len(pool)
    text, buttons = pool[idx]
    if memory.get("last_tip") and text.endswith(memory["last_tip"]):
        text, buttons = pool[(idx + 1) % len(pool)]
    return text, buttons


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
    Ежедневно в 18:00 — одно короткое полезное сообщение каждому педагогу:
    подсказка дня под его роль и дату (КСП на неделю, отчёт в конце месяца,
    СОР/СОЧ перед концом четверти, письмо родителям и т.д.), каждый день другая.
    Если в этот день уже ушла сводка уроков на завтра — второе сообщение не шлём.
    Кнопка «Не присылать» отключает всё.
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

                    # Одно полезное сообщение в день. Если вечером уже ушла сводка уроков
                    # на завтра (schedule_monitor) — второе сообщение не шлём.
                    today_key = now_local().strftime("%Y-%m-%d")
                    if memory.get("sched_digest_sent") == today_key or memory.get("daily_tip_date") == today_key:
                        continue
                    has_schedule = bool(await db.get_schedule(tg_id))
                    has_students = bool(await db.get_students(tg_id))
                    text, buttons = daily_tip(user, memory, lang, is_kg, has_schedule, has_students, now_local())
                    text = (f"Добрый вечер, {name}! " if lang == "ru" else f"Қайырлы кеш, {name}! ") + text
                    rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in buttons]
                    rows.append([InlineKeyboardButton("🔕 Не присылать" if lang == "ru" else "🔕 Жібермеу", callback_data="agent_reminders_off")])
                    await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(rows))
                    await db.update_notified(tg_id)
                    await db.update_agent_context(tg_id, {"daily_tip_date": today_key, "last_tip": text[-60:]})
                    await asyncio.sleep(0.3)

                except Exception as user_err:
                    logger.warning(f"Error notifying user {user.get('tg_id')}: {user_err}")

        except Exception as e:
            logger.error(f"Daily scheduler error: {e}")

        # Небольшая пауза (60 секунд), чтобы не сработать повторно в ту же минуту
        await asyncio.sleep(60)


async def send_first_doc_reminders(app: Application, db: Database) -> int:
    """Send one localized reminder to each eligible user without a first document."""
    users = await db.get_users_due_first_doc_reminder()
    sent = 0
    for user in users:
        tg_id = user["tg_id"]
        if user.get("notifications_enabled") == 0:
            continue
        try:
            memory = await db.get_agent_context(tg_id)
            if memory.get("reminders_enabled") is False:
                continue
            lang = user.get("lang", "ru")
            if lang == "kz":
                text = (
                    "👋 Сәлем! Сіз әлі алғашқы құжатыңызды жасамадыңыз. "
                    "Құжат пен тілді таңдаңыз — мен қадамдап көмектесемін."
                )
                button = "📄 Құжат жасау"
            else:
                text = (
                    "👋 Вы ещё не создали первый документ. Выберите документ и язык — "
                    "я помогу пройти шаги и подготовить файл."
                )
                button = "📄 Создать документ"
            await app.bot.send_message(
                chat_id=tg_id,
                text=text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(button, callback_data="menu_create")
                ]]),
            )
            await db.mark_first_doc_reminder_sent(tg_id)
            sent += 1
        except Exception as error:
            logger.warning("First-document reminder failed for user %s: %s", tg_id, error)
            if "blocked" in str(error).lower() or "deactivated" in str(error).lower() or "chat not found" in str(error).lower():
                await db.mark_first_doc_reminder_sent(tg_id)  # не пытаться каждый час заново
    return sent


async def first_doc_reminder_loop(app: Application, db: Database):
    """Poll hourly so the one-time reminder arrives shortly after the 24-hour mark."""
    logger.info("🔔 First-document reminder scheduler active (hourly)")
    while True:
        try:
            await send_first_doc_reminders(app, db)
        except Exception:
            logger.exception("First-document reminder scan failed")
        await asyncio.sleep(60 * 60)


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
