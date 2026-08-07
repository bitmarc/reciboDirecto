"""AC-01 — modificar `agreed_total_cents` falla por API y por SQL directo (RN-11).

Es la salida del hito 0. La protección vive en un trigger de PostgreSQL, no en
la capa de aplicación: una regla que solo se respeta desde el código no sobrevive
a un script de mantenimiento ni a una consola de administración.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import ServiceStatus
from tests.factories import make_account, make_client, make_service

pytestmark = pytest.mark.db


async def test_ac01_orm_update_of_agreed_total_fails(db: AsyncSession) -> None:
    account = await make_account(db)
    client = await make_client(db, account)
    service = await make_service(db, account, client, agreed_total_cents=230000)

    service.agreed_total_cents = 999999

    with pytest.raises(IntegrityError, match="RN-11"):
        await db.flush()


async def test_ac01_raw_sql_update_of_agreed_total_fails(db: AsyncSession) -> None:
    account = await make_account(db)
    client = await make_client(db, account)
    service = await make_service(db, account, client, agreed_total_cents=230000)

    with pytest.raises(IntegrityError, match="RN-11"):
        await db.execute(
            text("UPDATE service SET agreed_total_cents = :total WHERE id = :id"),
            {"total": 999999, "id": service.id},
        )


async def test_ac01_cannot_set_a_total_on_a_service_that_was_created_without_one(
    db: AsyncSession,
) -> None:
    """El caso que `<>` dejaría pasar y `IS DISTINCT FROM` atrapa.

    Un servicio de pago único nace sin total (RN-15). Escribirle uno después
    sería inventar un acuerdo que nunca existió, que es exactamente el conflicto
    de §1.2 que el producto viene a resolver.
    """
    account = await make_account(db)
    client = await make_client(db, account)
    service = await make_service(db, account, client, agreed_total_cents=None)

    with pytest.raises(IntegrityError, match="RN-11"):
        await db.execute(
            text("UPDATE service SET agreed_total_cents = :total WHERE id = :id"),
            {"total": 500000, "id": service.id},
        )


async def test_other_service_columns_remain_updatable(db: AsyncSession) -> None:
    """El trigger no puede convertir la tabla en inmutable entera.

    El estado del servicio cambia constantemente (RN-13, RN-17); solo el total
    acordado está congelado.
    """
    account = await make_account(db)
    client = await make_client(db, account)
    service = await make_service(db, account, client, agreed_total_cents=230000)

    service.status = ServiceStatus.SETTLED
    service.concept = "Instalación de contactos y revisión de tablero"
    await db.flush()
    await db.refresh(service)

    assert service.status is ServiceStatus.SETTLED
    assert service.agreed_total_cents == 230000
