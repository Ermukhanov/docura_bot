import mini_app
from security import create_auth_token, verify_auth_token


def test_auth_token_is_signed_and_rejects_tampering():
    token = create_auth_token(123456)

    assert verify_auth_token(token) == 123456
    assert verify_auth_token(token + "x") is None


def test_query_user_id_cannot_authenticate(monkeypatch):
    monkeypatch.setattr(mini_app, "verify_auth_token", lambda token: None)
    client = mini_app.app.test_client()

    response = client.get("/api/me?tg_id=123456")

    assert response.status_code == 401


def test_authenticated_user_cannot_read_another_profile(monkeypatch):
    monkeypatch.setattr(mini_app, "verify_auth_token", lambda token: None)
    client = mini_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 123456

    response = client.get("/api/profile/654321")

    assert response.status_code == 403
