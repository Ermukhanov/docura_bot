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
from handlers.rag_base import VOICE_UNDERSTAND_PROMPT
from database import Database

MENU_BTN = lambda lang: InlineKeyboardButton(
    "🏠 " + ("Главное меню" if lang == "ru" else "Басты мәзір"),
    callback_data="menu_main"
)

class VoiceHandler:
    def __init__(self, db: Database, anthropic_key: str, openai_key: str = ""):
        self.db            = db
        self.anthropic_key = anthropic_key

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user    = await self.db.get_user(user_id)
        lang    = user.get("lang", "ru") if user else "ru"

        if not user or not user.get("name"):
            await update.message.reply_text(
                "Сначала зарегистрируйтесь: /start" if lang == "ru"
                else "Алдымен тіркеліңіз: /start"
            )
            return

        proc_msg = await update.message.reply_text(
            "🎤 " + ("Слушаю..." if lang == "ru" else "Тыңдауда...")
        )

        try:
            # Скачать голосовое
            voice_file = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await voice_file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            # Транскрибация — пробуем разные методы
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

            # Показываем что распознали
            await update.message.reply_text(
                f"🎤 *{'Распознано' if lang == 'ru' else 'Танылды'}:*\n_{voice_text}_",
                parse_mode=ParseMode.MARKDOWN
            )

            # Понять запрос через Claude Haiku (дёшево)
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            prompt = VOICE_UNDERSTAND_PROMPT.format(
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
                # Если JSON не распарсился — предлагаем выбрать документ вручную
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
                await update.message.reply_text(
                    ("Не смог определить тип документа.\n\n"
                     "Попробуйте сказать точнее:\n"
                     "- *'Сделай КСП по математике для 7 класса'*\n"
                     "- *'Характеристика на ученика Асылбека'*\n"
                     "- *'Заявление на отпуск'*") if lang == "ru" else
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

        # Метод 1: faster-whisper
        try:
            from faster_whisper import WhisperModel
            print("Используем faster-whisper...")
            # Маленькая модель 'tiny' — быстро, работает на CPU
            # При первом запуске скачает ~75МБ
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                file_path,
                language="ru",
                beam_size=3,
                vad_filter=True,  # фильтр тишины
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            if text:
                print(f"faster-whisper: '{text}'")
                return text
        except ImportError:
            print("faster-whisper не установлен")
        except Exception as e:
            print(f"faster-whisper error: {e}")

        # Метод 2: SpeechRecognition через Google (бесплатно, нужен интернет)
        try:
            import speech_recognition as sr
            import subprocess

            # Конвертируем ogg в wav
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
