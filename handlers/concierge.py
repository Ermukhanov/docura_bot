"""
Docura.kz — разговорный ИИ-агент (концьерж) в чате.

ЧТО УМЕЕТ
- Знает местное время (Казахстан, UTC+5) и день недели; здоровается ТОЛЬКО в начале
  разговора (или после долгой паузы) и по времени суток — ночью не пишет «добрый день».
- Помнит разговор: последние сообщения хранятся в базе (переживают перезапуск бота),
  плюс короткая «долгая память» — устойчивые факты о пользователе (класс, предмет и т.п.).
- Показывает «печатает…», пока думает или готовит документ.
- Умеет готовить ВСЕ виды документов: человек пишет «сделай КСП по математике на завтра» —
  агент сам разбирает сообщение по полям документа, пишет, чего не хватает («для генерации
  нужны: тема урока, длительность»), человек отвечает обычным сообщением (можно сразу на
  несколько пунктов), и агент продолжает, пока данных не хватит, затем запускает генерацию.
  Язык документа спрашивается явно (кнопками или словами «на казахском»).

ВАЖНО: разговорная модель настраивается переменными CONCIERGE_API_KEY / CONCIERGE_MODEL /
CONCIERGE_BASE_URL (OpenAI-совместимый API). Подготовка документов использует Claude
(ANTHROPIC_API_KEY) и работает даже без CONCIERGE_API_KEY.
"""

import os
import json
import asyncio
import re
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Database, free_limit_for
from handlers.query_adapter import MessageQueryAdapter
from handlers.chat_utils import now_local, greeting_word, part_of_day, typing_action, LOCAL_TZ

logger = logging.getLogger(__name__)

CONCIERGE_API_KEY  = os.getenv("CONCIERGE_API_KEY", "")
CONCIERGE_MODEL    = os.getenv("CONCIERGE_MODEL", "gpt-4o-mini")
CONCIERGE_BASE_URL = os.getenv("CONCIERGE_BASE_URL")  # None -> дефолтный OpenAI endpoint

MAX_HISTORY_MESSAGES = 16  # последние 8 пар «пользователь + агент»

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

# Дешёвый предохранитель ДО вызова ИИ-классификатора (Шаг 2.3 задачи): не гоняем
# Claude Haiku на каждое "привет"/"как дела" — только когда сообщение похоже на
# запрос действия. Список не обязан быть исчерпывающим — это не финальная
# классификация (её делает _classify_document_intent), а просто фильтр расходов.
ACTION_TRIGGER_WORDS = (
    "сделай", "сделать", "создай", "создать", "нужен", "нужна", "нужно",
    "хочу", "составь", "составить", "подготовь", "подготовить", "сгенерируй",
    "сгенерировать", "напиши", "написать", "оформи", "оформить", "заполни",
    # казахский — разные формы глаголов "сделать/составить/нужно", чтобы не
    # зависеть только от того, попало ли название документа в DOCUMENT_NAME_HINTS
    "жаса", "жасап", "жасашы", "істе", "істеші", "керек", "құра", "құрастыр",
    "дайында", "дайындап", "жаз", "толтыр",
)

# Одних глаголов действия мало: пользователь может написать просто название
# документа без глагола ("циклограмма", "характеристика ученику"). Раньше это
# ловил старый хардкодный список именно по таким словам — сохраняем это же
# покрытие как часть дешёвого предфильтра (не обязано быть исчерпывающим,
# точное решение всё равно принимает AI-классификатор, это только предохранитель
# от лишних вызовов на обычный чат вроде "привет"/"как дела").
DOCUMENT_NAME_HINTS = (
    "циклограм", "cyclogram", "ксп", "ктп", "қмж", "күнтізбелік",
    "характеристик", "мінездеме", "тематическ", "тақырыптық жоспар",
    "отчёт", "отчет", "есеп", "конспект", "заявление", "өтініш",
    "объяснительн", "түсіндірме", "справк", "анықтама", "акт", "мониторинг",
    "утренник", "мереке", "сор", "соч", "бжб", "тжб", "объявление",
    "хабарландыру", "протокол", "хаттама", "план работы", "жұмыс жоспары",
    "карта развития", "даму картасы",
)


def _looks_like_action_request(text: str) -> bool:
    lowered = text.lower()
    return (any(word in lowered for word in ACTION_TRIGGER_WORDS)
            or any(word in lowered for word in DOCUMENT_NAME_HINTS))



def _week_period() -> str:
    today = now_local().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return f"{monday:%d.%m.%Y}–{sunday:%d.%m.%Y}"


def _greeting_word(lang: str) -> str:
    """Приветствие по МЕСТНОМУ времени (Казахстан, UTC+5), а не по времени сервера."""
    return greeting_word(lang, now_local())


WEEKDAYS = {
    "ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
    "kz": ["дүйсенбі", "сейсенбі", "сәрсенбі", "бейсенбі", "жұма", "сенбі", "жексенбі"],
}
PART_OF_DAY_RU = {"morning": "утро", "day": "день", "evening": "вечер", "night": "ночь"}

# Здороваемся заново только если пауза в разговоре длиннее этого (или истории нет).
GREET_GAP_HOURS = 8
# Незавершённая подготовка документа «живёт» не дольше этого времени без ответа.
TASK_TTL_MINUTES = 90
MAX_FACTS = 25

_KZ_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")

_GREETING_RE = re.compile(
    r"^\s*(?:добр(?:ое\s+утро|ый\s+день|ый\s+вечер|ой\s+ночи)|здравствуйте|здравствуй|привет(?:ствую)?|"
    r"қайырлы\s+(?:таң|күн|кеш)|кеш\s+жарық|сәлеметсіз\s+бе|сәлем(?:етсіз бе)?|hello|hi)"
    r"[\s,!.:–—-]*",
    re.IGNORECASE,
)

# Что нельзя запоминать в «долгой памяти»: пароли, номера карт и документов, здоровье.
_SENSITIVE_RE = re.compile(
    r"(парол|password|\bпин\b|карт[аыу]\b|иин|жсн|паспорт|диагноз|болезн|ауру|\bcvv\b|\d{9,})",
    re.IGNORECASE,
)

# Поля, которые можно заполнить «автоматически», не переспрашивая пользователя.
OPTIONAL_KEYS = {
    "goals", "key_points", "criteria", "typical_errors", "props", "theses", "conditions",
    "extra", "witnesses", "documents", "activities", "purpose", "events", "details",
    "directions", "topics", "roles", "hours_per_week", "textbook", "duration",
    "frequency", "materials", "observations", "decision", "problem_extra",
}

# Вопросы для документов со своим сценарием (не из общего реестра вопросов).
SPECIAL_FIELDS = {
    "kindergarten_cycle_schedule": [
        {"key": "group", "optional": False,
         "q": {"ru": "Для какой группы нужна циклограмма?", "kz": "Циклограмма қай топқа керек?", "en": "Which group is the cyclogram for?"}},
        {"key": "week_topic", "optional": False,
         "q": {"ru": "Тема недели (например: Домашние животные, Транспорт)", "kz": "Апта тақырыбы (мысалы: Үй жануарлары, Көлік)", "en": "Weekly theme (e.g. Pets, Transport)"}},
        {"key": "period", "optional": True,
         "q": {"ru": "Неделя или даты (если не укажете — возьму текущую неделю)", "kz": "Апта немесе күндер (көрсетпесеңіз — ағымдағы апта)", "en": "Week or dates (current week if not given)"}},
        {"key": "events", "optional": True,
         "q": {"ru": "Мероприятия на неделе (если нет — «нет»)", "kz": "Аптадағы іс-шаралар (жоқ болса — «жоқ»)", "en": "Events this week (or “none”)"}},
    ],
    "development_monitoring": [
        {"key": "period", "optional": False,
         "q": {"ru": "За какой период нужен мониторинг?", "kz": "Мониторинг қай кезеңге керек?", "en": "Which period is the monitoring for?"}},
        {"key": "age_group", "optional": False,
         "q": {"ru": "Для какой возрастной группы?", "kz": "Қай жас тобына арналған?", "en": "Which age group?"}},
        {"key": "children", "optional": False,
         "q": {"ru": "Список детей (через запятую или столбиком) или «пустой» для чистого бланка",
               "kz": "Балалар тізімі (үтірмен немесе бағанмен) немесе бос бланк үшін «бос»",
               "en": "List of children (comma-separated or one per line) or “blank” for an empty form"}},
    ],
}

# Ключи профиля -> ключи документа, которые можно подставить без вопросов.
PROFILE_PREFILL_KEYS = ("age_group", "group", "subject_class")

LANG_BUTTONS = [("🇷🇺 Русский", "ru"), ("🇰🇿 Қазақша", "kz"), ("🇬🇧 English", "en")]
DOC_LANG_NAMES = {"ru": "русский", "kz": "қазақша", "en": "English"}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _extract_json(raw: str) -> dict | None:
    raw = re.sub(r"```[a-z]*", "", raw or "").strip("` \n")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(match.group(0) if match else raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _detect_lang(text: str, default: str) -> str:
    """Язык ответа в этом диалоге: если человек пишет по-казахски — отвечаем по-казахски."""
    if sum(1 for ch in text if ch in _KZ_LETTERS) >= 2:
        return "kz"
    return default


def _field_label(question: str) -> str:
    """Короткая подпись поля из текста вопроса: без эмодзи, примеров и markdown."""
    first = question.split("\n")[0]
    first = re.sub(r"[_*`]", "", first)
    first = re.sub(r"^[^\wА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІіA-Za-z0-9]+", "", first)
    return first.strip().rstrip("?").strip()


def _field_hint(question: str) -> str:
    m = re.search(r"_(?:Пример|Мысал|Example)[^_]*_", question)
    if not m:
        return ""
    return re.sub(r"[_*`]", "", m.group(0)).strip()


def _strip_leading_greeting(reply: str) -> str:
    """Убирает приветствие в начале ответа (если здороваться не нужно)."""
    m = _GREETING_RE.match(reply)
    if not m:
        return reply
    rest = reply[m.end():]
    # после приветствия может идти имя: «Добрый день, Айгуль! …» — снимаем его тоже
    name_m = re.match(r"^[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][\wәғқңөұүһі-]{1,30}[!,.\s]+", rest)
    if name_m and (",", "!") and reply[m.start():m.end()].rstrip().endswith((",", " ")):
        rest = rest[name_m.end():]
    rest = rest.lstrip(" ,!.:–—-\n")
    if not rest:
        return reply
    return rest[0].upper() + rest[1:]


def _fix_greeting_time(reply: str, lang: str) -> str:
    """Если здороваться нужно, но модель написала приветствие не по времени суток — исправляем."""
    m = _GREETING_RE.match(reply)
    if not m:
        return reply
    word = _greeting_word(lang)
    tail = reply[m.end():]
    sep = reply[m.start():m.end()]
    trailing = re.search(r"[\s,!.:–—-]*$", sep)
    return word + (trailing.group(0) if trailing and trailing.group(0) else ", ") + tail


def _find_schedule_suggestions(schedule: dict, role: str):
    """Ищет в расписании на завтра совпадения по ключевым словам и возвращает
    список подходящих (doc_type, doc_name) — модель предложит один или спросит какой."""
    if not schedule:
        return None, None

    tomorrow_idx = (now_local().weekday() + 1) % 7
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
        self.anthropic_api_key = anthropic_api_key

    # ══════════════════════════════════════════════════════
    # ВЫЗОВЫ МОДЕЛЕЙ
    # ══════════════════════════════════════════════════════

    def _configured(self) -> bool:
        return bool(CONCIERGE_API_KEY or self.anthropic_api_key)

    def _call_ai_sync(self, system_prompt: str, history: list, user_message: str) -> dict:
        """Разговорная модель (OpenAI-совместимый API или Claude Haiku). Синхронно — вызывается в потоке."""
        if CONCIERGE_API_KEY:
            from openai import OpenAI
            kwargs = {"api_key": CONCIERGE_API_KEY}
            if CONCIERGE_BASE_URL:
                kwargs["base_url"] = CONCIERGE_BASE_URL
            client = OpenAI(**kwargs)

            messages = [{"role": "system", "content": system_prompt}]
            for turn in history[-MAX_HISTORY_MESSAGES:]:
                messages.append({"role": turn["role"], "content": turn["content"]})
            messages.append({"role": "user", "content": user_message})

            try:
                resp = client.chat.completions.create(
                    model=CONCIERGE_MODEL, messages=messages, max_tokens=500,
                    temperature=0.7, response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.info("Concierge: response_format не поддержан (%s), повтор без него", e)
                resp = client.chat.completions.create(
                    model=CONCIERGE_MODEL, messages=messages, max_tokens=500, temperature=0.7,
                )

            raw = resp.choices[0].message.content.strip()
            parsed = _extract_json(raw)
            if parsed is None:
                logger.warning("Concierge: модель вернула не-JSON, использую как текст: %r", raw[:200])
                return {"reply": raw, "action": "none", "doc_type": None}
            return parsed
        elif self.anthropic_api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)
            claude_messages = []
            for turn in history[-MAX_HISTORY_MESSAGES:]:
                claude_messages.append({"role": turn["role"], "content": turn["content"]})
            claude_messages.append({"role": "user", "content": user_message})
            msg = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=500,
                system=system_prompt,
                messages=claude_messages,
            )
            raw = msg.content[0].text.strip()
            parsed = _extract_json(raw)
            if parsed is None:
                return {"reply": raw, "action": "none", "doc_type": None}
            return parsed
        return {"reply": None, "action": "none", "doc_type": None}

    async def _call_ai(self, system_prompt: str, history: list, user_message: str) -> dict:
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, self._call_ai_sync, system_prompt, history, user_message
            )
        except Exception as e:
            logger.error("Concierge AI error (%s): %s", type(e).__name__, e)
            return {"reply": None, "action": "none", "doc_type": None}

    async def _claude_json(self, prompt: str, max_tokens: int = 700) -> dict | None:
        """Быстрый вызов Claude Haiku, возвращающий JSON (классификация и извлечение полей)."""
        import anthropic
        try:
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)
            msg = await client.messages.create(
                model="claude-haiku-4-5", max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_json(msg.content[0].text)
        except Exception as e:
            logger.error("Concierge Claude error (%s): %s", type(e).__name__, e)
            return None

    # ══════════════════════════════════════════════════════
    # КЛАССИФИКАЦИЯ ТИПА ДОКУМЕНТА
    # ══════════════════════════════════════════════════════

    def _allowed_doc_types_for_role(self, role: str) -> dict:
        from handlers.documents import CAT_DOCS, CAT_DOCS_KG, CAT_DOCS_COMMON, DOC_NAMES

        role_cats = CAT_DOCS_KG if role == "kindergarten" else CAT_DOCS
        doc_types = []
        for cat_docs in role_cats.values():
            doc_types.extend(cat_docs)
        for cat_docs in CAT_DOCS_COMMON.values():
            doc_types.extend(cat_docs)
        names = DOC_NAMES.get("ru", {})
        return {dt: names.get(dt, dt) for dt in dict.fromkeys(doc_types)}

    async def _classify_document_intent(self, text: str, role: str) -> dict:
        allowed = self._allowed_doc_types_for_role(role)
        doc_list = "\n".join(f"- {key}: {name}" for key, name in allowed.items())
        prompt = f"""Пользователь написал в чат Telegram-боту Docura.kz сообщение:
"{text}"

Определи, является ли это запросом на создание одного из следующих официальных документов:
{doc_list}

Если это запрос на создание одного из документов выше — верни его ключ (doc_type).
Если это обычный разговор, вопрос не по теме документов или документ, которого нет в списке — doc_type: null.

Ответь ТОЛЬКО JSON без markdown: {{"doc_type": "ключ_или_null"}}"""
        parsed = await self._claude_json(prompt, max_tokens=120) or {}
        doc_type = parsed.get("doc_type")
        return {"doc_type": doc_type if doc_type in allowed else None}

    # ══════════════════════════════════════════════════════
    # ХРАНЕНИЕ ПАМЯТИ ДИАЛОГА
    # ══════════════════════════════════════════════════════

    async def _load_state(self, user_id: int) -> dict:
        ctx = await self.db.get_agent_context(user_id)
        state = ctx.get("concierge") or {}
        state.setdefault("history", [])
        state.setdefault("pending", None)
        state.setdefault("facts", [])
        state.setdefault("doc_task", None)
        return state

    async def _save_state(self, user_id: int, state: dict):
        state["history"] = state.get("history", [])[-MAX_HISTORY_MESSAGES:]
        state["facts"] = state.get("facts", [])[-MAX_FACTS:]
        await self.db.update_agent_context(user_id, {"concierge": state})

    @staticmethod
    def _remember(state: dict, role: str, content: str):
        state.setdefault("history", []).append(
            {"role": role, "content": content, "ts": now_local().isoformat()}
        )

    @staticmethod
    def _gap_hours(state: dict) -> float | None:
        """Сколько часов прошло с последнего сообщения в этом диалоге."""
        last = None
        for turn in reversed(state.get("history", [])):
            last = _parse_dt(turn.get("ts"))
            if last:
                break
        if not last:
            return None
        return (now_local() - last).total_seconds() / 3600

    def _merge_facts(self, state: dict, result: dict):
        if result.get("forget") is True:
            state["facts"] = []
        for fact in (result.get("remember") or [])[:5]:
            if not isinstance(fact, str):
                continue
            fact = fact.strip()
            if not (3 <= len(fact) <= 160) or _SENSITIVE_RE.search(fact):
                continue
            if fact.casefold() not in {f.casefold() for f in state["facts"]}:
                state["facts"].append(fact)

    # ══════════════════════════════════════════════════════
    # СИСТЕМНЫЙ ПРОМПТ
    # ══════════════════════════════════════════════════════

    def _build_system_prompt(self, user: dict, lang: str, day_name, suggestions,
                             state: dict | None = None, should_greet: bool = False,
                             task_hint: str = "", schedule: dict | None = None,
                             students: list | None = None) -> str:
        state = state or {}
        is_pro = bool(user.get("subscribed"))
        is_kg = user.get("role") == "kindergarten"
        name_parts = (user.get("name") or "").split()
        name = name_parts[0] if name_parts else ("коллега" if lang == "ru" else "әріптес")
        free_left = max(0, free_limit_for(user) - user.get("free_used", 0))

        now = now_local()
        weekday = WEEKDAYS.get(lang, WEEKDAYS["ru"])[now.weekday()]
        gap = self._gap_hours(state)
        gap_text = "это первое сообщение" if gap is None else (
            f"с прошлого сообщения прошло {int(gap * 60)} мин" if gap < 1 else f"с прошлого сообщения прошло {gap:.0f} ч")

        time_block = (
            f"ТЕКУЩЕЕ ВРЕМЯ (Казахстан, UTC+5): {weekday}, {now:%d.%m.%Y}, {now:%H:%M} — "
            f"{PART_OF_DAY_RU[part_of_day(now.hour)]}. {gap_text}.\n"
            "Учитывай время суток в тоне: ночью не пиши «добрый день/вечер», не предлагай срочных дел без повода."
        )
        if should_greet:
            greet_rule = (f"ПРИВЕТСТВИЕ: это начало разговора — начни ответ ровно с «{_greeting_word(lang)}, {name}!» "
                          "и больше никаких других приветствий не используй.")
        else:
            greet_rule = ("ПРИВЕТСТВИЕ: разговор уже идёт — НЕ здоровайся и не начинай с «Добрый день/вечер/утро», "
                          "«Здравствуйте», «Привет». Сразу отвечай по сути.")

        facts = state.get("facts") or []
        facts_block = ("\nЧТО ТЫ ЗНАЕШЬ О ПОЛЬЗОВАТЕЛЕ (из прошлых разговоров, используй к месту, не пересказывай):\n- "
                       + "\n- ".join(facts) + "\n") if facts else ""

        if is_kg:
            profile_block = (f"Профиль: воспитатель, детский сад «{user.get('school') or '—'}», "
                             f"группа/возраст: {user.get('age_group') or '—'}.")
        else:
            profile_block = (f"Профиль: учитель, школа «{user.get('school') or '—'}», предмет: "
                             f"{user.get('subject') or '—'}, классы: {user.get('classes') or '—'}.")

        if schedule:
            lines = []
            for day, items in schedule.items():
                if isinstance(items, list):
                    day_str = ", ".join(f"{it.get('time', '')} {it.get('class', '')} {it.get('subject', '')}".strip() for it in items if isinstance(it, dict))
                    lines.append(f"- {day}: {day_str}")
                else:
                    lines.append(f"- {day}: {items}")
            schedule_text = "\n".join(lines)
            schedule_block = (
                f"\nРАСПИСАНИЕ / РЕЖИМ ДНЯ ПОЛЬЗОВАТЕЛЯ:\n{schedule_text}\n"
                f"Если пользователь спрашивает про расписание («проверь мое расписание», «что у меня запланировано», «какие уроки завтра», «расписание»), "
                f"ответь точно и конкретно по этому расписанию!\n"
            )
            if suggestions and day_name:
                opts = "; ".join(f"{dt}:{name_}" for dt, name_ in suggestions)
                schedule_block += f"ВНИМАНИЕ: завтра ({day_name}) есть событие: {opts}. При необходимости предложи подготовить документ.\n"
        elif suggestions and day_name:
            opts = "; ".join(f"{dt}:{name_}" for dt, name_ in suggestions)
            schedule_block = (
                f"\nЗАВТРА ({day_name}) в расписании есть событие, для которого обычно нужен документ. "
                f"Варианты (doc_type:название): {opts}. Если уместно — мягко спроси, не подготовить ли один из них, "
                f"но не повторяй это в каждом сообщении.\n")
        elif day_name:
            schedule_block = f"\nУ пользователя есть расписание, завтра — {day_name}, ничего примечательного.\n"
        else:
            schedule_block = ("\nУ пользователя пока нет расписания — если пользователь спрашивает про расписание или планы, "
                              "ответь, что расписание пока не загружено, и напомни, что его можно прикрепить (кнопка «Расписание»/«Режим дня» в меню).\n")

        students_block = ""
        if students:
            by_class = {}
            for s in students:
                by_class.setdefault(s.get("class_name") or "без группы", []).append(s.get("name", ""))
            std_parts = [f"{cls}: {', '.join(names[:15])}" for cls, names in by_class.items()]
            students_block = f"\nБАЗА УЧЕНИКОВ / ДЕТЕЙ ПОЛЬЗОВАТЕЛЯ:\n" + "; ".join(std_parts) + "\n"

        pro_rules = (
            "Пользователь на PRO. Ты можешь сам предлагать подготовить документ; если он согласился на твоё "
            "предыдущее предложение — верни action='generate' с конкретным doc_type."
        ) if is_pro else (
            "Пользователь на БЕСПЛАТНОМ плане. Сам НЕ предлагай «сгенерировать/подготовить документ» и всегда "
            "возвращай action='none'. Один раз за разговор можно мягко сказать, что на PRO такие документы готовятся "
            f"по расписанию автоматически. Осталось бесплатных документов: {free_left} (упоминай, только если уместно). "
            "Если человек сам просит документ — это обработает система, тебе ничего делать не нужно."
        )

        role_word = "воспитателям" if is_kg else "учителям"
        task_block = f"\n{task_hint}\n" if task_hint else ""

        return f"""Ты — дружелюбный, тёплый, но не навязчивый ассистент сервиса Docura.kz внутри Telegram-бота. \
Ты помогаешь {role_word} Казахстана; сейчас говоришь с {name}.

{time_block}
{greet_rule}
{profile_block}
{facts_block}{task_block}
{schedule_block}
{students_block}
ПРАВИЛА ОБЩЕНИЯ:
- Пиши на {"русском" if lang == "ru" else "казахском"} языке, коротко (2–4 предложения), тепло и по-человечески, без канцелярита.
- Обращайся по имени ({name}). Не повторяй то, что уже говорил в этом разговоре, и не задавай один и тот же вопрос дважды.
- Помни контекст: опирайся на историю сообщений ниже, а не отвечай каждый раз «с нуля».
- Если пользователь пишет не по теме документов — просто по-дружески ответь.
- Если человек сообщает устойчивый факт о себе (класс, предмет, любимый формат, имя директора) — добавь его в "remember" (коротко, до 100 символов). Не запоминай пароли, номера, здоровье, финансы. Если просит «забудь всё» — "forget": true.
{pro_rules}

ОТВЕЧАЙ СТРОГО В ФОРМАТЕ JSON, без markdown:
{{"reply": "текст для пользователя", "action": "none" | "propose" | "generate", "doc_type": "ключ или null", "remember": ["короткий факт"], "forget": false}}

- action="propose" — ты только что предложил конкретный документ и ждёшь ответа (если вариантов два — doc_type null и уточни в reply).
- action="generate" — ТОЛЬКО если пользователь явно согласился на ранее предложенный документ (doc_type конкретный). Только PRO.
- action="none" — во всех остальных случаях.
"""

    # ══════════════════════════════════════════════════════
    # СБОР ДАННЫХ ДЛЯ ДОКУМЕНТА ПРЯМО В РАЗГОВОРЕ
    # ══════════════════════════════════════════════════════

    def _task_fields(self, task: dict, user: dict, ui_lang: str) -> list[dict]:
        """Поля документа: [{key, label, hint, optional}]. Поля, которые уже есть в профиле, не спрашиваем."""
        from handlers.documents import DOC_QUESTIONS, REGISTRY_QUESTIONS
        from handlers.doc_questions import get_questions

        doc_type = task["doc_type"]
        profile = self._profile_values(user)
        fields = []
        if doc_type in SPECIAL_FIELDS:
            for f in SPECIAL_FIELDS[doc_type]:
                fields.append({"key": f["key"], "label": f["q"].get(ui_lang) or f["q"]["ru"],
                               "hint": "", "optional": f["optional"]})
        else:
            for q in get_questions(doc_type, ui_lang, DOC_QUESTIONS, REGISTRY_QUESTIONS):
                fields.append({"key": q["key"], "label": _field_label(q["q"]),
                               "hint": _field_hint(q["q"]), "optional": q["key"] in OPTIONAL_KEYS})
        # то, что уже известно из профиля, пользователя не спрашиваем
        for f in fields:
            if f["key"] in PROFILE_PREFILL_KEYS and profile.get(f["key"]):
                f["optional"] = True
        return fields

    @staticmethod
    def _profile_values(user: dict) -> dict:
        return {
            "age_group": user.get("age_group", "") or "",
            "group": user.get("age_group", "") or "",
            "subject_class": ", ".join(x for x in [user.get("subject", ""), user.get("classes", "")] if x),
        }

    def _missing(self, task: dict, fields: list[dict], user: dict) -> list[dict]:
        profile = self._profile_values(user)
        answers = task.get("answers", {})
        out = []
        for f in fields:
            if f["optional"]:
                continue  # необязательное поле заполнится «автоматически» — не переспрашиваем
            if str(answers.get(f["key"], "")).strip():
                continue
            if f["key"] in PROFILE_PREFILL_KEYS and profile.get(f["key"]):
                continue
            out.append(f)
        return out

    async def _extract_answers(self, task: dict, fields: list[dict], user_text: str, ui_lang: str) -> dict:
        """Просим Claude разобрать свободное сообщение по полям документа."""
        today = now_local()
        field_lines = "\n".join(
            f'- "{f["key"]}": {f["label"]}' + (f' ({f["hint"]})' if f["hint"] else "")
            + ("  [можно «автоматически»]" if f["optional"] else "")
            for f in fields
        )
        prompt = f"""Ты помогаешь собрать данные для официального документа «{task['doc_name']}» из переписки в Telegram.
Сегодня {today:%d.%m.%Y} ({WEEKDAYS['ru'][today.weekday()]}), Казахстан.

Поля документа:
{field_lines}

Уже собрано: {json.dumps(task.get('answers', {}), ensure_ascii=False)}
Язык документа: {task.get('doc_lang') or 'ещё не выбран'}

Сообщение пользователя:
"{user_text}"

Правила:
1. В "answers" положи ТОЛЬКО значения, которые пользователь прямо назвал в этом сообщении (можно сразу несколько полей). Ничего не выдумывай и не угадывай. Ключи — только из списка выше.
2. Если пользователь пишет «не знаю», «автоматически», «сам придумай» про поле, помеченное [можно «автоматически»] — значение «автоматически».
3. Относительные даты («завтра», «в пятницу») переводи в дату ДД.ММ.ГГГГ. Класс пиши как «7А».
4. "doc_lang": "ru", "kz" или "en", только если пользователь прямо указал язык документа («на казахском», «қазақша», «in English»), иначе null.
5. "student_hint": имя ученика/ребёнка, если названо, иначе null.
6. "intent": "continue" — сообщение продолжает подготовку этого документа; "cancel" — человек отказывается/просит отменить; "other" — сообщение вообще о другом.

Ответь ТОЛЬКО JSON без markdown:
{{"intent": "continue", "answers": {{}}, "doc_lang": null, "student_hint": null}}"""
        parsed = await self._claude_json(prompt, max_tokens=700) or {}
        valid = {f["key"] for f in fields}
        answers = {k: str(v).strip() for k, v in (parsed.get("answers") or {}).items()
                   if k in valid and v not in (None, "", [], {})}
        doc_lang = parsed.get("doc_lang") if parsed.get("doc_lang") in ("ru", "kz", "en") else None
        intent = parsed.get("intent") if parsed.get("intent") in ("continue", "cancel", "other") else "continue"
        return {"answers": answers, "doc_lang": doc_lang, "intent": intent, "student_hint": parsed.get("student_hint")}

    async def _apply_student_from_db(self, task: dict, user_id: int):
        """Если назван ученик/воспитанник из базы — подтягиваем его данные, как при выборе из списка."""
        answers = task.setdefault("answers", {})
        hint = (answers.get("student_name") or answers.get("child_name") or "").strip().casefold()
        if not hint or task.get("student_id"):
            return
        try:
            students = await self.db.get_students(user_id)
        except Exception:
            return
        tokens = [t for t in re.split(r"[\s,]+", hint) if len(t) >= 4]
        for s in students:
            full = (s.get("name") or "").casefold()
            if full and (full in hint or hint in full or any(t in full for t in tokens)):
                grades = json.loads(s.get("grades") or "{}")
                ach = json.loads(s.get("achievements") or "[]")
                answers.setdefault("student_name", f"{s['name']}, {s['class_name']}")
                answers.setdefault("performance", ", ".join(f"{k}-{v}" for k, v in grades.items()) if grades else "нет данных")
                answers.setdefault("activities", ", ".join(ach) if ach else "нет")
                answers.setdefault("behavior", s.get("behavior") or "хорошее")
                for src, dst in (("parents", "parents"), ("parent_phone", "parent_phone"), ("birth_date", "birth_date"),
                                 ("absences", "absences"), ("address", "address"), ("health_group", "health_group")):
                    answers.setdefault(dst, s.get(src, ""))
                if task["doc_type"] == "kg_individual_development_card":
                    answers["child_name"] = s["name"]
                    answers["group"] = s["class_name"]
                task["student_id"] = s["id"]
                return

    def _missing_message(self, task: dict, missing: list[dict], need_lang: bool, ui_lang: str) -> str:
        lines = []
        for f in missing:
            lines.append("• " + f["label"] + (f"  ({f['hint']})" if f["hint"] else ""))
        if need_lang:
            lines.append("• " + ("На каком языке составить документ?" if ui_lang == "ru" else "Құжатты қай тілде жасау керек?"))
        doc = task["doc_name"]
        if ui_lang == "kz":
            head = f"«{doc}» дайындау үшін маған мыналар жетіспейді:"
            tail = "Бәрін бір хабарламамен жазыңыз — қалғанын өзім жалғастырамын 👍"
        else:
            head = f"Чтобы подготовить «{doc}», мне не хватает:"
            tail = "Напишите всё одним сообщением — дальше я продолжу сам 👍"
        return head + "\n" + "\n".join(lines) + "\n\n" + tail

    def _task_keyboard(self, task: dict, need_lang: bool, ui_lang: str) -> InlineKeyboardMarkup:
        rows = []
        if need_lang:
            rows.append([InlineKeyboardButton(label, callback_data=f"cg_lang_{code}") for label, code in LANG_BUTTONS[:2]])
            rows.append([InlineKeyboardButton(LANG_BUTTONS[2][0], callback_data="cg_lang_en")])
        rows.append([InlineKeyboardButton("❌ Отмена" if ui_lang == "ru" else "❌ Бас тарту", callback_data="cg_cancel")])
        return InlineKeyboardMarkup(rows)

    def _new_task(self, doc_type: str, ui_lang: str) -> dict:
        from handlers.documents import DOC_NAMES
        return {
            "doc_type": doc_type,
            "doc_name": DOC_NAMES.get(ui_lang, DOC_NAMES["ru"]).get(doc_type, doc_type),
            "answers": {}, "doc_lang": None,
            "created_at": now_local().isoformat(),
        }

    def _task_alive(self, task: dict | None) -> bool:
        if not task:
            return False
        created = _parse_dt(task.get("updated_at") or task.get("created_at"))
        return bool(created and (now_local() - created) < timedelta(minutes=TASK_TTL_MINUTES))

    async def _begin_task(self, message, context, user, ui_lang: str, doc_type: str, first_text: str | None):
        """Начинает подготовку документа: извлекает из первого сообщения всё, что названо, и спрашивает остальное."""
        user_id = user["tg_id"]
        state = await self._load_state(user_id)
        task = self._new_task(doc_type, ui_lang)
        state["doc_task"] = task
        await self._advance_task(message, context, user, ui_lang, state, first_text)

    async def _advance_task(self, message, context, user, ui_lang: str, state: dict,
                            user_text: str | None, forced_lang: str | None = None) -> bool:
        """Один шаг сбора данных. Возвращает False, если сообщение не относится к документу."""
        user_id = user["tg_id"]
        task = state["doc_task"]
        fields = self._task_fields(task, user, ui_lang)

        if forced_lang:
            task["doc_lang"] = forced_lang

        if user_text:
            async with typing_action(context.bot, message.chat_id):
                extracted = await self._extract_answers(task, fields, user_text, ui_lang)
            if extracted["intent"] == "cancel":
                state["doc_task"] = None
                reply = "Хорошо, отменил. Если что — пишите 🙂" if ui_lang == "ru" else "Жарайды, бас тарттым. Керек болса — жазыңыз 🙂"
                self._remember(state, "user", user_text)
                self._remember(state, "assistant", reply)
                await self._save_state(user_id, state)
                await message.reply_text(reply)
                return True
            if extracted["intent"] == "other" and not extracted["answers"] and not extracted["doc_lang"]:
                return False
            task["answers"].update(extracted["answers"])
            if extracted["doc_lang"]:
                task["doc_lang"] = extracted["doc_lang"]
            await self._apply_student_from_db(task, user_id)

        task["updated_at"] = now_local().isoformat()
        missing = self._missing(task, fields, user)
        need_lang = not task.get("doc_lang")

        if missing or need_lang:
            reply = self._missing_message(task, missing, need_lang, ui_lang)
            if user_text:
                self._remember(state, "user", user_text)
            self._remember(state, "assistant", reply)
            await self._save_state(user_id, state)
            await message.reply_text(reply, reply_markup=self._task_keyboard(task, need_lang, ui_lang))
            return True

        # всё собрано — генерируем
        await self._generate_from_task(message, context, user, ui_lang, state, user_text)
        return True

    def _prepare_answers(self, task: dict, user: dict, doc_lang: str, fields: list | None = None) -> dict:
        """Ответы в формате, который ждёт DocumentHandler._generate (как после обычного опросника)."""
        doc_type = task["doc_type"]
        answers = dict(task.get("answers", {}))
        profile = self._profile_values(user)
        for key in PROFILE_PREFILL_KEYS:
            if not answers.get(key) and profile.get(key):
                answers[key] = profile[key]
        # «автоматически» -> понятная для генератора формулировка на языке документа
        auto = {"ru": "автоматически", "kz": "автоматты", "en": "automatic"}[doc_lang]
        for k, v in list(answers.items()):
            if str(v).strip().casefold() in ("автоматически", "автоматты", "automatic"):
                answers[k] = auto
        if doc_type not in SPECIAL_FIELDS:
            for f in fields or []:
                if f["optional"] and not str(answers.get(f["key"], "")).strip():
                    answers[f["key"]] = auto
        if doc_type == "kindergarten_cycle_schedule":
            answers.setdefault("organization", user.get("school", ""))
            answers.setdefault("educator_name", user.get("name", ""))
            answers["lang"] = doc_lang
            if not answers.get("period") or answers["period"] == auto:
                answers["period"] = _week_period()
            if not answers.get("events"):
                answers["events"] = "нет" if doc_lang == "ru" else ("жоқ" if doc_lang == "kz" else "none")
        elif doc_type == "development_monitoring":
            answers.setdefault("organization", user.get("school", ""))
            answers.setdefault("educator_name", user.get("name", ""))
            answers.setdefault("director_name", user.get("director", ""))
            answers.setdefault("group", answers.get("age_group", ""))
        return answers

    async def _generate_from_task(self, message, context, user, ui_lang: str, state: dict, user_text: str | None):
        from handlers.documents import DocumentHandler

        user_id = user["tg_id"]
        task = state["doc_task"]
        doc_lang = task["doc_lang"]

        # Лимит бесплатных документов — тот же экран, что и в обычном сценарии.
        fresh = await self.db.get_user(user_id) or user
        if not fresh.get("subscribed") and fresh.get("free_used", 0) >= free_limit_for(fresh):
            state["doc_task"] = None
            await self._save_state(user_id, state)
            await DocumentHandler(self.db, self.anthropic_api_key)._start_doc(
                MessageQueryAdapter(message), context, user_id, fresh, ui_lang, task["doc_type"])
            return

        answers = self._prepare_answers(task, fresh, doc_lang, self._task_fields(task, fresh, ui_lang))
        context.user_data.clear()
        context.user_data.update({
            "doc_type": task["doc_type"], "doc_lang": doc_lang, "doc_answers": answers,
            "questions": [], "q_index": 0, "step": None,
        })

        if user_text:
            self._remember(state, "user", user_text)
        note = (f"[Данные собраны, готовлю документ «{task['doc_name']}», язык: {DOC_LANG_NAMES[doc_lang]}]")
        self._remember(state, "assistant", note)
        state["doc_task"] = None
        await self._save_state(user_id, state)

        try:
            await DocumentHandler(self.db, self.anthropic_api_key)._generate(message, context, ui_lang)
        except Exception:
            logger.exception("Генерация из разговора не удалась (user %s, %s)", user_id, task["doc_type"])
            context.user_data.clear()
            await message.reply_text(
                "😔 Не получилось подготовить документ. Попробуйте ещё раз чуть позже или через /menu."
                if ui_lang == "ru" else
                "😔 Құжатты дайындау мүмкін болмады. Сәлден соң қайталап көріңіз немесе /menu арқылы жасаңыз.")

    # ══════════════════════════════════════════════════════
    # КНОПКИ ВНУТРИ РАЗГОВОРА (выбор языка / отмена)
    # ══════════════════════════════════════════════════════

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        if not user:
            return
        ui_lang = user.get("lang", "ru")
        state = await self._load_state(user_id)
        data = query.data

        if not self._task_alive(state.get("doc_task")):
            state["doc_task"] = None
            await self._save_state(user_id, state)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await query.message.reply_text(
                "Эта заявка уже неактуальна — напишите, какой документ нужен 🙂" if ui_lang == "ru"
                else "Бұл сұраным ескірген — қандай құжат керек екенін жазыңыз 🙂")
            return

        if data == "cg_cancel":
            state["doc_task"] = None
            await self._save_state(user_id, state)
            try:
                await query.edit_message_text("Отменил ✅" if ui_lang == "ru" else "Бас тарттым ✅")
            except Exception:
                pass
            return

        if data.startswith("cg_lang_"):
            parts = data.split("_")
            code = parts[2] if len(parts) > 2 else "ru"
            if code not in ("ru", "kz", "en"):
                return
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await self._advance_task(query.message, context, user, ui_lang, state, None, forced_lang=code)

    # ══════════════════════════════════════════════════════
    # ОСНОВНОЙ ВХОД: свободное сообщение в чате
    # ══════════════════════════════════════════════════════

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        text = update.message.text.strip()
        ui_lang = _detect_lang(text, user.get("lang", "ru"))
        message = update.message
        state = await self._load_state(user_id)

        # 1) Идёт подготовка документа — это, скорее всего, ответ на мой вопрос.
        task = state.get("doc_task")
        if task and not self._task_alive(task):
            state["doc_task"] = None
            task = None
        if task:
            handled = await self._advance_task(message, context, user, ui_lang, state, text)
            if handled:
                return
            # сообщение не про документ — отвечаем как обычно, но помним про незавершённое

        # 2) Новая просьба сделать документ.
        elif _looks_like_action_request(text) and self.anthropic_api_key:
            async with typing_action(context.bot, message.chat_id):
                classified = await self._classify_document_intent(text, user.get("role", "teacher"))
            if classified.get("doc_type"):
                await self._begin_task(message, context, user, ui_lang, classified["doc_type"], text)
                return

        # 3) Обычный разговор.
        if not self._configured():
            await message.reply_text(
                "Я пока учусь вести беседу 🙂 Напишите, какой документ нужен, или нажмите /menu."
                if ui_lang == "ru" else
                "Мен әзірге сөйлесуді үйреніп жатырмын 🙂 Қандай құжат керек екенін жазыңыз немесе /menu басыңыз.")
            return

        await self._chat_reply(message, context, user, ui_lang, state, text)

    async def _chat_reply(self, message, context, user, ui_lang: str, state: dict, text: str):
        user_id = user["tg_id"]
        role = user.get("role", "teacher")
        schedule_json = await self.db.get_schedule(user_id)
        schedule = json.loads(schedule_json) if schedule_json else None
        students = await self.db.get_students(user_id)
        day_name, suggestions = _find_schedule_suggestions(schedule, role) if schedule else (None, None)

        pending = state.get("pending")
        if pending and not suggestions:
            day_name = pending.get("day_name")
            suggestions = pending.get("options")

        gap = self._gap_hours(state)
        should_greet = gap is None or gap >= GREET_GAP_HOURS

        task_hint = ""
        task = state.get("doc_task")
        if task and self._task_alive(task):
            fields = self._task_fields(task, user, ui_lang)
            miss = ", ".join(f["label"] for f in self._missing(task, fields, user))
            task_hint = (f"У пользователя НЕЗАВЕРШЕНА подготовка документа «{task['doc_name']}»"
                         + (f" (не хватает: {miss})" if miss else "")
                         + ". Ответь на его сообщение, а в конце одной короткой фразой напомни, что можно продолжить.")

        system_prompt = self._build_system_prompt(
            user, ui_lang, day_name, suggestions, state, should_greet, task_hint,
            schedule=schedule, students=students
        )
        async with typing_action(context.bot, message.chat_id):
            result = await self._call_ai(system_prompt, state.get("history", []), text)

        reply = result.get("reply")
        if not reply:
            reply = "Извините, не расслышал — повторите, пожалуйста? 🙂" if ui_lang == "ru" else "Кешіріңіз, қайталап жіберіңізші?"
        reply = _fix_greeting_time(reply, ui_lang) if should_greet else _strip_leading_greeting(reply)

        action = result.get("action", "none")
        doc_type = result.get("doc_type")
        is_pro = bool(user.get("subscribed"))

        await message.reply_text(reply)

        self._remember(state, "user", text)
        self._remember(state, "assistant", reply)
        self._merge_facts(state, result)

        if action == "propose" and is_pro:
            state["pending"] = {"day_name": day_name, "options": suggestions, "proposed_at": now_local().isoformat()}
            await self._save_state(user_id, state)
            return

        state["pending"] = None
        await self._save_state(user_id, state)

        if action == "generate" and is_pro and doc_type:
            allowed = self._allowed_doc_types_for_role(role)
            if doc_type in allowed:
                await self._begin_task(message, context, user, ui_lang, doc_type, None)

    # ══════════════════════════════════════════════════════
    # СОВМЕСТИМОСТЬ: вызывается из voice.py
    # ══════════════════════════════════════════════════════

    async def _start_direct_document(self, update, context, user, lang, doc_type, known_data: dict | None = None):
        """Запускает подготовку документа из голосового/текстового запроса. Недостающие
        данные агент теперь собирает сам в разговоре (раньше — жёсткий опросник кнопками)."""
        first_text = None
        if known_data:
            first_text = "; ".join(f"{k}: {v}" for k, v in known_data.items() if v)
        await self._begin_task(update.message, context, user, lang, doc_type, first_text)

    async def _start_generation(self, update, context, user_id, user, lang, doc_type):
        await self._begin_task(update.message, context, user, lang, doc_type, None)

    # ══════════════════════════════════════════════════════
    # ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ (используется notifications.py)
    # ══════════════════════════════════════════════════════

    async def build_reminder(self, user: dict, lang: str) -> tuple[str, dict | None]:
        """Возвращает (текст напоминания, pending-предложение_или_None)."""
        user_id = user["tg_id"]
        role = user.get("role", "teacher")
        schedule_json = await self.db.get_schedule(user_id)
        schedule = json.loads(schedule_json) if schedule_json else None
        students = await self.db.get_students(user_id)
        day_name, suggestions = _find_schedule_suggestions(schedule, role) if schedule else (None, None)

        def _static():
            from handlers.texts import t
            name_parts = (user.get("name") or "").split()
            name = name_parts[0] if name_parts else ("коллега" if lang == "ru" else "әріптес")
            key = "notif_reminder_kg" if role == "kindergarten" else "notif_reminder"
            return t(lang, key, name=name), None

        if not self._configured():
            return _static()

        state = await self._load_state(user_id)
        system_prompt = self._build_system_prompt(
            user, lang, day_name, suggestions, state, should_greet=True,
            schedule=schedule, students=students
        )
        system_prompt += (
            "\n\nЭто ПРОАКТИВНОЕ напоминание — пользователь ничего не писал, ты пишешь первым, потому что он давно не "
            "создавал документы. Поздоровайся, мягко напомни о себе — без спама, по-дружески, 1–2 предложения."
        )
        result = await self._call_ai(system_prompt, [], "[система: отправь проактивное напоминание]")
        reply = result.get("reply")
        if not reply:
            return _static()
        reply = _fix_greeting_time(reply, lang)

        pending = None
        if result.get("action") == "propose" and bool(user.get("subscribed")):
            pending = {"day_name": day_name, "options": suggestions, "proposed_at": now_local().isoformat()}
        return reply, pending
