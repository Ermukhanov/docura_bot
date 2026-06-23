import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.environ.get("DATA_DIR", "."), "docura.db")

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
                    is_class_teacher INTEGER DEFAULT 0,
                    director TEXT,
                    role TEXT DEFAULT 'teacher',
                    subscribed INTEGER DEFAULT 0,
                    tier TEXT DEFAULT 'free',
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

                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_type TEXT NOT NULL,
                    lang TEXT DEFAULT 'ru',
                    content TEXT NOT NULL,
                    title TEXT,
                    added_by INTEGER,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.commit()

            # Миграция: добавляем колонку tier тем у кого база создана до введения тарифов.
            # У всех кто уже был subscribed=1 раньше — это были покупатели единственного
            # старого тарифа, по смыслу ближайший новому tier='pro', чтобы никто не потерял доступ.
            async with db.execute("PRAGMA table_info(users)") as cur:
                cols = [row[1] for row in await cur.fetchall()]
            if "tier" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
                await db.execute("UPDATE users SET tier='pro' WHERE subscribed=1")
                await db.commit()

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
            await db.execute("UPDATE users SET subscribed=1, tier=? WHERE tg_id=?", (tier, tg_id))
            await db.commit()

    async def deactivate_subscription(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET subscribed=0, tier='free' WHERE tg_id=?", (tg_id,))
            await db.commit()

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

    # ===== ANALYTICS =====
    async def log_analytics(self, teacher_id: int, doc_type: str, score: int, lang: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO analytics (teacher_id, doc_type, score, lang) VALUES (?,?,?,?)",
                (teacher_id, doc_type, score, lang)
            )
            await db.commit()

    async def get_admin_stats(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                total_users = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE subscribed=1") as cur:
                subscribed = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE tier='basic'") as cur:
                basic_count = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE tier='pro'") as cur:
                pro_count = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM documents") as cur:
                total_docs = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM documents WHERE date(created_at)=date('now')") as cur:
                today_docs = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE date(created_at) >= date('now','-7 days')") as cur:
                week_users = (await cur.fetchone())[0]
            async with db.execute("SELECT doc_type, COUNT(*) as cnt FROM documents GROUP BY doc_type ORDER BY cnt DESC LIMIT 5") as cur:
                top_docs = await cur.fetchall()
        return {
            "total_users": total_users,
            "subscribed": subscribed,
            "basic_count": basic_count,
            "pro_count": pro_count,
            "total_docs": total_docs,
            "today_docs": today_docs,
            "week_users": week_users,
            "top_docs": top_docs,
            "revenue": basic_count * 2490 + pro_count * 3990
        }

    async def get_recent_documents(self, limit: int = 20):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # ===== SAMPLES (RAG обучение) =====
    async def add_sample(self, doc_type: str, lang: str, content: str, title: str, added_by: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO samples (doc_type, lang, content, title, added_by) VALUES (?,?,?,?,?)",
                (doc_type, lang, content, title, added_by)
            )
            await db.commit()

    async def get_samples(self, doc_type: str, lang: str = None, limit: int = 2):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if lang:
                async with db.execute(
                    "SELECT * FROM samples WHERE doc_type=? AND lang=? AND is_active=1 ORDER BY created_at DESC LIMIT ?",
                    (doc_type, lang, limit)
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM samples WHERE doc_type=? AND is_active=1 ORDER BY created_at DESC LIMIT ?",
                    (doc_type, limit)
                ) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_all_samples(self, limit: int = 100):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM samples ORDER BY created_at DESC LIMIT ?", (limit,)
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
