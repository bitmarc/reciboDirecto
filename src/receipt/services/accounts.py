"""Alta y autenticación del emisor (E1)."""

from dataclasses import dataclass

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
from receipt.services.names import normalize_name, validate_person_name


def normalize_email(raw: str) -> str:
    """Recorta, valida y normaliza.

    El recorte va antes de validar: un correo pegado desde WhatsApp llega con
    espacios y el validador lo rechazaría por eso.
    """
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValidationError("El correo no parece válido.") from exc


def validate_issuer_name(raw: str) -> str:
    """El nombre de quien firma.

    Misma regla que la del cliente (§4.2) y por el mismo motivo: §8.1 rotula el
    pie del recibo «Firma de quien recibe el pago», y un nombre de pila suelto
    no identifica a nadie meses después, que es cuando se consulta el
    comprobante.
    """
    return validate_person_name(
        raw,
        missing="Escribe tu nombre: es quien firma el recibo.",
        incomplete="Escribe al menos nombre y un apellido: así te identifica quien lo recibe.",
    )


def validate_business_name(raw: str | None) -> str | None:
    """Opcional. Cuando existe encabeza el recibo (§8.1); nunca firma.

    Sin regla de nombre + apellido: un negocio se puede llamar como quiera.
    Vacío se normaliza a `None`, que es lo que hace segura la caída de
    `Account.issuer_name`.
    """
    return normalize_name(raw or "") or None


@dataclass(frozen=True)
class ProfileFields:
    """Perfil del emisor ya validado y normalizado (§6 `PATCH /settings`)."""

    person_name: str
    business_name: str | None
    phone: str
    default_city: str
    occupation: str | None


def validate_profile(
    *,
    person_name: str,
    business_name: str | None,
    phone: str,
    default_city: str,
    occupation: str | None,
    current_person_name: str | None = None,
) -> ProfileFields:
    """Valida el perfil completo **antes** de tocar nada.

    Se valida todo y se asigna después: asignar sobre el objeto ORM y rechazar a
    media lista lo dejaría sucio en la sesión, y un autoflush posterior
    persistiría media edición.

    `current_person_name` es el nombre que la cuenta ya tenía guardado, y llega
    solo desde la edición del perfil. Si el nombre que entra es idéntico, la
    regla de «nombre y apellido» no se aplica: las cuentas anteriores a la
    separación de nombres pueden traer una sola palabra —hasta entonces el campo
    solo tenía que no ir vacío— y bloquearle a alguien un cambio de teléfono por
    un campo que ni tocó sería cobrarle un error que no cometió. En el alta no
    hay historia que respetar y por eso el parámetro va vacío.
    """
    name = normalize_name(person_name)
    if not current_person_name or name != current_person_name:
        name = validate_issuer_name(person_name)

    validated = ProfileFields(
        person_name=name,
        business_name=validate_business_name(business_name),
        phone=phone.strip(),
        default_city=default_city.strip(),
        occupation=(occupation or "").strip() or None,
    )
    if not validated.phone:
        raise ValidationError("El teléfono es obligatorio: aparece en el recibo.")
    if not validated.default_city:
        raise ValidationError("La ciudad es obligatoria: encabeza el recibo.")
    return validated


async def register_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    person_name: str,
    phone: str,
    default_city: str,
    business_name: str | None = None,
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

    profile = validate_profile(
        person_name=person_name,
        business_name=business_name,
        phone=phone,
        default_city=default_city,
        occupation=occupation,
    )

    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise ConflictError("Ese correo ya tiene una cuenta.")

    account = Account(
        person_name=profile.person_name,
        business_name=profile.business_name,
        phone=profile.phone,
        occupation=profile.occupation,
        default_city=profile.default_city,
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


async def update_profile(
    session: AsyncSession,
    account: Account,
    *,
    person_name: str,
    phone: str,
    default_city: str,
    business_name: str | None = None,
    occupation: str | None = None,
) -> Account:
    """Actualiza el perfil que aparece como emisor (§6 `PATCH /settings`).

    `AC-02` exige que esto **no** altere los recibos ya emitidos. Lo garantiza el
    snapshot, no esta función: aquí los datos vivos cambian con normalidad.

    Existe como servicio y no como cuatro asignaciones en la ruta porque el
    nombre de la persona tiene la misma regla que en el alta, y dos copias de esa
    regla se separan a la primera edición.
    """
    profile = validate_profile(
        person_name=person_name,
        business_name=business_name,
        phone=phone,
        default_city=default_city,
        occupation=occupation,
        # Deja pasar el nombre que la cuenta ya tenía aunque no cumpla la regla
        # nueva. Ver `validate_profile`.
        current_person_name=account.person_name,
    )
    account.person_name = profile.person_name
    account.business_name = profile.business_name
    account.phone = profile.phone
    account.default_city = profile.default_city
    account.occupation = profile.occupation
    await session.flush()
    return account


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
