import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

MENU_BTN = lambda lang: InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")
BACK_BTN = lambda lang, cb: InlineKeyboardButton("◀️ " + ("Назад" if lang == "ru" else "Артқа"), callback_data=cb)
CANCEL_BTN = lambda lang: InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="menu_main")


def _esc(text):
    """Экранирует символы Markdown в данных пользователя — без этого Telegram падает,
    если имя/школа/предмет содержат *, _, [, `."""
    if text is None:
        return "—"
    text = str(text)
    for ch in ["_", "*", "[", "]", "`"]:
        text = text.replace(ch, "\\" + ch)
    return text


# Тарифы
TIERS = {
    "basic": {"price": 2490, "name_ru": "Базовый", "name_kz": "Негізгі"},
    "pro":   {"price": 3990, "name_ru": "PRO",     "name_kz": "PRO"},
}

class ProfileHandler:
    def __init__(self, db: Database):
        self.db = db

    async def show(self, query, user_id, lang):
        user = await self.db.get_user(user_id)
        if not user:
            return

        name     = user.get("name", "—")
        school   = user.get("school", "—")
        subject  = user.get("subject", "—")
        classes  = user.get("classes", "—")
        position = user.get("position", "—")
        director = user.get("director", "—")
        is_ct    = "✅" if user.get("is_class_teacher") else "❌"
        is_pro   = user.get("subscribed", 0)
        free_used = user.get("free_used", 0)
        free_left = max(0, 3 - free_used)

        if is_pro:
            sub_line = "⭐ *PRO* — безлимитный доступ активен" if lang == "ru" else "⭐ *PRO* — шексіз қол жеткізу белсенді"
            pro_badge = "⭐ PRO"
        else:
            sub_line = f"🆓 Бесплатно — осталось *{free_left}/3* документов" if lang == "ru" else f"🆓 Тегін — қалды *{free_left}/3* құжат"
            pro_badge = "🆓 Free"

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
                f"📄 Создано документов: *{free_used}*"
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
                f"📄 Жасалған құжаттар: *{free_used}*"
            )

        keyboard = [
            [InlineKeyboardButton("✏️ " + t(lang, "btn_edit_profile"), callback_data="prof_edit")],
            [InlineKeyboardButton("👥 " + t(lang, "btn_my_students"),  callback_data="prof_students")],
            [InlineKeyboardButton("⭐ " + t(lang, "btn_subscription"), callback_data="prof_sub")],
            [InlineKeyboardButton("🌐 " + t(lang, "btn_change_lang"),  callback_data="prof_lang")],
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

        if data == "prof_edit":
            context.user_data["step"] = "prof_edit_name"
            kb = [[CANCEL_BTN(lang)]]
            await query.edit_message_text(
                ("✏️ *Редактирование профиля*\n\nВведите ваше ФИО:" if lang == "ru"
                 else "✏️ *Профильді өңдеу*\n\nАты-жөніңізді енгізіңіз:"),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "prof_students":
            if user.get("tier") != "pro":
                kb = [
                    [InlineKeyboardButton("⭐ Перейти на PRO" if lang == "ru" else "⭐ PRO-ға өту", callback_data="prof_sub")],
                    [MENU_BTN(lang)],
                ]
                text = (
                    "👥 База учеников доступна только на тарифе *PRO*.\n\n"
                    "Перейдите на PRO чтобы добавлять учеников и использовать их данные в характеристиках."
                ) if lang == "ru" else (
                    "👥 Оқушылар базасы тек *PRO* тарифінде қол жетімді.\n\n"
                    "PRO-ға өтіп, оқушыларды қосыңыз."
                )
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            else:
                await self._show_students(query, user_id, lang)

        elif data == "prof_sub":
            await self._show_subscription(query, user, lang)

        elif data in ("prof_choose_basic", "prof_choose_pro"):
            tier = "basic" if data == "prof_choose_basic" else "pro"
            context.user_data["chosen_tier"] = tier
            context.user_data["step"] = "waiting_payment_screenshot"
            t_info = TIERS[tier]
            t_name = t_info["name_ru"] if lang == "ru" else t_info["name_kz"]
            price = t_info["price"]
            text = (
                f"💳 *Тариф {t_name} — {price} тг/мес*\n\n"
                f"*Оплата через Kaspi:*\n"
                f"`+7 771 451 4717`\n"
                f"_(нажмите на номер чтобы скопировать)_\n\n"
                f"1️⃣ Переведите {price} тг на этот номер\n"
                f"2️⃣ Пришлите скриншот оплаты следующим сообщением\n"
                f"3️⃣ Подписка активируется автоматически ✅"
            ) if lang == "ru" else (
                f"💳 *{t_name} тарифі — {price} тг/ай*\n\n"
                f"*Kaspi арқылы төлем:*\n"
                f"`+7 771 451 4717`\n\n"
                f"1️⃣ {price} тг осы нөмірге аударыңыз\n"
                f"2️⃣ Төлем скриншотын келесі хабарламада жіберіңіз\n"
                f"3️⃣ Жазылым автоматты белсендіріледі ✅"
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
            await query.edit_message_text(
                ("👤 *Добавление ученика*\n\nВведите полное имя ученика:" if lang == "ru"
                 else "👤 *Оқушы қосу*\n\nОқушының толық атын енгізіңіз:"),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data.startswith("student_del_"):
            student_id = int(data[12:])
            s = await self.db.get_student(student_id)
            name = s.get("name", "") if s else ""
            await self.db.delete_student(student_id)
            await query.answer("✅ Удалено" if lang == "ru" else "✅ Жойылды", show_alert=False)
            await self._show_students(query, user_id, lang)

        elif data.startswith("student_view_"):
            student_id = int(data[13:])
            await self._show_student_detail(query, student_id, lang)

        elif data == "prof_back_students":
            await self._show_students(query, user_id, lang)

    async def _show_students(self, query, user_id, lang):
        students = await self.db.get_students(user_id)
        keyboard = []

        if not students:
            keyboard = [
                [InlineKeyboardButton("➕ " + t(lang, "btn_add_student"), callback_data="prof_add_student")],
                [MENU_BTN(lang)],
            ]
            await query.edit_message_text(
                ("👥 *Мои ученики*\n\nСписок пуст. Добавьте первого ученика!" if lang == "ru"
                 else "👥 *Менің оқушыларым*\n\nТізім бос. Бірінші оқушыны қосыңыз!"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        text = f"👥 *{'Мои ученики' if lang == 'ru' else 'Менің оқушыларым'}* ({len(students)})\n\n"
        for s in students:
            grades = json.loads(s.get("grades", "{}"))
            avg = round(sum(grades.values()) / len(grades), 1) if grades else "—"
            text += f"👤 {s['name']} • {s['class_name']} • ср.балл: {avg}\n"

        for s in students:
            keyboard.append([
                InlineKeyboardButton(f"👤 {s['name']} ({s['class_name']})", callback_data=f"student_view_{s['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"student_del_{s['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("➕ " + t(lang, "btn_add_student"), callback_data="prof_add_student")])
        keyboard.append([MENU_BTN(lang)])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_student_detail(self, query, student_id, lang):
        s = await self.db.get_student(student_id)
        if not s:
            return

        grades       = json.loads(s.get("grades", "{}"))
        achievements = json.loads(s.get("achievements", "[]"))
        grades_str   = "\n".join(f"  • {k}: {v}" for k, v in grades.items()) if grades else ("  нет данных" if lang == "ru" else "  деректер жоқ")
        achieve_str  = "\n".join(f"  • {a}" for a in achievements) if achievements else ("  нет" if lang == "ru" else "  жоқ")

        text = (
            f"👤 *{s['name']}*\n"
            f"🏷 Класс: {s['class_name']}\n"
            f"😊 Поведение: {s.get('behavior', '—')}\n"
            f"📅 Пропуски: {s.get('absences', 0)} дн.\n\n"
            f"📊 *Оценки:*\n{grades_str}\n\n"
            f"🏆 *Достижения:*\n{achieve_str}"
        ) if lang == "ru" else (
            f"👤 *{s['name']}*\n"
            f"🏷 Сынып: {s['class_name']}\n"
            f"😊 Мінез-құлық: {s.get('behavior', '—')}\n"
            f"📅 Өткізулер: {s.get('absences', 0)} күн\n\n"
            f"📊 *Бағалар:*\n{grades_str}\n\n"
            f"🏆 *Жетістіктер:*\n{achieve_str}"
        )

        keyboard = [
            [BACK_BTN(lang, "prof_back_students")],
            [MENU_BTN(lang)],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_subscription(self, query, user, lang):
        tier = user.get("tier", "free")

        if user.get("subscribed") and tier in TIERS:
            t_info = TIERS[tier]
            t_name = t_info["name_ru"] if lang == "ru" else t_info["name_kz"]
            if tier == "pro":
                feats = (
                    "✅ Безлимитная генерация документов\n"
                    "✅ Все 21 тип документов\n"
                    "✅ Голосовой ввод\n"
                    "✅ База учеников\n"
                    "✅ Точечное редактирование документов"
                ) if lang == "ru" else (
                    "✅ Шексіз құжат жасау\n"
                    "✅ 21 түрлі құжат\n"
                    "✅ Дауыстық енгізу\n"
                    "✅ Оқушылар базасы\n"
                    "✅ Құжатты түзету"
                )
            else:
                feats = (
                    "✅ Безлимитная генерация документов\n"
                    "✅ Все 21 тип документов"
                ) if lang == "ru" else (
                    "✅ Шексіз құжат жасау\n"
                    "✅ 21 түрлі құжат"
                )
            text = (
                f"⭐ *Подписка {t_name} активна!*\n\n{feats}"
                if lang == "ru" else
                f"⭐ *{t_name} жазылымы белсенді!*\n\n{feats}"
            )
            keyboard = [
                [BACK_BTN(lang, "menu_profile")],
                [MENU_BTN(lang)],
            ]
        else:
            free_left = max(0, 3 - user.get("free_used", 0))
            text = (
                f"💳 *Подписка Docura*\n\n"
                f"У вас осталось *{free_left} бесплатных* документов.\n\n"
                f"🔹 *Базовый — 2 490 тг/мес:*\n"
                f"✅ Безлимитная генерация\n"
                f"✅ Все 21 тип документов\n\n"
                f"⭐ *PRO — 3 990 тг/мес:*\n"
                f"✅ Всё из Базового\n"
                f"✅ Голосовой ввод\n"
                f"✅ База учеников\n"
                f"✅ Точечное редактирование документов\n\n"
                f"Выберите тариф ниже 👇"
            ) if lang == "ru" else (
                f"💳 *Docura жазылымы*\n\n"
                f"Сізде *{free_left} тегін* құжат қалды.\n\n"
                f"🔹 *Негізгі — 2 490 тг/ай:*\n"
                f"✅ Шексіз жасау\n"
                f"✅ 21 түрлі құжат\n\n"
                f"⭐ *PRO — 3 990 тг/ай:*\n"
                f"✅ Негізгінің бәрі\n"
                f"✅ Дауыстық енгізу\n"
                f"✅ Оқушылар базасы\n"
                f"✅ Құжатты түзету\n\n"
                f"Тарифті таңдаңыз 👇"
            )
            keyboard = [
                [InlineKeyboardButton(
                    "🔹 Базовый — 2 490 тг" if lang == "ru" else "🔹 Негізгі — 2 490 тг",
                    callback_data="prof_choose_basic"
                )],
                [InlineKeyboardButton(
                    "⭐ PRO — 3 990 тг" if lang == "ru" else "⭐ PRO — 3 990 тг",
                    callback_data="prof_choose_pro"
                )],
                [BACK_BTN(lang, "menu_profile")],
                [MENU_BTN(lang)],
            ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает скриншот оплаты и пересылает админу с кнопкой активации нужного тарифа"""
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        tier    = context.user_data.get("chosen_tier", "pro")

        context.user_data["step"] = None

        ADMIN_CHAT_ID = 6561112046
        t_info = TIERS.get(tier, TIERS["pro"])

        try:
            caption = (
                f"💳 *Новая оплата!*\n\n"
                f"👤 {_esc(user.get('name', 'без имени'))}\n"
                f"🏫 {_esc(user.get('school', '—'))}\n"
                f"🆔 `{user_id}`\n"
                f"📦 Тариф: *{t_info['name_ru']}* ({t_info['price']} тг)\n\n"
                f"Нажми чтобы активировать 👇"
            )
            kb = [[InlineKeyboardButton(
                f"✅ Активировать {t_info['name_ru']}",
                callback_data=f"admin_activate_{tier}_{user_id}"
            )]]
            await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=update.message.photo[-1].file_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            print(f"Failed to forward payment screenshot: {e}")

        kb_user = [[MENU_BTN(lang)]]
        text = (
            "✅ Скриншот получен! Подписка активируется в течение нескольких минут.\n\n"
            "Спасибо за оплату! 🎉"
        ) if lang == "ru" else (
            "✅ Скриншот алынды! Жазылым бірнеше минутта белсендіріледі.\n\n"
            "Төлеміңіз үшін рахмет! 🎉"
        )
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_user))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        text    = update.message.text.strip()
        step    = context.user_data.get("step", "")

        cancel_kb = [[CANCEL_BTN(lang)]]

        # ── Редактирование профиля ──
        prof_steps = {
            "prof_edit_name":     ("name",     "prof_edit_school",   ("🏫 Введите название школы:" if lang == "ru" else "🏫 Мектептің атауын енгізіңіз:")),
            "prof_edit_school":   ("school",   "prof_edit_subject",  ("📚 Введите ваш предмет:" if lang == "ru" else "📚 Пәніңізді енгізіңіз:")),
            "prof_edit_subject":  ("subject",  "prof_edit_classes",  ("🏷 Введите ваши классы (например: 7А, 8Б):" if lang == "ru" else "🏷 Сыныптарыңызды енгізіңіз:")),
            "prof_edit_classes":  ("classes",  "prof_edit_position", ("💼 Введите вашу должность:" if lang == "ru" else "💼 Лауазымыңызды енгізіңіз:")),
            "prof_edit_position": ("position", "prof_edit_director", ("👔 Введите ФИО директора:" if lang == "ru" else "👔 Директордың аты-жөнін енгізіңіз:")),
        }

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

        # ── Добавление ученика ──
        elif step == "student_name":
            if len(text) < 3:
                await update.message.reply_text(
                    "❌ Введите полное имя (минимум 3 символа):" if lang == "ru" else "❌ Толық атын енгізіңіз:",
                    reply_markup=InlineKeyboardMarkup(cancel_kb)
                )
                return
            context.user_data["new_student"]["name"] = text
            context.user_data["step"] = "student_class"
            await update.message.reply_text(
                "🏷 Введите класс (например: 9А):" if lang == "ru" else "🏷 Сыныпты енгізіңіз (мысалы: 9А):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_class":
            context.user_data["new_student"]["class_name"] = text
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
            await update.message.reply_text(
                "😊 Выберите поведение ученика:" if lang == "ru" else "😊 Оқушының мінез-құлқын таңдаңыз:",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif step == "student_behavior":
            behavior_map = {
                "1": "отличное" if lang == "ru" else "өте жақсы",
                "2": "хорошее"  if lang == "ru" else "жақсы",
                "3": "удовлетворительное" if lang == "ru" else "қанағаттанарлық",
            }
            behavior = behavior_map.get(text, text)
            context.user_data["new_student"]["behavior"] = behavior
            await self._finish_add_student(update, context, user_id, lang)

    async def _finish_add_student(self, update, context, user_id, lang):
        student_data = context.user_data.pop("new_student", {})
        if not student_data.get("behavior"):
            student_data["behavior"] = "хорошее" if lang == "ru" else "жақсы"
        await self.db.add_student(user_id, student_data)
        context.user_data["step"] = None
        name = student_data.get("name", "")
        kb = [
            [InlineKeyboardButton("➕ " + ("Добавить ещё" if lang == "ru" else "Тағы қосу"), callback_data="prof_add_student")],
            [InlineKeyboardButton("👥 " + ("Мои ученики" if lang == "ru" else "Оқушыларым"),  callback_data="prof_students")],
            [MENU_BTN(lang)],
        ]
        await update.message.reply_text(
            f"✅ *{name}* добавлен в базу!" if lang == "ru" else f"✅ *{name}* базаға қосылды!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
