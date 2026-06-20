import asyncio
import logging
from dotenv import load_dotenv
import os

load_dotenv()

from telegram import Update, BotCommand, MenuButtonCommands
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from database import Database
from handlers.onboarding import OnboardingHandler
from handlers.main_menu import MainMenuHandler
from handlers.documents import DocumentHandler
from handlers.profile import ProfileHandler
from handlers.admin import AdminHandler
from handlers.voice import VoiceHandler
from handlers.notifications import send_reminders

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    lang = user.get("lang", "ru") if user else "ru"
    text = (
        "📋 *Команды Docura.kz:*\n\n"
        "/menu — 🏠 Главное меню\n"
        "/profile — 👤 Мой профиль\n"
        "/new — 📄 Создать документ\n"
        "/history — 📚 История документов\n"
        "/cancel — ❌ Отменить операцию\n"
        "/help — ❓ Помощь\n\n"
        "💡 Можно также отправить голосовое сообщение!"
    ) if lang == "ru" else (
        "📋 *Docura.kz командалары:*\n\n"
        "/menu — 🏠 Басты мәзір\n"
        "/profile — 👤 Менің профилім\n"
        "/new — 📄 Құжат жасау\n"
        "/history — 📚 Тарих\n"
        "/cancel — ❌ Болдырмау\n"
        "/help — ❓ Көмек"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый старт создания документа"""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    context.user_data.clear()
    from handlers.main_menu import MainMenuHandler
    mm = MainMenuHandler(db)
    lang = user.get("lang", "ru")
    await mm._send_main_menu(update.message.chat_id, context, update.effective_user.id, lang)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый переход в профиль"""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    lang = user.get("lang", "ru")
    # Отправляем как новое сообщение
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    free_left = max(0, 3 - user.get("free_used", 0))
    sub = "⭐ PRO — безлимит" if user.get("subscribed") else f"🆓 Бесплатно ({free_left}/3 осталось)"
    text = (
        f"👤 *Мой профиль*\n\n"
        f"📛 {user.get('name','—')}\n"
        f"🏫 {user.get('school','—')}\n"
        f"📚 {user.get('subject','—')}\n"
        f"🏷 {user.get('classes','—')}\n"
        f"⭐ {sub}"
    )
    kb = [
        [InlineKeyboardButton("✏️ Редактировать", callback_data="prof_edit")],
        [InlineKeyboardButton("👥 Мои ученики",   callback_data="prof_students")],
        [InlineKeyboardButton("⭐ Подписка",       callback_data="prof_sub")],
        [InlineKeyboardButton("🏠 Главное меню",   callback_data="menu_main")],
    ]
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """История документов"""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        return
    lang  = user.get("lang", "ru")
    docs  = await db.get_history(update.effective_user.id, limit=10)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    if not docs:
        text = "📚 История пуста — создайте первый документ!" if lang == "ru" else "📚 Тарих бос!"
    else:
        lines = ["📚 *Последние документы:*\n"]
        for d in docs:
            lines.append(f"📄 {d['doc_name']} — {d['created_at'][:10]} ⭐{d['score']}/100")
        text = "\n".join(lines)
    kb = [[InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def _route_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step", "")
    db   = context.application.bot_data["db"]

    if step.startswith("onboard_") or step.startswith("reg_"):
        await OnboardingHandler(db).handle_text(update, context)
    elif step.startswith("doc_") or step == "waiting_answer":
        await DocumentHandler(db, context.application.bot_data["anthropic_key"]).handle_text(update, context)
    elif step.startswith("prof_") or step.startswith("student_"):
        await ProfileHandler(db).handle_text(update, context)
    elif step.startswith("admin_"):
        await AdminHandler(db).handle_text(update, context)
    else:
        await MainMenuHandler(db).show(update, context)


async def post_init(app: Application):
    db = app.bot_data["db"]
    asyncio.create_task(send_reminders(app, db))

    # Устанавливаем меню команд в Telegram
    commands_ru = [
        BotCommand("menu",    "🏠 Главное меню"),
        BotCommand("new",     "📄 Создать документ"),
        BotCommand("profile", "👤 Мой профиль"),
        BotCommand("history", "📚 История документов"),
        BotCommand("cancel",  "❌ Отменить операцию"),
        BotCommand("help",    "❓ Помощь"),
    ]
    await app.bot.set_my_commands(commands_ru)
    logger.info("✅ Меню команд установлено")
    logger.info("🔔 Планировщик уведомлений запущен")


async def run():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN не найден в .env!")
    if not ANTHROPIC_API_KEY:
        raise ValueError("❌ ANTHROPIC_API_KEY не найден в .env!")

    db = Database()
    await db.init()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.bot_data["db"]            = db
    app.bot_data["anthropic_key"] = ANTHROPIC_API_KEY

    onboarding = OnboardingHandler(db)
    main_menu  = MainMenuHandler(db)
    documents  = DocumentHandler(db, ANTHROPIC_API_KEY)
    profile    = ProfileHandler(db)
    admin      = AdminHandler(db)
    voice      = VoiceHandler(db, ANTHROPIC_API_KEY)

    # Команды
    app.add_handler(CommandHandler("start",   onboarding.start))
    app.add_handler(CommandHandler("menu",    main_menu.show))
    app.add_handler(CommandHandler("cancel",  onboarding.cancel))
    app.add_handler(CommandHandler("mernar",  admin.login))
    app.add_handler(CommandHandler("new",     cmd_new))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("help",    cmd_help))

    # Голос
    app.add_handler(MessageHandler(filters.VOICE, voice.handle))

    # Фото — скриншоты оплаты
    app.add_handler(MessageHandler(filters.PHOTO, profile.handle_photo))

    # Колбэки
    app.add_handler(CallbackQueryHandler(onboarding.callback, pattern="^(lang_|role_|onboard_)"))
    app.add_handler(CallbackQueryHandler(main_menu.callback,  pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(documents.callback,  pattern="^(doc_|cat_|ans_|gen_)"))
    app.add_handler(CallbackQueryHandler(profile.callback,    pattern="^(prof_|sub_|student_)"))
    app.add_handler(CallbackQueryHandler(admin.callback,      pattern="^admin_"))

    # Текст
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _route_text))

    logger.info("✅ Docura.kz запущен!")

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
