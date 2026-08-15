import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
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

        # Зарегистрированным пользователям не показываем игровой онбординг снова.
        if user and user.get("role") and ((user.get("role") == "kindergarten" and user.get("age_group")) or (user.get("role") == "teacher" and user.get("classes") and user.get("subject"))):
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
                await self.db.upsert_user(user_id, {"referred_by": referrer["tg_id"]})

        context.user_data.clear()
        context.user_data["onboard_step"] = 0
        await update.message.reply_text(
            "👋 Привет! Я *AI-агент Docura* — ваш персональный помощник для создания официальных документов.\n\n"
            "📄 КСП, КТП, СОР/СОЧ — для учителей\n"
            "🧸 Циклограммы, тематические планы — для воспитателей\n"
            "📝 Характеристики, отчёты, заявления — всё по МОН РК\n\n"
            "🎁 *3 документа бесплатно* — без карты\n\nСоздайте профиль чтобы начать:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"), InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang_kz")],
                [InlineKeyboardButton("🌐 Открыть сайт", url=SITE_URL)],
            ]),
            parse_mode=ParseMode.MARKDOWN,
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
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"

        # Единственный сценарий онбординга (пошаговый: язык → роль → как это
        # работает → анкета по полям → подтверждение профиля → финиш).
        if data in {"lang_ru", "lang_kz"}:
            lang = data.rsplit("_", 1)[1]
            await self.db.upsert_user(user_id, {"lang": lang, "lang_selected": 1})
            context.user_data["onboard_step"] = 1
            await self._show_role_step(query, lang)
            return
        if data.startswith("onboard_role_"):
            role = data.rsplit("_", 1)[1]
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
        user = await self.db.get_user(user_id)
        name = (user.get("name") or "").split()[0]
        context.user_data.clear()
        text = (f"🎉 *Добро пожаловать, {name}!*\n\nЯ — ваш AI-ассистент. Давайте прямо сейчас создадим ваш первый документ!\n\nПросто напишите мне, например:\n• «Сделай КСП по математике для 7 класса»\n• «Нужна характеристика на ученика»\n• «Создай циклограмму на эту неделю»\n\nИли выберите из меню 👇" if lang == "ru" else f"🎉 *Қош келдіңіз, {name}!*\n\nМен сіздің AI-көмекшіңізмін. Бірінші құжатты қазір жасайық!\n\nМаған жай жазыңыз, мысалы:\n• «7 сынып математикасына ҚМЖ жаса»\n• «Оқушыға мінездеме керек»\n• «Осы аптаға циклограмма жаса»\n\nНемесе мәзірден таңдаңыз 👇")
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Создать первый документ" if lang == "ru" else "📄 Бірінші құжатты жасау", callback_data="menu_create")],
            [InlineKeyboardButton("🌐 Наш сайт" if lang == "ru" else "🌐 Біздің сайт", url=SITE_URL)],
            [InlineKeyboardButton("🗺 Посмотреть все функции" if lang == "ru" else "🗺 Барлық функцияларды көру", callback_data="menu_help")],
        ]), parse_mode=ParseMode.MARKDOWN)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        lang = user.get("lang", "ru") if user else "ru"
        role = user.get("role", "teacher") if user else context.user_data.get("role", "teacher")
        step = context.user_data.get("step", "")
        text = update.message.text.strip()

        if step == "onboard_registration":
            if len(text) < 2 or len(text) > 200:
                await update.message.reply_text(
                    t(lang, "val_too_short") if len(text) < 2 else t(lang, "val_too_long")
                )
                return
            role = context.user_data.get("onboard_role", role)
            fields = self._registration_fields(lang, role)
            idx = context.user_data.get("onboard_reg_index", 0)
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
                await update.message.reply_text(next_question, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
            else:
                await self._show_profile_confirmation(update.message, context, user_id, lang)
            return
