import os
import json
import anthropic
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t, TEXTS
from handlers.rag_base import get_system_prompt, get_kg_system_prompt, SELF_EVAL_PROMPT
from database import Database

DOC_QUESTIONS = {
    "ru": {
        "lesson_plan": [
            {"key": "subject_class", "q": "📚 Предмет и класс?\n\n_Пример: Математика, 7А_"},
            {"key": "topic",         "q": "📖 Тема урока?"},
            {"key": "duration",      "q": "⏱ Длительность?\n\n_Пример: 45 минут_"},
            {"key": "goals",         "q": "🎯 Цели обучения?\n\n_Или напишите «автоматически»_"},
        ],
        "calendar_plan": [
            {"key": "subject_class",  "q": "📚 Предмет и класс?\n\n_Пример: Алгебра, 8А_"},
            {"key": "month",          "q": "📅 На какой месяц?\n\n_Пример: Ноябрь 2024_"},
            {"key": "hours_per_week", "q": "⏰ Часов в неделю?\n\n_Пример: 3 часа_"},
            {"key": "topics",         "q": "📋 Темы уроков через запятую\n\n_Или напишите «по программе МОН»_"},
        ],
        "lesson_summary": [
            {"key": "subject_class", "q": "📚 Предмет и класс?"},
            {"key": "topic",         "q": "📖 Тема урока?"},
            {"key": "key_points",    "q": "🔑 Ключевые моменты?\n\n_Или напишите «автоматически»_"},
        ],
        "monthly_report": [
            {"key": "period",      "q": "📅 За какой период?\n\n_Пример: Октябрь 2024_"},
            {"key": "classes",     "q": "🏫 Классы?\n\n_Пример: 7А, 8Б, 9В_"},
            {"key": "performance", "q": "📊 Успеваемость?\n\n_Пример: 7А — 65%/90%, 8Б — 70%/95%\n(% качества / % успеваемости)_"},
            {"key": "extra",       "q": "🏆 Внеклассные мероприятия, олимпиады?\n\n_Если нет — напишите «нет»_"},
        ],
        "control_analysis": [
            {"key": "subject_class", "q": "📚 Предмет и класс?"},
            {"key": "date",          "q": "📅 Дата контрольной работы?"},
            {"key": "results",       "q": "📊 Результаты?\n\n_Пример: 5 — 3 уч., 4 — 8 уч., 3 — 5 уч., 2 — 2 уч._"},
            {"key": "topic",         "q": "📖 Тема контрольной работы?"},
        ],
        "characteristic": [
            {"key": "student_name", "q": "👤 ФИО ученика и класс?\n\n_Или выберите из базы ниже_"},
            {"key": "performance",  "q": "📊 Успеваемость?\n\n_Пример: отличник, хорошист, троечник_"},
            {"key": "behavior",     "q": "😊 Поведение и характер?\n\n_Пример: дисциплинированный, активный_"},
            {"key": "activities",   "q": "🏆 Участие в мероприятиях?\n\n_Если нет — напишите «нет»_"},
            {"key": "purpose",      "q": "📋 Цель характеристики?\n\n_Пример: по месту требования_"},
        ],
        "absence_cert": [
            {"key": "student_name",  "q": "👤 ФИО ученика и класс?"},
            {"key": "absence_dates", "q": "📅 Даты отсутствия?\n\n_Пример: 14-16 октября 2024_"},
            {"key": "reason",        "q": "❓ Причина?\n\n_Пример: болезнь (справка есть)_"},
        ],
        "discipline_act": [
            {"key": "student_name", "q": "👤 ФИО ученика и класс?"},
            {"key": "date",         "q": "📅 Дата нарушения?"},
            {"key": "violation",    "q": "⚠️ Описание нарушения?"},
            {"key": "witnesses",    "q": "👥 Свидетели?\n\n_Если нет — напишите «нет»_"},
        ],
        "gratitude_letter": [
            {"key": "student_name", "q": "👤 ФИО ученика и класс?"},
            {"key": "achievement",  "q": "🏆 За что награждается?\n\n_Пример: победа на олимпиаде по математике_"},
        ],
        "parent_letter": [
            {"key": "student_name", "q": "👤 ФИО ученика и класс?"},
            {"key": "topic",        "q": "📋 Тема письма?\n\n_Пример: успеваемость, поведение_"},
            {"key": "details",      "q": "📝 Детали письма?"},
        ],
        "vacation_request": [
            {"key": "vacation_type", "q": "🏖 Вид отпуска?\n\n1️⃣ Ежегодный трудовой (56 дней)\n2️⃣ За свой счёт\n3️⃣ Учебный\n\n_Напишите номер или название_"},
            {"key": "dates",         "q": "📅 Даты?\n\n_Пример: с 01.07.2025 по 25.08.2025_"},
        ],
        "explanation": [
            {"key": "absence_date", "q": "📅 Дата отсутствия или опоздания?\n\n_Пример: 15 октября 2024_"},
            {"key": "reason",       "q": "❓ Причина?\n\n_Пример: болезнь, задержка транспорта_"},
            {"key": "documents",    "q": "📎 Есть подтверждающие документы?\n\n_Если нет — напишите «нет»_"},
        ],
        "announcement": [
            {"key": "event",    "q": "📢 Что за мероприятие?\n\n_Пример: родительское собрание, олимпиада_"},
            {"key": "datetime", "q": "📅 Дата и время?\n\n_Пример: 20 ноября, 18:00_"},
            {"key": "location", "q": "📍 Место проведения?\n\n_Пример: кабинет 205, актовый зал_"},
        ],
        # ── САДИК ──────────────────────────────────────
        "kg_lesson_plan": [
            {"key": "age_group", "q": "👶 Возрастная группа?\n\n_Пример: средняя группа (4-5 лет)_"},
            {"key": "topic",     "q": "📖 Тема занятия?\n\n_Пример: Осень. Признаки осени_"},
            {"key": "section",   "q": "📚 Образовательная область?\n\n_Пример: Познание, Творчество_"},
            {"key": "duration",  "q": "⏱ Длительность?\n\n_Пример: 20 минут_"},
        ],
        "kg_tech_card": [
            {"key": "age_group", "q": "👶 Возрастная группа?"},
            {"key": "topic",     "q": "📖 Тема занятия?"},
            {"key": "section",   "q": "📚 Образовательная область?"},
            {"key": "tasks",     "q": "🎯 Задачи?\n\n_Или напишите «автоматически»_"},
        ],
        "kg_monthly_plan": [
            {"key": "age_group", "q": "👶 Возрастная группа?"},
            {"key": "month",     "q": "📅 На какой месяц?\n\n_Пример: Ноябрь 2024_"},
            {"key": "theme",     "q": "🌟 Тема месяца?\n\n_Пример: Моя семья_"},
        ],
        "kg_yearly_plan": [
            {"key": "age_group", "q": "👶 Возрастная группа?"},
            {"key": "year",      "q": "📅 Учебный год?\n\n_Пример: 2024-2025_"},
            {"key": "goals",     "q": "🎯 Основные цели?\n\n_Или напишите «автоматически»_"},
        ],
        "kg_cyclogram": [
            {"key": "age_group", "q": "👶 Возрастная группа?"},
            {"key": "month",     "q": "📅 Месяц?\n\n_Пример: Октябрь 2024_"},
        ],
        "kg_report": [
            {"key": "age_group",  "q": "👶 Возрастная группа?"},
            {"key": "period",     "q": "📅 За какой период?\n\n_Пример: I квартал 2024-2025_"},
            {"key": "attendance", "q": "📊 Посещаемость?\n\n_Пример: средняя 18 детей из 22_"},
            {"key": "extra",      "q": "🏆 Мероприятия, достижения?\n\n_Если нет — «нет»_"},
        ],
        "kg_characteristic": [
            {"key": "child_name",  "q": "👶 ФИО ребёнка и возраст?"},
            {"key": "behavior",    "q": "😊 Поведение и характер?"},
            {"key": "development", "q": "📊 Уровень развития?\n\n_Пример: соответствует возрасту_"},
            {"key": "purpose",     "q": "📋 Цель характеристики?\n\n_Пример: для ПМПК_"},
        ],
        "kg_parent_letter": [
            {"key": "child_name", "q": "👶 ФИО ребёнка?"},
            {"key": "topic",      "q": "📋 Тема письма?"},
            {"key": "details",    "q": "📝 Детали?"},
        ],
    },
    "kz": {
        "lesson_plan": [
            {"key": "subject_class", "q": "📚 Пән және сынып?\n\n_Мысалы: Математика, 7А_"},
            {"key": "topic",         "q": "📖 Сабақтың тақырыбы?"},
            {"key": "duration",      "q": "⏱ Ұзақтығы?\n\n_Мысалы: 45 минут_"},
            {"key": "goals",         "q": "🎯 Оқу мақсаттары?\n\n_Немесе «автоматты» деп жазыңыз_"},
        ],
        "monthly_report": [
            {"key": "period",      "q": "📅 Қандай кезең?\n\n_Мысалы: Қазан 2024_"},
            {"key": "classes",     "q": "🏫 Сыныптар?\n\n_Мысалы: 7А, 8Б, 9В_"},
            {"key": "performance", "q": "📊 Үлгерім?\n\n_Мысалы: 7А — 65%/90%_"},
            {"key": "extra",       "q": "🏆 Сыныптан тыс іс-шаралар?\n\n_Жоқ болса «жоқ» деп жазыңыз_"},
        ],
        "vacation_request": [
            {"key": "vacation_type", "q": "🏖 Демалыс түрі?\n\n1️⃣ Жылдық еңбек (56 күн)\n2️⃣ Ақысыз\n3️⃣ Оқу\n\n_Нөмірін жазыңыз_"},
            {"key": "dates",         "q": "📅 Күндері?\n\n_Мысалы: 01.07.2025-тен 25.08.2025-ке дейін_"},
        ],
        "explanation": [
            {"key": "absence_date", "q": "📅 Болмаған күн?\n\n_Мысалы: 2024 жылғы 15 қазан_"},
            {"key": "reason",       "q": "❓ Себебі?\n\n_Мысалы: ауырып қалу_"},
            {"key": "documents",    "q": "📎 Растайтын құжаттар?\n\n_Жоқ болса «жоқ»_"},
        ],
        "characteristic": [
            {"key": "student_name", "q": "👤 Оқушының аты-жөні мен сыныбы?"},
            {"key": "performance",  "q": "📊 Үлгерімі?\n\n_Мысалы: үздік, жақсы_"},
            {"key": "behavior",     "q": "😊 Мінез-құлқы?"},
            {"key": "activities",   "q": "🏆 Іс-шараларға қатысуы?\n\n_Немесе «жоқ»_"},
            {"key": "purpose",      "q": "📋 Мінездеме мақсаты?\n\n_Мысалы: талап еткен жерге_"},
        ],
        "announcement": [
            {"key": "event",    "q": "📢 Іс-шара?\n\n_Мысалы: ата-аналар жиналысы_"},
            {"key": "datetime", "q": "📅 Күні мен уақыты?\n\n_Мысалы: 20 қараша, 18:00_"},
            {"key": "location", "q": "📍 Орны?\n\n_Мысалы: 205 кабинет_"},
        ],
        "absence_cert": [
            {"key": "student_name",  "q": "👤 Оқушының аты-жөні мен сыныбы?"},
            {"key": "absence_dates", "q": "📅 Болмаған күндер?"},
            {"key": "reason",        "q": "❓ Себебі?"},
        ],
        "lesson_summary": [
            {"key": "subject_class", "q": "📚 Пән және сынып?"},
            {"key": "topic",         "q": "📖 Тақырып?"},
            {"key": "key_points",    "q": "🔑 Негізгі тұстар немесе «автоматты»?"},
        ],
        "calendar_plan": [
            {"key": "subject_class",  "q": "📚 Пән және сынып?"},
            {"key": "month",          "q": "📅 Қандай ай?\n\n_Мысалы: Қараша 2024_"},
            {"key": "hours_per_week", "q": "⏰ Аптасына неше сағат?"},
            {"key": "topics",         "q": "📋 Тақырыптар немесе «МОН бағдарламасы бойынша»"},
        ],
        "control_analysis": [
            {"key": "subject_class", "q": "📚 Пән және сынып?"},
            {"key": "date",          "q": "📅 Бақылау жұмысының күні?"},
            {"key": "results",       "q": "📊 Нәтижелер?\n\n_Мысалы: 5 — 3 оқ., 4 — 8 оқ._"},
            {"key": "topic",         "q": "📖 Тақырыбы?"},
        ],
        "discipline_act": [
            {"key": "student_name", "q": "👤 Оқушының аты-жөні мен сыныбы?"},
            {"key": "date",         "q": "📅 Бұзылған күн?"},
            {"key": "violation",    "q": "⚠️ Бұзушылықтың сипаттамасы?"},
            {"key": "witnesses",    "q": "👥 Куәгерлер?\n\n_Жоқ болса «жоқ»_"},
        ],
        "gratitude_letter": [
            {"key": "student_name", "q": "👤 Оқушының аты-жөні мен сыныбы?"},
            {"key": "achievement",  "q": "🏆 Не үшін марапатталады?"},
        ],
        "parent_letter": [
            {"key": "student_name", "q": "👤 Оқушының аты-жөні мен сыныбы?"},
            {"key": "topic",        "q": "📋 Хат тақырыбы?"},
            {"key": "details",      "q": "📝 Мәліметтер?"},
        ],
        # ── БАЛАБАҚША ──────────────────────────────────
        "kg_lesson_plan": [
            {"key": "age_group", "q": "👶 Жас тобы?\n\n_Мысалы: орта топ (4-5 жас)_"},
            {"key": "topic",     "q": "📖 Сабақ тақырыбы?"},
            {"key": "section",   "q": "📚 Білім беру саласы?\n\n_Мысалы: Танымдық, Шығармашылық_"},
            {"key": "duration",  "q": "⏱ Ұзақтығы?\n\n_Мысалы: 20 минут_"},
        ],
        "kg_tech_card": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "topic",     "q": "📖 Сабақ тақырыбы?"},
            {"key": "section",   "q": "📚 Білім беру саласы?"},
            {"key": "tasks",     "q": "🎯 Міндеттер?\n\n_Немесе «автоматты» деп жазыңыз_"},
        ],
        "kg_monthly_plan": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "month",     "q": "📅 Қандай ай?\n\n_Мысалы: Қараша 2024_"},
            {"key": "theme",     "q": "🌟 Ай тақырыбы?\n\n_Мысалы: Менің отбасым_"},
        ],
        "kg_yearly_plan": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "year",      "q": "📅 Оқу жылы?\n\n_Мысалы: 2024-2025_"},
            {"key": "goals",     "q": "🎯 Жылдық мақсаттар?\n\n_Немесе «автоматты»_"},
        ],
        "kg_cyclogram": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "month",     "q": "📅 Ай?\n\n_Мысалы: Қазан 2024_"},
        ],
        "kg_report": [
            {"key": "age_group",  "q": "👶 Жас тобы?"},
            {"key": "period",     "q": "📅 Қандай кезең?\n\n_Мысалы: I тоқсан 2024-2025_"},
            {"key": "attendance", "q": "📊 Қатысуы?\n\n_Мысалы: орташа 18 бала 22-ден_"},
            {"key": "extra",      "q": "🏆 Іс-шаралар?\n\n_Жоқ болса «жоқ»_"},
        ],
        "kg_characteristic": [
            {"key": "child_name",  "q": "👶 Баланың аты-жөні және жасы?"},
            {"key": "behavior",    "q": "😊 Мінез-құлқы?"},
            {"key": "development", "q": "📊 Даму деңгейі?\n\n_Мысалы: жасына сай_"},
            {"key": "purpose",     "q": "📋 Мінездеме мақсаты?\n\n_Мысалы: ПМПК үшін_"},
        ],
        "kg_parent_letter": [
            {"key": "child_name", "q": "👶 Баланың аты-жөні?"},
            {"key": "topic",      "q": "📋 Хат тақырыбы?"},
            {"key": "details",    "q": "📝 Мәліметтер?"},
        ],
    },
    "en": {
        "lesson_plan": [
            {"key": "subject_class", "q": "📚 Subject and class?\n\n_Example: Mathematics, Grade 7A_"},
            {"key": "topic",         "q": "📖 Lesson topic?"},
            {"key": "duration",      "q": "⏱ Duration?\n\n_Example: 45 minutes_"},
            {"key": "goals",         "q": "🎯 Learning objectives?\n\n_Or write «automatic»_"},
        ],
        "vacation_request": [
            {"key": "vacation_type", "q": "🏖 Type of leave?\n\n1️⃣ Annual leave\n2️⃣ Unpaid leave\n3️⃣ Study leave"},
            {"key": "dates",         "q": "📅 Dates?\n\n_Example: from 01.07.2025 to 25.08.2025_"},
        ],
        "explanation": [
            {"key": "absence_date", "q": "📅 Date of absence?\n\n_Example: October 15, 2024_"},
            {"key": "reason",       "q": "❓ Reason?\n\n_Example: illness, transport delay_"},
            {"key": "documents",    "q": "📎 Supporting documents?\n\n_If none — write «none»_"},
        ],
        "announcement": [
            {"key": "event",    "q": "📢 Event?\n\n_Example: parent meeting, olympiad_"},
            {"key": "datetime", "q": "📅 Date and time?\n\n_Example: November 20, 6:00 PM_"},
            {"key": "location", "q": "📍 Location?\n\n_Example: room 205, assembly hall_"},
        ],
    }
}

DOC_NAMES = {
    "ru": {
        "lesson_plan": "Краткосрочный план (КСП)",
        "calendar_plan": "Календарный план",
        "lesson_summary": "Конспект урока",
        "monthly_report": "Отчёт учителя",
        "control_analysis": "Анализ контрольной работы",
        "characteristic": "Характеристика ученика",
        "absence_cert": "Справка об отсутствии",
        "discipline_act": "Акт о нарушении дисциплины",
        "gratitude_letter": "Благодарственное письмо",
        "parent_letter": "Письмо родителям",
        "vacation_request": "Заявление на отпуск",
        "explanation": "Объяснительная записка",
        "announcement": "Объявление",
        "kg_lesson_plan": "Конспект занятия (садик)",
        "kg_tech_card": "Технологическая карта занятия",
        "kg_monthly_plan": "Перспективный план на месяц",
        "kg_yearly_plan": "Годовой план работы",
        "kg_cyclogram": "Циклограмма воспитателя",
        "kg_report": "Отчёт воспитателя",
        "kg_characteristic": "Характеристика на ребёнка",
        "kg_parent_letter": "Письмо родителям (садик)",
    },
    "kz": {
        "lesson_plan": "Қысқамерзімді жоспар (ҚМЖ)",
        "calendar_plan": "Күнтізбелік жоспар",
        "lesson_summary": "Сабақ конспектісі",
        "monthly_report": "Мұғалім есебі",
        "control_analysis": "Бақылау жұмысын талдау",
        "characteristic": "Оқушы мінездемесі",
        "absence_cert": "Болмағаны туралы анықтама",
        "discipline_act": "Тәртіп бұзу актісі",
        "gratitude_letter": "Алғыс хат",
        "parent_letter": "Ата-аналарға хат",
        "vacation_request": "Демалыс өтініші",
        "explanation": "Түсіндірме хат",
        "announcement": "Хабарландыру",
        "kg_lesson_plan": "Сабақ конспектісі (балабақша)",
        "kg_tech_card": "Сабақтың технологиялық картасы",
        "kg_monthly_plan": "Айлық перспективалық жоспар",
        "kg_yearly_plan": "Жылдық жұмыс жоспары",
        "kg_cyclogram": "Тәрбиешінің циклограммасы",
        "kg_report": "Тәрбиеші есебі",
        "kg_characteristic": "Балаға мінездеме",
        "kg_parent_letter": "Ата-аналарға хат (балабақша)",
    },
    "en": {
        "lesson_plan": "Lesson Plan",
        "calendar_plan": "Monthly Calendar Plan",
        "lesson_summary": "Lesson Summary",
        "monthly_report": "Teacher Report",
        "control_analysis": "Test Analysis",
        "characteristic": "Student Reference",
        "absence_cert": "Absence Certificate",
        "discipline_act": "Disciplinary Act",
        "gratitude_letter": "Gratitude Letter",
        "parent_letter": "Letter to Parents",
        "vacation_request": "Leave Application",
        "explanation": "Explanatory Note",
        "announcement": "Announcement",
    }
}

CAT_DOCS = {
    "planning": ["lesson_plan", "calendar_plan", "lesson_summary"],
    "reports":  ["monthly_report", "control_analysis"],
    "students": ["characteristic", "absence_cert", "discipline_act", "gratitude_letter", "parent_letter"],
    "personal": ["vacation_request", "explanation", "announcement"],
    "kg_planning": ["kg_lesson_plan", "kg_tech_card", "kg_monthly_plan", "kg_yearly_plan", "kg_cyclogram"],
    "kg_reports":  ["kg_report"],
    "kg_children": ["kg_characteristic", "kg_parent_letter"],
}

# Красивые разделители для дизайна
DIVIDER = "─" * 20

def _progress_bar(current, total):
    filled = "█" * current
    empty  = "░" * (total - current)
    return f"{filled}{empty} {current}/{total}"

def _doc_lang_name(doc_lang):
    return {"ru": "🇷🇺 Русский", "kz": "🇰🇿 Қазақша", "en": "🇬🇧 English"}.get(doc_lang, doc_lang)


class DocumentHandler:
    def __init__(self, db: Database, api_key: str):
        self.db      = db
        self.api_key = api_key

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query   = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"

        # ── Отмена генерации ──
        if data == "doc_cancel":
            context.user_data.clear()
            from handlers.main_menu import MainMenuHandler
            await query.edit_message_text(
                "❌ Отменено." if lang == "ru" else "❌ Болдырылмады."
            )
            await MainMenuHandler(self.db)._send_main_menu(
                query.message.chat_id, context, user_id, lang
            )
            return

        # ── Выбор языка документа ──
        if data.startswith("doc_lang_"):
            doc_lang = data.split("_")[2]
            context.user_data["doc_lang"] = doc_lang
            doc_type = context.user_data.get("doc_type", "")
            q_lang   = doc_lang if doc_lang in DOC_QUESTIONS else "ru"
            qs = DOC_QUESTIONS.get(q_lang, DOC_QUESTIONS["ru"]).get(doc_type, [])
            if not qs:
                fallback_q = {
                    "ru": "✍️ Опишите подробно что нужно создать:",
                    "kz": "✍️ Не жасау керектігін сипаттаңыз:",
                    "en": "✍️ Describe what you need in detail:",
                }
                qs = [{"key": "description", "q": fallback_q.get(doc_lang, "✍️ Describe:")}]
            context.user_data["questions"] = qs
            context.user_data["step"]      = "waiting_answer"
            doc_name = DOC_NAMES.get(doc_lang, DOC_NAMES["ru"]).get(doc_type, doc_type)
            await self._ask_question(query.message, context, lang, 0, edit=True, query=query, doc_name=doc_name)
            return

        # ── Категория документов ──
        if data.startswith("cat_"):
            cat = data[4:]  # убираем "cat_" целиком, а не split по "_"
            await self._show_doc_list(query, lang, cat)

        # ── Выбор конкретного документа ──
        elif data.startswith("doc_"):
            doc_type = data[4:]
            await self._start_doc(query, context, user_id, user, lang, doc_type)

        # ── Помощь с текстом ──
        elif data.startswith("ans_help_"):
            field = data[9:]
            context.user_data["help_field"] = field
            context.user_data["step"]       = "help_write"
            doc_type = context.user_data.get("doc_type", "")
            text = (
                f"✍️ *Помощь с текстом*\n\n"
                f"Напишите кратко что хотите сказать — я оформлю официально:"
            )
            kb = [[InlineKeyboardButton("❌ Отмена", callback_data="menu_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # ── Выбрать ученика из базы ──
        elif data.startswith("gen_student_"):
            student_id = int(data[12:])
            await self._use_student_data(query, context, user_id, user, lang, student_id)

    async def _show_doc_list(self, query, lang, cat):
        docs  = CAT_DOCS.get(cat, [])
        names = DOC_NAMES.get(lang, DOC_NAMES["ru"])
        keyboard = [[InlineKeyboardButton(names.get(d, d), callback_data=f"doc_{d}")] for d in docs]
        keyboard.append([InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_create")])

        # Названия категорий садика и школы
        cat_names = {
            "ru": {
                "planning": "Планирование", "reports": "Отчёты",
                "students": "По ученикам", "personal": "Личные документы",
                "kg_planning": "Планирование", "kg_reports": "Отчёты",
                "kg_children": "По детям",
            },
            "kz": {
                "planning": "Жоспарлау", "reports": "Есептер",
                "students": "Оқушылар бойынша", "personal": "Жеке құжаттар",
                "kg_planning": "Жоспарлау", "kg_reports": "Есептер",
                "kg_children": "Балалар бойынша",
            },
        }
        cat_name = cat_names.get(lang, cat_names["ru"]).get(cat, cat)
        header = "📂 *{cat}*\n{div}\nВыберите тип документа:" if lang == "ru" else "📂 *{cat}*\n{div}\nҚұжат түрін таңдаңыз:"
        await query.edit_message_text(
            header.format(cat=cat_name, div=DIVIDER),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _start_doc(self, query, context, user_id, user, lang, doc_type):
        subscribed = user.get("subscribed", 0)
        free_used  = user.get("free_used", 0)

        if not subscribed and free_used >= 3:
            keyboard = [
                [InlineKeyboardButton("⭐ Оформить подписку", callback_data="prof_sub")],
                [InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_main")],
            ]
            await query.edit_message_text(
                t(lang, "limit_reached"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Для документов по ученикам — предложить выбрать из базы
        if doc_type in ["characteristic", "absence_cert", "discipline_act", "gratitude_letter", "parent_letter"]:
            if user.get("is_class_teacher"):
                students = await self.db.get_students(user_id)
                if students:
                    keyboard = []
                    for s in students[:8]:
                        keyboard.append([InlineKeyboardButton(
                            f"👤 {s['name']} ({s['class_name']})",
                            callback_data=f"gen_student_{s['id']}"
                        )])
                    keyboard.append([InlineKeyboardButton(
                        "✍️ Ввести вручную" if lang == "ru" else "✍️ Қолмен енгізу",
                        callback_data="gen_student_0"
                    )])
                    context.user_data["doc_type"] = doc_type
                    text = "👥 *Выберите ученика из базы:*" if lang == "ru" else "👥 *Базадан оқушыны таңдаңыз:*"
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
                    return

        context.user_data["doc_type"]    = doc_type
        context.user_data["doc_answers"] = {}
        context.user_data["q_index"]     = 0

        # ── Спросить язык документа ──
        doc_name = DOC_NAMES.get(lang, DOC_NAMES["ru"]).get(doc_type, doc_type)
        text = (
            f"📄 *{doc_name}*\n"
            f"{DIVIDER}\n"
            f"🌐 На каком языке создать документ?"
        ) if lang == "ru" else (
            f"📄 *{doc_name}*\n"
            f"{DIVIDER}\n"
            f"🌐 Құжатты қандай тілде жасау керек?"
        )
        keyboard = [
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="doc_lang_ru"),
                InlineKeyboardButton("🇰🇿 Қазақша", callback_data="doc_lang_kz"),
            ],
            [InlineKeyboardButton("🇬🇧 English",    callback_data="doc_lang_en")],
            [InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_create")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _use_student_data(self, query, context, user_id, user, lang, student_id):
        doc_type = context.user_data.get("doc_type", "")
        context.user_data["doc_answers"] = {}
        context.user_data["q_index"]     = 0

        if student_id > 0:
            student = await self.db.get_student(student_id)
            if student:
                grades       = json.loads(student.get("grades", "{}"))
                achievements = json.loads(student.get("achievements", "[]"))
                grades_str   = ", ".join(f"{k}-{v}" for k, v in grades.items()) if grades else "нет данных"
                achieve_str  = ", ".join(achievements) if achievements else "нет"
                context.user_data["doc_answers"]["student_name"] = f"{student['name']}, {student['class_name']} класс"
                context.user_data["doc_answers"]["performance"]  = grades_str
                context.user_data["doc_answers"]["activities"]   = achieve_str
                context.user_data["doc_answers"]["behavior"]     = student.get("behavior", "хорошее")

        # Спросить язык документа
        doc_name = DOC_NAMES.get(lang, DOC_NAMES["ru"]).get(doc_type, doc_type)
        text = f"📄 *{doc_name}*\n{DIVIDER}\n🌐 На каком языке создать документ?" if lang == "ru" else f"📄 *{doc_name}*\n{DIVIDER}\n🌐 Құжатты қандай тілде жасау керек?"
        keyboard = [
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="doc_lang_ru"),
             InlineKeyboardButton("🇰🇿 Қазақша", callback_data="doc_lang_kz")],
            [InlineKeyboardButton("🇬🇧 English",  callback_data="doc_lang_en")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _ask_question(self, message, context, lang, idx, edit=False, query=None, doc_name=""):
        qs = context.user_data.get("questions", [])

        # Пропускаем вопросы у которых уже есть ответ
        while idx < len(qs) and qs[idx]["key"] in context.user_data.get("doc_answers", {}):
            idx += 1

        context.user_data["q_index"] = idx

        if idx >= len(qs):
            if edit and query:
                await query.edit_message_text("⏳ Начинаю генерацию..." if lang == "ru" else "⏳ Жасалуда...")
            await self._generate(message, context, lang)
            return

        q      = qs[idx]
        total  = len(qs)
        bar    = _progress_bar(idx + 1, total)
        doc_lang = context.user_data.get("doc_lang", lang)

        text = (
            f"📄 *{doc_name}* {_doc_lang_name(doc_lang)}\n"
            f"{bar}\n"
            f"{DIVIDER}\n\n"
            f"{q['q']}"
        ) if doc_name else (
            f"{bar}\n{DIVIDER}\n\n{q['q']}"
        )

        keyboard = [
            [InlineKeyboardButton("❌ " + t(lang, "cancel"), callback_data="doc_cancel")],
        ]

        if edit and query:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        text    = update.message.text.strip()
        step    = context.user_data.get("step", "")

        if len(text) < 1:
            await update.message.reply_text(t(lang, "val_too_short"))
            return
        if len(text) > 500:
            await update.message.reply_text(t(lang, "val_too_long"))
            return

        if step == "waiting_answer":
            qs  = context.user_data.get("questions", [])
            idx = context.user_data.get("q_index", 0)
            if idx < len(qs):
                context.user_data["doc_answers"][qs[idx]["key"]] = text
            doc_type = context.user_data.get("doc_type", "")
            doc_lang = context.user_data.get("doc_lang", lang)
            doc_name = DOC_NAMES.get(doc_lang, DOC_NAMES["ru"]).get(doc_type, "")
            await self._ask_question(update.message, context, lang, idx + 1, doc_name=doc_name)

        elif step == "help_write":
            await self._handle_help_write(update, context, user, lang, text)

    async def _handle_help_write(self, update, context, user, lang, user_input):
        field    = context.user_data.get("help_field", "")
        doc_type = context.user_data.get("doc_type", "")
        doc_lang = context.user_data.get("doc_lang", lang)
        doc_name = DOC_NAMES.get(doc_lang, DOC_NAMES["ru"]).get(doc_type, "")

        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = (
            f"Учитель просит помочь составить текст для поля «{field}» документа «{doc_name}».\n"
            f"Предмет: {user.get('subject')}, классы: {user.get('classes')}.\n"
            f"Что учитель хочет сказать: {user_input}\n\n"
            f"Предложи 2 коротких варианта официального текста для вставки в документ."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        result = msg.content[0].text
        context.user_data["step"] = "waiting_answer"
        await update.message.reply_text(
            f"✍️ *Варианты текста:*\n\n{result}\n\n{DIVIDER}\n_Скопируйте нужный вариант и отправьте_",
            parse_mode=ParseMode.MARKDOWN
        )

    async def _generate(self, message, context, lang):
        user_id  = message.chat_id
        user     = await self.db.get_user(user_id)
        doc_type = context.user_data.get("doc_type", "")
        doc_lang = context.user_data.get("doc_lang", lang)
        answers  = context.user_data.get("doc_answers", {})
        doc_name = DOC_NAMES.get(doc_lang, DOC_NAMES["ru"]).get(doc_type, doc_type)

        # Красивое сообщение о генерации
        gen_msgs = {
            "ru": f"⚙️ *Генерирую {doc_name}...*\n\n🌐 Язык: {_doc_lang_name(doc_lang)}\n⭐ Качество: автоматическая проверка\n\n_Напиши /cancel чтобы отменить_",
            "kz": f"⚙️ *{doc_name} жасалуда...*\n\n🌐 Тіл: {_doc_lang_name(doc_lang)}\n⭐ Сапа: автоматты тексеру\n\n_Болдырмау үшін /cancel жазыңыз_",
            "en": f"⚙️ *Generating {doc_name}...*\n\n🌐 Language: {_doc_lang_name(doc_lang)}\n⭐ Quality: auto-check\n\n_Type /cancel to stop_",
        }
        await message.reply_text(gen_msgs.get(lang, gen_msgs["ru"]), parse_mode=ParseMode.MARKDOWN)

        answers_text  = "\n".join(f"- {k}: {v}" for k, v in answers.items())
        # Выбираем промпт — садик или школа
        if doc_type.startswith("kg_"):
            system_prompt = get_kg_system_prompt(user, doc_lang)
        else:
            system_prompt = get_system_prompt(user, doc_lang)
        user_prompt   = f"Создай документ: {doc_name}\n\nДанные:\n{answers_text}"

        client  = anthropic.Anthropic(api_key=self.api_key)
        result  = ""
        score   = 0
        attempts = 0

        # Одна генерация — Sonnet 4.6 с хорошим промптом сразу даёт качество
        # Оценку делаем Haiku — дёшево и быстро
        while score < 85 and attempts < 2:
            attempts += 1
            if attempts > 1:
                improve_msg = {
                    "ru": "🔄 *Улучшаю качество...*\n_Аз қалды!_",
                    "kz": "🔄 *Сапаны жақсартуда...*\n_Аз қалды!_",
                    "en": "🔄 *Improving...*\n_Almost done!_",
                }
                await message.reply_text(improve_msg.get(lang, improve_msg["ru"]), parse_mode=ParseMode.MARKDOWN)

            # Основная генерация — Sonnet 4.6
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            result = msg.content[0].text

            # Самооценка — Haiku (в 10 раз дешевле)
            eval_msg = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": SELF_EVAL_PROMPT.format(document=result[:3000])}]
            )
            try:
                score = int("".join(filter(str.isdigit, eval_msg.content[0].text.strip()[:5])))
            except:
                score = 88

            if score < 85 and attempts < 2:
                user_prompt = (
                    f"Улучши документ. Оценка {score}/100. "
                    f"Заполни все пустые поля, добавь конкретику из профиля учителя.\n\n{result}"
                )

        # Формируем Word документ
        wait_msg = await message.reply_text(
            "📄 Формирую документ..." if lang == "ru" else "📄 Құжат жасалуда..."
        )

        try:
            from handlers.word_generator import generate_word
            filename = generate_word(result, doc_name, user.get("name", ""))
            with open(filename, "rb") as f:
                caption = {
                    "ru": f"📄 *{doc_name}*\n✅ Готов к печати • Docura.kz",
                    "kz": f"📄 *{doc_name}*\n✅ Басуға дайын • Docura.kz",
                }
                await message.reply_document(
                    document=f,
                    filename=f"{doc_name}_{datetime.now().strftime('%d%m%Y')}.docx",
                    caption=caption.get(lang, caption["ru"]),
                    parse_mode=ParseMode.MARKDOWN
                )
            os.remove(filename)
        except Exception as e:
            print(f"Word error: {e}")
            import traceback
            traceback.print_exc()
            # Запасной вариант — отправляем текст
            for chunk in range(0, len(result), 4000):
                await message.reply_text(result[chunk:chunk+4000])

        try:
            await wait_msg.delete()
        except:
            pass

        # Сохранить в БД
        await self.db.save_document(user_id, doc_type, doc_name, result, score)
        await self.db.log_analytics(user_id, doc_type, score, doc_lang)

        # Обновляем счётчик бесплатных — берём свежие данные из БД
        fresh_user = await self.db.get_user(user_id)
        if not fresh_user.get("subscribed"):
            await self.db.increment_free(user_id)
            fresh_user = await self.db.get_user(user_id)  # обновляем снова
            free_used = fresh_user.get("free_used", 0)
            free_left = max(0, 3 - free_used)

            if free_left == 0:
                kb_after = [
                    [InlineKeyboardButton("⭐ Оформить PRO — безлимит", callback_data="prof_sub")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ]
                note = ("⚠️ *Бесплатные документы исчерпаны!*\n"
                        "Оформите подписку PRO для безлимитного доступа.") if lang == "ru" else \
                       ("⚠️ *Тегін құжаттар таусылды!*\n"
                        "PRO жазылымын рәсімдеңіз.")
            else:
                kb_after = [
                    [InlineKeyboardButton("📄 Создать ещё", callback_data="menu_create")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ]
                note = (f"🆓 Осталось бесплатных: *{free_left}/3*") if lang == "ru" else \
                       (f"🆓 Қалған тегін: *{free_left}/3*")
        else:
            kb_after = [
                [InlineKeyboardButton("📄 Создать ещё", callback_data="menu_create")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ]
            note = "⭐ *PRO* — безлимитный доступ" if lang == "ru" else "⭐ *PRO* — шексіз қол жеткізу"

        await message.reply_text(
            note,
            reply_markup=InlineKeyboardMarkup(kb_after),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.clear()

    async def _create_word(self, content: str, title: str) -> str:
        from docx import Document as DocxDocument
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        import re

        # ── Цвета бренда Docura ───────────────────────────
        NAVY       = RGBColor(0x1A, 0x2E, 0x5A)   # тёмно-синий заголовки
        ACCENT     = RGBColor(0x2E, 0x75, 0xB6)   # синий акцент
        GRAY       = RGBColor(0x59, 0x59, 0x59)   # тёмно-серый текст
        LIGHT_GRAY = RGBColor(0xA0, 0xA0, 0xA0)   # светло-серый
        WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
        ROW_ALT    = "EBF3FB"   # светло-голубой для чётных строк таблицы
        HEADER_BG  = "1A2E5A"   # фон шапки таблицы

        doc     = DocxDocument()
        section = doc.sections[0]

        # Поля: 30мм лево, 15мм право, 20мм верх/низ (стандарт РК)
        section.left_margin   = Cm(3.0)
        section.right_margin  = Cm(1.5)
        section.top_margin    = Cm(2.0)
        section.bottom_margin = Cm(2.0)

        # Стиль Normal
        normal = doc.styles["Normal"]
        normal.font.name = "Times New Roman"
        normal.font.size = Pt(12)
        normal.font.color.rgb = GRAY
        normal.paragraph_format.line_spacing = Pt(18)
        normal.paragraph_format.space_after  = Pt(0)

        # ── Вспомогательные функции ───────────────────────

        def set_cell_bg(cell, hex_color):
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            # удаляем старый shd
            for old in tcPr.findall(qn("w:shd")):
                tcPr.remove(old)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"),   "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"),  hex_color)
            tcPr.append(shd)

        def set_cell_border(cell, hex_color="C0C0C0"):
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcBorders = OxmlElement("w:tcBorders")
            for side in ("top", "bottom", "left", "right"):
                el = OxmlElement(f"w:{side}")
                el.set(qn("w:val"),   "single")
                el.set(qn("w:sz"),    "4")
                el.set(qn("w:space"), "0")
                el.set(qn("w:color"), hex_color)
                tcBorders.append(el)
            tcPr.append(tcBorders)

        def add_hrule(doc, color="2E75B6", sz="12"):
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"),   "single")
            bottom.set(qn("w:sz"),    sz)
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), color)
            pBdr.append(bottom)
            pPr.append(pBdr)
            p.paragraph_format.space_after  = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            return p

        def add_colored_block(doc, text, bg_hex, text_color, font_size=11, bold=False):
            """Цветной прямоугольный блок через однострочную таблицу"""
            tbl = doc.add_table(rows=1, cols=1)
            tbl.style = "Table Grid"
            cell = tbl.rows[0].cells[0]
            set_cell_bg(cell, bg_hex)
            set_cell_border(cell, bg_hex)
            cell.add_paragraph()  # отступ сверху
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(text)
            run.bold           = bold
            run.font.name      = "Times New Roman"
            run.font.size      = Pt(font_size)
            run.font.color.rgb = text_color
            cell.add_paragraph()  # отступ снизу
            return tbl

        # ══════════════════════════════════════════════════
        # ШАПКА — цветной блок с названием сервиса
        # ══════════════════════════════════════════════════
        add_colored_block(
            doc,
            f"DOCURA.KZ  •  Профессиональные документы для учителей",
            bg_hex="1A2E5A", text_color=WHITE, font_size=9
        )
        sp = doc.add_paragraph()
        sp.paragraph_format.space_after = Pt(4)

        # ══════════════════════════════════════════════════
        # ЗАГОЛОВОК ДОКУМЕНТА
        # ══════════════════════════════════════════════════
        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_p.paragraph_format.space_before = Pt(14)
        title_p.paragraph_format.space_after  = Pt(6)
        title_run = title_p.add_run(title.upper())
        title_run.bold           = True
        title_run.font.name      = "Times New Roman"
        title_run.font.size      = Pt(15)
        title_run.font.color.rgb = NAVY

        # Тонкая синяя линия под заголовком
        add_hrule(doc, color="2E75B6", sz="8")

        sp2 = doc.add_paragraph()
        sp2.paragraph_format.space_after = Pt(8)

        # ══════════════════════════════════════════════════
        # ПАРСИНГ И ФОРМАТИРОВАНИЕ СОДЕРЖИМОГО
        # ══════════════════════════════════════════════════
        lines = content.split("\n")

        def is_section_header(s):
            if not s or len(s) < 3:
                return False
            # Полностью заглавные (кириллица/латиница), не число
            if s == s.upper() and re.search(r'[А-ЯA-Z]{3,}', s):
                return True
            # Нумерованный: "1. РАЗДЕЛ" или "I. РАЗДЕЛ"
            if re.match(r'^(\d+\.|[IVX]+\.)\s+[А-ЯA-Z\u04b0-\u04b1]', s):
                return True
            return False

        def is_subheader(s):
            return (s.endswith(":") and 4 < len(s) < 80
                    and not s.startswith(("-", "•", "*")))

        def is_bullet(s):
            return s.startswith(("- ", "• ", "* ", "– ", "· "))

        def is_table_row(s):
            return s.count("|") >= 2

        def is_signature(s):
            keywords = ["подпись", "директор", "учитель", "кл.рук", "классный руководитель",
                        "дата:", "м.п.", "печать", "қолы", "мұғалім", "күні:"]
            return any(k in s.lower() for k in keywords)

        def add_section_header(doc, text):
            """Секция с цветной полоской слева"""
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after  = Pt(4)
            # Левая граница как акцент
            pPr = p._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            left = OxmlElement("w:left")
            left.set(qn("w:val"),   "single")
            left.set(qn("w:sz"),    "18")
            left.set(qn("w:space"), "8")
            left.set(qn("w:color"), "2E75B6")
            pBdr.append(left)
            pPr.append(pBdr)
            run = p.add_run("  " + text)
            run.bold           = True
            run.font.name      = "Times New Roman"
            run.font.size      = Pt(12)
            run.font.color.rgb = NAVY
            return p

        i = 0
        prev_empty = False
        while i < len(lines):
            line    = lines[i]
            stripped = line.strip()

            # Пустая строка
            if not stripped:
                if not prev_empty:
                    sp = doc.add_paragraph()
                    sp.paragraph_format.space_after = Pt(2)
                prev_empty = True
                i += 1
                continue
            prev_empty = False

            # ── Markdown таблица ──
            if is_table_row(stripped):
                table_lines = []
                while i < len(lines) and (is_table_row(lines[i]) or
                                           lines[i].strip().startswith("|---") or
                                           lines[i].strip().startswith("| ---")):
                    raw = lines[i]
                    if not re.match(r'^\s*\|[\s\-:]+\|', raw):
                        table_lines.append(raw)
                    i += 1
                if table_lines:
                    _add_beautiful_table(doc, table_lines, ROW_ALT, HEADER_BG,
                                         NAVY, WHITE, GRAY, set_cell_bg, set_cell_border)
                    doc.add_paragraph().paragraph_format.space_after = Pt(6)
                continue

            # ── Заголовок раздела ──
            if is_section_header(stripped):
                add_section_header(doc, stripped)
                i += 1
                continue

            # ── Подзаголовок ──
            if is_subheader(stripped):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(8)
                p.paragraph_format.space_after  = Pt(2)
                run = p.add_run(stripped)
                run.bold           = True
                run.font.name      = "Times New Roman"
                run.font.size      = Pt(12)
                run.font.color.rgb = GRAY
                i += 1
                continue

            # ── Буллет ──
            if is_bullet(stripped):
                text = re.sub(r'^[-•*–·]\s+', '', stripped)
                p = doc.add_paragraph()
                p.paragraph_format.left_indent    = Cm(1.2)
                p.paragraph_format.first_line_indent = Cm(-0.5)
                p.paragraph_format.space_after    = Pt(2)
                bullet_run = p.add_run("▸  ")
                bullet_run.font.name      = "Arial"
                bullet_run.font.size      = Pt(10)
                bullet_run.font.color.rgb = ACCENT
                text_run = p.add_run(text)
                text_run.font.name      = "Times New Roman"
                text_run.font.size      = Pt(12)
                text_run.font.color.rgb = GRAY
                i += 1
                continue

            # ── Строка подписи ──
            if is_signature(stripped):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after  = Pt(2)
                run = p.add_run(stripped)
                run.font.name      = "Times New Roman"
                run.font.size      = Pt(12)
                run.font.color.rgb = GRAY
                i += 1
                continue

            # ── Обычный абзац ──
            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(1.25)
            p.paragraph_format.alignment         = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.space_after        = Pt(2)
            run = p.add_run(stripped)
            run.font.name      = "Times New Roman"
            run.font.size      = Pt(12)
            run.font.color.rgb = GRAY
            i += 1

        # ══════════════════════════════════════════════════
        # ПОДВАЛ
        # ══════════════════════════════════════════════════
        doc.add_paragraph().paragraph_format.space_before = Pt(16)
        add_hrule(doc, color="1A2E5A", sz="6")
        footer_p = doc.add_paragraph()
        footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer_p.paragraph_format.space_before = Pt(4)
        footer_run = footer_p.add_run(
            f"Сгенерировано сервисом Docura.kz  •  {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        footer_run.font.name      = "Times New Roman"
        footer_run.font.size      = Pt(8)
        footer_run.font.color.rgb = LIGHT_GRAY

        fname = f"/tmp/docura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        doc.save(fname)
        return fname


def _add_beautiful_table(doc, table_lines, row_alt, header_bg,
                          navy, white, gray, set_cell_bg, set_cell_border):
    """Красивая таблица с чередующимися строками"""
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    rows_data = []
    for line in table_lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows_data.append(cells)

    if not rows_data:
        return

    max_cols = max(len(r) for r in rows_data)
    # выравниваем строки
    rows_data = [r + [""] * (max_cols - len(r)) for r in rows_data]

    table = doc.add_table(rows=len(rows_data), cols=max_cols)
    table.style = "Table Grid"

    for r_idx, row_data in enumerate(rows_data):
        row = table.rows[r_idx]
        is_header = (r_idx == 0)
        bg = header_bg if is_header else (row_alt if r_idx % 2 == 0 else "FFFFFF")

        for c_idx, cell_text in enumerate(row_data):
            cell = row.cells[c_idx]
            # очистим текст по умолчанию
            for para in cell.paragraphs:
                for run in para.runs:
                    run.text = ""

            set_cell_bg(cell, bg)
            set_cell_border(cell, "C8D8E8" if not is_header else header_bg)

            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after  = Pt(4)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if is_header else WD_ALIGN_PARAGRAPH.LEFT

            run = p.add_run(cell_text)
            run.bold           = is_header
            run.font.name      = "Times New Roman"
            run.font.size      = Pt(11)
            run.font.color.rgb = white if is_header else gray
