"""
Docura.kz — AI-агент с памятью
Загружает расписание/режим дня, помнит контекст, предлагает нужные документы
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
    # ЗАГРУЗКА РАСПИСАНИЯ / РЕЖИМА ДНЯ
    # ══════════════════════════════════════════════════════

    async def show_schedule_menu(self, update_or_query, context, lang, is_kg=False):
        """Меню управления расписанием (для сада — режим дня и занятия по группе)"""
        user_id = update_or_query.from_user.id if hasattr(update_or_query, "from_user") else update_or_query.message.chat_id
        if not await self.db.has_seen_section(user_id, "schedule"):
            await self.db.mark_section_seen(user_id, "schedule")
            intro = (
                "📅 *Добро пожаловать в раздел Расписание!*\n\n"
                "Здесь вы можете загрузить своё расписание уроков или режим дня группы.\n\n"
                "После загрузки бот будет:\n"
                "✅ Автоматически подставлять данные в документы\n"
                "✅ Напоминать о нужных документах по расписанию\n"
                "✅ Предлагать создать КСП перед уроком\n\n"
                "Загрузите фото расписания или введите текстом 👇"
            ) if lang == "ru" else (
                "📅 *Кесте бөліміне қош келдіңіз!*\n\n"
                "Мұнда сабақ кестеңізді немесе топтың күн тәртібін жүктей аласыз.\n\n"
                "Жүктегеннен кейін бот:\n"
                "✅ Деректерді автоматты құжаттарға қосады\n"
                "✅ Кесте бойынша құжаттарды еске салады\n\n"
                "Кесте суретін жіберіңіз немесе мәтінмен жазыңыз 👇"
            )
            await update_or_query.message.reply_text(intro, parse_mode=ParseMode.MARKDOWN)
        if is_kg:
            text = (
                "📅 *Режим дня и сетка занятий*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Загрузите расписание занятий вашей группы — ИИ-ассистент будет автоматически "
                "подставлять нужные данные в циклограммы, КСП и карты развития.\n\n"
                "🤖 *Интеллектуальные функции:*\n"
                "• Мониторинг расписания каждые 6 часов\n"
                "• Авто-подготовка планов к занятиям заблаговременно\n\n"
                "Способы загрузки:\n"
                "📸 Фото расписания группы (стенд или журнал)\n"
                "✍️ Текстовый ввод (например: _Понедельник: Развитие речи, Музыка_)"
            ) if lang == "ru" else (
                "📅 *Күн тәртібі мен сабақтар кестесі*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Тобыңыздың сабақ кестесін жүктеңіз — ЖИ-көмекші деректерді циклограммаларға, "
                "ҚМЖ-ға және даму карталарына автоматты түрде қосады.\n\n"
                "🤖 *Зияткерлік мүмкіндіктер:*\n"
                "• Кестені әр 6 сағат сайын бақылау\n"
                "• Сабақ жоспарларын алдын ала автоматты дайындау\n\n"
                "Жүктеу жолдары:\n"
                "📸 Топ кестесінің фотосы (стенд немесе журнал)\n"
                "✍️ Мәтінмен енгізу (мысалы: _Дүйсенбі: Тіл дамыту, Музыка_)"
            )
        else:
            text = (
                "📅 *Расписание учебных занятий*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Загрузите ваше расписание — ИИ-ассистент свяжет его с базой классов и КСП.\n\n"
                "🤖 *Интеллектуальные возможности:*\n"
                "• Каждые 6 часов ИИ проверяет предстоящие уроки\n"
                "• Автоматическая генерация КСП по расписанию (если включен автопилот)\n"
                "• В чате всегда можно спросить: _«Что у меня завтра по расписанию?»_\n\n"
                "Способы добавления:\n"
                "📸 Фото расписания из учительской или журнала\n"
                "✍️ Текстовое описание по дням недели"
            ) if lang == "ru" else (
                "📅 *Сабақ кестесі және мониторинг*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Кестеңізді жүктеңіз — ЖИ-көмекші оны сыныптар және ҚМЖ базасымен байланыстырады.\n\n"
                "🤖 *Зияткерлік мүмкіндіктер:*\n"
                "• Әр 6 сағат сайын ЖИ алдағы сабақтарды бақылайды\n"
                "• Кесте бойынша ҚМЖ-ларды автоматты дайындау (автопилот қосулы болса)\n"
                "• Чаттан кез келген уақытта: _«Ертең қандай сабақтарым бар?»_ деп сұрай аласыз\n\n"
                "Қосу тәсілдері:\n"
                "📸 Кестенің фотосы\n"
                "✍️ Апта күндері бойынша мәтін"
            )

        photo_btn = ("📸 Фото режима дня" if lang == "ru" else "📸 Күн тәртібінің суреті") if is_kg \
            else ("📸 Фото расписания" if lang == "ru" else "📸 Кесте суреті")
        view_btn = ("📋 Мой режим дня" if lang == "ru" else "📋 Менің күн тәртібім") if is_kg \
            else ("📋 Моё расписание" if lang == "ru" else "📋 Менің кестем")

        user = await self.db.get_user(user_id)
        auto_gen = bool(user.get("auto_generate")) if user else False
        auto_gen_btn = ("🤖 Автогенерация: " + ("ВКЛ ✅" if auto_gen else "ВЫКЛ ❌")) if lang == "ru" \
            else ("🤖 Авто-жасау: " + ("ҚОСУЛЫ ✅" if auto_gen else "ӨШІРУЛІ ❌"))

        kb = [
            [InlineKeyboardButton(photo_btn, callback_data="agent_schedule_photo")],
            [InlineKeyboardButton("✍️ Ввести текстом" if lang == "ru" else "✍️ Мәтінмен енгізу", callback_data="agent_schedule_text")],
            [InlineKeyboardButton(view_btn, callback_data="agent_schedule_view")],
            [InlineKeyboardButton(auto_gen_btn, callback_data="agent_autogen_toggle")],
            [MENU_BTN(lang)],
        ]
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else:
            await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query   = update.callback_query
        await query.answer()
        data    = query.data
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"

        if data.startswith("agent_schedule") and (not user or not user.get("subscribed")):
            await query.edit_message_text(
                "Эта функция доступна в PRO. Docura сможет помнить твоё расписание, напоминать о документах и помогать готовить черновики заранее."
                if lang == "ru" else
                "Бұл функция PRO тарифінде қолжетімді. Docura кестеңізді есте сақтап, құжаттар туралы еске салып, құжат жобасын алдын ала дайындауға көмектеседі.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐ Посмотреть PRO" if lang == "ru" else "⭐ PRO көру", callback_data="prof_sub")],
                    [InlineKeyboardButton("Пока не нужно" if lang == "ru" else "Қазір керек емес", callback_data="menu_main")],
                ])
            )
            return

        if data == "agent_schedule":
            await self.show_schedule_menu(query, context, lang, is_kg)

        elif data == "agent_schedule_photo":
            context.user_data["step"] = "agent_waiting_schedule_photo"
            kb = [[MENU_BTN(lang)]]
            prompt_text = (
                ("📸 Пришлите фото режима дня — бот распознает его автоматически" if is_kg
                 else "📸 Пришлите фото расписания — бот распознает его автоматически") if lang == "ru"
                else "📸 Сурет жіберіңіз"
            )
            await query.edit_message_text(prompt_text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "agent_schedule_text":
            context.user_data["step"] = "agent_waiting_schedule_text"
            kb = [[MENU_BTN(lang)]]
            if is_kg:
                prompt_text = (
                    "✍️ Напишите режим дня / занятия группы в любом формате.\n\n"
                    "_Например:_\n"
                    "_Понедельник: 9:00 познание, 10:00 творчество_\n"
                    "_Вторник: 9:00 физкультура..._" if lang == "ru"
                    else "✍️ Топтың сабақтарын кез келген форматта жазыңыз."
                )
            else:
                prompt_text = (
                    "✍️ Напишите расписание в любом формате.\n\n"
                    "_Например:_\n"
                    "_Понедельник: 8:00 7А Математика, 9:00 8Б Математика_\n"
                    "_Вторник: 8:00 9В Алгебра..._" if lang == "ru"
                    else "✍️ Кестені кез келген форматта жазыңыз."
                )
            await query.edit_message_text(prompt_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "agent_schedule_view":
            schedule_json = await self.db.get_schedule(user_id)
            if not schedule_json:
                empty_text = (
                    ("📅 Режим дня не загружен." if is_kg else "📅 Расписание не загружено.") if lang == "ru"
                    else "📅 Кесте жүктелмеген."
                )
                kb = [
                    [InlineKeyboardButton("➕ Загрузить" if lang == "ru" else "➕ Жүктеу", callback_data="agent_schedule")],
                    [MENU_BTN(lang)],
                ]
                await query.edit_message_text(empty_text, reply_markup=InlineKeyboardMarkup(kb))
                return

            schedule = json.loads(schedule_json)
            title = ("📅 *Ваш режим дня:*\n" if is_kg else "📅 *Ваше расписание:*\n") if lang == "ru" \
                else ("📅 *Сіздің күн тәртібіңіз:*\n" if is_kg else "📅 *Сіздің кестеңіз:*\n")
            lines = [title]
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

        elif data == "agent_reminders":
            memory = await self.db.get_agent_context(user_id)
            enabled = bool(memory.get("reminders_enabled", True))
            auto_gen = bool(user.get("auto_generate")) if user else False
            title = "🔔 *Напоминания и автогенерация*\n\n" if lang == "ru" else "🔔 *Еске салғыштар мен авто-жасау*\n\n"
            status = ("включены" if enabled else "выключены") if lang == "ru" else ("қосулы" if enabled else "өшірулі")
            ag_status = ("включена" if auto_gen else "выключена") if lang == "ru" else ("қосулы" if auto_gen else "өшірулі")
            desc = (
                f"Напоминания: *{status}*\n"
                f"Автогенерация по расписанию: *{ag_status}*\n\n"
                f"_При включённой автогенерации ИИ каждые 6 часов проверяет расписание и базу учеников и готовит документы заблаговременно._"
            ) if lang == "ru" else (
                f"Еске салғыштар: *{status}*\n"
                f"Кесте бойынша авто-жасау: *{ag_status}*\n\n"
                f"_Авто-жасау қосулы болса, ЖИ әр 6 сағат сайын кесте мен балалар базасын тексеріп, құжаттарды алдын ала дайындайды._"
            )
            kb = [
                [InlineKeyboardButton("🔕 Выключить напоминания" if enabled and lang == "ru" else "🔔 Включить напоминания" if lang == "ru" else ("🔕 Еске салғышты өшіру" if enabled else "🔔 Еске салғышты қосу"), callback_data="agent_reminders_toggle")],
                [InlineKeyboardButton("🤖 " + ("Выключить автогенерацию" if auto_gen and lang == "ru" else "Включить автогенерацию" if lang == "ru" else ("Авто-жасауды өшіру" if auto_gen else "Авто-жасауды қосу")), callback_data="agent_autogen_toggle")],
                [MENU_BTN(lang)]
            ]
            await query.edit_message_text(title + desc, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        elif data == "agent_reminders_toggle":
            memory = await self.db.get_agent_context(user_id)
            await self.db.update_agent_context(user_id, {"reminders_enabled": not bool(memory.get("reminders_enabled", True))})
            await query.edit_message_text("✅ Настройка напоминаний сохранена." if lang == "ru" else "✅ Еске салғыш параметрі сақталды.", reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]]))

        elif data == "agent_autogen_toggle":
            if not user or not user.get("subscribed"):
                await query.edit_message_text(
                    "Автогенерация доступна только в PRO. Docura будет каждые 6 часов проверять ваше расписание и базу учеников и готовить документы автоматически." if lang == "ru"
                    else "Автоматты жасау тек PRO тарифінде қолжетімді. Docura әр 6 сағат сайын кестеңіз бен оқушылар базасын тексеріп, құжаттарды автоматты дайындайды.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⭐ Посмотреть PRO" if lang == "ru" else "⭐ PRO көру", callback_data="prof_sub")],
                        [MENU_BTN(lang)],
                    ])
                )
                return
            new_val = 0 if user.get("auto_generate") else 1
            await self.db.upsert_user(user_id, {"auto_generate": new_val})
            msg = (
                "✅ *Автогенерация включена!*\n\nИИ-агент каждые 6 часов проверяет ваше расписание и базу учеников и готовит документы заблаговременно." if new_val
                else "❌ *Автогенерация выключена.* Документы будут создаваться только по вашему запросу."
            ) if lang == "ru" else (
                "✅ *Авто-жасау қосылды!*\n\nЖИ агент әр 6 сағат сайын кесте мен оқушылар базасын тексеріп, құжаттарды алдын ала дайындайды." if new_val
                else "❌ *Авто-жасау өшірілді.*"
            )
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]]), parse_mode=ParseMode.MARKDOWN)

        elif data == "agent_reminders_off":
            await self.db.update_agent_context(user_id, {"reminders_enabled": False})
            await query.edit_message_text(
                "🔕 Напоминания отключены. Включить их можно в разделе «Напоминания»." if lang == "ru"
                else "🔕 Еске салғыштар өшірілді. Оларды «Еске салғыштар» бөлімінен қайта қоса аласыз.",
                reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]])
            )

        elif data == "agent_remind_later":
            # Не меняем предпочтение пользователя: лишь переносим следующее уведомление.
            await self.db.update_notified(user_id)
            await query.edit_message_text(
                "Хорошо, напомню позже." if lang == "ru" else "Жақсы, кейінірек еске саламын.",
                reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]])
            )

        elif data == "agent_skip":
            await query.edit_message_text(
                "Хорошо, не буду создавать автоматически." if lang == "ru" else "Жақсы, автоматты түрде жасамаймын.",
                reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]])
            )

        elif data in {"agent_auto_generate_ksp", "agent_auto_generate_cycle"}:
            if not user or not user.get("subscribed") or not user.get("auto_generate"):
                await query.edit_message_text(
                    "Автогенерация доступна только в PRO после её включения." if lang == "ru"
                    else "Автоматты жасау тек PRO тарифінде және ол қосылғанда қолжетімді.",
                    reply_markup=InlineKeyboardMarkup([[MENU_BTN(lang)]])
                )
                return

            from handlers.documents import DocumentHandler
            doc_type = "lesson_plan" if data == "agent_auto_generate_ksp" else "kindergarten_cycle_schedule"
            schedule_json = await self.db.get_schedule(user_id)
            schedule = json.loads(schedule_json) if schedule_json else {}
            monday = schedule.get("Понедельник", []) if isinstance(schedule, dict) else []
            first_lesson = next((item for item in monday if isinstance(item, dict)), {})

            # Проверяем базу учеников / воспитанников перед генерацией
            students = await self.db.get_students(user_id)

            # _generate использует профиль и полный контекст расписания и базы учеников.
            answers = {}
            if doc_type == "lesson_plan":
                subject = first_lesson.get("subject") or user.get("subject", "")
                class_name = first_lesson.get("class") or user.get("classes", "")
                if subject or class_name:
                    answers["subject_class"] = " ".join(part for part in [subject, class_name] if part)
                if class_name and students:
                    class_students = [s["name"] for s in students if s.get("class_name") == class_name]
                    if class_students:
                        answers["students_list"] = ", ".join(class_students[:25])
            else:
                answers = {
                    "organization": user.get("school", ""),
                    "group": user.get("age_group", ""),
                    "period": "следующая неделя" if lang == "ru" else "келесі апта",
                    "week_topic": "[уточнить]",
                    "events": "нет" if lang == "ru" else "жоқ",
                }
                if students:
                    pupil_names = [s["name"] for s in students]
                    answers["children_list"] = ", ".join(pupil_names[:25])

            context.user_data.update({
                "doc_type": doc_type,
                "doc_lang": user.get("document_lang") or lang,
                "doc_answers": answers,
                "q_index": 0,
                "step": "waiting_answer",
            })
            await query.edit_message_text("✅ Начинаю генерацию..." if lang == "ru" else "✅ Жасауды бастаймын...")
            await DocumentHandler(self.db, self.api_key)._generate(query.message, context, lang)

        elif data == "agent_suggest_doc":
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
        """Обрабатывает фото расписания / режима дня"""
        if context.user_data.get("step") != "agent_waiting_schedule_photo":
            return False  # не наш случай

        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"

        wait_msg = await update.message.reply_text(
            "🔍 Распознаю..." if lang == "ru" else "🔍 Танылуда..."
        )

        try:
            file_obj = await update.message.photo[-1].get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                await file_obj.download_to_drive(tmp.name)
                tmp_path = tmp.name

            with open(tmp_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")
            os.unlink(tmp_path)

            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            if is_kg:
                prompt = """Это фото режима дня / расписания занятий группы детского сада. Распознай и верни ТОЛЬКО JSON без markdown:
{
  "Понедельник": [{"time": "9:00", "class": "старшая группа", "subject": "Познание"}, ...],
  "Вторник": [...],
  ...
}
Если не можешь распознать — верни {"error": "не удалось распознать"}"""
            else:
                prompt = """Это фото расписания уроков учителя. Распознай расписание и верни ТОЛЬКО JSON без markdown:
{
  "Понедельник": [{"time": "8:00", "class": "7А", "subject": "Математика"}, ...],
  "Вторник": [...],
  ...
}
Если не можешь распознать — верни {"error": "не удалось распознать"}"""

            response = await client.messages.create(
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
            done_word = "Режим дня загружен!" if is_kg else "Расписание загружено!"
            done_word_kz = "Күн тәртібі жүктелді!" if is_kg else "Кесте жүктелді!"
            await update.message.reply_text(
                f"✅ *{done_word}*\n\n"
                f"📅 Дней: {days_count}\n"
                f"📚 Занятий: {lessons_count}\n\n"
                f"Теперь бот будет автоматически использовать эти данные при генерации." if lang == "ru" else
                f"✅ *{done_word_kz}*\n\n"
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
                "❌ Не удалось распознать с фото.\n"
                "Попробуйте ввести текстом или сделайте более чёткое фото." if lang == "ru"
                else "❌ Суреттен тану мүмкін болмады.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return True

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает текстовое расписание / режим дня"""
        if context.user_data.get("step") != "agent_waiting_schedule_text":
            return False

        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"
        text    = update.message.text.strip()

        wait_msg = await update.message.reply_text(
            "⏳ Сохраняю..." if lang == "ru" else "⏳ Сақталуда..."
        )

        try:
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            entity = "режим дня / занятия группы детского сада" if is_kg else "расписание"
            prompt = f"""Пользователь прислал своё {entity} в свободном формате. Преобразуй в JSON:
{{
  "Понедельник": [{{"time": "8:00", "class": "{'старшая группа' if is_kg else '7А'}", "subject": "{'Познание' if is_kg else 'Математика'}"}}, ...],
  "Вторник": [...],
  ...
}}
Верни ТОЛЬКО JSON без markdown.

ДАННЫЕ:
{text}"""

            response = await client.messages.create(
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
                "✅ *Сохранено!*\n\nТеперь бот использует это автоматически." if lang == "ru"
                else "✅ *Сақталды!*",
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
        """Собирает сохранённые данные пользователя для точной генерации."""
        user = await self.db.get_user(user_id)
        schedule_json = await self.db.get_schedule(user_id)
        try:
            schedule = json.loads(schedule_json) if schedule_json else {}
            today = datetime.now().strftime("%A")

            day_map = {
                "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
                "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота",
            }
            today_ru = day_map.get(today, "")
            today_lessons = schedule.get(today_ru, []) if isinstance(schedule, dict) else []
            lines = ["КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ ДЛЯ ТОЧНОЙ ГЕНЕРАЦИИ:"]

            if user:
                is_kg = user.get("role") == "kindergarten"
                institution = "детский сад" if is_kg else "школа"
                lines.append(f"Учреждение: {institution}" + (f" ({user.get('school')})" if user.get("school") else ""))
                if is_kg:
                    values = [user.get("age_group"), user.get("position")]
                    if any(values):
                        lines.append("Группа и возраст: " + ", ".join(value for value in values if value))
                else:
                    values = [user.get("subject"), user.get("classes")]
                    if any(values):
                        lines.append("Предмет и классы: " + ", ".join(value for value in values if value))

            documents = await self.db.get_history(user_id, limit=5)
            if documents:
                recent = []
                for document in documents:
                    label = document.get("doc_name") or document.get("doc_type", "документ")
                    date = (document.get("created_at") or "")[:10]
                    recent.append(f"{label} ({date})" if date else label)
                lines.append("Последние документы: " + "; ".join(recent))

            students = await self.db.get_students(user_id)
            if students:
                by_class = {}
                for student in students:
                    by_class.setdefault(student.get("class_name") or "без группы", []).append(student.get("name", ""))
                student_parts = [f"{group}: {', '.join(names[:20])}" for group, names in by_class.items()]
                lines.append("База учеников/детей: " + "; ".join(student_parts))

            if schedule:
                lines.append("РАСПИСАНИЕ / РЕЖИМ ДНЯ:")
            for day, lessons in schedule.items():
                if isinstance(lessons, list):
                    lessons_str = ", ".join(
                        f"{l.get('time', '')} {l.get('class', '')} {l.get('subject', '')}"
                        for l in lessons if isinstance(l, dict)
                    )
                    lines.append(f"- {day}: {lessons_str}")

            if today_lessons:
                lines.append(f"\nСегодня ({today_ru}):")
                for l in today_lessons:
                    if isinstance(l, dict):
                        lines.append(f"  • {l.get('time', '')} — {l.get('class', '')} {l.get('subject', '')}")

            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""
