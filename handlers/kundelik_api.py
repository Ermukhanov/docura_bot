"""
Kundelik.kz & BilimClass API Integration Client for Docura.kz.

Features:
- Live REST API client for Kundelik.kz (https://api.kundelik.kz/v1/) and BilimClass.
- Fallback Sandbox / Mock mode for demo tokens (e.g. demo_kundelik_token, demo_bilim_token)
  enabling fully realistic presentation and testing without requiring live school VPN/tokens.
- Syncing:
  * Teacher profile & school details
  * Classes / edu-groups
  * Students roster
  * Schedule / lessons for the week
  * Marks / Formative assessments (ФО 1-10, БЖБ/СОР, ТЖБ/СОЧ)
- Posting Marks & Criteria Descriptors directly from Docura bot/web.
- Access strictly restricted to MAX Tier users (тариф MAX: 7 490 ₸/мес).
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional
import aiohttp

logger = logging.getLogger(__name__)

KUNDELIK_API_BASE = os.getenv("KUNDELIK_API_BASE", "https://api.kundelik.kz/v1")
BILIMCLASS_API_BASE = os.getenv("BILIMCLASS_API_BASE", "https://api.bilimclass.kz/v1")

# Realistic Sandbox Mock Data for testing and demonstrations
MOCK_KUNDELIK_DATA = {
    "profile": {
        "person_id": 982341,
        "first_name": "Мернар",
        "last_name": "Ермуханов",
        "middle_name": "Серикович",
        "roles": ["Teacher"],
        "schools": [{"id": 100245, "name": "Школа-гимназия №6 г. Хромтау", "type": "school"}]
    },
    "classes": [
        {"id": 401, "name": "7 «А»", "subject": "Алгебра", "students_count": 24},
        {"id": 402, "name": "7 «Б»", "subject": "Алгебра", "students_count": 22},
        {"id": 501, "name": "8 «А»", "subject": "Геометрия", "students_count": 25},
        {"id": 502, "name": "9 «В»", "subject": "Алгебра", "students_count": 20}
    ],
    "students": {
        401: [
            {"id": 1001, "name": "Аманжолов Арман", "class_name": "7 «А»", "avg_mark": 8.7, "absences": 1, "recent_marks": [8, 9, 9, 10]},
            {"id": 1002, "name": "Берік Аружан", "class_name": "7 «А»", "avg_mark": 9.4, "absences": 0, "recent_marks": [9, 10, 10, 9]},
            {"id": 1003, "name": "Данияров Дамир", "class_name": "7 «А»", "avg_mark": 6.8, "absences": 3, "recent_marks": [7, 6, 7, 7]},
            {"id": 1004, "name": "Жұмағали Дильназ", "class_name": "7 «А»", "avg_mark": 9.8, "absences": 0, "recent_marks": [10, 10, 9, 10]},
            {"id": 1005, "name": "Ибрагимов Санжар", "class_name": "7 «А»", "avg_mark": 7.5, "absences": 2, "recent_marks": [8, 7, 7, 8]},
            {"id": 1006, "name": "Қалиева Айзере", "class_name": "7 «А»", "avg_mark": 8.9, "absences": 1, "recent_marks": [9, 9, 8, 9]},
            {"id": 1007, "name": "Нұрланұлы Әли", "class_name": "7 «А»", "avg_mark": 8.2, "absences": 1, "recent_marks": [8, 8, 9, 8]}
        ],
        402: [
            {"id": 1011, "name": "Әлімхан Нұрислам", "class_name": "7 «Б»", "avg_mark": 8.0, "absences": 2, "recent_marks": [8, 7, 8, 9]},
            {"id": 1012, "name": "Бақытжанова Амина", "class_name": "7 «Б»", "avg_mark": 9.1, "absences": 0, "recent_marks": [9, 9, 9, 10]},
            {"id": 1013, "name": "Еркінұлы Батыр", "class_name": "7 «Б»", "avg_mark": 7.2, "absences": 4, "recent_marks": [7, 7, 6, 8]}
        ],
        501: [
            {"id": 1021, "name": "Асқаров Мансұр", "class_name": "8 «А»", "avg_mark": 9.0, "absences": 1, "recent_marks": [9, 9, 10, 8]},
            {"id": 1022, "name": "Қайрат Мадина", "class_name": "8 «А»", "avg_mark": 8.5, "absences": 0, "recent_marks": [8, 9, 8, 9]}
        ],
        502: [
            {"id": 1031, "name": "Сапарбай Ернар", "class_name": "9 «В»", "avg_mark": 8.4, "absences": 2, "recent_marks": [8, 8, 9, 8]},
            {"id": 1032, "name": "Төлеген Диана", "class_name": "9 «В»", "avg_mark": 9.6, "absences": 0, "recent_marks": [10, 9, 10, 10]}
        ]
    },
    "schedule": [
        {"day": "Понедельник", "time": "08:30 - 09:15", "subject": "Алгебра", "class_name": "7 «А»", "room": "Каб. 204", "topic": "Линейные уравнения с одной переменной"},
        {"day": "Понедельник", "time": "09:25 - 10:10", "subject": "Алгебра", "class_name": "7 «Б»", "room": "Каб. 204", "topic": "Решение текстовых задач с помощью уравнений"},
        {"day": "Понедельник", "time": "10:30 - 11:15", "subject": "Геометрия", "class_name": "8 «А»", "room": "Каб. 204", "topic": "Теорема Пифагора и её применение"},
        {"day": "Вторник", "time": "08:30 - 09:15", "subject": "Алгебра", "class_name": "9 «В»", "room": "Каб. 204", "topic": "Квадратичная функция и её график"},
        {"day": "Вторник", "time": "09:25 - 10:10", "subject": "Алгебра", "class_name": "7 «А»", "room": "Каб. 204", "topic": "Системы линейных уравнений"},
        {"day": "Среда", "time": "08:30 - 09:15", "subject": "Геометрия", "class_name": "8 «А»", "room": "Каб. 204", "topic": "Площадь треугольника и параллелограмма"},
        {"day": "Среда", "time": "09:25 - 10:10", "subject": "Алгебра", "class_name": "7 «Б»", "room": "Каб. 204", "topic": "Самостоятельная работа: уравнения"},
        {"day": "Четверг", "time": "08:30 - 09:15", "subject": "Алгебра", "class_name": "9 «В»", "room": "Каб. 204", "topic": "Свойства квадратичной функции"},
        {"day": "Четверг", "time": "09:25 - 10:10", "subject": "Алгебра", "class_name": "7 «А»", "room": "Каб. 204", "topic": "Формативное оценивание по теме"},
        {"day": "Пятница", "time": "08:30 - 09:15", "subject": "Геометрия", "class_name": "8 «А»", "room": "Каб. 204", "topic": "Решение геометрических задач"},
        {"day": "Пятница", "time": "09:25 - 10:10", "subject": "Алгебра", "class_name": "7 «Б»", "room": "Каб. 204", "topic": "Итоговое повторение главы"}
    ]
}


class KundelikClient:
    def __init__(self, token: str, provider: str = "kundelik"):
        self.token = (token or "").strip()
        self.provider = provider.lower()
        self.is_mock = (
            not self.token or
            self.token.startswith("demo_") or
            "demo" in self.token.lower() or
            self.token == "test" or
            len(self.token) < 15
        )
        self.base_url = KUNDELIK_API_BASE if self.provider == "kundelik" else BILIMCLASS_API_BASE

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Access-Token": self.token,
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    async def get_profile(self) -> Dict[str, Any]:
        """Получение профиля учителя и информации о школе."""
        if self.is_mock:
            return {
                "ok": True,
                "provider": self.provider,
                "data": MOCK_KUNDELIK_DATA["profile"]
            }

        url = f"{self.base_url}/users/me"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"ok": True, "provider": self.provider, "data": data}
                    else:
                        text = await resp.text()
                        logger.warning("Kundelik API get_profile status %d: %s", resp.status, text[:200])
                        return {"ok": False, "error": f"HTTP {resp.status}", "details": text}
        except Exception as e:
            logger.exception("Kundelik get_profile connection error")
            return {"ok": False, "error": str(e)}

    async def get_classes(self) -> List[Dict[str, Any]]:
        """Получение списка классов учителя."""
        if self.is_mock:
            return MOCK_KUNDELIK_DATA["classes"]

        url = f"{self.base_url}/edu-groups"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Форматируем в единообразный список
                        res = []
                        for grp in data if isinstance(data, list) else data.get("groups", []):
                            res.append({
                                "id": grp.get("id"),
                                "name": grp.get("name") or grp.get("title", ""),
                                "subject": grp.get("subject", ""),
                                "students_count": grp.get("students_count", 0)
                            })
                        return res
                    return []
        except Exception as e:
            logger.warning("Kundelik get_classes error: %s", e)
            return []

    async def get_students_for_class(self, class_id: int) -> List[Dict[str, Any]]:
        """Получение списка учеников конкретного класса."""
        if self.is_mock:
            return MOCK_KUNDELIK_DATA["students"].get(class_id, MOCK_KUNDELIK_DATA["students"].get(401, []))

        url = f"{self.base_url}/edu-groups/{class_id}/students"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        res = []
                        for st in data if isinstance(data, list) else data.get("students", []):
                            res.append({
                                "id": st.get("id") or st.get("person_id"),
                                "name": st.get("name") or f"{st.get('last_name', '')} {st.get('first_name', '')}".strip(),
                                "class_name": st.get("class_name", ""),
                                "avg_mark": st.get("avg_mark", 8.5),
                                "absences": st.get("absences", 0),
                                "recent_marks": st.get("recent_marks", [])
                            })
                        return res
                    return []
        except Exception as e:
            logger.warning("Kundelik get_students error: %s", e)
            return []

    async def get_schedule(self) -> List[Dict[str, Any]]:
        """Получение расписания уроков на неделю."""
        if self.is_mock:
            return MOCK_KUNDELIK_DATA["schedule"]

        url = f"{self.base_url}/users/me/schedules"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data if isinstance(data, list) else data.get("schedules", [])
                    return []
        except Exception as e:
            logger.warning("Kundelik get_schedule error: %s", e)
            return []

    async def post_mark(self, student_id: int, student_name: str, class_name: str,
                          mark_val: int, mark_type: str = "ФО",
                          descriptor: str = "", topic: str = "") -> Dict[str, Any]:
        """
        Выставление формативной/суммативной оценки или дескриптора в журнал.
        mark_val: 1-10 (для 10-балльной шкалы РК) или 2-5
        mark_type: "ФО" (Формативное), "БЖБ" (СОР), "ТЖБ" (СОЧ)
        """
        if self.is_mock:
            # Для демо и песочницы сразу возвращаем успешное подтверждение
            return {
                "ok": True,
                "posted": True,
                "student_id": student_id,
                "student_name": student_name,
                "mark": mark_val,
                "mark_type": mark_type,
                "descriptor": descriptor or "Жарайсың! Тақырыпты жақсы меңгердің.",
                "message": f"Оценка {mark_val} ({mark_type}) успешно записана в {self.provider.capitalize()}!"
            }

        url = f"{self.base_url}/marks"
        payload = {
            "person": student_id,
            "value": mark_val,
            "type": mark_type,
            "comment": descriptor,
            "topic": topic
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._get_headers(), json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        return {"ok": True, "posted": True, "data": data}
                    else:
                        text = await resp.text()
                        logger.warning("Kundelik post_mark failed HTTP %d: %s", resp.status, text[:200])
                        return {"ok": False, "error": f"HTTP {resp.status}", "details": text}
        except Exception as e:
            logger.exception("Kundelik post_mark exception")
            return {"ok": False, "error": str(e)}


# Предопределённые методические дескрипторы для формативного оценивания (ФО)
CRITERIA_DESCRIPTORS_RU = {
    10: "Отличный результат! Все задания выполнены верно, применены рациональные способы решения.",
    9: "Очень хорошо! Тема усвоена, допущена незначительная вычислительная неточность.",
    8: "Хорошая работа! Понимает алгоритм решения, требуется больше внимания к оформлению.",
    7: "Тема усвоена в целом. Рекомендуется повторить ключевые формулы и определения.",
    6: "Удовлетворительно. Допускает ошибки в базовых алгоритмах, требуется консультация.",
    5: "Требуется помощь учителя. Не все базовые понятия усвоены.",
    4: "Низкий уровень. Необходимо повторить теоретический материал раздела.",
    3: "Тема не освоена. Задания не выполнены.",
}

CRITERIA_DESCRIPTORS_KZ = {
    10: "Керемет нәтиже! Барлық тапсырмалар дұрыс орындалды, тиімді тәсілдер қолданылды.",
    9: "Өте жақсы! Тақырып меңгерілді, шағын есептеу дәлсіздігі бар.",
    8: "Жақсы жұмыс! Есептеу алгоритмін біледі, ресімдеуге назар аудару қажет.",
    7: "Тақырып негізінен меңгерілді. Негізгі формулалар мен ережелерді қайталау ұсынылады.",
    6: "Қанағаттанарлық. Негізгі алгоритмдерде қателіктер бар, қосымша кеңес қажет.",
    5: "Мұғалімнің көмегі қажет. Негізгі ұғымдар толық меңгерілмеген.",
    4: "Төмен деңгей. Бөлімнің теориялық материалын қайталау қажет.",
    3: "Тақырып меңгерілмеді. Тапсырмалар орындалмады.",
}
