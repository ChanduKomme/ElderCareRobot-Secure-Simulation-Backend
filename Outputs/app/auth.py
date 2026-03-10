from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

JWT_ALGORITHM = "HS256"
JWT_ACTIVE_KID = os.getenv("JWT_ACTIVE_KID", "v1")
JWT_DEFAULT_KEYS = {JWT_ACTIVE_KID: os.getenv("JWT_SECRET_V1", "dev-insecure-change-me")}
if os.getenv("JWT_KEYRING_JSON"):
    JWT_KEYS = json.loads(os.getenv("JWT_KEYRING_JSON"))
else:
    JWT_KEYS = JWT_DEFAULT_KEYS
JWT_EXP_MINUTES = int(os.getenv("JWT_EXP_MINUTES", "120"))
PASSWORD_ITERATIONS = int(os.getenv("PASSWORD_ITERATIONS", "390000"))

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class RateLimitConfig:
    max_attempts: int = 8
    window_seconds: int = 60


_login_attempts: dict[str, deque[float]] = defaultdict(deque)
RATE_LIMIT_CONFIG = RateLimitConfig()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64.encode()),
            int(iterations),
        )
        return hmac.compare_digest(derived, base64.b64decode(digest_b64.encode()))
    except Exception:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MINUTES)).timestamp()),
        "kid": JWT_ACTIVE_KID,
    }
    return jwt.encode(payload, JWT_KEYS[JWT_ACTIVE_KID], algorithm=JWT_ALGORITHM, headers={"kid": JWT_ACTIVE_KID})


def decode_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid") or JWT_ACTIVE_KID
    key = JWT_KEYS.get(kid)
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown signing key")
    try:
        return jwt.decode(token, key, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


DbSession = Annotated[Session, Depends(get_db)]


def check_login_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = _login_attempts[client_ip]
    while attempts and now - attempts[0] > RATE_LIMIT_CONFIG.window_seconds:
        attempts.popleft()
    if len(attempts) >= RATE_LIMIT_CONFIG.max_attempts:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts")
    attempts.append(now)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = decode_token(credentials.credentials)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    request.state.authenticated_user = user
    request.state.audit_decision = getattr(request.state, "audit_decision", "allowed")
    request.state.audit_reason = getattr(request.state, "audit_reason", "authorized")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
