import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from handlers.texts import t
from database import Database

SITE_URL = os.getenv("SITE_URL", "https://docurakz.vercel.app/")

MENU_BTN   = lambda lang: InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")
CANCEL_BTN = lambda lang: InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="menu_main")

class OnboardingHandler:
    def __init__(self, db: Database):
        self.db = db

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)

        # После первого результата показываем обычное меню, сохраняя быстрый путь
        # для тех, кто ещё не создал ни одного документа.
        if user and user.get("role") and (user.get("free_used") or 0) > 0:
            context.user_data.clear()
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
                # Начисляем приглашённому +2 бонусных документа при старте (итого 5 вместо 3)
                await self.db.upsert_user(user_id, {"referred_by": referrer["tg_id"], "bonus_docs": 2})

        message = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if not message:
            return

        telegram_lang = (getattr(update.effective_user, "language_code", "") or "").lower()
        lang = (user or {}).get("lang") or ("kz" if telegram_lang.startswith(("kk", "kz")) else "ru")
        await self.db.upsert_user(user_id, {"lang": lang, "lang_selected": 1})
        await self.db.log_funnel_event(user_id, "start")
        context.user_data.clear()
        context.user_data["onboard_step"] = 0
        context.user_data["onboard_quick_start"] = True
        text = (
            "👋 Привет! Я Docura — помогу подготовить документ Word.\n\n"
            "Выберите, что нужно сделать. Затем выберите язык документа и ответьте на несколько коротких вопросов.\n\n"
            "🎁 Первые 3 документа бесплатно"
        ) if lang == "ru" else (
            "👋 Сәлем! Мен Docura — Word құжатын дайындауға көмектесемін.\n\n"
            "Қажетті құжатты таңдаңыз. Содан кейін құжат тілін таңдап, бірнеше қысқа сұраққа жауап беріңіз.\n\n"
            "🎁 Алғашқы 3 құжат тегін"
        )
        await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📘 КСП" if lang == "ru" else "📘 ҚМЖ", callback_data="onboard_doc_ksp")],
                [InlineKeyboardButton("🧸 Циклограмма", callback_data="onboard_doc_cycle")],
                [InlineKeyboardButton("👤 Характеристика", callback_data="onboard_doc_characteristic")],
                [InlineKeyboardButton("📄 Другое" if lang == "ru" else "📄 Басқа құжат", callback_data="onboard_doc_other")],
            ]),
        )

    async def _show_quick_role(self, query, lang):
        text = (
            "Чтобы подобрать нужный документ, выберите место работы:"
            if lang == "ru" else
            "Қажетті құжатты таңдау үшін жұмыс орныңызды көрсетіңіз:"
        )
        keyboard = [[
            InlineKeyboardButton("🏫 Школа" if lang == "ru" else "🏫 Мектеп", callback_data="onboard_role_teacher"),
            InlineKeyboardButton("🧸 Детский сад" if lang == "ru" else "🧸 Балабақша", callback_data="onboard_role_kindergarten"),
        ], [InlineKeyboardButton("← Назад" if lang == "ru" else "← Артқа", callback_data="onboard_quick_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def _launch_first_document(self, query, context, user_id, lang, role, doc_type):
        await self.db.upsert_user(user_id, {"lang": lang, "role": role})
        context.user_data["onboard_role"] = role
        context.user_data.pop("onboard_quick_doc", None)
        user = await self.db.get_user(user_id) or {"tg_id": user_id, "lang": lang, "role": role}
        from handlers.documents import DocumentHandler
        await DocumentHandler(self.db, os.getenv("ANTHROPIC_API_KEY", ""))._start_doc(
            query, context, user_id, user, lang, doc_type
        )

    def _post_doc_profile_fields(self, lang, role):
        if lang == "ru":
            school = "Название детского сада" if role == "kindergarten" else "Название школы"
            director = "ФИО заведующей" if role == "kindergarten" else "ФИО директора"
            return [
                ("school", f"🏫 {school}?"),
                ("position", "💼 Ваша должность?"),
                ("director", f"👔 {director}?"),
            ]
        return [
            ("school", "🏫 Мекеменің атауы?"),
            ("position", "💼 Лауазымыңыз?"),
            ("director", "👔 Басшының аты-жөні?"),
        ]

    async def _ask_post_doc_profile_field(self, query, context, user_id, lang):
        user = await self.db.get_user(user_id) or {}
        fields = self._post_doc_profile_fields(lang, user.get("role", "teacher"))
        idx = context.user_data.get("post_doc_profile_index", 0)
        if idx >= len(fields):
            context.user_data["step"] = None
            context.user_data.pop("post_doc_profile_index", None)
            text = "✅ Спасибо! Буду подставлять эти данные в следующие документы." if lang == "ru" else "✅ Рақмет! Бұл деректерді келесі құжаттарға қосамын."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Создать ещё" if lang == "ru" else "📄 Тағы жасау", callback_data="menu_create")],
                [InlineKeyboardButton("🏠 Главное меню" if lang == "ru" else "🏠 Басты мәзір", callback_data="menu_main")],
            ])
            await query.edit_message_text(text, reply_markup=keyboard)
            return
        context.user_data["step"] = "post_doc_profile"
        buttons = [[InlineKeyboardButton(
            "Пропустить остальные поля" if lang == "ru" else "Қалған өрістерді өткізу",
            callback_data="onboard_skip_profile",
        )]]
        buttons.append([InlineKeyboardButton(
            "Пропустить это поле" if lang == "ru" else "Бұл өрісті өткізу",
            callback_data="onboard_profile_skip_field",
        )])
        await query.edit_message_text(
            fields[idx][1] + ("\n\nЕсли не хотите сохранять поле — пропустите." if lang == "ru" else "\n\nСақтағыңыз келмесе, өткізіңіз."),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _drop_chat_task(self, user_id: int):
        """Сбрасывает незавершённую подготовку документа в разговоре с ИИ-агентом."""
        try:
            ctx = await self.db.get_agent_context(user_id)
            state = ctx.get("concierge")
            if state and state.get("doc_task"):
                state["doc_task"] = None
                await self.db.update_agent_context(user_id, {"concierge": state})
        except Exception:
            pass

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"
        context.user_data.clear()
        await self._drop_chat_task(user_id)
        msg = "❌ Отменено. Возвращаю в главное меню..." if lang == "ru" else "❌ Болдырылмады. Басты мәзірге оралуда..."
        message = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if message:
            await message.reply_text(msg)
        from handlers.main_menu import MainMenuHandler
        await MainMenuHandler(self.db).show(update, context)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"

        if data == "onboard_save_profile":
            context.user_data["post_doc_profile_index"] = 0
            await self._ask_post_doc_profile_field(query, context, user_id, lang)
            return
        if data == "onboard_profile_skip_field":
            context.user_data["post_doc_profile_index"] = context.user_data.get("post_doc_profile_index", 0) + 1
            await self._ask_post_doc_profile_field(query, context, user_id, lang)
            return
        if data == "onboard_skip_profile":
            context.user_data["step"] = None
            context.user_data.pop("post_doc_profile_index", None)
            text = "Хорошо, оставлю эти поля пустыми. Их можно добавить в профиле позже." if lang == "ru" else "Жақсы, бұл өрістерді бос қалдырамын. Кейін профильде толтыра аласыз."
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Создать ещё" if lang == "ru" else "📄 Тағы жасау", callback_data="menu_create")],
                [InlineKeyboardButton("🏠 Главное меню" if lang == "ru" else "🏠 Басты мәзір", callback_data="menu_main")],
            ]))
            return

        quick_docs = {
            "onboard_doc_ksp": ("teacher", "lesson_plan"),
            "onboard_doc_cycle": ("kindergarten", "kindergarten_cycle_schedule"),
        }
        if data in quick_docs:
            role, doc_type = quick_docs[data]
            await self._launch_first_document(query, context, user_id, lang, role, doc_type)
            return
        if data in {"onboard_doc_characteristic", "onboard_doc_other"}:
            context.user_data["onboard_quick_doc"] = (
                "characteristic" if data == "onboard_doc_characteristic" else "other"
            )
            await self._show_quick_role(query, lang)
            return
        if data == "onboard_quick_back":
            await query.edit_message_text(
                "Выберите документ:" if lang == "ru" else "Құжатты таңдаңыз:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📘 КСП" if lang == "ru" else "📘 ҚМЖ", callback_data="onboard_doc_ksp")],
                    [InlineKeyboardButton("🧸 Циклограмма", callback_data="onboard_doc_cycle")],
                    [InlineKeyboardButton("👤 Характеристика", callback_data="onboard_doc_characteristic")],
                    [InlineKeyboardButton("📄 Другое" if lang == "ru" else "📄 Басқа құжат", callback_data="onboard_doc_other")],
                ]),
            )
            return

        # Единственный сценарий онбординга (пошаговый: язык → роль → как это
        # работает → анкета по полям → подтверждение профиля → финиш).
        if data in {"lang_ru", "lang_kz"}:
            lang = data.rsplit("_", 1)[1]
            # Само поле lang является признаком выбранного языка интерфейса.
            await self.db.upsert_user(user_id, {"lang": lang})
            context.user_data["onboard_step"] = 1
            await self._show_role_step(query, lang)
            return
        if data.startswith("onboard_role_"):
            role = data.rsplit("_", 1)[1]
            quick_choice = context.user_data.get("onboard_quick_doc")
            if quick_choice:
                if quick_choice == "other":
                    await self.db.upsert_user(user_id, {"lang": lang, "role": role})
                    context.user_data["onboard_role"] = role
                    from handlers.documents import DocumentHandler
                    await DocumentHandler(self.db, os.getenv("ANTHROPIC_API_KEY", ""))._show_categories(
                        query, lang, await self.db.get_user(user_id)
                    )
                    return
                doc_type = "characteristic" if role == "teacher" else "kg_child_characteristic"
                await self._launch_first_document(query, context, user_id, lang, role, doc_type)
                return
            await self.db.upsert_user(user_id, {"role": role})
            context.user_data["onboard_role"] = role
            context.user_data["onboard_step"] = 2
            await self._show_how_it_works(query, lang, role)
            return
        if data == "onboard_begin_registration":
            await self._show_registration_question(query, context, lang, 0)
            return
        if data == "onboard_back":
            if context.user_data.get("onboard_step") == 2:
                context.user_data["onboard_step"] = 1
                await self._show_role_step(query, lang)
                return
            idx = context.user_data.get("onboard_reg_index", 0)
            if idx > 0:
                await self._show_registration_question(query, context, lang, idx - 1)
            else:
                context.user_data["onboard_step"] = 2
                await self._show_how_it_works(query, lang, context.user_data.get("onboard_role", "teacher"))
            return
        if data == "onboard_confirm_profile":
            await self._finish_new_onboarding(query, context, user_id, lang)
            return
        if data == "onboard_edit_profile":
            await self._show_registration_question(query, context, lang, 0)
            return
        if data == "onboard_cancel":
            context.user_data.clear()
            await self._show_welcome(query, lang)
            return

    async def _show_welcome(self, query, lang="ru"):
        """Первый экран для новых пользователей; язык выбирается следующей кнопкой."""
        text = (
            "👋 Добро пожаловать в *Docura.kz*!\n\n"
            "Я помогаю учителям и воспитателям Казахстана создавать официальные документы за 30 секунд.\n\n"
            "📄 КСП, КТП, СОР/СОЧ\n🧸 Циклограммы, тематические планы\n📝 Характеристики, отчёты, заявления\n\n"
            "🎁 *3 документа бесплатно* — без регистрации карты"
        ) if lang == "ru" else (
            "👋 *Docura.kz* қызметіне қош келдіңіз!\n\n"
            "Мен Қазақстан мұғалімдері мен тәрбиешілеріне 30 секундта ресми құжаттар жасауға көмектесемін.\n\n"
            "📄 ҚМЖ, КТЖ, БЖБ/ТЖБ\n🧸 Циклограммалар, тақырыптық жоспарлар\n📝 Мінездемелер, есептер, өтініштер\n\n"
            "🎁 *3 құжат тегін* — картасыз"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"), InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang_kz")]]),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _show_role_step(self, query, lang):
        text = (
            "🏫 Где вы работаете?\n\nВыберите чтобы бот настроился именно под вас:\n\n"
            "💡 Если вы воспитатель — выбирайте детский сад. Это важно для правильной генерации документов."
        ) if lang == "ru" else (
            "🏫 Сіз қай жерде жұмыс істейсіз?\n\nБотты дәл өзіңізге реттеу үшін таңдаңыз:\n\n"
            "💡 Егер сіз тәрбиеші болсаңыз — балабақшаны таңдаңыз. Бұл дұрыс құжат жасау үшін маңызды."
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏫 Работаю в школе" if lang == "ru" else "🏫 Мектепте жұмыс істеймін", callback_data="onboard_role_teacher")],
            [InlineKeyboardButton("🧸 Работаю в детском саду" if lang == "ru" else "🧸 Балабақшада жұмыс істеймін", callback_data="onboard_role_kindergarten")],
        ]))

    async def _show_how_it_works(self, query, lang, role):
        if lang == "ru":
            ending = "Циклограммы, тематические планы, мониторинг — всё по требованиям МОН РК." if role == "kindergarten" else "Бот запоминает ваши данные и больше не спрашивает лишнего."
            text = f"📖 *Как это работает?*\n\n1️⃣ Вы заполняете профиль (1 раз)\n2️⃣ Выбираете тип документа\n3️⃣ Отвечаете на 2-3 вопроса\n4️⃣ Получаете готовый Word файл ✅\n\n{ending}\n\n🎁 Первые *3 документа бесплатно*"
            start, back = "✅ Понятно, начинаем!", "← Назад"
        else:
            ending = "Циклограммалар, тақырыптық жоспарлар және мониторинг — бәрі ҚР ОАМ талаптарына сай." if role == "kindergarten" else "Бот деректеріңізді есте сақтайды және артық сұрақ қоймайды."
            text = f"📖 *Бұл қалай жұмыс істейді?*\n\n1️⃣ Профильді бір рет толтырасыз\n2️⃣ Құжат түрін таңдайсыз\n3️⃣ 2-3 сұраққа жауап бересіз\n4️⃣ Дайын Word файлын аласыз ✅\n\n{ending}\n\n🎁 Алғашқы *3 құжат тегін*"
            start, back = "✅ Түсінікті, бастаймыз!", "← Артқа"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(start, callback_data="onboard_begin_registration")],
            [InlineKeyboardButton(back, callback_data="onboard_back")],
        ]), parse_mode=ParseMode.MARKDOWN)

    def _registration_fields(self, lang, role):
        ru = lang == "ru"
        school_word = "детского сада" if role == "kindergarten" else "школы"
        institution = (f"🧸 Название вашего {school_word}:\n_Пример: Ясли-сад №12 «Алтынай» г. Актобе_" if role == "kindergarten" else "🏫 Название вашей школы:\n_Пример: СШ №5 г. Актобе_\n_или: Назарбаев Интеллектуальная школа_") if ru else ("🧸 Балабақшаңыздың атауы:\n_Мысалы: №12 «Алтынай» бөбекжай-бақшасы_" if role == "kindergarten" else "🏫 Мектебіңіздің атауы:\n_Мысалы: №5 ОМ Ақтөбе қ._")
        fields = [
            ("name", "👤 Введите ваше полное ФИО:\n_Пример: Иванова Мария Петровна_" if ru else "👤 Толық аты-жөніңізді енгізіңіз:\n_Мысалы: Иванова Мария Петровна_"),
            ("school", institution),
        ]
        if role == "teacher":
            fields += [
                ("subject", "📚 Какой предмет преподаёте?\n_Пример: Математика_\n_или: Русский язык и литература_" if ru else "📚 Қандай пәннен сабақ бересіз?\n_Мысалы: Математика_"),
                ("classes", "🏷 Какие классы ведёте?\n_Пример: 7А, 8Б, 9В_\n_или: 5-7 классы_" if ru else "🏷 Қандай сыныптарға сабақ бересіз?\n_Мысалы: 7А, 8Б, 9В_"),
            ]
        else:
            fields.append(("age_group", "👶 Ваша возрастная группа:\n_Пример: Старшая группа (5-6 лет)_\n_или: Средняя группа «Солнышко» (4-5 лет)_" if ru else "👶 Сіздің жас тобыңыз:\n_Мысалы: Ересек топ (5-6 жас)_"))
        fields += [
            ("position", "💼 Ваша должность:\n_Пример: учитель математики_\n_или: воспитатель старшей группы_" if ru else "💼 Лауазымыңыз:\n_Мысалы: математика мұғалімі_"),
            ("director", "👔 ФИО заведующей:\n_Пример: Тулегенова Бибигуль Сериковна_" if role == "kindergarten" and ru else "👔 ФИО директора школы:\n_Пример: Сейтқали Асылбек Бекұлы_\n_или: Петрова Анна Ивановна_" if ru else "👔 Басшыңыздың аты-жөні:\n_Мысалы: Төлегенова Бибігүл Серікқызы_"),
        ]
        return fields

    async def _show_registration_question(self, query, context, lang, idx):
        role = context.user_data.get("onboard_role", "teacher")
        fields = self._registration_fields(lang, role)
        if idx >= len(fields):
            user_id = query.from_user.id if getattr(query, "from_user", None) else 0
            await self._show_profile_confirmation(query, context, user_id, lang, edit=True)
            return
        context.user_data["onboard_reg_index"] = idx
        context.user_data["onboard_step"] = 3 + idx
        context.user_data["step"] = "onboard_registration"
        buttons = []
        if idx > 0:
            buttons.append([InlineKeyboardButton("← Назад" if lang == "ru" else "← Артқа", callback_data="onboard_back")])
        buttons.append([InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Болдырмау", callback_data="onboard_cancel")])
        await query.edit_message_text(fields[idx][1], reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)

    async def _show_profile_confirmation(self, message_or_query, context, user_id, lang, edit=False):
        user = await self.db.get_user(user_id)
        role = user.get("role", "teacher")
        if lang == "ru":
            details = f"👤 ФИО: {user.get('name', '')}\n🏫 {'Детский сад' if role == 'kindergarten' else 'Школа'}: {user.get('school', '')}\n"
            details += (f"👶 Группа: {user.get('age_group', '')}\n" if role == "kindergarten" else f"📚 Предмет: {user.get('subject', '')}\n🏷 Классы: {user.get('classes', '')}\n")
            text = f"✅ *Отлично! Проверьте данные:*\n\n{details}💼 Должность: {user.get('position', '')}\n👔 {'Заведующая' if role == 'kindergarten' else 'Директор'}: {user.get('director', '')}\n\nВсё верно?"
            yes, change = "✅ Да, всё верно!", "✏️ Изменить данные"
        else:
            details = f"👤 Аты-жөні: {user.get('name', '')}\n🏫 Ұйым: {user.get('school', '')}\n"
            details += (f"👶 Топ: {user.get('age_group', '')}\n" if role == "kindergarten" else f"📚 Пән: {user.get('subject', '')}\n🏷 Сыныптар: {user.get('classes', '')}\n")
            text = f"✅ *Тамаша! Деректерді тексеріңіз:*\n\n{details}💼 Лауазым: {user.get('position', '')}\n👔 Басшы: {user.get('director', '')}\n\nБарлығы дұрыс па?"
            yes, change = "✅ Иә, бәрі дұрыс!", "✏️ Деректерді өзгерту"
        context.user_data["step"] = "onboard_confirmation"
        context.user_data["onboard_step"] = 9
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(yes, callback_data="onboard_confirm_profile")], [InlineKeyboardButton(change, callback_data="onboard_edit_profile")]])
        if edit:
            await message_or_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await message_or_query.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    async def _finish_new_onboarding(self, query, context, user_id, lang):
        user = await self.db.get_user(user_id) or {}
        name_parts = (user.get("name") or "").split()
        name = name_parts[0] if name_parts else ("коллега" if lang == "ru" else "әріптес")
        name = escape_markdown(name, version=1)
        context.user_data.clear()
        text = (f"🎉 *Добро пожаловать, {name}!*\n\nЯ — ваш AI-ассистент. Давайте прямо сейчас создадим ваш первый документ!\n\nПросто напишите мне, например:\n• «Сделай КСП по математике для 7 класса»\n• «Нужна характеристика на ученика»\n• «Создай циклограмму на эту неделю»\n\nИли выберите из меню 👇" if lang == "ru" else f"🎉 *Қош келдіңіз, {name}!*\n\nМен сіздің AI-көмекшіңізмін. Бірінші құжатты қазір жасайық!\n\nМаған жай жазыңыз, мысалы:\n• «7 сынып математикасына ҚМЖ жаса»\n• «Оқушыға мінездеме керек»\n• «Осы аптаға циклограмма жаса»\n\nНемесе мәзірден таңдаңыз 👇")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        from handlers.main_menu import MainMenuHandler
        await MainMenuHandler(self.db)._send_main_menu(query.message.chat_id, context, user_id, lang)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if not message or not getattr(message, "text", None):
            return
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"
        role = user.get("role", "teacher") if user else context.user_data.get("role", "teacher")
        step = context.user_data.get("step", "")
        text = message.text.strip()

        if step == "post_doc_profile":
            if len(text) > 200:
                await message.reply_text(t(lang, "val_too_long"))
                return
            fields = self._post_doc_profile_fields(lang, (user or {}).get("role", role))
            idx = context.user_data.get("post_doc_profile_index", 0)
            if idx < len(fields) and text.lower() not in {"пропустить", "өткізу", "skip", "-"} and text:
                await self.db.upsert_user(user_id, {fields[idx][0]: text})
            context.user_data["post_doc_profile_index"] = idx + 1
            from handlers.query_adapter import MessageQueryAdapter
            await self._ask_post_doc_profile_field(MessageQueryAdapter(message), context, user_id, lang)
            return

        if step == "onboard_registration":
            if len(text) < 2 or len(text) > 200:
                await message.reply_text(
                    t(lang, "val_too_short") if len(text) < 2 else t(lang, "val_too_long")
                )
                return
            role = context.user_data.get("onboard_role", role)
            fields = self._registration_fields(lang, role)
            idx = context.user_data.get("onboard_reg_index", 0)
            if idx >= len(fields):
                await self._show_profile_confirmation(message, context, user_id, lang)
                return
            field = fields[idx][0]
            values = {field: text}
            # В существующей модели classes также используется как название группы.
            if role == "kindergarten" and field == "age_group":
                values["classes"] = text
            await self.db.upsert_user(user_id, values)
            if idx + 1 < len(fields):
                context.user_data["onboard_reg_index"] = idx + 1
                context.user_data["onboard_step"] = 3 + idx + 1
                context.user_data["step"] = "onboard_registration"
                next_question = fields[idx + 1][1]
                buttons = [[InlineKeyboardButton("← Назад" if lang == "ru" else "← Артқа", callback_data="onboard_back")], [InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Болдырмау", callback_data="onboard_cancel")]]
                await message.reply_text(next_question, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
            else:
                await self._show_profile_confirmation(message, context, user_id, lang)
            return

        elif step == "onboard_confirmation":
            from handlers.query_adapter import MessageQueryAdapter
            lowered = text.lower()
            if any(w in lowered for w in ["да", "иә", "дұрыс", "верно", "ок", "ok", "yes", "ага", "жарайды"]):
                await self._finish_new_onboarding(MessageQueryAdapter(message), context, user_id, lang)
            elif any(w in lowered for w in ["нет", "жоқ", "изменить", "өзгерт", "қате", "ошибка", "no"]):
                await self._show_registration_question(MessageQueryAdapter(message), context, lang, 0)
            else:
                await self._show_profile_confirmation(message, context, user_id, lang)
            return
