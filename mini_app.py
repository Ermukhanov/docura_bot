import os, json, hmac, hashlib, sqlite3
from urllib.parse import parse_qsl
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder='mini_app/templates')
DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(__file__), 'docura.db'))

def conn():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

def telegram_user():
    raw, token = request.headers.get('X-Telegram-Init-Data', ''), os.getenv('TELEGRAM_TOKEN', '')
    if not raw or not token: return None
    data = dict(parse_qsl(raw, keep_blank_values=True)); received = data.pop('hash', '')
    check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received): return None
    try: return json.loads(data.get('user', '{}'))
    except json.JSONDecodeError: return None

@app.get('/app')
def app_page(): return render_template('index.html')

@app.get('/api/me')
def api_me():
    tg = telegram_user()
    if not tg: return jsonify(error='Telegram authorization required'), 401
    with conn() as db:
        user = db.execute('SELECT * FROM users WHERE tg_id=?', (tg['id'],)).fetchone()
        if not user: return jsonify(error='User not found'), 404
        docs = db.execute('SELECT doc_name, score, created_at FROM documents WHERE teacher_id=? ORDER BY created_at DESC LIMIT 8', (tg['id'],)).fetchall()
        referrals = db.execute('SELECT COUNT(*) FROM users WHERE referred_by=?', (tg['id'],)).fetchone()[0]
        memory = db.execute('SELECT context_data, updated_at FROM agent_memory WHERE tg_id=?', (tg['id'],)).fetchone()
        schedule = db.execute('SELECT schedule_data, updated_at FROM schedules WHERE tg_id=?', (tg['id'],)).fetchone()
        samples = db.execute('SELECT COUNT(*) FROM user_templates WHERE tg_id=?', (tg['id'],)).fetchone()[0]
    return jsonify(user=dict(user), documents=[dict(x) for x in docs], referrals=referrals,
                   memory=dict(memory) if memory else None,
                   schedule=dict(schedule) if schedule else None, samples=samples,
                   bot='https://t.me/docurakz_bot')

if __name__ == '__main__': app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
