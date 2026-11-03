import mini_app
import asyncio
import hashlib
import hmac
import io
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from security import create_auth_token, verify_auth_token
from security import verify_telegram_init_data
import handlers.kundelik_api as kundelik_api


def test_auth_token_is_signed_and_rejects_tampering():
    token = create_auth_token(123456)

    assert verify_auth_token(token) == 123456
    assert verify_auth_token(token + "x") is None


def test_schema_failure_is_propagated_and_not_marked_initialized(monkeypatch):
    monkeypatch.setattr(mini_app, "_schema_initialized", False)

    class FailingConnection:
        def executescript(self, _script):
            raise mini_app.sqlite3.OperationalError("disk I/O error")

        def commit(self):
            raise AssertionError("commit must not run after schema creation fails")

    try:
        mini_app.ensure_schema(FailingConnection())
    except mini_app.sqlite3.OperationalError as exc:
        assert "disk I/O error" in str(exc)
    else:
        raise AssertionError("schema initialization failure must be propagated")

    assert mini_app._schema_initialized is False


def test_browser_login_token_can_only_be_consumed_once(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "web-auth.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    client = mini_app.app.test_client()
    token = create_auth_token(123456)

    first_login = client.get(f"/auth?token={token}")
    replay = client.get(f"/auth?token={token}")

    assert first_login.status_code == 302
    assert replay.status_code == 401


def _signed_init_data(bot_token, auth_date):
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": 246810}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_init_data_rejects_expired_auth_date():
    bot_token = "test-bot-token"
    current = int(time.time())
    fresh = _signed_init_data(bot_token, current)
    expired = _signed_init_data(bot_token, current - 86401)

    assert verify_telegram_init_data(fresh, bot_token) == {"id": 246810}
    assert verify_telegram_init_data(expired, bot_token) is None


def test_profile_page_does_not_disable_mobile_zoom():
    response = mini_app.app.test_client().get("/")

    assert response.status_code == 200
    assert b"user-scalable=no" not in response.data
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert b"#authRequiredView { grid-column:1 / -1" in response.data


def test_query_user_id_cannot_authenticate(monkeypatch):
    monkeypatch.setattr(mini_app, "verify_auth_token", lambda token: None)
    client = mini_app.app.test_client()

    response = client.get("/api/me?tg_id=123456")

    assert response.status_code == 401


def test_authenticated_user_cannot_read_another_profile(monkeypatch):
    monkeypatch.setattr(mini_app, "verify_auth_token", lambda token: None)
    monkeypatch.setitem(mini_app.app.config, "SESSION_COOKIE_SECURE", False)
    client = mini_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 123456

    response = client.get("/api/profile/654321")

    assert response.status_code == 403


def test_student_creation_rejects_invalid_absence_count(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "students.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    response = client.post("/api/students", json={"name": "Test", "class_name": "5A", "absences": "many"})

    assert response.status_code == 400
    assert response.get_json()["error"]


def test_student_creation_rejects_invalid_text_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "invalid-students.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    invalid_payloads = [
        {"name": "Student", "class_name": ""},
        {"name": 123, "class_name": "5A"},
        {"name": "Student", "class_name": ["5A"]},
        {"name": "S" * 201, "class_name": "5A"},
    ]
    for payload in invalid_payloads:
        response = client.post("/api/students", json=payload)
        assert response.status_code == 400

    with mini_app.conn() as db:
        assert db.execute("SELECT COUNT(*) FROM students WHERE teacher_id=?", (123456,)).fetchone()[0] == 0


def test_student_delete_does_not_remove_another_users_record(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "student-owner.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    with mini_app.conn() as db:
        cursor = db.execute(
            "INSERT INTO students (teacher_id, name, class_name) VALUES (?, ?, ?)",
            (654321, "Private student", "5A"),
        )
        student_id = cursor.lastrowid
        own_id = db.execute(
            "INSERT INTO students (teacher_id, name, class_name) VALUES (?, ?, ?)",
            (123456, "Owned student", "5B"),
        ).lastrowid
        db.commit()
    client = mini_app.app.test_client()

    response = client.post(f"/api/students/{student_id}/delete")
    own_response = client.post(f"/api/students/{own_id}/delete")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Запись не найдена"
    assert own_response.status_code == 200
    assert own_response.get_json()["ok"] is True
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM students WHERE id=?", (student_id,)).fetchone()
        assert db.execute("SELECT 1 FROM students WHERE id=?", (own_id,)).fetchone() is None


def test_student_update_is_available_to_owner_and_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "student-update.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    with mini_app.conn() as db:
        own_id = db.execute(
            "INSERT INTO students (teacher_id, name, class_name) VALUES (?, ?, ?)",
            (123456, "A", "5A"),
        ).lastrowid
        other_id = db.execute(
            "INSERT INTO students (teacher_id, name, class_name) VALUES (?, ?, ?)",
            (654321, "B", "6B"),
        ).lastrowid
        db.commit()
    client = mini_app.app.test_client()
    payload = {"name": "Updated", "class_name": "7C", "behavior": "good", "absences": 2, "grades": "Math", "parents": "Parent"}

    own_response = client.put(f"/api/students/{own_id}", json=payload)
    foreign_response = client.put(f"/api/students/{other_id}", json=payload)

    assert own_response.status_code == 200
    assert own_response.get_json()["student"]["name"] == "Updated"
    assert foreign_response.status_code == 404
    with mini_app.conn() as db:
        assert db.execute("SELECT name FROM students WHERE id=?", (other_id,)).fetchone()["name"] == "B"


def test_profile_and_schedule_writes_persist_for_authenticated_user(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "profile-schedule.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role) VALUES (?, 'teacher')", (123456,))
        db.commit()
    client = mini_app.app.test_client()

    profile_response = client.post("/api/profile", json={"name": "Учитель", "school": "Школа 7"})
    schedule_response = client.post("/api/schedule/lesson", json={
        "day": "Понедельник", "time": "09:00", "subject": "Математика", "class": "5А"
    })

    assert profile_response.status_code == 200
    assert profile_response.get_json()["user"]["name"] == "Учитель"
    assert schedule_response.status_code == 200
    assert schedule_response.get_json()["schedule"]["Понедельник"] == [
        {"time": "09:00", "subject": "Математика", "class": "5А"}
    ]


def test_schedule_lesson_validates_fields_and_avoids_duplicate_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "lesson-validation.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    invalid = client.post("/api/schedule/lesson", json={
        "day": ["Понедельник"], "time": "09:00", "subject": "Математика", "class": "5А"
    })
    payload = {"day": "Понедельник", "time": "09:00", "subject": "Математика", "class": "5А"}
    first = client.post("/api/schedule/lesson", json=payload)
    duplicate = client.post("/api/schedule/lesson", json=payload)

    assert invalid.status_code == 400
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.get_json()["duplicate"] is True
    assert len(duplicate.get_json()["schedule"]["Понедельник"]) == 1


def test_memory_add_and_clear_persist_for_authenticated_user(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    add_response = client.post("/api/memory/add", json={"note": "Use short explanations"})
    assert add_response.status_code == 200
    assert add_response.get_json()["context"]["user_preferences"][0]["text"] == "Use short explanations"
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM agent_memory WHERE tg_id=?", (123456,)).fetchone()

    clear_response = client.post("/api/memory/clear")
    assert clear_response.status_code == 200
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM agent_memory WHERE tg_id=?", (123456,)).fetchone() is None


def test_kundelik_connect_never_creates_fake_student_records(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "kundelik-connect.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})

    class FakeKundelikClient:
        def __init__(self, token, provider, user_info=None):
            self.provider = provider

        async def get_profile(self):
            return {"ok": True, "data": {"schools": [{"id": 5, "name": "Школа 5"}], "person_id": 7}}

        async def get_classes(self):
            return [{"id": 9, "name": "5А"}]

        async def get_schedule(self):
            return [{"day": "Понедельник", "time": "09:00", "subject": "Математика", "class": "5А"}]

    monkeypatch.setattr("handlers.kundelik_api.KundelikClient", FakeKundelikClient)
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role, tier) VALUES (?, 'teacher', 'max')", (123456,))
        db.commit()
    client = mini_app.app.test_client()

    response = client.post("/api/kundelik/connect", json={"token": "real-token", "provider": "kundelik"})

    assert response.status_code == 200
    assert response.get_json()["classes_count"] == 1
    with mini_app.conn() as db:
        assert db.execute("SELECT COUNT(*) FROM students WHERE teacher_id=?", (123456,)).fetchone()[0] == 0
        saved_schedule = db.execute("SELECT schedule_data FROM schedules WHERE tg_id=?", (123456,)).fetchone()
        assert saved_schedule
        assert '"class": "5А"' in saved_schedule["schedule_data"]


def test_live_kundelik_failures_do_not_return_mock_data(monkeypatch):
    class FakeResponse:
        status = 503
        headers = {"Content-Type": "text/html"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {"error": "unavailable"}

        async def text(self):
            return "unavailable"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return FakeResponse()

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(kundelik_api, "aiohttp", SimpleNamespace(
        ClientSession=FakeSession, ClientTimeout=lambda **kwargs: None
    ))
    client = kundelik_api.KundelikClient("valid-looking-live-token-123", "kundelik")

    async def check_live_calls():
        profile = await client.get_profile()
        classes = await client.get_classes()
        students = await client.get_students_for_class(1)
        schedule = await client.get_schedule()
        mark = await client.post_mark(1, "Student", "5A", 8)
        return profile, classes, students, schedule, mark

    profile, classes, students, schedule, mark = asyncio.run(check_live_calls())

    assert client.is_mock is False
    assert profile["ok"] is False
    assert classes == []
    assert students == []
    assert schedule == []
    assert mark["ok"] is False


def test_failed_kundelik_login_never_creates_synthetic_session_token(monkeypatch):
    monkeypatch.setattr(kundelik_api, "aiohttp", None)

    result = asyncio.run(kundelik_api.KundelikClient.login_with_credentials(
        "teacher-login", "password123", "kundelik"
    ))

    assert result["ok"] is False
    assert "token" not in result


def test_kundelik_connect_rejects_unverified_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "kundelik-login.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})

    async def reject_login(cls, login, password, provider, user_info=None):
        return {"ok": False, "error": "Login could not be verified"}

    monkeypatch.setattr(kundelik_api.KundelikClient, "login_with_credentials", classmethod(reject_login))
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role, tier) VALUES (?, 'teacher', 'max')", (123456,))
        db.commit()
    client = mini_app.app.test_client()

    response = client.post("/api/kundelik/connect", json={
        "login": "teacher", "password": "wrong-password", "provider": "kundelik"
    })

    assert response.status_code == 400
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM kundelik_integrations WHERE tg_id=?", (123456,)).fetchone() is None


def test_kundelik_grade_uses_only_a_student_from_live_roster(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "kundelik-roster.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})

    class FakeKundelikClient:
        posted = []

        def __init__(self, token, provider, user_info=None):
            pass

        async def get_classes(self):
            return [{"id": 77, "name": "5А"}]

        async def get_students_for_class(self, class_id):
            return [{"id": 9988, "name": "Әлия", "class_name": "5А"}]

        async def post_mark(self, student_id, student_name, class_name, mark_val, mark_type, descriptor):
            self.posted.append((student_id, student_name, class_name, mark_val))
            return {"ok": True}

    FakeKundelikClient.posted = []
    monkeypatch.setattr(kundelik_api, "KundelikClient", FakeKundelikClient)
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role, tier) VALUES (?, 'teacher', 'max')", (123456,))
        db.execute(
            "INSERT INTO kundelik_integrations (tg_id, provider, token) VALUES (?, ?, ?)",
            (123456, "kundelik", "real-token"),
        )
        db.commit()
    client = mini_app.app.test_client()

    status = client.get("/api/kundelik/status")
    assert "token" not in status.get_json()["integration"]
    accepted = client.post("/api/kundelik/grade", json={
        "student_id": "9988", "student_name": "Attacker supplied name", "class_name": "5А", "mark": 8
    })
    rejected = client.post("/api/kundelik/grade", json={
        "student_id": "1234", "student_name": "Fake", "class_name": "5А", "mark": 8
    })

    assert status.get_json()["students"] == [{"id": "9988", "name": "Әлия", "class_name": "5А"}]
    assert accepted.status_code == 200
    assert FakeKundelikClient.posted == [(9988, "Әлия", "5А", 8)]
    assert rejected.status_code == 400


def test_receipt_upload_rejects_unknown_tier_and_wrong_file_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "receipt-input.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    bad_tier = client.post("/api/payment/verify-receipt", data={
        "tier": "admin", "receipt": (io.BytesIO(b"%PDF-1.4\ncheck"), "receipt.pdf")
    })
    wrong_signature = client.post("/api/payment/verify-receipt", data={
        "tier": "pro", "receipt": (io.BytesIO(b"not a PDF"), "receipt.pdf")
    })

    assert bad_tier.status_code == 400
    assert wrong_signature.status_code == 400


def test_receipt_rejects_hashes_written_by_previous_short_hash_format(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "legacy-receipt.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    receipt_bytes = b"%PDF-1.4\nold-check"
    old_hash = hashlib.sha256(receipt_bytes).hexdigest()[:16]
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role) VALUES (?, 'teacher')", (123456,))
        db.execute("INSERT INTO receipts (hash, tg_id, tier, amount) VALUES (?, ?, ?, ?)",
                   (old_hash, 654321, "pro", 4990))
        db.commit()

    response = mini_app.app.test_client().post("/api/payment/verify-receipt", data={
        "tier": "pro", "receipt": (io.BytesIO(receipt_bytes), "receipt.pdf")
    })

    assert response.status_code == 400
    assert "уже был использован" in response.get_json()["error"]


def test_receipt_verification_requires_ai_amount_to_match_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "receipt-amount.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    monkeypatch.setattr(mini_app, "ANTHROPIC_API_KEY", "test-key")
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role) VALUES (?, 'teacher')", (123456,))
        db.commit()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(content=[
                SimpleNamespace(text=json.dumps({"valid": True, "amount": 999, "reason": ""}))
            ]))

    monkeypatch.setattr(mini_app.anthropic, "Anthropic", FakeClient)
    response = mini_app.app.test_client().post("/api/payment/verify-receipt", data={
        "tier": "pro", "receipt": (io.BytesIO(b"%PDF-1.4\ncheck"), "receipt.pdf")
    })

    assert response.status_code == 400
    with mini_app.conn() as db:
        user = db.execute("SELECT subscribed FROM users WHERE tg_id=?", (123456,)).fetchone()
        assert user["subscribed"] == 0
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_receipt_cannot_activate_two_accounts_after_concurrent_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "receipt-race.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    monkeypatch.setattr(mini_app, "ANTHROPIC_API_KEY", "test-key")
    receipt_bytes = b"%PDF-1.4\nshared-check"
    receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role) VALUES (?, 'teacher')", (123456,))
        db.commit()

    class FakeClient:
        def __init__(self, api_key):
            def verify_while_claiming(**kwargs):
                with mini_app.conn() as db:
                    db.execute("INSERT INTO receipts (hash, tg_id, tier, amount) VALUES (?, ?, ?, ?)",
                               (receipt_hash, 654321, "pro", 4990))
                    db.commit()
                return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"valid": True, "amount": 4990}))])
            self.messages = SimpleNamespace(create=verify_while_claiming)

    monkeypatch.setattr(mini_app.anthropic, "Anthropic", FakeClient)
    response = mini_app.app.test_client().post("/api/payment/verify-receipt", data={
        "tier": "pro", "receipt": (io.BytesIO(receipt_bytes), "receipt.pdf")
    })

    assert response.status_code == 400
    with mini_app.conn() as db:
        assert db.execute("SELECT subscribed FROM users WHERE tg_id=?", (123456,)).fetchone()[0] == 0


def test_schedule_upload_rejects_unsupported_oversized_and_mislabeled_files(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "schedule-upload.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    client = mini_app.app.test_client()

    unsupported = client.post("/api/schedule/upload", data={
        "file": (io.BytesIO(b"text"), "schedule.txt")
    })
    mislabeled = client.post("/api/schedule/upload", data={
        "file": (io.BytesIO(b"not really a PDF"), "schedule.pdf")
    })
    oversized = client.post("/api/schedule/upload", data={
        "file": (io.BytesIO(b"%PDF-" + b"x" * (mini_app.MAX_SCHEDULE_UPLOAD_BYTES + 1)), "large.pdf")
    })

    assert unsupported.status_code == 400
    assert mislabeled.status_code == 400
    assert oversized.status_code == 413


def test_schedule_text_limits_input_and_rejects_invalid_ai_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "schedule-text.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    monkeypatch.setattr(mini_app, "ANTHROPIC_API_KEY", "test-key")
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role) VALUES (?, 'teacher')", (123456,))
        db.commit()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(content=[
                SimpleNamespace(text="[]")
            ]))

    monkeypatch.setattr(mini_app.anthropic, "Anthropic", FakeClient)
    client = mini_app.app.test_client()
    oversized = client.post("/api/schedule/parse-text", json={"text": "x" * (mini_app.MAX_SCHEDULE_TEXT_CHARS + 1)})
    malformed = client.post("/api/schedule/parse-text", json={"text": "Понедельник, математика"})

    assert oversized.status_code == 413
    assert malformed.status_code == 502
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM schedules WHERE tg_id=?", (123456,)).fetchone() is None


def test_kindergarten_account_cannot_use_school_kundelik_endpoints(monkeypatch, tmp_path):
    monkeypatch.setattr(mini_app, "DB_PATH", str(tmp_path / "kindergarten.db"))
    monkeypatch.setattr(mini_app, "_schema_initialized", False)
    monkeypatch.setattr(mini_app, "telegram_user", lambda: {"id": 123456})
    with mini_app.conn() as db:
        db.execute("INSERT INTO users (tg_id, role, tier) VALUES (?, 'kindergarten', 'max')", (123456,))
        db.commit()
    client = mini_app.app.test_client()

    for method, path, payload in (
        ("get", "/api/kundelik/status", None),
        ("post", "/api/kundelik/connect", {"token": "unused"}),
        ("post", "/api/kundelik/grade", {"mark": 9}),
        ("post", "/api/kundelik/disconnect", {}),
    ):
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 403
        assert "только профилям школы" in response.get_json()["error"]
