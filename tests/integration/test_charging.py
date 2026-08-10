"""Motor de cobro — `AC-04` a `AC-07` de extremo a extremo.

Las mismas reglas se prueban sin base de datos en `tests/unit/test_calculations.py`.
Lo que se comprueba aquí es que la transacción de `charge()` las respeta cuando
hay tablas, folios y snapshots de por medio: es donde se rompen las cosas.
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import LOCAL_TZ, month_bounds_utc, period_of
from receipt.domain.enums import (
    PaymentMethod,
    ReceiptStatus,
    ReceiptTemplate,
    ServiceStatus,
)
from receipt.models import Account, Client, Payment, Service
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.errors import BusinessRuleViolation, ValidationError
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
    # No se llama `client`: ese nombre ya es el cliente HTTP en `conftest`.
    return await make_client(db, account)


class TestAc04PartialPayment:
    """Servicio de $2,300, pago de $1,800 → saldo $500, servicio `open`."""

    async def test_the_balance_is_five_hundred_and_the_service_stays_open(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        totals = await load_totals(scope, result.service)
        assert totals.balance_cents == 50000
        assert result.service.status is ServiceStatus.OPEN

    async def test_the_receipt_freezes_the_pending_balance(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        amounts = result.receipt.snapshot["amounts"]
        assert amounts["previous_paid_cents"] == 0
        assert amounts["payment_cents"] == 180000
        assert amounts["balance_cents"] == 50000

    async def test_a_declared_total_forces_the_detailed_template(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§8.4: con total acordado, el recibo tiene que enseñar el saldo."""
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        assert result.receipt.template is ReceiptTemplate.DETAILED


class TestAc05AdjustmentSettlesTheService:
    """Adjustment de +$100 con motivo, pago de $600 → total $2,400, saldo $0."""

    async def test_the_second_payment_with_an_adjustment_settles_everything(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        second = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                adjustment_amount_cents=10000,
                adjustment_reason="Cambio de 2 contactos en recámara",
                payment_amount_cents=60000,
            ),
        )

        totals = await load_totals(scope, second.service)
        assert totals.current_total_cents == 240000
        assert totals.paid_cents == 240000
        assert totals.balance_cents == 0
        assert second.service.status is ServiceStatus.SETTLED

    async def test_the_second_receipt_shows_the_previous_payment_and_the_reason(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§8.3: el motivo del extra aparece textualmente en el recibo posterior."""
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                adjustment_amount_cents=10000,
                adjustment_reason="Cambio de 2 contactos en recámara",
                payment_amount_cents=60000,
            ),
        )

        amounts = second.receipt.snapshot["amounts"]
        assert amounts["current_total_cents"] == 240000
        assert amounts["previous_paid_cents"] == 180000
        assert amounts["payment_cents"] == 60000
        assert amounts["accumulated_paid_cents"] == 240000
        assert amounts["balance_cents"] == 0
        assert amounts["adjustments"] == [
            {"amount_cents": 10000, "reason": "Cambio de 2 contactos en recámara"}
        ]

    async def test_the_first_receipt_did_not_change(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-10: el extra posterior no reescribe el recibo que ya se entregó."""
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        frozen = json.dumps(first.receipt.snapshot, sort_keys=True)

        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                adjustment_amount_cents=10000,
                adjustment_reason="Cambio de 2 contactos en recámara",
                payment_amount_cents=60000,
            ),
        )

        assert json.dumps(first.receipt.snapshot, sort_keys=True) == frozen

    async def test_rn12_an_adjustment_without_a_reason_is_rejected(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        with pytest.raises(ValidationError, match="por qué subió"):
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    service_id=first.service.id,
                    adjustment_amount_cents=10000,
                    adjustment_reason="   ",
                    payment_amount_cents=60000,
                ),
            )


class TestAc06MaterialIsInternal:
    """Pago de $1,000 con $300 de material: el recibo muestra $1,000 sin desglose."""

    async def test_the_material_is_stored_but_never_reaches_the_receipt(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

        assert result.payment.material_cents == 30000, "el registro interno sí se guarda"

        snapshot = result.receipt.snapshot
        assert snapshot["amounts"]["payment_cents"] == 100000
        assert "material_cents" not in snapshot["amounts"]
        assert "30000" not in json.dumps(snapshot, ensure_ascii=False)

    async def test_the_concept_is_worded_as_an_aggregate(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )
        assert result.receipt.snapshot["service"]["concept_line"] == (
            "Reparación de tablero — mano de obra y material"
        )

    async def test_material_above_the_payment_is_rejected(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        with pytest.raises(ValidationError, match="material"):
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    concept="Reparación de tablero",
                    payment_amount_cents=100000,
                    material_cents=100001,
                ),
            )


class TestAc07TheAccountingCutIsLocal:
    """Un pago del 31 de enero 23:30 hora CDMX cuenta en enero, no en febrero."""

    PAID_AT = datetime(2026, 1, 31, 23, 30, tzinfo=LOCAL_TZ)

    async def test_the_payment_falls_inside_the_january_window(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La consulta de ingresos de §9.1 acota por `paid_at` en UTC.

        En UTC ese instante ya es febrero: si los límites del mes se calcularan
        en UTC, el pago se contaría en el mes equivocado.
        """
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=self.PAID_AT,
            ),
        )

        january_start, january_end = month_bounds_utc("2026-01")
        february_start, february_end = month_bounds_utc("2026-02")

        found_in_january = await db.scalars(
            select(Payment.id)
            .where(Payment.service_id == result.service.id)
            .where(Payment.paid_at >= january_start)
            .where(Payment.paid_at < january_end)
        )
        found_in_february = await db.scalars(
            select(Payment.id)
            .where(Payment.service_id == result.service.id)
            .where(Payment.paid_at >= february_start)
            .where(Payment.paid_at < february_end)
        )

        assert list(found_in_january) == [result.payment.id]
        assert list(found_in_february) == []
        assert period_of(result.payment.paid_at) == "2026-01"

    async def test_the_receipt_freezes_the_payment_date_it_documents(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """El comprobante prueba un pago: su fecha tiene que viajar en el snapshot."""
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=self.PAID_AT,
            ),
        )
        assert result.receipt.snapshot["paid_at_local"].startswith("2026-01-31T23:30:00")


class TestOverpaymentIsRefused:
    """Decisión de producto: un sobrepago es un aumento de precio sin documentar.

    RN-12 exige motivo para todo aumento, y §7.2 pone el campo del monto extra
    en la misma pantalla, a un toque de distancia.
    """

    async def test_paying_more_than_the_balance_fails_and_points_to_the_adjustment(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        with pytest.raises(BusinessRuleViolation, match="RN-12") as raised:
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    concept="Instalación de contactos",
                    agreed_total_cents=230000,
                    payment_amount_cents=240000,
                ),
            )

        assert "monto extra" in raised.value.message
        assert "$2,300.00" in raised.value.message

    async def test_the_same_amount_is_accepted_once_the_extra_work_is_registered(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La ruta que el error propone tiene que funcionar de verdad."""
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        second = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                adjustment_amount_cents=10000,
                adjustment_reason="Cambio de 2 contactos en recámara",
                payment_amount_cents=60000,
            ),
        )
        assert second.receipt.snapshot["amounts"]["balance_cents"] == 0

    async def test_a_payment_without_an_agreed_total_has_no_ceiling(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-15: sin total declarado no hay saldo contra el cual medir."""
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Cambio de apagador",
                payment_amount_cents=45000,
            ),
        )
        assert result.service.status is ServiceStatus.SETTLED
        assert result.receipt.template is ReceiptTemplate.SINGLE


class TestTheTransactionResolvesEverythingAtOnce:
    async def test_one_call_creates_service_payment_and_receipt(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                location_alias="Casa Ramírez",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
                method=PaymentMethod.TRANSFER,
            ),
        )

        assert result.service.code == "SRV-0001"
        assert result.receipt.folio == "RB-0001"
        assert result.receipt.status is ReceiptStatus.ISSUED
        assert result.receipt.public_token
        assert result.receipt.snapshot["method"] == "transfer"
        assert result.receipt.snapshot["service"]["location_alias"] == "Casa Ramírez"

    async def test_the_public_token_is_not_derived_from_the_id(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§10: prohibido derivarlo del ID, y con entropía suficiente."""
        first = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Uno", payment_amount_cents=10000),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Dos", payment_amount_cents=10000),
        )

        assert first.receipt.public_token != second.receipt.public_token
        assert len(first.receipt.public_token) >= 40
        assert str(first.receipt.id) not in first.receipt.public_token

    async def test_a_second_payment_attaches_to_the_open_service(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                payment_amount_cents=50000,
            ),
        )

        assert second.service.id == first.service.id
        assert second.receipt.snapshot["service"]["code"] == "SRV-0001"

    async def test_a_client_of_another_account_cannot_be_charged(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """AC-13: el cliente ajeno es indistinguible de uno que no existe."""
        other = await make_account(db, display_name="Cuenta ajena")
        foreign = await make_client(db, other, full_name="Cliente Ajeno")

        with pytest.raises(ValidationError, match="directorio"):
            await charge(
                scope,
                account,
                ChargeRequest(client_id=foreign.id, concept="Trabajo", payment_amount_cents=10000),
            )

    async def test_a_service_of_another_client_cannot_be_continued(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        neighbour = await make_client(db, account, full_name="Luis Mendoza", phone="5511112222")
        theirs = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=neighbour.id,
                concept="Cableado",
                agreed_total_cents=100000,
                payment_amount_cents=40000,
            ),
        )

        with pytest.raises(ValidationError, match="otro cliente"):
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    service_id=theirs.service.id,
                    payment_amount_cents=10000,
                ),
            )

    async def test_a_brand_new_job_cannot_carry_an_adjustment(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§7.2 solo ofrece el monto extra al continuar un trabajo."""
        with pytest.raises(ValidationError, match="total acordado"):
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    concept="Instalación",
                    payment_amount_cents=10000,
                    adjustment_amount_cents=5000,
                    adjustment_reason="Extra",
                ),
            )

    async def test_a_job_without_a_concept_is_rejected(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        with pytest.raises(ValidationError, match="qué trabajo"):
            await charge(
                scope,
                account,
                ChargeRequest(client_id=customer.id, concept="  ", payment_amount_cents=10000),
            )

    async def test_section17_the_capture_delay_is_measurable(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§17 mide `paid_at` contra `created_at` para saber si se captura en sitio.

        Ambos tienen que quedar registrados por separado; si `created_at` se
        copiara de `paid_at`, la señal desaparecería.
        """
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación",
                payment_amount_cents=10000,
                paid_at=datetime(2026, 1, 5, 12, 0, tzinfo=LOCAL_TZ),
            ),
        )

        assert result.payment.paid_at.astimezone(LOCAL_TZ).day == 5
        assert result.payment.created_at != result.payment.paid_at


class TestFoliosArePerAccount:
    """RN-25: secuenciales por cuenta, no globales."""

    async def test_each_account_starts_its_own_numbering(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        mine = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Uno", payment_amount_cents=10000),
        )

        other_account = await make_account(db, display_name="Otra cuenta")
        other_scope = AccountScope(db, other_account.id)
        other_client = await make_client(db, other_account, full_name="Otro Cliente")
        theirs = await charge(
            other_scope,
            other_account,
            ChargeRequest(client_id=other_client.id, concept="Uno", payment_amount_cents=10000),
        )

        assert mine.receipt.folio == "RB-0001"
        assert theirs.receipt.folio == "RB-0001", "el folio del vecino no corre el mío"
        assert mine.service.code == "SRV-0001"
        assert theirs.service.code == "SRV-0001"

    async def test_folios_advance_within_the_account(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Uno", payment_amount_cents=10000),
        )
        second = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Dos", payment_amount_cents=10000),
        )

        assert (first.receipt.folio, second.receipt.folio) == ("RB-0001", "RB-0002")
        assert (first.service.code, second.service.code) == ("SRV-0001", "SRV-0002")

    async def test_a_continued_job_does_not_burn_a_service_folio(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación",
                agreed_total_cents=100000,
                payment_amount_cents=40000,
            ),
        )
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, service_id=first.service.id, payment_amount_cents=60000
            ),
        )
        third = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Otro trabajo", payment_amount_cents=1000),
        )

        assert third.service.code == "SRV-0002"


class TestUsageCounting:
    async def test_every_issued_receipt_consumes_quota(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        for _ in range(3):
            result = await charge(
                scope,
                account,
                ChargeRequest(client_id=customer.id, concept="Trabajo", payment_amount_cents=10000),
            )

        period = period_of(result.receipt.issued_at)
        assert await issued_in_period(db, account.id, period) == 3

    async def test_the_quota_is_counted_per_account(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Trabajo", payment_amount_cents=10000),
        )
        other_account = await make_account(db, display_name="Otra cuenta")

        period = period_of(result.receipt.issued_at)
        assert await issued_in_period(db, account.id, period) == 1
        assert await issued_in_period(db, other_account.id, period) == 0


class TestServiceStatusIsPersisted:
    async def test_the_status_written_to_the_database_matches_the_balance(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """El estado no puede vivir solo en memoria: `/trabajos` filtra por él."""
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación",
                agreed_total_cents=100000,
                payment_amount_cents=100000,
            ),
        )
        await db.flush()

        stored = await db.scalar(select(Service.status).where(Service.id == result.service.id))
        assert stored is ServiceStatus.SETTLED
