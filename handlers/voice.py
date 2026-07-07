import os
import json
import base64
import tempfile
import asyncio
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from handlers.texts import t
from database import Database

MENU_BTN = lambda lang: InlineKeyboardButton(
    "🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"),
    callback_data="menu_main"
)

VOICE_UNDERSTAND_PROMPT_TEACHER = """Учитель отправил голосовое сообщение:
"{voice_text}"

Данные учителя:
- Предмет: {subject}
- Классы: {classes}
- Классный руководитель: {is_ct}

Определи:
1. Какой документ нужно создать (один из: lesson_plan, calendar_plan, lesson_summary, monthly_report, control_analysis, characteristic, absence_cert, discipline_act, gratitude_letter, parent_letter, vacation_request, explanation, announcement)
2. Какие данные уже есть в запросе
3. Каких данных не хватает (задай только НЕОБХОДИМЫЕ вопросы, максимум 3)

Ответь в формате JSON:
{{
  "doc_type": "тип документа",
  "known_data": {{"ключ": "значение"}},
  "missing_questions": ["вопрос 1", "вопрос 2"]
}}"""

VOICE_UNDERSTAND_PROMPT_KG = """Воспитатель детского сада отправил голосовое сообщение:
"{voice_text}"

Данные воспитателя:
- Возрастная группа: {classes}

Определи:
1. Какой документ нужно создать (один из: kg_thematic_plan, kg_activity_summary, kg_monthly_report, kg_child_characteristic, kg_parent_letter, kg_absence_cert, kg_vacation_request, kg_explanation, kg_announcement)
2. Какие данные уже есть в запросе
3. Каких данных не хватает (задай только НЕОБХОДИМЫЕ вопросы, максимум 3)

Ответь в формате JSON:
{{
  "doc_type": "тип документа",
  "known_data": {{"ключ": "значение"}},
  "missing_questions": ["вопрос 1", "вопрос 2"]
}}"""

class VoiceHandler:
    def __init__(self, db: Database, anthropic_key: str, openai_key: str = ""):
        self.db            = db
        self.anthropic_key = anthropic_key

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"
        is_kg   = (user or {}).get("role") == "kindergarten"

        if not user or not user.get("name"):
            await update.message.reply_text(
                "Сначала зарегистрируйтесь: /start" if lang == "ru"
                else "Алдымен тіркеліңіз: /start"
            )
            return

        # Голосовой ввод доступен только на тарифе PRO
        if user.get("tier") != "pro":
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = [[InlineKeyboardButton(
                "⭐ Перейти на PRO" if lang == "ru" else "⭐ PRO-ға өту",
                callback_data="prof_sub"
            )]]
            text = (
                "🎤 Голосовой ввод доступен только на тарифе *PRO*.\n\n"
                "Оформите PRO для голосового создания документов, или напишите текстом через /new."
            ) if lang == "ru" else (
                "🎤 Дауыстық енгізу тек *PRO* тарифінде қол жетімді.\n\n"
                "PRO рәсімдеңіз немесе /new арқылы мәтінмен жазыңыз."
            )
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        proc_msg = await update.message.reply_text(
            "🎤 " + ("Слушаю..." if lang == "ru" else "Тыңдауда...")
        )

        try:
            voice_file = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await voice_file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            voice_text = await asyncio.get_event_loop().run_in_executor(
                None, self._transcribe_best, tmp_path
            )

            try:
                os.unlink(tmp_path)
            except:
                pass

            try:
                await proc_msg.delete()
            except:
                pass

            if not voice_text or len(voice_text.strip()) < 3:
                kb = [[MENU_BTN(lang)]]
                await update.message.reply_text(
                    ("🎤 Не смог распознать речь.\n\n"
                     "Попробуйте:\n"
                     "- Говорить чётче и громче\n"
                     "- Написать текстом\n"
                     "- Использовать /new") if lang == "ru" else
                    ("🎤 Сөзді тани алмадым.\n\n"
                     "Мәтінмен жазыңыз немесе /new"),
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                return

            await update.message.reply_text(
                f"🎤 *{'Распознано' if lang == 'ru' else 'Танылды'}:*\n_{voice_text}_",
                parse_mode=ParseMode.MARKDOWN
            )

            client = anthropic.Anthropic(api_key=self.anthropic_key)

            if is_kg:
                prompt = VOICE_UNDERSTAND_PROMPT_KG.format(
                    voice_text=voice_text,
                    classes=user.get("age_group", ""),
                )
            else:
                prompt = VOICE_UNDERSTAND_PROMPT_TEACHER.format(
                    voice_text=voice_text,
                    subject=user.get("subject", ""),
                    classes=user.get("classes", ""),
                    is_ct="да" if user.get("is_class_teacher") else "нет"
                )

            msg = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )

            raw = msg.content[0].text.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                kb = [[MENU_BTN(lang)]]
                await update.message.reply_text(
                    ("Не смог определить тип документа.\n"
                     "Выберите документ через меню 👇") if lang == "ru" else
                    ("Құжат түрін анықтай алмадым.\n"
                     "Мәзірден таңдаңыз 👇"),
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                return

            doc_type          = parsed.get("doc_type", "")
            known_data        = parsed.get("known_data", {})
            missing_questions = parsed.get("missing_questions", [])

            if not doc_type:
                kb = [[MENU_BTN(lang)]]
                example_text = (
                    ("- *«Тематический план на ноябрь для старшей группы»*\n"
                     "- *«Характеристика на воспитанника Ерасыла»*\n"
                     "- *«Заявление на отпуск»*") if is_kg else
                    ("- *'Сделай КСП по математике для 7 класса'*\n"
                     "- *'Характеристика на ученика Асылбека'*\n"
                     "- *'Заявление на отпуск'*")
                )
                await update.message.reply_text(
                    (f"Не смог определить тип документа.\n\n"
                     f"Попробуйте сказать точнее:\n{example_text}") if lang == "ru" else
                    ("Құжат түрін анықтай алмадым.\n"
                     "Нақтырақ айтып көріңіз."),
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            from handlers.documents import DOC_QUESTIONS, DOC_NAMES
            qs = DOC_QUESTIONS.get(lang, DOC_QUESTIONS["ru"]).get(doc_type, [])
            doc_name = DOC_NAMES.get(lang, DOC_NAMES["ru"]).get(doc_type, doc_type)

            context.user_data["doc_type"]    = doc_type
            context.user_data["doc_answers"] = known_data
            context.user_data["q_index"]     = 0
            context.user_data["step"]        = "waiting_answer"
            context.user_data["questions"]   = qs

            if missing_questions:
                mini_qs = [{"key": f"extra_{i}", "q": q}
                           for i, q in enumerate(missing_questions)]
                context.user_data["questions"] = mini_qs + list(qs)

            await update.message.reply_text(
                f"✅ *{doc_name}* — {'начинаем!' if lang == 'ru' else 'бастаймыз!'}",
                parse_mode=ParseMode.MARKDOWN
            )

            from handlers.documents import DocumentHandler
            await DocumentHandler(self.db, self.anthropic_key)._ask_question(
                update.message, context, lang, 0
            )

        except Exception as e:
            print(f"Voice error: {e}")
            import traceback
            traceback.print_exc()
            kb = [[MENU_BTN(lang)]]
            await update.message.reply_text(
                ("❌ Ошибка при обработке голосового.\n"
                 "Попробуйте написать текстом.") if lang == "ru" else
                ("❌ Қате. Мәтінмен жазып көріңіз."),
                reply_markup=InlineKeyboardMarkup(kb)
            )

    def _transcribe_best(self, file_path: str) -> str:
        """
        Пробует несколько методов транскрипции по порядку.
        1. faster-whisper (если установлен)
        2. SpeechRecognition (если установлен)
        3. Возвращает пустую строку
        """
        try:
            from faster_whisper import WhisperModel
            print("Используем faster-whisper...")
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                file_path,
                language="ru",
                beam_size=3,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            if text:
                print(f"faster-whisper: '{text}'")
                return text
        except ImportError:
            print("faster-whisper не установлен")
        except Exception as e:
            print(f"faster-whisper error: {e}")

        try:
            import speech_recognition as sr
            import subprocess

            wav_path = file_path.replace(".ogg", ".wav")
            result = subprocess.run(
                ["ffmpeg", "-i", file_path, "-ar", "16000", "-ac", "1",
                 wav_path, "-y", "-loglevel", "quiet"],
                capture_output=True
            )
            if result.returncode == 0 and os.path.exists(wav_path):
                recognizer = sr.Recognizer()
                with sr.AudioFile(wav_path) as source:
                    audio = recognizer.record(source)
                try:
                    text = recognizer.recognize_google(audio, language="ru-RU")
                    os.unlink(wav_path)
                    print(f"SpeechRecognition: '{text}'")
                    return text
                except sr.UnknownValueError:
                    pass
                finally:
                    try:
                        os.unlink(wav_path)
                    except:
                        pass
        except ImportError:
            print("SpeechRecognition не установлен")
        except Exception as e:
            print(f"SpeechRecognition error: {e}")

        return ""
