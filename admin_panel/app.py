import os
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "docura_admin_secret_2024"

DB_PATH      = os.path.join(os.path.dirname(__file__), '..', 'docura.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXT  = {'pdf', 'docx', 'txt', 'doc'}
ADMIN_LOGIN  = "Unicorn"
ADMIN_PASS   = "Gulkhan"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def init_rag_table():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rag_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                description TEXT,
                content TEXT,
                size_kb INTEGER DEFAULT 0,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()


init_rag_table()


# ── AUTH ──────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if (request.form.get('login') == ADMIN_LOGIN and
                request.form.get('password') == ADMIN_PASS):
            session['admin'] = True
            return redirect(url_for('dashboard'))
        error = "Неверный логин или пароль"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── DASHBOARD ─────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    with db() as conn:
        stats = {
            'total_users':  conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            'subscribed':   conn.execute("SELECT COUNT(*) FROM users WHERE subscribed=1").fetchone()[0],
            'total_docs':   conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            'today_docs':   conn.execute("SELECT COUNT(*) FROM documents WHERE date(created_at)=date('now')").fetchone()[0],
            'week_users':   conn.execute("SELECT COUNT(*) FROM users WHERE date(created_at) >= date('now','-7 days')").fetchone()[0],
            'rag_docs':     conn.execute("SELECT COUNT(*) FROM rag_documents WHERE is_active=1").fetchone()[0],
        }
        stats['revenue'] = stats['subscribed'] * 1990

        top_docs = conn.execute(
            "SELECT doc_type, COUNT(*) as cnt FROM documents GROUP BY doc_type ORDER BY cnt DESC LIMIT 5"
        ).fetchall()

        recent_users = conn.execute(
            "SELECT tg_id, name, school, subscribed, created_at FROM users ORDER BY created_at DESC LIMIT 8"
        ).fetchall()

        # График по дням (последние 7 дней)
        chart_data = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as cnt
            FROM users
            WHERE date(created_at) >= date('now','-6 days')
            GROUP BY day ORDER BY day
        """).fetchall()

    return render_template('dashboard.html',
                           stats=stats,
                           top_docs=top_docs,
                           recent_users=recent_users,
                           chart_data=json.dumps([dict(r) for r in chart_data]))


# ── USERS ─────────────────────────────────────────────
@app.route('/users')
@login_required
def users():
    search = request.args.get('q', '')
    sub_filter = request.args.get('sub', '')
    with db() as conn:
        query = "SELECT * FROM users WHERE 1=1"
        params = []
        if search:
            query += " AND (name LIKE ? OR school LIKE ? OR tg_id LIKE ?)"
            params += [f'%{search}%', f'%{search}%', f'%{search}%']
        if sub_filter == '1':
            query += " AND subscribed=1"
        elif sub_filter == '0':
            query += " AND subscribed=0"
        query += " ORDER BY created_at DESC LIMIT 100"
        all_users = conn.execute(query, params).fetchall()
    return render_template('users.html', users=all_users, search=search, sub_filter=sub_filter)


@app.route('/users/activate/<int:tg_id>', methods=['POST'])
@login_required
def activate_sub(tg_id):
    with db() as conn:
        conn.execute("UPDATE users SET subscribed=1 WHERE tg_id=?", (tg_id,))
        conn.commit()
    flash(f'✅ Подписка активирована для {tg_id}', 'success')
    return redirect(url_for('users'))


@app.route('/users/deactivate/<int:tg_id>', methods=['POST'])
@login_required
def deactivate_sub(tg_id):
    with db() as conn:
        conn.execute("UPDATE users SET subscribed=0 WHERE tg_id=?", (tg_id,))
        conn.commit()
    flash(f'Подписка деактивирована для {tg_id}', 'info')
    return redirect(url_for('users'))


@app.route('/users/delete/<int:tg_id>', methods=['POST'])
@login_required
def delete_user(tg_id):
    with db() as conn:
        conn.execute("DELETE FROM users WHERE tg_id=?", (tg_id,))
        conn.commit()
    flash(f'Пользователь {tg_id} удалён', 'warning')
    return redirect(url_for('users'))


# ── DOCUMENTS ─────────────────────────────────────────
@app.route('/documents')
@login_required
def documents():
    with db() as conn:
        docs = conn.execute(
            "SELECT d.*, u.name as teacher_name FROM documents d "
            "LEFT JOIN users u ON d.teacher_id=u.tg_id "
            "ORDER BY d.created_at DESC LIMIT 100"
        ).fetchall()
    return render_template('documents.html', docs=docs)


# ── BROADCAST ─────────────────────────────────────────
@app.route('/broadcast', methods=['GET', 'POST'])
@login_required
def broadcast():
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        target = request.form.get('target', 'all')
        if text:
            with db() as conn:
                if target == 'pro':
                    users_list = conn.execute("SELECT tg_id FROM users WHERE subscribed=1").fetchall()
                elif target == 'free':
                    users_list = conn.execute("SELECT tg_id FROM users WHERE subscribed=0").fetchall()
                else:
                    users_list = conn.execute("SELECT tg_id FROM users").fetchall()
            # Сохраняем задачу рассылки
            with db() as conn:
                conn.execute(
                    "INSERT INTO bot_settings (key, value) VALUES (?, ?)",
                    (f'broadcast_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                     json.dumps({'text': text, 'target': target, 'count': len(users_list)}))
                )
                conn.commit()
            flash(f'✅ Рассылка запланирована для {len(users_list)} пользователей', 'success')
            return redirect(url_for('broadcast'))
    return render_template('broadcast.html')


# ── RAG / БАЗА ЗНАНИЙ ─────────────────────────────────
@app.route('/knowledge')
@login_required
def knowledge():
    with db() as conn:
        docs = conn.execute(
            "SELECT * FROM rag_documents ORDER BY uploaded_at DESC"
        ).fetchall()
    return render_template('knowledge.html', docs=docs)


@app.route('/knowledge/upload', methods=['POST'])
@login_required
def upload_doc():
    if 'file' not in request.files:
        flash('Файл не выбран', 'warning')
        return redirect(url_for('knowledge'))

    file = request.files['file']
    description = request.form.get('description', '')

    if file.filename == '':
        flash('Файл не выбран', 'warning')
        return redirect(url_for('knowledge'))

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        flash('Формат не поддерживается. Используй PDF, DOCX, TXT', 'danger')
        return redirect(url_for('knowledge'))

    filename = secure_filename(file.filename)
    # Уникальное имя
    unique_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    filepath = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(filepath)

    # Извлекаем текст
    content = extract_text(filepath, ext)
    size_kb = os.path.getsize(filepath) // 1024

    with db() as conn:
        conn.execute(
            """INSERT INTO rag_documents
               (filename, original_name, file_type, description, content, size_kb)
               VALUES (?,?,?,?,?,?)""",
            (unique_name, filename, ext, description, content[:50000], size_kb)
        )
        conn.commit()

    flash(f'✅ Документ "{filename}" загружен и добавлен в базу знаний', 'success')
    return redirect(url_for('knowledge'))


@app.route('/knowledge/toggle/<int:doc_id>', methods=['POST'])
@login_required
def toggle_doc(doc_id):
    with db() as conn:
        current = conn.execute("SELECT is_active FROM rag_documents WHERE id=?", (doc_id,)).fetchone()
        if current:
            new_val = 0 if current['is_active'] else 1
            conn.execute("UPDATE rag_documents SET is_active=? WHERE id=?", (new_val, doc_id))
            conn.commit()
    return redirect(url_for('knowledge'))


@app.route('/knowledge/delete/<int:doc_id>', methods=['POST'])
@login_required
def delete_doc(doc_id):
    with db() as conn:
        row = conn.execute("SELECT filename FROM rag_documents WHERE id=?", (doc_id,)).fetchone()
        if row:
            filepath = os.path.join(UPLOAD_FOLDER, row['filename'])
            if os.path.exists(filepath):
                os.remove(filepath)
            conn.execute("DELETE FROM rag_documents WHERE id=?", (doc_id,))
            conn.commit()
    flash('Документ удалён из базы знаний', 'info')
    return redirect(url_for('knowledge'))


@app.route('/knowledge/preview/<int:doc_id>')
@login_required
def preview_doc(doc_id):
    with db() as conn:
        doc = conn.execute("SELECT * FROM rag_documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        return "Не найдено", 404
    return render_template('preview.html', doc=doc)


# ── SETTINGS ──────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        keys = ['sub_price', 'free_limit', 'welcome_text', 'anthropic_model']
        with db() as conn:
            for k in keys:
                v = request.form.get(k, '')
                conn.execute(
                    "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?,?,?)",
                    (k, v, datetime.now().isoformat())
                )
            conn.commit()
        flash('✅ Настройки сохранены', 'success')
        return redirect(url_for('settings'))

    with db() as conn:
        rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
        cfg = {r['key']: r['value'] for r in rows}
    return render_template('settings.html', cfg=cfg)


# ── API для графиков ───────────────────────────────────
@app.route('/api/stats')
@login_required
def api_stats():
    with db() as conn:
        daily = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as users
            FROM users WHERE date(created_at) >= date('now','-29 days')
            GROUP BY day ORDER BY day
        """).fetchall()
        docs_daily = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as docs
            FROM documents WHERE date(created_at) >= date('now','-29 days')
            GROUP BY day ORDER BY day
        """).fetchall()
    return jsonify({
        'users': [dict(r) for r in daily],
        'docs':  [dict(r) for r in docs_daily]
    })


# ── УТИЛИТЫ ───────────────────────────────────────────
def extract_text(filepath: str, ext: str) -> str:
    try:
        if ext == 'txt':
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        elif ext == 'pdf':
            try:
                import pypdf
                reader = pypdf.PdfReader(filepath)
                return '\n'.join(p.extract_text() or '' for p in reader.pages)
            except ImportError:
                return "[PDF загружен. Установи pypdf для извлечения текста: pip install pypdf]"
        elif ext in ('docx', 'doc'):
            try:
                from docx import Document
                doc = Document(filepath)
                return '\n'.join(p.text for p in doc.paragraphs)
            except Exception:
                return "[DOCX загружен. Текст будет доступен при следующем запуске.]"
    except Exception as e:
        return f"[Ошибка извлечения текста: {e}]"
    return ""



if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
