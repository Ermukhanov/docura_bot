from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest
import asyncio
import logging
from database import Database
from handlers.documents import DOC_NAMES, CAT_DOCS_ALL
import os

logger = logging.getLogger(__name__)

# Доступ к админке — по Telegram ID (переменная окружения ADMIN_IDS, через запятую).
# Раньше вход был по логину/паролю, а флаг admin_auth хранился в user_data —
# он стирался при любом context.user_data.clear() (создание документа, /cancel,
# /new, онбординг) и при каждом перезапуске бота, после чего ВСЕ кнопки админки
# отвечали «Нет доступа». Telegram ID подделать нельзя, а хранить его не надо.
def _parse_admin_ids():
    raw = os.getenv("ADMIN_IDS", "6561112046")
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids

ADMIN_IDS = _parse_admin_ids()


def is_admin(user_id) -> bool:
    return user_id in ADMIN_IDS


def _esc(text) -> str:
    """Экранирование для Telegram Markdown (v1): имена, школы и названия
    документов с символами _ * ` [ ломали разбор и кнопка «молча» не работала."""
    text = "" if text is None else str(text)
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


async def _safe_edit(query, text, **kwargs):
    """edit_message_text, который не падает на типичных ошибках Telegram:
    «message is not modified» (повторное нажатие) и ошибке разбора Markdown."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return None
        if "parse entities" in msg or "find end of the entity" in msg:
            kwargs.pop("parse_mode", None)
            try:
                return await query.edit_message_text(text, **kwargs)
            except BadRequest as e2:
                if "message is not modified" in str(e2).lower():
                    return None
                raise
        raise


ALL_DOC_TYPES = []
for cat_list in CAT_DOCS_ALL.values():
    for d in cat_list:
        if d not in ALL_DOC_TYPES:
            ALL_DOC_TYPES.append(d)


class AdminHandler:
    def __init__(self, db: Database):
        self.db = db

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/mernar — открывает админ-меню, если ID в ADMIN_IDS."""
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Нет доступа.")
            return
        context.user_data["step"] = "admin_panel"
        await self._send_menu(update.message.chat_id, context)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data  = query.data

        TIER_NAMES = {"pro": "PRO", "max": "MAX", "b2b": "B2B"}

        if not is_admin(update.effective_user.id):
            await query.answer("⛔ Нет доступа", show_alert=True)
            return

        if data.startswith("admin_activate_") and data != "admin_activate_btn" and data != "admin_activate_max_btn":
            rest = data[len("admin_activate_"):]
            try:
                if rest.startswith("id_"):
                    tier = "pro"
                    tg_id = int(rest[3:])
                else:
                    tier_part, _, tg_id_part = rest.partition("_")
                    tier = tier_part if tier_part in TIER_NAMES else "pro"
                    tg_id = int(tg_id_part) if tg_id_part else int(rest)
            except ValueError:
                await query.answer("❌ Некорректный ID", show_alert=True)
                return

            await self.db.activate_subscription(tg_id, tier=tier)
            t_name = TIER_NAMES.get(tier, "PRO")
            note = f"\n\n✅ {t_name.upper()} АКТИВИРОВАН"
            try:
                if query.message.caption is not None:
                    await query.edit_message_caption(caption=query.message.caption + note)
                else:
                    await _safe_edit(query, (query.message.text or "") + note)
            except BadRequest as e:
                logger.warning("admin activate: cannot annotate message: %s", e)
            try:
                extra_perks = "\n💎 Включает интеграцию с Kundelik.kz / BilimClass, презентации и автопилот!" if tier == "max" else ""
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=f"🎉 *Поздравляем! Подписка {t_name} активирована.*\n\nТеперь у вас безлимитный доступ к документам!{extra_perks}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        if data == "admin_stats":
            await self._show_stats(query)
        elif data == "admin_users":
            await self._show_users(query, filt="all")
        elif data == "admin_users_pro":
            await self._show_users(query, filt="pro")
        elif data == "admin_users_max":
            await self._show_users(query, filt="max")
        elif data == "admin_users_free":
            await self._show_users(query, filt="free")
        elif data == "admin_users_kg":
            await self._show_users(query, filt="kg")
        elif data == "admin_docs":
            await self._show_recent_docs(query)
        elif data == "admin_broadcast":
            context.user_data["step"] = "admin_broadcast"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await _safe_edit(query, "📢 Введите текст рассылки:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_activate_btn":
            context.user_data["step"] = "admin_activate"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await _safe_edit(query, "💳 Введите Telegram ID для активации PRO:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_activate_max_btn":
            context.user_data["step"] = "admin_activate_max"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await _safe_edit(query, "💎 Введите Telegram ID для активации MAX (Kundelik + BilimClass + Автопилот):", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_deactivate_btn":
            context.user_data["step"] = "admin_deactivate"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await _safe_edit(query, "🔓 Введите Telegram ID для отмены PRO:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_reset_btn":
            context.user_data["step"] = "admin_reset"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await _safe_edit(query, 
                "♻️ Введите Telegram ID пользователя для сброса аккаунта:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        elif data.startswith("admin_reset_confirm_"):
            tg_id = int(data[len("admin_reset_confirm_"):])
            if await self.db.reset_user_account(tg_id):
                target_data = context.application.user_data.get(tg_id)
                if target_data is not None:
                    target_data.clear()
                context.user_data["step"] = "admin_panel"
                await _safe_edit(query, 
                    "✅ Аккаунт пользователя сброшен. Пользователь не удален и не заблокирован",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Главное меню", callback_data="admin_menu")]])
                )
            else:
                await _safe_edit(query, 
                    "❌ Пользователь с таким Telegram ID не найден.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Главное меню", callback_data="admin_menu")]])
                )
        elif data == "admin_reset_cancel":
            context.user_data["step"] = "admin_panel"
            await self._show_menu(query)
        elif data == "admin_menu":
            await self._show_menu(query)

        elif data == "admin_samples":
            await self._show_samples_menu(query)
        elif data == "admin_samples_add":
            await self._show_doc_type_picker(query, page=0)
        elif data.startswith("admin_samples_page_"):
            page = int(data.split("_")[-1])
            await self._show_doc_type_picker(query, page=page)
        elif data.startswith("admin_samples_pick_"):
            doc_type = data[len("admin_samples_pick_"):]
            context.user_data["sample_doc_type"] = doc_type
            context.user_data["step"] = "admin_sample_lang"
            kb = [
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="admin_sample_lang_ru"),
                 InlineKeyboardButton("🇰🇿 Қазақша", callback_data="admin_sample_lang_kz")],
                [InlineKeyboardButton("← Назад", callback_data="admin_samples_add")],
            ]
            name = DOC_NAMES.get("ru", {}).get(doc_type, doc_type)
            await _safe_edit(query, 
                f"📄 Тип документа: *{name}*\n\nНа каком языке образец?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
        elif data.startswith("admin_sample_lang_"):
            doc_lang = data.split("_")[-1]
            context.user_data["sample_lang"] = doc_lang
            context.user_data["step"] = "admin_sample_text"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_samples_add")]]
            await _safe_edit(query, 
                "✍️ Отправьте текст образца документа.\n\n"
                "Скопируйте готовый, реальный, качественный документ — бот будет ориентироваться "
                "на его структуру и стиль при генерации этого типа документов.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        elif data == "admin_samples_list":
            await self._show_samples_list(query)
        elif data.startswith("admin_sample_del_"):
            sample_id = int(data.split("_")[-1])
            await self.db.delete_sample(sample_id)
            await query.answer("🗑 Удалено", show_alert=False)
            await self._show_samples_list(query)
        elif data.startswith("admin_sample_toggle_"):
            sample_id = int(data.split("_")[-1])
            await self.db.toggle_sample(sample_id)
            await self._show_samples_list(query)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        step  = context.user_data.get("step", "")
        text  = update.message.text.strip()

        if not is_admin(update.effective_user.id):
            context.user_data["step"] = None
            await update.message.reply_text("⛔ Нет доступа.")
            return

        if step == "admin_panel":
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_activate":
            try:
                tg_id = int(text.strip())
                await self.db.activate_subscription(tg_id, tier="pro")
                await update.message.reply_text(f"✅ Подписка PRO активирована для {tg_id}")
                try:
                    await context.bot.send_message(
                        chat_id=tg_id,
                        text="🎉 *Поздравляем! Подписка PRO активирована.*\n\nТеперь у вас безлимитный доступ к документам!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
            except Exception:
                await update.message.reply_text("❌ Неверный ID.")
            context.user_data["step"] = "admin_panel"
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_activate_max":
            try:
                tg_id = int(text.strip())
                await self.db.activate_subscription(tg_id, tier="max")
                await update.message.reply_text(f"💎 Подписка MAX активирована для {tg_id}!\nВключено: Kundelik.kz, BilimClass, презентации, автопилот.")
                try:
                    await context.bot.send_message(
                        chat_id=tg_id,
                        text=(
                            "🎉 *Поздравляем! Вам активирован тариф MAX!*\n\n"
                            "💎 *Все премиальные функции доступны:*\n"
                            "• Интеграция с Kundelik.kz и BilimClass (выставление оценок и расписание)\n"
                            "• Безлимитная генерация документов и презентаций PowerPoint\n"
                            "• Автопилот расписания и мониторинг"
                        ),
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
            except Exception:
                await update.message.reply_text("❌ Неверный ID.")
            context.user_data["step"] = "admin_panel"
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_deactivate":
            try:
                tg_id = int(text.strip())
                await self.db.deactivate_subscription(tg_id)
                await update.message.reply_text(f"✅ Подписка отменена для {tg_id}")
            except Exception:
                await update.message.reply_text("❌ Неверный ID.")
            context.user_data["step"] = "admin_panel"
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_reset":
            try:
                tg_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ Неверный Telegram ID. Введите число.")
                return

            user = await self.db.get_user(tg_id)
            if not user:
                await update.message.reply_text("❌ Пользователь с таким Telegram ID не найден.")
                return

            context.user_data["step"] = "admin_panel"
            kb = [
                [InlineKeyboardButton("Да, сбросить", callback_data=f"admin_reset_confirm_{tg_id}")],
                [InlineKeyboardButton("Отмена", callback_data="admin_reset_cancel")],
            ]
            await update.message.reply_text(
                "Сбросить профиль и онбординг пользователя?\n"
                "Пользователь останется в базе. Оплата, PRO-доступ, история оплат и реферальные бонусы сохранятся",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif step == "admin_broadcast":
            users = await self.db.get_all_users(limit=100000)
            sent, failed = 0, 0
            for u in users:
                try:
                    try:
                        await context.bot.send_message(chat_id=u["tg_id"], text=text, parse_mode=ParseMode.MARKDOWN)
                    except BadRequest as e:
                        if "parse entities" in str(e).lower():
                            await context.bot.send_message(chat_id=u["tg_id"], text=text)
                        else:
                            raise
                    sent += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)  # лимит Telegram ~30 сообщений/сек
            context.user_data["step"] = "admin_panel"
            await update.message.reply_text(f"✅ Доставлено: {sent}\n❌ Не доставлено: {failed}")
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_sample_text":
            doc_type = context.user_data.get("sample_doc_type", "")
            doc_lang = context.user_data.get("sample_lang", "ru")
            title = DOC_NAMES.get("ru", {}).get(doc_type, doc_type)

            if len(text) < 50:
                await update.message.reply_text("⚠️ Слишком короткий образец. Пришлите полный текст документа (минимум 50 символов).")
                return

            await self.db.add_sample(
                doc_type=doc_type, lang=doc_lang, content=text,
                title=title, added_by=update.effective_user.id
            )
            context.user_data["step"] = "admin_panel"
            kb = [
                [InlineKeyboardButton("➕ Добавить ещё", callback_data="admin_samples_add")],
                [InlineKeyboardButton("📚 Список образцов", callback_data="admin_samples_list")],
                [InlineKeyboardButton("← Главное меню", callback_data="admin_menu")],
            ]
            await update.message.reply_text(
                f"✅ Образец «{title}» ({doc_lang}) сохранён!\n\n"
                f"Теперь бот будет использовать его как эталон при генерации этого типа документов.",
                reply_markup=InlineKeyboardMarkup(kb)
            )

    async def _send_menu(self, chat_id, context):
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛠 *Админ-панель Docura.kz*",
            reply_markup=InlineKeyboardMarkup(self._main_keyboard()),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _show_menu(self, query):
        await _safe_edit(query, 
            "🛠 *Админ-панель Docura.kz*",
            reply_markup=InlineKeyboardMarkup(self._main_keyboard()),
            parse_mode=ParseMode.MARKDOWN
        )

    def _main_keyboard(self):
        return [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 Все пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("⭐ PRO", callback_data="admin_users_pro"),
             InlineKeyboardButton("🆓 Free", callback_data="admin_users_free")],
            [InlineKeyboardButton("🧸 Садики", callback_data="admin_users_kg")],
            [InlineKeyboardButton("📄 Последние документы", callback_data="admin_docs")],
            [InlineKeyboardButton("🎓 Обучить бота (образцы)", callback_data="admin_samples")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("💳 Активировать PRO", callback_data="admin_activate_btn"),
             InlineKeyboardButton("💎 Активировать MAX", callback_data="admin_activate_max_btn")],
            [InlineKeyboardButton("🔓 Снять подписку", callback_data="admin_deactivate_btn"),
             InlineKeyboardButton("♻️ Сбросить аккаунт", callback_data="admin_reset_btn")],
        ]

    async def _show_stats(self, query):
        stats = await self.db.get_admin_stats()
        text = (
            f"📊 *Статистика Docura.kz*\n{'─'*22}\n\n"
            f"👥 Пользователей всего: *{stats['total_users']}*\n"
            f"📈 За неделю: *+{stats.get('week_users', 0)}*\n\n"
            f"⭐ PRO подписчиков: *{stats['subscribed']}*\n"
            f"📊 Конверсия: *{(stats['subscribed'] / stats['total_users'] * 100) if stats['total_users'] else 0:.1f}%*\n"
            f"💰 Доход/мес: *{stats['revenue']:,} тг*\n\n"
            f"📄 Документов всего: *{stats['total_docs']}*\n"
            f"📄 Сегодня: *{stats['today_docs']}*\n\n"
            f"🏆 *Топ документов:*\n"
        )
        for row in stats["top_docs"]:
            text += f"  • {_esc(DOC_NAMES.get('ru', {}).get(row[0], row[0]))}: {row[1]} шт.\n"
        kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_users(self, query, filt="all"):
        users = await self.db.get_all_users(limit=500)
        if filt == "pro":
            users = [u for u in users if u.get("subscribed") and (u.get("tier") == "pro" or not u.get("tier"))]
            title = "⭐ PRO пользователи"
        elif filt == "max":
            users = [u for u in users if u.get("subscribed") and u.get("tier") == "max"]
            title = "💎 MAX пользователи"
        elif filt == "free":
            users = [u for u in users if not u.get("subscribed")]
            title = "🆓 Бесплатные пользователи"
        elif filt == "kg":
            users = [u for u in users if u.get("role") == "kindergarten"]
            title = "🧸 Воспитатели садиков"
        else:
            title = "👥 Все пользователи"

        users = users[:25]
        if not users:
            lines = [f"{title}\n\nНикого не найдено."]
        else:
            lines = [f"*{title}* ({len(users)})\n"]
            for u in users:
                tier = (u.get("tier") or "pro").upper() if u.get("subscribed") else ""
                sub = f"💎{tier}" if tier == "MAX" else "⭐PRO" if tier == "PRO" else "🆓"
                role_emoji = "🧸" if u.get("role") == "kindergarten" else "🏫"
                lines.append(
                    f"{sub} {role_emoji} `{u['tg_id']}` — {_esc(u.get('name') or 'без имени')}\n"
                    f"   {_esc(u.get('school') or '—')} | докум: {u.get('free_used', 0)}"
                )

        kb = [
            [InlineKeyboardButton("⭐ PRO", callback_data="admin_users_pro"),
             InlineKeyboardButton("💎 MAX", callback_data="admin_users_max"),
             InlineKeyboardButton("🆓 Free", callback_data="admin_users_free")],
            [InlineKeyboardButton("🧸 Садики", callback_data="admin_users_kg"),
             InlineKeyboardButton("👥 Все", callback_data="admin_users")],
            [InlineKeyboardButton("💳 Активировать PRO", callback_data="admin_activate_btn"),
             InlineKeyboardButton("💎 Активировать MAX", callback_data="admin_activate_max_btn")],
            [InlineKeyboardButton("← Назад", callback_data="admin_menu")],
        ]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n_...обрезано_"
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_recent_docs(self, query):
        docs = await self.db.get_recent_documents(limit=15)
        if not docs:
            lines = ["📄 *Последние документы*\n\nПока нет."]
        else:
            lines = ["📄 *Последние документы:*\n"]
            for d in docs:
                date = str(d["created_at"])[:16] if d.get("created_at") else "—"
                lines.append(f"📄 {_esc(d.get('doc_name'))}\n   👤 `{d.get('teacher_id')}` | ⭐{d.get('score', 0)}/100 | {date}")
        kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n_...обрезано_"
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_samples_menu(self, query):
        samples = await self.db.get_all_samples(limit=200)
        text = (
            f"🎓 *Обучение бота*\n{'─'*22}\n\n"
            f"Загружено образцов: *{len(samples)}*\n\n"
            f"Загрузи реальный, качественный документ (школьный или садиковский) — бот будет "
            f"генерировать новые документы того же типа, ориентируясь на стиль и структуру твоего образца.\n\n"
            f"Чем больше хороших образцов — тем точнее генерация."
        )
        kb = [
            [InlineKeyboardButton("➕ Загрузить образец", callback_data="admin_samples_add")],
            [InlineKeyboardButton("📚 Список образцов", callback_data="admin_samples_list")],
            [InlineKeyboardButton("← Назад", callback_data="admin_menu")],
        ]
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_doc_type_picker(self, query, page=0):
        per_page = 8
        start = page * per_page
        chunk = ALL_DOC_TYPES[start:start + per_page]
        names = DOC_NAMES.get("ru", {})

        kb = [[InlineKeyboardButton(names.get(d, d), callback_data=f"admin_samples_pick_{d}")] for d in chunk]

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_samples_page_{page-1}"))
        if start + per_page < len(ALL_DOC_TYPES):
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_samples_page_{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("← Назад", callback_data="admin_samples")])

        await _safe_edit(query, 
            "📄 Выберите тип документа для образца (школа + садик):",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    async def _show_samples_list(self, query):
        samples = await self.db.get_all_samples(limit=30)
        if not samples:
            kb = [
                [InlineKeyboardButton("➕ Загрузить образец", callback_data="admin_samples_add")],
                [InlineKeyboardButton("← Назад", callback_data="admin_samples")],
            ]
            await _safe_edit(query, "📚 Образцов пока нет.", reply_markup=InlineKeyboardMarkup(kb))
            return

        lines = ["📚 *Загруженные образцы:*\n"]
        kb = []
        names = DOC_NAMES.get("ru", {})
        for s in samples:
            status = "✅" if s.get("is_active") else "⏸"
            doc_name = names.get(s["doc_type"], s["doc_type"])
            lines.append(f"{status} #{s['id']} {_esc(doc_name)} ({s['lang']}) — {str(s['created_at'])[:10]}")
            kb.append([
                InlineKeyboardButton(f"#{s['id']} {'Выкл' if s.get('is_active') else 'Вкл'}", callback_data=f"admin_sample_toggle_{s['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"admin_sample_del_{s['id']}"),
            ])
        kb.append([InlineKeyboardButton("← Назад", callback_data="admin_samples")])

        text = "\n".join(lines)
        if len(text) > 3500:
            text = text[:3400] + "\n\n_...обрезано_"
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
