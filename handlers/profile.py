import json
import base64
import hashlib
import re
from urllib.parse import quote
import anthropic
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database, free_limit_for

MENU_BTN   = lambda lang: InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")
BACK_BTN   = lambda lang, cb: InlineKeyboardButton("◀️ " + ("Назад" if lang == "ru" else "Артқа"), callback_data=cb)
CANCEL_BTN = lambda lang: InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="menu_main")

KASPI_NUMBER = "+7 771 451 4717"

# Только один платный тариф — Docura PRO.
# pro_promo — та же самая подписка PRO, просто первый месяц дешевле (одноразово на аккаунт).
# ВАЖНО: обе "цены" идут на один и тот же продукт (tier="pro" в БД) — pro/pro_promo
# различаются только суммой оплаты и тем, что pro_promo можно использовать один раз.
TIER_PRICES = {"pro": 4990, "pro_promo": 2490}
TIER_NAMES  = {"pro": "PRO"}

def _esc(text):
    if text is None: return "—"
    text = str(text)
    for ch in ["_", "*", "[", "]", "`"]:
        text = text.replace(ch, "\\" + ch)
    return text

class ProfileHandler:
    def __init__(self, db: Database, api_key: str = ""):
        self.db      = db
        self.api_key = api_key

    async def show(self, query, user_id, lang):
        user = await self.db.get_user(user_id)
        if not user:
            return

        is_kg = user.get("role") == "kindergarten"

        name     = user.get("name", "—")
        school   = user.get("school", "—")
        position = user.get("position", "—")
        director = user.get("director", "—")
        is_pro   = user.get("subscribed", 0)
        free_used = user.get("free_used", 0)
        total_free = free_limit_for(user)
        free_left = max(0, total_free - free_used)
        bonus_docs = user.get("bonus_docs", 0) or 0
        saved_hours = free_used * 0.5
        saved_hours_text = f"{saved_hours:g}"

        if is_pro:
            sub_line = "⭐ *PRO* — безлимитный доступ активен" if lang == "ru" else "⭐ *PRO* — шексіз қол жеткізу белсенді"
        else:
            sub_line = f"🆓 Бесплатно — осталось *{free_left}/{total_free}* документов" if lang == "ru" else f"🆓 Тегін — қалды *{free_left}/{total_free}* құжат"
            if bonus_docs:
                sub_line += (
                    f"\n🎁 Из них {bonus_docs} бонусных за приглашённых друзей" if lang == "ru"
                    else f"\n🎁 Оның ішінде {bonus_docs} шақырылған достар үшін бонус"
                )

        if is_kg:
            age_group = user.get("age_group", "—")
            if lang == "ru":
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МОЙ ПРОФИЛЬ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *ФИО:* {name}\n"
                    f"🏫 *Детский сад:* {school}\n"
                    f"👶 *Возрастная группа:* {age_group}\n"
                    f"💼 *Должность:* {position}\n"
                    f"👔 *Заведующая:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Создано документов: *{free_used}*\n"
                    f"⏱ Сэкономлено времени: примерно *{saved_hours_text} ч*"
                )
            else:
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МЕНІҢ ПРОФИЛІМ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *Аты-жөні:* {name}\n"
                    f"🏫 *Балабақша:* {school}\n"
                    f"👶 *Жас тобы:* {age_group}\n"
                    f"💼 *Лауазым:* {position}\n"
                    f"👔 *Меңгеруші:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Жасалған құжаттар: *{free_used}*\n"
                    f"⏱ Үнемделген уақыт: шамамен *{saved_hours_text} сағ*"
                )
        else:
            subject  = user.get("subject", "—")
            classes  = user.get("classes", "—")
            is_ct    = "✅" if user.get("is_class_teacher") else "❌"
            if lang == "ru":
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МОЙ ПРОФИЛЬ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *ФИО:* {name}\n"
                    f"🏫 *Школа:* {school}\n"
                    f"📚 *Предмет:* {subject}\n"
                    f"🏷 *Классы:* {classes}\n"
                    f"💼 *Должность:* {position}\n"
                    f"👔 *Директор:* {director}\n"
                    f"🏫 *Кл.рук:* {is_ct}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Создано документов: *{free_used}*\n"
                    f"⏱ Сэкономлено времени: примерно *{saved_hours_text} ч*"
                )
            else:
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МЕНІҢ ПРОФИЛІМ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *Аты-жөні:* {name}\n"
                    f"🏫 *Мектеп:* {school}\n"
                    f"📚 *Пән:* {subject}\n"
                    f"🏷 *Сыныптар:* {classes}\n"
                    f"💼 *Лауазым:* {position}\n"
                    f"👔 *Директор:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Жасалған құжаттар: *{free_used}*\n"
                    f"⏱ Үнемделген уақыт: шамамен *{saved_hours_text} сағ*"
                )

        students_btn_text = t(lang, "btn_my_children") if is_kg else t(lang, "btn_my_students")

        keyboard = [
            [InlineKeyboardButton("✏️ " + t(lang, "btn_edit_profile"), callback_data="prof_edit")],
            [InlineKeyboardButton("👥 " + students_btn_text,           callback_data="prof_students")],
            [InlineKeyboardButton("📅 " + ("Моё расписание" if not is_kg and lang == "ru" else "Мой режим дня" if is_kg and lang == "ru" else "Менің кестем"), callback_data="agent_schedule")],
            [InlineKeyboardButton("🔔 " + ("Напоминания" if lang == "ru" else "Еске салғыштар"), callback_data="agent_reminders")],
            [InlineKeyboardButton("⭐ " + t(lang, "btn_subscription"),  callback_data="prof_sub")],
            [InlineKeyboardButton("🌐 " + t(lang, "btn_change_lang"),   callback_data="prof_lang")],
            [InlineKeyboardButton("✉️ " + ("Жалоба или отзыв" if lang == "ru" else "Шағым немесе пікір"), callback_data="prof_complaint")],
            [MENU_BTN(lang)],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query   = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"

        if data in ("prof_students", "prof_add_student", "prof_upload_students") and (not user or not user.get("subscribed")):
            await query.edit_message_text(
                "Эта функция доступна в PRO. В PRO можно хранить группы и детей/учеников и использовать их в документах."
                if lang == "ru" else
                "Бұл функция PRO тарифінде қолжетімді. PRO ішінде топтар мен балаларды/оқушыларды сақтап, құжаттарда пайдалануға болады.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐ Посмотреть PRO" if lang == "ru" else "⭐ PRO көру", callback_data="prof_sub")],
                    [InlineKeyboardButton("Пока не нужно" if lang == "ru" else "Қазір керек емес", callback_data="menu_main")],
                ])
            )
            return

        if data in ("agent_schedule", "agent_reminders"):
            from handlers.agent import AgentHandler
            await AgentHandler(self.db, self.api_key).callback(update, context)
            return

        if data == "prof_edit":
            context.user_data["step"] = "prof_edit_name"
            kb = [[CANCEL_BTN(lang)]]
            await query.edit_message_text(
                ("✏️ *Редактирование профиля*\n\nВведите ваше ФИО:" if lang == "ru"
                 else "✏️ *Профильді өңдеу*\n\nАты-жөніңізді енгізіңіз:"),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "prof_complaint":
            context.user_data["step"] = "prof_complaint"
            await query.edit_message_text(
                ("✉️ Напишите жалобу, отзыв или опишите ошибку одним сообщением.\n\nПосле отправки появится кнопка для письма на docurakz@gmail.com."
                 if lang == "ru" else
                 "✉️ Шағымды, пікірді немесе қатені бір хабарламада жазыңыз.\n\nЖібергеннен кейін docurakz@gmail.com поштасына арналған батырма шығады."),
                reply_markup=InlineKeyboardMarkup([[CANCEL_BTN(lang)]])
            )

        elif data == "prof_students":
            await self._show_students(query, user_id, lang, is_kg)

        elif data == "prof_sub":
            await self._show_subscription(query, user, lang)

        elif data in ("prof_choose_pro", "prof_choose_pro_promo"):
            tier = "pro_promo" if data == "prof_choose_pro_promo" else "pro"

            # Акцию можно активировать только один раз за всю историю аккаунта
            if tier == "pro_promo" and user.get("promo_used"):
                await self._show_subscription(query, user, lang)
                return

            context.user_data["chosen_tier"] = tier
            context.user_data["step"] = "waiting_payment_receipt"
            price = TIER_PRICES[tier]

            promo_line_ru = f"\n🔥 Обычная цена {TIER_PRICES['pro']} тг — для вас первый месяц дешевле!\n" if tier == "pro_promo" else ""
            promo_line_kz = f"\n🔥 Әдеттегі баға {TIER_PRICES['pro']} тг — сізге бірінші ай арзанырақ!\n" if tier == "pro_promo" else ""

            text = (
                f"💳 *Docura PRO — {price} тг/мес*\n"
                f"{promo_line_ru}\n"
                f"Переведите *{price} тг* на Kaspi:\n"
                f"`{KASPI_NUMBER}`\n"
                f"_(нажмите чтобы скопировать)_\n\n"
                f"Затем пришлите *скриншот или файл чека* — подписка активируется автоматически ✅\n\n"
                f"_Бот сам проверит сумму и получателя_"
            ) if lang == "ru" else (
                f"💳 *Docura PRO — {price} тг/ай*\n"
                f"{promo_line_kz}\n"
                f"*{price} тг* осы нөмірге аударыңыз:\n"
                f"`{KASPI_NUMBER}`\n\n"
                f"Чек скриншотын немесе файлын жіберіңіз — жазылым автоматты белсендіріледі ✅"
            )
            kb = [[BACK_BTN(lang, "prof_sub")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)



        elif data == "prof_lang":
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="prof_set_lang_ru"),
                 InlineKeyboardButton("🇰🇿 Қазақша", callback_data="prof_set_lang_kz")],
                [MENU_BTN(lang)],
            ]
            text = "🌐 Выберите язык интерфейса:" if lang == "ru" else "🌐 Интерфейс тілін таңдаңыз:"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("prof_set_lang_"):
            new_lang = data.split("_")[-1]
            await self.db.upsert_user(user_id, {"lang": new_lang})
            kb = [[MENU_BTN(new_lang)]]
            await query.edit_message_text(
                "✅ Язык изменён на Русский!" if new_lang == "ru" else "✅ Тіл Қазақшаға өзгертілді!",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data == "prof_add_student":
            context.user_data["step"] = "student_name"
            context.user_data["new_student"] = {}
            kb = [[CANCEL_BTN(lang)]]
            name_q = (
                ("👤 *Добавление воспитанника*\n\nВведите полное имя ребёнка:" if is_kg else
                 "👤 *Добавление ученика*\n\nВведите полное имя ученика:")
                if lang == "ru" else
                ("👤 *Тәрбиеленуші қосу*\n\nБаланың толық атын енгізіңіз:" if is_kg else
                 "👤 *Оқушы қосу*\n\nОқушының толық атын енгізіңіз:")
            )
            await query.edit_message_text(name_q, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "prof_upload_students":
            context.user_data["step"] = "student_bulk_upload"
            await query.edit_message_text(
                ("Отправьте список учеников одним сообщением или фото журнала класса.\n\n"
                 "Формат текста:\n1. Иванов Алмаз, 2010, родитель: Иванов А.С.\n2. Петрова Айгерим, 2011\n\n"
                 "Или просто имена через запятую:\nАлмаз Иванов, Айгерим Петрова, Нурлан Ахметов") if lang == "ru" else
                "Оқушылар тізімін бір хабарламамен немесе сынып журналының суретімен жіберіңіз.",
                reply_markup=InlineKeyboardMarkup([[CANCEL_BTN(lang)]])
            )

        elif data == "prof_bulk_confirm":
            pending = context.user_data.pop("bulk_students", [])
            default_class = user.get("age_group") if is_kg else user.get("classes", "")
            for item in pending:
                data = {
                    "name": item["name"], "class_name": item.get("class_name") or default_class or "—",
                    "grades": {}, "achievements": [], "absences": 0,
                    "behavior": "хорошее" if lang == "ru" else "жақсы",
                }
                await self.db.add_student(user_id, data)
                students = await self.db.get_students(user_id)
                saved = next((student for student in reversed(students) if student["name"] == item["name"]), None)
                if saved:
                    await self.db.update_student(saved["id"], {
                        "birth_date": item.get("birth_date", ""), "parents": item.get("parent_name", ""),
                        "parent_phone": item.get("phone", ""),
                    })
            context.user_data["step"] = None
            await query.edit_message_text(
                (f"✅ Добавлено учеников: {len(pending)}" if lang == "ru" else f"✅ Қосылған оқушылар: {len(pending)}"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 " + ("Мои ученики" if not is_kg and lang == "ru" else "Мои воспитанники" if is_kg and lang == "ru" else "Менің оқушыларым"), callback_data="prof_students")], [MENU_BTN(lang)]])
            )

        elif data == "prof_bulk_cancel":
            context.user_data.pop("bulk_students", None)
            context.user_data["step"] = None
            await self._show_students(query, user_id, lang, is_kg)

        elif data.startswith("student_del_"):
            student_id = int(data[12:])
            await self.db.delete_student(student_id)
            await query.answer("✅ Удалено" if lang == "ru" else "✅ Жойылды", show_alert=False)
            await self._show_students(query, user_id, lang, is_kg)

        elif data.startswith("student_view_"):
            student_id = int(data[13:])
            await self._show_student_detail(query, student_id, lang, is_kg)

        elif data == "prof_back_students":
            await self._show_students(query, user_id, lang, is_kg)

    async def _show_students(self, query, user_id, lang, is_kg=False):
        students = await self.db.get_students(user_id)
        keyboard = []
        add_btn_text = t(lang, "btn_add_child") if is_kg else t(lang, "btn_add_student")
        empty_text = t(lang, "no_children") if is_kg else t(lang, "no_students")
        title = ("👥 *Мои воспитанники*" if is_kg else "👥 *Мои ученики*") if lang == "ru" \
            else ("👥 *Менің тәрбиеленушілерім*" if is_kg else "👥 *Менің оқушыларым*")

        if not students:
            keyboard = [
                [InlineKeyboardButton("➕ " + add_btn_text, callback_data="prof_add_student")],
                [InlineKeyboardButton("📋 " + ("Загрузить список" if lang == "ru" else "Тізімді жүктеу"), callback_data="prof_upload_students")],
                [MENU_BTN(lang)],
            ]
            await query.edit_message_text(
                f"{title}\n\n{empty_text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        text = f"{title} ({len(students)})\n\n"
        for s in students:
            grades = json.loads(s.get("grades", "{}"))
            avg = round(sum(grades.values()) / len(grades), 1) if grades else "—"
            if is_kg:
                text += f"👶 {s['name']} • {s['class_name']}\n"
            else:
                text += f"👤 {s['name']} • {s['class_name']} • ср.балл: {avg}\n"

        for s in students:
            keyboard.append([
                InlineKeyboardButton(f"{'👶' if is_kg else '👤'} {s['name']} ({s['class_name']})", callback_data=f"student_view_{s['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"student_del_{s['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("➕ " + add_btn_text, callback_data="prof_add_student")])
        keyboard.append([InlineKeyboardButton("📋 " + ("Загрузить список" if lang == "ru" else "Тізімді жүктеу"), callback_data="prof_upload_students")])
        keyboard.append([MENU_BTN(lang)])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_student_detail(self, query, student_id, lang, is_kg=False):
        s = await self.db.get_student(student_id)
        if not s:
            return

        grades       = json.loads(s.get("grades", "{}"))
        achievements = json.loads(s.get("achievements", "[]"))
        grades_str   = "\n".join(f"  • {k}: {v}" for k, v in grades.items()) if grades else ("  нет данных" if lang == "ru" else "  деректер жоқ")
        achieve_str  = "\n".join(f"  • {a}" for a in achievements) if achievements else ("  нет" if lang == "ru" else "  жоқ")

        group_label = ("Группа" if lang == "ru" else "Тобы") if is_kg else ("Класс" if lang == "ru" else "Сынып")

        text = (
            f"{'👶' if is_kg else '👤'} *{s['name']}*\n"
            f"🏷 {group_label}: {s['class_name']}\n"
            f"😊 Поведение: {s.get('behavior', '—')}\n"
            f"📅 Пропуски: {s.get('absences', 0)} дн.\n\n"
            f"👪 Родители: {s.get('parents') or '—'}\n"
            f"📞 Телефон: {s.get('parent_phone') or '—'}\n"
            f"🎂 Дата рождения: {s.get('birth_date') or '—'}\n\n"
            f"📊 *{'Достижения' if is_kg else 'Оценки'}:*\n{'' if is_kg else grades_str}\n\n"
            f"🏆 *Достижения:*\n{achieve_str}"
        ) if lang == "ru" else (
            f"{'👶' if is_kg else '👤'} *{s['name']}*\n"
            f"🏷 {group_label}: {s['class_name']}\n"
            f"😊 Мінез-құлық: {s.get('behavior', '—')}\n"
            f"📅 Өткізулер: {s.get('absences', 0)} күн\n\n"
            f"👪 Ата-ана: {s.get('parents') or '—'}\n"
            f"📞 Телефон: {s.get('parent_phone') or '—'}\n"
            f"🎂 Туған күні: {s.get('birth_date') or '—'}\n\n"
            f"🏆 *Жетістіктер:*\n{achieve_str}"
        )

        keyboard = [
            [BACK_BTN(lang, "prof_back_students")],
            [MENU_BTN(lang)],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_subscription(self, query, user, lang):
        tier  = user.get("tier", "free")
        is_kg = user.get("role") == "kindergarten"
        db_line_ru = "✅ База воспитанников" if is_kg else "✅ База учеников"
        db_line_kz = "✅ Тәрбиеленушілер базасы" if is_kg else "✅ Оқушылар базасы"

        if user.get("subscribed") and tier in TIER_NAMES:
            feats = (
                "✅ Безлимитная генерация документов\n"
                "✅ Все типы документов\n"
                f"✅ Голосовой ввод\n{db_line_ru}\n✅ Точечное редактирование"
            ) if lang == "ru" else (
                "✅ Шексіз құжат жасау\n"
                "✅ Барлық құжат түрлері\n"
                f"✅ Дауыстық енгізу\n{db_line_kz}\n✅ Түзету"
            )
            text = (f"⭐ *Docura PRO активна!*\n\n{feats}" if lang == "ru"
                    else f"⭐ *Docura PRO белсенді!*\n\n{feats}")
            keyboard = [[BACK_BTN(lang, "menu_profile")], [MENU_BTN(lang)]]
        else:
            total_free = free_limit_for(user)
            free_left = max(0, total_free - user.get("free_used", 0))
            promo_available = not user.get("promo_used")
            price = TIER_PRICES["pro_promo"] if promo_available else TIER_PRICES["pro"]
            callback = "prof_choose_pro_promo" if promo_available else "prof_choose_pro"

            price_line_ru = (
                f"🔥 *Первый месяц — {price} тг* (вместо {TIER_PRICES['pro']} тг), дальше {TIER_PRICES['pro']} тг/мес\n"
                if promo_available else f"💳 *{price} тг/мес*\n"
            )
            price_line_kz = (
                f"🔥 *Бірінші ай — {price} тг* ({TIER_PRICES['pro']} тг орнына), одан кейін {TIER_PRICES['pro']} тг/ай\n"
                if promo_available else f"💳 *{price} тг/ай*\n"
            )

            text = (
                f"⭐ *Docura PRO*\n\n"
                f"Осталось бесплатных: *{free_left}/{total_free}*\n"
                f"_(пригласи коллегу через /invite и получи +5 документов)_\n\n"
                f"{price_line_ru}"
                f"✅ Безлимитная генерация\n"
                f"✅ Все типы документов\n"
                f"✅ Голосовой ввод\n"
                f"{db_line_ru}\n"
                f"✅ Точечное редактирование\n\n"
                f"👇 После оплаты пришлите чек — подписка активируется автоматически"
            ) if lang == "ru" else (
                f"⭐ *Docura PRO*\n\n"
                f"Тегін қалды: *{free_left}/{total_free}*\n"
                f"_(/invite арқылы әріптесіңізді шақырып, +5 құжат алыңыз)_\n\n"
                f"{price_line_kz}"
                f"✅ Шексіз жасау\n"
                f"✅ Барлық құжат түрлері\n"
                f"✅ Дауыстық енгізу\n"
                f"{db_line_kz}\n\n"
                f"👇 Төлемнен кейін чекті жіберіңіз"
            )
            keyboard = [
                [InlineKeyboardButton(
                    (f"🔥 PRO первый месяц — {price} тг" if promo_available else f"⭐ Оформить PRO — {price} тг")
                    if lang == "ru" else
                    (f"🔥 PRO бірінші ай — {price} тг" if promo_available else f"⭐ PRO рәсімдеу — {price} тг"),
                    callback_data=callback
                )],
                [BACK_BTN(lang, "menu_profile")],
                [MENU_BTN(lang)],
            ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает скриншот чека Kaspi и проверяет его через Claude Vision"""
        if context.user_data.get("step") == "student_bulk_upload":
            await self._parse_students_from_photo(update, context)
            return
        if context.user_data.get("step") == "template_photo":
            # Фото образца может первым попасть в общий обработчик профиля.
            # Передаём его в штатный поток документов, не затрагивая чеки.
            from handlers.documents import DocumentHandler
            await DocumentHandler(self.db, self.api_key).handle_photo(update, context)
            return
        await self._process_receipt(update, context, source="photo")

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает файл чека Kaspi (PDF или изображение) и проверяет через Claude Vision"""
        doc = update.message.document
        if not doc:
            return
        mime = doc.mime_type or ""
        if not any(x in mime for x in ["image", "pdf", "jpeg", "png"]):
            return
        await self._process_receipt(update, context, source="document")

    async def _process_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE, source: str):
        """Основная логика: скачать чек → проверить через Claude → активировать подписку"""
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        tier    = context.user_data.get("chosen_tier", "pro")
        expected_amount = TIER_PRICES.get(tier, 3990)

        wait_msg = await update.message.reply_text(
            "🔍 Проверяю чек..." if lang == "ru" else "🔍 Чек тексерілуде..."
        )

        try:
            if source == "photo":
                file_obj = await update.message.photo[-1].get_file()
                media_type = "image/jpeg"
            else:
                file_obj = await update.message.document.get_file()
                mime = update.message.document.mime_type or "image/jpeg"
                media_type = mime if "pdf" not in mime else "image/jpeg"

            import tempfile, os
            suffix = ".jpg" if "image" in media_type else ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                await file_obj.download_to_drive(tmp.name)
                tmp_path = tmp.name

            with open(tmp_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")

            receipt_hash = hashlib.sha256(img_data.encode()).hexdigest()[:16]
            os.unlink(tmp_path)

            used = await self.db.is_receipt_used(receipt_hash)
            if used:
                await wait_msg.delete()
                await update.message.reply_text(
                    "❌ Этот чек уже был использован для активации." if lang == "ru"
                    else "❌ Бұл чек бұрын қолданылған."
                )
                return

            today = datetime.now().strftime("%d.%m.%Y")
            client = anthropic.Anthropic(api_key=self.api_key)

            # ВАЖНО: сверяем с суммой ИМЕННО того тарифа, который выбрал пользователь
            # (а не с любой из возможных сумм) — иначе акцию 2490 легко перепутать
            # с обычным Базовым тарифом, у которого та же цена.
            prompt = f"""Это чек оплаты Kaspi. Проверь следующее:
1. Получатель содержит номер телефона: {KASPI_NUMBER} (может быть записан без пробелов, с дефисами или скобками — это нормально)
2. Сумма равна {expected_amount} тенге (ровно эта сумма, не другая)
3. Дата операции — сегодня ({today}) или вчера (допустимо)

Ответь ТОЛЬКО в формате JSON без markdown:
{{"valid": true/false, "amount": число_или_null, "reason": "причина если false"}}

Если чек нечёткий или текст плохо читается — valid: false, reason: "нечёткий чек".
Если сумма не совпадает — valid: false, reason: "неверная сумма"."""

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=150,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )

            import re as re_mod
            raw = response.content[0].text.strip()
            raw = re_mod.sub(r"```[a-z]*", "", raw).strip("` \n")
            result = json.loads(raw)

        except Exception as e:
            print(f"Receipt check error: {e}")
            await wait_msg.delete()
            await update.message.reply_text(
                "❌ Не удалось прочитать чек. Пришлите более чёткий скриншот." if lang == "ru"
                else "❌ Чекті оқу мүмкін болмады. Нақтырақ скриншот жіберіңіз."
            )
            return

        await wait_msg.delete()

        # Двойная проверка суммы на нашей стороне — не полагаемся только на ответ модели
        amount_ok = result.get("valid") and int(result.get("amount") or 0) == expected_amount

        if not amount_ok:
            reason_map = {
                "ru": {
                    "нечёткий чек": "Чек нечёткий — сделайте более чёткий скриншот.",
                    "неверная сумма": f"Неверная сумма. Нужно ровно {expected_amount} тг.",
                    "другой получатель": f"Получатель не совпадает. Переводите на {KASPI_NUMBER}.",
                    "старый чек": "Чек устарел. Нужна оплата сегодняшним числом.",
                },
                "kz": {
                    "нечёткий чек": "Чек анық емес — нақтырақ скриншот жіберіңіз.",
                    "неверная сумма": f"Сумма дұрыс емес. Дәл {expected_amount} тг керек.",
                    "другой получатель": f"Алушы сәйкес емес. {KASPI_NUMBER} нөміріне аударыңыз.",
                    "старый чек": "Чек ескірген. Бүгінгі күнмен төлем керек.",
                }
            }
            reason = result.get("reason", "неверная сумма")
            msg_map = reason_map.get(lang, reason_map["ru"])
            friendly = next((v for k, v in msg_map.items() if k in reason.lower()), msg_map["неверная сумма"])
            kb = [[InlineKeyboardButton("💳 Выбрать тариф" if lang == "ru" else "💳 Тариф таңдау", callback_data="prof_sub")]]
            await update.message.reply_text(
                f"❌ Чек не прошёл проверку.\n\n{friendly}" if lang == "ru"
                else f"❌ Чек тексеруден өтпеді.\n\n{friendly}",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return

        amount = result.get("amount", expected_amount)

        await self.db.save_receipt_hash(receipt_hash, user_id, "pro", amount)
        await self.db.activate_subscription(user_id, tier="pro")
        if tier == "pro_promo":
            await self.db.mark_promo_used(user_id)

        context.user_data.pop("chosen_tier", None)
        context.user_data.pop("step", None)

        kb = [[MENU_BTN(lang)]]
        await update.message.reply_text(
            f"🎉 *PRO активирован!*\n\nТеперь у вас безлимитный доступ к Docura.kz. Спасибо за оплату!" if lang == "ru"
            else f"🎉 *PRO белсендірілді!*\n\nEndi Docura.kz-де шексіз қол жеткізу бар. Рахмет!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"
        text    = update.message.text.strip()
        step    = context.user_data.get("step", "")

        cancel_kb = [[CANCEL_BTN(lang)]]

        if step == "prof_complaint":
            context.user_data["step"] = None
            subject = quote("Docura: отзыв или жалоба")
            body = quote(text)
            mail_url = f"mailto:docurakz@gmail.com?subject={subject}&body={body}"
            await update.message.reply_text(
                "✅ Спасибо! Нажмите кнопку ниже, чтобы отправить сообщение." if lang == "ru" else "✅ Рахмет! Хабарламаны жіберу үшін төмендегі батырманы басыңыз.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Отправить на почту" if lang == "ru" else "✉️ Поштаға жіберу", url=mail_url)], [MENU_BTN(lang)]])
            )
            return

        if step == "student_bulk_upload":
            await self._parse_students_from_text(update, context, text)
            return

        # ── Редактирование профиля: САДИК ──
        kg_prof_steps = {
            "prof_edit_name":     ("name",     "prof_edit_school",    ("🏫 Введите название детского сада:" if lang == "ru" else "🏫 Балабақшаның атауын енгізіңіз:")),
            "prof_edit_school":   ("school",   "prof_edit_age_group", ("👶 Введите возрастную группу:\n\n_Пример: старшая группа (5-6 лет)_" if lang == "ru" else "👶 Жас тобын енгізіңіз:")),
            "prof_edit_age_group":("age_group","prof_edit_position",  ("💼 Введите вашу должность:" if lang == "ru" else "💼 Лауазымыңызды енгізіңіз:")),
            "prof_edit_position": ("position", "prof_edit_director",  ("👔 Введите ФИО заведующей:" if lang == "ru" else "👔 Меңгерушінің аты-жөнін енгізіңіз:")),
        }

        # ── Редактирование профиля: ШКОЛА ──
        teacher_prof_steps = {
            "prof_edit_name":     ("name",     "prof_edit_school",   ("🏫 Введите название школы:" if lang == "ru" else "🏫 Мектептің атауын енгізіңіз:")),
            "prof_edit_school":   ("school",   "prof_edit_subject",  ("📚 Введите ваш предмет:" if lang == "ru" else "📚 Пәніңізді енгізіңіз:")),
            "prof_edit_subject":  ("subject",  "prof_edit_classes",  ("🏷 Введите ваши классы (например: 7А, 8Б):" if lang == "ru" else "🏷 Сыныптарыңызды енгізіңіз:")),
            "prof_edit_classes":  ("classes",  "prof_edit_position", ("💼 Введите вашу должность:" if lang == "ru" else "💼 Лауазымыңызды енгізіңіз:")),
            "prof_edit_position": ("position", "prof_edit_director", ("👔 Введите ФИО директора:" if lang == "ru" else "👔 Директордың аты-жөнін енгізіңіз:")),
        }

        prof_steps = kg_prof_steps if is_kg else teacher_prof_steps

        if step in prof_steps:
            if len(text) < 2:
                await update.message.reply_text(
                    "❌ Слишком коротко. Попробуйте снова:" if lang == "ru" else "❌ Тым қысқа. Қайталап көріңіз:",
                    reply_markup=InlineKeyboardMarkup(cancel_kb)
                )
                return
            field, next_step, next_q = prof_steps[step]
            await self.db.upsert_user(user_id, {field: text})
            context.user_data["step"] = next_step
            await update.message.reply_text(next_q, reply_markup=InlineKeyboardMarkup(cancel_kb), parse_mode=ParseMode.MARKDOWN)

        elif step == "prof_edit_director":
            await self.db.upsert_user(user_id, {"director": text})
            context.user_data["step"] = None
            kb = [[MENU_BTN(lang)]]
            await update.message.reply_text(
                "✅ *Профиль обновлён!*" if lang == "ru" else "✅ *Профиль жаңартылды!*",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        # ── Добавление ученика/воспитанника ──
        elif step == "student_name":
            if len(text) < 3:
                await update.message.reply_text(
                    "❌ Введите полное имя (минимум 3 символа):" if lang == "ru" else "❌ Толық атын енгізіңіз:",
                    reply_markup=InlineKeyboardMarkup(cancel_kb)
                )
                return
            context.user_data["new_student"]["name"] = text
            context.user_data["step"] = "student_class"
            group_q = (
                ("🏷 Введите группу (например: старшая «Ромашка»):" if is_kg else "🏷 Введите класс (например: 9А):")
                if lang == "ru" else
                ("🏷 Топты енгізіңіз:" if is_kg else "🏷 Сыныпты енгізіңіз (мысалы: 9А):")
            )
            await update.message.reply_text(group_q, reply_markup=InlineKeyboardMarkup(cancel_kb))

        elif step == "student_class":
            context.user_data["new_student"]["class_name"] = text
            if is_kg:
                # Для сада оценки не нужны — сразу к достижениям
                context.user_data["new_student"]["grades"] = {}
                context.user_data["step"] = "student_achievements"
                await update.message.reply_text(
                    ("🏆 Достижения ребёнка через запятую:\n"
                     "_Например: выступление на утреннике, конкурс рисунков_\n"
                     "Или напишите *пропустить*") if lang == "ru" else
                    ("🏆 Баланың жетістіктерін үтірмен енгізіңіз:\n"
                     "Немесе *өткізу* жазыңыз"),
                    reply_markup=InlineKeyboardMarkup(cancel_kb),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                context.user_data["step"] = "student_grades"
                await update.message.reply_text(
                    ("📊 Введите оценки через запятую:\n"
                     "_Формат: Математика-5, Русский-4_\n"
                     "Или напишите *пропустить*") if lang == "ru" else
                    ("📊 Бағаларды үтірмен енгізіңіз:\n"
                     "_Формат: Математика-5, Орыс тілі-4_\n"
                     "Немесе *өткізу* жазыңыз"),
                    reply_markup=InlineKeyboardMarkup(cancel_kb),
                    parse_mode=ParseMode.MARKDOWN
                )

        elif step == "student_grades":
            grades = {}
            if text.lower() not in ["пропустить", "өткізу", "-", "нет"]:
                for part in text.split(","):
                    if "-" in part:
                        subj, grade = part.strip().rsplit("-", 1)
                        try:
                            grades[subj.strip()] = int(grade.strip())
                        except:
                            pass
            context.user_data["new_student"]["grades"] = grades
            context.user_data["step"] = "student_achievements"
            await update.message.reply_text(
                ("🏆 Введите достижения через запятую:\n_Призёр олимпиады, КВН, Волонтёр_\n"
                 "Или напишите *пропустить*") if lang == "ru" else
                ("🏆 Жетістіктерді үтірмен енгізіңіз:\n"
                 "Немесе *өткізу* жазыңыз"),
                reply_markup=InlineKeyboardMarkup(cancel_kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif step == "student_achievements":
            achievements = []
            if text.lower() not in ["пропустить", "өткізу", "-", "нет", "жоқ"]:
                achievements = [a.strip() for a in text.split(",") if a.strip()]
            context.user_data["new_student"]["achievements"] = achievements
            context.user_data["step"] = "student_absences"
            await update.message.reply_text(
                "📅 Количество пропущенных дней (введите число или 0):" if lang == "ru"
                else "📅 Өткізген күндер санын енгізіңіз (0 немесе сан):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_absences":
            try:
                absences = int(text)
            except:
                absences = 0
            context.user_data["new_student"]["absences"] = absences
            context.user_data["step"] = "student_behavior"
            kb = [
                [InlineKeyboardButton("😊 " + ("Отличное" if lang == "ru" else "Өте жақсы"), callback_data="_beh_1"),
                 InlineKeyboardButton("🙂 " + ("Хорошее" if lang == "ru" else "Жақсы"),      callback_data="_beh_2")],
                [InlineKeyboardButton("😐 " + ("Удовл." if lang == "ru" else "Қанағат."),    callback_data="_beh_3")],
                [CANCEL_BTN(lang)],
            ]
            beh_q = (
                ("😊 Выберите поведение ребёнка:" if is_kg else "😊 Выберите поведение ученика:")
                if lang == "ru" else
                ("😊 Баланың мінез-құлқын таңдаңыз:" if is_kg else "😊 Оқушының мінез-құлқын таңдаңыз:")
            )
            await update.message.reply_text(beh_q, reply_markup=InlineKeyboardMarkup(kb))

        elif step == "student_behavior":
            behavior_map = {
                "1": "отличное" if lang == "ru" else "өте жақсы",
                "2": "хорошее"  if lang == "ru" else "жақсы",
                "3": "удовлетворительное" if lang == "ru" else "қанағаттанарлық",
            }
            behavior = behavior_map.get(text, text)
            context.user_data["new_student"]["behavior"] = behavior
            context.user_data["step"] = "student_parents"
            await update.message.reply_text(
                "👪 ФИО родителей (или «пропустить»):" if lang == "ru" else "👪 Ата-анасының аты-жөні (немесе «өткізу»):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_parents":
            context.user_data["new_student"]["parents"] = "" if text.lower() in {"пропустить", "өткізу", "-", "нет", "жоқ"} else text
            context.user_data["step"] = "student_parent_phone"
            await update.message.reply_text(
                "📞 Телефон родителей (или «пропустить»):" if lang == "ru" else "📞 Ата-анасының телефоны (немесе «өткізу»):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_parent_phone":
            context.user_data["new_student"]["parent_phone"] = "" if text.lower() in {"пропустить", "өткізу", "-", "нет", "жоқ"} else text
            context.user_data["step"] = "student_birth_date"
            await update.message.reply_text(
                "🎂 Дата рождения (или «пропустить»):" if lang == "ru" else "🎂 Туған күні (немесе «өткізу»):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_birth_date":
            context.user_data["new_student"]["birth_date"] = "" if text.lower() in {"пропустить", "өткізу", "-", "нет", "жоқ"} else text
            await self._finish_add_student(update, context, user_id, lang, is_kg)

    async def _finish_add_student(self, update, context, user_id, lang, is_kg=False):
        student_data = context.user_data.pop("new_student", {})
        if not student_data.get("behavior"):
            student_data["behavior"] = "хорошее" if lang == "ru" else "жақсы"
        await self.db.add_student(user_id, student_data)
        # Базовый метод БД сохраняет прежние поля; новые добавляем тем же безопасным
        # интерфейсом обновления, не меняя существующую структуру добавления.
        saved = next((s for s in reversed(await self.db.get_students(user_id)) if s["name"] == student_data.get("name")), None)
        if saved:
            await self.db.update_student(saved["id"], {
                "parents": student_data.get("parents", ""),
                "parent_phone": student_data.get("parent_phone", ""),
                "birth_date": student_data.get("birth_date", ""),
            })
        context.user_data["step"] = None
        name = student_data.get("name", "")
        add_btn_text = t(lang, "btn_add_child") if is_kg else t(lang, "btn_add_student")
        list_btn_text = t(lang, "btn_my_children") if is_kg else t(lang, "btn_my_students")
        kb = [
            [InlineKeyboardButton("➕ " + ("Добавить ещё" if lang == "ru" else "Тағы қосу"), callback_data="prof_add_student")],
            [InlineKeyboardButton("👥 " + list_btn_text,  callback_data="prof_students")],
            [MENU_BTN(lang)],
        ]
        await update.message.reply_text(
            f"✅ *{name}* добавлен в базу!" if lang == "ru" else f"✅ *{name}* базаға қосылды!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _parse_students_from_text(self, update, context, raw_text: str):
        await self._parse_students_with_ai(update, context, [{"type": "text", "text": raw_text}])

    async def _parse_students_from_photo(self, update, context):
        user = await self.db.get_user(update.effective_user.id)
        lang = user.get("lang", "ru") if user else "ru"
        wait = await update.message.reply_text("🔍 Читаю список..." if lang == "ru" else "🔍 Тізім оқылуда...")
        try:
            photo = update.message.photo[-1]
            file_obj = await photo.get_file()
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                await file_obj.download_to_drive(tmp.name)
                path = tmp.name
            with open(path, "rb") as file:
                encoded = base64.standard_b64encode(file.read()).decode("utf-8")
            os.unlink(path)
            await wait.delete()
            await self._parse_students_with_ai(update, context, [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}}])
        except Exception:
            await wait.delete()
            await update.message.reply_text("Не удалось прочитать фото. Пришлите более чёткое изображение." if lang == "ru" else "Фотоны оқу мүмкін болмады. Анығырақ сурет жіберіңіз.")

    async def _parse_students_with_ai(self, update, context, content: list):
        user = await self.db.get_user(update.effective_user.id)
        lang = user.get("lang", "ru") if user else "ru"
        prompt = ("Извлеки список учеников из текста или изображения. Верни ТОЛЬКО JSON-массив: "
                  "[{\"name\":\"\", \"birth_date\":\"\", \"parent_name\":\"\", \"phone\":\"\"}]. "
                  "Если данных нет — оставь поле пустым. Не придумывай данные.")
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(model="claude-haiku-4-5", max_tokens=1200,
                                              messages=[{"role": "user", "content": content + [{"type": "text", "text": prompt}]}])
            raw = re.sub(r"```[a-z]*", "", response.content[0].text.strip()).strip("` \n")
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(match.group(0) if match else raw)
            students = [item for item in parsed if isinstance(item, dict) and str(item.get("name", "")).strip()]
        except Exception as exc:
            print(f"Student list parse error: {exc}")
            students = []
        if not students:
            await update.message.reply_text("Не удалось найти учеников. Попробуйте другой текст или более чёткое фото." if lang == "ru" else "Оқушылар тізімі табылмады. Басқа мәтін не анығырақ фото жіберіңіз.")
            return
        context.user_data["bulk_students"] = students
        context.user_data["step"] = "student_bulk_confirm"
        names = "\n".join(f"• {item['name']}" for item in students[:30])
        await update.message.reply_text(
            (f"Нашёл {len(students)} учеников. Добавить всех?\n\n{names}" if lang == "ru" else f"{len(students)} оқушы табылды. Барлығын қосу керек пе?\n\n{names}"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Добавить всех" if lang == "ru" else "Барлығын қосу", callback_data="prof_bulk_confirm")], [InlineKeyboardButton("Отмена" if lang == "ru" else "Болдырмау", callback_data="prof_bulk_cancel")]])
        )
