from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database import Database
from handlers.documents import DOC_NAMES, CAT_DOCS

ADMIN_LOGIN    = "Unicorn"
ADMIN_PASSWORD = "Gulkhan"

# Список всех типов документов для выбора при загрузке образца
ALL_DOC_TYPES = []
for cat_list in CAT_DOCS.values():
    for d in cat_list:
        if d not in ALL_DOC_TYPES:
            ALL_DOC_TYPES.append(d)


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

        ADMIN_CHAT_ID = 6561112046  # только в этот чат шлются кнопки активации после оплаты

        # Активация подписки по кнопке из уведомления об оплате —
        # доступна сразу в личном чате владельца, без отдельного входа в /mernar
        if data.startswith("admin_activate_id_") and update.effective_user.id == ADMIN_CHAT_ID:
            tg_id = int(data.split("_")[-1])
            await self.db.activate_subscription(tg_id)
            await query.edit_message_caption(
                caption=query.message.caption + "\n\n✅ *PRO АКТИВИРОВАН*",
                parse_mode=ParseMode.MARKDOWN
            )
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text="🎉 *Поздравляем! Подписка PRO активирована.*\n\nТеперь у вас безлимитная генерация документов!",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            return

        if not context.user_data.get("admin_auth"):
            await query.edit_message_text("❌ Нет доступа.")
            return

        if data == "admin_stats":
            await self._show_stats(query)
        elif data == "admin_users":
            await self._show_users(query, filt="all")
        elif data == "admin_users_pro":
            await self._show_users(query, filt="pro")
        elif data == "admin_users_free":
            await self._show_users(query, filt="free")
        elif data == "admin_users_kg":
            await self._show_users(query, filt="kg")
        elif data == "admin_docs":
            await self._show_recent_docs(query)
        elif data == "admin_broadcast":
            context.user_data["step"] = "admin_broadcast"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await query.edit_message_text("📢 Введите текст рассылки:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_activate_btn":
            context.user_data["step"] = "admin_activate"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await query.edit_message_text("💳 Введите Telegram ID для активации PRO:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_deactivate_btn":
            context.user_data["step"] = "admin_deactivate"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
            await query.edit_message_text("🔓 Введите Telegram ID для отмены PRO:", reply_markup=InlineKeyboardMarkup(kb))
        elif data == "admin_menu":
            await self._show_menu(query)

        # ── ОБУЧЕНИЕ БОТА (образцы документов) ──
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
            await query.edit_message_text(
                f"📄 Тип документа: *{name}*\n\nНа каком языке образец?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
        elif data.startswith("admin_sample_lang_"):
            doc_lang = data.split("_")[-1]
            context.user_data["sample_lang"] = doc_lang
            context.user_data["step"] = "admin_sample_text"
            kb = [[InlineKeyboardButton("← Назад", callback_data="admin_samples_add")]]
            await query.edit_message_text(
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
                await update.message.reply_text("❌ Неверный ID.")
            context.user_data["step"] = "admin_panel"
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_deactivate":
            try:
                tg_id = int(text.strip())
                await self.db.deactivate_subscription(tg_id)
                await update.message.reply_text(f"✅ Подписка отменена для {tg_id}")
            except:
                await update.message.reply_text("❌ Неверный ID.")
            context.user_data["step"] = "admin_panel"
            await self._send_menu(update.message.chat_id, context)

        elif step == "admin_broadcast":
            users = await self.db.get_all_users(limit=10000)
            sent, failed = 0, 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u["tg_id"], text=text, parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                except:
                    failed += 1
            context.user_data["step"] = "admin_panel"
            await update.message.reply_text(f"✅ Доставлено: {sent}\n❌ Не доставлено: {failed}")
            await self._send_menu(update.message.chat_id, context)

        # ── Сохранение образца ──
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
        await query.edit_message_text(
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
             InlineKeyboardButton("🔓 Снять PRO", callback_data="admin_deactivate_btn")],
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
            text += f"  • {row[0]}: {row[1]} шт.\n"
        kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_users(self, query, filt="all"):
        users = await self.db.get_all_users(limit=500)
        if filt == "pro":
            users = [u for u in users if u.get("subscribed")]
            title = "⭐ PRO пользователи"
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
                sub = "⭐" if u.get("subscribed") else "🆓"
                role_emoji = "🧸" if u.get("role") == "kindergarten" else "🏫"
                lines.append(
                    f"{sub}{role_emoji} `{u['tg_id']}` — {u.get('name') or 'без имени'}\n"
                    f"   {u.get('school') or '—'} | докум: {u.get('free_used', 0)}"
                )

        kb = [
            [InlineKeyboardButton("⭐ PRO", callback_data="admin_users_pro"),
             InlineKeyboardButton("🆓 Free", callback_data="admin_users_free")],
            [InlineKeyboardButton("🧸 Садики", callback_data="admin_users_kg"),
             InlineKeyboardButton("👥 Все", callback_data="admin_users")],
            [InlineKeyboardButton("💳 Активировать PRO", callback_data="admin_activate_btn")],
            [InlineKeyboardButton("← Назад", callback_data="admin_menu")],
        ]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n_...обрезано_"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def _show_recent_docs(self, query):
        docs = await self.db.get_recent_documents(limit=15)
        if not docs:
            lines = ["📄 *Последние документы*\n\nПока нет."]
        else:
            lines = ["📄 *Последние документы:*\n"]
            for d in docs:
                date = d["created_at"][:16] if d.get("created_at") else "—"
                lines.append(f"📄 {d['doc_name']}\n   👤 `{d['teacher_id']}` | ⭐{d['score']}/100 | {date}")
        kb = [[InlineKeyboardButton("← Назад", callback_data="admin_menu")]]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n_...обрезано_"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    # ════════════════════════════════════════════════
    # ОБУЧЕНИЕ БОТА — загрузка образцов документов
    # ════════════════════════════════════════════════
    async def _show_samples_menu(self, query):
        samples = await self.db.get_all_samples(limit=200)
        text = (
            f"🎓 *Обучение бота*\n{'─'*22}\n\n"
            f"Загружено образцов: *{len(samples)}*\n\n"
            f"Загрузи реальный, качественный документ — бот будет генерировать "
            f"новые документы того же типа, ориентируясь на стиль и структуру твоего образца.\n\n"
            f"Чем больше хороших образцов — тем точнее генерация."
        )
        kb = [
            [InlineKeyboardButton("➕ Загрузить образец", callback_data="admin_samples_add")],
            [InlineKeyboardButton("📚 Список образцов", callback_data="admin_samples_list")],
            [InlineKeyboardButton("← Назад", callback_data="admin_menu")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

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

        await query.edit_message_text(
            "📄 Выберите тип документа для образца:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    async def _show_samples_list(self, query):
        samples = await self.db.get_all_samples(limit=30)
        if not samples:
            kb = [
                [InlineKeyboardButton("➕ Загрузить образец", callback_data="admin_samples_add")],
                [InlineKeyboardButton("← Назад", callback_data="admin_samples")],
            ]
            await query.edit_message_text("📚 Образцов пока нет.", reply_markup=InlineKeyboardMarkup(kb))
            return

        lines = ["📚 *Загруженные образцы:*\n"]
        kb = []
        names = DOC_NAMES.get("ru", {})
        for s in samples:
            status = "✅" if s.get("is_active") else "⏸"
            doc_name = names.get(s["doc_type"], s["doc_type"])
            lines.append(f"{status} #{s['id']} {doc_name} ({s['lang']}) — {s['created_at'][:10]}")
            kb.append([
                InlineKeyboardButton(f"#{s['id']} {'Выкл' if s.get('is_active') else 'Вкл'}", callback_data=f"admin_sample_toggle_{s['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"admin_sample_del_{s['id']}"),
            ])
        kb.append([InlineKeyboardButton("← Назад", callback_data="admin_samples")])

        text = "\n".join(lines)
        if len(text) > 3500:
            text = text[:3400] + "\n\n_...обрезано_"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
