from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database import Database

ADMIN_LOGIN    = "Unicorn"
ADMIN_PASSWORD = "Gulkhan"

class AdminHandler:
    def __init__(self, db: Database):
        self.db = db

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["step"]         = "admin_login"
        context.user_data["admin_auth"]   = False
        context.user_data["admin_stage"]  = "login"
        await update.message.reply_text("🔐 Введите логин:")

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data  = query.data

        if not context.user_data.get("admin_auth"):
            await query.edit_message_text("❌ Нет доступа.")
            return

        if data == "admin_stats":
            await self._show_stats(query)
        elif data == "admin_users":
            await self._show_users(query)
        elif data == "admin_broadcast":
            context.user_data["step"] = "admin_broadcast"
            await query.edit_message_text("📢 Введите текст рассылки:")
        elif data == "admin_menu":
            await self._show_menu(query)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        step  = context.user_data.get("step", "")
        text  = update.message.text.strip()

        if step == "admin_login":
            context.user_data["admin_input_login"] = text
            context.user_data["step"] = "admin_password"
            await update.message.reply_text("🔑 Введите пароль:")

        elif step == "admin_password":
            login    = context.user_data.get("admin_input_login", "")
            password = text
            if login == ADMIN_LOGIN and password == ADMIN_PASSWORD:
                context.user_data["admin_auth"] = True
                context.user_data["step"] = "admin_panel"
                await update.message.reply_text("✅ Добро пожаловать, администратор!")
                await self._send_menu(update.message.chat_id, context)
            else:
                context.user_data["step"] = None
                await update.message.reply_text("❌ Неверный логин или пароль.")

        elif step == "admin_activate":
            try:
                tg_id = int(text.strip())
                await self.db.activate_subscription(tg_id)
                await update.message.reply_text(f"✅ Подписка активирована для {tg_id}")
            except:
                await update.message.reply_text("❌ Неверный ID. Введите числовой Telegram ID.")
            context.user_data["step"] = "admin_panel"

        elif step == "admin_broadcast":
            users = await self.db.get_all_users(limit=10000)
            sent = 0
            for u in users:
                try:
                    await context.bot.send_message(
                        chat_id=u["tg_id"],
                        text=text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    sent += 1
                except:
                    pass
            context.user_data["step"] = "admin_panel"
            await update.message.reply_text(f"✅ Рассылка отправлена {sent} пользователям.")

    async def _send_menu(self, chat_id, context):
        keyboard = [
            [InlineKeyboardButton("📊 Статистика",        callback_data="admin_stats")],
            [InlineKeyboardButton("👥 Пользователи",      callback_data="admin_users")],
            [InlineKeyboardButton("📢 Рассылка",          callback_data="admin_broadcast")],
            [InlineKeyboardButton("💳 Активировать подписку", callback_data="admin_activate_btn")],
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛠 *Админ-панель Docura.kz*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _show_menu(self, query):
        keyboard = [
            [InlineKeyboardButton("📊 Статистика",        callback_data="admin_stats")],
            [InlineKeyboardButton("👥 Пользователи",      callback_data="admin_users")],
            [InlineKeyboardButton("📢 Рассылка",          callback_data="admin_broadcast")],
            [InlineKeyboardButton("💳 Активировать подписку", callback_data="admin_activate_btn")],
        ]
        await query.edit_message_text(
            "🛠 *Админ-панель Docura.kz*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _show_stats(self, query):
        stats = await self.db.get_admin_stats()
        text = (
            f"📊 *Статистика Docura.kz*\n\n"
            f"👥 Пользователей: *{stats['total_users']}*\n"
            f"⭐ Подписчиков: *{stats['subscribed']}*\n"
            f"💰 Доход/мес: *{stats['revenue']:,} тг*\n\n"
            f"📄 Документов всего: *{stats['total_docs']}*\n"
            f"📄 Сегодня: *{stats['today_docs']}*\n\n"
            f"🏆 *Топ документов:*\n"
        )
        for row in stats["top_docs"]:
            text += f"  • {row[0]}: {row[1]} шт.\n"

        keyboard = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_users(self, query):
        users = await self.db.get_all_users(limit=20)
        lines = ["👥 *Последние пользователи:*\n"]
        for u in users:
            sub = "⭐" if u.get("subscribed") else "🆓"
            lines.append(f"{sub} `{u['tg_id']}` — {u.get('name', 'без имени')} | {u.get('school', '—')}")

        keyboard = [
            [InlineKeyboardButton("💳 Активировать подписку", callback_data="admin_activate_btn")],
            [InlineKeyboardButton("← Назад", callback_data="admin_menu")],
        ]
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
