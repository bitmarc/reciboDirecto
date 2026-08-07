"""Aislamiento por cuenta (§10, AC-13).

AC-13 exige que un usuario de la cuenta A no pueda leer ningún recurso de la
cuenta B **por ninguna ruta**. La defensa de fondo es que ninguna consulta se
pueda construir sin `account_id`; estas pruebas verifican el mecanismo y, sobre
todo, que no se pueda agregar una tabla nueva olvidando declararlo.

La prueba que recorre las rutas registradas llega en el hito 2, cuando existan
rutas de recurso que recorrer.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Base, Client, Payment, Service
from receipt.repositories import AccountScope, registered_models
from tests.factories import make_account, make_client, make_payment, make_service

pytestmark = pytest.mark.db


async def test_a_client_of_another_account_is_invisible(db: AsyncSession) -> None:
    account_a = await make_account(db, display_name="Cuenta A")
    account_b = await make_account(db, display_name="Cuenta B")
    client_b = await make_client(db, account_b, full_name="Cliente De B")

    scope_a = AccountScope(db, account_a.id)

    assert await scope_a.get(Client, client_b.id) is None
    assert await scope_a.list(scope_a.select(Client)) == []


async def test_a_payment_of_another_account_is_invisible_through_its_service(
    db: AsyncSession,
) -> None:
    """Los pagos no llevan `account_id`: cuelgan del servicio.

    Es el caso donde un filtro escrito a mano se olvida con más facilidad.
    """
    account_a = await make_account(db, display_name="Cuenta A")
    account_b = await make_account(db, display_name="Cuenta B")
    client_b = await make_client(db, account_b)
    service_b = await make_service(db, account_b, client_b)
    payment_b = await make_payment(db, service_b)

    scope_a = AccountScope(db, account_a.id)

    assert await scope_a.get(Payment, payment_b.id) is None


async def test_own_resources_remain_reachable(db: AsyncSession) -> None:
    account = await make_account(db)
    client = await make_client(db, account)
    service = await make_service(db, account, client)
    payment = await make_payment(db, service)

    scope = AccountScope(db, account.id)

    assert (await scope.get(Client, client.id)) is not None
    assert (await scope.get(Service, service.id)) is not None
    assert (await scope.get(Payment, payment.id)) is not None


def test_every_mapped_table_declares_how_it_is_scoped_to_an_account() -> None:
    """Una tabla sin regla de alcance es una fuga entre cuentas esperando a pasar.

    Esta prueba falla en cuanto se agrega un modelo nuevo sin registrarlo, que es
    justo el momento en que conviene enterarse.
    """
    mapped_models = {
        mapper.class_
        for mapper in Base.registry.mappers
        if not getattr(mapper.class_, "__abstract__", False)
    }
    # Catálogos globales, sin dueño: los cupones no pertenecen a una cuenta.
    global_models = {"Coupon"}

    unscoped = {
        model.__name__
        for model in mapped_models - registered_models()
        if model.__name__ not in global_models
    }

    assert not unscoped, (
        f"Modelos sin alcance por cuenta: {sorted(unscoped)}. "
        f"Regístralos en receipt.repositories.base._RULES (AC-13)."
    )


async def test_querying_an_unregistered_model_fails_loudly(db: AsyncSession) -> None:
    from receipt.models import Coupon

    scope = AccountScope(db, 1)
    with pytest.raises(LookupError, match="alcance por cuenta"):
        scope.select(Coupon)
