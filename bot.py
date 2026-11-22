import asyncio
import logging
import time
from dotenv import load_dotenv
import os

load_dotenv()

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, TypeHandler, filters, ContextTypes
)
from database import Database
from handlers.onboarding import OnboardingHandler
from handlers.main_menu import MainMenuHandler
from handlers.documents import DocumentHandler
from handlers.profile import ProfileHandler
from handlers.admin import AdminHandler, ADMIN_IDS
from handlers.feedback import FeedbackHandler
from handlers.voice import VoiceHandler
from handlers.agent import AgentHandler
from handlers.concierge import ConciergeHandler
from handlers.query_adapter import MessageQueryAdapter
from handlers.notifications import (
    send_reminders, first_doc_reminder_loop, check_subscription_expirations,
)
from handlers.schedule_monitor import schedule_monitor_loop, pending_still_valid
from security import create_auth_token

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# httpx logs complete request URLs at INFO; Telegram Bot API URLs contain the
# bot token, so keep transport request logging at WARNING in production.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def _get_msg(update: Update):
    return getattr(update, "effective_message", None) or getattr(update, "message", None)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Краткая помощь, доступная пользователю в любой момент через /help."""
    msg = _get_msg(update)
    if not msg:
        return
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
    await msg.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Главное меню" if lang == "ru" else "🏠 Басты мәзір", callback_data="menu_main")
        ]]),
        parse_mode="Markdown",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый старт создания документа"""
    msg = _get_msg(update)
    if not msg:
        return
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
    await mm._send_main_menu(msg.chat_id, context, update.effective_user.id, lang)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый переход в профиль — теперь просто делегирует в ProfileHandler,
    чтобы не дублировать (и не рассинхронизировать) логику отображения профиля
    учителя/воспитателя. Раньше эта команда строила текст профиля вручную и
    ВСЕГДА показывала школьные поля «Предмет»/«Классы», даже для воспитателей
    детского сада, у которых эти поля всегда пустые — а нужные им поля
    (детский сад/возрастная группа) не показывались вовсе."""
    msg = _get_msg(update)
    if not msg:
        return
    db   = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return

    lang = user.get("lang", "ru")
    await ProfileHandler(db).show(MessageQueryAdapter(msg), update.effective_user.id, lang)


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
    token = create_auth_token(user_id)
    browser_url = f"{site_url}/auth?token={token}"
    webapp_url = f"{site_url}/app"

    text = (
        f"💻 *Ваш персональный веб-кабинет Docura*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"В веб-панели вам доступны:\n"
        f"• 👥 База {'воспитанников' if user.get('role') == 'kindergarten' else 'учеников'} с аналитикой\n"
        f"• 📅 {'Режим дня и сетка занятий ОУД' if user.get('role') == 'kindergarten' else 'Интерактивное расписание уроков'}\n"
        f"• 📄 Архив и скачивание всех созданных документов\n"
        f"• 📊 Аналитика сэкономленного времени и тарифы\n\n"
        f"🔐 *Безопасный вход:* ссылка с одноразовым ключом доступа:\n`{browser_url}`"
    ) if lang == "ru" else (
        f"💻 *Сіздің Docura жеке веб-кабинетіңіз*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Веб-панельде қолжетімді:\n"
        f"• 👥 {'Тәрбиеленушілер' if user.get('role') == 'kindergarten' else 'Оқушылар'} базасы\n"
        f"• 📅 {'Күн тәртібі мен ОҮҚ кестесі' if user.get('role') == 'kindergarten' else 'Интерактивті сабақ кестесі'}\n"
        f"• 📄 Барлық дайын құжаттардың мұрағаты\n"
        f"• 📊 Үнемделген уақыт аналитикасы мен тарифтер\n\n"
        f"🔐 *Қауіпсіз кіру:* бір реттік кіру кілті бар жеке сілтеме:\n`{browser_url}`"
    )
    msg = _get_msg(update)
    if not msg:
        return
    kb = [
        [
            InlineKeyboardButton("📱 " + ("Веб-кабинет (Mini App)" if lang == "ru" else "Веб-кабинет (Mini App)"), web_app=WebAppInfo(url=webapp_url)),
            InlineKeyboardButton("🌐 " + ("В браузере" if lang == "ru" else "Браузерде"), url=browser_url),
        ],
        [InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")]
    ]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cmd_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает актуальные тарифы тем же экраном, что и кнопка подписки."""
    msg = _get_msg(update)
    if not msg:
        return
    db = context.application.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    lang = user.get("lang", "ru")
    await ProfileHandler(db)._show_subscription(MessageQueryAdapter(msg), user, lang)


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает безопасный сценарий сообщения в поддержку."""
    msg = _get_msg(update)
    if not msg:
        return
    db = context.application.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await OnboardingHandler(db).start(update, context)
        return
    lang = user.get("lang", "ru")
    # Используем существующую обработку ProfileHandler для сохранения обратной совместимости.
    context.user_data["step"] = "prof_complaint"
    await msg.reply_text(
        "💬 Опишите вопрос, ошибку или пожелание одним сообщением.\n\nПосле отправки появится кнопка связи с поддержкой."
        if lang == "ru" else
        "💬 Сұрағыңызды, қатені немесе ұсынысыңызды бір хабарламада жазыңыз.\n\nЖібергеннен кейін қолдау қызметіне хабарласу батырмасы шығады.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Болдырмау", callback_data="menu_main")
        ]]),
    )


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Реферальная программа: показывает персональную ссылку и статистику."""
    msg = _get_msg(update)
    if not msg:
        return
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
    await msg.reply_text(text, parse_mode="Markdown")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """История документов"""
    msg = _get_msg(update)
    if not msg:
        return
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
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отключение уведомлений"""
    msg = _get_msg(update)
    if not msg:
        return
    db = context.application.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    lang = user.get("lang", "ru") if user else "ru"
    await db.set_notifications_enabled(user_id, False)
    await db.update_agent_context(user_id, {"reminders_enabled": False})
    text = (
        "🔕 Уведомления и напоминания отключены.\nВключить их снова можно в любой момент командой /on. Желаете что-нибудь создать сейчас?"
        if lang == "ru" else
        "🔕 Хабарламалар мен еске салғыштар өшірілді.\nОларды қайта қосу үшін кез келген уақытта /on жазыңыз. Қазір құжат дайындаймыз ба?"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [[InlineKeyboardButton("📄 Создать документ" if lang == "ru" else "📄 Құжат жасау", callback_data="menu_create")]]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включение уведомлений"""
    msg = _get_msg(update)
    if not msg:
        return
    db = context.application.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    lang = user.get("lang", "ru") if user else "ru"
    await db.set_notifications_enabled(user_id, True)
    await db.update_agent_context(user_id, {"reminders_enabled": True})
    text = (
        "🔔 Уведомления успешно включены!\nЯ буду вовремя напоминать о важных уроках и документах. Посмотрим расписание или подготовим КСП?"
        if lang == "ru" else
        "🔔 Хабарламалар сәтті қосылды!\nМаңызды сабақтар мен құжаттарды дер кезінде еске салып тұрамын. Кестені көреміз бе, әлде ҚМЖ дайындаймыз ба?"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = [
        [InlineKeyboardButton("📅 Расписание" if lang == "ru" else "📅 Сабақ кестесі", callback_data="agent_schedule")],
        [InlineKeyboardButton("📄 Создать КСП" if lang == "ru" else "📄 ҚМЖ жасау", callback_data="doc_lesson_plan")]
    ]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


def _dialog_persistence():
    """Незаконченные диалоги (шаг анкеты, введённые ответы) переживают перезапуск бота.
    Раньше каждый деплой молча сбрасывал учителя посреди заполнения в главное меню.
    Файл лежит рядом с базой — на volume, если DB_PATH указывает туда."""
    from telegram.ext import PicklePersistence, PersistenceInput
    from database import DB_PATH
    path = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "docura_dialogs.pickle")
    return PicklePersistence(
        filepath=path,
        store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False),
        update_interval=30,
    )


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


async def _join_org(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    db = context.application.bot_data["db"]
    tg_id = update.effective_user.id
    user = await db.get_user(tg_id)
    if not user:
        await db.upsert_user(tg_id, {})
    status, org = await db.join_org(code.strip().lower(), tg_id)
    lang = ((user or {}).get("lang") or "ru")
    name = org["name"] if org else ""
    texts = {
        "ok": (f"🏢 Вы подключены к «{name}».\nDocura для вас безлимитна — оплачивает организация.",
               f"🏢 Сіз «{name}» ұйымына қосылдыңыз.\nDocura сіз үшін шексіз — ұйым төлейді."),
        "already": (f"🏢 Вы уже подключены к «{name}» — безлимит активен.",
                    f"🏢 Сіз «{name}» ұйымына қосылғансыз — шексіз режим белсенді."),
        "full": (f"⚠️ В «{name}» заняты все места. Попросите руководителя добавить места.",
                 f"⚠️ «{name}» ұйымында орын қалмады. Басшыға хабарласыңыз."),
        "inactive": (f"⚠️ Подписка «{name}» сейчас не активна. Обратитесь к руководителю.",
                     f"⚠️ «{name}» жазылымы қазір белсенді емес. Басшыға хабарласыңыз."),
        "not_found": ("⚠️ Ссылка организации не найдена. Проверьте её у руководителя.",
                      "⚠️ Ұйым сілтемесі табылмады. Басшыдан тексеріңіз."),
    }
    ru, kz = texts[status]
    await update.effective_message.reply_text(kz if lang == "kz" else ru)
    if status == "ok":
        await db.log_funnel_event(tg_id, "org_joined")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _start_after_account_reset(update, context):
        return
    # Ссылка садика/школы: t.me/docurakz_bot?start=org_<code> — педагог сразу получает B2B
    if context.args and context.args[0].startswith("org_"):
        await _join_org(update, context, context.args[0][4:])
        context.args = []
    # Ссылки с сайта: t.me/docurakz_bot?start=cabinet / ?start=support
    if context.args and context.args[0] in ("cabinet", "support"):
        db = context.application.bot_data["db"]
        user = await db.get_user(update.effective_user.id)
        if user and user.get("name"):
            await (cmd_cabinet if context.args[0] == "cabinet" else cmd_support)(update, context)
            return
    await OnboardingHandler(context.application.bot_data["db"]).start(update, context)


async def _route_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = _get_msg(update)
    if not msg or not getattr(msg, "text", None):
        return
    db   = context.application.bot_data["db"]
    key  = context.application.bot_data["anthropic_key"]
    if await _start_after_account_reset(update, context):
        return
    # Некоторые сценарии сбрасывают step значением None. Для маршрутизации
    # это означает отсутствие активного сценария, а не ошибку .startswith().
    step = context.user_data.get("step") or ""

    if step.startswith("onboard_") or step.startswith("reg_") or step == "post_doc_profile":
        await OnboardingHandler(db).handle_text(update, context)
    elif step.startswith("doc_") or step in ("waiting_answer", "monitoring_data"):
        await DocumentHandler(db, key).handle_text(update, context)
    elif step.startswith("prof_") or step.startswith("student_"):
        await ProfileHandler(db).handle_text(update, context)
    elif step.startswith("admin_"):
        await AdminHandler(db).handle_text(update, context)
    elif step.startswith("agent_"):
        handled = await AgentHandler(db, key).handle_text(update, context)
        if handled is False and context.user_data.get("step") in (None, "", "agent_ksp_topics"):
            # это были не темы уроков (вопрос, команда, устаревшая сводка) — отвечает ассистент
            context.user_data["step"] = None
            await context.application.bot_data["concierge"].handle_text(update, context)
    else:
        # Нет активного сценария — либо это ещё не зарегистрированный пользователь
        # (тогда ведём в онбординг, как и раньше), либо это просто свободное сообщение
        # в чате — тогда отвечает разговорный агент, а не заново открывает меню.
        user = await db.get_user(update.effective_user.id)
        # Учитель может пропустить анкету (ФИО пустое), но роль он уже выбрал — значит,
        # онбординг пройден, и на свободное сообщение должен отвечать ассистент,
        # а не показываться снова главное меню.
        if not user or not (user.get("name") or user.get("role")):
            await OnboardingHandler(db).start(update, context)
        else:
            # Вечерняя сводка с автогенерацией могла прийти, пока у учителя шёл другой
            # сценарий или его не было в памяти бота — тогда ждём темы отсюда.
            memory = await db.get_agent_context(update.effective_user.id)
            pend = memory.get("ksp_pending")
            if pend:
                await db.update_agent_context(update.effective_user.id, {"ksp_pending": None})
                if pending_still_valid(pend):
                    context.user_data["step"] = "agent_ksp_topics"
                    context.user_data["ksp_pending"] = pend
                    if await AgentHandler(db, key).handle_text(update, context):
                        return
            concierge = context.application.bot_data["concierge"]
            await concierge.handle_text(update, context)


async def _track_user_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = getattr(update, "effective_user", None)
    if user:
        state = context.user_data
        step = state.get("step")
        funnel_step = None
        if step == "onboard_registration":
            funnel_step = f"registration_question_{int(state.get('onboard_reg_index', 0)) + 1}"
        elif step == "onboard_confirmation":
            funnel_step = "registration_confirmation"
        elif step == "waiting_answer":
            questions = state.get("questions", [])
            index = int(state.get("q_index", 0))
            doc_type = str(state.get("doc_type") or "document")
            funnel_step = f"document_{doc_type}_question_{min(index + 1, max(len(questions), 1))}_of_{max(len(questions), 1)}"
        elif step == "monitoring_data":
            funnel_step = "document_monitoring_observations"
        elif step == "onboard_quick_doc":
            funnel_step = "first_document_role_choice"
        elif state.get("doc_type") and not state.get("doc_lang"):
            funnel_step = "first_document_language_choice" if state.get("onboard_quick_start") else "document_language_choice"
        elif state.get("onboard_step") is not None:
            funnel_step = f"onboarding_step_{int(state['onboard_step'])}"
        await context.application.bot_data["db"].update_last_active(user.id, funnel_step=funnel_step)


_last_admin_alert = 0.0


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок. Раньше исключения в хендлерах только
    писались в лог, а пользователь видел «кнопка не реагирует». Клиенту
    отправляется только нейтральная подсказка; технические детали получает
    администратор (не чаще раза в минуту)."""
    global _last_admin_alert
    # Повторное нажатие той же кнопки: Telegram отвечает «Message is not modified».
    # Это не ошибка — молча закрываем «часики» на кнопке, без алертов.
    if "Message is not modified" in str(context.error):
        try:
            if isinstance(update, Update) and update.callback_query:
                await update.callback_query.answer()
        except Exception:
            pass
        return
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


async def _resume_interrupted_generations(app: Application):
    """После перезапуска: кому документ не успел сгенерироваться — сообщаем и даём кнопку «Повторить»."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await asyncio.sleep(3)
    for user_id, data in list(app.user_data.items()):
        snap = data.pop("generating", None) if isinstance(data, dict) else None
        if not snap or not snap.get("doc_type"):
            continue
        data["interrupted_generation"] = snap
        data["step"] = None  # иначе следующее сообщение учителя приняли бы за ответ на вопрос анкеты
        ru = snap.get("lang", "ru") != "kz"
        try:
            await app.bot.send_message(
                snap.get("chat_id") or user_id,
                ("⚠️ Бот обновлялся, и документ не успел сохраниться. Ваши ответы сохранены — нажмите «Повторить»."
                 if ru else "⚠️ Бот жаңартылды, құжат дайын болмай қалды. Жауаптарыңыз сақталған — «Қайталау» басыңыз."),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Повторить" if ru else "🔄 Қайталау", callback_data="gen_retry")]]),
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("resume notice failed for %s: %s", user_id, exc)
        await asyncio.sleep(0.05)


async def post_init(app: Application):
    db        = app.bot_data["db"]
    concierge = app.bot_data["concierge"]
    # Храним ссылки на фоновые задачи: иначе сборщик мусора может прервать их молча.
    app.bot_data["bg_tasks"] = [
        asyncio.create_task(send_reminders(app, db, concierge)),
        asyncio.create_task(first_doc_reminder_loop(app, db)),
        asyncio.create_task(check_subscription_expirations(app, db)),
        asyncio.create_task(schedule_monitor_loop(app, db)),
    ]

    app.bot_data["bg_tasks"].append(asyncio.create_task(_resume_interrupted_generations(app)))

    # Устанавливаем меню команд в Telegram
    commands_ru = [
        BotCommand("new",     "📄 Создать документ"),
        BotCommand("menu",    "🏠 Главное меню"),
        BotCommand("history", "📚 Мои документы"),
        BotCommand("profile", "👤 Профиль и настройки"),
        BotCommand("tariffs", "⭐ Тариф"),
        BotCommand("help",    "❓ Помощь"),
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
        .persistence(_dialog_persistence())
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
    feedback = FeedbackHandler(db, ADMIN_IDS)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, feedback.on_text), group=-1)
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
    app.add_handler(CommandHandler("off",     cmd_off))
    app.add_handler(CommandHandler("on",      cmd_on))

    # Голос
    app.add_handler(MessageHandler(filters.VOICE & filters.UpdateType.MESSAGE, voice.handle))
    # Документы (Word-образцы, расписание в PDF/Word, или чеки)
    async def _handle_document(update, context):
        step = context.user_data.get("step")
        if step == "waiting_payment_receipt":
            from handlers.profile import ProfileHandler as PH
            await PH(db, ANTHROPIC_API_KEY).handle_document(update, context)
            return
        if step == "agent_waiting_schedule_photo":
            if await agent.handle_photo(update, context):
                return
        await documents.handle_document(update, context)
    app.add_handler(MessageHandler(filters.Document.ALL & filters.UpdateType.MESSAGE, _handle_document))

    # Фото — расписание/режим дня или чек
    async def _handle_photo(update, context):
        if await agent.handle_photo(update, context):
            return
        # иначе это чек оплаты — обрабатывает profile
        from handlers.profile import ProfileHandler as PH
        await PH(db, ANTHROPIC_API_KEY).handle_photo(update, context)
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateType.MESSAGE, _handle_photo))

    # Колбэки
    app.add_handler(CallbackQueryHandler(onboarding.callback, pattern="^(lang_|role_|onboard_)"))
    app.add_handler(CallbackQueryHandler(main_menu.callback,  pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(documents.callback,  pattern="^(doc_|cat_|ans_|gen_|rating_)"))
    app.add_handler(CallbackQueryHandler(profile.callback,    pattern="^(prof_|sub_|student_)"))
    app.add_handler(CallbackQueryHandler(admin.callback,      pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(feedback.callback,   pattern="^fb_"))
    app.add_handler(CallbackQueryHandler(agent.callback,      pattern="^agent_"))
    app.add_handler(CallbackQueryHandler(concierge.callback,  pattern="^cg_"))

    # Текст
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, _route_text))

    # Run after normal handlers so successful starts are persisted first.
    app.add_handler(TypeHandler(Update, _track_user_activity), group=1)

    app.add_error_handler(on_error)

    logger.info("✅ Docura.kz запущен!")

    # Фоновый запуск веб-кабинета (Mini App) для Railway и облачных серверов
    def _run_mini_app():
        try:
            from mini_app import app as flask_app
            from waitress import serve
            port = int(os.environ.get("PORT", 8080))
            logger.info("🌐 Веб-кабинет Mini App запускается на порту %d", port)
            serve(flask_app, host="0.0.0.0", port=port, threads=8)
        except OSError:
            # Порт уже занят (например, mini_app.py запущен в отдельном процессе локально)
            pass
        except Exception as e:
            logger.warning("Веб-кабинет: %s", e)

    import threading
    threading.Thread(target=_run_mini_app, daemon=True).start()

    async with app:
        await app.start()
        # post_init вызывается сам только в run_polling(); при ручном запуске его нужно
        # вызвать явно — иначе не стартовали напоминания, мониторинг расписания,
        # проверка подписок и меню команд.
        await post_init(app)
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
