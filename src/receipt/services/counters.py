"""Folios secuenciales por cuenta (RN-25).

PostgreSQL no tiene secuencias por fila y los folios del PRD son **por cuenta**,
no globales: el primer recibo de cada emisor es `RB-0001`. La asignación usa una
tabla contador con `INSERT … ON CONFLICT DO UPDATE … RETURNING`, que es una sola
sentencia y toma el bloqueo de fila por sí misma: dos emisiones simultáneas de
la misma cuenta se serializan y ninguna repite folio, sin necesidad de
transacciones serializables.

El bloqueo se libera al terminar la transacción que lo tomó. La consecuencia
buscada es que una emisión que falla y revierte **no quema el folio**: los
huecos de RN-23 provienen de cancelaciones, que ocurren sobre recibos ya
emitidos, no de intentos fallidos.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import CounterKind

_PREFIXES = {
    CounterKind.SERVICE: "SRV",
    CounterKind.RECEIPT: "RB",
}

FOLIO_DIGITS = 4
"""`SRV-0001`, `RB-0002` (§4.2). Pasado el diez mil el folio simplemente crece:
truncarlo repetiría números, que es lo único inaceptable."""

# La fila se siembra al registrar la cuenta (`services.accounts`), pero la
# sentencia la crea si falta. Una cuenta sin contador no puede quedarse sin poder
# cobrar por un fallo de siembra, y `ON CONFLICT` hace que la reparación sea
# indistinguible del camino normal incluso bajo concurrencia.
_ALLOCATE = text(
    """
    INSERT INTO account_counter (account_id, kind, next_value)
    VALUES (:account_id, CAST(:kind AS counter_kind), 2)
    ON CONFLICT (account_id, kind)
    DO UPDATE SET next_value = account_counter.next_value + 1
    RETURNING next_value - 1
    """
)


async def allocate_number(session: AsyncSession, account_id: int, kind: CounterKind) -> int:
    """Reserva el siguiente número de la cuenta y lo devuelve."""
    number = await session.scalar(_ALLOCATE, {"account_id": account_id, "kind": kind.value})
    if number is None:  # pragma: no cover - la sentencia siempre devuelve fila
        raise RuntimeError(f"No se pudo asignar folio {kind.value} de la cuenta {account_id}")
    return int(number)


def format_folio(kind: CounterKind, number: int) -> str:
    return f"{_PREFIXES[kind]}-{number:0{FOLIO_DIGITS}d}"


async def allocate_folio(session: AsyncSession, account_id: int, kind: CounterKind) -> str:
    """`SRV-0001` para servicios, `RB-0001` para recibos."""
    return format_folio(kind, await allocate_number(session, account_id, kind))
