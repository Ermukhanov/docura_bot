"""
Docura.kz — надёжный PDF генератор
Работает на Windows/Linux/Mac без скачивания шрифтов
"""
import os
import re
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Цвета ────────────────────────────────────────────
NAVY       = colors.HexColor("#1A2E5A")
BLUE       = colors.HexColor("#2E75B6")
LIGHT_BLUE = colors.HexColor("#EBF3FB")
GRAY       = colors.HexColor("#4A5568")
LIGHT_GRAY = colors.HexColor("#A0AEC0")
WHITE      = colors.white
BLACK      = colors.HexColor("#1A202C")
ROW_ALT    = colors.HexColor("#F7FAFC")

def _find_cyrillic_font():
    """
    Ищет шрифт с кириллицей.
    Возвращает (regular_path, bold_path, italic_path) или None
    """
    candidates = []

    # Windows
    win = "C:/Windows/Fonts/"
    candidates += [
        (win+"times.ttf",   win+"timesbd.ttf",   win+"timesi.ttf"),
        (win+"arial.ttf",   win+"arialbd.ttf",   win+"ariali.ttf"),
        (win+"calibri.ttf", win+"calibrib.ttf",  win+"calibrii.ttf"),
        (win+"tahoma.ttf",  win+"tahomabd.ttf",  win+"tahoma.ttf"),
        (win+"verdana.ttf", win+"verdanab.ttf",  win+"verdanai.ttf"),
    ]

    # Linux/Mac
    linux_paths = [
        "/usr/share/fonts/truetype/dejavu/",
        "/usr/share/fonts/truetype/liberation/",
        "/usr/share/fonts/truetype/freefont/",
        "/usr/share/fonts/truetype/ubuntu/",
        "/System/Library/Fonts/",
    ]
    linux_names = [
        ("DejaVuSerif.ttf",       "DejaVuSerif-Bold.ttf",     "DejaVuSerif-Italic.ttf"),
        ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf"),
        ("FreeSerif.ttf",         "FreeSerifBold.ttf",        "FreeSerifItalic.ttf"),
        ("Ubuntu-R.ttf",          "Ubuntu-B.ttf",             "Ubuntu-RI.ttf"),
    ]
    for base in linux_paths:
        for r, b, i in linux_names:
            candidates.append((base+r, base+b, base+i))

    for r, b, i in candidates:
        if os.path.exists(r) and os.path.exists(b):
            italic = i if os.path.exists(i) else r
            return r, b, italic

    return None


_font_name = None

def _register_fonts():
    global _font_name
    if _font_name:
        return _font_name

    paths = _find_cyrillic_font()
    if paths:
        try:
            r, b, i = paths
            pdfmetrics.registerFont(TTFont("DocFont",        r))
            pdfmetrics.registerFont(TTFont("DocFont-Bold",   b))
            pdfmetrics.registerFont(TTFont("DocFont-Italic", i))
            pdfmetrics.registerFontFamily("DocFont",
                normal="DocFont", bold="DocFont-Bold", italic="DocFont-Italic")
            _font_name = "DocFont"
            print(f"✅ Шрифт загружен: {r}")
            return "DocFont"
        except Exception as e:
            print(f"Шрифт ошибка: {e}")

    # Фолбэк — встроенный Helvetica (без кириллицы но не упадёт)
    _font_name = "Helvetica"
    print("⚠️  Кириллический шрифт не найден — используем Helvetica")
    return "Helvetica"


def _make_styles(F):
    FB = F + "-Bold"   if F != "Helvetica" else "Helvetica-Bold"
    FI = F + "-Italic" if F != "Helvetica" else "Helvetica-Oblique"
    return {
        "brand":    ParagraphStyle("brand",    fontName=FB, fontSize=9,
                                   textColor=WHITE, alignment=TA_CENTER, leading=13),
        "title":    ParagraphStyle("title",    fontName=FB, fontSize=15,
                                   textColor=NAVY, alignment=TA_CENTER,
                                   leading=20, spaceBefore=4, spaceAfter=4),
        "section":  ParagraphStyle("section",  fontName=FB, fontSize=12,
                                   textColor=NAVY, leading=16, spaceBefore=8, spaceAfter=4),
        "subheader":ParagraphStyle("subheader",fontName=FB, fontSize=11,
                                   textColor=GRAY, leading=15, spaceBefore=5, spaceAfter=3),
        "body":     ParagraphStyle("body",     fontName=F,  fontSize=11,
                                   textColor=BLACK, leading=17,
                                   alignment=TA_JUSTIFY,
                                   firstLineIndent=1.2*cm, spaceAfter=3),
        "bullet":   ParagraphStyle("bullet",   fontName=F,  fontSize=11,
                                   textColor=BLACK, leading=16,
                                   leftIndent=1.2*cm, firstLineIndent=-0.5*cm,
                                   spaceAfter=2),
        "signature":ParagraphStyle("signature",fontName=F,  fontSize=11,
                                   textColor=GRAY, leading=16, spaceAfter=3),
        "footer":   ParagraphStyle("footer",   fontName=FI, fontSize=8,
                                   textColor=LIGHT_GRAY, alignment=TA_CENTER, leading=10),
        "th":       ParagraphStyle("th",       fontName=FB, fontSize=10,
                                   textColor=WHITE, alignment=TA_CENTER, leading=13),
        "td":       ParagraphStyle("td",       fontName=F,  fontSize=10,
                                   textColor=BLACK, leading=13),
    }


def _parse_to_elements(content, styles):
    elements = []
    lines = content.split("\n")

    def is_section(s):
        if not s or len(s) < 3: return False
        if re.match(r'^(\d+\.|[IVX]+\.)\s+', s): return True
        # Полностью заглавные
        no_punct = re.sub(r'[^a-zA-Zа-яёА-ЯЁіІқҚғҒүҮұҰіәӘһҺ]', '', s)
        return len(no_punct) > 2 and no_punct == no_punct.upper() and no_punct.upper() != no_punct.lower()

    def is_sub(s):
        return (s.endswith(":") and 4 < len(s) < 80
                and not s.startswith(("-","•","*","–")))

    def is_bullet(s):
        return s.startswith(("- ","• ","* ","– ","· "))

    def is_sig(s):
        kw = ["подпись","директор","учитель","кл.рук","дата:","м.п.",
              "классный руководитель","қолы","мұғалім","күні:",
              "мектеп директоры","/__","/ ___"]
        sl = s.lower()
        return any(k in sl for k in kw)

    def is_table(s):
        return s.count("|") >= 2

    prev_empty = False
    i = 0
    while i < len(lines):
        s = lines[i].strip()

        if not s:
            if not prev_empty:
                elements.append(Spacer(1, 5))
            prev_empty = True
            i += 1
            continue
        prev_empty = False

        # Таблица
        if is_table(s):
            tbl = []
            while i < len(lines) and (is_table(lines[i]) or
                  re.match(r'^\s*\|[\s\-:]+\|', lines[i])):
                raw = lines[i].strip()
                if not re.match(r'^\|[\s\-:]+\|', raw):
                    tbl.append(raw)
                i += 1
            if tbl:
                el = _make_table(tbl, styles)
                if el:
                    elements.append(el)
                    elements.append(Spacer(1, 6))
            continue

        # Заголовок раздела — синяя плашка
        if is_section(s):
            data = [[Paragraph(s, styles["section"])]]
            t = Table(data, colWidths=[16.5*cm])
            t.setStyle(TableStyle([
                ("BACKGROUND",    (0,0),(-1,-1), LIGHT_BLUE),
                ("LINEBEFORE",    (0,0),(0,-1),  4, BLUE),
                ("LEFTPADDING",   (0,0),(-1,-1), 10),
                ("RIGHTPADDING",  (0,0),(-1,-1), 6),
                ("TOPPADDING",    (0,0),(-1,-1), 6),
                ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ]))
            elements.append(Spacer(1, 6))
            elements.append(t)
            elements.append(Spacer(1, 4))
            i += 1
            continue

        if is_sub(s):
            elements.append(Paragraph(s, styles["subheader"]))
            i += 1
            continue

        if is_bullet(s):
            text = re.sub(r'^[-•*–·]\s+', '', s)
            elements.append(Paragraph(f"▸  {text}", styles["bullet"]))
            i += 1
            continue

        if is_sig(s):
            elements.append(Paragraph(s, styles["signature"]))
            i += 1
            continue

        elements.append(Paragraph(s, styles["body"]))
        i += 1

    return elements


def _make_table(lines, styles):
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return None
    max_c = max(len(r) for r in rows)
    rows  = [r + [""] * (max_c - len(r)) for r in rows]
    col_w = 16.5 * cm / max_c
    content = []
    for ri, row in enumerate(rows):
        st = styles["th"] if ri == 0 else styles["td"]
        content.append([Paragraph(c or "", st) for c in row])
    t = Table(content, colWidths=[col_w] * max_c, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1, 0), NAVY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, ROW_ALT]),
        ("GRID",          (0,0),(-1,-1), 0.5, colors.HexColor("#C8D8E8")),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ("RIGHTPADDING",  (0,0),(-1,-1), 6),
    ]))
    return t


def generate_pdf(content: str, title: str, teacher_name: str = "") -> str:
    """Главная функция — генерирует PDF и возвращает путь к файлу"""
    F      = _register_fonts()
    styles = _make_styles(F)
    fname  = f"/tmp/docura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    doc = SimpleDocTemplate(
        fname, pagesize=A4,
        leftMargin=3*cm, rightMargin=1.5*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title=title, author="Docura.kz"
    )

    story = []

    # Шапка
    hdr = Table(
        [[Paragraph("DOCURA.KZ  •  Профессиональные документы для учителей Казахстана",
                    styles["brand"])]],
        colWidths=[16.5*cm]
    )
    hdr.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 9),
        ("BOTTOMPADDING", (0,0),(-1,-1), 9),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 14))

    # Заголовок
    story.append(Paragraph(title.upper(), styles["title"]))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))
    story.append(Spacer(1, 4))

    # Контент
    story.extend(_parse_to_elements(content, styles))

    # Подвал
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GRAY, spaceAfter=5))
    story.append(Paragraph(
        f"Сгенерировано Docura.kz  •  {datetime.now().strftime('%d.%m.%Y %H:%M')}  •  t.me/docurakz_bot",
        styles["footer"]
    ))

    doc.build(story)
    return fname
