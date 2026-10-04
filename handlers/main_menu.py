from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import os
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database, free_limit_for

SITE_URL = os.getenv("SITE_URL", "https://docurabot-production.up.railway.app/")

class MainMenuHandler:
    def __init__(self, db: Database):
        self.db = db

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        onboarding_done = user and user.get("role") and (
            (user.get("role") == "kindergarten" and user.get("classes") and user.get("age_group")) or
            (user.get("role") == "teacher" and user.get("classes") and user.get("subject"))
        )
        if not onboarding_done:
            from handlers.onboarding import OnboardingHandler
            await OnboardingHandler(self.db).start(update, context)
            return
        lang = user.get("lang", "ru")
        await self._send_main_menu(update.message.chat_id, context, user_id, lang)

    def _build_menu_text(self, user: dict, lang: str) -> str:
        name = (user.get("name") or "").split()[0] or ("коллега" if lang == "ru" else "әріптес")
        is_kg = user.get("role") == "kindergarten"
        subscribed = bool(user.get("subscribed"))
        free_used  = user.get("free_used", 0)
        total_free = free_limit_for(user)
        free_left  = max(0, total_free - free_used)
        bonus_docs = user.get("bonus_docs", 0) or 0
        role_label = "Воспитатель" if is_kg else "Учитель"
        role_label_kz = "Тәрбиеші" if is_kg else "Мұғалім"
        org = user.get("school") or ("Детский сад" if is_kg else "Школа")
        spec = user.get("age_group") if is_kg else user.get("subject", "")

        if subscribed:
            status_line = "⭐ *Docura PRO* — безлимитный доступ активен" if lang == "ru" else "⭐ *Docura PRO* — шексіз қолжетімділік белсенді"
        else:
            status_line = f"🆓 *Бесплатный доступ:* осталось *{free_left}/{total_free}* документов" if lang == "ru" else f"🆓 *Тегін қолжетімділік:* қалды *{free_left}/{total_free}* құжат"
            if bonus_docs:
                status_line += f" (+{bonus_docs} бонус)"

        if lang == "ru":
            return (
                f"✨ *Здравствуйте, {name}!* ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💼 *{role_label}*: {org}\n"
                f"📚 *{'Группа' if is_kg else 'Предмет'}*: {spec or '—'}\n"
                f"{status_line}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🤖 *Docura — ваш персональный ИИ-методист:*\n"
                f"• Составляет КСП, циклограммы, СОР/СОЧ по стандартам РК\n"
                f"• Помнит ваших учеников и подставляет их в документы\n"
                f"• Проверяет расписание и готовит планы заблаговременно\n"
                f"• Принимает голосовые сообщения — говорите, я запишу\n\n"
                f"Выберите действие ниже или напишите запрос в чат 👇"
            )
        else:
            return (
                f"✨ *Қош келдіңіз, {name}!* ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💼 *{role_label_kz}*: {org}\n"
                f"📚 *{'Топ' if is_kg else 'Пән'}*: {spec or '—'}\n"
                f"{status_line}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🤖 *Docura — сіздің дербес ЖИ-әдіскеріңіз:*\n"
                f"• ҚМЖ, циклограмма, БЖБ/ТЖБ ҚР стандарттары бойынша дайындайды\n"
                f"• Оқушыларыңыздың базасын сақтап, құжатқа автоматты қосады\n"
                f"• Кестені әр 6 сағат сайын бақылап отырады\n"
                f"• Мәтін және дауыстық хабарламаларды қабылдайды\n\n"
                f"Төмендегі бөлімді таңдаңыз немесе чатқа жазыңыз 👇"
            )

    async def _send_main_menu(self, chat_id, context, user_id, lang):
        user = await self.db.get_user(user_id) or {}
        is_kg = user.get("role") == "kindergarten"
        menu_text = self._build_menu_text(user, lang)
        keyboard = self._main_keyboard(lang, is_kg)
        await context.bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    def _main_keyboard(self, lang: str, is_kg: bool = False):
        students_title = "👥 " + ("Мои воспитанники" if is_kg and lang == "ru" else "Мои ученики" if lang == "ru" else "Менің оқушыларым")
        schedule_title = "📅 " + ("Режим дня" if is_kg and lang == "ru" else "Расписание" if lang == "ru" else "Кесте")
        return [
            [InlineKeyboardButton("📄 " + ("Создать документ" if lang == "ru" else "Құжат жасау"), callback_data="menu_create")],
            [
                InlineKeyboardButton(students_title, callback_data="prof_students"),
                InlineKeyboardButton(schedule_title, callback_data="agent_schedule"),
            ],
            [
                InlineKeyboardButton("💻 " + ("Личный кабинет" if lang == "ru" else "Жеке кабинет"), callback_data="prof_cabinet"),
                InlineKeyboardButton("⭐ " + ("Тарифы PRO" if lang == "ru" else "PRO тарифтер"), callback_data="prof_sub"),
            ],
            [
                InlineKeyboardButton("👤 " + ("Мой профиль" if lang == "ru" else "Менің профилім"), callback_data="menu_profile"),
                InlineKeyboardButton("📚 " + ("История" if lang == "ru" else "Тарих"), callback_data="menu_history"),
            ],
            [
                InlineKeyboardButton("❓ " + ("Инструкция" if lang == "ru" else "Нұсқаулық"), callback_data="menu_help"),
            ],
        ]

    def _settings_keyboard(self, lang, is_kg):
        return [
            [InlineKeyboardButton("💻 Личный веб-кабинет" if lang == "ru" else "💻 Жеке веб-кабинет", callback_data="prof_cabinet")],
            [InlineKeyboardButton("👤 Мой профиль" if lang == "ru" else "👤 Менің профилім", callback_data="menu_profile")],
            [InlineKeyboardButton("🔔 Напоминания" if lang == "ru" else "🔔 Еске салғыштар", callback_data="agent_reminders")],
            [InlineKeyboardButton("⭐ Тариф и подписка" if lang == "ru" else "⭐ Тариф және жазылым", callback_data="prof_sub")],
            [InlineKeyboardButton("🌐 Сменить язык" if lang == "ru" else "🌐 Тілді өзгерту", callback_data="prof_lang")],
            [InlineKeyboardButton("💬 Написать разработчику" if lang == "ru" else "💬 Әзірлеушіге жазу", callback_data="menu_feedback")],
            [InlineKeyboardButton("← Назад" if lang == "ru" else "← Артқа", callback_data="menu_main")],
        ]

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"

        if data == "menu_create":
            await self._show_categories(query, lang, user)

        elif data == "menu_history":
            await self._show_history(query, user_id, lang)

        elif data == "menu_profile":
            from handlers.profile import ProfileHandler
            await ProfileHandler(self.db).show(query, user_id, lang)

        elif data == "menu_settings":
            await query.edit_message_text(
                "⚙️ *Настройки*" if lang == "ru" else "⚙️ *Баптаулар*",
                reply_markup=InlineKeyboardMarkup(self._settings_keyboard(lang, user.get("role") == "kindergarten")),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "menu_invite":
            await self._show_invite(query, context, user_id, user, lang)

        elif data == "menu_feedback":
            # Переиспользуем уже рабочий обработчик жалоб/отзывов из ProfileHandler
            # (step "prof_complaint" маршрутизируется в bot.py по префиксу "prof_").
            # Раньше здесь стоял несуществующий шаг "feedback_waiting", который
            # никто нигде не обрабатывал — кнопка "Написать разработчику" молча
            # ничего не делала, сообщение пользователя просто терялось.
            context.user_data["step"] = "prof_complaint"
            await query.edit_message_text(
                "Напишите ваш вопрос или отзыв." if lang == "ru" else "Сұрағыңызды жазыңыз."
            )

        elif data == "menu_help":
            keyboard = [
                [InlineKeyboardButton("🌐 Личный кабинет (Сайт)" if lang == "ru" else "🌐 Жеке кабинет (Сайт)", url=SITE_URL)],
                [InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]
            ]
            if lang == "ru":
                help_text = (
                    "📖 *Подробная инструкция по работе с Docura*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 *1. Ваш персональный ИИ-ассистент:*\n"
                    "• Вам не обязательно ходить по меню — просто напишите любой вопрос или надиктуйте голосовое в чат (например: _«Подготовь КСП по математике 6 класс на завтра»_ или _«Проверь мое расписание на пятницу»_).\n\n"
                    "📄 *2. Создание документов по стандартам МОН РК:*\n"
                    "• Нажмите *«Создать документ»* → выберите категорию и нужный шаблон.\n"
                    "• Ответьте на краткие уточняющие вопросы (или надиктуйте голосом).\n"
                    "• Бот сформирует официальный Word (.docx) документ, готовый к печати или сдаче завучу.\n\n"
                    "👥 *3. База учащихся / воспитанников:*\n"
                    "• Загрузите учеников в разделе *«Мои ученики»* (списком или фото журнала).\n"
                    "• При составлении характеристик, карт развития и писем данные и оценки подставятся автоматически.\n\n"
                    "📅 *4. Расписание и умный мониторинг:*\n"
                    "• Отправьте фото или текст расписания уроков.\n"
                    "• ИИ-агент каждые 6 часов мониторит ваше расписание и заранее напоминает о планах к урокам.\n\n"
                    "🎁 *5. Бонусы за приглашение коллег:*\n"
                    "• Приглашайте коллег по персональной ссылке (/invite) — получайте +5 документов за каждого!"
                )
            else:
                help_text = (
                    "📖 *Docura жүйесін пайдалану бойынша толық нұсқаулық*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 *1. Сіздің дербес ЖИ-көмекшіңіз:*\n"
                    "• Мәзірді ашу міндетті емес — кез келген сұрақты немесе дауыстық хабарламаны чатқа жаза салыңыз (мысалы: _«Ертеңге 6-сынып математика бойынша ҚМЖ дайында»_ немесе _«Жұмадағы кестемді тексер»_).\n\n"
                    "📄 *2. ҚР стандарттарына сай ресми құжаттар:*\n"
                    "• *«Құжат жасау»* батырмасын басып, санат пен қажетті үлгіні таңдаңыз.\n"
                    "• Қысқа сұрақтарға жауап беріңіз немесе дауыспен айтыңыз.\n"
                    "• Бот басып шығаруға немесе оқу ісі меңгерушісіне тапсыруға дайын Word (.docx) файлын береді.\n\n"
                    "👥 *3. Оқушылар / тәрбиеленушілер базасы:*\n"
                    "• *«Менің оқушыларым»* бөлімінде балаларды қосыңыз (тізім не журнал фотосы).\n"
                    "• Мінездеме, даму карталары мен хаттар жасағанда олардың деректері автоматты қосылады.\n\n"
                    "📅 *4. Сабақ кестесі және мониторинг:*\n"
                    "• Сабақ кестесінің фотосын немесе мәтінін жіберіңіз.\n"
                    "• ЖИ-агент әр 6 сағат сайын кестені бақылап, сабақ жоспарларын алдын ала ескертіп отырады.\n\n"
                    "🎁 *5. Әріптестерді шақыру бонустары:*\n"
                    "• Жеке сілтемемен (/invite) әріптестеріңізді шақырып, әр адам үшін +5 құжат алыңыз!"
                )
            await query.edit_message_text(
                help_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "menu_main":
            menu_text = self._build_menu_text(user, lang)
            keyboard = self._main_keyboard(lang, user.get("role") == "kindergarten")
            await query.edit_message_text(
                menu_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    async def _show_categories(self, query, lang, user):
        """
        ВАЖНО: категории теперь зависят от роли пользователя.
        Раньше здесь всегда показывались школьные категории (cat_planning/cat_reports/...)
        независимо от роли — из-за этого у воспитателей документы выходили "школьными",
        а категория для садика (cat_kg_*) не имела списка документов.
        """
        is_kg = user.get("role") == "kindergarten"

        if is_kg:
            keyboard = [
                [InlineKeyboardButton(t(lang, "cat_kg_planning"), callback_data="cat_kg_planning")],
                [InlineKeyboardButton(t(lang, "cat_kg_reports"),  callback_data="cat_kg_reports")],
                [InlineKeyboardButton(t(lang, "cat_kg_children"), callback_data="cat_kg_children")],
                [InlineKeyboardButton(t(lang, "cat_kg_personal"), callback_data="cat_kg_personal")],
                [InlineKeyboardButton(t(lang, "cat_common"),      callback_data="cat_common")],
            ]
        else:
            is_ct = user.get("is_class_teacher", 0)
            keyboard = [
                [InlineKeyboardButton(t(lang, "cat_planning"),  callback_data="cat_planning")],
                [InlineKeyboardButton(t(lang, "cat_reports"),   callback_data="cat_reports")],
            ]
            if is_ct:
                keyboard.append([InlineKeyboardButton(t(lang, "cat_students"), callback_data="cat_students")])
            keyboard.append([InlineKeyboardButton(t(lang, "cat_personal"), callback_data="cat_personal")])
            keyboard.append([InlineKeyboardButton(t(lang, "cat_common"),   callback_data="cat_common")])

        keyboard.append([InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")])

        if lang == "ru":
            cat_text = (
                "📁 *Каталог методических документов*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Все шаблоны составлены строго по нормам Министерства просвещения РК.\n\n"
                "💡 *Умный ввод:* Вы можете выбрать категорию по кнопкам ниже или *просто надиктовать голосовое / написать тему в чат* — ИИ сразу поймёт вас!\n\n"
                "Выберите нужный раздел:"
            )
        else:
            cat_text = (
                "📁 *Әдістемелік құжаттар каталогы*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Барлық үлгілер ҚР Оқу-ағарту министрлігінің мемлекеттік стандарттарына сәйкес келеді.\n\n"
                "💡 *Ақылды енгізу:* Төмендегі санатты таңдауыңызға немесе *чатқа дауыспен/мәтінмен тақырыпты жаза салуыңызға* болады — ЖИ бірден түсінеді!\n\n"
                "Қажетті бөлімді таңдаңыз:"
            )

        await query.edit_message_text(
            cat_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _show_invite(self, query, context, user_id, user, lang):
        """Та же реферальная карточка, что и команда /invite — но доступна по кнопке
        (например, из пейвола, когда закончились бесплатные документы)."""
        ref_code = user.get("ref_code")
        if not ref_code:
            ref_code = await self.db.generate_unique_ref_code()
            await self.db.upsert_user(user_id, {"ref_code": ref_code})

        bot_username = context.bot.username
        if not bot_username:
            me = await context.bot.get_me()
            bot_username = me.username

        link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
        referrals_count = await self.db.count_referrals(user_id)
        bonus_docs = user.get("bonus_docs", 0) or 0

        text = (
            f"🎁 *Пригласи коллегу — получи документы!*\n\n"
            f"За каждого коллегу, который зарегистрируется по вашей ссылке:\n"
            f"• Вам — *+5 документов* на баланс\n"
            f"• Ему — *+2 бесплатных документа* при старте (итого 5 вместо 3)\n\n"
            f"🔗 Ваша персональная ссылка:\n`{link}`\n\n"
            f"👥 Приглашено (зарегистрировалось): *{referrals_count}*\n"
            f"📄 Бонусных документов начислено всего: *{bonus_docs}*"
        ) if lang == "ru" else (
            f"🎁 *Әріптесіңізді шақырыңыз — құжаттар алыңыз!*\n\n"
            f"Сіздің сілтеме бойынша тіркелген әр әріптес үшін:\n"
            f"• Сізге — *+5 құжат*\n"
            f"• Оған — *+2 тегін құжат* (3 орнына 5)\n\n"
            f"🔗 Сіздің жеке сілтемеңіз:\n`{link}`\n\n"
            f"👥 Шақырылғандар (тіркелген): *{referrals_count}*\n"
            f"📄 Барлық бонус құжаттар: *{bonus_docs}*"
        )
        keyboard = [[InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_history(self, query, user_id, lang):
        docs = await self.db.get_history(user_id, limit=15)
        if not docs:
            keyboard = [[InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]]
            await query.edit_message_text(
                t(lang, "history_title") + "\n\n" + t(lang, "history_empty"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        lines = [t(lang, "history_title"), ""]
        for d in docs[:10]:
            date = d["created_at"][:10]
            lines.append(f"📄 *{d['doc_name']}*\n📅 {date} | ⭐ {d['score']}/100")
        text = "\n".join(lines)

        keyboard = [[InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
