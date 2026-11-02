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
