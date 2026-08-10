"""Cancelación de recibos — `AC-08`, `AC-09`, `AC-11` y `AC-16`.

RN-22 en una frase: **cancelar nunca borra**. El recibo se queda, con su motivo,
fuera de la contabilidad y con su folio quemado. Lo único que desaparece es el
efecto del pago sobre el saldo del trabajo.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import period_of
from receipt.domain.enums import PaymentStatus, ReceiptStatus, ServiceStatus
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.errors import ConflictError, ValidationError
from receipt.services.receipts import cancel_receipt
from receipt.services.usage import issued_in_period
from receipt.services.work import load_totals
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


async def charge_once(
    scope: AccountScope,
    account: Account,
    customer: Client,
    *,
    agreed_total_cents: int | None = 230000,
    amount_cents: int = 180000,
    service_id: int | None = None,
):
    return await charge(
        scope,
        account,
        ChargeRequest(
            client_id=customer.id,
            service_id=service_id,
            concept="Instalación de contactos",
            agreed_total_cents=agreed_total_cents if service_id is None else None,
            payment_amount_cents=amount_cents,
        ),
    )


class TestAc08CancellingWithoutAReasonFails:
    async def test_an_empty_reason_is_rejected(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer)

        with pytest.raises(ValidationError, match="por qué"):
            await cancel_receipt(scope, result.receipt, reason="")

    async def test_a_blank_reason_is_rejected(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """Un motivo de puros espacios es lo mismo que no haberlo escrito."""
        result = await charge_once(scope, account, customer)

        with pytest.raises(ValidationError, match="por qué"):
            await cancel_receipt(scope, result.receipt, reason="    ")

    async def test_the_receipt_survives_the_failed_attempt_intact(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer)

        with pytest.raises(ValidationError):
            await cancel_receipt(scope, result.receipt, reason="")

        assert result.receipt.status is ReceiptStatus.ISSUED
        assert result.receipt.cancelled_at is None

    async def test_the_database_refuses_it_too(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La regla no puede vivir solo en la capa de aplicación.

        Un script de mantenimiento o una consola de administración no pasan por
        `cancel_receipt`, y `AC-08` no dice "por la API".
        """
        result = await charge_once(scope, account, customer)
        await db.flush()

        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "UPDATE receipt SET status = 'cancelled', cancelled_at = now() WHERE id = :id"
                ),
                {"id": result.receipt.id},
            )


class TestAc09TheCancelledPaymentStopsCounting:
    async def test_the_service_returns_to_its_previous_balance(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer, amount_cents=230000)
        assert result.service.status is ServiceStatus.SETTLED

        await cancel_receipt(scope, result.receipt, reason="Se registró con el monto equivocado")

        totals = await load_totals(scope, result.service)
        assert totals.paid_cents == 0
        assert totals.balance_cents == 230000
        assert result.service.status is ServiceStatus.OPEN

    async def test_the_payment_itself_is_cancelled_not_deleted(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-22: el pago se cancela junto con su recibo. §3.4: no hay borrado."""
        result = await charge_once(scope, account, customer)

        await cancel_receipt(scope, result.receipt, reason="Cobro duplicado")
        await db.flush()

        assert result.payment.status is PaymentStatus.CANCELLED
        still_there = await db.scalar(
            text("SELECT count(*) FROM payment WHERE id = :id"), {"id": result.payment.id}
        )
        assert still_there == 1

    async def test_only_the_cancelled_payment_stops_counting(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge_once(scope, account, customer, amount_cents=180000)
        second = await charge_once(
            scope, account, customer, service_id=first.service.id, amount_cents=50000
        )

        await cancel_receipt(scope, second.receipt, reason="Se capturó dos veces")

        totals = await load_totals(scope, first.service)
        assert totals.paid_cents == 180000
        assert totals.balance_cents == 50000

    async def test_the_receipt_stays_in_the_history_with_its_reason(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer)

        await cancel_receipt(scope, result.receipt, reason="El cliente pidió factura aparte")

        assert result.receipt.status is ReceiptStatus.CANCELLED
        assert result.receipt.cancellation_reason == "El cliente pidió factura aparte"
        assert result.receipt.cancelled_at is not None

    async def test_the_snapshot_is_not_touched_by_the_cancellation(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-10 y RN-24: la liga sigue mostrando lo que decía, con el sello encima."""
        result = await charge_once(scope, account, customer)
        frozen = dict(result.receipt.snapshot)

        await cancel_receipt(scope, result.receipt, reason="Monto equivocado")

        assert result.receipt.snapshot == frozen

    async def test_cancelling_twice_is_refused(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer)
        await cancel_receipt(scope, result.receipt, reason="Monto equivocado")

        with pytest.raises(ConflictError, match="ya estaba cancelado"):
            await cancel_receipt(scope, result.receipt, reason="Otra vez")


class TestAc11TheFolioIsNeverReused:
    async def test_the_next_receipt_skips_the_cancelled_number(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-23: los huecos en la numeración son correctos y esperados."""
        first = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=10000
        )
        second = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=20000
        )
        assert (first.receipt.folio, second.receipt.folio) == ("RB-0001", "RB-0002")

        await cancel_receipt(scope, second.receipt, reason="Monto equivocado")

        third = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=30000
        )
        assert third.receipt.folio == "RB-0003", "el folio cancelado no se recicla"

    async def test_the_cancelled_folio_remains_taken(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=10000
        )
        await cancel_receipt(scope, first.receipt, reason="Monto equivocado")

        second = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=20000
        )

        assert first.receipt.folio == "RB-0001"
        assert second.receipt.folio == "RB-0002"


class TestAc16ACancelledReceiptStillConsumesQuota:
    """Si el cupo se devolviera, el límite sería evadible cancelando (RN-33)."""

    async def test_the_counter_does_not_go_back_down(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge_once(scope, account, customer)
        period = period_of(result.receipt.issued_at)
        assert await issued_in_period(db, account.id, period) == 1

        await cancel_receipt(scope, result.receipt, reason="Monto equivocado")

        assert await issued_in_period(db, account.id, period) == 1

    async def test_reissuing_after_cancelling_consumes_a_second_slot(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """El escenario que la regla evita: cancelar y volver a emitir gratis."""
        first = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=10000
        )
        await cancel_receipt(scope, first.receipt, reason="Monto equivocado")

        second = await charge_once(
            scope, account, customer, agreed_total_cents=None, amount_cents=10000
        )

        period = period_of(second.receipt.issued_at)
        assert await issued_in_period(db, account.id, period) == 2
