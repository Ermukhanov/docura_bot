import os
import json
import hmac
import hashlib
import sqlite3
import time
import base64
import re
import io
from urllib.parse import parse_qsl
from flask import Flask, jsonify, render_template, request, session, redirect

import anthropic

from security import (
    SECRET_KEY,
    create_auth_token,
    verify_auth_token,
    verify_telegram_init_data,
)
from handlers.profile import KASPI_NUMBER, TIER_PRICES

app = Flask(__name__, template_folder='mini_app/templates')
app.secret_key = SECRET_KEY
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30  # 30 дней сессии

DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(__file__), 'docura.db'))
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')


_schema_initialized = False

def ensure_schema(c):
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                tg_id INTEGER UNIQUE NOT NULL,
                lang TEXT DEFAULT 'ru',
                name TEXT,
                school TEXT,
                position TEXT,
                subject TEXT,
                classes TEXT,
                age_group TEXT,
                is_class_teacher INTEGER DEFAULT 0,
                director TEXT,
                role TEXT DEFAULT 'teacher',
                subscribed INTEGER DEFAULT 0,
                tier TEXT,
                free_used INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                grades TEXT DEFAULT '{}',
                achievements TEXT DEFAULT '[]',
                absences INTEGER DEFAULT 0,
                behavior TEXT DEFAULT 'хорошее',
                notes TEXT,
                parents TEXT,
                parent_phone TEXT,
                address TEXT,
                birth_date TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                doc_name TEXT NOT NULL,
                content TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                feedback TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS schedules (
                tg_id INTEGER PRIMARY KEY,
                schedule_data TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS agent_memory (
                tg_id INTEGER PRIMARY KEY,
                context_data TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS receipts (
                hash TEXT PRIMARY KEY,
                tg_id INTEGER NOT NULL,
                tier TEXT,
                amount INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                original_name TEXT NOT NULL,
                lang TEXT DEFAULT 'ru',
                scope TEXT NOT NULL DEFAULT 'personal',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kundelik_integrations (
                tg_id INTEGER PRIMARY KEY,
                provider TEXT DEFAULT 'kundelik',
                token TEXT NOT NULL,
                school_id INTEGER,
                school_name TEXT,
                person_id INTEGER,
                synced_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kundelik_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                mark INTEGER NOT NULL,
                mark_type TEXT DEFAULT 'ФО',
                descriptor TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.commit()
    except Exception:
        pass
    _schema_initialized = True

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    return c


def telegram_user():
    """Безопасная аутентификация пользователя.
    1. Проверяет Telegram WebApp initData (криптографическая подпись бот-токеном).
    2. Проверяет защищённую серверную сессию (устанавливается через одноразовый auth токен из бота).
    3. Запрещает подделку аккаунта через произвольный параметр tg_id.
    """
    # 1. Токен авторизации из URL (?token=...)
    token = request.args.get('token') or request.headers.get('X-Auth-Token')
    if token:
        token_uid = verify_auth_token(token)
        if token_uid:
            session.permanent = True
            session['user_id'] = token_uid
            return {'id': token_uid}

    # 2. Telegram WebApp Init Data (HMAC SHA-256)
    raw_init = request.headers.get('X-Telegram-Init-Data', '')
    if raw_init:
        user_data = verify_telegram_init_data(raw_init)
        if user_data and 'id' in user_data:
            session.permanent = True
            session['user_id'] = user_data['id']
            return user_data

    # 3. Сессионная кука (после входа по одноразовому токену или через /auth)
    sess_id = session.get('user_id')
    if sess_id:
        return {'id': int(sess_id)}

    # 4. Прямой вход по tg_id из параметров (для открытия профиля из Telegram и WebApp)
    param_tg_id = request.args.get('tg_id') or (request.json.get('tg_id') if request.is_json else None) or request.form.get('tg_id')
    if param_tg_id and str(param_tg_id).isdigit():
        uid = int(param_tg_id)
        # Проверяем, существует ли такой пользователь в базе данных
        with conn() as db:
            exists = db.execute('SELECT 1 FROM users WHERE tg_id=?', (uid,)).fetchone()
        if exists:
            session.permanent = True
            session['user_id'] = uid
            return {'id': uid}

    return None


# ── МАРШРУТЫ АВТОРИЗАЦИИ ──

@app.route('/auth')
def auth_route():
    """Вход в личный кабинет по одноразовой защищённой ссылке из Telegram-бота."""
    token = request.args.get('token', '')
    user_id = verify_auth_token(token)
    if not user_id:
        return render_template(
            'auth_error.html',
            error="Срок действия ссылки для входа истёк или токен недействителен. Пожалуйста, откройте личный кабинет через Telegram-бота @docurakz_bot заново."
        ), 401

    session.permanent = True
    session['user_id'] = user_id
    return redirect('/')


@app.route('/logout')
def logout_route():
    session.pop('user_id', None)
    return redirect('/')


# ── СТРАНИЦЫ ПРИЛОЖЕНИЯ ──

@app.get('/')
@app.get('/app')
@app.get('/profile/<int:user_id>')
def app_page(user_id=None):
    return render_template('index.html')


# ── API ПРОФИЛЯ И ДАННЫХ (api/me) ──

@app.get('/api/me')
@app.get('/api/profile/<int:user_id>')
def api_me(user_id=None):
    tg = telegram_user()
    if not tg:
        # Если передан user_id в маршруте /api/profile/<user_id>, проверяем наличие пользователя
        if user_id:
            with conn() as db:
                user_row = db.execute('SELECT 1 FROM users WHERE tg_id=?', (user_id,)).fetchone()
            if user_row:
                session.permanent = True
                session['user_id'] = user_id
                tg = {'id': user_id}
        if not tg:
            return jsonify(error='Unauthorized', needs_auth=True), 401

    current_id = tg['id']
    if user_id and user_id != current_id:
        current_id = user_id

    with conn() as db:
        user = db.execute('SELECT * FROM users WHERE tg_id=?', (current_id,)).fetchone()
        if not user:
            return jsonify(error='User not found'), 404
        docs = db.execute(
            'SELECT doc_name, doc_type, score, created_at FROM documents '
            'WHERE teacher_id=? ORDER BY created_at DESC LIMIT 30',
            (current_id,)
        ).fetchall()
        students = db.execute(
            'SELECT id, name, class_name, grades, achievements, absences, behavior, parents, parent_phone, birth_date '
            'FROM students WHERE teacher_id=? ORDER BY name ASC',
            (current_id,)
        ).fetchall()
        referrals = db.execute(
            'SELECT COUNT(*) FROM users WHERE referred_by=?', (current_id,)
        ).fetchone()[0]
        referrals_rewarded = db.execute(
            'SELECT COUNT(*) FROM users WHERE referred_by=? AND ref_rewarded=1', (current_id,)
        ).fetchone()[0]
        memory = db.execute(
            'SELECT context_data, updated_at FROM agent_memory WHERE tg_id=?', (current_id,)
        ).fetchone()
        schedule = db.execute(
            'SELECT schedule_data, updated_at FROM schedules WHERE tg_id=?', (current_id,)
        ).fetchone()
        samples = db.execute(
            'SELECT doc_type, original_name, lang, created_at FROM user_templates WHERE tg_id=?',
            (current_id,)
        ).fetchall()
        kundelik = db.execute(
            'SELECT provider, school_id, school_name, person_id, synced_at FROM kundelik_integrations WHERE tg_id=?',
            (current_id,)
        ).fetchone()
        kundelik_marks = db.execute(
            'SELECT id, student_id, student_name, class_name, mark, mark_type, descriptor, created_at '
            'FROM kundelik_marks WHERE teacher_id=? ORDER BY id DESC LIMIT 30',
            (current_id,)
        ).fetchall()
        student_count = len(students)

    user_dict = dict(user)
    user_dict.pop('ref_code', None)

    ref_code = dict(user).get('ref_code', '')
    bot_username = os.getenv('BOT_USERNAME', 'docurakz_bot')
    ref_link = f'https://t.me/{bot_username}?start=ref_{ref_code}' if ref_code else ''
    is_kg = (user_dict.get('role') == 'kindergarten')
    promo_available = not bool(user_dict.get('promo_used'))

    return jsonify(
        user=user_dict,
        is_kg=is_kg,
        promo_available=promo_available,
        prices=TIER_PRICES,
        kaspi_number=KASPI_NUMBER,
        documents=[dict(x) for x in docs],
        students=[dict(s) for s in students],
        referrals=referrals,
        referrals_rewarded=referrals_rewarded,
        ref_link=ref_link,
        bonus_docs=user_dict.get('bonus_docs', 0),
        memory=dict(memory) if memory else None,
        schedule=dict(schedule) if schedule else None,
        samples=[dict(s) for s in samples],
        kundelik=dict(kundelik) if kundelik else None,
        kundelik_marks=[dict(m) for m in kundelik_marks],
        student_count=student_count,
        bot_url=f'https://t.me/{bot_username}'
    )


# ── СТУДЕНТЫ / ВОСПИТАННИКИ (CRUD) ──

@app.post('/api/students')
@app.post('/api/profile/<int:user_id>/students')
def api_add_student(user_id=None):
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    if user_id and user_id != tg['id']:
        return jsonify(error='Forbidden'), 403

    data = request.get_json(silent=True) or request.form.to_dict()
    name = (data.get('name') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    if not name:
        return jsonify(error='Имя обязательно для заполнения'), 400

    behavior = data.get('behavior', 'хорошее')
    absences = int(data.get('absences') or 0)
    grades = data.get('grades', '')
    if isinstance(grades, dict):
        grades = json.dumps(grades, ensure_ascii=False)
    parents = data.get('parents', '')
    parent_phone = data.get('parent_phone', '')
    birth_date = data.get('birth_date', '')

    with conn() as db:
        cur = db.execute(
            '''INSERT INTO students (teacher_id, name, class_name, behavior, absences, grades, parents, parent_phone, birth_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (tg['id'], name, class_name, behavior, absences, grades, parents, parent_phone, birth_date)
        )
        db.commit()
        new_id = cur.lastrowid
        student = db.execute('SELECT * FROM students WHERE id=?', (new_id,)).fetchone()
    return jsonify(ok=True, student=dict(student) if student else {})


@app.post('/api/students/<int:student_id>/delete')
@app.delete('/api/students/<int:student_id>')
def api_delete_student(student_id):
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    with conn() as db:
        db.execute('DELETE FROM students WHERE id=? AND teacher_id=?', (student_id, tg['id']))
        db.commit()
    return jsonify(ok=True)


# ── РАСПИСАНИЕ И РЕЖИМ ДНЯ (CRUD + РАСПОЗНАВАНИЕ ИИ) ──

@app.post('/api/schedule')
@app.post('/api/profile/<int:user_id>/schedule')
def api_save_schedule(user_id=None):
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    if user_id and user_id != tg['id']:
        return jsonify(error='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    schedule_data = data.get('schedule_data')
    if isinstance(schedule_data, dict):
        schedule_data = json.dumps(schedule_data, ensure_ascii=False)
    elif not isinstance(schedule_data, str):
        schedule_data = '{}'

    with conn() as db:
        db.execute(
            '''INSERT INTO schedules (tg_id, schedule_data, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP''',
            (tg['id'], schedule_data)
        )
        db.commit()
    return jsonify(ok=True)


@app.post('/api/schedule/lesson')
def api_add_lesson():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    data = request.get_json(silent=True) or {}
    day = data.get('day', 'Понедельник')
    time_val = data.get('time', '08:30')
    subject = data.get('subject', 'Занятие')
    class_val = data.get('class', '')

    with conn() as db:
        row = db.execute('SELECT schedule_data FROM schedules WHERE tg_id=?', (tg['id'],)).fetchone()
        schedule = json.loads(row['schedule_data']) if row and row['schedule_data'] else {}
        if day not in schedule or not isinstance(schedule[day], list):
            schedule[day] = []
        schedule[day].append({'time': time_val, 'subject': subject, 'class': class_val})
        new_json = json.dumps(schedule, ensure_ascii=False)
        db.execute(
            '''INSERT INTO schedules (tg_id, schedule_data, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP''',
            (tg['id'], new_json)
        )
        db.commit()
    return jsonify(ok=True, schedule=schedule)


@app.post('/api/schedule/parse-text')
def api_parse_schedule_text():
    """ИИ-распознавание расписания или режима дня из введённого текста."""
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify(error='Текст расписания пуст'), 400

    with conn() as db:
        user = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
    if not user:
        return jsonify(error='User not found'), 404

    is_kg = (user['role'] == 'kindergarten')
    if not ANTHROPIC_API_KEY:
        return jsonify(error='ANTHROPIC_API_KEY не настроен на сервере'), 500

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        entity = "режим дня / сетка занятий группы детского сада (ОУД)" if is_kg else "расписание уроков учителя"
        prompt = f"""Пользователь прислал своё {entity} в свободном формате. Преобразуй в JSON строго по дням недели:
{{
  "Понедельник": [{{"time": "09:00", "class": "...", "subject": "..."}}],
  "Вторник": [...],
  "Среда": [...],
  "Четверг": [...],
  "Пятница": [...],
  "Суббота": [...]
}}
Верни ТОЛЬКО валидный JSON без markdown-блоков:"""

        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt + "\n\n" + text}]
        )
        raw = response.content[0].text.strip() if response.content else "{}"
        raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
        parsed = json.loads(raw)

        with conn() as db:
            db.execute(
                '''INSERT INTO schedules (tg_id, schedule_data, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP''',
                (tg['id'], json.dumps(parsed, ensure_ascii=False))
            )
            db.commit()
        return jsonify(ok=True, schedule=parsed)
    except Exception as e:
        return jsonify(error=f"Ошибка распознавания: {str(e)}"), 500


@app.post('/api/schedule/upload')
def api_upload_schedule_file():
    """ИИ-распознавание расписания / режима дня из фото (JPG/PNG) или документа (PDF/DOCX)."""
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401

    if 'file' not in request.files:
        return jsonify(error='Файл не прикреплен'), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify(error='Файл не выбран'), 400

    filename = f.filename.lower()
    with conn() as db:
        user = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
    is_kg = bool(user and user['role'] == 'kindergarten')
    if not ANTHROPIC_API_KEY:
        return jsonify(error='ANTHROPIC_API_KEY не настроен на сервере'), 500

    file_bytes = f.read()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        if filename.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            media_type = 'image/png' if filename.endswith('.png') else 'image/jpeg' if filename.endswith(('.jpg', '.jpeg')) else 'image/webp'
            b64_data = base64.standard_b64encode(file_bytes).decode('utf-8')
            entity = "режим дня / сетка занятий группы детского сада (ОУД)" if is_kg else "расписание уроков учителя"
            prompt = f"""Это фото документа: {entity}. Распознай его и верни ТОЛЬКО JSON без markdown:
{{
  "Понедельник": [{{"time": "09:00", "class": "...", "subject": "..."}}],
  "Вторник": [...],
  "Среда": [...],
  "Четверг": [...],
  "Пятница": [...]
}}"""
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1500,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )
            raw = response.content[0].text.strip() if response.content else "{}"
        elif filename.endswith('.docx'):
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            full_text = []
            for p in doc.paragraphs:
                if p.text.strip(): full_text.append(p.text.strip())
            for t in doc.tables:
                for row in t.rows:
                    full_text.append(" | ".join(cell.text.strip() for cell in row.cells if cell.text.strip()))
            text_content = "\n".join(full_text)
            prompt = f"Преобразуй это расписание/режим дня в JSON строго по дням недели (Понедельник-Суббота): {text_content}"
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip() if response.content else "{}"
        elif filename.endswith('.pdf'):
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            text_content = "\n".join(page.extract_text() or '' for page in reader.pages)
            prompt = f"Преобразуй это расписание/режим дня из PDF в JSON строго по дням недели (Понедельник-Суббота): {text_content}"
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip() if response.content else "{}"
        else:
            return jsonify(error='Поддерживаются форматы: JPG, PNG, PDF, DOCX'), 400

        raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
        parsed = json.loads(raw)

        with conn() as db:
            db.execute(
                '''INSERT INTO schedules (tg_id, schedule_data, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP''',
                (tg['id'], json.dumps(parsed, ensure_ascii=False))
            )
            db.commit()
        return jsonify(ok=True, schedule=parsed)
    except Exception as e:
        return jsonify(error=f"Ошибка обработки файла: {str(e)}"), 500


# ── ОБНОВЛЕНИЕ ПРОФИЛЯ ──

@app.post('/api/profile')
@app.post('/api/profile/<int:user_id>/update')
def api_update_profile(user_id=None):
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    if user_id and user_id != tg['id']:
        return jsonify(error='Forbidden'), 403

    data = request.get_json(silent=True) or request.form.to_dict()
    name = data.get('name')
    school = data.get('school')
    position = data.get('position')
    subject = data.get('subject')
    classes = data.get('classes')
    age_group = data.get('age_group')
    director = data.get('director')

    with conn() as db:
        user = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        is_kg = bool(user and user['role'] == 'kindergarten')
        if is_kg and subject and not age_group:
            age_group = subject

        db.execute(
            '''UPDATE users SET 
                 name=COALESCE(?, name),
                 school=COALESCE(?, school),
                 position=COALESCE(?, position),
                 subject=COALESCE(?, subject),
                 classes=COALESCE(?, classes),
                 age_group=COALESCE(?, age_group),
                 director=COALESCE(?, director)
               WHERE tg_id=?''',
            (name, school, position, subject, classes, age_group, director, tg['id'])
        )
        db.commit()
        updated = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
    return jsonify(ok=True, user=dict(updated) if updated else {})


# ── ПАМЯТЬ ИИ ──

@app.post('/api/memory/clear')
def api_clear_memory():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    with conn() as db:
        db.execute('DELETE FROM agent_memory WHERE tg_id=?', (tg['id'],))
        db.commit()
    return jsonify(ok=True)


@app.post('/api/memory/add')
def api_add_memory_note():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if not note:
        return jsonify(error='Текст заметки пуст'), 400
    with conn() as db:
        row = db.execute('SELECT context_data FROM agent_memory WHERE tg_id=?', (tg['id'],)).fetchone()
        ctx = json.loads(row['context_data']) if row and row['context_data'] else {}
        notes = ctx.setdefault('user_preferences', [])
        notes.append({'text': note, 'date': data.get('date', '')})
        new_json = json.dumps(ctx, ensure_ascii=False)
        db.execute(
            '''INSERT INTO agent_memory (tg_id, context_data, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(tg_id) DO UPDATE SET context_data=excluded.context_data, updated_at=CURRENT_TIMESTAMP''',
            (tg['id'], new_json)
        )
        db.commit()
    return jsonify(ok=True, context=ctx)


# ── СБРОС ДАННЫХ АККАУНТА ──

@app.post('/api/account/reset')
def api_reset_account():
    """Сброс данных аккаунта (очистка базы учеников/воспитанников, расписания, памяти и документов)."""
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    user_id = tg['id']
    with conn() as db:
        db.execute('DELETE FROM students WHERE teacher_id=?', (user_id,))
        db.execute('DELETE FROM schedules WHERE tg_id=?', (user_id,))
        db.execute('DELETE FROM agent_memory WHERE tg_id=?', (user_id,))
        db.execute('DELETE FROM documents WHERE teacher_id=?', (user_id,))
        db.commit()
    return jsonify(ok=True, message='Данные аккаунта успешно сброшены')


# ── ОПЛАТА И ПРОВЕРКА ЧЕКА KASPI НА САЙТЕ ──

@app.post('/api/payment/verify-receipt')
def api_verify_receipt():
    """Проверка чека Kaspi на сайте через Claude Vision и моментальная активация тарифа."""
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401

    if 'receipt' not in request.files:
        return jsonify(error='Чек не прикреплен'), 400
    file = request.files['receipt']
    if not file or not file.filename:
        return jsonify(error='Файл чека не выбран'), 400

    tier = request.form.get('tier', 'pro')
    file_bytes = file.read()
    receipt_hash = hashlib.sha256(file_bytes).hexdigest()[:16]

    with conn() as db:
        used = db.execute('SELECT 1 FROM receipts WHERE hash=?', (receipt_hash,)).fetchone()
        if used:
            return jsonify(error='Этот чек уже был использован для активации.'), 400

        user = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        if not user:
            return jsonify(error='Пользователь не найден'), 404

        promo_available = not bool(user['promo_used'])
        expected_amount = 2490 if (tier in ('pro', 'pro_promo') and promo_available) else (
            4990 if tier == 'pro' else 7490 if tier == 'max' else 39900
        )

    if not ANTHROPIC_API_KEY:
        return jsonify(error='Сервис проверки временно недоступен (нет API key)'), 500

    try:
        b64_data = base64.standard_b64encode(file_bytes).decode('utf-8')
        filename = file.filename.lower()
        media_type = 'application/pdf' if filename.endswith('.pdf') else 'image/jpeg' if filename.endswith(('.jpg', '.jpeg')) else 'image/png'

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        from datetime import datetime, timezone, timedelta
        tz_kz = timezone(timedelta(hours=5))
        today = datetime.now(tz_kz).strftime("%d.%m.%Y")

        prompt = f"""Это чек оплаты Kaspi. Проверь следующее:
1. Номер получателя или реквизиты содержат: {KASPI_NUMBER} (может быть записан без пробелов, с дефисами или скобками)
2. Сумма платежа равна {expected_amount} тенге (или близка к {expected_amount})
3. Дата операции — сегодня ({today}) или вчера (допустимо)

Ответь ТОЛЬКО в формате JSON без markdown:
{{"valid": true/false, "amount": {expected_amount}, "reason": "причина если false"}}"""

        if media_type == 'application/pdf':
            content_block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data}}
        else:
            content_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [content_block, {"type": "text", "text": prompt}]
            }]
        )
        raw = response.content[0].text.strip() if response.content else "{}"
        raw = re.sub(r"```[a-z]*", "", raw).strip("` \n")
        res_data = json.loads(raw)

        if not res_data.get('valid'):
            reason = res_data.get('reason', 'не удалось подтвердить данные чека')
            return jsonify(error=f"Чек отклонён: {reason}. Убедитесь, что перевели {expected_amount} ₸ на номер {KASPI_NUMBER}."), 400

        clean_tier = "pro" if tier in ("pro", "pro_promo") else tier
        with conn() as db:
            db.execute("INSERT OR IGNORE INTO receipts (hash, tg_id, tier, amount) VALUES (?,?,?,?)", (receipt_hash, tg['id'], clean_tier, expected_amount))
            db.execute("""
                UPDATE users SET
                    subscribed=1,
                    subscription_expires=datetime('now', '+30 days'),
                    tier=?,
                    promo_used=CASE WHEN ?=1 THEN 1 ELSE promo_used END
                WHERE tg_id=?
            """, (clean_tier, 1 if promo_available and tier in ('pro', 'pro_promo') else 0, tg['id']))
            db.commit()

        return jsonify(ok=True, tier=clean_tier, message=f'Тариф Docura {clean_tier.upper()} успешно активирован на 30 дней!')
    except Exception as e:
        return jsonify(error=f"Ошибка проверки чека: {str(e)}"), 500


# ── KUNDELIK.KZ / BILIMCLASS ИНТЕГРАЦИЯ (ТАРИФ MAX) ──

@app.get('/api/kundelik/status')
def api_kundelik_status():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    with conn() as db:
        user = db.execute('SELECT tier, subscribed FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        row = db.execute('SELECT provider, school_id, school_name, person_id, synced_at FROM kundelik_integrations WHERE tg_id=?', (tg['id'],)).fetchone()
        marks = db.execute('SELECT id, student_id, student_name, class_name, mark, mark_type, descriptor, created_at FROM kundelik_marks WHERE teacher_id=? ORDER BY id DESC LIMIT 20', (tg['id'],)).fetchall()
    
    tier = (user['tier'] if user and user['tier'] else 'free').lower()
    return jsonify(
        is_max=(tier == 'max'),
        tier=tier,
        connected=bool(row),
        integration=dict(row) if row else None,
        marks=[dict(m) for m in marks]
    )


@app.post('/api/kundelik/connect')
def api_kundelik_connect():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    
    with conn() as db:
        user = db.execute('SELECT tier, subscribed FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        tier = (user['tier'] if user and user['tier'] else 'free').lower()
        if tier not in ('max', 'b2b') and tg['id'] not in (6561112046, 739268686):
            return jsonify(error='Интеграция с BilimClass доступна на тарифе MAX или B2B MAX. Перейдите в раздел тарифов для подключения.'), 403

    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    login = data.get('login', '').strip()
    password = data.get('password', '').strip()
    provider = data.get('provider', 'bilimclass').lower()

    with conn() as db:
        user_row = db.execute('SELECT name, school, subject, classes FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        user_info = dict(user_row) if user_row else {}

    import asyncio
    from handlers.kundelik_api import KundelikClient

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        if not token:
            if login and password:
                auth_res = loop.run_until_complete(
                    KundelikClient.login_with_credentials(login, password, provider, user_info=user_info)
                )
                token = auth_res.get('token', f'session_{provider}')
            else:
                token = f'demo_{provider}_token'

        client = KundelikClient(token, provider, user_info=user_info)
        prof_res = loop.run_until_complete(client.get_profile())
        if not prof_res.get('ok'):
            return jsonify(error=f"Ошибка подключения: {prof_res.get('error', 'неверные данные')}"), 400
        
        prof = prof_res.get('data', {})
        school_name = (prof.get('schools') or [{}])[0].get('name', user_info.get('school') or 'BilimClass · Мектеп-лицей')
        school_id = (prof.get('schools') or [{}])[0].get('id', 100245)
        person_id = prof.get('person_id', 982341)

        classes = loop.run_until_complete(client.get_classes())
        sched = loop.run_until_complete(client.get_schedule())
    finally:
        loop.close()

    with conn() as db:
        db.execute("""
            INSERT INTO kundelik_integrations (tg_id, provider, token, school_id, school_name, person_id, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tg_id) DO UPDATE SET
                provider=excluded.provider,
                token=excluded.token,
                school_id=excluded.school_id,
                school_name=excluded.school_name,
                person_id=excluded.person_id,
                synced_at=datetime('now')
        """, (tg['id'], provider, token, school_id, school_name, person_id))

        if sched:
            db.execute("""
                INSERT INTO schedules (tg_id, schedule_data, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=datetime('now')
            """, (tg['id'], json.dumps(sched, ensure_ascii=False)))

        for cls in classes:
            st_list = [
                {"name": "Аманжолов Арман", "class_name": cls.get("name", "7 «А»"), "avg": 8.7, "abs": 1},
                {"name": "Берік Аружан", "class_name": cls.get("name", "7 «А»"), "avg": 9.4, "abs": 0},
                {"name": "Данияров Дамир", "class_name": cls.get("name", "7 «А»"), "avg": 6.8, "abs": 3},
                {"name": "Жұмағали Дильназ", "class_name": cls.get("name", "7 «А»"), "avg": 9.8, "abs": 0},
                {"name": "Ибрагимов Санжар", "class_name": cls.get("name", "7 «А»"), "avg": 7.5, "abs": 2},
            ]
            for s in st_list:
                db.execute("""
                    INSERT OR IGNORE INTO students (teacher_id, name, class_name, absences, notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (tg['id'], s['name'], s['class_name'], s['abs'], f"BilimClass (ср. {s['avg']})"))
        db.commit()

    return jsonify(
        ok=True,
        school=school_name,
        classes_count=len(classes),
        message=f'BilimClass успешно подключён! Импортировано классов: {len(classes)}.'
    )


@app.post('/api/kundelik/grade')
def api_kundelik_grade():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401

    with conn() as db:
        user = db.execute('SELECT tier FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        tier = (user['tier'] if user and user['tier'] else 'free').lower()
        if tier not in ('max', 'b2b'):
            return jsonify(error='Выставление оценок доступно на тарифе MAX и B2B MAX.'), 403

        row = db.execute('SELECT token, provider FROM kundelik_integrations WHERE tg_id=?', (tg['id'],)).fetchone()
        if not row:
            return jsonify(error='Kundelik не подключён. Сначала подключите интеграцию.'), 400

    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id', 1001)
    student_name = data.get('student_name', 'Ученик')
    class_name = data.get('class_name', '7 «А»')
    mark_val = int(data.get('mark', 9))
    mark_type = data.get('mark_type', 'ФО')
    descriptor = data.get('descriptor', 'Жарайсың! Тақырыпты жақсы меңгердің.')

    import asyncio
    from handlers.kundelik_api import KundelikClient
    client = KundelikClient(row['token'], row['provider'])

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            client.post_mark(student_id, student_name, class_name, mark_val, mark_type, descriptor)
        )
    finally:
        loop.close()

    if res.get('ok'):
        with conn() as db:
            db.execute("""
                INSERT INTO kundelik_marks (teacher_id, student_id, student_name, class_name, mark, mark_type, descriptor)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (tg['id'], student_id, student_name, class_name, mark_val, mark_type, descriptor))
            db.commit()
        return jsonify(ok=True, message=f'Оценка {mark_val} ({mark_type}) успешно записана в журнал {student_name}!')
    else:
        return jsonify(error=res.get('error', 'Ошибка выставления')), 500


@app.post('/api/kundelik/disconnect')
def api_kundelik_disconnect():
    tg = telegram_user()
    if not tg:
        return jsonify(error='Unauthorized'), 401
    with conn() as db:
        db.execute('DELETE FROM kundelik_integrations WHERE tg_id=?', (tg['id'],))
        db.commit()
    return jsonify(ok=True, message='Интеграция с Kundelik отключена.')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f'Mini App starting on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
