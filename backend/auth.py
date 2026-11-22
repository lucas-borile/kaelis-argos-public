from __future__ import annotations

import secrets
import os
from typing import Annotated, Any

from fastapi import Header, HTTPException, Request, WebSocket, status

from db import create_app_session, get_app_session, get_app_user, purge_expired_app_sessions, revoke_app_session, revoke_app_user_sessions, verify_password

ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
DEFAULT_SESSION_TTL_SECONDS = 28_800
MIN_SESSION_TTL_SECONDS = 300
try:
    SESSION_TTL_SECONDS = max(MIN_SESSION_TTL_SECONDS, int(os.getenv("ARGOS_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS))))
except ValueError:
    SESSION_TTL_SECONDS = DEFAULT_SESSION_TTL_SECONDS


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = get_app_user(username, include_hash=True)
    if not user or not user.get("active"):
        return None
    if not verify_password(password, str(user.get("password_hash", ""))):
        return None
    return {
        "username": username,
        "role": user.get("role", "viewer"),
        "name": user.get("name", username),
    }


def create_session(user: dict[str, Any]) -> dict[str, Any]:
    purge_expired_app_sessions()
    revoke_app_user_sessions(str(user.get("username", "")))
    token = secrets.token_urlsafe(32)
    return create_app_session(token, user, SESSION_TTL_SECONDS)


def get_session(token: str | None) -> dict[str, Any] | None:
    return get_app_session(token)


def revoke_session(token: str | None) -> bool:
    return revoke_app_session(token)


SESSION_COOKIE_NAME = "argos_session"


def current_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    bearer = parse_bearer_token(authorization) or parse_bearer_token(request.headers.get("authorization"))
    user = get_session(request.cookies.get(SESSION_COOKIE_NAME)) or get_session(bearer)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    return user


def require_role(user: dict[str, Any], minimum_role: str) -> dict[str, Any]:
    user_level = ROLE_LEVELS.get(user.get("role", "viewer"), 0)
    minimum_level = ROLE_LEVELS[minimum_role]
    if user_level < minimum_level:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
    return user


def current_operator(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    return require_role(current_user(request, authorization), "operator")


def current_admin(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    return require_role(current_user(request, authorization), "admin")


async def websocket_user(websocket: WebSocket) -> dict[str, Any] | None:
    user = get_session(websocket.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        await websocket.close(code=1008)
        return None
    return user


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix) :].strip()
