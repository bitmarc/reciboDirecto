"""Tokens de un solo uso: recuperación de contraseña y verificación de correo.

Del token se guarda **solo el hash SHA-256**. Una fuga de la tabla `auth_token`
no permite usar ninguno: el valor que viaja en la liga nunca toca la base de
datos.
"""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.branding import APP_NAME
from receipt.config import get_settings
from receipt.domain.clock import now_utc
from receipt.domain.enums import AuthTokenPurpose
from receipt.infra.mail import Mailer, Message
from receipt.models import AuthToken, User
from receipt.security.passwords import MIN_PASSWORD_LENGTH, hash_password
from receipt.services.errors import ValidationError

PASSWORD_RESET_TTL = timedelta(hours=1)
EMAIL_VERIFICATION_TTL = timedelta(days=7)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(
    session: AsyncSession,
    user: User,
    purpose: AuthTokenPurpose,
    ttl: timedelta,
) -> str:
    """Crea un token y devuelve su valor en claro, que solo existe aquí."""
    token = secrets.token_urlsafe(32)
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_digest(token),
            expires_at=now_utc() + ttl,
        )
    )
    await session.flush()
    return token


async def consume_token(
    session: AsyncSession, token: str, purpose: AuthTokenPurpose
) -> User | None:
    """Valida y marca como usado. `None` si no sirve, sin decir por qué.

    Un token caducado, ya usado o inexistente responden igual: distinguirlos
    ayudaría a un atacante a saber que acertó el valor.
    """
    row = await session.scalar(
        select(AuthToken)
        .where(AuthToken.token_hash == _digest(token))
        .where(AuthToken.purpose == purpose)
        .where(AuthToken.used_at.is_(None))
        .where(AuthToken.expires_at > now_utc())
    )
    if row is None:
        return None

    row.used_at = now_utc()
    user = await session.get(User, row.user_id)
    await session.flush()
    return user


async def invalidate_tokens(session: AsyncSession, user: User, purpose: AuthTokenPurpose) -> None:
    """Anula los tokens vivos de un propósito.

    Tras restablecer la contraseña, cualquier liga de recuperación pendiente
    —incluida una que un atacante hubiera pedido antes— deja de servir.
    """
    await session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id)
        .where(AuthToken.purpose == purpose)
        .where(AuthToken.used_at.is_(None))
        .values(used_at=now_utc())
    )


async def send_password_reset(session: AsyncSession, mailer: Mailer, user: User) -> None:
    await invalidate_tokens(session, user, AuthTokenPurpose.PASSWORD_RESET)
    token = await issue_token(session, user, AuthTokenPurpose.PASSWORD_RESET, PASSWORD_RESET_TTL)
    link = f"{get_settings().public_base_url}/restablecer?token={token}"
    await mailer.send(
        Message(
            to=user.email,
            subject=f"Restablece tu contraseña de {APP_NAME}",
            body=(
                "Recibimos una solicitud para cambiar tu contraseña.\n\n"
                f"Entra aquí para elegir una nueva: {link}\n\n"
                "La liga sirve una sola vez y vence en una hora.\n"
                "Si no fuiste tú, ignora este mensaje: tu contraseña no cambió."
            ),
        )
    )


async def send_email_verification(session: AsyncSession, mailer: Mailer, user: User) -> None:
    """Verificación de correo: se envía, pero **no bloquea el uso**.

    Ninguna AC la exige, y dejar al primer usuario real fuera de su cuenta por
    un correo caído en spam sería un autogol.
    """
    await invalidate_tokens(session, user, AuthTokenPurpose.EMAIL_VERIFICATION)
    token = await issue_token(
        session, user, AuthTokenPurpose.EMAIL_VERIFICATION, EMAIL_VERIFICATION_TTL
    )
    link = f"{get_settings().public_base_url}/verificar?token={token}"
    await mailer.send(
        Message(
            to=user.email,
            subject=f"Confirma tu correo en {APP_NAME}",
            body=(
                "Confirma tu correo para que podamos ayudarte a recuperar tu "
                "cuenta si olvidas la contraseña.\n\n"
                f"{link}\n\n"
                f"Puedes seguir usando {APP_NAME} mientras tanto."
            ),
        )
    )


async def reset_password(session: AsyncSession, user: User, new_password: str) -> None:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    user.password_hash = hash_password(new_password)
    await invalidate_tokens(session, user, AuthTokenPurpose.PASSWORD_RESET)
    await session.flush()


async def mark_email_verified(session: AsyncSession, user: User) -> None:
    user.email_verified_at = now_utc()
    await session.flush()
