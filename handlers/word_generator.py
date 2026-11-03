"""
Docura.kz — профессиональный Word генератор
Стандарт: Times New Roman 14pt, поля ГОСТ, кириллица
"""
import os
import re
import tempfile
from datetime import datetime
from handlers.doc_schemas import label as _label, DAYS as _DAYS, L as _L
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ── Цвета ─────────────────────────────────────────────
NAVY  = RGBColor(0x1A, 0x2E, 0x5A)
BLUE  = RGBColor(0x2E, 0x75, 0xB6)
GRAY  = RGBColor(0x59, 0x59, 0x59)
BLACK = RGBColor(0x1A, 0x20, 0x2C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_BLUE_HEX = "EBF3FB"
NAVY_HEX       = "1A2E5A"
BLUE_HEX       = "2E75B6"

FONT_MAIN = "Times New Roman"
FONT_SIZE_MAIN = 14  # ГОСТ РК для официальных документов


def _set_cell_color(cell, hex_color: str):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color)
    tcPr.append(shd)


def _set_cell_borders(cell, color_hex="C8D8E8", size="4"):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "bottom", "left", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"),   "single")
        el.set(qn("w:sz"),    size)
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color_hex)
        borders.append(el)
    tcPr.append(borders)


def _add_paragraph_border_left(para, color_hex=BLUE_HEX, size="18"):
    """Синяя полоска слева у параграфа"""
    pPr  = para._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"),   "single")
    left.set(qn("w:sz"),    size)
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color_hex)
    pBdr.append(left)
    pPr.append(pBdr)


def _add_hrule(doc, color_hex=BLUE_HEX, size="12"):
    """Горизонтальная линия"""
    p   = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    size)
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), color_hex)
    pBdr.append(bot)
    pPr.append(pBdr)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(0)
    return p


def _run(para, text, bold=False, italic=False, size=None, color=None, font=None):
    run = para.add_run(text)
    run.font.name  = font or FONT_MAIN
    run.font.size  = Pt(size or FONT_SIZE_MAIN)
    run.font.bold  = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = color
    return run


def _para(doc, text="", align=WD_ALIGN_PARAGRAPH.LEFT,
          bold=False, italic=False, size=None, color=None,
          space_before=0, space_after=4, first_indent=None,
          keep_with_next=False):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before    = Pt(space_before)
    p.paragraph_format.space_after     = Pt(space_after)
    p.paragraph_format.keep_with_next  = keep_with_next
    if first_indent is not None:
        p.paragraph_format.first_line_indent = Cm(first_indent)
    if text:
        _run(p, text, bold=bold, italic=italic, size=size, color=color)
    return p


def _add_signature_block(doc, teacher_name: str, director_name: str, lang: str = "ru"):
    """Добавляет обязательный блок подписей в конец каждого Word-документа —
    на языке ВЫБРАННОГО документа, а не жёстко на русском. Раньше этот блок
    был захардкожен по-русски и generate_word() даже не принимала параметр
    языка — казахский/английский документ получал русскую подпись в конце."""
    labels = {
        "ru": {"staff": "Педагог", "head": "Руководитель", "date": "Дата", "seal": "М.П."},
        "kz": {"staff": "Педагог", "head": "Басшы", "date": "Күні", "seal": "М.О."},
        "en": {"staff": "Staff member", "head": "Head", "date": "Date", "seal": "Seal"},
    }.get(lang, {"staff": "Педагог", "head": "Руководитель", "date": "Дата", "seal": "М.П."})
    doc.add_paragraph().paragraph_format.space_before = Pt(12)
    _add_hrule(doc, "A0AEC0", "6")
    _para(doc, f"{labels['staff']}: {teacher_name} ____________________", space_before=6)
    _para(doc, f"{labels['head']}: {director_name} ____________________")
    _para(doc, f"{labels['date']}: ____________________")
    _para(doc, labels["seal"])
    _para(doc, "⚠️ Составлено с помощью ИИ. Проверено учителем: ____________________ (подпись/дата)", italic=True, size=9, space_before=8)

def validate_cycle_schedule_data(data: dict) -> list[str]:
    """Проверяет обязательные поля до создания циклограммы."""
    labels = {
        "group": "группа",
        "period": "неделя или даты",
        "week_topic": "тема недели",
    }
    return [label for key, label in labels.items() if not str(data.get(key, "")).strip()]


def generate_word(content: str, title: str, teacher_name: str = "", director_name: str = "", cycle_data: dict | None = None, monitoring_data: dict | None = None, registry_doc_type: str = "", lang: str = "ru") -> str:
    """
    Генерирует красивый Word документ по стандартам РК.
    Возвращает путь к .docx файлу.
    lang — язык ВЫБРАННОГО документа (doc_lang), влияет на подписи блока в конце
    (см. _add_signature_block). Основной текст документа уже приходит на нужном
    языке от Claude — здесь локализуются только элементы, которые генератор
    добавляет сам (не через LLM): подписи, шапки таблиц циклограммы/мониторинга.
    """
    doc = Document()
    doc.core_properties.author = "Docura"

    is_dev_card = (
        registry_doc_type in ("kg_individual_development_card", "development_monitoring", "kg_monitoring")
        or bool(monitoring_data)
    )

    # ── Страница: поля ГОСТ РК ────────────────────────
    for section in doc.sections:
        if is_dev_card:
            section.orientation   = WD_ORIENT.LANDSCAPE
            section.page_width    = Cm(29.7)
            section.page_height   = Cm(21.0)
            section.left_margin   = Cm(2.0)
            section.right_margin  = Cm(2.0)
            section.top_margin    = Cm(1.0)
            section.bottom_margin = Cm(1.5)
        elif cycle_data:
            section.left_margin   = Cm(3.0)
            section.right_margin  = Cm(1.5)
            section.top_margin    = Cm(2.0)
            section.bottom_margin = Cm(2.0)
            section.orientation   = WD_ORIENT.LANDSCAPE
            section.page_width    = Cm(29.7)
            section.page_height   = Cm(21.0)
        else:
            section.left_margin   = Cm(3.0)   # 30мм
            section.right_margin  = Cm(1.5)   # 15мм
            section.top_margin    = Cm(2.0)   # 20мм
            section.bottom_margin = Cm(2.0)   # 20мм
            section.page_width    = Cm(21.0)  # A4
            section.page_height   = Cm(29.7)

    # ── Стиль Normal по умолчанию ─────────────────────
    style = doc.styles["Normal"]
    style.font.name = FONT_MAIN
    style.font.size = Pt(12 if is_dev_card else FONT_SIZE_MAIN)
    style.paragraph_format.line_spacing = Pt(14 if is_dev_card else 18)
    style.paragraph_format.space_after  = Pt(0)

    # Бренд Docura сохраняется только в метаданных файла.

    if not is_dev_card:
        # ── ЗАГОЛОВОК ДОКУМЕНТА ───────────────────────────
        _para(doc, title.upper(),
              align=WD_ALIGN_PARAGRAPH.CENTER,
              bold=True, size=16, color=NAVY,
              space_before=6, space_after=6)

        # Синяя линия под заголовком
        _add_hrule(doc, BLUE_HEX, "12")
        doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # ── ПАРСИМ СОДЕРЖИМОЕ ─────────────────────────────
    if is_dev_card:
        _add_development_monitoring(doc, monitoring_data or {})
    elif cycle_data:
        _add_kindergarten_cycle_schedule(doc, cycle_data)
    else:
        _parse_content(doc, content)

    _add_signature_block(doc, teacher_name, director_name, lang)

    fname = os.path.join(tempfile.gettempdir(), f"docura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx")
    doc.save(fname)
    return fname


def _add_individual_development_card(doc, data: dict):
    """Формирует индивидуальную карту развития ребёнка по единому эталону Жеке даму картасы."""
    _add_development_monitoring(doc, data)



def _add_kindergarten_cycle_schedule(doc, data: dict):
    """Создаёт циклограмму только из подтверждённых данных пользователя."""
    missing = validate_cycle_schedule_data(data)
    if missing:
        raise ValueError("Не заполнены обязательные поля: " + ", ".join(missing))

    def value(key):
        return str(data.get(key, "")).strip() or "________________"

    lang = data.get("lang", "ru")
    if lang not in ("ru", "kz", "en"):
        lang = "ru"
    info = [
        (_label("organization", lang), value("organization")),
        (_label("group", lang), value("group")),
        (_label("period", lang), value("period")),
        (_label("educator", lang), value("educator_name")),
    ]
    info_table = doc.add_table(rows=len(info), cols=2)
    info_table.style = "Table Grid"
    for row, (label, text) in zip(info_table.rows, info):
        _set_cell_color(row.cells[0], LIGHT_BLUE_HEX)
        _set_cell_borders(row.cells[0])
        _set_cell_borders(row.cells[1])
        _run(row.cells[0].paragraphs[0], label + ":", bold=True, size=11, color=NAVY)
        _run(row.cells[1].paragraphs[0], text, size=11)

    _para(doc, _label("planned_week", lang), bold=True, color=NAVY,
          space_before=10, space_after=5)
    topic = value("week_topic")
    events = str(data.get("events", "")).strip()
    has_events = events and events.lower() not in {"нет", "жоқ", "none", "no"}
    days = [_label(k, "ru") for k in _DAYS]
    blocks = [
        _label("cyc_morning", "ru"),
        "ОУД",
        _label("cyc_walk", "ru"),
        _label("cyc_sleep", "ru"),
        _label("cyc_games", "ru"),
    ]
    daily = [
        [f"Беседа по теме «{topic}»; гимнастика с наблюдениями.", f"ОУД: «Знакомство с темой {topic}». Цель: выделять признаки. Игра: «Найди пару».", f"Наблюдение по теме «{topic}»; подвижная игра «Повтори движение».", "Подготовка ко сну по режиму группы; дыхательное упражнение.", f"Игра «Назови по теме {topic}»; беседа при уходе."],
        [f"Рассматривание иллюстраций по теме «{topic}»; новая гимнастика.", f"ОУД: «Признаки и действия». Цель: расширять словарь. Игра: «Что изменилось?».", "Подвижная игра с правилами; наблюдение на прогулке.", "Расслабление перед сном; процедуры по режиму группы.", f"Творческая игра по теме «{topic}»; индивидуальная беседа."],
        [f"Утренний круг по теме «{topic}»; ритмическая гимнастика.", f"ОУД: «Сравни и выбери». Цель: учить сравнивать. Игра: «Лишний предмет».", "Поисковая прогулка; игра-эстафета без инвентаря.", "Чтение перед сном; процедуры по режиму группы.", "Настольная игра «Собери картинку»; итоги дня."],
        [f"Пальчиковая игра по теме «{topic}»; координационная гимнастика.", f"ОУД: «Где и как бывает». Цель: закреплять связи. Игра: «Кому что нужно?».", "Наблюдение и игра «Опиши, не называя».", "Спокойная музыка перед сном; процедуры по режиму группы.", f"Сюжетно-ролевая игра по теме «{topic}»; обмен впечатлениями."],
        [f"Повторение слов по теме «{topic}»; выбор движений детьми.", f"ОУД: «Итоги недели». Цель: применять знания. Игра: «Угадай по описанию».", "Игра-наблюдение «Что запомнили?»; двигательная активность.", "Подготовка ко сну по режиму группы; расслабление.", "Свободные игры и беседа об итогах темы."],
    ]
    if lang == "en":
        days = [_label(k, "en") for k in _DAYS]
        blocks = [_label("cyc_morning", "en"), "Organized learning activity", _label("cyc_walk", "en"),
                  _label("cyc_sleep", "en"), _label("cyc_games", "en")]
        daily = [
            [f"Conversation on the topic “{topic}”; observation exercises.", f"Learning activity: “Getting to know {topic}”. Aim: identify features. Game: “Find a pair”.", f"Observation on the topic “{topic}”; action game “Repeat the movement”.", "Preparation for nap according to the group routine; breathing exercise.", f"Game “Name it by topic: {topic}”; short chat with parents."],
            [f"Looking at illustrations on “{topic}”; new exercises.", "Learning activity: “Features and actions”. Aim: expand vocabulary. Game: “What has changed?”.", "Outdoor game with rules; observation on the walk.", "Relaxation before nap; procedures per group routine.", f"Creative play on “{topic}”; individual conversation."],
            [f"Morning circle on “{topic}”; rhythmic exercises.", "Learning activity: “Compare and choose”. Aim: learn to compare. Game: “Odd one out”.", "Exploration walk; relay game without equipment.", "Reading before nap; procedures per group routine.", "Board game “Put the picture together”; summary of the day."],
            [f"Finger play on “{topic}”; coordination exercises.", "Learning activity: “Where and how it happens”. Aim: consolidate connections. Game: “Who needs what?”.", "Observation and the game “Describe without naming”.", "Calm music before nap; procedures per group routine.", f"Role play on “{topic}”; sharing impressions."],
            [f"Repeating words on “{topic}”; children choose the movements.", "Learning activity: “Results of the week”. Aim: apply knowledge. Game: “Guess by description”.", "Observation game “What do we remember?”; physical activity.", "Preparation for nap according to the group routine; relaxation.", "Free play and a talk about the results of the topic."],
        ]
    if lang == "kz":
        days = [_label(k, "kz") for k in _DAYS]
        blocks = [_label("cyc_morning", "kz"), "ҰОҚ", _label("cyc_walk", "kz"), _label("cyc_sleep", "kz"), _label("cyc_games", "kz")]
        daily = [
            [f"«{topic}» тақырыбы бойынша әңгіме; бақылау жаттығулары.", f"ҰОҚ: «{topic} тақырыбымен танысу». Мақсат: белгілерін ажырату. Ойын: «Жұбын тап».", f"«{topic}» бойынша бақылау; «Қимылды қайтала» ойыны.", "Топ режимі бойынша ұйқыға дайындық; тыныс алу жаттығуы.", f"«{topic}» бойынша атау ойыны; ата-анамен қысқа әңгіме."],
            [f"«{topic}» суреттерін қарау; жаңа гимнастика.", "ҰОҚ: «Белгілері мен әрекеттері». Мақсат: сөздік қорын кеңейту. Ойын: «Не өзгерді?».", "Ережелі қимылды ойын; серуендегі бақылау.", "Ұйқы алдындағы босаңсу; топ режимі бойынша процедуралар.", f"«{topic}» бойынша шығармашылық ойын; жеке әңгіме."],
            [f"«{topic}» бойынша таңғы шеңбер; ырғақты гимнастика.", "ҰОҚ: «Салыстыр және таңда». Мақсат: салыстыруға үйрету. Ойын: «Артық зат».", "Іздеу серуені; құралсыз эстафета ойыны.", "Ұйқы алдындағы оқу; топ режимі бойынша процедуралар.", "Үстел ойыны; күн қорытындысы."],
            [f"«{topic}» бойынша саусақ ойыны; үйлестіру гимнастикасы.", "ҰОҚ: «Қайда және қалай болады». Мақсат: байланыстарды бекіту. Ойын: «Кімге не керек?».", "Бақылау және «Атын атамай сипатта» ойыны.", "Ұйқы алдындағы баяу музыка; топ режимі бойынша процедуралар.", f"«{topic}» бойынша сюжеттік ойын; әсерлермен алмасу."],
            [f"«{topic}» сөздерін қайталау; балалар қимылды таңдайды.", "ҰОҚ: «Апта қорытындысы». Мақсат: білімді ойында қолдану. Ойын: «Сипаттама бойынша тап».", "«Не есте қалды?» бақылау ойыны; қимыл белсенділігі.", "Топ режимі бойынша ұйқыға дайындық; босаңсу.", "Еркін ойындар және тақырып қорытындысы."],
        ]

    table = doc.add_table(rows=1 + len(blocks), cols=1 + len(days))
    table.style = "Table Grid"
    headers = [_L("Режимный момент", "Режим сәті", "Routine block")[lang], *days]
    for c_idx, header in enumerate(headers):
        cell = table.rows[0].cells[c_idx]
        _set_cell_color(cell, NAVY_HEX)
        _set_cell_borders(cell, NAVY_HEX)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, header, bold=True, size=9, color=WHITE)

    for r_idx, block in enumerate(blocks, start=1):
        label_cell = table.rows[r_idx].cells[0]
        _set_cell_color(label_cell, LIGHT_BLUE_HEX)
        _set_cell_borders(label_cell)
        _run(label_cell.paragraphs[0], block, bold=True, size=9, color=NAVY)
        for c_idx in range(1, len(days) + 1):
            cell = table.rows[r_idx].cells[c_idx]
            _set_cell_borders(cell)
            text = daily[c_idx - 1][r_idx - 1]
            # Пользовательское мероприятие отражается как план, а не как факт.
            if has_events and r_idx == 2:
                text += " " + _L("Учесть при планировании", "Жоспарлау кезінде ескеру", "To consider when planning")[lang] + f": {events}."
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _run(p, text, size=8)



def _infer_birth_and_age(child_entry, data: dict, group_str: str, lang: str, idx: int) -> tuple[str, str, str, str]:
    """Извлекает только предоставленные ФИО, год рождения, возраст и группу."""
    clean_group = group_str.strip() if group_str else ""
    if clean_group.lower() in {"", "автоматически", "автоматты", "automatic", "________________"}:
        clean_group = "________________"

    name = ""
    raw_by = ""
    raw_age = ""

    if isinstance(child_entry, dict):
        name = str(child_entry.get("name") or child_entry.get("child_name") or "").strip()
        raw_by = str(child_entry.get("birth_year") or child_entry.get("birth_date") or "").strip()
        raw_age = str(child_entry.get("age") or "").strip()
        if child_entry.get("group") or child_entry.get("class_name"):
            g_cand = str(child_entry.get("group") or child_entry.get("class_name")).strip()
            if g_cand and g_cand.lower() not in {"автоматически", "автоматты", "automatic"}:
                clean_group = g_cand
    elif isinstance(child_entry, (list, tuple)):
        name = str(child_entry[0]).strip() if len(child_entry) > 0 else ""
        raw_by = str(child_entry[1]).strip() if len(child_entry) > 1 else ""
        raw_age = str(child_entry[2]).strip() if len(child_entry) > 2 else ""
    else:
        name = str(child_entry or "").strip()

    if not raw_by and data.get("birth_year"):
        raw_by = str(data.get("birth_year")).strip()
    if not raw_age and data.get("age"):
        raw_age = str(data.get("age")).strip()
    if not raw_by and not raw_age and data.get("birth_year_age"):
        bya = str(data.get("birth_year_age")).strip()
        ym = re.search(r"\b(20\d{2})\b", bya)
        if ym:
            raw_by = ym.group(1)
        am = re.search(r"\b([1-7])\s*(?:жас|лет|год|г\.|y)", bya, re.I)
        if am:
            raw_age = am.group(1)

    # Не выводим возраст из одного года рождения: без даты рождения он может
    # быть неточным в течение учебного года.
    birth_year = "________________"
    age = "________________"
    ym = re.search(r"\b(20\d{2})\b", raw_by)
    if ym:
        birth_year = ym.group(1)
    am = re.search(r"\b([1-7])\b", raw_age)
    if am:
        age = am.group(1)

    if lang == "kz":
        by_str = f"{birth_year} жыл" if birth_year != "________________" else birth_year
        age_str = f"{age} жас" if age != "________________" else age
    elif lang == "en":
        by_str = birth_year
        age_str = f"{age} years" if age != "________________" else age
    else:
        by_str = f"{birth_year} г." if birth_year != "________________" else birth_year
        age_num = int(age) if age.isdigit() else 0
        age_word = "года" if age_num in (2, 3, 4) else "лет"
        age_str = f"{age} {age_word}" if age != "________________" else age

    return name or "________________", by_str, age_str, clean_group


def _add_development_monitoring(doc, data: dict):
    """Генерирует Индивидуальные карты развития детей (Баланың жеке даму картасы)
    строго по структуре эталона Жеке даму картасы.docx (альбомная ориентация, 7x5 таблица)."""
    lang = data.get("lang", "ru")
    if lang not in ("ru", "kz", "en"):
        lang = "ru"

    # Учебный год
    raw_period = str(data.get("school_year") or data.get("period") or "").strip()
    ym = re.search(r"20\d{2}\s*[–\-/]\s*20\d{2}", raw_period)
    school_year = re.sub(r"\s*[–/-]\s*", "–", ym.group(0)) if ym else "2026–2027"

    group_raw = str(data.get("group") or data.get("age_group") or "").strip()

    # Проверяем, нужен ли пустой бланк
    obs_raw = str(data.get("observations") or "").strip().lower()
    raw_children_field = data.get("children")
    is_blank = obs_raw in {"пустой", "бос", "blank", "empty"} or (
        isinstance(raw_children_field, str) and raw_children_field.strip().lower() in {"пустой", "бос", "blank", "empty"}
    )

    # Собираем список детей (из БД, из параметров или из эталонного списка)
    children_entries = []
    if data.get("children_records"):
        children_entries = list(data["children_records"])
    elif data.get("child_name") or data.get("student_name"):
        c_name = str(data.get("child_name") or data.get("student_name")).strip()
        if c_name and c_name.lower() not in {"автоматически", "автоматты", "automatic"}:
            children_entries = [c_name]
    elif isinstance(raw_children_field, list) and raw_children_field:
        if all(x == "—" for x in raw_children_field):
            is_blank = True
            children_entries = ["________________"]
        else:
            children_entries = [x for x in raw_children_field if str(x).strip()]
    elif isinstance(raw_children_field, str) and raw_children_field.strip():
        rc = raw_children_field.strip()
        if rc.lower() not in {"автоматически", "автоматты", "automatic", "пустой", "бос", "blank", "empty"}:
            children_entries = [x.strip() for x in rc.replace("\n", ",").split(",") if x.strip()]

    if not children_entries:
        children_entries = ["________________"]

    title_text = {
        "kz": f"{school_year} оқу жылындағы Баланың жеке даму картасы",
        "ru": f"Индивидуальная карта развития ребёнка на {school_year} учебный год",
        "en": f"Child's Individual Development Card for the {school_year} academic year",
    }[lang]

    headers = [
        _label("monitoring_competencies", lang),
        _label("monitoring_initial", lang),
        _label("monitoring_intermediate", lang),
        _label("monitoring_final", lang),
        _label("monitoring_conclusion", lang),
    ]

    competencies = [
        _label("monitoring_physical", lang),
        _label("monitoring_communication", lang),
        _label("monitoring_cognitive", lang),
        _label("monitoring_creative_research", lang),
        _label("monitoring_social_emotional", lang),
    ]

    legend_text = {
        "kz": (
            "Дағдыларды меңгеру деңгейлері: "
            "1-деңгей – бала белгілі бір қимыл мен біліктерді жаңғырта алады (1-3 ұпай) "
            "2 – деңгей – бала не істеп жатқанын түсінеді, белгілі білім қорына ие (4 – 6 ұпай) "
            "3 – деңгей – бала өзінің білетінін және жасай алатынын қолданады, білімін өз бетінше және шығармашылықпен қолдана алады (7 – 10 ұпай)"
        ),
        "ru": (
            "Уровни овладения навыками: "
            "1-уровень – ребенок может воспроизводить отдельные действия и умения (1-3 балла) "
            "2 – уровень – ребенок понимает, что делает, обладает определенным запасом знаний (4 – 6 баллов) "
            "3 – уровень – ребенок применяет то, что знает и умеет, самостоятельно и творчески использует знания (7 – 10 баллов)"
        ),
        "en": (
            "Skill mastery levels: "
            "Level 1 – the child can reproduce certain actions and skills (1-3 points) "
            "Level 2 – the child understands what they are doing and has a certain stock of knowledge (4 – 6 points) "
            "Level 3 – the child applies what they know and can do, using knowledge independently and creatively (7 – 10 points)"
        ),
    }[lang]

    col_widths = [Cm(4.55), Cm(6.32), Cm(5.07), Cm(5.07), Cm(5.06)]
    custom_rows = data.get("rows") or []
    if not isinstance(custom_rows, (list, tuple)):
        custom_rows = []

    for idx, entry in enumerate(children_entries):
        if idx > 0:
            doc.add_page_break()

        c_name, c_by, c_age, c_group = _infer_birth_and_age(entry, data, group_raw, lang, idx)

        _para(
            doc,
            title_text,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            bold=True,
            size=12,
            space_before=0,
            space_after=2,
            keep_with_next=True,
        )

        meta_text = {
            "kz": f"Баланың аты-жөні: {c_name}  Туған жылы: {c_by}\nБаланың жасы: {c_age}     Тобы: {c_group}",
            "ru": f"ФИО ребёнка: {c_name}  Год рождения: {c_by}\nВозраст ребёнка: {c_age}     Группа: {c_group}",
            "en": f"Child's full name: {c_name}  Birth year: {c_by}\nChild's age: {c_age}     Group: {c_group}",
        }[lang]
        _para(
            doc,
            meta_text,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            bold=True,
            size=12,
            space_before=0,
            space_after=4,
            keep_with_next=True,
        )

        table = doc.add_table(rows=7, cols=5)
        table.style = "Table Grid"

        # Шапка таблицы (Row 0)
        for c_idx, h_text in enumerate(headers):
            cell = table.rows[0].cells[c_idx]
            cell.width = col_widths[c_idx]
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(p, h_text, bold=True, size=11)

        # Строки компетентностей (Rows 1..5)
        for r_idx, comp_name in enumerate(competencies, start=1):
            row_cells = table.rows[r_idx].cells
            for c_idx in range(5):
                row_cells[c_idx].width = col_widths[c_idx]

            p0 = row_cells[0].paragraphs[0]
            p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(p0, comp_name, bold=True, size=11)

            if not is_blank and r_idx - 1 < len(custom_rows):
                row_data = custom_rows[r_idx - 1]
                if isinstance(row_data, (list, tuple)) and len(row_data) >= 4:
                    c1, c2, c3, c4 = row_data[:4]
                else:
                    continue
                for c_idx, val in enumerate((c1, c2, c3, c4), start=1):
                    p = row_cells[c_idx].paragraphs[0]
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if c_idx == 4 else WD_ALIGN_PARAGRAPH.LEFT
                    _run(p, val, size=11)

        # Нижняя строка (Row 6) — объединённая легенда уровней
        merged_cell = table.rows[6].cells[0].merge(table.rows[6].cells[4])
        p_leg = merged_cell.paragraphs[0]
        p_leg.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _run(p_leg, legend_text, bold=True, size=10)



def _parse_content(doc, content: str):
    lines = content.split("\n")

    def is_section(s):
        if not s or len(s) < 3: return False
        if re.match(r'^(\d+\.|[IVX]+\.)\s+', s): return True
        no_punct = re.sub(r'[^a-zA-Zа-яёА-ЯЁіІқҚғҒүҮұҰіәӘһҺ]', '', s)
        return (len(no_punct) > 2
                and no_punct == no_punct.upper()
                and no_punct.upper() != no_punct.lower())

    def is_sub(s):
        return (s.endswith(":") and 4 < len(s) < 80
                and not s.startswith(("-","•","*","–")))

    def is_bullet(s):
        return s.startswith(("- ","• ","* ","– ","· "))

    def is_sig(s):
        kw = ["подпись","директор","учитель","кл.рук","дата:","м.п.",
              "классный руководитель","қолы","мұғалім","күні:",
              "мектеп директоры","________","___/",
              "signature","director","teacher","head of","date:","seal","staff member",
              "меңгеруші","тәрбиеші","заведующ","воспитатель"]
        return any(k in s.lower() for k in kw)

    def is_table(s):
        return s.count("|") >= 2

    prev_empty = False
    i = 0
    while i < len(lines):
        s = lines[i].strip()

        if "Составлено с помощью ИИ" in s and "Проверено учителем" in s:
            i += 1
            continue

        if not s:
            if not prev_empty:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(3)
            prev_empty = True
            i += 1
            continue
        prev_empty = False

        # Таблица Markdown
        if is_table(s):
            tbl_lines = []
            while i < len(lines) and (is_table(lines[i]) or
                  re.match(r'^\s*\|[\s\-:]+\|', lines[i])):
                raw = lines[i].strip()
                if not re.match(r'^\|[\s\-:]+\|', raw):
                    tbl_lines.append(raw)
                i += 1
            if tbl_lines:
                _add_table(doc, tbl_lines)
            continue

        # Заголовок раздела — синяя плашка + полоска
        if is_section(s):
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
            p = doc.add_paragraph()
            p.paragraph_format.space_before   = Pt(8)
            p.paragraph_format.space_after    = Pt(4)
            p.paragraph_format.left_indent    = Cm(0.3)
            p.paragraph_format.keep_with_next = True
            _add_paragraph_border_left(p, BLUE_HEX, "20")
            # Светло-голубой фон через shading параграфа
            pPr = p._p.get_or_add_pPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"),   "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"),  LIGHT_BLUE_HEX)
            pPr.append(shd)
            r = p.add_run("  " + s)
            r.font.name      = FONT_MAIN
            r.font.size      = Pt(FONT_SIZE_MAIN)
            r.font.bold      = True
            r.font.color.rgb = NAVY
            i += 1
            continue

        # Подзаголовок
        if is_sub(s):
            p = _para(doc, s, bold=True, color=GRAY,
                      space_before=6, space_after=3,
                      keep_with_next=True)
            i += 1
            continue

        # Буллет
        if is_bullet(s):
            text = re.sub(r'^[-•*–·]\s+', '', s)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent       = Cm(1.25)
            p.paragraph_format.first_line_indent = Cm(-0.5)
            p.paragraph_format.space_after        = Pt(2)
            bullet_run = p.add_run("▸  ")
            bullet_run.font.name      = "Arial"
            bullet_run.font.size      = Pt(11)
            bullet_run.font.color.rgb = BLUE
            text_run = p.add_run(text)
            text_run.font.name      = FONT_MAIN
            text_run.font.size      = Pt(FONT_SIZE_MAIN)
            text_run.font.color.rgb = BLACK
            i += 1
            continue

        # Подпись
        if is_sig(s):
            p = _para(doc, s, color=GRAY,
                      space_before=3, space_after=3)
            i += 1
            continue

        # Обычный абзац
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.first_line_indent = Cm(1.25)
        p.paragraph_format.space_after        = Pt(3)
        r = p.add_run(s)
        r.font.name      = FONT_MAIN
        r.font.size      = Pt(FONT_SIZE_MAIN)
        r.font.color.rgb = BLACK
        i += 1


def _add_table(doc, lines: list):
    """Красивая таблица с синей шапкой и чередующимися строками"""
    rows_data = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows_data.append(cells)

    if not rows_data:
        return

    max_cols = max(len(r) for r in rows_data)
    rows_data = [r + [""] * (max_cols - len(r)) for r in rows_data]

    table = doc.add_table(rows=len(rows_data), cols=max_cols)
    table.style = "Table Grid"

    # Ширина колонок
    page_width_cm = 21.0 - 3.0 - 1.5  # A4 - поля
    col_width = Cm(page_width_cm / max_cols)
    for row in table.rows:
        for cell in row.cells:
            cell.width = col_width

    for r_idx, row_data in enumerate(rows_data):
        is_header = (r_idx == 0)
        is_even   = (r_idx % 2 == 0)
        bg = NAVY_HEX if is_header else ("EBF3FB" if is_even else "FFFFFF")

        for c_idx, cell_text in enumerate(row_data):
            cell = table.rows[r_idx].cells[c_idx]
            _set_cell_color(cell, bg)
            _set_cell_borders(cell, "C8D8E8" if not is_header else NAVY_HEX)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

            # Очищаем дефолтный параграф
            for para in cell.paragraphs:
                for run in para.runs:
                    run.text = ""

            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if is_header else WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after  = Pt(4)

            r = p.add_run(cell_text)
            r.font.name      = FONT_MAIN
            r.font.size      = Pt(11)
            r.font.bold      = is_header
            r.font.color.rgb = WHITE if is_header else BLACK

    doc.add_paragraph().paragraph_format.space_after = Pt(6)
