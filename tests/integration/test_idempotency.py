"""Doble envío del formulario de cobro (§6).

`AC-18` se cierra en el hito 4, cuando exista la pantalla que manda la clave.
Lo que se prueba aquí es el mecanismo de la capa de servicio, que es donde vive
el riesgo: un electricista con mala señal va a tocar el botón dos veces, y un
recibo duplicado con un cobro duplicado es un fallo grave.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Account, Client, Payment, Receipt
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.errors import ConflictError
from tests.factories import make_account, make_client

pytestmark = pytest.mark.db


@pytest.fixture
async def account(db: AsyncSession) -> Account:
    return await make_account(db)


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account)


def a_charge(customer: Client | int, **overrides) -> ChargeRequest:
    """El mismo formulario, enviado tal cual. `overrides` cambia lo que se quiera."""
    fields: dict = {
        "client_id": customer if isinstance(customer, int) else customer.id,
        "concept": "Instalación de contactos",
        "agreed_total_cents": 230000,
        "payment_amount_cents": 180000,
        "idempotency_key": "formulario-abc123",
    }
    fields.update(overrides)
    return ChargeRequest(**fields)


class TestTheSameKeyNeverChargesTwice:
    async def test_the_second_submission_returns_the_first_receipt(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(scope, account, a_charge(customer))
        second = await charge(scope, account, a_charge(customer))

        assert second.replayed is True
        assert second.receipt.id == first.receipt.id
        assert second.receipt.folio == first.receipt.folio
        assert second.payment.id == first.payment.id

    async def test_only_one_payment_and_one_receipt_reach_the_database(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(scope, account, a_charge(customer))
        await charge(scope, account, a_charge(customer))
        await db.flush()

        payments = await db.scalar(select(func.count(Payment.id)))
        receipts = await db.scalar(select(func.count(Receipt.id)))
        assert (payments, receipts) == (1, 1)

    async def test_the_folio_is_not_burned_by_the_replay(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """Un reintento no puede dejar un hueco: no hubo emisión que cancelar."""
        await charge(scope, account, a_charge(customer))
        await charge(scope, account, a_charge(customer))

        next_one = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Otro trabajo",
                payment_amount_cents=10000,
                idempotency_key="formulario-def456",
            ),
        )
        assert next_one.receipt.folio == "RB-0002"


class TestAReusedKeyWithDifferentContentIsAnError:
    async def test_changing_the_amount_under_the_same_key_is_refused(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """Es un error del cliente, no un reintento, y hay que reportarlo."""
        await charge(scope, account, a_charge(customer))

        with pytest.raises(ConflictError, match="cobro distinto"):
            await charge(scope, account, a_charge(customer, payment_amount_cents=60000))

    async def test_two_different_keys_produce_two_receipts(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """Cobrar dos veces a propósito sigue siendo legítimo."""
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Trabajo uno",
                payment_amount_cents=10000,
                idempotency_key="uno",
            ),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Trabajo dos",
                payment_amount_cents=10000,
                idempotency_key="dos",
            ),
        )

        assert first.receipt.id != second.receipt.id
        assert (first.receipt.folio, second.receipt.folio) == ("RB-0001", "RB-0002")


class TestTheKeyIsScopedToTheAccount:
    async def test_two_accounts_may_use_the_same_key(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La clave la genera el navegador: la colisión entre cuentas es posible
        y no puede hacer que una cuenta vea el recibo de otra (AC-13)."""
        mine = await charge(scope, account, a_charge(customer))

        other_account = await make_account(db, display_name="Otra cuenta")
        other_scope = AccountScope(db, other_account.id)
        other_client = await make_client(db, other_account, full_name="Otro Cliente")
        theirs = await charge(
            other_scope,
            other_account,
            ChargeRequest(
                client_id=other_client.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
                idempotency_key="formulario-abc123",
            ),
        )

        assert theirs.replayed is False
        assert theirs.receipt.id != mine.receipt.id


class TestWithoutAKeyNothingIsDeduplicated:
    async def test_two_identical_charges_without_a_key_are_two_charges(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """Sin clave no hay forma de distinguir un reintento de un cobro real."""
        first = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Trabajo", payment_amount_cents=10000),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Trabajo", payment_amount_cents=10000),
        )

        assert first.receipt.id != second.receipt.id
        assert first.replayed is False and second.replayed is False


class TestTheFingerprint:
    """Huella del contenido del formulario. No necesita base de datos."""

    def test_identical_forms_hash_the_same_even_without_an_explicit_date(self) -> None:
        """`paid_at` vacío no puede resolverse antes de calcular la huella: cada
        intento daría un instante distinto y el reintento parecería otro cobro."""
        assert a_charge(1).fingerprint() == a_charge(1).fingerprint()

    def test_a_different_amount_changes_the_fingerprint(self) -> None:
        assert a_charge(1).fingerprint() != a_charge(1, payment_amount_cents=60000).fingerprint()

    def test_a_different_client_changes_the_fingerprint(self) -> None:
        assert a_charge(1).fingerprint() != a_charge(2).fingerprint()

    def test_the_key_itself_is_not_part_of_the_fingerprint(self) -> None:
        """La huella describe el contenido; la clave es la etiqueta que lo señala."""
        assert a_charge(1).fingerprint() == a_charge(1, idempotency_key="otra").fingerprint()
