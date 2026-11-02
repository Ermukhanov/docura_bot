import mini_app
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from security import create_auth_token, verify_auth_token
from security import verify_telegram_init_data


def test_auth_token_is_signed_and_rejects_tampering():
    token = create_auth_token(123456)

    assert verify_auth_token(token) == 123456
    assert verify_auth_token(token + "x") is None


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
        db.commit()
    client = mini_app.app.test_client()

    response = client.post(f"/api/students/{student_id}/delete")

    assert response.status_code == 200
    with mini_app.conn() as db:
        assert db.execute("SELECT 1 FROM students WHERE id=?", (student_id,)).fetchone()


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
