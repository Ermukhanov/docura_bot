from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

class MainMenuHandler:
    def __init__(self, db: Database):
        self.db = db

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await self.db.get_user(user_id)
        if not user or not user.get("name"):
            from handlers.onboarding import OnboardingHandler
            await OnboardingHandler(self.db).start(update, context)
            return
        lang = user.get("lang", "ru")
        await self._send_main_menu(update.message.chat_id, context, user_id, lang)

    async def _send_main_menu(self, chat_id, context, user_id, lang):
        user = await self.db.get_user(user_id)
        subscribed = user.get("subscribed", 0)
        free_used  = user.get("free_used", 0)
        free_left  = max(0, 3 - free_used)
        role       = user.get("role", "teacher")

        if subscribed:
            status = t(lang, "status_pro")
        else:
            status = t(lang, "status_free", n=free_left)

        if role == "kindergarten":
            emoji = "🧸"
            title = "Балабақша Docura" if lang == "kz" else "Детский сад Docura"
        else:
            emoji = "🏫"
            title = "Мектеп Docura" if lang == "kz" else "Школа Docura"

        keyboard = [
            [InlineKeyboardButton(t(lang, "btn_create"),  callback_data="menu_create")],
            [InlineKeyboardButton(t(lang, "btn_history"), callback_data="menu_history"),
             InlineKeyboardButton(t(lang, "btn_profile"), callback_data="menu_profile")],
            [InlineKeyboardButton(t(lang, "btn_help"),    callback_data="menu_help")],
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{emoji} *{title}*\n\n{status}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        role    = user.get("role", "teacher") if user else "teacher"

        if data == "menu_create":
            await self._show_categories(query, lang, user)

        elif data == "menu_history":
            await self._show_history(query, user_id, lang)

        elif data == "menu_profile":
            from handlers.profile import ProfileHandler
            await ProfileHandler(self.db).show(query, user_id, lang)

        elif data == "menu_help":
            keyboard = [[InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")]]
            await query.edit_message_text(
                t(lang, "help_text"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "menu_main":
            subscribed = user.get("subscribed", 0)
            free_used  = user.get("free_used", 0)
            free_left  = max(0, 3 - free_used)
            status = t(lang, "status_pro") if subscribed else t(lang, "status_free", n=free_left)
            if role == "kindergarten":
                title = "Балабақша Docura 🧸" if lang == "kz" else "Детский сад Docura 🧸"
            else:
                title = "Мектеп Docura 🏫" if lang == "kz" else "Школа Docura 🏫"
            keyboard = [
                [InlineKeyboardButton(t(lang, "btn_create"),  callback_data="menu_create")],
                [InlineKeyboardButton(t(lang, "btn_history"), callback_data="menu_history"),
                 InlineKeyboardButton(t(lang, "btn_profile"), callback_data="menu_profile")],
                [InlineKeyboardButton(t(lang, "btn_help"),    callback_data="menu_help")],
            ]
            await query.edit_message_text(
                f"*{title}*\n\n{status}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    async def _show_categories(self, query, lang, user):
        role  = user.get("role", "teacher")
        is_ct = user.get("is_class_teacher", 0)

        if role == "kindergarten":
            # Меню для воспитателей садика
            if lang == "ru":
                keyboard = [
                    [InlineKeyboardButton("📝 Планирование",    callback_data="cat_kg_planning")],
                    [InlineKeyboardButton("📊 Отчёты",          callback_data="cat_kg_reports")],
                    [InlineKeyboardButton("👶 По детям",         callback_data="cat_kg_children")],
                    [InlineKeyboardButton("📋 Личные документы", callback_data="cat_personal")],
                    [InlineKeyboardButton("← Назад",            callback_data="menu_main")],
                ]
            else:
                keyboard = [
                    [InlineKeyboardButton("📝 Жоспарлау",       callback_data="cat_kg_planning")],
                    [InlineKeyboardButton("📊 Есептер",          callback_data="cat_kg_reports")],
                    [InlineKeyboardButton("👶 Балалар бойынша",  callback_data="cat_kg_children")],
                    [InlineKeyboardButton("📋 Жеке құжаттар",   callback_data="cat_personal")],
                    [InlineKeyboardButton("← Артқа",            callback_data="menu_main")],
                ]
        else:
            # Меню для учителей школы
            keyboard = [
                [InlineKeyboardButton(t(lang, "cat_planning"), callback_data="cat_planning")],
                [InlineKeyboardButton(t(lang, "cat_reports"),  callback_data="cat_reports")],
            ]
            if is_ct:
                keyboard.append([InlineKeyboardButton(t(lang, "cat_students"), callback_data="cat_students")])
            keyboard.append([InlineKeyboardButton(t(lang, "cat_personal"), callback_data="cat_personal")])
            keyboard.append([InlineKeyboardButton("← " + t(lang, "back"), callback_data="menu_main")])

        title = t(lang, "choose_category")
        await query.edit_message_text(
            title,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

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
