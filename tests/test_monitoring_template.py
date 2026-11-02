import os

import pytest
from docx import Document

from handlers.word_generator import generate_word


@pytest.mark.parametrize(
    ("lang", "title_text", "first_header"),
    [
        ("kz", "2026–2027 оқу жылындағы Баланың жеке даму картасы", "Құзыреттіліктер"),
        ("ru", "Индивидуальная карта развития ребёнка на 2026–2027 учебный год", "Компетентности"),
    ],
)
def test_monitoring_template_keeps_sample_structure_without_invented_child_data(
    lang, title_text, first_header
):
    path = generate_word(
        "",
        "Мониторинг развития",
        monitoring_data={"lang": lang, "rows": []},
        registry_doc_type="development_monitoring",
        lang=lang,
    )
    try:
        doc = Document(path)
        full_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        table = doc.tables[0]

        assert title_text in full_text
        assert len(table.rows) == 7
        assert len(table.columns) == 5
        assert table.rows[0].cells[0].text == first_header
        if lang == "ru":
            assert "первичного мониторинга (октябрь — декабрь)" in table.rows[0].cells[1].text
            assert "промежуточного мониторинга (февраль — апрель)" in table.rows[0].cells[2].text
            assert "итогового мониторинга (июнь — август)" in table.rows[0].cells[3].text
        assert "Алтаев" not in full_text
        assert "Жұлдызша" not in full_text
        if lang == "ru":
            assert "Год рождения: ________________" in full_text
            assert "Возраст ребёнка: ________________" in full_text
            assert "Группа: ________________" in full_text
        else:
            assert "Туған жылы: ________________" in full_text
            assert "Баланың жасы: ________________" in full_text
            assert "Тобы: ________________" in full_text
        assert all(not table.rows[row].cells[column].text.strip()
                   for row in range(1, 6) for column in range(1, 5))
        assert "________________" in full_text
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_monitoring_template_normalizes_slash_school_year():
    path = generate_word(
        "",
        "Мониторинг развития",
        monitoring_data={"lang": "ru", "period": "2026/2027", "rows": []},
        registry_doc_type="development_monitoring",
    )
    try:
        doc = Document(path)
        assert "2026–2027 учебный год" in doc.paragraphs[0].text
    finally:
        if os.path.exists(path):
            os.remove(path)
