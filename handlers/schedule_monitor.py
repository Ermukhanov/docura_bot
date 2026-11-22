"""Мониторинг расписания и автогенерация КСП.

Раньше: раз в 6 часов бот брал ПЕРВЫЙ урок дня и сам делал КСП без темы урока
отдельным упрощённым запросом (качество хуже обычного), а напоминание в 18:00
дублировало то же самое. К тому же цикл вообще не запускался (post_init не вызывался).

Теперь:
• Бот проверяет расписание каждые 30 минут и один раз в день, вечером (15:00–20:00),
  присылает учителю ВСЕ уроки на завтра с кнопкой КСП на каждый урок.
• Автогенерация (MAX, кнопка в «Расписании»): бот просит темы уроков одним сообщением
  и делает КСП на каждый урок обычным полным пайплайном (с проверкой качества,
  данными учеников и профиля). Без темы КСП не генерируется — тему не выдумываем.
• Воспитателям в пятницу вечером — предложение циклограммы на следующую неделю.
"""
import asyncio
import json
import logging
import re
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from database import Database, format_lesson, _DAY_ALIASES
from handlers.chat_utils import now_local

logger = logging.getLogger(__name__)

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAYS_KZ = ["Дүйсенбі", "Сейсенбі", "Сәрсенбі", "Бейсенбі", "Жұма", "Сенбі", "Жексенбі"]
WINDOW = (15, 20)  # часы, когда уместно писать про завтрашние уроки
CHECK_EVERY = 30 * 60


def lessons_for(schedule: dict, weekday: int) -> list[dict]:
    want = DAYS_RU[weekday].lower()
    for key, items in (schedule or {}).items():
        if _DAY_ALIASES.get(str(key).strip().lower().rstrip("."), str(key)).lower() == want:
            return [it for it in (items if isinstance(items, list) else []) if isinstance(it, dict)]
    return []


def wants_auto(user: dict) -> bool:
    return bool(user.get("subscribed")) and bool(user.get("auto_generate"))


def _lesson_label(it: dict) -> str:
    return " ".join(p for p in [str(it.get("class") or "").strip(), str(it.get("subject") or "").strip()] if p) or "урок"


async def _send_teacher_digest(app: Application, db: Database, user: dict, lessons: list[dict], day_idx: int, date):
    tg_id, lang = user["tg_id"], (user.get("lang") or "ru")
    ru = lang != "kz"
    day = (DAYS_RU if ru else DAYS_KZ)[day_idx].lower()
    lines = [f"{n}) {format_lesson(it, lang)}" for n, it in enumerate(lessons, 1)]
    head = (f"📅 Завтра, {day} — уроков: {len(lessons)}\n" if ru else f"📅 Ертең, {day} — сабақ саны: {len(lessons)}\n")
    pending = {"date": date.strftime("%d.%m.%Y"), "lessons": lessons[:8]}
    if wants_auto(user):
        ex = "\n".join(f"{n}) …" for n in range(1, min(len(lessons), 3) + 1))
        text = head + "\n".join(lines) + (
            "\n\n🤖 Автогенерация включена. Напишите темы уроков — по одной строке на урок, "
            f"в том же порядке, и я сделаю КСП на каждый:\n{ex}\n\nЕсли урок не нужен — напишите «-»."
            if ru else
            "\n\n🤖 Авто-жасау қосулы. Сабақ тақырыптарын жазыңыз — әр сабаққа бір жол, "
            f"сол ретпен, мен әрқайсысына ҚМЖ жасаймын:\n{ex}\n\nСабақ керек болмаса — «-» деп жазыңыз.")
        ud = app.user_data.get(tg_id) if hasattr(app.user_data, "get") else None
        if ud is not None and not ud.get("step"):  # не ломаем начатый учителем сценарий
            ud["step"] = "agent_ksp_topics"
            ud["ksp_pending"] = pending
        else:
            # пользователя ещё нет в памяти бота — данные сохранятся при первом его сообщении
            await db.update_agent_context(tg_id, {"ksp_pending": pending})
    else:
        text = head + "\n".join(lines) + ("\n\nНужен КСП? Нажмите на урок — спрошу только тему."
                                          if ru else "\n\nҚМЖ керек пе? Сабақты басыңыз — тек тақырыбын сұраймын.")
    kb = [[InlineKeyboardButton(("📋 КСП: " if ru else "📋 ҚМЖ: ") + _lesson_label(it)[:40], callback_data=f"agent_ksp_{n}")]
          for n, it in enumerate(lessons[:8])]
    kb.append([InlineKeyboardButton("🔕 Не присылать" if ru else "🔕 Жібермеу", callback_data="agent_reminders_off")])
    await db.update_agent_context(tg_id, {"ksp_lessons": pending})
    await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(kb))


async def _send_kg_weekly(app: Application, user: dict):
    ru = (user.get("lang") or "ru") != "kz"
    await app.bot.send_message(
        chat_id=user["tg_id"],
        text=("📅 Скоро новая неделя. Подготовить циклограмму на следующую неделю по вашему режиму дня?"
              if ru else "📅 Жаңа апта жақындап қалды. Күн тәртібіңіз бойынша келесі аптаға циклограмма дайындайын ба?"),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, подготовить" if ru else "✅ Иә, дайында", callback_data="agent_auto_generate_cycle")],
            [InlineKeyboardButton("🔕 Не присылать" if ru else "🔕 Жібермеу", callback_data="agent_reminders_off")],
        ]))


async def run_once(app: Application, db: Database) -> int:
    now = now_local()
    if not (WINDOW[0] <= now.hour < WINDOW[1]):
        return 0
    today_key = now.strftime("%Y-%m-%d")
    tomorrow = now + timedelta(days=1)
    sent = 0
    for user in await db.get_users_with_schedules():
        tg_id = user["tg_id"]
        try:
            if user.get("notifications_enabled") == 0:
                continue
            memory = await db.get_agent_context(tg_id)
            if memory.get("reminders_enabled") is False or memory.get("sched_digest_date") == today_key:
                continue
            raw = await db.get_schedule(tg_id)
            schedule = json.loads(raw) if raw else {}
            if not isinstance(schedule, dict):
                continue
            if user.get("role") == "kindergarten":
                if now.weekday() == 4:  # пятница
                    await _send_kg_weekly(app, user)
                    sent += 1
            else:
                lessons = lessons_for(schedule, tomorrow.weekday())
                if lessons:
                    await _send_teacher_digest(app, db, user, lessons, tomorrow.weekday(), tomorrow.date())
                    sent += 1
            await db.update_agent_context(tg_id, {"sched_digest_date": today_key})
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.warning("schedule digest failed for %s: %s", tg_id, exc)
    return sent


async def schedule_monitor_loop(app: Application, db: Database):
    logger.info("📅 Мониторинг расписания запущен (проверка каждые 30 мин, сообщение раз в день вечером)")
    await asyncio.sleep(30)
    while True:
        try:
            n = await run_once(app, db)
            if n:
                logger.info("📅 Отправлено сводок по расписанию: %s", n)
        except Exception:
            logger.exception("schedule monitor loop error")
        await asyncio.sleep(CHECK_EVERY)


def parse_topics(text: str, count: int) -> list[str]:
    """«1) Дроби\\n2) -\\n3. Уравнения» → ['Дроби', '', 'Уравнения']."""
    parts = [p for p in re.split(r"\n|;", text) if p.strip()]
    if len(parts) == 1 and count > 1 and "," in parts[0]:
        parts = parts[0].split(",")
    topics = []
    for p in parts[:count]:
        t = re.sub(r"^\s*\d+\s*[).:-]\s*", "", p).strip().strip("«»\"")
        topics.append("" if t in {"-", "—", "нет", "жоқ", "пропустить"} else t)
    return topics


def pending_still_valid(pend: dict) -> bool:
    """Темы принимаем только пока урок впереди (сводка вчерашнего вечера после урока не действует)."""
    from datetime import datetime
    try:
        return datetime.strptime((pend or {}).get("date", ""), "%d.%m.%Y").date() >= now_local().date()
    except ValueError:
        return False
