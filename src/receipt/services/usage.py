"""Consumo mensual del plan gratuito (§4.2, RN-33).

El hito 6 construye el **bloqueo** al llegar al límite (`AC-15`) y su
instrumentación. Lo que existe desde el hito 2 es el conteo, porque `AC-16`
pertenece al motor de cobro: *un recibo cancelado consume cupo*.

De ahí la única regla de este módulo: **el contador nunca baja.** Cancelar no lo
devuelve. Si lo devolviera, el límite sería evadible cancelando y volviendo a
emitir, y el plan gratuito dejaría de existir en la práctica.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RECORD_ISSUANCE = text(
    """
    INSERT INTO usage_counter (account_id, period, receipts_issued)
    VALUES (:account_id, :period, 1)
    ON CONFLICT (account_id, period)
    DO UPDATE SET receipts_issued = usage_counter.receipts_issued + 1
    RETURNING receipts_issued
    """
)


async def record_issuance(session: AsyncSession, account_id: int, period: str) -> int:
    """Suma un recibo al periodo y devuelve cuántos van.

    `period` es `YYYY-MM` en hora de CDMX, no en UTC (§4.2): el corte del mes
    tiene que caer donde el emisor lo espera, igual que el contable (AC-07).
    """
    issued = await session.scalar(_RECORD_ISSUANCE, {"account_id": account_id, "period": period})
    if issued is None:  # pragma: no cover - la sentencia siempre devuelve fila
        raise RuntimeError(f"No se pudo registrar el consumo de {account_id} en {period}")
    return int(issued)


async def issued_in_period(session: AsyncSession, account_id: int, period: str) -> int:
    """Cuántos recibos lleva emitidos la cuenta en el periodo, cancelados incluidos."""
    issued = await session.scalar(
        text(
            "SELECT receipts_issued FROM usage_counter"
            " WHERE account_id = :account_id AND period = :period"
        ),
        {"account_id": account_id, "period": period},
    )
    return int(issued or 0)
