"""Sesión del emisor en cookie firmada.

§3.1: cookie `HttpOnly` + `Secure` + `SameSite=Lax`. No hay tabla de sesiones ni
Redis a propósito — sería infraestructura pagada (§13) para un dato que cabe
firmado en la propia cookie.
"""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from receipt.config import get_settings

_SALT = "receipt.session.v1"


@dataclass(frozen=True)
class SessionData:
    user_id: int
    account_id: int
    csrf_token: str

    def to_payload(self) -> dict[str, object]:
        return {"u": self.user_id, "a": self.account_id, "c": self.csrf_token}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SessionData":
        return cls(
            user_id=int(payload["u"]),
            account_id=int(payload["a"]),
            csrf_token=str(payload["c"]),
        )


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SALT)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def issue_session(response: Response, *, user_id: int, account_id: int) -> SessionData:
    settings = get_settings()
    data = SessionData(user_id=user_id, account_id=account_id, csrf_token=new_csrf_token())
    response.set_cookie(
        settings.session_cookie_name,
        _serializer().dumps(data.to_payload()),
        max_age=settings.session_max_age_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookies_require_https,
        samesite="lax",
        path="/",
    )
    return data


def read_session(request: Request) -> SessionData | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw, max_age=settings.session_max_age_days * 24 * 60 * 60)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return SessionData.from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return None


def clear_session(response: Response) -> None:
    response.delete_cookie(get_settings().session_cookie_name, path="/")
