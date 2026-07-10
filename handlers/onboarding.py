from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

MENU_BTN   = lambda lang: InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")
CANCEL_BTN = lambda lang: InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="menu_main")

class OnboardingHandler:
    def __init__(self, db: Database):
        self.db = db

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)

        if user and user.get("name") and user.get("school"):
            context.user_data.clear()
            lang = user.get("lang", "ru")
            from handlers.main_menu import MainMenuHandler
            await MainMenuHandler(self.db).show(update, context)
            return

        # ── Реферальный код: /start ref_XXXXX ──
        # Записываем только для НОВЫХ (ещё не зарегистрированных) пользователей,
        # и только один раз — чтобы уже привязанного реферера нельзя было перезаписать
        # повторным переходом по чужой ссылке.
        if context.args and not (user and user.get("referred_by")):
            raw_arg = context.args[0]
            ref_code = raw_arg[4:] if raw_arg.startswith("ref_") else raw_arg
            referrer = await self.db.get_user_by_ref_code(ref_code)
            if referrer and referrer["tg_id"] != user_id:
                await self.db.upsert_user(user_id, {"referred_by": referrer["tg_id"]})

        keyboard = [[
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang_kz"),
        ]]
        await update.message.reply_text(
            t("ru", "choose_lang"),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"
        context.user_data.clear()
        msg = "❌ Отменено. Возвращаю в главное меню..." if lang == "ru" else "❌ Болдырылмады. Басты мәзірге оралуда..."
        await update.message.reply_text(msg)
        from handlers.main_menu import MainMenuHandler
        await MainMenuHandler(self.db).show(update, context)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = update.effective_user.id

        if data.startswith("lang_"):
            lang = data.split("_")[1]
            await self.db.upsert_user(user_id, {"lang": lang})
            await self._show_onboard(query, context, lang, 1)

        elif data.startswith("onboard_"):
            user = await self.db.get_user(user_id)
            lang = user.get("lang", "ru") if user else "ru"
            slide = int(data.split("_")[1])
            if slide <= 3:
                await self._show_onboard(query, context, lang, slide)
            else:
                # Показываем выбор роли перед регистрацией
                await self._show_role_select(query, lang)

        elif data.startswith("role_select_"):
            role = data.split("role_select_")[1]  # teacher или kindergarten
            await self.db.upsert_user(user_id, {"role": role})
            user = await self.db.get_user(user_id)
            lang = user.get("lang", "ru") if user else "ru"
            await self._start_registration(query, context, lang, role)

        elif data.startswith("role_class_"):
            user = await self.db.get_user(user_id)
            lang = user.get("lang", "ru") if user else "ru"
            is_ct = data == "role_class_yes"
            await self.db.upsert_user(user_id, {"is_class_teacher": 1 if is_ct else 0})
            if is_ct:
                context.user_data["step"] = "reg_class_name"
                kb = [[CANCEL_BTN(lang)]]
                await query.edit_message_text(
                    t(lang, "reg_class_name"),
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await self._finish_registration(query, context, user_id, lang)

    async def _show_role_select(self, query, lang):
        if lang == "ru":
            text = "👋 Кто вы?\n\nВыберите вашу роль:"
            keyboard = [
                [InlineKeyboardButton("🏫 Учитель школы",        callback_data="role_select_teacher")],
                [InlineKeyboardButton("🧸 Воспитатель садика",   callback_data="role_select_kindergarten")],
            ]
        else:
            text = "👋 Сіз кімсіз?\n\nРөліңізді таңдаңыз:"
            keyboard = [
                [InlineKeyboardButton("🏫 Мектеп мұғалімі",      callback_data="role_select_teacher")],
                [InlineKeyboardButton("🧸 Балабақша тәрбиешісі", callback_data="role_select_kindergarten")],
            ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_onboard(self, query, context, lang, slide):
        titles = {1: t(lang, "onboard_1_title"), 2: t(lang, "onboard_2_title"), 3: t(lang, "onboard_3_title")}
        texts  = {1: t(lang, "onboard_1_text"),  2: t(lang, "onboard_2_text"),  3: t(lang, "onboard_3_text")}
        dots   = {1: "●○○", 2: "○●○", 3: "○○●"}

        if slide < 3:
            keyboard = [[InlineKeyboardButton(t(lang, "next") + " →", callback_data=f"onboard_{slide+1}")]]
        else:
            keyboard = [[InlineKeyboardButton("🚀 " + t(lang, "start_reg"), callback_data="onboard_4")]]

        text = f"*{titles[slide]}*\n\n{texts[slide]}\n\n{dots[slide]}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _start_registration(self, query, context, lang, role="teacher"):
        if role == "kindergarten":
            # Для садика — отдельная анкета без класс-руководства и школьных полей
            context.user_data["step"] = "reg_name"
            context.user_data["role"] = "kindergarten"
            kb = [[CANCEL_BTN(lang)]]
            text = "👤 Ваше ФИО?\n\n_Пример: Айгуль Сейткали_" if lang == "ru" else "👤 Аты-жөніңіз?\n\n_Мысалы: Айгуль Сейткали_"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            context.user_data["step"] = "reg_name"
            kb = [[CANCEL_BTN(lang)]]
            await query.edit_message_text(
                t(lang, "reg_name"),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"
        role = user.get("role", "teacher") if user else context.user_data.get("role", "teacher")
        step = context.user_data.get("step", "")
        text = update.message.text.strip()

        cancel_kb = [[CANCEL_BTN(lang)]]

        if len(text) < 2:
            await update.message.reply_text(t(lang, "val_too_short"), reply_markup=InlineKeyboardMarkup(cancel_kb))
            return
        if len(text) > 200:
            await update.message.reply_text(t(lang, "val_too_long"), reply_markup=InlineKeyboardMarkup(cancel_kb))
            return

        if role == "kindergarten":
            # Отдельный (упрощённый) онбординг для садика:
            # ФИО -> название садика -> возрастная группа -> должность -> заведующая
            kg_steps = {
                "reg_name": (
                    "name", "reg_school",
                    "🏫 Название детского сада?" if lang == "ru" else "🏫 Балабақшаның атауы?"
                ),
                "reg_school": (
                    "school", "reg_age_group",
                    ("👶 Какая возрастная группа?\n\n_Пример: младшая группа (3-4 года), средняя группа (4-5 лет)_"
                     if lang == "ru" else
                     "👶 Қандай жас тобы?\n\n_Мысалы: кіші топ (3-4 жас)_")
                ),
                "reg_age_group": (
                    "age_group", "reg_position",
                    ("💼 Ваша должность?\n\n_Пример: воспитатель старшей группы_"
                     if lang == "ru" else "💼 Лауазымыңыз?")
                ),
                "reg_position": (
                    "position", "reg_director",
                    ("👔 ФИО заведующей?\n\n_Пример: Иванова А.Б._"
                     if lang == "ru" else "👔 Меңгерушінің аты-жөні?")
                ),
            }
            if step in kg_steps:
                field, next_step, next_q = kg_steps[step]
                await self.db.upsert_user(user_id, {field: text})
                context.user_data["step"] = next_step
                await update.message.reply_text(next_q, reply_markup=InlineKeyboardMarkup(cancel_kb), parse_mode=ParseMode.MARKDOWN)
            elif step == "reg_director":
                await self.db.upsert_user(user_id, {"director": text})
                await self._finish_registration(None, context, user_id, lang, update=update)
        else:
            # Стандартный онбординг для учителей школы
            steps_flow = {
                "reg_name":     ("name",     "reg_school",   t(lang, "reg_school")),
                "reg_school":   ("school",   "reg_subject",  t(lang, "reg_subject")),
                "reg_subject":  ("subject",  "reg_classes",  t(lang, "reg_classes")),
                "reg_classes":  ("classes",  "reg_position", t(lang, "reg_position")),
                "reg_position": ("position", "reg_director", t(lang, "reg_director")),
            }

            if step in steps_flow:
                field, next_step, next_q = steps_flow[step]
                await self.db.upsert_user(user_id, {field: text})
                context.user_data["step"] = next_step
                await update.message.reply_text(next_q, reply_markup=InlineKeyboardMarkup(cancel_kb), parse_mode=ParseMode.MARKDOWN)

            elif step == "reg_director":
                await self.db.upsert_user(user_id, {"director": text})
                context.user_data["step"] = "reg_class_teacher"
                keyboard = [[
                    InlineKeyboardButton("✅ " + t(lang, "yes"), callback_data="role_class_yes"),
                    InlineKeyboardButton("❌ " + t(lang, "no"),  callback_data="role_class_no"),
                ]]
                await update.message.reply_text(
                    t(lang, "reg_is_class_teacher"),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )

            elif step == "reg_class_name":
                await self.db.upsert_user(user_id, {"classes": text})
                await self._finish_registration(None, context, user_id, lang, update=update)

    async def _finish_registration(self, query, context, user_id, lang, update=None):
        context.user_data["step"] = None
        user = await self.db.get_user(user_id)
        name = (user.get("name") or "").split()[0]
        role = user.get("role", "teacher")

        # ── Реферальная награда: начисляется один раз, сразу после завершения регистрации ──
        if user.get("referred_by") and not user.get("ref_rewarded") and user["referred_by"] != user_id:
            referrer_id = user["referred_by"]
            referrer = await self.db.get_user(referrer_id)
            if referrer:
                await self.db.add_bonus_docs(user_id, 2)        # новичку +2 бесплатных документа
                await self.db.add_bonus_docs(referrer_id, 5)   # пригласившему +5 документов
                await self.db.mark_ref_rewarded(user_id)
                try:
                    ref_lang = referrer.get("lang", "ru")
                    ref_msg = (
                        "🎉 *По вашей ссылке зарегистрировался новый пользователь!*\n\n"
                        "Вам начислено *+5 документов* на баланс. Спасибо, что делитесь Docura.kz!"
                    ) if ref_lang == "ru" else (
                        "🎉 *Сіздің сілтеме бойынша жаңа пайдаланушы тіркелді!*\n\n"
                        "Балансыңызға *+5 құжат* қосылды. Docura.kz-ты бөлескеніңіз үшін рахмет!"
                    )
                    await context.bot.send_message(chat_id=referrer_id, text=ref_msg, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass  # реферер мог заблокировать бота — не мешаем регистрации новичка

        if role == "kindergarten":
            msg = f"✅ Профиль сохранён! Добро пожаловать, {name}! 🧸" if lang == "ru" else f"✅ Профиль сақталды! Қош келдіңіз, {name}! 🧸"
        else:
            msg = t(lang, "reg_done", name=name)

        from handlers.main_menu import MainMenuHandler
        mm = MainMenuHandler(self.db)

        if query:
            await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
            await mm._send_main_menu(query.message.chat_id, context, user_id, lang)
        elif update:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            await mm._send_main_menu(update.message.chat_id, context, user_id, lang)
