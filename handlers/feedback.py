"""Опрос ушедших: «что помешало создать документ?».

Админ запускает рассылку из админ-панели. Пользователь отвечает кнопкой
или своими словами и получает +2 бесплатных документа (один раз).
Ответы видны в админ-панели («Воронка») и сразу пересылаются админам.
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

REASONS = {
    "hard": ("Сложно разобраться", "Түсіну қиын"),
    "long": ("Слишком много вопросов", "Сұрақ тым көп"),
    "nodoc": ("Не нашёл(ла) нужный документ", "Керек құжатты таппадым"),
    "quality": ("Документ не подошёл по качеству", "Құжат сапасы ұнамады"),
    "trust": ("Не доверяю / боюсь за данные", "Сенбеймін / деректерге алаңдаймын"),
    "look": ("Просто посмотреть заходил(а)", "Жай қарап шықтым"),
}

WINBACK_TEXT = {
    "ru": (
        "Здравствуйте! Это Docura 👋\n\n"
        "Вы заходили к нам, но так и не создали документ. Подскажите честно, что помешало? "
        "Мы исправляем бота по вашим ответам.\n\n"
        "За ответ — 🎁 +2 бесплатных документа."
    ),
    "kz": (
        "Сәлеметсіз бе! Бұл Docura 👋\n\n"
        "Сіз бізге кірдіңіз, бірақ құжат жасамадыңыз. Шынымен айтыңызшы, не кедергі болды? "
        "Біз ботты сіздің жауаптарыңыз бойынша түзетеміз.\n\n"
        "Жауап үшін — 🎁 +2 тегін құжат."
    ),
}


def winback_keyboard(lang: str) -> InlineKeyboardMarkup:
    i = 1 if lang == "kz" else 0
    rows = [[InlineKeyboardButton(v[i], callback_data=f"fb_{k}")] for k, v in REASONS.items()]
    rows.append([InlineKeyboardButton("✍️ Напишу своими словами" if i == 0 else "✍️ Өз сөзіммен жазамын",
                                      callback_data="fb_text")])
    return InlineKeyboardMarkup(rows)


class FeedbackHandler:
    def __init__(self, db, admin_ids):
        self.db = db
        self.admin_ids = admin_ids

    async def _thanks(self, message, lang, first):
        bonus = ("🎁 +2 бесплатных документа уже начислены." if lang != "kz" else "🎁 +2 тегін құжат қосылды.") if first else ""
        text = ("Спасибо, это очень помогает! " if lang != "kz" else "Рахмет, бұл қатты көмектеседі! ") + bonus
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📄 Создать документ" if lang != "kz" else "📄 Құжат жасау", callback_data="menu_create")]])
        await message.reply_text(text, reply_markup=kb)

    async def _notify_admins(self, context, user, reason, text=""):
        who = f"{user.full_name} (@{user.username})" if user.username else f"{user.full_name} ({user.id})"
        body = f"💬 Ответ на опрос от {who}\nПричина: {reason}"
        if text:
            body += f"\n«{text[:1500]}»"
        for admin_id in self.admin_ids:
            try:
                await context.bot.send_message(admin_id, body)
            except Exception:
                pass

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        db_user = await self.db.get_user(user.id) or {}
        lang = db_user.get("lang") or "ru"
        key = query.data[3:]
        if key == "text":
            context.user_data["awaiting_feedback"] = True
            await query.edit_message_text(
                "Напишите одним сообщением, что помешало. Можно коротко 🙏" if lang != "kz"
                else "Не кедергі болғанын бір хабарламамен жазыңыз 🙏"
            )
            return
        if key not in REASONS:
            return
        label = REASONS[key][0]
        first = await self.db.save_feedback(user.id, key)
        try:
            await self.db.log_funnel_event(user.id, "feedback", key)
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await self._thanks(query.message, lang, first)
        await self._notify_admins(context, user, label)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Группа -1: перехватывает только ответ на опрос."""
        if not context.user_data.get("awaiting_feedback"):
            return
        context.user_data.pop("awaiting_feedback", None)
        user = update.effective_user
        text = (update.effective_message.text or "").strip()
        db_user = await self.db.get_user(user.id) or {}
        lang = db_user.get("lang") or "ru"
        first = await self.db.save_feedback(user.id, "text", text)
        try:
            await self.db.log_funnel_event(user.id, "feedback", "text")
        except Exception:
            pass
        await self._thanks(update.effective_message, lang, first)
        await self._notify_admins(context, user, "своими словами", text)
        raise ApplicationHandlerStop


async def send_winback(bot, db, limit: int = 1000) -> tuple[int, int]:
    """Рассылает опрос; возвращает (отправлено, не доставлено)."""
    sent = failed = 0
    for u in await db.get_winback_candidates(limit):
        lang = u.get("lang") or "ru"
        try:
            await bot.send_message(u["tg_id"], WINBACK_TEXT.get(lang, WINBACK_TEXT["ru"]),
                                   reply_markup=winback_keyboard(lang))
            sent += 1
        except Exception:
            failed += 1
        await db.mark_winback_sent(u["tg_id"])
        await asyncio.sleep(0.06)
    return sent, failed
