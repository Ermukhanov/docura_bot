import asyncio
import logging
import time
from dotenv import load_dotenv
import os

load_dotenv()

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from database import Database
from handlers.onboarding import OnboardingHandler
from handlers.main_menu import MainMenuHandler
from handlers.documents import DocumentHandler
from handlers.profile import ProfileHandler
from handlers.admin import AdminHandler, ADMIN_IDS
from handlers.voice import VoiceHandler
from handlers.agent import AgentHandler
from handlers.concierge import ConciergeHandler
from handlers.query_adapter import MessageQueryAdapter
from handlers.notifications import send_reminders, check_subscription_expirations, monitor_schedules

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Краткая помощь, доступная пользователю в любой момент через /help."""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    lang = user.get("lang", "ru") if user else "ru"
    text = (
        "❓ *Помощь Docura.kz*\n\n"
        "Я помогу создать школьные и дошкольные документы за несколько шагов.\n\n"
        "*Быстрый способ:* просто напишите, что нужно. Например:\n"
        "• «Сделай циклограмму на завтра»\n"
        "• «Создай КСП по математике для 7 класса»\n"
        "• «Нужна характеристика на ученика»\n\n"
        "*Команды:*\n"
        "/menu — 🏠 Главное меню\n"
        "/profile — 👤 Мой профиль\n"
        "/new — 📄 Создать документ\n"
        "/history — 📚 История документов\n"
        "/invite — 🎁 Пригласить и получить бонус\n"
        "/tariffs — ⭐ Тарифы и подписка\n"
        "/support — 💬 Связаться с поддержкой\n"
        "/cancel — ❌ Отменить операцию\n"
        "/help — ❓ Помощь\n\n"
        "\n💡 Можно также отправить голосовое сообщение или открыть «Создать документ» в меню."
    ) if lang == "ru" else (
        "❓ *Docura.kz көмегі*\n\n"
        "Мектеп және балабақша құжаттарын бірнеше қадаммен жасауға көмектесемін.\n\n"
        "*Жылдам жол:* не керек екенін жай ғана жазыңыз. Мысалы:\n"
        "• «Ертеңге циклограмма жаса»\n"
        "• «7-сынып математикасына ҚМЖ жаса»\n"
        "• «Оқушыға мінездеме керек»\n\n"
        "*Командалар:*\n"
        "/menu — 🏠 Басты мәзір\n"
        "/profile — 👤 Менің профилім\n"
        "/new — 📄 Құжат жасау\n"
        "/history — 📚 Тарих\n"
        "/invite — 🎁 Шақыру және бонус алу\n"
        "/tariffs — ⭐ Тарифтер мен жазылым\n"
        "/support — 💬 Қолдау қызметі\n"
        "/cancel — ❌ Болдырмау\n"
        "/help — ❓ Көмек\n\n"
        "💡 Дауыс хабарламасын да жібере аласыз."
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Главное меню" if lang == "ru" else "🏠 Басты мәзір", callback_data="menu_main")
        ]]),
        parse_mode="Markdown",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый старт создания документа"""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    context.user_data.clear()
    await OnboardingHandler(db)._drop_chat_task(update.effective_user.id)
    from handlers.main_menu import MainMenuHandler
    mm = MainMenuHandler(db)
    lang = user.get("lang", "ru")
    await mm._send_main_menu(update.message.chat_id, context, update.effective_user.id, lang)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый переход в профиль — теперь просто делегирует в ProfileHandler,
    чтобы не дублировать (и не рассинхронизировать) логику отображения профиля
    учителя/воспитателя. Раньше эта команда строила текст профиля вручную и
    ВСЕГДА показывала школьные поля «Предмет»/«Классы», даже для воспитателей
    детского сада, у которых эти поля всегда пустые — а нужные им поля
    (детский сад/возрастная группа) не показывались вовсе."""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return

    lang = user.get("lang", "ru")
    await ProfileHandler(db).show(MessageQueryAdapter(update.message), update.effective_user.id, lang)


def get_site_url() -> str:
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")
    if railway_domain:
        return f"https://{railway_domain.strip('/')}"
    site_url = os.getenv("SITE_URL")
    if site_url:
        return site_url.rstrip("/")
    return "https://docurabot-production.up.railway.app"


async def cmd_cabinet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый переход в личный веб-кабинет педагога."""
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return

    user_id = update.effective_user.id
    lang = user.get("lang", "ru")
    site_url = get_site_url()
    cabinet_url = f"{site_url}/?tg_id={user_id}" if "vercel.app" in site_url else f"{site_url}/profile/{user_id}"

    text = (
        f"💻 *Ваш персональный веб-кабинет Docura*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"В веб-панели вам доступны:\n"
        f"• 👥 Полная база учеников и воспитанников с оценками\n"
        f"• 📅 Интерактивное расписание уроков и режим дня\n"
        f"• 📄 Архив и скачивание всех созданных документов\n"
        f"• 📊 Аналитика сэкономленного времени и тарифы\n\n"
        f"🔗 *Прямая ссылка на ваш кабинет:*\n`{cabinet_url}`"
    ) if lang == "ru" else (
        f"💻 *Сіздің Docura жеке веб-кабинетіңіз*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Веб-панельде қолжетімді:\n"
        f"• 👥 Оқушылар мен тәрбиеленушілердің толық базасы\n"
        f"• 📅 Интерактивті сабақ кестесі мен күн тәртібі\n"
        f"• 📄 Барлық дайын құжаттардың мұрағаты\n"
        f"• 📊 Үнемделген уақыт аналитикасы мен тарифтер\n\n"
        f"🔗 *Кабинетіңізге тікелей сілтеме:*\n`{cabinet_url}`"
    )
    kb = [
        [
            InlineKeyboardButton("📱 " + ("Веб-кабинет (Mini App)" if lang == "ru" else "Веб-кабинет (Mini App)"), web_app=WebAppInfo(url=cabinet_url)),
            InlineKeyboardButton("🌐 " + ("В браузере" if lang == "ru" else "Браузерде"), url=cabinet_url),
        ],
        [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cmd_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает актуальные тарифы тем же экраном, что и кнопка подписки."""
    db = context.application.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    lang = user.get("lang", "ru")
    await ProfileHandler(db)._show_subscription(MessageQueryAdapter(update.message), user, lang)


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает безопасный сценарий сообщения в поддержку."""
    db = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    lang = user.get("lang", "ru")
    # Используем существующую обработку ProfileHandler для сохранения обратной совместимости.
    context.user_data["step"] = "prof_complaint"
    await update.message.reply_text(
        "💬 Опишите вопрос, ошибку или пожелание одним сообщением.\n\nПосле отправки появится кнопка связи с поддержкой."
        if lang == "ru" else
        "💬 Сұрағыңызды, қатені немесе ұсынысыңызды бір хабарламада жазыңыз.\n\nЖібергеннен кейін қолдау қызметіне хабарласу батырмасы шығады.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Болдырмау", callback_data="menu_main")
        ]]),
    )


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Реферальная программа: показывает персональную ссылку и статистику."""
    db      = context.application.bot_data["db"]
    user_id = update.effective_user.id
    user    = await db.get_user(user_id)

    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return

    lang = user.get("lang", "ru")

    ref_code = user.get("ref_code")
    if not ref_code:
        ref_code = await db.generate_unique_ref_code()
        await db.upsert_user(user_id, {"ref_code": ref_code})

    bot_username = context.bot.username
    if not bot_username:
        me = await context.bot.get_me()
        bot_username = me.username

    link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
    referrals_count = await db.count_referrals(user_id)
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
    await update.message.reply_text(text, parse_mode="Markdown")


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
    kb = [[InlineKeyboardButton("🏠 Главное меню" if lang == "ru" else "🏠 Басты мәзір", callback_data="menu_main")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def _start_after_account_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Возвращает пользователя к выбору учреждения после сброса из админки."""
    db = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("reset_pending"):
        return False

    context.user_data.clear()
    # Язык профиля был сброшен; русский нужен только как стартовый язык интерфейса.
    # После сброса запускаем единый onboarding: сначала выбор языка.
    await db.upsert_user(update.effective_user.id, {"reset_pending": 0, "lang": None, "lang_selected": 0})
    await OnboardingHandler(db).start(update, context)
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _start_after_account_reset(update, context):
        return
    await OnboardingHandler(context.application.bot_data["db"]).start(update, context)


async def _route_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db   = context.application.bot_data["db"]
    key  = context.application.bot_data["anthropic_key"]
    if await _start_after_account_reset(update, context):
        return
    # Некоторые сценарии сбрасывают step значением None. Для маршрутизации
    # это означает отсутствие активного сценария, а не ошибку .startswith().
    step = context.user_data.get("step") or ""

    if step.startswith("onboard_") or step.startswith("reg_"):
        await OnboardingHandler(db).handle_text(update, context)
    elif step.startswith("doc_") or step == "waiting_answer":
        await DocumentHandler(db, key).handle_text(update, context)
    elif step.startswith("prof_") or step.startswith("student_"):
        await ProfileHandler(db).handle_text(update, context)
    elif step.startswith("admin_"):
        await AdminHandler(db).handle_text(update, context)
    elif step.startswith("agent_"):
        await AgentHandler(db, key).handle_text(update, context)
    else:
        # Нет активного сценария — либо это ещё не зарегистрированный пользователь
        # (тогда ведём в онбординг, как и раньше), либо это просто свободное сообщение
        # в чате — тогда отвечает разговорный агент, а не заново открывает меню.
        user = await db.get_user(update.effective_user.id)
        if not user or not user.get("name"):
            await OnboardingHandler(db).start(update, context)
        else:
            concierge = context.application.bot_data["concierge"]
            await concierge.handle_text(update, context)


_last_admin_alert = 0.0


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок. Раньше исключения в хендлерах только
    писались в лог, а пользователь видел «кнопка не реагирует». Теперь:
    пользователь получает понятное сообщение, админ — краткий алерт (не чаще раза в минуту)."""
    global _last_admin_alert
    logger.error("Unhandled exception", exc_info=context.error)
    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("⚠️ Что-то пошло не так. Попробуйте ещё раз или нажмите /menu.")
    except Exception:
        pass
    now = time.time()
    if now - _last_admin_alert > 60 and ADMIN_IDS:
        _last_admin_alert = now
        who = getattr(getattr(update, "effective_user", None), "id", "?")
        text = f"⚠️ Ошибка у пользователя {who}: {type(context.error).__name__}: {str(context.error)[:300]}"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, text)
            except Exception:
                pass


async def post_init(app: Application):
    db        = app.bot_data["db"]
    concierge = app.bot_data["concierge"]
    # Храним ссылки на фоновые задачи: иначе сборщик мусора может прервать их молча.
    app.bot_data["bg_tasks"] = [
        asyncio.create_task(send_reminders(app, db, concierge)),
        asyncio.create_task(check_subscription_expirations(app, db)),
        asyncio.create_task(monitor_schedules(app, db, app.bot_data["anthropic_key"])),
    ]

    # Устанавливаем меню команд в Telegram
    commands_ru = [
        BotCommand("menu",    "🏠 Главное меню"),
        BotCommand("new",     "📄 Создать документ"),
        BotCommand("profile", "👤 Мой профиль"),
        BotCommand("history", "📚 История документов"),
        BotCommand("invite",  "🎁 Пригласить и получить бонус"),
        BotCommand("tariffs", "⭐ Тарифы и подписка"),
        BotCommand("support", "💬 Связаться с поддержкой"),
        BotCommand("cancel",  "❌ Отменить операцию"),
        BotCommand("help",    "❓ Помощь и список команд"),
    ]
    await app.bot.set_my_commands(commands_ru)
    logger.info("✅ Меню команд установлено")
    logger.info("🔔 Планировщик уведомлений запущен")
    logger.info("⏳ Проверка истечения подписок запущена")
    logger.info("📅 Мониторинг расписаний (каждые 6 часов) запущен")


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

    concierge = ConciergeHandler(db, ANTHROPIC_API_KEY)
    app.bot_data["concierge"] = concierge
    if concierge._configured():
        logger.info("🤖 Разговорный агент подключён (%s)", os.getenv("CONCIERGE_MODEL", "gpt-4o-mini"))
    else:
        logger.info("🤖 Разговорный агент НЕ настроен (нет CONCIERGE_API_KEY) — работает в режиме заглушки")

    onboarding = OnboardingHandler(db)
    main_menu  = MainMenuHandler(db)
    documents  = DocumentHandler(db, ANTHROPIC_API_KEY)
    profile    = ProfileHandler(db)
    admin      = AdminHandler(db)
    voice      = VoiceHandler(db, ANTHROPIC_API_KEY)
    agent      = AgentHandler(db, ANTHROPIC_API_KEY)

    # Команды
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("menu",    main_menu.show))
    app.add_handler(CommandHandler("cancel",  onboarding.cancel))
    app.add_handler(CommandHandler("mernar",  admin.login))
    app.add_handler(CommandHandler("new",     cmd_new))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("cabinet", cmd_cabinet))
    app.add_handler(CommandHandler("webapp",  cmd_cabinet))
    app.add_handler(CommandHandler("app",     cmd_cabinet))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("invite",  cmd_invite))
    app.add_handler(CommandHandler("tariffs", cmd_tariffs))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("help",    cmd_help))

    # Голос
    app.add_handler(MessageHandler(filters.VOICE, voice.handle))
    # Личные Word-образцы для документов детского сада.
    app.add_handler(MessageHandler(filters.Document.ALL, documents.handle_document))

    # Фото — расписание/режим дня или чек
    async def _handle_photo(update, context):
        if await agent.handle_photo(update, context):
            return
        # иначе это чек оплаты — обрабатывает profile
        from handlers.profile import ProfileHandler as PH
        await PH(db).handle_photo(update, context)
    app.add_handler(MessageHandler(filters.PHOTO, _handle_photo))

    # Колбэки
    app.add_handler(CallbackQueryHandler(onboarding.callback, pattern="^(lang_|role_|onboard_)"))
    app.add_handler(CallbackQueryHandler(main_menu.callback,  pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(documents.callback,  pattern="^(doc_|cat_|ans_|gen_|rating_)"))
    app.add_handler(CallbackQueryHandler(profile.callback,    pattern="^(prof_|sub_|student_)"))
    app.add_handler(CallbackQueryHandler(admin.callback,      pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(agent.callback,      pattern="^agent_"))
    app.add_handler(CallbackQueryHandler(concierge.callback,  pattern="^cg_"))

    # Текст
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _route_text))

    app.add_error_handler(on_error)

    logger.info("✅ Docura.kz запущен!")

    # Фоновый запуск веб-кабинета (Mini App) для Railway и облачных серверов
    def _run_mini_app():
        try:
            from mini_app import app as flask_app
            port = int(os.environ.get("PORT", 8080))
            logger.info("🌐 Веб-кабинет Mini App запускается на порту %d", port)
            flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        except OSError:
            # Порт уже занят (например, mini_app.py запущен в отдельном процессе локально)
            pass
        except Exception as e:
            logger.warning("Веб-кабинет: %s", e)

    import threading
    threading.Thread(target=_run_mini_app, daemon=True).start()

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
