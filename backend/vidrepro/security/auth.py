import hashlib
import secrets
import time

import bcrypt
import jwt

from vidrepro.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def issue_token(user_id: str, ttl: int | None = None) -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + (ttl or s.jwt_ttl_seconds)},
        s.jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str) -> str:
    """Return user_id or raise jwt exceptions."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    return payload["sub"]


def new_api_key() -> tuple[str, str]:
    """Return (plaintext key shown once, sha256 hash stored)."""
    key = "vr_live_" + secrets.token_urlsafe(32)
    return key, hashlib.sha256(key.encode()).hexdigest()


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
