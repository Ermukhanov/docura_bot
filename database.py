import aiosqlite
import json
import os
from datetime import datetime

# ВАЖНО (Railway/деплой): по умолчанию база лежит рядом с кодом — это НЕ переживёт
# редеплой на Railway без volume. Если используешь Railway volume, задай переменную
# окружения DB_PATH равной пути ВНУТРИ смонтированного volume, например:
#   DB_PATH=/data/docura.db
# (где /data — это тот самый "Mount Path", который ты указал при создании volume
# в настройках сервиса на Railway). Без этого volume просто не используется ботом,
# даже если он подключён к сервису.
DB_PATH = os.getenv("DB_PATH", "docura.db")

class Database:
    def __init__(self):
        self.db_path = DB_PATH

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
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
                    FOREIGN KEY (teacher_id) REFERENCES users(tg_id)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL,
                    doc_type TEXT NOT NULL,
                    doc_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (teacher_id) REFERENCES users(tg_id)
                );

                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER,
                    doc_type TEXT,
                    generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    score INTEGER,
                    lang TEXT
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

                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_type TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    content TEXT NOT NULL,
                    title TEXT,
                    added_by INTEGER,
                    is_active INTEGER DEFAULT 1,
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
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tg_id, doc_type, scope)
                );
            """)
            await db.commit()

            # ── Миграция для баз созданных до появления новых колонок ──
            await self._ensure_column(db, "users", "tier", "TEXT")
            await self._ensure_column(db, "users", "age_group", "TEXT")
            await self._ensure_column(db, "users", "ref_code", "TEXT")
            await self._ensure_column(db, "users", "referred_by", "INTEGER")
            await self._ensure_column(db, "users", "ref_rewarded", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "bonus_docs", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "promo_used", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "reset_pending", "INTEGER DEFAULT 0")
            await db.commit()

            # Уникальный индекс на ref_code — создаём отдельно от ALTER TABLE,
            # т.к. SQLite не позволяет добавлять UNIQUE через ADD COLUMN
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code) WHERE ref_code IS NOT NULL"
            )
            await db.commit()

            # Бэкфилл: пользователи, у которых subscribed=1 но tier ещё не проставлен
            # (оформили подписку до появления колонки tier) — считаем их PRO,
            # иначе после этого обновления бот ошибочно предложит им оформить подписку заново.
            await db.execute(
                "UPDATE users SET tier='pro' WHERE subscribed=1 AND (tier IS NULL OR tier='')"
            )
            await db.commit()

    async def _ensure_column(self, db, table: str, column: str, coltype: str):
        """Добавляет колонку в таблицу, если её ещё нет (безопасная миграция)."""
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            cols = [row[1] for row in await cur.fetchall()]
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    # ===== USERS =====
    async def get_user(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def upsert_user(self, tg_id: int, data: dict):
        user = await self.get_user(tg_id)
        if user:
            sets = ", ".join(f"{k}=?" for k in data)
            vals = list(data.values()) + [tg_id]
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f"UPDATE users SET {sets} WHERE tg_id=?", vals)
                await db.commit()
        else:
            data["tg_id"] = tg_id
            cols = ", ".join(data.keys())
            qs   = ", ".join("?" * len(data))
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f"INSERT INTO users ({cols}) VALUES ({qs})", list(data.values()))
                await db.commit()

    async def increment_free(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET free_used=free_used+1 WHERE tg_id=?", (tg_id,))
            await db.commit()

    async def activate_subscription(self, tg_id: int, tier: str = "pro"):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscribed=1, tier=? WHERE tg_id=?", (tier, tg_id)
            )
            await db.commit()

    async def deactivate_subscription(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscribed=0, tier=NULL WHERE tg_id=?", (tg_id,)
            )
            await db.commit()

    # ===== ЛИЧНЫЕ WORD-ОБРАЗЦЫ =====
    async def save_user_template(self, tg_id: int, doc_type: str, file_path: str,
                                 original_name: str, lang: str, metadata: dict | None = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO user_templates (tg_id, doc_type, file_path, original_name, lang, scope, metadata)
                   VALUES (?, ?, ?, ?, ?, 'personal', ?)
                   ON CONFLICT(tg_id, doc_type, scope) DO UPDATE SET
                     file_path=excluded.file_path, original_name=excluded.original_name,
                     lang=excluded.lang, metadata=excluded.metadata, created_at=CURRENT_TIMESTAMP""",
                (tg_id, doc_type, file_path, original_name, lang, json.dumps(metadata or {}, ensure_ascii=False))
            )
            await db.commit()

    async def get_user_template(self, tg_id: int, doc_type: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM user_templates WHERE tg_id=? AND doc_type=? AND scope='personal'", (tg_id, doc_type)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_user_template(self, tg_id: int, doc_type: str) -> str | None:
        template = await self.get_user_template(tg_id, doc_type)
        if not template:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_templates WHERE id=?", (template["id"],))
            await db.commit()
        return template["file_path"]

    async def reset_user_account(self, tg_id: int) -> bool:
        """Очищает только профиль и незавершённый онбординг, сохраняя доступы и историю."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE users SET
                    lang=NULL, name=NULL, school=NULL, position=NULL, subject=NULL,
                    classes=NULL, age_group=NULL, is_class_teacher=0, director=NULL,
                    role=NULL, notified_at=NULL, reset_pending=1
                   WHERE tg_id=?""",
                (tg_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ===== STUDENTS =====
    async def get_students(self, teacher_id: int, class_name: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if class_name:
                async with db.execute(
                    "SELECT * FROM students WHERE teacher_id=? AND class_name=? ORDER BY name",
                    (teacher_id, class_name)
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM students WHERE teacher_id=? ORDER BY class_name, name",
                    (teacher_id,)
                ) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_student(self, student_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM students WHERE id=?", (student_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def add_student(self, teacher_id: int, data: dict):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO students (teacher_id, name, class_name, grades, achievements, absences, behavior, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (teacher_id, data["name"], data["class_name"],
                 json.dumps(data.get("grades", {}), ensure_ascii=False),
                 json.dumps(data.get("achievements", []), ensure_ascii=False),
                 data.get("absences", 0), data.get("behavior", "хорошее"),
                 data.get("notes", ""))
            )
            await db.commit()

    async def update_student(self, student_id: int, data: dict):
        if "grades" in data and isinstance(data["grades"], dict):
            data["grades"] = json.dumps(data["grades"], ensure_ascii=False)
        if "achievements" in data and isinstance(data["achievements"], list):
            data["achievements"] = json.dumps(data["achievements"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [student_id]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE students SET {sets} WHERE id=?", vals)
            await db.commit()

    async def delete_student(self, student_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM students WHERE id=?", (student_id,))
            await db.commit()

    # ===== DOCUMENTS =====
    async def save_document(self, teacher_id: int, doc_type: str, doc_name: str, content: str, score: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO documents (teacher_id, doc_type, doc_name, content, score) VALUES (?,?,?,?,?)",
                (teacher_id, doc_type, doc_name, content, score)
            )
            await db.commit()

    async def get_history(self, teacher_id: int, limit: int = 20):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM documents WHERE teacher_id=? ORDER BY created_at DESC LIMIT ?",
                (teacher_id, limit)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_recent_documents(self, limit: int = 15):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # ===== ANALYTICS =====
    async def log_analytics(self, teacher_id: int, doc_type: str, score: int, lang: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO analytics (teacher_id, doc_type, score, lang) VALUES (?,?,?,?)",
                (teacher_id, doc_type, score, lang)
            )
            await db.commit()

    async def get_admin_stats(self):
        TIER_PRICE = {"basic": 2490, "pro": 3990}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT COUNT(*) as c FROM users") as cur:
                total_users = (await cur.fetchone())["c"]
            async with db.execute("SELECT COUNT(*) as c FROM users WHERE subscribed=1") as cur:
                subscribed = (await cur.fetchone())["c"]
            async with db.execute("SELECT COUNT(*) as c FROM documents") as cur:
                total_docs = (await cur.fetchone())["c"]
            async with db.execute("SELECT COUNT(*) as c FROM documents WHERE date(created_at)=date('now')") as cur:
                today_docs = (await cur.fetchone())["c"]
            async with db.execute("SELECT COUNT(*) as c FROM users WHERE date(created_at) >= date('now','-7 days')") as cur:
                week_users = (await cur.fetchone())["c"]
            async with db.execute("SELECT doc_type, COUNT(*) as cnt FROM documents GROUP BY doc_type ORDER BY cnt DESC LIMIT 5") as cur:
                top_docs = await cur.fetchall()
            async with db.execute("SELECT tier, COUNT(*) as c FROM users WHERE subscribed=1 GROUP BY tier") as cur:
                tier_rows = await cur.fetchall()

        revenue = 0
        for row in tier_rows:
            revenue += TIER_PRICE.get(row["tier"] or "pro", 3990) * row["c"]

        return {
            "total_users": total_users,
            "subscribed": subscribed,
            "total_docs": total_docs,
            "today_docs": today_docs,
            "week_users": week_users,
            "top_docs": [(r["doc_type"], r["cnt"]) for r in top_docs],
            "revenue": revenue
        }

    async def get_all_users(self, limit=50):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_users_for_notification(self):
        """Пользователи которым не отправляли уведомление 3+ дня"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM users WHERE
                   (notified_at IS NULL OR julianday('now') - julianday(notified_at) >= 3)
                   AND name IS NOT NULL""",
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def update_notified(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET notified_at=? WHERE tg_id=?", (datetime.now().isoformat(), tg_id))
            await db.commit()

    # ===== SCHEDULE (Расписание / режим дня) =====
    async def save_schedule(self, tg_id: int, schedule_json: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO schedules (tg_id, schedule_data)
                VALUES (?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP
            """, (tg_id, schedule_json))
            await db.commit()

    async def get_schedule(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT schedule_data FROM schedules WHERE tg_id=?", (tg_id,)) as cur:
                row = await cur.fetchone()
                return row["schedule_data"] if row else None

    # ===== AGENT MEMORY (Контекст агента) =====
    async def save_agent_context(self, tg_id: int, context_json: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO agent_memory (tg_id, context_data)
                VALUES (?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET context_data=excluded.context_data, updated_at=CURRENT_TIMESTAMP
            """, (tg_id, context_json))
            await db.commit()

    async def get_agent_context(self, tg_id: int) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT context_data FROM agent_memory WHERE tg_id=?", (tg_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    return json.loads(row["context_data"])
                return {}

    async def update_agent_context(self, tg_id: int, updates: dict):
        current = await self.get_agent_context(tg_id)
        current.update(updates)
        await self.save_agent_context(tg_id, json.dumps(current, ensure_ascii=False))

    # ===== RECEIPTS (защита от повторного использования чека Kaspi) =====
    async def is_receipt_used(self, receipt_hash: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT 1 FROM receipts WHERE hash=?", (receipt_hash,)) as cur:
                row = await cur.fetchone()
                return row is not None

    async def save_receipt_hash(self, receipt_hash: str, tg_id: int, tier: str, amount: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO receipts (hash, tg_id, tier, amount) VALUES (?,?,?,?)",
                (receipt_hash, tg_id, tier, amount)
            )
            await db.commit()

    # ===== SAMPLES (образцы документов для обучения генерации) =====
    async def add_sample(self, doc_type: str, lang: str, content: str, title: str, added_by: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO samples (doc_type, lang, content, title, added_by)
                   VALUES (?,?,?,?,?)""",
                (doc_type, lang, content, title, added_by)
            )
            await db.commit()

    async def get_all_samples(self, limit: int = 200):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM samples ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_samples_for_type(self, doc_type: str, lang: str, limit: int = 3):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM samples WHERE doc_type=? AND lang=? AND is_active=1
                   ORDER BY created_at DESC LIMIT ?""",
                (doc_type, lang, limit)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def delete_sample(self, sample_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM samples WHERE id=?", (sample_id,))
            await db.commit()

    async def toggle_sample(self, sample_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT is_active FROM samples WHERE id=?", (sample_id,)) as cur:
                row = await cur.fetchone()
            if row:
                new_val = 0 if row[0] else 1
                await db.execute("UPDATE samples SET is_active=? WHERE id=?", (new_val, sample_id))
                await db.commit()

    # ===== REFERRALS (реферальная программа) =====
    async def get_user_by_ref_code(self, ref_code: str):
        if not ref_code:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE ref_code=?", (ref_code,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def generate_unique_ref_code(self) -> str:
        """Генерирует уникальный короткий код (буквы+цифры), проверяя коллизии в БД."""
        import random, string
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(20):
            code = "".join(random.choices(alphabet, k=6))
            existing = await self.get_user_by_ref_code(code)
            if not existing:
                return code
        # Крайний случай — увеличиваем длину, если 20 попыток не хватило
        return "".join(random.choices(alphabet, k=10))

    async def count_referrals(self, tg_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by=? AND ref_rewarded=1", (tg_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def add_bonus_docs(self, tg_id: int, amount: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET bonus_docs = COALESCE(bonus_docs, 0) + ? WHERE tg_id=?",
                (amount, tg_id)
            )
            await db.commit()

    async def mark_ref_rewarded(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET ref_rewarded=1 WHERE tg_id=?", (tg_id,))
            await db.commit()

    # ===== ПРОМО-ПОДПИСКА (первый месяц PRO по акции) =====
    async def mark_promo_used(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET promo_used=1 WHERE tg_id=?", (tg_id,))
            await db.commit()


# ===== Общие константы/хелперы для лимита бесплатных документов =====
FREE_BASE_LIMIT = 3

def free_limit_for(user: dict) -> int:
    """Сколько всего бесплатных документов доступно пользователю
    (база + бонусы за приглашённых друзей)."""
    if not user:
        return FREE_BASE_LIMIT
    return FREE_BASE_LIMIT + (user.get("bonus_docs") or 0)
