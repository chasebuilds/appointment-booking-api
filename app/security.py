from datetime import timedelta
from typing import Any

import bcrypt
import jwt

from app.models import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, role: str, secret: str, minutes: int) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        return dict(jwt.decode(token, secret, algorithms=["HS256"]))
    except jwt.PyJWTError:
        return None
