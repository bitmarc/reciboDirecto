"""Alta y autenticación del emisor (E1)."""

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import CounterKind
from receipt.models import Account, AccountCounter, User
from receipt.security.passwords import (
    MIN_PASSWORD_LENGTH,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from receipt.services.errors import ConflictError, ValidationError


def normalize_email(raw: str) -> str:
    """Recorta, valida y normaliza.

    El recorte va antes de validar: un correo pegado desde WhatsApp llega con
    espacios y el validador lo rechazaría por eso.
    """
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValidationError("El correo no parece válido.") from exc


async def register_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    phone: str,
    default_city: str,
    occupation: str | None = None,
) -> User:
    """Crea cuenta y usuario en una sola transacción.

    §4.1: los datos cuelgan de `Account`, no de `User`. En v1 hay exactamente un
    usuario por cuenta, pero el modelo ya permite agregar más sin migrar.
    """
    email = normalize_email(email)

    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if not display_name.strip():
        raise ValidationError("Escribe tu nombre o el de tu negocio.")
    if not phone.strip():
        raise ValidationError("El teléfono es obligatorio: aparece en el recibo.")
    if not default_city.strip():
        raise ValidationError("La ciudad es obligatoria: encabeza el recibo.")

    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise ConflictError("Ese correo ya tiene una cuenta.")

    account = Account(
        display_name=display_name.strip(),
        phone=phone.strip(),
        occupation=(occupation or "").strip() or None,
        default_city=default_city.strip(),
    )
    session.add(account)
    await session.flush()

    # Contadores de folio por cuenta (RN-25). Se siembran aquí para que la
    # emisión nunca tenga que crear la fila bajo concurrencia.
    session.add_all(
        AccountCounter(account_id=account.id, kind=kind, next_value=1) for kind in CounterKind
    )

    user = User(
        account_id=account.id,
        email=email,
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User | None:
    """Devuelve el usuario si las credenciales son correctas, `None` si no.

    No distingue "correo inexistente" de "contraseña incorrecta", ni en el
    mensaje ni en el tiempo de respuesta.
    """
    try:
        normalized = normalize_email(email)
    except ValidationError:
        dummy_verify()
        return None

    user = await session.scalar(select(User).where(User.email == normalized))
    if user is None:
        dummy_verify()
        return None

    if not verify_password(user.password_hash, password):
        return None

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    return user
