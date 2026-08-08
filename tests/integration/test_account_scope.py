"""Aislamiento por cuenta (§10, AC-13).

AC-13 exige que un usuario de la cuenta A no pueda leer ningún recurso de la
cuenta B **por ninguna ruta**. Hay dos defensas y las dos se prueban aquí:

1. Ninguna consulta se puede construir sin `account_id`, y ninguna tabla nueva
   puede consultarse sin declarar cómo se ata a una cuenta.
2. El barrido de rutas: se recorre `app.routes` por introspección y se
   comprueba que cada recurso con ID responde 404 a una cuenta ajena. Cubre
   literalmente el "por ninguna ruta" de la AC, y sigue cubriendo las rutas que
   se agreguen en los hitos siguientes sin tocar esta prueba.
"""

import re

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.main import create_app
from receipt.models import Base, Client, Payment, Service
from receipt.repositories import AccountScope, registered_models
from tests.factories import make_account, make_client, make_payment, make_service

pytestmark = pytest.mark.db

_PATH_PARAM = re.compile(r"\{(\w+)\}")


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


# ---------------------------------------------------------------------------
# AC-13 — barrido de rutas
# ---------------------------------------------------------------------------


def resource_routes() -> list[str]:
    """Rutas GET con un único identificador numérico en el camino.

    Se descubren por introspección, no a mano: una ruta nueva queda cubierta
    sola, que es justo lo que hace falta cuando el hito 2 agregue `/trabajos` y
    `/recibos`.
    """
    paths = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        params = _PATH_PARAM.findall(route.path)
        if len(params) == 1 and params[0].endswith("_id"):
            paths.append(route.path)
    return sorted(paths)


def test_the_sweep_actually_finds_routes_to_check() -> None:
    """Guarda contra el falso verde.

    Si el descubrimiento dejara de encontrar rutas, el barrido pasaría sin
    comprobar nada y AC-13 quedaría sin cobertura en silencio.
    """
    assert resource_routes(), "el barrido de AC-13 no encontró ninguna ruta de recurso"


@pytest.mark.parametrize("path", resource_routes())
async def test_ac13_no_route_exposes_a_resource_of_another_account(
    signed_in: AsyncClient, db: AsyncSession, path: str
) -> None:
    other = await make_account(db, display_name="Cuenta ajena")
    foreign = await make_client(db, other, full_name="Cliente Ajeno", phone="5500000000")

    param = _PATH_PARAM.search(path)
    assert param is not None
    url = path.replace(param.group(0), str(foreign.id))

    response = await signed_in.get(url, follow_redirects=False)

    assert response.status_code == 404, (
        f"{url} respondió {response.status_code} a una cuenta ajena; "
        f"AC-13 exige que ningún recurso de otra cuenta sea alcanzable."
    )
    assert "Cliente Ajeno" not in response.text
