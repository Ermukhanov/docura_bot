"""
Docura.kz — разговорный агент (концьерж)

Отвечает на любое свободное сообщение в чате (когда нет активного шага/сценария):
здоровается по времени суток и по имени, напоминает прикрепить расписание,
рассказывает про акцию/фичи, и — если у пользователя PRO — проактивно предлагает
сгенерировать документ на основе завтрашнего расписания (например "у вас
собрание с родителями — сделать письмо родителям или отчёт?"), и по согласию
запускает обычный сценарий генерации (тот же, что через меню).

Для бесплатного плана — только мягкое упоминание "если хотите, чтобы я сам сразу
предлагал и делал такие документы — можно перейти на PRO", без слов
"сгенерировать"/"сделать документ" и без реального запуска генерации.

ВАЖНО: использует ОТДЕЛЬНУЮ модель/провайдера (не Anthropic) — настраивается
через переменные окружения CONCIERGE_API_KEY / CONCIERGE_MODEL / CONCIERGE_BASE_URL.
Сделано по стандарту OpenAI-совместимого chat.completions API — под него
подходит большинство сторонних провайдеров (просто мигрирует через base_url).
Если провайдер использует другой формат — нужно будет адаптировать только
метод _call_ai в этом файле, остальной код не трогается.
"""

import os
import json
import asyncio
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Database, free_limit_for
from handlers.query_adapter import MessageQueryAdapter

CONCIERGE_API_KEY  = os.getenv("CONCIERGE_API_KEY", "")
CONCIERGE_MODEL    = os.getenv("CONCIERGE_MODEL", "gpt-4o-mini")
CONCIERGE_BASE_URL = os.getenv("CONCIERGE_BASE_URL")  # None -> дефолтный OpenAI endpoint

MAX_HISTORY_TURNS = 6  # сколько последних пар (юзер+агент) храним для контекста

# Ключевые слова в расписании -> какой документ предложить.
# doc_types указаны отдельно для школы/сада — модель сама выберет подходящий по роли.
SCHEDULE_SUGGESTIONS = [
    (["собрание", "жиналыс"], {
        "teacher":      [("parent_letter", "Письмо родителям"), ("monthly_report", "Отчёт учителя")],
        "kindergarten": [("kg_parent_letter", "Письмо родителям"), ("kg_monthly_report", "Отчёт воспитателя")],
    }),
    (["контрольная", "бақылау жұмысы"], {
        "teacher":      [("control_analysis", "Анализ контрольной работы")],
        "kindergarten": [],
    }),
]

DAY_MAP_RU = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
    4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}


def _greeting_word(lang: str) -> str:
    hour = datetime.now().hour
    if lang == "kz":
        if 5 <= hour < 12:  return "Қайырлы таң"
        if 12 <= hour < 18: return "Қайырлы күн"
        if 18 <= hour < 23: return "Қайырлы кеш"
        return "Кеш жарық"
    if 5 <= hour < 12:  return "Доброе утро"
    if 12 <= hour < 18: return "Добрый день"
    if 18 <= hour < 23: return "Добрый вечер"
    return "Доброй ночи"


def _find_schedule_suggestions(schedule: dict, role: str):
    """Ищет в расписании на завтра совпадения по ключевым словам и возвращает
    список подходящих (doc_type, doc_name) — модель предложит один или спросит какой."""
    if not schedule:
        return None, None

    tomorrow_idx = (datetime.now().weekday() + 1) % 7
    day_name = DAY_MAP_RU[tomorrow_idx]
    entries = schedule.get(day_name, [])
    if not entries:
        return day_name, None

    text_blob = " ".join(
        f"{e.get('subject','')} {e.get('class','')}" for e in entries if isinstance(e, dict)
    ).lower()

    for keywords, by_role in SCHEDULE_SUGGESTIONS:
        if any(kw in text_blob for kw in keywords):
            options = by_role.get(role, [])
            if options:
                return day_name, options

    return day_name, None


class ConciergeHandler:
    def __init__(self, db: Database, anthropic_api_key: str):
        self.db = db
        self.anthropic_api_key = anthropic_api_key  # нужен, чтобы запустить реальную генерацию документа

    # ══════════════════════════════════════════════════════
    # ВЫЗОВ ВНЕШНЕЙ МОДЕЛИ
    # ══════════════════════════════════════════════════════

    def _configured(self) -> bool:
        return bool(CONCIERGE_API_KEY)

    def _call_ai_sync(self, system_prompt: str, history: list, user_message: str) -> dict:
        """Синхронный вызов — оборачивается в run_in_executor снаружи."""
        from openai import OpenAI
        kwargs = {"api_key": CONCIERGE_API_KEY}
        if CONCIERGE_BASE_URL:
            kwargs["base_url"] = CONCIERGE_BASE_URL
        client = OpenAI(**kwargs)

        messages = [{"role": "system", "content": system_prompt}]
        for turn in history[-MAX_HISTORY_TURNS:]:
            messages.append(turn)
        messages.append({"role": "user", "content": user_message})

        # Не все OpenAI-совместимые провайдеры поддерживают строгий JSON-режим —
        # пробуем с ним, и если сервер ругается на сам параметр, откатываемся без него
        # (промпт и так явно просит вернуть чистый JSON, плюс есть очистка от markdown ниже).
        try:
            resp = client.chat.completions.create(
                model=CONCIERGE_MODEL,
                messages=messages,
                max_tokens=400,
                temperature=0.7,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            print(f"Concierge: response_format не поддержан провайдером ({e}), пробую без него")
            resp = client.chat.completions.create(
                model=CONCIERGE_MODEL,
                messages=messages,
                max_tokens=400,
                temperature=0.7,
            )

        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
        # На случай если модель всё равно добавила текст до/после JSON — вырезаем сам объект
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)
        return json.loads(raw)

    async def _call_ai(self, system_prompt: str, history: list, user_message: str) -> dict:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._call_ai_sync, system_prompt, history, user_message
            )
        except Exception as e:
            print(f"Concierge AI error: {e}")
            return {"reply": None, "action": "none", "doc_type": None}

    # ══════════════════════════════════════════════════════
    # СИСТЕМНЫЙ ПРОМПТ
    # ══════════════════════════════════════════════════════

    def _build_system_prompt(self, user: dict, lang: str, day_name, suggestions) -> str:
        is_pro = bool(user.get("subscribed"))
        is_kg  = user.get("role") == "kindergarten"
        name   = (user.get("name") or "").split()[0] or ("коллега" if lang == "ru" else "әріптес")
        greeting = _greeting_word(lang)
        free_left = max(0, free_limit_for(user) - user.get("free_used", 0))

        schedule_block = ""
        if suggestions and day_name:
            opts = "; ".join(f"{dt}:{name_}" for dt, name_ in suggestions)
            schedule_block = (
                f"\nЗАВТРА ({day_name}) в расписании пользователя есть что-то похожее на "
                f"мероприятие, для которого обычно нужен документ. Подходящие варианты "
                f"(doc_type:название): {opts}. "
                f"Если уместно — мягко спроси, не нужно ли подготовить один из этих документов, "
                f"но не дави и не повторяй одно и то же в каждом сообщении, если уже спрашивал недавно.\n"
            )
        elif day_name:
            schedule_block = f"\nУ пользователя есть сохранённое расписание, завтра — {day_name}, но ничего примечательного там нет.\n"
        else:
            schedule_block = "\nУ пользователя ПОКА НЕТ сохранённого расписания — если уместно, мягко напомни, что можно прикрепить расписание (в главном меню кнопка «Расписание»/«Режим дня») — тогда документы будут генерироваться точнее и быстрее.\n"

        pro_rules = (
            "Пользователь на подписке PRO. Тебе МОЖНО прямо предлагать сгенерировать документ "
            "и, если пользователь согласился (сказал 'да'/'давай'/'сделай' и т.п. на твоё "
            "предыдущее предложение), вернуть action='generate' с конкретным doc_type "
            "из предложенных ранее вариантов."
        ) if is_pro else (
            "Пользователь НА БЕСПЛАТНОМ плане. Тебе ЗАПРЕЩЕНО предлагать сгенерировать/сделать "
            "документ и запрещено использовать слова «сгенерировать», «сделаю», «подготовлю документ» "
            "и т.п. Вместо этого можешь мягко, БЕЗ давления, один раз за разговор упомянуть, что "
            "на PRO ты сам будешь готовить такие документы по расписанию — и жизнь станет проще. "
            "Никогда не возвращай action='generate' или action='propose' для этого пользователя — "
            f"всегда action='none'. Также напомни, что осталось {free_left} бесплатных документов, "
            "если это уместно."
        )

        role_word = "воспитатель" if is_kg else "учитель"

        return f"""Ты — дружелюбный, тёплый, но не навязчивый ассистент сервиса Docura.kz внутри Telegram-бота.
Ты помогаешь {role_word}ям Казахстана — сейчас общаешься с {name}.

ПРАВИЛА ОБЩЕНИЯ:
- Пиши на {"русском" if lang == "ru" else "казахском"} языке, коротко (2-4 предложения), тепло и по-человечески, без канцелярита.
- Обращайся по имени ({name}), не «Мадам»/«сэр» — это не принято в русско-/казахскоязычном общении.
- Если это начало нового разговора (истории сообщений ниже нет) — поздоровайся: "{greeting}, {name}!" и дальше по делу.
- Если разговор уже идёт (есть история ниже) — НЕ здоровайся заново, отвечай по контексту.
- Не будь роботом-меню — ты живой собеседник, а не список опций.
- Если пользователь пишет что-то не по теме документов — можешь просто по-дружески ответить, не обязательно каждый раз сводить к документам.
{schedule_block}
{pro_rules}

ОТВЕЧАЙ СТРОГО В ФОРМАТЕ JSON, без markdown-разметки:
{{"reply": "текст который увидит пользователь", "action": "none" | "propose" | "generate", "doc_type": "ключ_типа_документа или null"}}

- action="propose" — если ты только что предложил конкретный документ и ждёшь ответа (doc_type — тот, что предлагаешь; если вариантов два — оставь doc_type null и уточни в reply какой из двух).
- action="generate" — ТОЛЬКО если пользователь только что явно согласился на ранее предложенный документ (doc_type — конкретный, не null). Доступно только для PRO.
- action="none" — во всех остальных случаях, включая весь бесплатный план.
"""

    # ══════════════════════════════════════════════════════
    # ХРАНЕНИЕ ИСТОРИИ / ОТЛОЖЕННОГО ПРЕДЛОЖЕНИЯ
    # ══════════════════════════════════════════════════════

    async def _load_state(self, user_id: int) -> dict:
        ctx = await self.db.get_agent_context(user_id)
        return ctx.get("concierge", {"history": [], "pending": None})

    async def _save_state(self, user_id: int, state: dict):
        state["history"] = state.get("history", [])[-(MAX_HISTORY_TURNS * 2):]
        await self.db.update_agent_context(user_id, {"concierge": state})

    # ══════════════════════════════════════════════════════
    # ОСНОВНОЙ ВХОД: свободное сообщение в чате
    # ══════════════════════════════════════════════════════

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru")
        text    = update.message.text.strip()

        if not self._configured():
            # Ключ ещё не подключён — не ломаем бота, отвечаем мягким заглушечным текстом
            await update.message.reply_text(
                f"{_greeting_word(lang)}! 👋 Я пока учусь вести беседу — а пока жду команду "
                f"или нажмите /menu, чтобы посмотреть, что я умею." if lang == "ru" else
                f"{_greeting_word(lang)}! 👋 /menu — мәзірді көру үшін."
            )
            return

        state = await self._load_state(user_id)
        schedule_json = await self.db.get_schedule(user_id)
        schedule = json.loads(schedule_json) if schedule_json else None
        role = user.get("role", "teacher")
        day_name, suggestions = _find_schedule_suggestions(schedule, role) if schedule else (None, None)

        # Если ранее было отложенное предложение (например, из напоминания) — подмешиваем в контекст
        pending = state.get("pending")
        if pending and not suggestions:
            day_name = pending.get("day_name")
            suggestions = pending.get("options")

        system_prompt = self._build_system_prompt(user, lang, day_name, suggestions)
        result = await self._call_ai(system_prompt, state.get("history", []), text)

        reply = result.get("reply")
        if not reply:
            reply = "Извините, не расслышал — повторите, пожалуйста? 🙂" if lang == "ru" else "Кешіріңіз, қайталап жіберіңізші?"

        action   = result.get("action", "none")
        doc_type = result.get("doc_type")
        is_pro   = bool(user.get("subscribed"))

        await update.message.reply_text(reply)

        # Обновляем историю
        state.setdefault("history", [])
        state["history"].append({"role": "user", "content": text})
        state["history"].append({"role": "assistant", "content": reply})

        if action == "propose" and is_pro:
            state["pending"] = {
                "day_name": day_name,
                "options": suggestions,
                "proposed_at": datetime.now().isoformat(),
            }
            await self._save_state(user_id, state)
            return

        if action == "generate" and is_pro and doc_type:
            state["pending"] = None
            await self._save_state(user_id, state)
            await self._start_generation(update, context, user_id, user, lang, doc_type)
            return

        state["pending"] = None
        await self._save_state(user_id, state)

    async def _start_generation(self, update, context, user_id, user, lang, doc_type):
        """Запускает обычный сценарий генерации документа (выбор языка -> вопросы -> генерация) —
        тот же самый, что и через меню, просто вход не из кнопки, а из разговора."""
        from handlers.documents import DocumentHandler, DOC_NAMES

        doc_name = DOC_NAMES.get(lang, DOC_NAMES["ru"]).get(doc_type, doc_type)
        await update.message.reply_text(
            f"✅ Хорошо, начинаю: *{doc_name}*" if lang == "ru" else f"✅ Жарайды, бастаймын: *{doc_name}*",
            parse_mode=ParseMode.MARKDOWN
        )

        docs = DocumentHandler(self.db, self.anthropic_api_key)
        adapter = MessageQueryAdapter(update.message)
        await docs._start_doc(adapter, context, user_id, user, lang, doc_type)

    # ══════════════════════════════════════════════════════
    # ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ (используется notifications.py)
    # ══════════════════════════════════════════════════════

    async def build_reminder(self, user: dict, lang: str) -> tuple[str, dict | None]:
        """Возвращает (текст напоминания, pending-предложение_или_None)."""
        user_id = user["tg_id"]
        role = user.get("role", "teacher")
        schedule_json = await self.db.get_schedule(user_id)
        schedule = json.loads(schedule_json) if schedule_json else None
        day_name, suggestions = _find_schedule_suggestions(schedule, role) if schedule else (None, None)

        if not self._configured():
            # Без ключа — обычное статичное напоминание (как было раньше), бот не ломается
            from handlers.texts import t
            name = (user.get("name") or "").split()[0]
            key = "notif_reminder_kg" if role == "kindergarten" else "notif_reminder"
            return t(lang, key, name=name), None

        system_prompt = self._build_system_prompt(user, lang, day_name, suggestions)
        system_prompt += (
            "\n\nЭто ПРОАКТИВНОЕ напоминание — пользователь ничего не писал, ты пишешь первым, "
            "потому что он давно не создавал документы. Обязательно поздоровайся (это начало разговора) "
            "и мягко напомни о себе — без спама, по-дружески."
        )
        result = await self._call_ai(system_prompt, [], "[система: отправь проактивное напоминание]")
        reply = result.get("reply")
        if not reply:
            from handlers.texts import t
            name = (user.get("name") or "").split()[0]
            key = "notif_reminder_kg" if role == "kindergarten" else "notif_reminder"
            return t(lang, key, name=name), None

        pending = None
        is_pro = bool(user.get("subscribed"))
        if result.get("action") == "propose" and is_pro:
            pending = {"day_name": day_name, "options": suggestions, "proposed_at": datetime.now().isoformat()}

        return reply, pending
