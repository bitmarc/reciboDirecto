"""Directorio de clientes (E2).

Es el bloque 1 de la pantalla de cobro (§7.2): el emisor busca al cliente o lo
da de alta ahí mismo, sin salir de la pantalla. Todo lo que se construye aquí lo
reutiliza el hito 4.
"""

import re
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, func, or_
from sqlalchemy.sql._typing import ColumnExpressionArgument

from receipt.domain.clock import now_utc
from receipt.models import Client
from receipt.repositories import AccountScope
from receipt.services.accounts import normalize_email
from receipt.services.errors import ValidationError
from receipt.services.names import normalize_name, validate_person_name

_NON_DIGITS = re.compile(r"\D")

MIN_PHONE_DIGITS = 8

# Búsqueda insensible a acentos: nadie escribe "Ramírez" con tilde en el celular
# estando de pie en una puerta, y un buscador que no encuentra a la clienta
# obliga a darla de alta otra vez.
#
# Se usa `translate` y no la extensión `unaccent` a propósito: cubre el alfabeto
# español, que es todo lo que la v1 necesita (§2.2 descarta la
# internacionalización), y no obliga a habilitar una extensión en Azure Database
# for PostgreSQL. Si el producto saliera del español, `unaccent` es el relevo.
_ACCENTED = "áàäâãéèëêíìïîóòöôõúùüûñç"
_PLAIN = "aaaaaeeeeiiiiooooouuuunc"
assert len(_ACCENTED) == len(_PLAIN)


def folded(value: ColumnExpressionArgument[str] | str) -> ColumnElement[str]:
    """Minúsculas y sin acentos, para comparar como la gente teclea.

    Es público porque el historial de recibos busca igual (`services.receipts`):
    dos buscadores con reglas distintas de acentuación desconciertan más que uno
    que no encuentra nada.
    """
    return func.translate(func.lower(value), _ACCENTED, _PLAIN)


def validate_full_name(raw: str) -> str:
    """§4.2: obligatorio, mínimo nombre + un apellido.

    El recibo dice "Recibí de {cliente}" (§8.1). Un nombre de pila suelto no
    identifica a nadie meses después, que es justo cuando se consulta el
    comprobante.

    La regla es la misma que la del emisor y vive en `services/names.py`; aquí
    solo se le pone el copy del cliente.
    """
    return validate_person_name(
        raw,
        missing="El nombre del cliente es obligatorio.",
        incomplete=(
            "Escribe al menos nombre y un apellido, para que el recibo identifique "
            "a quién le cobraste."
        ),
    )


def validate_phone(raw: str) -> str:
    """Obligatorio. Se guarda tal como se escribió, pero debe tener dígitos."""
    phone = normalize_name(raw)
    if not phone:
        raise ValidationError("El teléfono del cliente es obligatorio.")
    if len(_NON_DIGITS.sub("", phone)) < MIN_PHONE_DIGITS:
        raise ValidationError("El teléfono parece incompleto.")
    return phone


def validate_optional_email(raw: str | None) -> str | None:
    """Opcional. Su ausencia deshabilita el envío por correo (§7.2)."""
    if raw is None or not raw.strip():
        return None
    return normalize_email(raw)


def search_statement(
    scope: AccountScope, *, query: str = "", include_archived: bool = False
) -> Select[tuple[Client]]:
    """Consulta del directorio, ya acotada a la cuenta de la sesión.

    Busca por nombre y por teléfono. El teléfono se compara además sin
    separadores, para que "55 1234 5678" encuentre a quien se guardó como
    "5512345678" y al revés: el emisor teclea el número como lo recuerda, no
    como lo capturó.
    """
    statement = scope.select(Client)

    if not include_archived:
        statement = statement.where(Client.archived_at.is_(None))

    term = normalize_name(query)
    if term:
        pattern = folded(f"%{term}%")
        conditions = [
            folded(Client.full_name).like(pattern),
            folded(Client.phone).like(pattern),
        ]

        digits = _NON_DIGITS.sub("", term)
        if digits:
            # Sin índice: con un directorio de decenas de clientes el barrido es
            # irrelevante. Si algún día crece, aquí va una columna normalizada.
            conditions.append(func.regexp_replace(Client.phone, r"\D", "", "g").like(f"%{digits}%"))

        statement = statement.where(or_(*conditions))

    return statement.order_by(Client.full_name)


async def create_client(
    scope: AccountScope,
    *,
    full_name: str,
    phone: str,
    email: str | None = None,
) -> Client:
    client = Client(
        account_id=scope.account_id,
        full_name=validate_full_name(full_name),
        phone=validate_phone(phone),
        email=validate_optional_email(email),
    )
    scope.session.add(client)
    await scope.session.flush()
    return client


async def update_client(
    scope: AccountScope,
    client: Client,
    *,
    full_name: str,
    phone: str,
    email: str | None = None,
) -> Client:
    """Edita un cliente.

    `AC-03` exige que renombrar a un cliente no altere los recibos previos. Eso
    lo garantiza el snapshot del hito 2, no esta función: aquí el nombre cambia
    con normalidad y el histórico queda protegido aguas abajo.
    """
    client.full_name = validate_full_name(full_name)
    client.phone = validate_phone(phone)
    client.email = validate_optional_email(email)
    await scope.session.flush()
    return client


async def archive_client(scope: AccountScope, client: Client) -> Client:
    """Oculta del selector sin borrar histórico (§4.2).

    No hay borrado físico (§3.4): el cliente sigue en el directorio con el
    filtro de archivados y sus recibos siguen resolviendo.
    """
    if client.archived_at is None:
        client.archived_at = now_utc()
        await scope.session.flush()
    return client


async def restore_client(scope: AccountScope, client: Client) -> Client:
    client.archived_at = None
    await scope.session.flush()
    return client


async def find_possible_duplicates(
    scope: AccountScope, *, phone: str, exclude_id: int | None = None
) -> Sequence[Client]:
    """Clientes activos con el mismo teléfono.

    No bloquea el alta —dos personas pueden compartir un teléfono de casa— pero
    permite avisar antes de duplicar a alguien que ya estaba.
    """
    digits = _NON_DIGITS.sub("", phone)
    if not digits:
        return []

    statement = (
        scope.select(Client)
        .where(Client.archived_at.is_(None))
        .where(func.regexp_replace(Client.phone, r"\D", "", "g") == digits)
    )
    if exclude_id is not None:
        statement = statement.where(Client.id != exclude_id)
    return await scope.list(statement)
