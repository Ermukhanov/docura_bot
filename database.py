import aiosqlite
import json
import os
from typing import Any
from datetime import datetime, timedelta

# ВАЖНО (Railway/деплой): по умолчанию база лежит рядом с кодом — это НЕ переживёт
# редеплой на Railway без volume. Если используешь Railway volume, задай переменную
# окружения DB_PATH равной пути ВНУТРИ смонтированного volume, например:
#   DB_PATH=/data/docura.db
# (где /data — это тот самый "Mount Path", который ты указал при создании volume
# в настройках сервиса на Railway). Без этого volume просто не используется ботом,
# даже если он подключён к сервису.
DB_PATH = os.getenv("DB_PATH", "docura.db")

USER_UPDATE_COLUMNS = frozenset({
    "lang", "name", "school", "position", "subject", "classes", "age_group",
    "is_class_teacher", "director", "role", "subscribed", "tier", "free_used",
    "created_at", "notified_at", "ref_code", "referred_by", "ref_rewarded",
    "bonus_docs", "promo_used", "reset_pending", "document_lang", "lang_selected",
    "last_doc_type", "last_doc_date", "auto_generate", "reminder_days", "sub_expires",
    "funnel_step", "seen_sections", "notifications_enabled", "last_active_at",
    "last_broadcast_at", "org_code",
})
STUDENT_UPDATE_COLUMNS = frozenset({
    "name", "class_name", "grades", "achievements", "absences", "behavior", "notes",
    "parents", "parent_phone", "address", "birth_date", "health_group", "notes_extended",
})


def _validated_fields(data: dict, allowed: frozenset[str], table: str) -> dict:
    if not isinstance(data, dict):
        raise TypeError(f"{table} updates must be a dictionary")
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unsupported {table} field(s): {', '.join(sorted(unknown))}")
    return data

FUNNEL_EVENTS = {
    "start", "doc_type_chosen", "doc_generated", "lang_chosen", "role_chosen",
    "profile_done", "paywall_shown", "generation_failed", "feedback", "org_joined",
}


class Database:
    def __init__(self):
        self.db_path = DB_PATH

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
                    feedback TEXT,
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

                CREATE TABLE IF NOT EXISTS funnel_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    doc_type TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );


            """)
            await db.commit()

            # ── Миграция для баз созданных до появления новых колонок ──
            await self._ensure_column(db, "users", "tier", "TEXT")
            await self._ensure_column(db, "documents", "file_id", "TEXT")
            await self._ensure_column(db, "users", "age_group", "TEXT")
            await self._ensure_column(db, "users", "ref_code", "TEXT")
            await self._ensure_column(db, "users", "referred_by", "INTEGER")
            await self._ensure_column(db, "users", "ref_rewarded", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "bonus_docs", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "promo_used", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "reset_pending", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "document_lang", "TEXT")
            await self._ensure_column(db, "users", "lang_selected", "INTEGER DEFAULT 0")
            # Память агента и настройки персональных напоминаний.
            # Добавляются отдельно, чтобы не ломать уже созданные базы SQLite.
            await self._ensure_column(db, "users", "last_doc_type", "TEXT")
            await self._ensure_column(db, "users", "last_doc_date", "TEXT")
            await self._ensure_column(db, "users", "auto_generate", "INTEGER DEFAULT 0")
            await self._ensure_column(db, "users", "reminder_days", "INTEGER DEFAULT 7")
            # Дата окончания подписки. NULL означает "без автоматического срока" —
            # так помечены и уже существующие подписчики на момент этой миграции
            # (никого не отключает задним числом), и любые подписки, активированные
            # вручную через админку (B2B-партнёры) — за них отвечает администратор,
            # а не автоматика.
            await self._ensure_column(db, "users", "sub_expires", "TEXT")
            await self._ensure_column(db, "students", "parents", "TEXT")
            await self._ensure_column(db, "students", "parent_phone", "TEXT")
            await self._ensure_column(db, "students", "address", "TEXT")
            await self._ensure_column(db, "students", "birth_date", "TEXT")
            await self._ensure_column(db, "students", "health_group", "TEXT")
            await self._ensure_column(db, "students", "notes_extended", "TEXT")
            await self._ensure_column(db, "analytics", "rating", "INTEGER")
            await self._ensure_column(db, "documents", "feedback", "TEXT")
            await self._ensure_column(db, "users", "funnel_step", "TEXT")
            await self._ensure_column(db, "users", "seen_sections", "TEXT DEFAULT '{}'")
            await self._ensure_column(db, "users", "notifications_enabled", "INTEGER DEFAULT 1")
            await self._ensure_column(db, "users", "last_active_at", "TEXT")
            await self._ensure_column(db, "users", "last_broadcast_at", "TEXT")
            await self._ensure_column(db, "users", "first_doc_reminder_sent_at", "TEXT")
            # Интеграции Kundelik/BilimClass удалены: стираем сохранённые токены.
            await db.execute("DROP TABLE IF EXISTS kundelik_integrations")
            await db.execute("DROP TABLE IF EXISTS kundelik_marks")
            await self._ensure_column(db, "users", "winback_sent_at", "TEXT")
            await self._ensure_column(db, "users", "winback_answered", "INTEGER DEFAULT 0")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS organizations (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    seats INTEGER DEFAULT 25,
                    active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            await self._ensure_column(db, "users", "org_code", "TEXT")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    reason TEXT DEFAULT '',
                    text TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            await db.commit()

            # Уникальный индекс на ref_code — создаём отдельно от ALTER TABLE,
            # т.к. SQLite не позволяет добавлять UNIQUE через ADD COLUMN
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code) WHERE ref_code IS NOT NULL"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_funnel_events_event_created "
                "ON funnel_events(event, created_at)"
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

    async def has_seen_section(self, tg_id: int, section: str) -> bool:
        user = await self.get_user(tg_id)
        if not user:
            return False
        try:
            return json.loads(user.get("seen_sections") or "{}").get(section, False)
        except (TypeError, json.JSONDecodeError):
            return False

    async def mark_section_seen(self, tg_id: int, section: str):
        user = await self.get_user(tg_id)
        if not user:
            return
        try:
            seen = json.loads(user.get("seen_sections") or "{}")
        except (TypeError, json.JSONDecodeError):
            seen = {}
        seen[section] = True
        await self.upsert_user(tg_id, {"seen_sections": json.dumps(seen)})

    async def upsert_user(self, tg_id: int, data: dict):
        data = _validated_fields(data, USER_UPDATE_COLUMNS, "user")
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                sanitized[k] = json.dumps(v, ensure_ascii=False)
            else:
                sanitized[k] = v
        if not sanitized:
            return
        columns = ["tg_id", *sanitized]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in sanitized)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(tg_id) DO UPDATE SET {updates}",
                [tg_id, *sanitized.values()],
            )
            await db.commit()

    async def increment_free(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET free_used=free_used+1 WHERE tg_id=?", (tg_id,))
            await db.commit()

    async def activate_subscription(self, tg_id: int, tier: str = "pro", expires_in_days: int | None = None):
        """
        expires_in_days — на сколько дней активировать подписку с автопродлением/
        напоминанием об оплате. Передавать ТОЛЬКО для самостоятельной оплаты
        пользователем через чек (см. profile.py _process_receipt).
        Если не передано (по умолчанию) — подписка бессрочная, пока её не снимут
        вручную. Так активируются B2B-партнёры (школы/садики) через админку —
        ими управляет администратор, а не автоматика, чтобы бот случайно не
        отключил партнёрское учреждение через 30 дней без ведома администратора.
        """
        expires = (datetime.now() + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscribed=1, tier=?, sub_expires=? WHERE tg_id=?", (tier, expires, tg_id)
            )
            await db.commit()

    # ── B2B: садик/школа подключает всех педагогов одной ссылкой ──
    async def create_org(self, name: str, seats: int = 25) -> str:
        import secrets
        async with aiosqlite.connect(self.db_path) as db:
            while True:
                code = secrets.token_hex(3)
                async with db.execute("SELECT 1 FROM organizations WHERE code=?", (code,)) as cur:
                    if not await cur.fetchone():
                        break
            await db.execute(
                "INSERT INTO organizations (code, name, seats) VALUES (?, ?, ?)",
                (code, name.strip()[:120], max(1, int(seats))),
            )
            await db.commit()
        return code

    async def get_org(self, code: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM organizations WHERE code=?", (code,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            org = dict(row)
            async with db.execute("SELECT COUNT(*) FROM users WHERE org_code=?", (code,)) as cur:
                org["members"] = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM documents d JOIN users u ON u.tg_id=d.teacher_id "
                "WHERE u.org_code=? AND d.created_at >= datetime('now','-30 days')", (code,)
            ) as cur:
                org["docs_30d"] = (await cur.fetchone())[0]
            return org

    async def list_orgs(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT code FROM organizations ORDER BY created_at DESC") as cur:
                codes = [r[0] for r in await cur.fetchall()]
        return [await self.get_org(c) for c in codes]

    async def join_org(self, code: str, tg_id: int) -> tuple[str, dict | None]:
        """ok | already | full | inactive | not_found. При ok — тариф b2b без срока."""
        org = await self.get_org(code)
        if not org:
            return "not_found", None
        if not org["active"]:
            return "inactive", org
        user = await self.get_user(tg_id)
        if user and user.get("org_code") == code:
            return "already", org
        if org["members"] >= org["seats"]:
            return "full", org
        await self.upsert_user(tg_id, {"org_code": code})
        await self.activate_subscription(tg_id, tier="b2b")
        return "ok", org

    async def set_org_active(self, code: str, active: bool) -> int:
        """Выключение снимает тариф со всех педагогов организации; включение возвращает."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE organizations SET active=? WHERE code=?", (1 if active else 0, code))
            if active:
                cur = await db.execute(
                    "UPDATE users SET subscribed=1, tier='b2b', sub_expires=NULL WHERE org_code=?", (code,))
            else:
                cur = await db.execute(
                    "UPDATE users SET subscribed=0, tier=NULL, sub_expires=NULL WHERE org_code=? AND tier='b2b'", (code,))
            await db.commit()
            return cur.rowcount

    async def deactivate_subscription(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscribed=0, tier=NULL, sub_expires=NULL WHERE tg_id=?", (tg_id,)
            )
            await db.commit()

    async def get_expired_subscriptions(self):
        """Пользователи с автопродлением (expires_in_days при активации), у которых
        истёк срок оплаты. Подписки без установленного срока (B2B/админ-активация)
        сюда никогда не попадают — ими управляет администратор вручную."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE subscribed=1 AND sub_expires IS NOT NULL AND sub_expires < ?",
                (datetime.now().isoformat(),)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

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
            async with db.execute(
                "SELECT * FROM students WHERE teacher_id=? ORDER BY class_name, name",
                (teacher_id,)
            ) as cur:
                rows = await cur.fetchall()
            # Класс сравниваем в нормальном виде: «7а» и «7 «А»» — один класс.
            want = normalize_class_name(class_name) if class_name else None
            # Один и тот же ученик мог быть сохранён несколько раз (повторный импорт
            # списка) — в выборе он показывался дважды. Оставляем первую запись.
            out, seen = [], set()
            for r in rows:
                d = dict(r)
                d["class_name"] = normalize_class_name(d.get("class_name"))
                if want and d["class_name"] != want:
                    continue
                key = (str(d["name"] or "").strip().lower(), d["class_name"].lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append(d)
            return out

    async def get_student(self, student_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM students WHERE id=?", (student_id,)) as cur:
                row = await cur.fetchone()
                return ({**dict(row), "class_name": normalize_class_name(row["class_name"])} if row else None)

    async def get_student_for_teacher(self, student_id: int, teacher_id: int):
        """Fetch a student only when it belongs to the requesting teacher."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM students WHERE id=? AND teacher_id=?", (student_id, teacher_id)
            ) as cur:
                row = await cur.fetchone()
                return ({**dict(row), "class_name": normalize_class_name(row["class_name"])} if row else None)

    async def add_student(self, teacher_id: int, data: dict | str = None, class_name: str = "", **kwargs):
        """Добавить ученика. Поддерживает как передачу словаря data, так и именованные аргументы:
        add_student(user_id, data={...})
        add_student(user_id, name="Арман", class_name="7А", notes="...", absences=0)
        """
        async with aiosqlite.connect(self.db_path) as db:
            if isinstance(data, dict):
                st_dict = data
            elif isinstance(data, str):
                st_dict = {"name": data, "class_name": class_name, **kwargs}
            else:
                st_dict = kwargs

            name = st_dict.get("name", "")
            cls_name = normalize_class_name(st_dict.get("class_name", ""))
            grades = st_dict.get("grades", {})
            achievements = st_dict.get("achievements", [])
            absences = st_dict.get("absences", 0)
            behavior = st_dict.get("behavior", "хорошее")
            notes = st_dict.get("notes", "")

            async with db.execute(
                "SELECT id FROM students WHERE teacher_id=? AND lower(trim(name))=lower(trim(?)) AND lower(trim(class_name))=lower(trim(?))",
                (teacher_id, name, cls_name),
            ) as cur:
                if await cur.fetchone():
                    return  # такой ученик уже есть — не создаём дубль

            await db.execute(
                """INSERT INTO students (teacher_id, name, class_name, grades, achievements, absences, behavior, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    teacher_id,
                    name,
                    cls_name,
                    json.dumps(grades, ensure_ascii=False) if isinstance(grades, (dict, list)) else str(grades),
                    json.dumps(achievements, ensure_ascii=False) if isinstance(achievements, list) else str(achievements),
                    absences,
                    behavior,
                    notes
                )
            )
            await db.commit()

    async def update_student(self, student_id: int, data: dict):
        data = _validated_fields(data, STUDENT_UPDATE_COLUMNS, "student")
        if not data:
            return
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                sanitized[k] = json.dumps(v, ensure_ascii=False)
            else:
                sanitized[k] = v
        sets = ", ".join(f"{k}=?" for k in sanitized)
        vals = list(sanitized.values()) + [student_id]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE students SET {sets} WHERE id=?", vals)
            await db.commit()

    async def update_student_for_teacher(self, student_id: int, teacher_id: int, data: dict) -> bool:
        data = _validated_fields(data, STUDENT_UPDATE_COLUMNS, "student")
        if not data:
            return False
        sanitized = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in data.items()
        }
        sets = ", ".join(f"{key}=?" for key in sanitized)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE students SET {sets} WHERE id=? AND teacher_id=?",
                [*sanitized.values(), student_id, teacher_id],
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_student(self, student_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM students WHERE id=?", (student_id,))
            await db.commit()

    async def delete_student_for_teacher(self, student_id: int, teacher_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM students WHERE id=? AND teacher_id=?", (student_id, teacher_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ===== DOCUMENTS =====
    async def save_document(self, teacher_id: int, doc_type: str, doc_name: str, content: str, score: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO documents (teacher_id, doc_type, doc_name, content, score) VALUES (?,?,?,?,?)",
                (teacher_id, doc_type, doc_name, content, score)
            )
            await db.execute(
                "INSERT INTO funnel_events (tg_id, event, doc_type) VALUES (?, 'doc_generated', ?)",
                (teacher_id, doc_type),
            )
            await db.execute(
                "UPDATE users SET funnel_step='doc_generated' WHERE tg_id=?",
                (teacher_id,),
            )
            await db.commit()
            return cursor.lastrowid

    async def update_document_feedback(self, document_id: int, feedback: str):
        """Сохраняет оценку, не меняя историю и содержимое документа."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE documents SET feedback=? WHERE id=?", (feedback, document_id))
            await db.commit()

    async def get_document(self, document_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM documents WHERE id=?", (document_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_document_file(self, document_id: int, file_id: str):
        """Telegram file_id отправленного Word-файла — чтобы из «Моих документов»
        прислать ровно тот же файл, без повторной генерации."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE documents SET file_id=? WHERE id=?", (file_id, document_id))
            await db.commit()

    async def count_documents(self, teacher_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM documents WHERE teacher_id=?", (teacher_id,)) as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

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
        # ВАЖНО: в системе только один платный тариф — PRO. "pro_promo" (2490 тг) — это
        # тот же tier="pro" в БД, просто скидка на первый месяц; для расчёта текущего/
        # регулярного дохода используем полную цену подписки (см. profile.py TIER_PRICES).
        from pricing import TIER_PRICES
        PRO_PRICE = TIER_PRICES["pro"]
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

        revenue = subscribed * PRO_PRICE

        return {
            "total_users": total_users,
            "subscribed": subscribed,
            "total_docs": total_docs,
            "today_docs": today_docs,
            "week_users": week_users,
            "top_docs": [(r["doc_type"], r["cnt"]) for r in top_docs],
            "revenue": revenue
        }

    async def get_all_users(self, limit=50, offset=0, filt="all"):
        filters = {
            "all": "",
            "pro": "WHERE subscribed=1 AND (tier='pro' OR tier IS NULL OR tier='')",
            "max": "WHERE subscribed=1 AND tier='max'",
            "free": "WHERE COALESCE(subscribed, 0)=0",
            "kg": "WHERE role='kindergarten'",
            "unregistered": "WHERE (name IS NULL OR trim(name)='')",
        }
        if filt not in filters:
            raise ValueError("Unsupported user filter")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM users {filters[filt]} ORDER BY created_at DESC, tg_id DESC LIMIT ? OFFSET ?",
                (max(1, min(int(limit), 1000)), max(0, int(offset))),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_unregistered_users(self, limit=100000):
        """Users without a profile name; notification opt-outs are excluded."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM users
                   WHERE (name IS NULL OR trim(name)='')
                     AND COALESCE(notifications_enabled, 1)=1
                   ORDER BY created_at, tg_id LIMIT ?""",
                (max(1, min(int(limit), 100000)),),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_user_funnel_events(self, tg_id: int, limit=20):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT event, doc_type, created_at FROM funnel_events
                   WHERE tg_id=? ORDER BY id DESC LIMIT ?""",
                (tg_id, max(1, min(int(limit), 100))),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_users_for_notification(self):
        """Пользователи которым не отправляли уведомление 3+ дня и у которых не отключены уведомления"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM users WHERE
                   (notified_at IS NULL OR julianday('now') - julianday(notified_at) >= 3)
                   AND (notifications_enabled IS NULL OR notifications_enabled = 1)
                   AND (name IS NOT NULL OR role IS NOT NULL)
                   AND (COALESCE(free_used, 0) > 0 OR EXISTS (
                       SELECT 1 FROM documents d WHERE d.teacher_id=users.tg_id
                   ))""",
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_users_for_broadcast(self, days: int = 14):
        """Пользователи с включёнными уведомлениями, не получавшие рассылку news >= 14 дней"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM users WHERE
                   (last_broadcast_at IS NULL OR julianday('now') - julianday(last_broadcast_at) >= ?)
                   AND (notifications_enabled IS NULL OR notifications_enabled = 1)
                   AND name IS NOT NULL
                   AND (COALESCE(free_used, 0) > 0 OR EXISTS (
                       SELECT 1 FROM documents d WHERE d.teacher_id=users.tg_id
                   ))""",
                (days,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def update_notified(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET notified_at=? WHERE tg_id=?", (datetime.now().isoformat(), tg_id))
            await db.commit()

    async def update_last_active(self, tg_id: int, funnel_step: str | None = None):
        async with aiosqlite.connect(self.db_path) as db:
            if funnel_step:
                await db.execute(
                    "UPDATE users SET last_active_at=?, funnel_step=? WHERE tg_id=?",
                    (datetime.now().isoformat(), funnel_step[:120], tg_id),
                )
            else:
                await db.execute("UPDATE users SET last_active_at=? WHERE tg_id=?", (datetime.now().isoformat(), tg_id))
            await db.commit()

    async def update_broadcast_sent(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET last_broadcast_at=? WHERE tg_id=?", (datetime.now().isoformat(), tg_id))
            await db.commit()

    async def set_notifications_enabled(self, tg_id: int, enabled: bool):
        val = 1 if enabled else 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET notifications_enabled=? WHERE tg_id=?", (val, tg_id))
            await db.commit()

    # ===== FUNNEL EVENTS / FIRST DOCUMENT REMINDER =====
    async def log_funnel_event(self, tg_id: int, event: str, doc_type: str = ""):
        if event not in FUNNEL_EVENTS:
            raise ValueError(f"Unsupported funnel event: {event}")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO funnel_events (tg_id, event, doc_type) VALUES (?, ?, ?)",
                (tg_id, event, doc_type or ""),
            )
            await db.commit()

    async def get_funnel_report(self, days: int | None = 30) -> dict:
        """Воронка новых пользователей за период (по таблицам users/documents —
        работает и для тех, кто пришёл до появления событий)."""
        since = "datetime('now', '-100 years')" if not days else f"datetime('now', '-{int(days)} days')"
        # Люди: и те, у кого есть строка users, и те, кто только нажал /start
        # (строка users появляется лишь после выбора языка).
        people = f"""
            WITH ids AS (
                SELECT tg_id, MIN(t) AS first_seen FROM (
                    SELECT tg_id, created_at AS t FROM users
                    UNION ALL SELECT tg_id, created_at AS t FROM funnel_events WHERE event='start'
                ) GROUP BY tg_id
            ), p AS (SELECT tg_id FROM ids WHERE datetime(first_seen) >= {since})
        """
        where = f"u.tg_id IN (SELECT tg_id FROM ids WHERE datetime(first_seen) >= {since})"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            q = people + """
                SELECT
                  COUNT(*) AS started,
                  SUM(CASE WHEN u.tg_id IS NOT NULL THEN 1 ELSE 0 END) AS lang,
                  SUM(CASE WHEN EXISTS(SELECT 1 FROM funnel_events f WHERE f.tg_id=p.tg_id AND f.event IN ('role_chosen','doc_type_chosen'))
                            OR trim(COALESCE(u.name,''))<>''
                            OR EXISTS(SELECT 1 FROM documents d WHERE d.teacher_id=p.tg_id) THEN 1 ELSE 0 END) AS role,
                  SUM(CASE WHEN trim(COALESCE(u.name,''))<>'' THEN 1 ELSE 0 END) AS profile,
                  SUM(CASE WHEN EXISTS(SELECT 1 FROM funnel_events f WHERE f.tg_id=p.tg_id AND f.event='doc_type_chosen')
                            OR EXISTS(SELECT 1 FROM documents d WHERE d.teacher_id=p.tg_id) THEN 1 ELSE 0 END) AS chose_doc,
                  SUM(CASE WHEN (SELECT COUNT(*) FROM documents d WHERE d.teacher_id=p.tg_id) >= 1 THEN 1 ELSE 0 END) AS doc1,
                  SUM(CASE WHEN (SELECT COUNT(*) FROM documents d WHERE d.teacher_id=p.tg_id) >= 2 THEN 1 ELSE 0 END) AS doc2,
                  SUM(CASE WHEN COALESCE(u.subscribed,0)=1 THEN 1 ELSE 0 END) AS paid
                FROM p LEFT JOIN users u ON u.tg_id=p.tg_id"""
            async with db.execute(q) as cur:
                row = dict(await cur.fetchone())
            async with db.execute(
                people + f"""SELECT COALESCE(u.funnel_step,'') AS step, COUNT(*) AS c FROM users u
                    WHERE {where} AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.teacher_id=u.tg_id)
                    GROUP BY step ORDER BY c DESC LIMIT 8"""
            ) as cur:
                row["stuck"] = [dict(r) for r in await cur.fetchall()]
            async with db.execute(
                people + """SELECT f.event, COUNT(DISTINCT f.tg_id) AS c FROM funnel_events f
                    WHERE f.tg_id IN (SELECT tg_id FROM p) AND f.event IN ('generation_failed','paywall_shown') GROUP BY f.event"""
            ) as cur:
                row["problems"] = {r["event"]: r["c"] for r in await cur.fetchall()}
            async with db.execute(
                "SELECT reason, COUNT(*) AS c FROM user_feedback GROUP BY reason ORDER BY c DESC"
            ) as cur:
                row["feedback"] = [dict(r) for r in await cur.fetchall()]
            async with db.execute(
                "SELECT tg_id, reason, text, created_at FROM user_feedback WHERE trim(COALESCE(text,''))<>'' "
                "ORDER BY id DESC LIMIT 5"
            ) as cur:
                row["feedback_texts"] = [dict(r) for r in await cur.fetchall()]
        return {k: (v or 0) if not isinstance(v, (list, dict)) else v for k, v in row.items()}

    async def get_winback_candidates(self, limit: int = 1000) -> list[dict]:
        """Пришли больше суток назад, не создали ни одного документа, ещё не спрашивали."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM users u
                   WHERE datetime(u.created_at) <= datetime('now', '-1 day')
                     -- только недавние: фоновые задачи раньше не запускались, и без этого
                     -- при включении напоминание ушло бы всем, кто зарегистрировался месяцы назад
                     AND datetime(u.created_at) >= datetime('now', '-10 days')
                     AND COALESCE(u.notifications_enabled, 1)=1
                     AND u.winback_sent_at IS NULL
                     AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.teacher_id=u.tg_id)
                   ORDER BY u.created_at LIMIT ?""",
                (max(1, int(limit)),),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def mark_winback_sent(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET winback_sent_at=? WHERE tg_id=?", (datetime.now().isoformat(), tg_id))
            await db.commit()

    async def save_feedback(self, tg_id: int, reason: str, text: str = "") -> bool:
        """Сохраняет ответ. Возвращает True, если это первый ответ (за него бонус)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO user_feedback (tg_id, reason, text) VALUES (?, ?, ?)",
                (tg_id, (reason or "")[:60], (text or "")[:2000]),
            )
            cur = await db.execute(
                "UPDATE users SET winback_answered=1, bonus_docs=COALESCE(bonus_docs,0)+2 "
                "WHERE tg_id=? AND COALESCE(winback_answered,0)=0",
                (tg_id,),
            )
            first = cur.rowcount > 0
            await db.commit()
            return first

    async def get_funnel_event_counts(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT event, doc_type, COUNT(*) AS count FROM funnel_events "
                "GROUP BY event, doc_type ORDER BY event, count DESC"
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_users_due_first_doc_reminder(self) -> list[dict]:
        """Users at least 24 hours old, with no generated document or prior reminder."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT u.* FROM users AS u
                   WHERE datetime(u.created_at) <= datetime('now', '-1 day')
                     AND COALESCE(u.notifications_enabled, 1)=1
                     AND u.first_doc_reminder_sent_at IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM documents AS d WHERE d.teacher_id=u.tg_id
                     )
                   ORDER BY u.created_at"""
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def mark_first_doc_reminder_sent(self, tg_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE users SET first_doc_reminder_sent_at=?
                   WHERE tg_id=? AND first_doc_reminder_sent_at IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM documents WHERE teacher_id=?
                     )""",
                (datetime.now().isoformat(), tg_id, tg_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ===== SCHEDULE (Расписание / режим дня) =====
    async def save_schedule(self, tg_id: int, schedule_data: Any):
        if isinstance(schedule_data, (dict, list)):
            schedule_str = json.dumps(schedule_data, ensure_ascii=False)
        else:
            schedule_str = str(schedule_data) if schedule_data is not None else "[]"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO schedules (tg_id, schedule_data)
                VALUES (?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET schedule_data=excluded.schedule_data, updated_at=CURRENT_TIMESTAMP
            """, (tg_id, schedule_str))
            await db.commit()

    async def get_schedule(self, tg_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT schedule_data FROM schedules WHERE tg_id=?", (tg_id,)) as cur:
                row = await cur.fetchone()
                return normalize_schedule_json(row["schedule_data"]) if row else None

    async def get_users_with_schedules(self):
        """Возвращает пользователей, у которых сохранено расписание и включены уведомления."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT u.*, s.schedule_data 
                   FROM users u 
                   JOIN schedules s ON u.tg_id = s.tg_id
                   WHERE u.name IS NOT NULL
                     AND (u.notifications_enabled IS NULL OR u.notifications_enabled = 1)"""
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # ===== AGENT MEMORY (Контекст агента) =====
    async def save_agent_context(self, tg_id: int, context_data: Any):
        if isinstance(context_data, (dict, list)):
            ctx_str = json.dumps(context_data, ensure_ascii=False)
        else:
            ctx_str = str(context_data) if context_data is not None else "{}"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO agent_memory (tg_id, context_data)
                VALUES (?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET context_data=excluded.context_data, updated_at=CURRENT_TIMESTAMP
            """, (tg_id, ctx_str))
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
            # Keep one active version per document type and language; older rows
            # stay available in admin history as archived versions.
            await db.execute(
                "UPDATE samples SET is_active=0 WHERE doc_type=? AND lang=? AND is_active=1",
                (doc_type, lang),
            )
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

FREE_BASE_LIMIT = 3

_DAY_ALIASES = {
    "пн": "Понедельник", "понедельник": "Понедельник", "mon": "Понедельник", "monday": "Понедельник", "дүйсенбі": "Понедельник",
    "вт": "Вторник", "вторник": "Вторник", "tue": "Вторник", "tuesday": "Вторник", "сейсенбі": "Вторник",
    "ср": "Среда", "среда": "Среда", "wed": "Среда", "wednesday": "Среда", "сәрсенбі": "Среда",
    "чт": "Четверг", "четверг": "Четверг", "thu": "Четверг", "thursday": "Четверг", "бейсенбі": "Четверг",
    "пт": "Пятница", "пятница": "Пятница", "fri": "Пятница", "friday": "Пятница", "жұма": "Пятница",
    "сб": "Суббота", "суббота": "Суббота", "sat": "Суббота", "saturday": "Суббота", "сенбі": "Суббота",
    "вс": "Воскресенье", "воскресенье": "Воскресенье", "sun": "Воскресенье", "sunday": "Воскресенье", "жексенбі": "Воскресенье",
}


def normalize_schedule_json(raw):
    """Расписание всегда отдаётся как JSON-объект {"Понедельник": [ {...}, ... ]}.
    В базе встречаются старые записи-списки ([{"day": "Пн", ...}]) — раньше они
    роняли чат и напоминания (AttributeError: 'list' object has no attribute 'get')."""
    if not raw:
        return raw
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return "{}"
    if isinstance(data, dict):
        return json.dumps(_clean_schedule_nulls(data), ensure_ascii=False)
    out = {}
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            day = str(entry.get("day") or entry.get("weekday") or entry.get("день") or "").strip()
            day = _DAY_ALIASES.get(day.lower().rstrip("."), day)
            if day:
                out.setdefault(day, []).append({k: v for k, v in entry.items() if k not in ("day", "weekday", "день")})
    return json.dumps(_clean_schedule_nulls(out), ensure_ascii=False)


def normalize_class_name(value) -> str:
    """«7а», «7 А», «7-а», «7"А"» → «7 «А»», как в остальной базе: иначе один класс
    распадался на два и ученик не находился при выборе класса."""
    import re as _re
    text = str(value or "").strip()
    m = _re.fullmatch(r"(\d{1,2})\s*[-–\s]?\s*[«\"']?\s*([A-Za-zА-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ])\s*[»\"']?(?:\s*класс|\s*сынып)?", text, _re.I)
    return f"{m.group(1)} «{m.group(2).upper()}»" if m else text


def _clean_schedule_nulls(data: dict) -> dict:
    """null/«None» в полях урока → пустая строка: иначе учитель видел «None — 8Б Алгебра»."""
    for lessons in data.values():
        if isinstance(lessons, list):
            for l in lessons:
                if isinstance(l, dict):
                    for k, v in list(l.items()):
                        if v is None or (isinstance(v, str) and v.strip().lower() in ("none", "null")):
                            l[k] = ""
    return data


def format_lesson(l: dict, lang: str = "ru") -> str:
    """Строка урока для показа: время, а если его нет — номер урока."""
    time = str(l.get("time") or "").strip()
    num = str(l.get("lesson") or l.get("number") or "").strip()
    if not time and num:
        time = f"{num} урок" if lang == "ru" else f"{num}-сабақ"
    head = f"{time} — " if time else ""
    return (head + f"{l.get('class') or ''} {l.get('subject') or ''}").rstrip()


def free_limit_for(user: dict) -> int:
    """Сколько всего бесплатных документов доступно пользователю
    (база + бонусы за приглашённых друзей)."""
    if not user:
        return FREE_BASE_LIMIT
    return FREE_BASE_LIMIT + (user.get("bonus_docs") or 0)
