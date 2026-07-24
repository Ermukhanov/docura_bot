from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import os
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database, free_limit_for

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

    async def _send_main_menu(self, chat_id, context, user_id, lang):
        user = await self.db.get_user(user_id)
        subscribed = user.get("subscribed", 0)
        free_used  = user.get("free_used", 0)
        total_free = free_limit_for(user)
        free_left  = max(0, total_free - free_used)
        if subscribed:
            status = t(lang, "status_pro")
        else:
            status = t(lang, "status_free", n=free_left, total=total_free)

        keyboard = self._main_keyboard(lang)
        await context.bot.send_message(
            chat_id=chat_id,
            text=t(lang, "main_menu", status=status),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    def _main_keyboard(self, lang):
        """Короткое главное меню: редкие настройки вынесены на второй экран."""
        return [
            [InlineKeyboardButton(t(lang, "btn_create"), callback_data="menu_create")],
            *([[InlineKeyboardButton("✨ Личный кабинет" if lang == "ru" else "✨ Жеке кабинет", web_app=WebAppInfo(url=os.getenv("MINI_APP_URL")))]] if os.getenv("MINI_APP_URL") else []),
            [InlineKeyboardButton("⚙️ Настройки" if lang == "ru" else "⚙️ Баптаулар", callback_data="menu_settings")],
            [InlineKeyboardButton(t(lang, "btn_help"), callback_data="menu_help")],
        ]

    def _settings_keyboard(self, lang, is_kg):
        students = "👥 Мои воспитанники" if is_kg and lang == "ru" else "👥 Мои ученики" if lang == "ru" else "👥 Менің тәрбиеленушілерім"
        schedule = "📅 Расписание / режим дня" if lang == "ru" else "📅 Кесте / күн тәртібі"
        return [
            [InlineKeyboardButton("👤 Мой профиль" if lang == "ru" else "👤 Менің профилім", callback_data="menu_profile")],
            [InlineKeyboardButton(students, callback_data="prof_students")],
            [InlineKeyboardButton(schedule, callback_data="agent_schedule")],
            [InlineKeyboardButton("🔔 Напоминания" if lang == "ru" else "🔔 Еске салғыштар", callback_data="agent_reminders")],
            [InlineKeyboardButton("⭐ Тариф и подписка" if lang == "ru" else "⭐ Тариф және жазылым", callback_data="prof_sub")],
            [InlineKeyboardButton("💬 Написать разработчику" if lang == "ru" else "💬 Әзірлеушіге жазу", callback_data="menu_feedback")],
            [InlineKeyboardButton("🌐 Сменить язык" if lang == "ru" else "🌐 Тілді өзгерту", callback_data="prof_lang")],
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
            context.user_data["step"] = "feedback_waiting"
            await query.edit_message_text("Напишите ваш вопрос или отзыв." if lang == "ru" else "Сұрағыңызды жазыңыз.")

        elif data == "menu_help":
            help_key = "help_text_kg" if user.get("role") == "kindergarten" else "help_text"
            keyboard = [[InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]]
            await query.edit_message_text(
                t(lang, help_key),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "menu_main":
            subscribed = user.get("subscribed", 0)
            free_used  = user.get("free_used", 0)
            total_free = free_limit_for(user)
            free_left  = max(0, total_free - free_used)
            status = t(lang, "status_pro") if subscribed else t(lang, "status_free", n=free_left, total=total_free)
            keyboard = self._main_keyboard(lang)
            await query.edit_message_text(
                t(lang, "main_menu", status=status),
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

        await query.edit_message_text(
            t(lang, "choose_category"),
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
