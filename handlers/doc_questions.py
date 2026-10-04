"""
Вопросы для документов на ru / kz / en.

Раньше для английского в DOC_QUESTIONS было описано только 4 типа документов из 34:
для остальных бот молча задавал один общий вопрос «Describe what you need» и терял
структурированный сбор данных. Здесь вопросы строятся по ключам полей, а для английского
берутся из EN_Q — поэтому набор полей у всех языков всегда одинаковый.
"""

# Английские формулировки по ключам полей (ключи общие для всех языков).
EN_Q = {
    "subject_class":   "📚 Subject and class?",
    "topic":           "📖 Topic?",
    "key_points":      "🔑 Key points?\n\n_Or write «automatic»_",
    "period":          "📅 Which period?\n\n_Example: October 2025_",
    "classes":         "🏫 Classes?\n\n_Example: 7A, 8B, 9C_",
    "performance":     "📊 Academic performance?\n\n_Example: 7A — 65%/90% (knowledge quality % / success %)_",
    "extra":           "🏆 Extracurricular events, olympiads?\n\n_If none — write «none»_",
    "date":            "📅 Date?",
    "results":         "📊 How many students got each grade?\n\n_Format: 5 — N students, 4 — N students, and so on_",
    "sor_or_soch":     "📋 SOR or SOCH?\n\n_Write SOR or SOCH_",
    "section_topic":   "📖 Unit or topic?",
    "max_score":       "🔢 Maximum score?\n\n_Usually 10 or 15_",
    "criteria":        "📊 Do you have ready criteria? If yes — write them.\n\n_If not — write «automatic»_",
    "typical_errors":  "⚠️ Typical mistakes of the class?\n\n_Or write «automatic»_",
    "student_name":    "👤 Full name and class/group of the student or child?\n\n_Or choose from the list_",
    "behavior":        "😊 Behaviour and character?\n\n_Example: disciplined, active_",
    "activities":      "🏆 Participation in events?\n\n_If none — write «none»_",
    "purpose":         "📋 Purpose of the reference?\n\n_Example: for submission on request_",
    "absence_dates":   "📅 Dates of absence?\n\n_Example: 14–16 October 2025_",
    "reason":          "❓ Reason?\n\n_Example: illness (certificate available)_",
    "violation":       "⚠️ Description of the violation?",
    "witnesses":       "👥 Witnesses?\n\n_If none — write «none»_",
    "achievement":     "🏆 What is the award for?\n\n_Example: winning the mathematics olympiad_",
    "details":         "📝 Details of the letter?",
    "age_group":       "👶 Age group?\n\n_Example: middle group (4–5 years)_",
    "month":           "📅 Which month?\n\n_Example: November 2025_",
    "topics":          "📋 Weekly themes, separated by commas\n\n_Or write «by programme»_",
    "events":          "🎉 Key events of the month, separated by commas?\n\n_Or write «automatic»_",
    "theme":           "🎭 Theme of the matinee?\n\n_Example: New Year, 8 March, Nauryz_",
    "roles":           "🎬 Roles and participants?\n\n_Example: Santa — educator, Snow Maiden — music teacher, 5 children_",
    "props":           "🎁 Props needed?\n\n_Or write «automatic»_",
    "progress":        "📊 How well did the children master the programme (briefly)?",
    "vacation_type":   "🏖 Type of leave:\n\n1 — Annual paid leave\n2 — Unpaid leave\n\n_Example: 1_",
    "dates":           "📅 Dates?\n\n_Example: from 01.07.2026 to 25.08.2026_",
    "absence_date":    "📅 Date of absence or lateness?",
    "documents":       "📎 Supporting documents?\n\n_If none — write «none»_",
    "event":           "📢 What event?\n\n_Example: matinee, parent meeting_",
    "datetime":        "📅 Date and time?",
    "location":        "📍 Venue?\n\n_Example: music hall_",
    "group":           "🏷 Class/group?",
    "directions":      "📋 Areas of work?\n\n_Example: patriotic, health, labour. Or «automatic»_",
    "attendees":       "👥 How many people attended?",
    "theses":          "🗣 Briefly, what did the speakers say?\n\n_Or write «automatic»_",
    "decision":        "✅ Final decision of the meeting?",
    "problem":         "❓ What is the problem / what needs improving?\n\n_Example: falls behind in reading_",
    "frequency":       "⏰ How often are the sessions?\n\n_Example: twice a week_",
    "visit_date":      "📅 Date of the inspection?",
    "commission":      "👥 Commission members?\n\n_Example: class teacher Ivanova M., social worker Petrova A._",
    "family":          "👨‍👩‍👧 Family composition?",
    "conditions":      "🏠 Living conditions and the child's study place?\n\n_Or write «automatic»_",
    "duration":        "⏱ Duration?\n\n_Example: 45 minutes_",
    "goals":           "🎯 Learning objectives?\n\n_Or write «automatic»_",
    "textbook":        "📘 Textbook or programme?\n\n_If unknown — write «standard MoE programme»_",
    "hours_per_week":  "⏰ Hours per week?\n\n_If unknown — write «don't know»_",
    "child_name":      "👶 Child's full name?",
    "birth_year_age":  "🎂 Birth year and age?",
    "school_year":     "📅 Academic year?",
    "observations":    "🔍 Do you have real observations, or do you need a blank form?",
    "materials":       "🧰 Which materials are actually available? If none — write «none».",
    "skills":          "📊 What was covered (by educational areas)?\n\n_Or write «automatic» — I will use the areas from the state standard_",
    "description":     "✍️ Describe in detail what you need:",
}


def get_questions(doc_type: str, lang: str, doc_questions: dict, registry_questions: dict) -> list:
    """Список вопросов для документа на языке lang. Никогда не пустой для известных типов:
    если для языка вопросов нет, берётся русский набор полей и переводится по ключам."""
    qs = (registry_questions.get(lang, {}).get(doc_type)
          or doc_questions.get(lang, {}).get(doc_type))
    if qs:
        return qs
    base = (registry_questions.get("ru", {}).get(doc_type)
            or doc_questions.get("ru", {}).get(doc_type) or [])
    if lang == "en":
        return [{"key": q["key"], "q": EN_Q.get(q["key"], q["q"])} for q in base]
    return base


def all_keys_translated(doc_questions: dict, registry_questions: dict) -> list:
    """Ключи, у которых нет английской формулировки (для тестов)."""
    keys = set()
    for src in (doc_questions.get("ru", {}), registry_questions.get("ru", {})):
        for qs in src.values():
            keys.update(q["key"] for q in qs)
    return sorted(k for k in keys if k not in EN_Q)
