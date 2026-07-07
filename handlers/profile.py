import json
import base64
import hashlib
import anthropic
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

MENU_BTN   = lambda lang: InlineKeyboardButton("🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"), callback_data="menu_main")
BACK_BTN   = lambda lang, cb: InlineKeyboardButton("◀️ " + ("Назад" if lang == "ru" else "Артқа"), callback_data=cb)
CANCEL_BTN = lambda lang: InlineKeyboardButton("❌ " + ("Отмена" if lang == "ru" else "Болдырмау"), callback_data="menu_main")

KASPI_NUMBER = "+7 771 451 4717"
TIER_AMOUNTS = {2490: "basic", 3990: "pro"}
TIER_NAMES   = {"basic": "Базовый", "pro": "PRO"}

def _esc(text):
    if text is None: return "—"
    text = str(text)
    for ch in ["_", "*", "[", "]", "`"]:
        text = text.replace(ch, "\\" + ch)
    return text

class ProfileHandler:
    def __init__(self, db: Database, api_key: str = ""):
        self.db      = db
        self.api_key = api_key

    async def show(self, query, user_id, lang):
        user = await self.db.get_user(user_id)
        if not user:
            return

        is_kg = user.get("role") == "kindergarten"

        name     = user.get("name", "—")
        school   = user.get("school", "—")
        position = user.get("position", "—")
        director = user.get("director", "—")
        is_pro   = user.get("subscribed", 0)
        free_used = user.get("free_used", 0)
        free_left = max(0, 3 - free_used)

        if is_pro:
            sub_line = "⭐ *PRO* — безлимитный доступ активен" if lang == "ru" else "⭐ *PRO* — шексіз қол жеткізу белсенді"
        else:
            sub_line = f"🆓 Бесплатно — осталось *{free_left}/3* документов" if lang == "ru" else f"🆓 Тегін — қалды *{free_left}/3* құжат"

        if is_kg:
            age_group = user.get("age_group", "—")
            if lang == "ru":
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МОЙ ПРОФИЛЬ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *ФИО:* {name}\n"
                    f"🏫 *Детский сад:* {school}\n"
                    f"👶 *Возрастная группа:* {age_group}\n"
                    f"💼 *Должность:* {position}\n"
                    f"👔 *Заведующая:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Создано документов: *{free_used}*"
                )
            else:
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МЕНІҢ ПРОФИЛІМ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *Аты-жөні:* {name}\n"
                    f"🏫 *Балабақша:* {school}\n"
                    f"👶 *Жас тобы:* {age_group}\n"
                    f"💼 *Лауазым:* {position}\n"
                    f"👔 *Меңгеруші:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Жасалған құжаттар: *{free_used}*"
                )
        else:
            subject  = user.get("subject", "—")
            classes  = user.get("classes", "—")
            is_ct    = "✅" if user.get("is_class_teacher") else "❌"
            if lang == "ru":
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МОЙ ПРОФИЛЬ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *ФИО:* {name}\n"
                    f"🏫 *Школа:* {school}\n"
                    f"📚 *Предмет:* {subject}\n"
                    f"🏷 *Классы:* {classes}\n"
                    f"💼 *Должность:* {position}\n"
                    f"👔 *Директор:* {director}\n"
                    f"🏫 *Кл.рук:* {is_ct}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Создано документов: *{free_used}*"
                )
            else:
                text = (
                    f"{'⭐ PRO ПРОФИЛЬ' if is_pro else '👤 МЕНІҢ ПРОФИЛІМ'}\n"
                    f"{'━' * 28}\n\n"
                    f"📛 *Аты-жөні:* {name}\n"
                    f"🏫 *Мектеп:* {school}\n"
                    f"📚 *Пән:* {subject}\n"
                    f"🏷 *Сыныптар:* {classes}\n"
                    f"💼 *Лауазым:* {position}\n"
                    f"👔 *Директор:* {director}\n\n"
                    f"{'━' * 28}\n"
                    f"{sub_line}\n"
                    f"📄 Жасалған құжаттар: *{free_used}*"
                )

        students_btn_text = t(lang, "btn_my_children") if is_kg else t(lang, "btn_my_students")

        keyboard = [
            [InlineKeyboardButton("✏️ " + t(lang, "btn_edit_profile"), callback_data="prof_edit")],
            [InlineKeyboardButton("👥 " + students_btn_text,           callback_data="prof_students")],
            [InlineKeyboardButton("⭐ " + t(lang, "btn_subscription"),  callback_data="prof_sub")],
            [InlineKeyboardButton("🌐 " + t(lang, "btn_change_lang"),   callback_data="prof_lang")],
            [MENU_BTN(lang)],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query   = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"

        if data == "prof_edit":
            context.user_data["step"] = "prof_edit_name"
            kb = [[CANCEL_BTN(lang)]]
            await query.edit_message_text(
                ("✏️ *Редактирование профиля*\n\nВведите ваше ФИО:" if lang == "ru"
                 else "✏️ *Профильді өңдеу*\n\nАты-жөніңізді енгізіңіз:"),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "prof_students":
            await self._show_students(query, user_id, lang, is_kg)

        elif data == "prof_sub":
            await self._show_subscription(query, user, lang)

        elif data in ("prof_choose_basic", "prof_choose_pro"):
            tier = "basic" if data == "prof_choose_basic" else "pro"
            context.user_data["chosen_tier"] = tier
            context.user_data["step"] = "waiting_payment_receipt"
            price = 2490 if tier == "basic" else 3990
            t_name = TIER_NAMES[tier]
            text = (
                f"💳 *Тариф {t_name} — {price} тг/мес*\n\n"
                f"Переведите *{price} тг* на Kaspi:\n"
                f"`{KASPI_NUMBER}`\n"
                f"_(нажмите чтобы скопировать)_\n\n"
                f"Затем пришлите *скриншот или файл чека* — подписка активируется автоматически ✅\n\n"
                f"_Бот сам проверит сумму и получателя_"
            ) if lang == "ru" else (
                f"💳 *{t_name} тарифі — {price} тг/ай*\n\n"
                f"*{price} тг* осы нөмірге аударыңыз:\n"
                f"`{KASPI_NUMBER}`\n\n"
                f"Чек скриншотын немесе файлын жіберіңіз — жазылым автоматты белсендіріледі ✅"
            )
            kb = [[BACK_BTN(lang, "prof_sub")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "prof_lang":
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="prof_set_lang_ru"),
                 InlineKeyboardButton("🇰🇿 Қазақша", callback_data="prof_set_lang_kz")],
                [MENU_BTN(lang)],
            ]
            text = "🌐 Выберите язык интерфейса:" if lang == "ru" else "🌐 Интерфейс тілін таңдаңыз:"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("prof_set_lang_"):
            new_lang = data.split("_")[-1]
            await self.db.upsert_user(user_id, {"lang": new_lang})
            kb = [[MENU_BTN(new_lang)]]
            await query.edit_message_text(
                "✅ Язык изменён на Русский!" if new_lang == "ru" else "✅ Тіл Қазақшаға өзгертілді!",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data == "prof_add_student":
            context.user_data["step"] = "student_name"
            context.user_data["new_student"] = {}
            kb = [[CANCEL_BTN(lang)]]
            name_q = (
                ("👤 *Добавление воспитанника*\n\nВведите полное имя ребёнка:" if is_kg else
                 "👤 *Добавление ученика*\n\nВведите полное имя ученика:")
                if lang == "ru" else
                ("👤 *Тәрбиеленуші қосу*\n\nБаланың толық атын енгізіңіз:" if is_kg else
                 "👤 *Оқушы қосу*\n\nОқушының толық атын енгізіңіз:")
            )
            await query.edit_message_text(name_q, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("student_del_"):
            student_id = int(data[12:])
            await self.db.delete_student(student_id)
            await query.answer("✅ Удалено" if lang == "ru" else "✅ Жойылды", show_alert=False)
            await self._show_students(query, user_id, lang, is_kg)

        elif data.startswith("student_view_"):
            student_id = int(data[13:])
            await self._show_student_detail(query, student_id, lang, is_kg)

        elif data == "prof_back_students":
            await self._show_students(query, user_id, lang, is_kg)

    async def _show_students(self, query, user_id, lang, is_kg=False):
        students = await self.db.get_students(user_id)
        keyboard = []
        add_btn_text = t(lang, "btn_add_child") if is_kg else t(lang, "btn_add_student")
        empty_text = t(lang, "no_children") if is_kg else t(lang, "no_students")
        title = ("👥 *Мои воспитанники*" if is_kg else "👥 *Мои ученики*") if lang == "ru" \
            else ("👥 *Менің тәрбиеленушілерім*" if is_kg else "👥 *Менің оқушыларым*")

        if not students:
            keyboard = [
                [InlineKeyboardButton("➕ " + add_btn_text, callback_data="prof_add_student")],
                [MENU_BTN(lang)],
            ]
            await query.edit_message_text(
                f"{title}\n\n{empty_text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        text = f"{title} ({len(students)})\n\n"
        for s in students:
            grades = json.loads(s.get("grades", "{}"))
            avg = round(sum(grades.values()) / len(grades), 1) if grades else "—"
            if is_kg:
                text += f"👶 {s['name']} • {s['class_name']}\n"
            else:
                text += f"👤 {s['name']} • {s['class_name']} • ср.балл: {avg}\n"

        for s in students:
            keyboard.append([
                InlineKeyboardButton(f"{'👶' if is_kg else '👤'} {s['name']} ({s['class_name']})", callback_data=f"student_view_{s['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"student_del_{s['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("➕ " + add_btn_text, callback_data="prof_add_student")])
        keyboard.append([MENU_BTN(lang)])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_student_detail(self, query, student_id, lang, is_kg=False):
        s = await self.db.get_student(student_id)
        if not s:
            return

        grades       = json.loads(s.get("grades", "{}"))
        achievements = json.loads(s.get("achievements", "[]"))
        grades_str   = "\n".join(f"  • {k}: {v}" for k, v in grades.items()) if grades else ("  нет данных" if lang == "ru" else "  деректер жоқ")
        achieve_str  = "\n".join(f"  • {a}" for a in achievements) if achievements else ("  нет" if lang == "ru" else "  жоқ")

        group_label = ("Группа" if lang == "ru" else "Тобы") if is_kg else ("Класс" if lang == "ru" else "Сынып")

        text = (
            f"{'👶' if is_kg else '👤'} *{s['name']}*\n"
            f"🏷 {group_label}: {s['class_name']}\n"
            f"😊 Поведение: {s.get('behavior', '—')}\n"
            f"📅 Пропуски: {s.get('absences', 0)} дн.\n\n"
            f"📊 *{'Достижения' if is_kg else 'Оценки'}:*\n{'' if is_kg else grades_str}\n\n"
            f"🏆 *Достижения:*\n{achieve_str}"
        ) if lang == "ru" else (
            f"{'👶' if is_kg else '👤'} *{s['name']}*\n"
            f"🏷 {group_label}: {s['class_name']}\n"
            f"😊 Мінез-құлық: {s.get('behavior', '—')}\n"
            f"📅 Өткізулер: {s.get('absences', 0)} күн\n\n"
            f"🏆 *Жетістіктер:*\n{achieve_str}"
        )

        keyboard = [
            [BACK_BTN(lang, "prof_back_students")],
            [MENU_BTN(lang)],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def _show_subscription(self, query, user, lang):
        tier = user.get("tier", "free")

        if user.get("subscribed") and tier in TIER_NAMES:
            t_name = TIER_NAMES[tier]
            feats = (
                "✅ Безлимитная генерация документов\n"
                "✅ Все типы документов\n"
                + ("✅ Голосовой ввод\n✅ База учеников\n✅ Точечное редактирование" if tier == "pro" else "")
            ) if lang == "ru" else (
                "✅ Шексіз құжат жасау\n"
                "✅ Барлық құжат түрлері\n"
                + ("✅ Дауыстық енгізу\n✅ Оқушылар базасы\n✅ Түзету" if tier == "pro" else "")
            )
            text = (f"⭐ *{t_name} подписка активна!*\n\n{feats}" if lang == "ru"
                    else f"⭐ *{t_name} жазылымы белсенді!*\n\n{feats}")
            keyboard = [[BACK_BTN(lang, "menu_profile")], [MENU_BTN(lang)]]
        else:
            free_left = max(0, 3 - user.get("free_used", 0))
            text = (
                f"💳 *Подписка Docura*\n\n"
                f"Осталось бесплатных: *{free_left}/3*\n\n"
                f"🔹 *Базовый — 2 490 тг/мес*\n"
                f"✅ Безлимитная генерация\n"
                f"✅ Все типы документов\n\n"
                f"⭐ *PRO — 3 990 тг/мес*\n"
                f"✅ Всё из Базового\n"
                f"✅ Голосовой ввод\n"
                f"✅ База учеников\n"
                f"✅ Точечное редактирование\n\n"
                f"👇 Выберите тариф — после оплаты пришлите чек и подписка активируется автоматически"
            ) if lang == "ru" else (
                f"💳 *Docura жазылымы*\n\n"
                f"Тегін қалды: *{free_left}/3*\n\n"
                f"🔹 *Негізгі — 2 490 тг/ай*\n"
                f"✅ Шексіз жасау\n"
                f"✅ Барлық құжат түрлері\n\n"
                f"⭐ *PRO — 3 990 тг/ай*\n"
                f"✅ Дауыстық енгізу\n"
                f"✅ Оқушылар базасы\n\n"
                f"👇 Тарифті таңдаңыз"
            )
            keyboard = [
                [InlineKeyboardButton("🔹 Базовый — 2 490 тг" if lang == "ru" else "🔹 Негізгі — 2 490 тг", callback_data="prof_choose_basic")],
                [InlineKeyboardButton("⭐ PRO — 3 990 тг", callback_data="prof_choose_pro")],
                [BACK_BTN(lang, "menu_profile")],
                [MENU_BTN(lang)],
            ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает скриншот чека Kaspi и проверяет его через Claude Vision"""
        await self._process_receipt(update, context, source="photo")

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает файл чека Kaspi (PDF или изображение) и проверяет через Claude Vision"""
        doc = update.message.document
        if not doc:
            return
        mime = doc.mime_type or ""
        if not any(x in mime for x in ["image", "pdf", "jpeg", "png"]):
            return
        await self._process_receipt(update, context, source="document")

    async def _process_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE, source: str):
        """Основная логика: скачать чек → проверить через Claude → активировать подписку"""
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        tier    = context.user_data.get("chosen_tier", "pro")

        wait_msg = await update.message.reply_text(
            "🔍 Проверяю чек..." if lang == "ru" else "🔍 Чек тексерілуде..."
        )

        try:
            if source == "photo":
                file_obj = await update.message.photo[-1].get_file()
                media_type = "image/jpeg"
            else:
                file_obj = await update.message.document.get_file()
                mime = update.message.document.mime_type or "image/jpeg"
                media_type = mime if "pdf" not in mime else "image/jpeg"

            import tempfile, os
            suffix = ".jpg" if "image" in media_type else ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                await file_obj.download_to_drive(tmp.name)
                tmp_path = tmp.name

            with open(tmp_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")

            receipt_hash = hashlib.sha256(img_data.encode()).hexdigest()[:16]
            os.unlink(tmp_path)

            used = await self.db.is_receipt_used(receipt_hash)
            if used:
                await wait_msg.delete()
                await update.message.reply_text(
                    "❌ Этот чек уже был использован для активации." if lang == "ru"
                    else "❌ Бұл чек бұрын қолданылған."
                )
                return

            today = datetime.now().strftime("%d.%m.%Y")
            client = anthropic.Anthropic(api_key=self.api_key)

            prompt = f"""Это чек оплаты Kaspi. Проверь следующее:
1. Получатель содержит номер телефона: {KASPI_NUMBER} (может быть записан без пробелов, с дефисами или скобками — это нормально)
2. Сумма равна одному из значений: {list(TIER_AMOUNTS.keys())} тенге
3. Дата операции — сегодня ({today}) или вчера (допустимо)

Ответь ТОЛЬКО в формате JSON без markdown:
{{"valid": true/false, "amount": число_или_null, "reason": "причина если false"}}

Если чек нечёткий или текст плохо читается — valid: false, reason: "нечёткий чек"."""

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=150,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )

            import re as re_mod
            raw = response.content[0].text.strip()
            raw = re_mod.sub(r"```[a-z]*", "", raw).strip("` \n")
            result = json.loads(raw)

        except Exception as e:
            print(f"Receipt check error: {e}")
            await wait_msg.delete()
            await update.message.reply_text(
                "❌ Не удалось прочитать чек. Пришлите более чёткий скриншот." if lang == "ru"
                else "❌ Чекті оқу мүмкін болмады. Нақтырақ скриншот жіберіңіз."
            )
            return

        await wait_msg.delete()

        if not result.get("valid"):
            reason_map = {
                "ru": {
                    "нечёткий чек": "Чек нечёткий — сделайте более чёткий скриншот.",
                    "неверная сумма": f"Неверная сумма. Нужно {list(TIER_AMOUNTS.keys())} тг.",
                    "другой получатель": f"Получатель не совпадает. Переводите на {KASPI_NUMBER}.",
                    "старый чек": "Чек устарел. Нужна оплата сегодняшним числом.",
                },
                "kz": {
                    "нечёткий чек": "Чек анық емес — нақтырақ скриншот жіберіңіз.",
                    "неверная сумма": f"Сумма дұрыс емес. {list(TIER_AMOUNTS.keys())} тг керек.",
                    "другой получатель": f"Алушы сәйкес емес. {KASPI_NUMBER} нөміріне аударыңыз.",
                    "старый чек": "Чек ескірген. Бүгінгі күнмен төлем керек.",
                }
            }
            reason = result.get("reason", "")
            msg_map = reason_map.get(lang, reason_map["ru"])
            friendly = next((v for k, v in msg_map.items() if k in reason.lower()), reason)
            kb = [[InlineKeyboardButton("💳 Выбрать тариф" if lang == "ru" else "💳 Тариф таңдау", callback_data="prof_sub")]]
            await update.message.reply_text(
                f"❌ Чек не прошёл проверку.\n\n{friendly}" if lang == "ru"
                else f"❌ Чек тексеруден өтпеді.\n\n{friendly}",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return

        amount = result.get("amount", 0)
        tier = TIER_AMOUNTS.get(int(amount) if amount else 0, context.user_data.get("chosen_tier", "pro"))
        t_name = TIER_NAMES.get(tier, "PRO")

        await self.db.save_receipt_hash(receipt_hash, user_id, tier, amount)
        await self.db.activate_subscription(user_id, tier=tier)

        context.user_data.pop("chosen_tier", None)
        context.user_data.pop("step", None)

        kb = [[MENU_BTN(lang)]]
        await update.message.reply_text(
            f"🎉 *{t_name} активирован!*\n\nТеперь у вас безлимитный доступ к Docura.kz. Спасибо за оплату!" if lang == "ru"
            else f"🎉 *{t_name} белсендірілді!*\n\nEndi Docura.kz-де шексіз қол жеткізу бар. Рахмет!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"
        text    = update.message.text.strip()
        step    = context.user_data.get("step", "")

        cancel_kb = [[CANCEL_BTN(lang)]]

        # ── Редактирование профиля: САДИК ──
        kg_prof_steps = {
            "prof_edit_name":     ("name",     "prof_edit_school",    ("🏫 Введите название детского сада:" if lang == "ru" else "🏫 Балабақшаның атауын енгізіңіз:")),
            "prof_edit_school":   ("school",   "prof_edit_age_group", ("👶 Введите возрастную группу:\n\n_Пример: старшая группа (5-6 лет)_" if lang == "ru" else "👶 Жас тобын енгізіңіз:")),
            "prof_edit_age_group":("age_group","prof_edit_position",  ("💼 Введите вашу должность:" if lang == "ru" else "💼 Лауазымыңызды енгізіңіз:")),
            "prof_edit_position": ("position", "prof_edit_director",  ("👔 Введите ФИО заведующей:" if lang == "ru" else "👔 Меңгерушінің аты-жөнін енгізіңіз:")),
        }

        # ── Редактирование профиля: ШКОЛА ──
        teacher_prof_steps = {
            "prof_edit_name":     ("name",     "prof_edit_school",   ("🏫 Введите название школы:" if lang == "ru" else "🏫 Мектептің атауын енгізіңіз:")),
            "prof_edit_school":   ("school",   "prof_edit_subject",  ("📚 Введите ваш предмет:" if lang == "ru" else "📚 Пәніңізді енгізіңіз:")),
            "prof_edit_subject":  ("subject",  "prof_edit_classes",  ("🏷 Введите ваши классы (например: 7А, 8Б):" if lang == "ru" else "🏷 Сыныптарыңызды енгізіңіз:")),
            "prof_edit_classes":  ("classes",  "prof_edit_position", ("💼 Введите вашу должность:" if lang == "ru" else "💼 Лауазымыңызды енгізіңіз:")),
            "prof_edit_position": ("position", "prof_edit_director", ("👔 Введите ФИО директора:" if lang == "ru" else "👔 Директордың аты-жөнін енгізіңіз:")),
        }

        prof_steps = kg_prof_steps if is_kg else teacher_prof_steps

        if step in prof_steps:
            if len(text) < 2:
                await update.message.reply_text(
                    "❌ Слишком коротко. Попробуйте снова:" if lang == "ru" else "❌ Тым қысқа. Қайталап көріңіз:",
                    reply_markup=InlineKeyboardMarkup(cancel_kb)
                )
                return
            field, next_step, next_q = prof_steps[step]
            await self.db.upsert_user(user_id, {field: text})
            context.user_data["step"] = next_step
            await update.message.reply_text(next_q, reply_markup=InlineKeyboardMarkup(cancel_kb), parse_mode=ParseMode.MARKDOWN)

        elif step == "prof_edit_director":
            await self.db.upsert_user(user_id, {"director": text})
            context.user_data["step"] = None
            kb = [[MENU_BTN(lang)]]
            await update.message.reply_text(
                "✅ *Профиль обновлён!*" if lang == "ru" else "✅ *Профиль жаңартылды!*",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        # ── Добавление ученика/воспитанника ──
        elif step == "student_name":
            if len(text) < 3:
                await update.message.reply_text(
                    "❌ Введите полное имя (минимум 3 символа):" if lang == "ru" else "❌ Толық атын енгізіңіз:",
                    reply_markup=InlineKeyboardMarkup(cancel_kb)
                )
                return
            context.user_data["new_student"]["name"] = text
            context.user_data["step"] = "student_class"
            group_q = (
                ("🏷 Введите группу (например: старшая «Ромашка»):" if is_kg else "🏷 Введите класс (например: 9А):")
                if lang == "ru" else
                ("🏷 Топты енгізіңіз:" if is_kg else "🏷 Сыныпты енгізіңіз (мысалы: 9А):")
            )
            await update.message.reply_text(group_q, reply_markup=InlineKeyboardMarkup(cancel_kb))

        elif step == "student_class":
            context.user_data["new_student"]["class_name"] = text
            if is_kg:
                # Для сада оценки не нужны — сразу к достижениям
                context.user_data["new_student"]["grades"] = {}
                context.user_data["step"] = "student_achievements"
                await update.message.reply_text(
                    ("🏆 Достижения ребёнка через запятую:\n"
                     "_Например: выступление на утреннике, конкурс рисунков_\n"
                     "Или напишите *пропустить*") if lang == "ru" else
                    ("🏆 Баланың жетістіктерін үтірмен енгізіңіз:\n"
                     "Немесе *өткізу* жазыңыз"),
                    reply_markup=InlineKeyboardMarkup(cancel_kb),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                context.user_data["step"] = "student_grades"
                await update.message.reply_text(
                    ("📊 Введите оценки через запятую:\n"
                     "_Формат: Математика-5, Русский-4_\n"
                     "Или напишите *пропустить*") if lang == "ru" else
                    ("📊 Бағаларды үтірмен енгізіңіз:\n"
                     "_Формат: Математика-5, Орыс тілі-4_\n"
                     "Немесе *өткізу* жазыңыз"),
                    reply_markup=InlineKeyboardMarkup(cancel_kb),
                    parse_mode=ParseMode.MARKDOWN
                )

        elif step == "student_grades":
            grades = {}
            if text.lower() not in ["пропустить", "өткізу", "-", "нет"]:
                for part in text.split(","):
                    if "-" in part:
                        subj, grade = part.strip().rsplit("-", 1)
                        try:
                            grades[subj.strip()] = int(grade.strip())
                        except:
                            pass
            context.user_data["new_student"]["grades"] = grades
            context.user_data["step"] = "student_achievements"
            await update.message.reply_text(
                ("🏆 Введите достижения через запятую:\n_Призёр олимпиады, КВН, Волонтёр_\n"
                 "Или напишите *пропустить*") if lang == "ru" else
                ("🏆 Жетістіктерді үтірмен енгізіңіз:\n"
                 "Немесе *өткізу* жазыңыз"),
                reply_markup=InlineKeyboardMarkup(cancel_kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif step == "student_achievements":
            achievements = []
            if text.lower() not in ["пропустить", "өткізу", "-", "нет", "жоқ"]:
                achievements = [a.strip() for a in text.split(",") if a.strip()]
            context.user_data["new_student"]["achievements"] = achievements
            context.user_data["step"] = "student_absences"
            await update.message.reply_text(
                "📅 Количество пропущенных дней (введите число или 0):" if lang == "ru"
                else "📅 Өткізген күндер санын енгізіңіз (0 немесе сан):",
                reply_markup=InlineKeyboardMarkup(cancel_kb)
            )

        elif step == "student_absences":
            try:
                absences = int(text)
            except:
                absences = 0
            context.user_data["new_student"]["absences"] = absences
            context.user_data["step"] = "student_behavior"
            kb = [
                [InlineKeyboardButton("😊 " + ("Отличное" if lang == "ru" else "Өте жақсы"), callback_data="_beh_1"),
                 InlineKeyboardButton("🙂 " + ("Хорошее" if lang == "ru" else "Жақсы"),      callback_data="_beh_2")],
                [InlineKeyboardButton("😐 " + ("Удовл." if lang == "ru" else "Қанағат."),    callback_data="_beh_3")],
                [CANCEL_BTN(lang)],
            ]
            beh_q = (
                ("😊 Выберите поведение ребёнка:" if is_kg else "😊 Выберите поведение ученика:")
                if lang == "ru" else
                ("😊 Баланың мінез-құлқын таңдаңыз:" if is_kg else "😊 Оқушының мінез-құлқын таңдаңыз:")
            )
            await update.message.reply_text(beh_q, reply_markup=InlineKeyboardMarkup(kb))

        elif step == "student_behavior":
            behavior_map = {
                "1": "отличное" if lang == "ru" else "өте жақсы",
                "2": "хорошее"  if lang == "ru" else "жақсы",
                "3": "удовлетворительное" if lang == "ru" else "қанағаттанарлық",
            }
            behavior = behavior_map.get(text, text)
            context.user_data["new_student"]["behavior"] = behavior
            await self._finish_add_student(update, context, user_id, lang, is_kg)

    async def _finish_add_student(self, update, context, user_id, lang, is_kg=False):
        student_data = context.user_data.pop("new_student", {})
        if not student_data.get("behavior"):
            student_data["behavior"] = "хорошее" if lang == "ru" else "жақсы"
        await self.db.add_student(user_id, student_data)
        context.user_data["step"] = None
        name = student_data.get("name", "")
        add_btn_text = t(lang, "btn_add_child") if is_kg else t(lang, "btn_add_student")
        list_btn_text = t(lang, "btn_my_children") if is_kg else t(lang, "btn_my_students")
        kb = [
            [InlineKeyboardButton("➕ " + ("Добавить ещё" if lang == "ru" else "Тағы қосу"), callback_data="prof_add_student")],
            [InlineKeyboardButton("👥 " + list_btn_text,  callback_data="prof_students")],
            [MENU_BTN(lang)],
        ]
        await update.message.reply_text(
            f"✅ *{name}* добавлен в базу!" if lang == "ru" else f"✅ *{name}* базаға қосылды!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
