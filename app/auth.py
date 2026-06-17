from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, Response, status


SESSION_COOKIE_NAME = "coin_bot_admin_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    session_secret: str
    required: bool
    configured: bool
    app_env: str

    @classmethod
    def from_env(cls) -> "AuthConfig":
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
        session_secret = os.getenv("SESSION_SECRET", "").strip()
        required = app_env == "production" or bool(password_hash)
        return cls(
            username=os.getenv("ADMIN_USERNAME", "admin").strip() or "admin",
            password_hash=password_hash,
            session_secret=session_secret,
            required=required,
            configured=bool(password_hash and session_secret),
            app_env=app_env,
        )


def auth_status(request: Request) -> dict[str, Any]:
    config = AuthConfig.from_env()
    authenticated = True if not config.required else get_session_user(request) is not None
    return {
        "auth_required": config.required,
        "auth_configured": config.configured,
        "authenticated": authenticated,
        "username": config.username if authenticated else None,
        "app_env": config.app_env,
    }


def login_admin(username: str, password: str, response: Response) -> dict[str, Any]:
    config = AuthConfig.from_env()
    if not config.required:
        return {"ok": True, **auth_status_for_user(config.username)}
    if not config.configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="관리자 인증 설정이 필요합니다.")
    if not secrets.compare_digest(username, config.username) or not verify_password(password, config.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(config.username),
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return {"ok": True, **auth_status_for_user(config.username)}


def logout_admin(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "authenticated": False}


def require_admin_session(request: Request) -> None:
    config = AuthConfig.from_env()
    if not config.required:
        return
    if not config.configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="관리자 인증 설정이 필요합니다.")
    if get_session_user(request) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="관리자 로그인이 필요합니다.")


def get_session_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


def create_session_token(username: str) -> str:
    config = AuthConfig.from_env()
    if not config.session_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SESSION_SECRET 설정이 필요합니다.")
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(12),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(body, config.session_secret)
    return f"{body}.{signature}"


def verify_session_token(token: str) -> str | None:
    config = AuthConfig.from_env()
    if not config.session_secret or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    if not secrets.compare_digest(_sign(body, config.session_secret), signature):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    subject = str(payload.get("sub") or "")
    return subject if subject == config.username else None


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations_raw, salt, expected = password_hash.split("$", 3)
            iterations = int(iterations_raw)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return secrets.compare_digest(_b64url(digest), expected)
    if password_hash.startswith("sha256$"):
        expected = password_hash.split("$", 1)[1]
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(digest, expected)
    return False


def hash_password(password: str, *, salt: str | None = None, iterations: int = 240_000) -> str:
    salt = salt or secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${_b64url(digest)}"


def auth_status_for_user(username: str) -> dict[str, Any]:
    config = AuthConfig.from_env()
    return {
        "auth_required": config.required,
        "auth_configured": config.configured,
        "authenticated": True,
        "username": username,
        "app_env": config.app_env,
    }


def _sign(body: str, secret: str) -> str:
    return _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
