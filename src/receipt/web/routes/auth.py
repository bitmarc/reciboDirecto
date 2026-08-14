"""Registro, sesión, recuperación de contraseña y verificación (E1).

Las rutas visibles al usuario van en español (§7); las de §6 se conservan como
alias para que la superficie de API del PRD resuelva.
"""

import logging

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response

from receipt.domain.enums import AuthTokenPurpose
from receipt.infra.mail import MailDeliveryError
from receipt.models import User
from receipt.security.csrf import verify_csrf
from receipt.security.rate_limit import client_key
from receipt.security.sessions import clear_session, issue_session
from receipt.services.accounts import authenticate, normalize_email, register_account
from receipt.services.auth_tokens import (
    consume_token,
    mark_email_verified,
    reset_password,
    send_email_verification,
    send_password_reset,
)
from receipt.services.errors import DomainError, ValidationError
from receipt.web.dependencies import DbSession, MailerDep, auth_limiter, client_ip
from receipt.web.templating import render

router = APIRouter(tags=["auth"])

logger = logging.getLogger("receipt.auth")

_THROTTLED = "Demasiados intentos. Espera unos minutos antes de volver a probar."

# Respuesta única del formulario de recuperación. Decir "ese correo no existe"
# convertiría el formulario en un detector de cuentas dadas de alta.
_RESET_SENT = (
    "Si ese correo tiene una cuenta, te enviamos una liga para cambiar la "
    "contraseña. Revisa tu bandeja."
)


def _safe_next(target: str) -> str:
    """Solo rutas internas: un `next` absoluto sería un redirector abierto."""
    return target if target.startswith("/") and not target.startswith("//") else "/"


@router.get("/entrar")
async def login_form(request: Request, next: str = "/") -> Response:
    return render(request, "auth/login.html", {"next": next})


@router.post("/auth/login")
async def login(
    request: Request,
    db: DbSession,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
) -> Response:
    key = client_key("login", client_ip(request), email)
    if auth_limiter.is_blocked(key):
        return render(
            request,
            "auth/login.html",
            {"error": _THROTTLED, "email": email, "next": next},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = await authenticate(db, email=email, password=password)
    if user is None:
        auth_limiter.record_attempt(key)
        return render(
            request,
            "auth/login.html",
            {"error": "Correo o contraseña incorrectos.", "email": email, "next": next},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Quien acierta la contraseña no debe arrastrar el castigo de sus tecleos
    # previos.
    auth_limiter.reset(key)
    await db.commit()

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user_id=user.id, account_id=user.account_id)
    return response


@router.get("/registro")
async def register_form(request: Request) -> Response:
    return render(request, "auth/register.html", {})


@router.post("/auth/register")
async def register(
    request: Request,
    db: DbSession,
    mailer: MailerDep,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    phone: str = Form(...),
    default_city: str = Form("Ciudad de México"),
    occupation: str = Form(""),
) -> Response:
    try:
        user = await register_account(
            db,
            email=email,
            password=password,
            display_name=display_name,
            phone=phone,
            default_city=default_city,
            occupation=occupation,
        )
    except DomainError as exc:
        return render(
            request,
            "auth/register.html",
            {
                "error": exc.message,
                "email": email,
                "display_name": display_name,
                "phone": phone,
                "default_city": default_city,
                "occupation": occupation,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await send_email_verification(db, mailer, user)
    except MailDeliveryError:
        # El alta se completa igual. La cuenta ya existe cuando se manda el
        # correo, y `send_email_verification` documenta que la verificación no
        # bloquea el uso: tumbar un registro por un proveedor caído sería un
        # autogol. El token emitido se compromete sin problema — «Reenviar la
        # liga de confirmación» en /ajustes lo invalida y emite otro.
        logger.exception("No salió el correo de verificación de una cuenta recién creada")

    await db.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user_id=user.id, account_id=user.account_id)
    return response


@router.post("/auth/logout", dependencies=[Depends(verify_csrf)])
async def logout() -> Response:
    response = RedirectResponse("/entrar", status_code=status.HTTP_303_SEE_OTHER)
    clear_session(response)
    return response


# ---------------------------------------------------------------------------
# Recuperación de contraseña
# ---------------------------------------------------------------------------


@router.get("/recuperar")
async def forgot_form(request: Request) -> Response:
    return render(request, "auth/forgot.html", {})


@router.post("/auth/forgot-password")
async def forgot_password(
    request: Request, db: DbSession, mailer: MailerDep, email: str = Form(...)
) -> Response:
    """Responde **siempre lo mismo**, exista o no el correo."""
    key = client_key("forgot", client_ip(request), email)
    if auth_limiter.is_blocked(key):
        return render(
            request,
            "auth/forgot.html",
            {"error": _THROTTLED},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    auth_limiter.record_attempt(key)

    try:
        normalized = normalize_email(email)
    except ValidationError:
        normalized = None

    if normalized:
        user = await db.scalar(select(User).where(User.email == normalized))
        if user is not None:
            try:
                await send_password_reset(db, mailer, user)
                await db.commit()
            except MailDeliveryError:
                # Se calla **a propósito**, y cuesta. Un error de transporte
                # solo puede aparecer cuando hubo intento de envío, o sea
                # cuando la cuenta existe: mostrarlo convertiría el formulario
                # en el detector de cuentas que `_RESET_SENT` evita.
                #
                # El precio es que el usuario espera una liga que no llega, sin
                # ninguna señal. Con el dominio gestionado y sus 10 correos por
                # hora esto no es hipotético, así que **este log es el único
                # aviso que existe** y hay que mirarlo.
                logger.exception("No salió una liga de recuperación de contraseña")

    return render(request, "auth/forgot.html", {"notice": _RESET_SENT})


@router.get("/restablecer")
async def reset_form(request: Request, token: str = "") -> Response:
    return render(request, "auth/reset.html", {"token": token})


@router.post("/auth/reset-password")
async def do_reset_password(
    request: Request,
    db: DbSession,
    token: str = Form(...),
    password: str = Form(...),
) -> Response:
    user = await consume_token(db, token, AuthTokenPurpose.PASSWORD_RESET)
    if user is None:
        # Caducado, ya usado o inexistente responden igual: distinguirlos
        # ayudaría a un atacante a saber que acertó el valor.
        return render(
            request,
            "auth/reset.html",
            {
                "token": "",
                "error": "Esa liga ya no sirve. Pide una nueva.",
                "expired": True,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await reset_password(db, user, password)
    except DomainError as exc:
        return render(
            request,
            "auth/reset.html",
            {"token": token, "error": exc.message},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user_id=user.id, account_id=user.account_id)
    return response


@router.get("/verificar")
async def verify_email(request: Request, db: DbSession, token: str = "") -> Response:
    user = await consume_token(db, token, AuthTokenPurpose.EMAIL_VERIFICATION)
    if user is None:
        return render(
            request,
            "auth/verified.html",
            {"ok": False},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await mark_email_verified(db, user)
    await db.commit()
    return render(request, "auth/verified.html", {"ok": True})
