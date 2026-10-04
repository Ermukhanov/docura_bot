"""
Docura.kz — единые схемы документов на трёх языках (ru / kz / en).

ЗАЧЕМ. Раньше нормативные правила в rag_base.py и эталонные образцы содержали
русские названия колонок и разделов («Этап урока», «Понедельник», «МОТИВАЦИОННО-
ПОБУДИТЕЛЬНЫЙ ЭТАП» и т.д.) с пометкой «СТРОГО ИМЕННО ТАК». Модель копировала их
буквально, и в казахском документе шапка таблицы и заголовки оставались русскими,
а содержимое — казахским.

ЧТО ДЕЛАЕТ МОДУЛЬ.
1. SCHEMAS — для КАЖДОГО типа документа: заголовки разделов и шапки таблиц на
   всех трёх языках (из общего глоссария, чтобы не расходились).
2. build_structure_instruction() — блок для промпта: «используй ровно эти заголовки
   и названия колонок на языке документа, русские из правил — замени».
3. localize_labels() — детерминированная страховка ПОСЛЕ генерации: если модель всё
   же оставила известную подпись на другом языке (в заголовке, шапке таблицы или
   «Подпись: значение»), она заменяется на подпись нужного языка.
4. find_foreign_labels() — проверка для тестов/логов.
5. coverage_problems() — проверка полноты: у каждого типа документа есть схема,
   название и подписи на каждом языке.
"""

import re

LANGS = ("ru", "kz", "en")


def L(ru, kz, en):
    return {"ru": ru, "kz": kz, "en": en}


# ══════════════════════════════════════════════════════════════
# ГЛОССАРИЙ — одна подпись = три языка
# ══════════════════════════════════════════════════════════════
G = {
    # общие поля шапки
    "subject":        L("Предмет", "Пән", "Subject"),
    "class":          L("Класс", "Сынып", "Class"),
    "group":          L("Группа", "Топ", "Group"),
    "date":           L("Дата", "Күні", "Date"),
    "period":         L("Период", "Кезең", "Period"),
    "topic":          L("Тема", "Тақырып", "Topic"),
    "teacher":        L("Учитель", "Мұғалім", "Teacher"),
    "teacher_fio":    L("ФИО учителя", "Мұғалімнің аты-жөні", "Teacher's full name"),
    "educator":       L("Воспитатель", "Тәрбиеші", "Educator"),
    "educator_fio":   L("ФИО воспитателя", "Тәрбиешінің аты-жөні", "Educator's full name"),
    "student_fio":    L("ФИО ученика", "Оқушының аты-жөні", "Student's full name"),
    "child_fio":      L("ФИО ребёнка", "Баланың аты-жөні", "Child's full name"),
    "fio":            L("ФИО", "Аты-жөні", "Full name"),
    "organization":   L("Организация", "Ұйым", "Organization"),
    "school":         L("Школа", "Мектеп", "School"),
    "kindergarten":   L("Детский сад", "Балабақша", "Kindergarten"),
    "age":            L("Возраст", "Жасы", "Age"),
    "age_group":      L("Возрастная группа", "Жас тобы", "Age group"),
    "month":          L("Месяц", "Ай", "Month"),
    "year":           L("Учебный год", "Оқу жылы", "Academic year"),
    "place":          L("Место проведения", "Өтетін орны", "Venue"),
    "time":           L("Время", "Уақыты", "Time"),
    "number":         L("№", "№", "No."),
    "note":           L("Примечание", "Ескертпе", "Note"),
    "responsible":    L("Ответственный", "Жауапты", "Responsible"),
    "deadline":       L("Сроки", "Мерзімі", "Deadline"),
    "signature":      L("Подпись", "Қолы", "Signature"),
    "director":       L("Директор", "Директор", "Director"),
    "head":           L("Заведующая", "Меңгеруші", "Head of kindergarten"),
    # урок
    "lesson_topic":   L("Тема урока", "Сабақ тақырыбы", "Lesson topic"),
    "objectives":     L("Цели обучения", "Оқу мақсаттары", "Learning objectives"),
    "criteria":       L("Критерии оценивания", "Бағалау критерийлері", "Assessment criteria"),
    "lang_goals":     L("Языковые цели", "Тілдік мақсаттар", "Language objectives"),
    "resources":      L("Ресурсы", "Ресурстар", "Resources"),
    "lesson_stage":   L("Этап урока", "Сабақ кезеңі", "Lesson stage"),
    "activity":       L("Деятельность педагога и ученика", "Педагог пен оқушының әрекеті", "Teacher and student activity"),
    "assess_res":     L("Оценивание и ресурсы", "Бағалау және ресурстар", "Assessment and resources"),
    "lesson_flow":    L("Ход урока", "Сабақ барысы", "Lesson procedure"),
    "reflection":     L("Рефлексия", "Рефлексия", "Reflection"),
    "differentiation": L("Дифференциация", "Саралау", "Differentiation"),
    "section":        L("Раздел", "Бөлім", "Unit"),
    "hours":          L("Часы", "Сағат", "Hours"),
    "dates":          L("Даты", "Күндері", "Dates"),
    "key_points":     L("Ключевые моменты", "Негізгі ойлар", "Key points"),
    "homework":       L("Домашнее задание", "Үй тапсырмасы", "Homework"),
    # СОР/СОЧ и анализ
    "tasks":          L("Задания", "Тапсырмалар", "Tasks"),
    "rubric":         L("Рубрикатор", "Бағалау рубрикасы", "Assessment rubric"),
    "criterion":      L("Критерий оценивания", "Бағалау критерийі", "Assessment criterion"),
    "descriptor":     L("Дескриптор ученика", "Оқушы дескрипторы", "Student descriptor"),
    "score":          L("Балл", "Балл", "Score"),
    "typical_error":  L("Типичная ошибка", "Типтік қате", "Typical error"),
    "students_count": L("Кол-во учеников", "Оқушылар саны", "Number of students"),
    "cause":          L("Возможная причина", "Ықтимал себебі", "Possible cause"),
    "remedial":       L("План коррекционной работы", "Түзету жұмысының жоспары", "Remedial action plan"),
    "quality":        L("Качество знаний", "Білім сапасы", "Knowledge quality"),
    "success":        L("Успеваемость", "Үлгерім", "Academic performance"),
    "avg_score":      L("Средний балл", "Орташа балл", "Average score"),
    "grade":          L("Оценка", "Баға", "Grade"),
    "conclusions":    L("Выводы", "Қорытынды", "Conclusions"),
    # отчёт
    "rep_program":    L("Выполнение учебной программы", "Оқу бағдарламасының орындалуы", "Curriculum completion"),
    "rep_progress":   L("Успеваемость", "Үлгерім", "Academic performance"),
    "rep_tests":      L("Контрольные работы", "Бақылау жұмыстары", "Tests"),
    "rep_extra":      L("Внеклассная работа", "Сыныптан тыс жұмыс", "Extracurricular work"),
    "rep_selfedu":    L("Самообразование", "Өзін-өзі дамыту", "Self-education"),
    "rep_problems":   L("Проблемы и пути решения", "Мәселелер және шешу жолдары", "Problems and solutions"),
    "rep_edu_prog":   L("Освоение образовательной программы", "Білім беру бағдарламасын меңгеру", "Mastery of the educational programme"),
    "rep_events":     L("Проведённые мероприятия", "Өткізілген іс-шаралар", "Events held"),
    "rep_parents":    L("Работа с родителями", "Ата-аналармен жұмыс", "Work with parents"),
    # характеристика / справки / акты
    "characteristic": L("Характеристика", "Мінездеме", "Reference"),
    "certificate":    L("Справка", "Анықтама", "Certificate"),
    "act":            L("Акт", "Акт", "Report"),
    "letter":         L("Письмо", "Хат", "Letter"),
    "gratitude":      L("Благодарственное письмо", "Алғыс хат", "Letter of appreciation"),
    "application":    L("Заявление", "Өтініш", "Application"),
    "explanatory":    L("Объяснительная записка", "Түсініктеме хат", "Explanatory note"),
    "announcement":   L("Объявление", "Хабарландыру", "Announcement"),
    "protocol":       L("Протокол", "Хаттама", "Minutes"),
    "agenda":         L("Повестка дня", "Күн тәртібі", "Agenda"),
    "decision":       L("Итоговое решение собрания", "Жиналыстың қорытынды шешімі", "Final decision of the meeting"),
    "chairman":       L("Председатель", "Төраға", "Chairperson"),
    "secretary":      L("Секретарь", "Хатшы", "Secretary"),
    "attendees":      L("Присутствовали", "Қатысқандар", "Attendees"),
    "commission":     L("Состав комиссии", "Комиссия құрамы", "Commission members"),
    "family":         L("Состав семьи", "Отбасы құрамы", "Family composition"),
    "conditions":     L("Описание условий", "Жағдайдың сипаттамасы", "Description of conditions"),
    "final_conclusion": L("Заключение комиссии", "Комиссия қорытындысы", "Conclusion of the commission"),
    "violation":      L("Описание нарушения", "Бұзушылықтың сипаттамасы", "Description of the violation"),
    "witnesses":      L("Свидетели", "Куәгерлер", "Witnesses"),
    "reason":         L("Причина", "Себебі", "Reason"),
    "to_director":    L("Директору", "Директорға", "To the Director"),
    # садик
    "weeks_theme":    L("Темы недель", "Апта тақырыптары", "Weekly themes"),
    "week":           L("Неделя", "Апта", "Week"),
    "event":          L("Мероприятие", "Іс-шара", "Event"),
    "edu_area":       L("Образовательная область", "Білім беру саласы", "Educational area"),
    "activities":     L("Виды деятельности", "Қызмет түрлері", "Types of activity"),
    "final_event":    L("Итоговое мероприятие", "Қорытынды іс-шара", "Final event"),
    "block":          L("Блок", "Блок", "Block"),
    "planned_week":   L("Планируемое содержание недели", "Аптаның жоспарланған мазмұны", "Planned content of the week"),
    "stage_motiv":    L("МОТИВАЦИОННО-ПОБУДИТЕЛЬНЫЙ ЭТАП", "МОТИВАЦИЯЛЫҚ-ЫНТАЛАНДЫРУШЫ КЕЗЕҢ", "MOTIVATIONAL AND INCENTIVE STAGE"),
    "stage_search":   L("ОРГАНИЗАЦИОННО-ПОИСКОВЫЙ ЭТАП", "ҰЙЫМДАСТЫРУ-ІЗДЕСТІРУ КЕЗЕҢІ", "ORGANIZATIONAL AND SEARCH STAGE"),
    "stage_reflect":  L("РЕФЛЕКСИВНО-КОРРИГИРУЮЩИЙ ЭТАП", "РЕФЛЕКСИЯЛЫҚ-ТҮЗЕТУШІ КЕЗЕҢ", "REFLECTIVE AND CORRECTIVE STAGE"),
    "aims":           L("Цель и задачи", "Мақсаты мен міндеттері", "Aim and tasks"),
    "prelim_work":    L("Предварительная работа", "Алдын ала жұмыс", "Preliminary work"),
    "materials":      L("Материалы и оборудование", "Материалдар мен жабдықтар", "Materials and equipment"),
    "mastered":       L("Освоили полностью", "Толық меңгерді", "Fully mastered"),
    "partly":         L("Осваивают частично", "Ішінара меңгеруде", "Partially mastered"),
    "need_support":   L("Требуется поддержка", "Қолдау қажет", "Support needed"),
    "skill_area":     L("Образовательная область / навык", "Білім беру саласы / дағды", "Educational area / skill"),
    "scenario":       L("Сценарий", "Сценарий", "Script"),
    "characters":     L("Действующие лица", "Кейіпкерлер", "Characters"),
    "props":          L("Реквизит", "Реквизит", "Props"),
    "topic_theme":    L("Тема направления", "Бағыт тақырыбы", "Area of work"),
    "schedule_date":  L("Дата / периодичность", "Күні / жиілігі", "Date / frequency"),
    "lesson_theme":   L("Тема занятия", "Сабақ тақырыбы", "Session topic"),
    "expected":       L("Ожидаемый результат", "Күтілетін нәтиже", "Expected result"),
    "final_expected": L("Ожидаемый итоговый результат к концу периода", "Кезең соңындағы күтілетін қорытынды нәтиже", "Expected final result by the end of the period"),
    "step_schedule":  L("Пошаговый график занятий", "Сабақтардың қадамдық кестесі", "Step-by-step session schedule"),
    "problem":        L("Проблема", "Мәселе", "Problem"),
    "forms":          L("Формы работы", "Жұмыс түрлері", "Forms of work"),
    "meetings":       L("Тематика родительских собраний", "Ата-аналар жиналысының тақырыптары", "Parent meeting topics"),
    "consultations":  L("График консультаций", "Кеңес беру кестесі", "Consultation schedule"),
    # дни недели
    "mon": L("Понедельник", "Дүйсенбі", "Monday"),
    "tue": L("Вторник", "Сейсенбі", "Tuesday"),
    "wed": L("Среда", "Сәрсенбі", "Wednesday"),
    "thu": L("Четверг", "Бейсенбі", "Thursday"),
    "fri": L("Пятница", "Жұма", "Friday"),
    "sat": L("Суббота", "Сенбі", "Saturday"),
    "sun": L("Воскресенье", "Жексенбі", "Sunday"),
    # блоки циклограммы
    "cyc_morning": L("Утренний приём и гимнастика", "Таңертеңгі қабылдау және гимнастика", "Morning arrival and exercises"),
    "cyc_oud":     L("ОУД (организованная учебная деятельность)", "ҰОҚ (ұйымдастырылған оқу қызметі)", "Organized learning activity"),
    "cyc_walk":    L("Прогулка", "Серуен", "Walk"),
    "cyc_sleep":   L("Сон и закаливание", "Ұйқы және шынықтыру", "Nap and hardening"),
    "cyc_games":   L("Игры и уход детей домой", "Ойындар және үйге қайту", "Games and going home"),
}

DAYS = ["mon", "tue", "wed", "thu", "fri"]
CYCLE_BLOCKS = ["cyc_morning", "cyc_oud", "cyc_walk", "cyc_sleep", "cyc_games"]


def label(key: str, lang: str) -> str:
    return G[key].get(lang) or G[key]["ru"]


# ══════════════════════════════════════════════════════════════
# СХЕМЫ ДОКУМЕНТОВ: разделы (заголовки) + таблицы (шапки колонок)
# ══════════════════════════════════════════════════════════════
def S(headings=None, tables=None, fields=None, phrases=None):
    return {
        "headings": headings or [],     # ключи глоссария: заголовки разделов в порядке следования
        "tables": tables or [],         # список таблиц; каждая — список ключей колонок
        "fields": fields or [],         # подписи полей шапки («Предмет: …»)
        "phrases": phrases or {},       # готовые формулы документа {ru, kz, en}: list[str]
    }


_HDR_SCHOOL = ["subject", "class", "date", "teacher_fio"]
_HDR_KG = ["age_group", "date", "educator_fio"]

SCHEMAS = {
    # ───────── ШКОЛА ─────────
    "lesson_plan": S(
        headings=["objectives", "criteria", "lang_goals", "resources", "lesson_flow", "reflection", "differentiation"],
        tables=[["lesson_stage", "activity", "assess_res"]],
        fields=_HDR_SCHOOL + ["lesson_topic"]),
    "calendar_plan": S(
        tables=[["section", "lesson_topic", "objectives", "hours", "dates"]],
        fields=["subject", "class", "teacher_fio", "year"]),
    "lesson_summary": S(
        headings=["objectives", "lesson_flow", "key_points", "homework", "reflection"],
        fields=_HDR_SCHOOL + ["lesson_topic"]),
    "monthly_report": S(
        headings=["rep_program", "rep_progress", "rep_tests", "rep_extra", "rep_selfedu", "rep_problems"],
        fields=["teacher_fio", "period"]),
    "control_analysis": S(
        headings=["quality", "success", "avg_score", "typical_error", "remedial", "conclusions"],
        tables=[["typical_error", "students_count", "cause"]],
        fields=["subject", "class", "date"]),
    "sor_soch": S(
        headings=["tasks", "rubric"],
        tables=[["criterion", "descriptor", "score"]],
        fields=["subject", "class", "section", "date"]),
    "sor_soch_analysis": S(
        headings=["quality", "success", "typical_error", "remedial"],
        tables=[["typical_error", "students_count", "cause"]],
        fields=["subject", "class", "date"]),
    "characteristic": S(headings=["characteristic"], fields=["student_fio", "class"]),
    "absence_cert": S(headings=["certificate"], fields=["student_fio", "class", "reason"]),
    "discipline_act": S(headings=["act", "violation", "witnesses"], fields=["student_fio", "class", "date"]),
    "gratitude_letter": S(headings=["gratitude"], fields=["student_fio", "class"]),
    "parent_letter": S(headings=["letter"], fields=["student_fio", "class"]),
    "vacation_request": S(headings=["application"], fields=["fio", "date"]),
    "explanation": S(headings=["explanatory"], fields=["fio", "date"]),
    "announcement": S(headings=["announcement"], fields=["date", "time", "place"]),
    # ───────── САДИК ─────────
    "kg_thematic_plan": S(
        headings=["weeks_theme", "edu_area", "activities", "final_event"],
        fields=["age_group", "month", "educator_fio"]),
    "kg_activity_summary": S(
        headings=["aims", "prelim_work", "materials", "stage_motiv", "stage_search", "stage_reflect"],
        fields=_HDR_KG + ["topic"]),
    "kg_individual_development_card": S(
        tables=[["skill_area"]], fields=["child_fio", "group", "year"]),
    "kindergarten_cycle_schedule": S(
        tables=[["block"] + DAYS], fields=["organization", "group", "period", "educator"]),
    "kg_perspective_plan": S(
        tables=[["week", "event", "edu_area", "responsible"]],
        fields=["age_group", "month", "educator_fio"]),
    "kg_matinee_script": S(
        headings=["characters", "props", "scenario"], fields=["topic", "age_group", "date"]),
    "kg_monthly_report": S(
        headings=["rep_edu_prog", "rep_events", "rep_parents", "rep_selfedu", "rep_problems"],
        fields=["educator_fio", "period", "age_group"]),
    "kg_child_characteristic": S(headings=["characteristic"], fields=["child_fio", "group"]),
    "kg_parent_letter": S(headings=["letter"], fields=["child_fio", "group"]),
    "kg_absence_cert": S(headings=["certificate"], fields=["child_fio", "group", "reason"]),
    "development_monitoring": S(
        tables=[["number", "child_fio", "skill_area", "note"]],
        fields=["organization", "group", "age", "period", "educator"]),
    "kg_monitoring": S(
        tables=[["skill_area", "mastered", "partly", "need_support"]],
        fields=["organization", "group", "age", "period", "educator"]),
    "kg_vacation_request": S(headings=["application"], fields=["fio", "date"]),
    "kg_explanation": S(headings=["explanatory"], fields=["fio", "date"]),
    "kg_announcement": S(headings=["announcement"], fields=["date", "time", "place"]),
    # ───────── ОБЩИЕ ─────────
    "parent_work_plan": S(
        headings=["meetings", "consultations", "forms"], fields=["period", "group"]),
    "upbringing_plan": S(
        tables=[["topic_theme", "deadline", "responsible"]], fields=["period", "group"]),
    "parent_meeting_protocol": S(
        headings=["agenda", "decision"], fields=["date", "group", "chairman", "secretary", "attendees"]),
    "individual_work_plan": S(
        headings=["step_schedule", "final_expected"],
        tables=[["schedule_date", "lesson_theme", "expected"]],
        fields=["child_fio", "group", "problem", "period"]),
    "housing_survey_act": S(
        headings=["family", "conditions", "final_conclusion"],
        fields=["date", "commission", "child_fio", "group"]),
}

# Фразы-формулы документов (адресат, начало заявления) — на каждом языке.
PHRASES = {
    "vacation_request": {
        "ru": ["Директору {school}", "ЗАЯВЛЕНИЕ", "Прошу предоставить мне"],
        "kz": ["{school} директорына", "ӨТІНІШ", "Маған ... демалыс беруіңізді сұраймын"],
        "en": ["To the Director of {school}", "APPLICATION", "I request to be granted"],
    },
    "kg_vacation_request": {
        "ru": ["Заведующей {school}", "ЗАЯВЛЕНИЕ", "Прошу предоставить мне"],
        "kz": ["{school} меңгерушісіне", "ӨТІНІШ", "Маған ... демалыс беруіңізді сұраймын"],
        "en": ["To the Head of {school}", "APPLICATION", "I request to be granted"],
    },
    "explanation": {
        "ru": ["Директору {school}", "ОБЪЯСНИТЕЛЬНАЯ ЗАПИСКА"],
        "kz": ["{school} директорына", "ТҮСІНІКТЕМЕ ХАТ"],
        "en": ["To the Director of {school}", "EXPLANATORY NOTE"],
    },
    "kg_explanation": {
        "ru": ["Заведующей {school}", "ОБЪЯСНИТЕЛЬНАЯ ЗАПИСКА"],
        "kz": ["{school} меңгерушісіне", "ТҮСІНІКТЕМЕ ХАТ"],
        "en": ["To the Head of {school}", "EXPLANATORY NOTE"],
    },
    "parent_letter": {"ru": ["Уважаемые родители!"], "kz": ["Құрметті ата-аналар!"], "en": ["Dear parents,"]},
    "kg_parent_letter": {"ru": ["Уважаемые родители!"], "kz": ["Құрметті ата-аналар!"], "en": ["Dear parents,"]},
    "gratitude_letter": {"ru": ["Уважаемые родители!"], "kz": ["Құрметті ата-аналар!"], "en": ["Dear parents,"]},
}


def all_doc_types():
    return list(SCHEMAS.keys())


# ══════════════════════════════════════════════════════════════
# БЛОК ДЛЯ ПРОМПТА
# ══════════════════════════════════════════════════════════════
_LANG_NAME = {"ru": "русском", "kz": "казахском", "en": "английском"}


def build_structure_instruction(doc_type: str, lang: str) -> str:
    """Явный список заголовков/колонок на языке документа. Ставится в КОНЕЦ промпта,
    чтобы перебить русские названия из нормативных правил и образцов."""
    schema = SCHEMAS.get(doc_type)
    lang = lang if lang in LANGS else "ru"
    lines = [
        "",
        f"ЯЗЫК СТРУКТУРЫ ДОКУМЕНТА — {_LANG_NAME[lang].upper()}. Это важнее любых образцов и правил выше.",
        f"Все заголовки разделов, названия колонок таблиц, подписи полей шапки, дни недели, названия этапов "
        f"и блоков, а также формулы вроде «Директору…», «Уважаемые родители!» пиши ТОЛЬКО на {_LANG_NAME[lang]} языке.",
        "Русские названия колонок и разделов в правилах и образцах выше — это лишь описание СТРУКТУРЫ: "
        "в документе вместо них используй перевод из списка ниже. Ни одного слова структуры на другом языке.",
    ]
    if schema:
        if schema["headings"]:
            lines.append("Заголовки разделов (в этом порядке, где применимо): " +
                         "; ".join(label(k, lang).upper() for k in schema["headings"]))
        for i, cols in enumerate(schema["tables"], 1):
            lines.append(f"Шапка таблицы {i}: | " + " | ".join(label(k, lang) for k in cols) + " |")
        if schema["fields"]:
            lines.append("Подписи полей шапки: " + "; ".join(label(k, lang) for k in schema["fields"]))
        if doc_type == "kindergarten_cycle_schedule":
            lines.append("Дни недели: " + " | ".join(label(k, lang) for k in DAYS))
            lines.append("Строки-блоки (ровно 5): " + "; ".join(label(k, lang) for k in CYCLE_BLOCKS))
    phrases = PHRASES.get(doc_type, {}).get(lang)
    if phrases:
        lines.append("Стандартные формулировки: " + " / ".join(phrases))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# ЛОКАЛИЗАЦИЯ ПОДПИСЕЙ ПОСЛЕ ГЕНЕРАЦИИ
# ══════════════════════════════════════════════════════════════
def _norm(s: str) -> str:
    s = s.strip().strip("|").strip()
    s = re.sub(r"^\s*(\d+[.)]|[-•*])\s*", "", s)
    s = s.rstrip(":：").strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def _build_index():
    """norm(label на любом языке) -> {lang: label}. Неоднозначные формы пропускаем."""
    index, ambiguous = {}, set()
    for key, forms in G.items():
        for lg in LANGS:
            n = _norm(forms[lg])
            if not n or n in ("№",):
                continue
            if n in index and index[n] != forms:
                ambiguous.add(n)
            index[n] = forms
    for n in ambiguous:
        index.pop(n, None)
    return index


_INDEX = _build_index()


def _match_case(src: str, dst: str) -> str:
    letters = [c for c in src if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return dst.upper()
    return dst


def _translate_cell(cell: str, lang: str) -> str:
    m = re.match(r"^(\s*)((?:\d+[.)]|[-•*])\s*)?(.*?)(\s*)$", cell, re.S)
    lead, prefix, core, trail = m.group(1), m.group(2) or "", m.group(3), m.group(4)
    if not core:
        return cell
    colon = core.endswith((":", "："))
    core_nc = core.rstrip(":：").rstrip()
    forms = _INDEX.get(_norm(core_nc))
    if not forms:
        return cell
    target = forms[lang]
    if _norm(target) == _norm(core_nc):
        return cell
    out = _match_case(core_nc, target) + (":" if colon else "")
    return lead + prefix + out + trail


# Названия документов (DOC_NAMES) регистрируются из documents.py, чтобы заголовок
# документа тоже переводился (не импортируем documents.py отсюда — циклический импорт).
_TITLES = {}


def register_titles(doc_names: dict):
    for dt in SCHEMAS:
        forms = {lg: doc_names.get(lg, {}).get(dt) for lg in LANGS}
        if all(forms.values()):
            _TITLES[dt] = forms


_STEM = 5


def _stems(s: str) -> set:
    return {w[:_STEM] for w in re.findall(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІіA-Za-z]{4,}", s.casefold())}


def fix_title(text: str, doc_type: str, lang: str) -> str:
    """Если первая строка — заголовок документа на другом языке (например, русский
    «КРАТКОСРОЧНЫЙ ПЛАН УРОКА» в казахском документе), заменяет её названием на языке документа."""
    forms = _TITLES.get(doc_type)
    if not forms or lang not in LANGS or not text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if len(line.strip()) > 100 or line.strip().startswith("|"):
            return text
        letters = [c for c in line if c.isalpha()]
        if not letters or not all(c.isupper() for c in letters):
            return text
        target = forms[lang].upper()
        if _norm(line) == _norm(target):
            return text
        line_stems = _stems(line)
        for lg in LANGS:
            if lg == lang:
                continue
            if line_stems & _stems(forms[lg]):
                lines[i] = target
                return "\n".join(lines)
        return text
    return text


def localize_labels(text: str, lang: str) -> str:
    """Заменяет подписи структуры, оставшиеся на другом языке, на подписи нужного языка.
    Затрагивает ТОЛЬКО: строки-заголовки (целиком известная подпись), ячейки таблиц,
    у которых вся ячейка — известная подпись (шапка и первая колонка), и подпись
    перед двоеточием в «Подпись: значение». Содержимое документа не меняется."""
    if lang not in LANGS or not text:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            cells = line.split("|")
            new = [_translate_cell(c, lang) if i not in (0, len(cells) - 1) else c
                   for i, c in enumerate(cells)]
            out.append("|".join(new))
            continue
        # заголовок целиком
        if stripped and len(stripped) < 90:
            t = _translate_cell(line, lang)
            if t != line:
                out.append(t)
                continue
        # «Подпись: значение»
        m = re.match(r"^(\s*(?:[-•*]\s*)?)([^:|]{2,60}):(\s*\S.*)$", line)
        if m:
            forms = _INDEX.get(_norm(m.group(2)))
            if forms and _norm(forms[lang]) != _norm(m.group(2)):
                out.append(f"{m.group(1)}{_match_case(m.group(2), forms[lang])}:{m.group(3)}")
                continue
        out.append(line)
    return "\n".join(out)


def find_foreign_labels(text: str, lang: str) -> list[str]:
    """Известные подписи структуры, оставшиеся на языке, отличном от lang."""
    problems = []
    for line in text.split("\n"):
        stripped = line.strip()
        cells = ([c for c in stripped.strip("|").split("|")] if stripped.startswith("|") else [stripped])
        for c in cells:
            forms = _INDEX.get(_norm(c)) if c.strip() else None
            if forms and _norm(forms[lang]) != _norm(c):
                problems.append(c.strip())
    return problems


_CYR = re.compile(r"[А-Яа-яЁё]")
_KZ_ONLY = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")


def has_cyrillic(s: str) -> bool:
    return bool(_CYR.search(s))


# ══════════════════════════════════════════════════════════════
# ПРОВЕРКА ПОЛНОТЫ (используется в тестах)
# ══════════════════════════════════════════════════════════════
def coverage_problems(doc_names: dict, question_getter) -> list[str]:
    """doc_names: DOC_NAMES; question_getter(doc_type, lang) -> list[dict]."""
    problems = []
    for dt in all_doc_types():
        schema = SCHEMAS[dt]
        for lg in LANGS:
            if dt not in doc_names.get(lg, {}) and dt != "kg_monitoring":
                problems.append(f"{dt}: нет названия документа ({lg})")
            for k in schema["headings"] + schema["fields"] + [c for t in schema["tables"] for c in t]:
                if not G.get(k, {}).get(lg):
                    problems.append(f"{dt}: нет подписи '{k}' ({lg})")
            if dt not in ("kindergarten_cycle_schedule", "development_monitoring", "kg_monitoring"):
                qs = question_getter(dt, lg)
                if not qs:
                    problems.append(f"{dt}: нет вопросов ({lg})")
    for lg in LANGS:
        for k, forms in G.items():
            if not forms.get(lg):
                problems.append(f"глоссарий: '{k}' без перевода ({lg})")
            if lg == "en" and has_cyrillic(forms["en"]):
                problems.append(f"глоссарий: английская подпись '{k}' содержит кириллицу")
            if lg == "kz" and k not in ("number",) and not has_cyrillic(forms["kz"]) and forms["kz"] not in ("№",):
                problems.append(f"глоссарий: казахская подпись '{k}' не на кириллице")
    return problems
