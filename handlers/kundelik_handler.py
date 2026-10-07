"""
Telegram Bot Handler for Kundelik.kz and BilimClass Integration.
Exclusively available for MAX tier users (7 490 ₸/мес).
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Database
from handlers.kundelik_api import (
    KundelikClient,
    CRITERIA_DESCRIPTORS_RU,
    CRITERIA_DESCRIPTORS_KZ,
)

logger = logging.getLogger(__name__)

MAX_TIER_UPSELL_TEXT_RU = (
    "🔒 *Интеграция с Күнделік / BilimClass доступна только на тарифе MAX!*\n\n"
    "💎 *Тариф MAX (7 490 ₸/мес)* включает:\n"
    "• Автоматическую синхронизацию расписания и классов с Kundelik.kz\n"
    "• Импорт списка учеников и их оценок в один клик\n"
    "• Выставление формативных оценок (1-10 ФО) прямо из Telegram с умными дескрипторами\n"
    "• ИИ-мониторинг успеваемости и аналитические отчёты\n"
    "• Генерацию презентаций PowerPoint (.pptx)\n\n"
    "Перейдите в раздел тарифов, чтобы подключить MAX!"
)

MAX_TIER_UPSELL_TEXT_KZ = (
    "🔒 *Күнделік / BilimClass интеграциясы тек MAX тарифінде қолжетімді!*\n\n"
    "💎 *MAX тарифі (7 490 ₸/ай)* мүмкіндіктері:\n"
    "• Kundelik.kz жүйесінен сабақ кестесі мен сыныптарды автоматты синхрондау\n"
    "• Оқушылар тізімі мен бағаларын 1 түймемен импорттау\n"
    "• Қалыптастырушы бағалауды (1-10 ҚБ) Telegram арқылы дескрипторлармен қою\n"
    "• Оқу үлгерімінің ЖИ-мониторингі және аналитикалық есептер\n"
    "• PowerPoint (.pptx) презентацияларын жасау\n\n"
    "MAX тарифін қосу үшін тарифтер бөліміне өтіңіз!"
)


class KundelikHandler:
    def __init__(self, db: Database):
        self.db = db

    async def show_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главный экран интеграции с Kundelik / BilimClass."""
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id) or {}
        lang = user.get("lang", "ru")
        tier = (user.get("tier") or "").lower()
        is_admin = user_id in context.application.bot_data.get("admin_ids", [739268686])

        # Проверка тарифа MAX
        if tier != "max" and not is_admin:
            text = MAX_TIER_UPSELL_TEXT_RU if lang == "ru" else MAX_TIER_UPSELL_TEXT_KZ
            kb = [
                [InlineKeyboardButton("⭐ " + ("Перейти к тарифам" if lang == "ru" else "Тарифтерге өту"), callback_data="prof_sub")],
                [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
            ]
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            return

        integration = await self.db.get_kundelik_integration(user_id)
        if not integration:
            await self._show_connect_screen(update, context, lang)
        else:
            await self._show_connected_screen(update, context, lang, integration)

    async def _show_connect_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
        text = (
            "🔗 *Интеграция с Kundelik.kz / BilimClass (Тариф MAX)*\n\n"
            "Подключите ваш школьный журнал для автоматической синхронизации расписания, "
            "списков классов и выставления оценок прямо из Telegram.\n\n"
            "💡 *Как подключить:*\n"
            "1. Получите токен доступа в личном кабинете Kundelik / BilimClass (или используйте тестовый токен `demo_kundelik_token`).\n"
            "2. Нажмите кнопку «🔑 Ввести токен» ниже."
        ) if lang == "ru" else (
            "🔗 *Kundelik.kz / BilimClass интеграциясы (MAX тарифі)*\n\n"
            "Сабақ кестесін, сыныптарды синхрондау және бағаларды Telegram арқылы "
            "қою үшін журналды байланыстырыңыз.\n\n"
            "💡 *Қосылу жолы:*\n"
            "1. Kundelik / BilimClass жеке кабинетінен токен алыңыз (немесе тест үшін `demo_kundelik_token` қолданыңыз).\n"
            "2. Төмендегі «🔑 Токен енгізу» түймесін басыңыз."
        )
        kb = [
            [InlineKeyboardButton("🔑 " + ("Ввести токен доступа" if lang == "ru" else "Қолжетімділік токенін енгізу"), callback_data="kd_enter_token")],
            [InlineKeyboardButton("⚡ " + ("Демо-подключение (Тестовая школа)" if lang == "ru" else "Демо қосылу (Сынақ мектебі)"), callback_data="kd_demo_connect")],
            [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
        ]
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_connected_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, integration: dict):
        school = integration.get("school_name") or "Школа-гимназия №6 г. Хромтау"
        provider = (integration.get("provider") or "kundelik").capitalize()
        synced_at = (integration.get("synced_at") or "")[:16]

        text = (
            f"✅ *{provider} успешно подключён!* (Тариф MAX)\n\n"
            f"🏫 *Организация:* {school}\n"
            f"🔄 *Последняя синхронизация:* {synced_at}\n\n"
            f"⚡ *Доступные действия:*\n"
            f"• 📊 *Журнал оценок:* выставление формативных оценок (1-10 ФО) с готовыми критериями\n"
            f"• 👥 *Синхронизация учеников:* обновление списков классов\n"
            f"• 📅 *Синхронизация расписания:* загрузка уроков в календарь Docura"
        ) if lang == "ru" else (
            f"✅ *{provider} сәтті қосылды!* (MAX тарифі)\n\n"
            f"🏫 *Мекеме:* {school}\n"
            f"🔄 *Соңғы синхрондау:* {synced_at}\n\n"
            f"⚡ *Қолжетімді әрекеттер:*\n"
            f"• 📊 *Бағалау журналы:* қалыптастырушы баға (1-10 ҚБ) және дескриптор қою\n"
            f"• 👥 *Оқушыларды синхрондау:* сынып тізімдерін жаңарту\n"
            f"• 📅 *Кестені синхрондау:* сабақтарды Docura күнтізбесіне жүктеу"
        )
        kb = [
            [InlineKeyboardButton("📊 " + ("Поставить оценку (Журнал)" if lang == "ru" else "Баға қою (Журнал)"), callback_data="kd_classes")],
            [InlineKeyboardButton("🔄 " + ("Синхронизировать всё" if lang == "ru" else "Барлығын синхрондау"), callback_data="kd_sync_all")],
            [InlineKeyboardButton("📈 " + ("История оценок" if lang == "ru" else "Бағалар тарихы"), callback_data="kd_marks_history")],
            [InlineKeyboardButton("🔌 " + ("Отключить интеграцию" if lang == "ru" else "Байланысты ажырату"), callback_data="kd_disconnect")],
            [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
        ]
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id) or {}
        lang = user.get("lang", "ru")

        if data == "kd_menu":
            await self.show_menu(update, context)

        elif data == "kd_demo_connect":
            # Быстрое подключение демо-школы
            await self.db.set_kundelik_integration(
                tg_id=user_id,
                token="demo_kundelik_token",
                provider="kundelik",
                school_id=100245,
                school_name="Школа-гимназия №6 г. Хромтау",
                person_id=982341
            )
            # Синхронизируем базовые данные
            client = KundelikClient("demo_kundelik_token")
            classes = await client.get_classes()
            for cls in classes:
                students = await client.get_students_for_class(cls["id"])
                for st in students:
                    await self.db.add_student(
                        user_id, st["name"], st["class_name"],
                        notes=f"Синхронизировано из Kundelik.kz (avg: {st['avg_mark']})",
                        absences=st["absences"]
                    )
            schedule = await client.get_schedule()
            if schedule:
                await self.db.save_schedule(user_id, schedule)

            await query.answer("✅ Kundelik успешно подключён!", show_alert=True)
            await self.show_menu(update, context)

        elif data == "kd_enter_token":
            context.user_data["step"] = "kd_wait_token"
            text = (
                "🔑 *Введите токен доступа Kundelik.kz или BilimClass*\n\n"
                "Отправьте токен в ответном сообщении.\n"
                "Если у вас пока нет боевого токена школы, отправьте `demo_kundelik_token` для демонстрационного режима."
            ) if lang == "ru" else (
                "🔑 *Kundelik.kz немесе BilimClass токенін енгізіңіз*\n\n"
                "Токенді жауап ретінде жіберіңіз.\n"
                "Егер мектеп токені әзірге болмаса, сынақ режимі үшін `demo_kundelik_token` жібере аласыз."
            )
            kb = [[InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="kd_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "kd_classes":
            integration = await self.db.get_kundelik_integration(user_id)
            if not integration:
                await self.show_menu(update, context)
                return

            client = KundelikClient(integration["token"], integration.get("provider", "kundelik"))
            classes = await client.get_classes()

            text = (
                "📚 *Выберите класс для выставления оценок:*"
            ) if lang == "ru" else (
                "📚 *Баға қою үшін сыныпты таңдаңыз:*"
            )
            kb = []
            for cls in classes:
                kb.append([InlineKeyboardButton(
                    f"📖 {cls['name']} — {cls['subject']} ({cls.get('students_count', 0)} уч.)",
                    callback_data=f"kd_class_{cls['id']}"
                )])
            kb.append([InlineKeyboardButton("◀️ " + ("Назад" if lang == "ru" else "Артқа"), callback_data="kd_menu")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("kd_class_"):
            class_id = int(data.replace("kd_class_", ""))
            integration = await self.db.get_kundelik_integration(user_id)
            client = KundelikClient(integration["token"], integration.get("provider", "kundelik"))
            students = await client.get_students_for_class(class_id)

            text = (
                f"👥 *Выберите ученика:*"
            ) if lang == "ru" else (
                f"👥 *Оқушыны таңдаңыз:*"
            )
            kb = []
            for st in students:
                kb.append([InlineKeyboardButton(
                    f"👤 {st['name']} (ср. {st['avg_mark']})",
                    callback_data=f"kd_st_{class_id}_{st['id']}"
                )])
            kb.append([InlineKeyboardButton("◀️ " + ("Назад к классам" if lang == "ru" else "Сыныптарға қайту"), callback_data="kd_classes")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("kd_st_"):
            parts = data.split("_")
            class_id = int(parts[2])
            student_id = int(parts[3])

            integration = await self.db.get_kundelik_integration(user_id)
            client = KundelikClient(integration["token"], integration.get("provider", "kundelik"))
            students = await client.get_students_for_class(class_id)
            student = next((s for s in students if s["id"] == student_id), None)
            student_name = student["name"] if student else "Ученик"

            context.user_data["kd_active_student"] = {
                "id": student_id,
                "name": student_name,
                "class_id": class_id,
                "class_name": student.get("class_name", "") if student else ""
            }

            text = (
                f"📝 *Выставление оценки:* {student_name}\n\n"
                f"Выберите балл формативного оценивания (ФО 1-10):\n"
                f"• 8-10 — Высокий уровень\n"
                f"• 4-7 — Средний уровень\n"
                f"• 1-3 — Низкий уровень"
            ) if lang == "ru" else (
                f"📝 *Баға қою:* {student_name}\n\n"
                f"Қалыптастырушы бағалау балын таңдаңыз (ҚБ 1-10):\n"
                f"• 8-10 — Жоғары деңгей\n"
                f"• 4-7 — Орта деңгей\n"
                f"• 1-3 — Төмен деңгей"
            )

            # Кнопки 1-10
            kb = [
                [
                    InlineKeyboardButton("🔟 10", callback_data="kd_mark_10"),
                    InlineKeyboardButton("9️⃣ 9", callback_data="kd_mark_9"),
                    InlineKeyboardButton("8️⃣ 8", callback_data="kd_mark_8"),
                ],
                [
                    InlineKeyboardButton("7️⃣ 7", callback_data="kd_mark_7"),
                    InlineKeyboardButton("6️⃣ 6", callback_data="kd_mark_6"),
                    InlineKeyboardButton("5️⃣ 5", callback_data="kd_mark_5"),
                ],
                [
                    InlineKeyboardButton("4️⃣ 4", callback_data="kd_mark_4"),
                    InlineKeyboardButton("3️⃣ 3", callback_data="kd_mark_3"),
                    InlineKeyboardButton("2️⃣ 2", callback_data="kd_mark_2"),
                ],
                [InlineKeyboardButton("◀️ " + ("К списку учеников" if lang == "ru" else "Оқушыларға қайту"), callback_data=f"kd_class_{class_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("kd_mark_"):
            mark_val = int(data.replace("kd_mark_", ""))
            st_data = context.user_data.get("kd_active_student") or {}
            student_id = st_data.get("id", 1001)
            student_name = st_data.get("name", "Ученик")
            class_name = st_data.get("class_name", "7 «А»")

            desc_dict = CRITERIA_DESCRIPTORS_RU if lang == "ru" else CRITERIA_DESCRIPTORS_KZ
            descriptor = desc_dict.get(mark_val, "Жақсы нәтиже!")

            integration = await self.db.get_kundelik_integration(user_id)
            client = KundelikClient(integration["token"] if integration else "demo", integration.get("provider", "kundelik") if integration else "kundelik")
            res = await client.post_mark(student_id, student_name, class_name, mark_val, "ФО", descriptor)

            if res.get("ok"):
                await self.db.save_kundelik_mark(user_id, student_id, student_name, class_name, mark_val, "ФО", descriptor)
                success_text = (
                    f"✅ *Оценка успешно выставлена в Kundelik.kz!*\n\n"
                    f"👤 *Ученик:* {student_name} ({class_name})\n"
                    f"🎯 *Балл:* {mark_val} (ФО)\n"
                    f"💬 *Дескриптор:* _{descriptor}_\n\n"
                    f"Оценка синхронизирована с журналом."
                ) if lang == "ru" else (
                    f"✅ *Баға Kundelik.kz журналына сәтті қойылды!*\n\n"
                    f"👤 *Оқушы:* {student_name} ({class_name})\n"
                    f"🎯 *Балл:* {mark_val} (ҚБ)\n"
                    f"💬 *Дескриптор:* _{descriptor}_\n\n"
                    f"Баға мектеп журналымен үйлестірілді."
                )
                kb = [
                    [InlineKeyboardButton("👤 " + ("Поставить оценку другому ученику" if lang == "ru" else "Басқа оқушыға қою"), callback_data=f"kd_class_{st_data.get('class_id', 401)}")],
                    [InlineKeyboardButton("📊 " + ("Журнал классов" if lang == "ru" else "Сыныптар журналы"), callback_data="kd_classes")],
                    [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
                ]
                await query.edit_message_text(success_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            else:
                await query.answer(f"Ошибка выставления: {res.get('error')}", show_alert=True)

        elif data == "kd_sync_all":
            integration = await self.db.get_kundelik_integration(user_id)
            if not integration:
                await self.show_menu(update, context)
                return

            client = KundelikClient(integration["token"], integration.get("provider", "kundelik"))
            # Синхронизация расписания
            sched = await client.get_schedule()
            if sched:
                await self.db.save_schedule(user_id, sched)
            # Синхронизация классов и учеников
            classes = await client.get_classes()
            count = 0
            for cls in classes:
                students = await client.get_students_for_class(cls["id"])
                for st in students:
                    await self.db.add_student(
                        user_id, st["name"], st["class_name"],
                        notes=f"Kundelik.kz (ср. {st['avg_mark']})",
                        absences=st["absences"]
                    )
                    count += 1

            await query.answer("✅ Успешно синхронизировано!", show_alert=True)
            await self.show_menu(update, context)

        elif data == "kd_marks_history":
            marks = await self.db.get_kundelik_marks(user_id, limit=10)
            if not marks:
                msg = "История выставленных оценок пуста." if lang == "ru" else "Қойылған бағалар тарихы бос."
            else:
                lines = []
                for m in marks:
                    dt = (m.get("created_at") or "")[:16]
                    lines.append(f"• *{m['student_name']}* ({m['class_name']}) — *{m['mark']}* ({m['mark_type']})\n  _{m['descriptor']}_ ({dt})")
                header = "📊 *Недавно выставленные оценки:*\n\n" if lang == "ru" else "📊 *Соңғы қойылған бағалар:*\n\n"
                msg = header + "\n\n".join(lines)

            kb = [
                [InlineKeyboardButton("◀️ " + ("Назад" if lang == "ru" else "Артқа"), callback_data="kd_menu")]
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "kd_disconnect":
            await self.db.remove_kundelik_integration(user_id)
            await query.answer("Интеграция отключена.", show_alert=True)
            await self.show_menu(update, context)

    async def handle_token_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода токена пользователем."""
        user_id = update.effective_user.id
        token = update.message.text.strip()
        user = await self.db.get_user(user_id) or {}
        lang = user.get("lang", "ru")

        context.user_data["step"] = None

        client = KundelikClient(token)
        prof_res = await client.get_profile()

        if prof_res.get("ok"):
            prof = prof_res.get("data", {})
            school = (prof.get("schools") or [{}])[0].get("name", "Школа Kundelik.kz")
            await self.db.set_kundelik_integration(
                tg_id=user_id,
                token=token,
                provider="kundelik",
                school_name=school,
                person_id=prof.get("person_id")
            )
            # Автоматически синхронизируем учеников и расписание
            classes = await client.get_classes()
            for cls in classes:
                students = await client.get_students_for_class(cls["id"])
                for st in students:
                    await self.db.add_student(
                        user_id, st["name"], st["class_name"],
                        notes=f"Kundelik.kz (ср. {st['avg_mark']})",
                        absences=st["absences"]
                    )
            sched = await client.get_schedule()
            if sched:
                await self.db.save_schedule(user_id, sched)

            text = (
                f"🎉 *Kundelik.kz успешно подключён!*\n\n"
                f"🏫 Школа: {school}\n"
                f"Расписание и списки учеников автоматически синхронизированы."
            ) if lang == "ru" else (
                f"🎉 *Kundelik.kz сәтті байланыстырылды!*\n\n"
                f"🏫 Мектеп: {school}\n"
                f"Кесте және оқушылар тізімі автоматты түрде қосылды."
            )
            kb = [
                [InlineKeyboardButton("📊 " + ("Перейти к журналу" if lang == "ru" else "Журналға өту"), callback_data="kd_classes")],
                [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
            ]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                f"❌ Не удалось подключить токен: {prof_res.get('error')}. Попробуйте ещё раз или выберите «Демо-подключение» в меню /kundelik."
            )
