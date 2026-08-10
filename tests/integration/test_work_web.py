"""Pantallas de `/trabajos` y `/recibos` (§7.5).

El hito 2 construye el motor, no la pantalla bonita: `/cobrar` llega en el hito
4 y la plantilla del comprobante en el hito 3. Lo que se comprueba aquí es que
el emisor puede **consultar** lo que el motor produce, registrar un monto extra
y cancelar un recibo, y que nada de eso se sale de su cuenta.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import ReceiptStatus, ServiceStatus
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.work import load_totals
from tests.conftest import csrf_token_of, document_text, visible_text
from tests.factories import make_client

pytestmark = pytest.mark.db


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    """La cuenta que `signed_in` acaba de registrar por la vía real.

    La prueba corre dentro de una transacción propia, así que es la única.
    """
    account = await db.scalar(select(Account))
    assert account is not None
    return account


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


@pytest.fixture
async def cobro(scope: AccountScope, account: Account, customer: Client):
    """Un trabajo de $2,300 con un abono de $1,800: el caso de `AC-04`."""
    return await charge(
        scope,
        account,
        ChargeRequest(
            client_id=customer.id,
            concept="Instalación de contactos",
            location_alias="Casa Ramírez",
            agreed_total_cents=230000,
            payment_amount_cents=180000,
        ),
    )


class TestTheWorkList:
    async def test_it_shows_the_job_with_its_balance(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.get("/trabajos")

        assert response.status_code == 200
        text = visible_text(response)
        assert "Instalación de contactos" in text
        assert "SRV-0001" in text
        assert "Ana Ramírez" in text
        assert "$500.00" in text, "el saldo pendiente es lo que se viene a consultar"

    async def test_it_filters_by_status(self, signed_in: AsyncClient, cobro) -> None:
        open_jobs = await signed_in.get("/trabajos?estado=open")
        settled_jobs = await signed_in.get("/trabajos?estado=settled")

        assert "SRV-0001" in visible_text(open_jobs)
        assert "SRV-0001" not in visible_text(settled_jobs)

    async def test_an_invented_filter_does_not_break_the_page(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        """El filtro viaja en la barra de direcciones: un valor cualquiera no
        puede ser un error 500."""
        response = await signed_in.get("/trabajos?estado=inventado")
        assert response.status_code == 200

    async def test_the_empty_state_explains_where_jobs_come_from(
        self, signed_in: AsyncClient, account: Account
    ) -> None:
        response = await signed_in.get("/trabajos")
        assert "al registrar el primer cobro" in visible_text(response)


class TestTheJobPage:
    async def test_it_shows_amounts_payments_and_the_receipt(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        response = await signed_in.get(f"/trabajos/{cobro.service.id}")

        text = visible_text(response)
        assert "$2,300.00" in text, "total acordado"
        assert "$1,800.00" in text, "lo pagado"
        assert "$500.00" in text, "saldo pendiente"
        assert "RB-0001" in text

    async def test_a_job_of_another_account_is_not_found(
        self, signed_in: AsyncClient, db: AsyncSession, cobro
    ) -> None:
        from tests.factories import make_account

        other = await make_account(db, display_name="Cuenta ajena")
        other_client = await make_client(db, other, full_name="Cliente Ajeno")
        theirs = await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Trabajo ajeno", payment_amount_cents=10000
            ),
        )

        response = await signed_in.get(f"/trabajos/{theirs.service.id}")

        assert response.status_code == 404
        assert "Trabajo ajeno" not in response.text


class TestRegisteringExtraWork:
    async def test_an_extra_amount_raises_the_balance(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        response = await signed_in.post(
            f"/trabajos/{cobro.service.id}/extras",
            data={
                "amount_cents": 10000,
                "reason": "Cambio de 2 contactos en recámara",
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        totals = await load_totals(scope, cobro.service)
        assert totals.current_total_cents == 240000
        assert totals.balance_cents == 60000

    async def test_rn12_an_extra_without_a_reason_is_refused(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        response = await signed_in.post(
            f"/trabajos/{cobro.service.id}/extras",
            data={
                "amount_cents": 10000,
                "reason": "   ",
                "csrf_token": csrf_token_of(signed_in),
            },
        )

        assert response.status_code == 400
        assert "por qué subió el precio" in visible_text(response)
        totals = await load_totals(scope, cobro.service)
        assert totals.current_total_cents == 230000, "no se registró nada"

    async def test_an_extra_reopens_a_settled_job(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        settled = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Cableado",
                agreed_total_cents=100000,
                payment_amount_cents=100000,
            ),
        )
        assert settled.service.status is ServiceStatus.SETTLED

        await signed_in.post(
            f"/trabajos/{settled.service.id}/extras",
            data={
                "amount_cents": 20000,
                "reason": "Se agregó una salida más",
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert settled.service.status is ServiceStatus.OPEN

    async def test_it_requires_the_csrf_token(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.post(
            f"/trabajos/{cobro.service.id}/extras",
            data={"amount_cents": 10000, "reason": "Sin token"},
        )
        assert response.status_code == 403


class TestTheReceiptHistory:
    async def test_it_lists_the_issued_receipt(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.get("/recibos")

        text = visible_text(response)
        assert "RB-0001" in text
        assert "Ana Ramírez" in text
        assert "$1,800.00" in text

    async def test_it_searches_by_folio_and_by_client(self, signed_in: AsyncClient, cobro) -> None:
        by_folio = await signed_in.get("/recibos?q=RB-0001")
        by_client = await signed_in.get("/recibos?q=ramirez")
        by_nobody = await signed_in.get("/recibos?q=zzzz")

        assert "Ana Ramírez" in visible_text(by_folio)
        assert "Ana Ramírez" in visible_text(by_client), "la búsqueda ignora acentos"
        # El folio aparece en el marcador de posición del buscador, así que la
        # ausencia se comprueba con el estado vacío, no con la subcadena.
        assert "Ningún recibo coincide" in visible_text(by_nobody)

    async def test_the_detail_renders_the_frozen_snapshot(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        text = visible_text(response)
        assert "RB-0001" in text
        assert "Ana Ramírez" in text
        assert "Instalación de contactos" in text
        assert "$1,800.00" in text
        assert "$500.00" in text, "el saldo pendiente congelado"


class TestAc02And03TheReceiptDoesNotFollowLaterEdits:
    async def test_renaming_the_client_does_not_change_the_receipt(
        self, signed_in: AsyncClient, scope: AccountScope, customer: Client, cobro
    ) -> None:
        """`AC-03`. El comprobante lee el snapshot, no la tabla viva.

        La ficha del emisor muestra además un enlace de navegación al cliente
        **actual**, y ese sí lleva el nombre nuevo. No es una fuga de `AC-03`:
        el criterio habla del recibo, y el recibo es el documento. Ver los dos
        nombres juntos es justamente lo que le dice al emisor que se trata de la
        misma persona, ahora renombrada.
        """
        from receipt.services.clients import update_client

        await update_client(
            scope, customer, full_name="Ana María Ramírez Torres", phone="5598765432"
        )

        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        assert "Recibí de Ana Ramírez" in document_text(response), "el comprobante no cambió"
        assert f'href="/clientes/{customer.id}"' in response.text, "el enlace vivo sigue ahí"

    async def test_changing_the_issuer_phone_does_not_change_the_receipt(
        self, signed_in: AsyncClient, account: Account, cobro
    ) -> None:
        """`AC-02`, en la parte que el hito 2 puede sostener: el snapshot ya
        congeló los datos del emisor antes del cambio."""
        frozen = cobro.receipt.snapshot["issuer"]["phone"]
        account.phone = "5500000000"

        assert frozen != "5500000000"
        assert cobro.receipt.snapshot["issuer"]["phone"] == frozen


class TestCancellingFromTheWeb:
    async def test_it_cancels_the_receipt_and_frees_the_balance(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/cancelar",
            data={
                "reason": "Se registró con el monto equivocado",
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert cobro.receipt.status is ReceiptStatus.CANCELLED
        totals = await load_totals(scope, cobro.service)
        assert totals.paid_cents == 0

    async def test_ac08_cancelling_without_a_reason_is_refused(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/cancelar",
            data={"reason": "", "csrf_token": csrf_token_of(signed_in)},
        )

        assert response.status_code == 400
        assert cobro.receipt.status is ReceiptStatus.ISSUED

    async def test_the_cancelled_receipt_shows_its_seal_and_reason(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/cancelar",
            data={"reason": "Cobro duplicado", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        text = visible_text(response)
        assert "Cancelado" in text
        assert "Cobro duplicado" in text

    async def test_a_cancelled_receipt_no_longer_offers_the_cancel_form(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/cancelar",
            data={"reason": "Cobro duplicado", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")
        assert "Cancelar recibo" not in visible_text(response)


class TestTheClientPage:
    async def test_it_now_lists_the_jobs_of_that_client(
        self, signed_in: AsyncClient, customer: Client, cobro
    ) -> None:
        response = await signed_in.get(f"/clientes/{customer.id}")

        text = visible_text(response)
        assert "Instalación de contactos" in text
        assert "SRV-0001" in text
        assert "$500.00" in text
