import os
import json
import anthropic
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t, TEXTS
from handlers.rag_base import get_system_prompt, SELF_EVAL_PROMPT
from database import Database, free_limit_for

# ══════════════════════════════════════════════════════════════
# ВОПРОСЫ ДЛЯ ДОКУМЕНТОВ УЧИТЕЛЯ (школа)
# ══════════════════════════════════════════════════════════════
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

        # ── ДОКУМЕНТЫ ДЛЯ САДИКА ──
        "kg_thematic_plan": [
            {"key": "age_group",  "q": "👶 Возрастная группа?\n\n_Пример: средняя группа (4-5 лет)_"},
            {"key": "month",      "q": "📅 На какой месяц?\n\n_Пример: Ноябрь 2024_"},
            {"key": "topics",     "q": "📋 Темы недель через запятую\n\n_Или напишите «по программе»_"},
        ],
        "kg_activity_summary": [
            {"key": "age_group", "q": "👶 Возрастная группа?"},
            {"key": "topic",     "q": "📖 Тема занятия?\n\n_Пример: «Осень золотая» (ознакомление с природой)_"},
            {"key": "goals",     "q": "🎯 Цель занятия?\n\n_Или напишите «автоматически»_"},
        ],
        "kg_monthly_report": [
            {"key": "period",    "q": "📅 За какой период?\n\n_Пример: Октябрь 2024_"},
            {"key": "age_group", "q": "👶 Группа?\n\n_Пример: старшая группа «Ромашка»_"},
            {"key": "progress",  "q": "📊 Освоение программы детьми (кратко)?\n\n_Пример: большинство детей усвоили материал по ФЭМП_"},
            {"key": "extra",     "q": "🏆 Мероприятия, утренники, конкурсы?\n\n_Если нет — напишите «нет»_"},
        ],
        "kg_child_characteristic": [
            {"key": "student_name", "q": "👤 Имя ребёнка, возраст, группа?\n\n_Или выберите из базы ниже_"},
            {"key": "performance",  "q": "📊 Освоение программы?\n\n_Пример: хорошо усваивает материал_"},
            {"key": "behavior",     "q": "😊 Поведение и характер?\n\n_Пример: общительный, любознательный_"},
            {"key": "activities",   "q": "🏆 Достижения, участие в утренниках?\n\n_Если нет — напишите «нет»_"},
            {"key": "purpose",      "q": "📋 Цель характеристики?\n\n_Пример: для психолога, по месту требования_"},
        ],
        "kg_parent_letter": [
            {"key": "student_name", "q": "👤 Имя ребёнка и группа?"},
            {"key": "topic",        "q": "📋 Тема письма?\n\n_Пример: адаптация, поведение, успехи_"},
            {"key": "details",      "q": "📝 Детали письма?"},
        ],
        "kg_absence_cert": [
            {"key": "student_name",  "q": "👤 Имя ребёнка и группа?"},
            {"key": "absence_dates", "q": "📅 Даты отсутствия?"},
            {"key": "reason",        "q": "❓ Причина?\n\n_Пример: болезнь (справка есть)_"},
        ],
        "kg_vacation_request": [
            {"key": "vacation_type", "q": "🏖 Вид отпуска?\n\n1️⃣ Ежегодный трудовой\n2️⃣ За свой счёт\n\n_Напишите номер или название_"},
            {"key": "dates",         "q": "📅 Даты?\n\n_Пример: с 01.07.2025 по 25.08.2025_"},
        ],
        "kg_explanation": [
            {"key": "absence_date", "q": "📅 Дата отсутствия или опоздания?"},
            {"key": "reason",       "q": "❓ Причина?"},
            {"key": "documents",    "q": "📎 Есть подтверждающие документы?\n\n_Если нет — напишите «нет»_"},
        ],
        "kg_announcement": [
            {"key": "event",    "q": "📢 Что за мероприятие?\n\n_Пример: утренник, родительское собрание_"},
            {"key": "datetime", "q": "📅 Дата и время?"},
            {"key": "location", "q": "📍 Место проведения?\n\n_Пример: музыкальный зал_"},
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

        # ── САДИК (KZ) ──
        "kg_thematic_plan": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "month",     "q": "📅 Қандай ай?"},
            {"key": "topics",    "q": "📋 Апталық тақырыптар немесе «бағдарлама бойынша»"},
        ],
        "kg_activity_summary": [
            {"key": "age_group", "q": "👶 Жас тобы?"},
            {"key": "topic",     "q": "📖 Сабақтың тақырыбы?"},
            {"key": "goals",     "q": "🎯 Сабақтың мақсаты немесе «автоматты»?"},
        ],
        "kg_monthly_report": [
            {"key": "period",    "q": "📅 Қандай кезең?"},
            {"key": "age_group", "q": "👶 Топ?"},
            {"key": "progress",  "q": "📊 Балалардың бағдарламаны меңгеруі?"},
            {"key": "extra",     "q": "🏆 Іс-шаралар, мерекелер?\n\n_Жоқ болса «жоқ»_"},
        ],
        "kg_child_characteristic": [
            {"key": "student_name", "q": "👤 Баланың аты, жасы, тобы?"},
            {"key": "performance",  "q": "📊 Бағдарламаны меңгеруі?"},
            {"key": "behavior",     "q": "😊 Мінез-құлқы?"},
            {"key": "activities",   "q": "🏆 Жетістіктері?\n\n_Жоқ болса «жоқ»_"},
            {"key": "purpose",      "q": "📋 Мінездеме мақсаты?"},
        ],
        "kg_parent_letter": [
            {"key": "student_name", "q": "👤 Баланың аты және тобы?"},
            {"key": "topic",        "q": "📋 Хат тақырыбы?"},
            {"key": "details",      "q": "📝 Мәліметтер?"},
        ],
        "kg_absence_cert": [
            {"key": "student_name",  "q": "👤 Баланың аты және тобы?"},
            {"key": "absence_dates", "q": "📅 Болмаған күндер?"},
            {"key": "reason",        "q": "❓ Себебі?"},
        ],
        "kg_vacation_request": [
            {"key": "vacation_type", "q": "🏖 Демалыс түрі?"},
            {"key": "dates",         "q": "📅 Күндері?"},
        ],
        "kg_explanation": [
            {"key": "absence_date", "q": "📅 Болмаған күн?"},
            {"key": "reason",       "q": "❓ Себебі?"},
            {"key": "documents",    "q": "📎 Растайтын құжаттар?\n\n_Жоқ болса «жоқ»_"},
        ],
        "kg_announcement": [
            {"key": "event",    "q": "📢 Іс-шара?\n\n_Мысалы: мереке, ата-аналар жиналысы_"},
            {"key": "datetime", "q": "📅 Күні мен уақыты?"},
            {"key": "location", "q": "📍 Орны?"},
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
        # садик
        "kg_thematic_plan": "Тематический план занятий",
        "kg_activity_summary": "Конспект занятия",
        "kg_monthly_report": "Отчёт воспитателя",
        "kg_child_characteristic": "Характеристика воспитанника",
        "kg_parent_letter": "Письмо родителям",
        "kg_absence_cert": "Справка об отсутствии ребёнка",
        "kg_vacation_request": "Заявление на отпуск",
        "kg_explanation": "Объяснительная записка",
        "kg_announcement": "Объявление для родителей",
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
        # балабақша
        "kg_thematic_plan": "Тақырыптық жоспар",
        "kg_activity_summary": "Сабақ конспектісі",
        "kg_monthly_report": "Тәрбиеші есебі",
        "kg_child_characteristic": "Тәрбиеленуші мінездемесі",
        "kg_parent_letter": "Ата-аналарға хат",
        "kg_absence_cert": "Баланың болмағаны туралы анықтама",
        "kg_vacation_request": "Демалыс өтініші",
        "kg_explanation": "Түсіндірме хат",
        "kg_announcement": "Ата-аналарға хабарландыру",
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
        "kg_thematic_plan": "Thematic Activity Plan",
        "kg_activity_summary": "Activity Summary",
        "kg_monthly_report": "Kindergarten Teacher Report",
        "kg_child_characteristic": "Child Reference",
        "kg_parent_letter": "Letter to Parents",
        "kg_absence_cert": "Child Absence Certificate",
        "kg_vacation_request": "Leave Application",
        "kg_explanation": "Explanatory Note",
        "kg_announcement": "Announcement for Parents",
    }
}

# Категории документов учителя (школа)
CAT_DOCS = {
    "planning": ["lesson_plan", "calendar_plan", "lesson_summary"],
    "reports":  ["monthly_report", "control_analysis"],
    "students": ["characteristic", "absence_cert", "discipline_act", "gratitude_letter", "parent_letter"],
    "personal": ["vacation_request", "explanation", "announcement"],
}

# Категории документов садика (полностью отдельный набор — НЕ школьные типы)
CAT_DOCS_KG = {
    "kg_planning": ["kg_thematic_plan", "kg_activity_summary"],
    "kg_reports":  ["kg_monthly_report"],
    "kg_children": ["kg_child_characteristic", "kg_parent_letter", "kg_absence_cert"],
    "kg_personal": ["kg_vacation_request", "kg_explanation", "kg_announcement"],
}

# Объединённый словарь категорий — используется при поиске документов по категории,
# независимо от роли (роль определяет только какое меню категорий показать)
CAT_DOCS_ALL = {**CAT_DOCS, **CAT_DOCS_KG}

# Типы документов, для которых предлагается выбор ребёнка/ученика из базы
STUDENT_LINKED_DOC_TYPES = [
    "characteristic", "absence_cert", "discipline_act", "gratitude_letter", "parent_letter",
    "kg_child_characteristic", "kg_parent_letter", "kg_absence_cert",
]

# Красивые разделители для дизайна
DIVIDER = "─" * 20

def _build_profile_context(user: dict, lang: str) -> str:
    """Собирает весь профиль пользователя для автоподстановки в промпт."""
    role = user.get("role", "teacher")
    if role == "kindergarten":
        lines = [
            "ПРОФИЛЬ ВОСПИТАТЕЛЯ (используй автоматически):",
            f"- ФИО: {user.get('name') or '[не указано]'}",
            f"- Детский сад: {user.get('school') or '[не указано]'}",
            f"- Возрастная группа: {user.get('age_group') or '[не указано]'}",
            f"- Должность: {user.get('position') or 'воспитатель'}",
            f"- Заведующая: {user.get('director') or '[не указано]'}",
        ]
    else:
        is_ct = "Да" if user.get("is_class_teacher") else "Нет"
        lines = [
            "ПРОФИЛЬ УЧИТЕЛЯ (используй автоматически):",
            f"- ФИО: {user.get('name') or '[не указано]'}",
            f"- Школа: {user.get('school') or '[не указано]'}",
            f"- Должность: {user.get('position') or 'учитель'}",
            f"- Предмет: {user.get('subject') or '[не указано]'}",
            f"- Классы: {user.get('classes') or '[не указано]'}",
            f"- Классный руководитель: {is_ct}",
            f"- Директор: {user.get('director') or '[не указано]'}",
        ]
    return "\n".join(lines)

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

        # ── Категория документов (и школьная, и садиковская — cat_planning / cat_kg_planning и т.д.) ──
        if data.startswith("cat_"):
            cat = data[len("cat_"):]
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

        # ── Выбрать ученика/воспитанника из базы ──
        elif data.startswith("gen_student_"):
            student_id = int(data[12:])
            await self._use_student_data(query, context, user_id, user, lang, student_id)

    async def _show_doc_list(self, query, lang, cat):
        docs  = CAT_DOCS_ALL.get(cat, [])
        names = DOC_NAMES.get(lang, DOC_NAMES["ru"])
        keyboard = [[InlineKeyboardButton(names.get(d, d), callback_data=f"doc_{d}")] for d in docs]
        keyboard.append([InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_create")])

        cat_name = t(lang, f"cat_{cat}")
        header = "📂 *{cat}*\n{div}\nВыберите тип документа:" if lang == "ru" else "📂 *{cat}*\n{div}\nҚұжат түрін таңдаңыз:"

        if not docs:
            # Защита от несуществующей/незаполненной категории — не показываем пустой экран молча
            empty_text = (
                "⚠️ Для этой категории пока нет документов." if lang == "ru"
                else "⚠️ Бұл санатта әзірге құжат жоқ."
            )
            await query.edit_message_text(
                empty_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_create")]])
            )
            return

        await query.edit_message_text(
            header.format(cat=cat_name, div=DIVIDER),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _start_doc(self, query, context, user_id, user, lang, doc_type):
        subscribed = user.get("subscribed", 0)
        free_used  = user.get("free_used", 0)

        if not subscribed and free_used >= free_limit_for(user):
            from handlers.profile import TIER_PRICES, PROMO_ANCHOR_PRICE
            promo_available = not user.get("promo_used")

            if promo_available:
                text = (
                    f"🚫 *Бесплатные попытки закончились.*\n\n"
                    f"🔥 Месяц PRO с безлимитной генерацией — всего *{TIER_PRICES['pro_promo']} тг* "
                    f"(вместо {PROMO_ANCHOR_PRICE} тг)!\n"
                    f"Это разовое предложение, доступно один раз для нового пользователя.\n\n"
                    f"После оплаты пришлите чек — доступ откроется автоматически."
                ) if lang == "ru" else (
                    f"🚫 *Тегін әрекеттер аяқталды.*\n\n"
                    f"🔥 Шексіз PRO — небары *{TIER_PRICES['pro_promo']} тг* ({PROMO_ANCHOR_PRICE} тг орнына)!\n"
                    f"Бұл жаңа пайдаланушыға бір реттік ұсыныс.\n\n"
                    f"Төлемнен кейін чекті жіберіңіз — қолжетімділік автоматты ашылады."
                )
                keyboard = [
                    [InlineKeyboardButton(
                        f"🔥 Активировать PRO за {TIER_PRICES['pro_promo']} тг" if lang == "ru" else f"🔥 PRO-ды {TIER_PRICES['pro_promo']} тг-ге белсендіру",
                        callback_data="prof_choose_pro_promo"
                    )],
                    [InlineKeyboardButton("🎁 Или пригласить и получить +5" if lang == "ru" else "🎁 Немесе шақырып +5 алу", callback_data="menu_profile")],
                    [InlineKeyboardButton("Все тарифы" if lang == "ru" else "Барлық тарифтер", callback_data="prof_sub")],
                    [InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_main")],
                ]
            else:
                text = t(lang, "limit_reached")
                keyboard = [
                    [InlineKeyboardButton("⭐ Оформить подписку", callback_data="prof_sub")],
                    [InlineKeyboardButton("◀️ " + t(lang, "back"), callback_data="menu_main")],
                ]

            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Для документов по ученику/ребёнку — предложить выбрать из базы
        if doc_type in STUDENT_LINKED_DOC_TYPES:
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
                is_kg = user.get("role") == "kindergarten"
                title = ("👥 *Выберите воспитанника из базы:*" if is_kg else "👥 *Выберите ученика из базы:*") if lang == "ru" \
                    else ("👥 *Базадан баланы таңдаңыз:*" if is_kg else "👥 *Базадан оқушыны таңдаңыз:*")
                await query.edit_message_text(title, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
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
                context.user_data["doc_answers"]["student_name"] = f"{student['name']}, {student['class_name']}"
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
        is_kg    = user.get("role") == "kindergarten"

        if is_kg:
            context_line = f"Возрастная группа: {user.get('age_group')}."
            asker = "Воспитатель"
        else:
            context_line = f"Предмет: {user.get('subject')}, классы: {user.get('classes')}."
            asker = "Учитель"

        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = (
            f"{asker} просит помочь составить текст для поля «{field}» документа «{doc_name}».\n"
            f"{context_line}\n"
            f"Что хочет сказать: {user_input}\n\n"
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

        # Профиль пользователя — подставляем автоматически
        profile_ctx = _build_profile_context(user, lang)

        # Расписание из агентной памяти (если загружено) — только если реально есть данные
        schedule_ctx = ""
        try:
            from handlers.agent import AgentHandler
            schedule_ctx = await AgentHandler(self.db, self.api_key).get_context_for_generation(user_id)
        except Exception:
            pass

        system_prompt = get_system_prompt(user, doc_lang)

        # Образцы, загруженные админом для этого типа документа — добавляем как эталон
        samples_ctx = ""
        try:
            samples = await self.db.get_samples_for_type(doc_type, doc_lang, limit=1)
            if samples:
                samples_ctx = "\nЭТАЛОННЫЙ ОБРАЗЕЦ (ориентируйся на структуру и стиль):\n" + samples[0]["content"][:3000]
        except Exception:
            pass

        user_prompt = (
            f"Создай документ: {doc_name}\n\n"
            f"{profile_ctx}\n"
            f"{schedule_ctx}\n"
            f"{samples_ctx}\n\n"
            f"Данные для этого документа:\n{answers_text}\n\n"
            f"Используй профиль автоматически. Если данных не хватает — пиши [уточнить], не выдумывай."
        )

        client  = anthropic.Anthropic(api_key=self.api_key)
        result  = ""
        score   = 0
        attempts = 0

        while score < 85 and attempts < 2:
            attempts += 1
            if attempts > 1:
                improve_msg = {
                    "ru": "🔄 *Улучшаю качество...*\n_Аз қалды!_",
                    "kz": "🔄 *Сапаны жақсартуда...*\n_Аз қалды!_",
                    "en": "🔄 *Improving...*\n_Almost done!_",
                }
                await message.reply_text(improve_msg.get(lang, improve_msg["ru"]), parse_mode=ParseMode.MARKDOWN)

            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            result = msg.content[0].text

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
                    f"Заполни все пустые поля, добавь конкретику из профиля.\n\n{result}"
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
            fresh_user = await self.db.get_user(user_id)
            free_used = fresh_user.get("free_used", 0)
            total_free = free_limit_for(fresh_user)
            free_left = max(0, total_free - free_used)

            if free_left == 0:
                from handlers.profile import TIER_PRICES, PROMO_ANCHOR_PRICE
                promo_available = not fresh_user.get("promo_used")

                if promo_available:
                    kb_after = [
                        [InlineKeyboardButton(
                            f"🔥 PRO за {TIER_PRICES['pro_promo']} тг (вместо {PROMO_ANCHOR_PRICE})" if lang == "ru"
                            else f"🔥 PRO {TIER_PRICES['pro_promo']} тг",
                            callback_data="prof_choose_pro_promo"
                        )],
                        [InlineKeyboardButton("🎁 Или пригласить и получить +5", callback_data="menu_profile")],
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                    ]
                    note = (
                        f"⚠️ *Бесплатные документы исчерпаны!*\n\n"
                        f"🔥 Месяц PRO с безлимитной генерацией — всего *{TIER_PRICES['pro_promo']} тг* "
                        f"(вместо {PROMO_ANCHOR_PRICE} тг). Разовое предложение для вас.\n"
                        f"Или пригласите коллегу через /invite — за каждого +5 документов."
                    ) if lang == "ru" else (
                        f"⚠️ *Тегін құжаттар таусылды!*\n\n"
                        f"🔥 Шексіз PRO — небары *{TIER_PRICES['pro_promo']} тг* ({PROMO_ANCHOR_PRICE} тг орнына).\n"
                        f"Немесе /invite арқылы әріптесіңізді шақырыңыз."
                    )
                else:
                    kb_after = [
                        [InlineKeyboardButton("🎁 Пригласить и получить +5", callback_data="menu_profile")],
                        [InlineKeyboardButton("⭐ Оформить PRO — безлимит", callback_data="prof_sub")],
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                    ]
                    note = ("⚠️ *Бесплатные документы исчерпаны!*\n"
                            "Оформите PRO или пригласите коллегу через /invite — за каждого +5 документов.") if lang == "ru" else \
                           ("⚠️ *Тегін құжаттар таусылды!*\n"
                            "PRO рәсімдеңіз немесе /invite арқылы әріптесіңізді шақырыңыз.")
            else:
                kb_after = [
                    [InlineKeyboardButton("📄 Создать ещё", callback_data="menu_create")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ]
                note = (f"🆓 Осталось бесплатных: *{free_left}/{total_free}*") if lang == "ru" else \
                       (f"🆓 Қалған тегін: *{free_left}/{total_free}*")
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
