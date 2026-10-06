import os
import time
import hmac
import hashlib
import json
from urllib.parse import parse_qsl

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("TELEGRAM_TOKEN") or "docura-secure-secret-key-2026-production"

def create_auth_token(user_id: int) -> str:
    """Генерирует криптографически подписанный одноразовый токен для безопасного входа из бота в браузер."""
    ts = int(time.time())
    nonce = os.urandom(8).hex()
    payload = f"{user_id}:{ts}:{nonce}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_auth_token(token: str) -> int | None:
    """Проверяет HMAC-подпись и срок действия токена (20 минут). Возвращает user_id или None."""
    if not token or not isinstance(token, str):
        return None
    try:
        parts = token.split(":")
        if len(parts) != 4:
            return None
        user_id_str, ts_str, nonce, sig = parts
        payload = f"{user_id_str}:{ts_str}:{nonce}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        ts = int(ts_str)
        # Срок жизни токена: 20 минут (1200 сек)
        now = time.time()
        if now - ts > 1200 or now < ts - 60:
            return None
        return int(user_id_str)
    except Exception:
        return None

def verify_telegram_init_data(raw_init_data: str, bot_token: str = "") -> dict | None:
    """Криптографическая проверка подписи Telegram WebApp initData по алгоритму Telegram."""
    if not raw_init_data:
        return None
    token = bot_token or os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        return None
    try:
        data = dict(parse_qsl(raw_init_data, keep_blank_values=True))
        received_hash = data.pop("hash", "")
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_hash, received_hash):
            user_data = json.loads(data.get("user", "{}"))
            return user_data if user_data and "id" in user_data else None
    except Exception:
        pass
    return None
