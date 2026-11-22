from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import os
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from handlers.texts import t
from database import Database, free_limit_for

SITE_URL = os.getenv("SITE_URL", "https://docurabot-production.up.railway.app/")

class MainMenuHandler:
    def __init__(self, db: Database):
        self.db = db

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        onboarding_done = user and user.get("role")
        if not onboarding_done:
            from handlers.onboarding import OnboardingHandler
            await OnboardingHandler(self.db).start(update, context)
            return
        lang = user.get("lang", "ru")
        message = getattr(update, "effective_message", None) or getattr(update, "message", None)
        chat_id = message.chat_id if message else update.effective_user.id
        await self._send_main_menu(chat_id, context, user_id, lang)

    def _build_menu_text(self, user: dict, lang: str) -> str:
        name_parts = (user.get("name") or "").split()
        name = escape_markdown(
            name_parts[0] if name_parts else ("коллега" if lang == "ru" else "әріптес"),
            version=1,
        )
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
            # Тот же тариф, что и в профиле (раньше меню всегда писало PRO, а профиль — MAX).
            tier = (user.get("tier") or "pro").lower()
            if tier == "max":
                status_line = "💎 *Docura MAX* — безлимитный доступ активен" if lang == "ru" else "💎 *Docura MAX* — шексіз қолжетімділік белсенді"
            elif tier == "b2b":
                status_line = "🏢 *Docura для организации* — доступ активен" if lang == "ru" else "🏢 *Мекемеге арналған Docura* — қолжетімділік белсенді"
            else:
                status_line = "⭐ *Docura PRO* — безлимитный доступ активен" if lang == "ru" else "⭐ *Docura PRO* — шексіз қолжетімділік белсенді"
        else:
            status_line = f"🆓 *Бесплатный доступ:* осталось *{free_left}/{total_free}* документов" if lang == "ru" else f"🆓 *Тегін қолжетімділік:* қалды *{free_left}/{total_free}* құжат"
            if bonus_docs:
                status_line += f" (+{bonus_docs} бонус)"

        if not free_used:
            if lang == "ru":
                return (
                    f"✨ *Здравствуйте, {name}!* ✨\n"
                    f"{status_line}\n\n"
                    "Нажмите *«Создать документ»*, выберите тип и язык. Я задам несколько коротких вопросов и подготовлю Word-файл."
                )
            return (
                f"✨ *Қош келдіңіз, {name}!* ✨\n"
                f"{status_line}\n\n"
                "*«Құжат жасау»* батырмасын басып, құжат түрі мен тілін таңдаңыз. Бірнеше қысқа сұрақтан кейін Word файлын дайындаймын."
            )

        if lang == "ru":
            return (
                f"✨ *Здравствуйте, {name}!* ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💼 *{role_label}*: {org}\n"
                f"📚 *{'Группа' if is_kg else 'Предмет'}*: {spec or '—'}\n"
                f"{status_line}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Нажмите «Создать документ» или просто напишите, что нужно 👇"
            )
        else:
            return (
                f"✨ *Қош келдіңіз, {name}!* ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💼 *{role_label_kz}*: {org}\n"
                f"📚 *{'Топ' if is_kg else 'Пән'}*: {spec or '—'}\n"
                f"{status_line}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Төмендегі бөлімді таңдаңыз немесе чатқа жазыңыз 👇"
            )

    async def _send_main_menu(self, chat_id, context, user_id, lang):
        user = await self.db.get_user(user_id) or {}
        is_kg = user.get("role") == "kindergarten"
        menu_text = self._build_menu_text(user, lang)
        keyboard = self._main_keyboard(lang, is_kg, show_extras=(user.get("free_used", 0) or 0) > 0)
        await context.bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    def _main_keyboard(self, lang: str, is_kg: bool = False, show_extras: bool = True):
        """Главное меню: каждый раздел ровно один раз.
        Расписание, напоминания, язык, веб-кабинет и поддержка — внутри «Профиля»."""
        ru = lang == "ru"
        create_button = [InlineKeyboardButton("📄 " + ("Создать документ" if ru else "Құжат жасау"), callback_data="menu_create")]
        if not show_extras:
            return [create_button]
        students = ("Воспитанники" if is_kg else "Ученики") if ru else ("Тәрбиеленушілер" if is_kg else "Оқушылар")
        return [
            create_button,
            [
                InlineKeyboardButton("📚 " + ("Мои документы" if ru else "Құжаттарым"), callback_data="menu_history"),
                InlineKeyboardButton("👥 " + students, callback_data="prof_students"),
            ],
            [
                InlineKeyboardButton("👤 " + ("Профиль и настройки" if ru else "Профиль және баптаулар"), callback_data="menu_profile"),
                InlineKeyboardButton("⭐ " + ("Тариф" if ru else "Тариф"), callback_data="prof_sub"),
            ],
            [InlineKeyboardButton("🎁 " + ("+5 документов за коллегу" if ru else "Әріптес үшін +5 құжат"), callback_data="menu_invite")],
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

        elif data.startswith("menu_histdoc_"):
            await self._resend_history_doc(query, user_id, lang, data.rsplit("_", 1)[1])

        elif data == "menu_profile":
            from handlers.profile import ProfileHandler
            await ProfileHandler(self.db).show(query, user_id, lang)

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
                [InlineKeyboardButton(t(lang, "back"), callback_data="menu_main")]
            ]
            if lang == "ru":
                help_text = (
                    "📖 *Подробная инструкция по работе с Docura*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 *1. Ваш персональный ИИ-ассистент:*\n"
                    "• Вам не обязательно ходить по меню — просто напишите любой вопрос или надиктуйте голосовое в чат (например: _«Подготовь КСП по математике 6 класс на завтра»_ или _«Проверь мое расписание на пятницу»_).\n\n"
                    "📄 *2. Создание документов:*\n"
                    "• Нажмите *«Создать документ»* → выберите категорию и нужный шаблон.\n"
                    "• Ответьте на краткие уточняющие вопросы (или надиктуйте голосом).\n"
                    "• Бот подготовит Word (.docx) файл. Перед подачей проверьте его по требованиям вашей организации.\n\n"
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
                    "📄 *2. Құжат дайындау:*\n"
                    "• *«Құжат жасау»* батырмасын басып, санат пен қажетті үлгіні таңдаңыз.\n"
                    "• Қысқа сұрақтарға жауап беріңіз немесе дауыспен айтыңыз.\n"
                    "• Бот Word (.docx) файлын дайындайды. Тапсырар алдында ұйымыңыздың талаптарына сай екенін тексеріңіз.\n\n"
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
            keyboard = self._main_keyboard(
                lang, user.get("role") == "kindergarten", show_extras=(user.get("free_used", 0) or 0) > 0
            )
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
        from handlers.documents import DOC_NAMES
        names = DOC_NAMES.get(lang, DOC_NAMES["ru"])
        # Самые нужные документы — сразу кнопками, остальное — по разделам.
        popular = (["kindergarten_cycle_schedule", "kg_thematic_plan", "kg_child_characteristic"] if is_kg
                   else ["lesson_plan", "characteristic", "sor_soch"])
        keyboard = [[InlineKeyboardButton("⭐ " + names.get(d, d), callback_data=f"doc_{d}")] for d in popular]

        if is_kg:
            cats = ["cat_kg_planning", "cat_kg_reports", "cat_kg_children", "cat_kg_personal", "cat_common"]
        else:
            # «По ученикам» нужны любому учителю (справка об отсутствии, письмо
            # родителям, благодарственное письмо), а не только классному руководителю:
            # раньше раздел был скрыт, и включить его в профиле было нельзя.
            cats = ["cat_planning", "cat_reports", "cat_students", "cat_personal", "cat_common"]
        row = []
        for c in cats:
            row.append(InlineKeyboardButton(t(lang, c), callback_data=c))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(t(lang, "back"), callback_data="menu_main")])

        if lang == "ru":
            cat_text = (
                "📄 *Какой документ сделать?*\n\n"
                "⭐ — самые популярные. Остальные — в разделах ниже.\n"
                "Можно и просто написать в чат: «КСП по математике, 6 класс, тема Дроби»."
            )
        else:
            cat_text = (
                "📄 *Қандай құжат жасаймыз?*\n\n"
                "⭐ — ең танымалдары. Қалғандары — төмендегі бөлімдерде.\n"
                "Чатқа жай жаза салуға да болады: «6 сынып математика ҚМЖ, тақырыбы Бөлшектер»."
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
        keyboard = [[InlineKeyboardButton(t(lang, "back"), callback_data="menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _resend_history_doc(self, query, user_id, lang, raw_id):
        try:
            doc = await self.db.get_document(int(raw_id))
        except (TypeError, ValueError):
            doc = None
        if not doc or doc.get("teacher_id") != user_id:
            await query.answer("Документ не найден" if lang == "ru" else "Құжат табылмады", show_alert=True)
            return
        await query.answer()
        caption = f"📄 *{doc['doc_name']}*"
        if doc.get("file_id"):
            try:
                await query.message.reply_document(document=doc["file_id"], caption=caption, parse_mode=ParseMode.MARKDOWN)
                return
            except Exception as exc:
                print(f"history resend by file_id failed: {exc}")
        # Старые документы (до сохранения file_id): собираем Word заново из сохранённого текста.
        import os as _os
        from handlers.word_generator import generate_word
        user = await self.db.get_user(user_id) or {}
        fname = generate_word(doc["content"], doc["doc_name"], user.get("name") or "",
                              director_name=user.get("director") or "", registry_doc_type=doc.get("doc_type") or "",
                              lang=user.get("lang") or "ru")
        try:
            with open(fname, "rb") as f:
                date = (doc.get("created_at") or "")[:10].replace("-", "")
                await query.message.reply_document(document=f, filename=f"{doc['doc_name']}_{date}.docx",
                                                   caption=caption, parse_mode=ParseMode.MARKDOWN)
        finally:
            try:
                _os.remove(fname)
            except OSError:
                pass

    async def _show_history(self, query, user_id, lang):
        docs = await self.db.get_history(user_id, limit=15)
        if not docs:
            keyboard = [[InlineKeyboardButton(t(lang, "back"), callback_data="menu_main")]]
            await query.edit_message_text(
                t(lang, "history_title") + "\n\n" + t(lang, "history_empty"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Каждый документ — кнопка: нажал и получил тот же Word-файл ещё раз.
        # Внутреннюю оценку качества (score) учителю больше не показываем.
        hint = ("Нажмите на документ, чтобы скачать его ещё раз:" if lang == "ru"
                else "Құжатты қайта жүктеу үшін оны басыңыз:")
        text = t(lang, "history_title") + "\n\n" + hint
        keyboard = []
        for d in docs:
            date = (d.get("created_at") or "")[:16].replace("T", " ")
            dd = f"{date[8:10]}.{date[5:7]} {date[11:16]}" if len(date) >= 16 else date
            label = f"📄 {d['doc_name'][:34]} · {dd}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"menu_histdoc_{d['id']}")])
        keyboard.append([InlineKeyboardButton(t(lang, "back"), callback_data="menu_main")])
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
