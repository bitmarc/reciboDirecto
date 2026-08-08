"""Dependencias compartidas por las rutas privadas."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from receipt.config import get_settings
from receipt.db import get_db_session
from receipt.infra.blob import BlobStore, get_blob_store
from receipt.infra.mail import Mailer, get_mailer
from receipt.models import Account, User
from receipt.repositories import AccountScope
from receipt.security.rate_limit import RateLimiter
from receipt.security.sessions import SessionData, read_session

LOGIN_PATH = "/entrar"


class NotAuthenticated(Exception):
    """Se traduce a una redirección al login en el manejador de `main`."""


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def require_session(request: Request) -> SessionData:
    session = read_session(request)
    if session is None:
        raise NotAuthenticated
    return session


CurrentSession = Annotated[SessionData, Depends(require_session)]


async def get_scope(session: CurrentSession, db: DbSession) -> AccountScope:
    """Puerta de acceso a los datos, atada al `account_id` de la cookie.

    Ninguna ruta recibe un `account_id` por parámetro: el único que existe viene
    de la sesión firmada (§10).
    """
    return AccountScope(db, session.account_id)


Scope = Annotated[AccountScope, Depends(get_scope)]


async def get_current_user(session: CurrentSession, scope: Scope) -> User:
    user = await scope.get(User, session.user_id)
    if user is None:
        # Cookie válida contra un usuario que ya no existe.
        raise NotAuthenticated
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_account(scope: Scope, session: CurrentSession) -> Account:
    account = await scope.get(Account, session.account_id)
    if account is None:
        raise NotAuthenticated
    return account


CurrentAccount = Annotated[Account, Depends(get_current_account)]


def redirect_to_login(request: Request) -> RedirectResponse:
    target = request.url.path
    suffix = f"?next={target}" if target not in {"/", LOGIN_PATH} else ""
    return RedirectResponse(f"{LOGIN_PATH}{suffix}", status_code=status.HTTP_303_SEE_OTHER)


def not_found() -> HTTPException:
    """404 uniforme.

    Un recurso de otra cuenta y uno inexistente responden igual, para no filtrar
    la existencia de datos ajenos (AC-12, AC-13).
    """
    return HTTPException(status.HTTP_404_NOT_FOUND, "No encontrado")


Blobs = Annotated[BlobStore, Depends(get_blob_store)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]


# Un solo limitador para login y recuperación: ambos formularios permiten
# probar credenciales sin límite si no se acotan (§10).
_settings = get_settings()
auth_limiter = RateLimiter(
    max_attempts=_settings.login_max_attempts,
    window_seconds=_settings.login_window_seconds,
)


def client_ip(request: Request) -> str | None:
    """IP del cliente, respetando el proxy de App Service.

    Solo se lee `X-Forwarded-For` porque en producción la app va siempre detrás
    de un proxy de confianza; expuesta directamente, esta cabecera la falsifica
    cualquiera y el límite se evade trivialmente.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
