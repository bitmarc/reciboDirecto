"""Protección CSRF para los formularios autenticados.

`SameSite=Lax` ya bloquea el envío de cookies en POST desde otro sitio; este
token es la segunda capa. Las rutas públicas de recibo (§7.3) no llevan sesión
ni formularios, así que quedan fuera.
"""

import hmac

from fastapi import HTTPException, status
from starlette.requests import Request

from receipt.security.sessions import read_session

CSRF_FIELD_NAME = "csrf_token"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


async def verify_csrf(request: Request) -> None:
    """Dependencia para las rutas que mutan estado bajo sesión."""
    if request.method in _SAFE_METHODS:
        return

    session = read_session(request)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión no iniciada")

    submitted = request.headers.get("x-csrf-token")
    if submitted is None:
        form = await request.form()
        raw = form.get(CSRF_FIELD_NAME)
        submitted = raw if isinstance(raw, str) else None

    if submitted is None or not hmac.compare_digest(submitted, session.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token de formulario inválido")
