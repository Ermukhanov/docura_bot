"""
Docura.kz — AI-агент с памятью
Загружает расписание, помнит контекст, предлагает нужные документы
"""
import json
import base64
import tempfile
import os
from datetime import datetime
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database import Database


MENU_BTN = lambda lang: InlineKeyboardButton(
    "🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"),
    callback_data="menu_main"
)


class AgentHandler:
    def __init__(self, db: Database, api_key: str):
        self.db      = db
        self.api_key = api_key

    # ══════════════════════════════════════════════════════
    # ЗАГРУЗКА РАСПИСАНИЯ
    # ══════════════════════════════════════════════════════

    async def show_schedule_menu(self, update_or_query, context, lang):
        """Меню управления расписанием"""
        text = (
            "📅 *Расписание*\n\n"
            "Загрузи своё расписание — бот будет автоматически подставлять "
            "нужные данные в документы.\n\n"
            "Можно прислать:\n"
            "📸 Фото расписания из журнала\n"
            "📄 Файл Excel/CSV\n"
            "✍️ Или ввести текстом"
        ) if lang == "ru" else (
            "📅 *Кесте*\n\n"
            "Кестеңізді жүктеңіз — бот деректерді автоматты түрде құжаттарға қосады."
        )
        kb = [
            [InlineKeyboardButton("📸 Фото расписания" if lang == "ru" else "📸 Кесте суреті", callback_data="agent_schedule_photo")],
            [InlineKeyboardButton("✍️ Ввести текстом" if lang == "ru" else "✍️ Мәтінмен енгізу", callback_data="agent_schedule_text")],
            [InlineKeyboardButton("📋 Моё расписание" if lang == "ru" else "📋 Менің кестем", callback_data="agent_schedule_view")],
            [MENU_BTN(lang)],
        ]
        text_obj = text
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(text_obj, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            await update_or_query.message.reply_text(text_obj, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query   = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"

        if data == "agent_schedule":
            await self.show_schedule_menu(query, context, lang)

        elif data == "agent_schedule_photo":
            context.user_data["step"] = "agent_waiting_schedule_photo"
            kb = [[MENU_BTN(lang)]]
            await query.edit_message_text(
                "📸 Пришлите фото расписания — бот распознает его автоматически" if lang == "ru"
                else "📸 Кесте суретін жіберіңіз",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data == "agent_schedule_text":
            context.user_data["step"] = "agent_waiting_schedule_text"
            kb = [[MENU_BTN(lang)]]
            await query.edit_message_text(
                "✍️ Напишите расписание в любом формате.\n\n"
                "_Например:_\n"
                "_Понедельник: 8:00 7А Математика, 9:00 8Б Математика_\n"
                "_Вторник: 8:00 9В Алгебра..._" if lang == "ru"
                else "✍️ Кестені кез келген форматта жазыңыз.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "agent_schedule_view":
            schedule_json = await self.db.get_schedule(user_id)
            if not schedule_json:
                kb = [
                    [InlineKeyboardButton("➕ Загрузить" if lang == "ru" else "➕ Жүктеу", callback_data="agent_schedule")],
                    [MENU_BTN(lang)],
                ]
                await query.edit_message_text(
                    "📅 Расписание не загружено." if lang == "ru" else "📅 Кесте жүктелмеген.",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                return

            schedule = json.loads(schedule_json)
            lines = ["📅 *Ваше расписание:*\n" if lang == "ru" else "📅 *Сіздің кестеңіз:*\n"]
            for day, lessons in schedule.items():
                lines.append(f"*{day}:*")
                if isinstance(lessons, list):
                    for l in lessons:
                        if isinstance(l, dict):
                            lines.append(f"  {l.get('time', '')} — {l.get('class', '')} {l.get('subject', '')}")
                        else:
                            lines.append(f"  {l}")
                else:
                    lines.append(f"  {lessons}")

            kb = [
                [InlineKeyboardButton("🔄 Обновить" if lang == "ru" else "🔄 Жаңарту", callback_data="agent_schedule")],
                [MENU_BTN(lang)],
            ]
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3900] + "\n_...обрезано_"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "agent_suggest_doc":
            # Пользователь принял предложение сгенерировать документ
            doc_type = context.user_data.get("suggested_doc_type")
            if doc_type:
                from handlers.documents import DocumentHandler
                docs = DocumentHandler(self.db, self.api_key)
                context.user_data["doc_type"] = doc_type
                context.user_data["doc_answers"] = context.user_data.get("suggested_doc_data", {})
                context.user_data["q_index"] = 0
                context.user_data["step"] = "waiting_answer"
                await query.edit_message_text(
                    "✅ Начинаю генерацию..." if lang == "ru" else "✅ Жасауды бастаймын..."
                )
                await docs._generate(query.message, context, lang)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает фото расписания"""
        if context.user_data.get("step") != "agent_waiting_schedule_photo":
            return False  # не наш случай

        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"

        wait_msg = await update.message.reply_text(
            "🔍 Распознаю расписание..." if lang == "ru" else "🔍 Кесте танылуда..."
        )

        try:
            file_obj = await update.message.photo[-1].get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                await file_obj.download_to_drive(tmp.name)
                tmp_path = tmp.name

            with open(tmp_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")
            os.unlink(tmp_path)

            client = anthropic.Anthropic(api_key=self.api_key)
            prompt = """Это фото расписания уроков учителя. Распознай расписание и верни ТОЛЬКО JSON без markdown:
{
  "Понедельник": [{"time": "8:00", "class": "7А", "subject": "Математика"}, ...],
  "Вторник": [...],
  ...
}
Если не можешь распознать — верни {"error": "не удалось распознать"}"""

            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )

            import re
            raw = response.content[0].text.strip()
            raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
            schedule = json.loads(raw)

            if "error" in schedule:
                raise ValueError(schedule["error"])

            await self.db.save_schedule(user_id, json.dumps(schedule, ensure_ascii=False))
            await self.db.update_agent_context(user_id, {"schedule_loaded": True, "schedule_days": list(schedule.keys())})

            await wait_msg.delete()
            context.user_data["step"] = None

            days_count = len(schedule)
            lessons_count = sum(len(v) if isinstance(v, list) else 1 for v in schedule.values())

            kb = [
                [InlineKeyboardButton("📋 Посмотреть" if lang == "ru" else "📋 Қарау", callback_data="agent_schedule_view")],
                [MENU_BTN(lang)],
            ]
            await update.message.reply_text(
                f"✅ *Расписание загружено!*\n\n"
                f"📅 Дней: {days_count}\n"
                f"📚 Уроков: {lessons_count}\n\n"
                f"Теперь бот будет автоматически использовать твоё расписание при генерации КСП и отчётов." if lang == "ru" else
                f"✅ *Кесте жүктелді!*\n\n"
                f"📅 Күн: {days_count}\n"
                f"📚 Сабақ: {lessons_count}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            return True

        except Exception as e:
            print(f"Schedule parse error: {e}")
            await wait_msg.delete()
            context.user_data["step"] = None
            kb = [
                [InlineKeyboardButton("✍️ Ввести текстом" if lang == "ru" else "✍️ Мәтінмен", callback_data="agent_schedule_text")],
                [MENU_BTN(lang)],
            ]
            await update.message.reply_text(
                "❌ Не удалось распознать расписание с фото.\n"
                "Попробуйте ввести текстом или сделайте более чёткое фото." if lang == "ru"
                else "❌ Суреттен кестені тану мүмкін болмады.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return True

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает текстовое расписание"""
        if context.user_data.get("step") != "agent_waiting_schedule_text":
            return False

        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        text    = update.message.text.strip()

        wait_msg = await update.message.reply_text(
            "⏳ Сохраняю расписание..." if lang == "ru" else "⏳ Кесте сақталуда..."
        )

        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            prompt = f"""Пользователь прислал своё расписание в свободном формате. Преобразуй в JSON:
{{
  "Понедельник": [{{"time": "8:00", "class": "7А", "subject": "Математика"}}, ...],
  "Вторник": [...],
  ...
}}
Верни ТОЛЬКО JSON без markdown.

РАСПИСАНИЕ:
{text}"""

            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )

            import re
            raw = response.content[0].text.strip()
            raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
            schedule = json.loads(raw)

            await self.db.save_schedule(user_id, json.dumps(schedule, ensure_ascii=False))
            await self.db.update_agent_context(user_id, {"schedule_loaded": True})

            await wait_msg.delete()
            context.user_data["step"] = None

            kb = [[InlineKeyboardButton("📋 Посмотреть" if lang == "ru" else "📋 Қарау", callback_data="agent_schedule_view")], [MENU_BTN(lang)]]
            await update.message.reply_text(
                "✅ *Расписание сохранено!*\n\nТеперь бот использует его автоматически." if lang == "ru"
                else "✅ *Кесте сақталды!*",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            return True

        except Exception as e:
            print(f"Schedule text parse error: {e}")
            await wait_msg.delete()
            context.user_data["step"] = None
            await update.message.reply_text(
                "❌ Не удалось обработать. Попробуйте другой формат." if lang == "ru"
                else "❌ Өңдеу мүмкін болмады."
            )
            return True

    # ══════════════════════════════════════════════════════
    # УМНЫЕ ПОДСКАЗКИ НА ОСНОВЕ ПАМЯТИ
    # ══════════════════════════════════════════════════════

    async def get_context_for_generation(self, user_id: int) -> str:
        """Возвращает контекст агента для подстановки в промпт при генерации"""
        schedule_json = await self.db.get_schedule(user_id)
        if not schedule_json:
            return ""

        try:
            schedule = json.loads(schedule_json)
            today = datetime.now().strftime("%A")

            # Находим уроки на сегодня
            day_map = {
                "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
                "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота",
            }
            today_ru = day_map.get(today, "")
            today_lessons = schedule.get(today_ru, [])

            lines = ["\nРАСПИСАНИЕ УЧИТЕЛЯ:"]
            for day, lessons in schedule.items():
                if isinstance(lessons, list):
                    lessons_str = ", ".join(
                        f"{l.get('time', '')} {l.get('class', '')} {l.get('subject', '')}"
                        for l in lessons if isinstance(l, dict)
                    )
                    lines.append(f"- {day}: {lessons_str}")

            if today_lessons:
                lines.append(f"\nСегодня ({today_ru}) уроки:")
                for l in today_lessons:
                    if isinstance(l, dict):
                        lines.append(f"  • {l.get('time', '')} — {l.get('class', '')} {l.get('subject', '')}")

            return "\n".join(lines)
        except:
            return ""

    async def suggest_after_generation(self, user_id: int, doc_type: str, lang: str) -> dict | None:
        """После генерации документа предлагает следующий нужный документ"""
        # Логика подсказок на основе того что уже сгенерировано
        suggestions = {
            "lesson_plan": ("monthly_report", "Сгенерировать отчёт учителя за месяц?" if lang == "ru" else "Айлық есеп жасайын ба?"),
            "characteristic": ("parent_letter", "Написать письмо родителям этого ученика?" if lang == "ru" else "Ата-анаға хат жазайын ба?"),
            "discipline_act": ("parent_letter", "Написать письмо родителям о нарушении?" if lang == "ru" else "Ата-анаға хат жазайын ба?"),
        }
        if doc_type in suggestions:
            next_type, question = suggestions[doc_type]
            return {"doc_type": next_type, "question": question}
        return None
