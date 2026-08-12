"""Contabilidad — §9 de extremo a extremo, con `AC-06` y `AC-07`.

Las definiciones de §9.1 se prueban sin base de datos en
`tests/unit/test_accounting.py`. Lo que se comprueba aquí es que el panel y el
reporte las respetan cuando hay pagos reales, recibos cancelados y trabajos
cerrados de por medio, y que **la pantalla y el archivo descargado dicen lo
mismo**: si divergieran, el emisor no tendría manera de saber cuál creer.
"""

import csv
import io
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import LOCAL_TZ, now_utc
from receipt.domain.enums import ServiceStatus
from receipt.models import Account, Client, Service
from receipt.repositories import AccountScope
from receipt.services.accounting import monthly_report, overview
from receipt.services.charging import ChargeRequest, charge
from receipt.services.errors import BusinessRuleViolation, ConflictError, ValidationError
from receipt.services.receipts import cancel_receipt
from receipt.services.work import close_uncollected, load_totals
from tests.conftest import csrf_token_of, visible_text
from tests.factories import make_account, make_client, make_service

pytestmark = pytest.mark.db


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    stored = await db.scalar(select(Account))
    assert stored is not None
    return stored


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


def read_csv(payload: bytes) -> list[list[str]]:
    """El CSV tal como lo lee una hoja de cálculo, con su BOM ya consumido."""
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))


# ---------------------------------------------------------------------------
# AC-06 — el material sale del ingreso y nunca del recibo
# ---------------------------------------------------------------------------


class TestAc06MaterialLeavesTheIncome:
    """Pago de $1,000 con $300 de material → ingreso del mes $700.

    La otra mitad del criterio —que el recibo muestre $1,000 sin desglose— vive
    en `test_charging.py` y en `test_public_receipt.py`, que es donde se mira el
    documento. Aquí se prueba la mitad contable.
    """

    async def test_the_month_income_is_the_payment_minus_the_material(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

        panel = await overview(scope)

        assert panel.income_cents == 70000

    async def test_the_dashboard_shows_the_net_figure_and_not_the_gross(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-14: el material es registro interno y no aparece en ningún lado."""
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

        response = await signed_in.get("/")

        text = visible_text(response)
        assert "$700.00" in text
        assert "$1,000.00" not in text, "el bruto no es el ingreso"
        assert "$300.00" not in text, "el material no se publica"

    async def test_the_report_row_keeps_the_gross_and_the_net_apart(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """El reporte sí desglosa: es del emisor, no del cliente (§9.2)."""
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

        report = await monthly_report(scope, (await overview(scope)).period)

        (row,) = report.rows
        assert (row.amount_cents, row.material_cents, row.income_cents) == (100000, 30000, 70000)
        assert report.income_cents == 70000


# ---------------------------------------------------------------------------
# AC-07 — el corte del mes es local, no UTC
# ---------------------------------------------------------------------------


class TestAc07TheMonthEndsAtMidnightInMexicoCity:
    """Un pago del 31 de enero 23:30 hora CDMX cuenta en enero, no en febrero.

    En UTC ese instante ya es el 1 de febrero. Si el panel cortara por UTC, el
    ingreso aparecería en el mes equivocado y el emisor vería su mejor día caer
    del lado que no es.
    """

    PAID_AT = datetime(2026, 1, 31, 23, 30, tzinfo=LOCAL_TZ)

    async def test_the_income_lands_in_january(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=self.PAID_AT,
            ),
        )

        january = await overview(scope, period="2026-01")
        february = await overview(scope, period="2026-02")

        assert january.income_cents == 60000
        assert february.income_cents == 0

    async def test_the_monthly_report_lands_it_in_january_too(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La pantalla y el archivo cortan por el mismo lado o no sirven."""
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=self.PAID_AT,
            ),
        )

        january = await monthly_report(scope, "2026-01")
        february = await monthly_report(scope, "2026-02")

        assert [row.paid_at for row in january.rows] == [self.PAID_AT]
        assert february.rows == ()

    async def test_the_csv_prints_the_local_date_and_not_the_utc_one(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=self.PAID_AT,
            ),
        )

        response = await signed_in.get("/reportes/mensual.csv?periodo=2026-01")

        header, row = read_csv(response.content)
        assert header[0] == "Fecha"
        assert row[0] == "2026-01-31", "en UTC sería 2026-02-01"


# ---------------------------------------------------------------------------
# Ingreso: lo cancelado sale (§9.1, RN-22)
# ---------------------------------------------------------------------------


class TestCancelledMoneyLeavesTheAccounting:
    async def test_cancelling_a_receipt_removes_its_payment_from_the_income(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=80000,
            ),
        )
        assert (await overview(scope)).income_cents == 80000

        await cancel_receipt(scope, result.receipt, reason="Cobro duplicado")

        assert (await overview(scope)).income_cents == 0

    async def test_the_cancelled_row_stays_in_the_report_with_a_net_of_zero(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-22: visible en el historial y fuera de la contabilidad.

        Las dos cosas a la vez. El renglón conserva su monto —es el registro de
        que ese cobro existió— y su columna contable va en cero, para que sumar
        la columna dé exactamente el ingreso del panel.
        """
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=80000,
            ),
        )
        await cancel_receipt(scope, result.receipt, reason="Cobro duplicado")

        report = await monthly_report(scope, (await overview(scope)).period)

        (row,) = report.rows
        assert row.amount_cents == 80000, "el registro histórico se conserva"
        assert row.income_cents == 0
        assert not row.counted
        assert report.income_cents == 0

    async def test_the_csv_income_column_adds_up_to_the_dashboard_figure(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """La prueba que justifica la decisión de escribir 0 y no el monto.

        El emisor abre el archivo y selecciona la columna. Lo que le diga Excel
        tiene que ser lo mismo que le dijo el panel.
        """
        kept = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Uno", payment_amount_cents=50000),
        )
        dropped = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Dos", payment_amount_cents=30000),
        )
        await cancel_receipt(scope, dropped.receipt, reason="Se registró de más")
        assert kept.receipt.folio

        panel = await overview(scope)
        response = await signed_in.get(f"/reportes/mensual.csv?periodo={panel.period}")

        header, *rows = read_csv(response.content)
        column = header.index("Ingreso neto")
        total_cents = sum(round(float(row[column]) * 100) for row in rows)

        assert len(rows) == 2, "los dos pagos aparecen"
        assert total_cents == panel.income_cents == 50000


# ---------------------------------------------------------------------------
# Por cobrar y quién me debe (§7.4, §9.1)
# ---------------------------------------------------------------------------


class TestWhoOwesMe:
    async def test_the_receivable_is_the_sum_of_open_balances(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        panel = await overview(scope)

        assert panel.receivable_cents == 50000
        assert [debtor.balance_cents for debtor in panel.debtors] == [50000]

    async def test_a_job_without_an_agreed_total_owes_nothing(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-15: no se puede deber contra un total que nunca se declaró."""
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, concept="Cambio de apagador", payment_amount_cents=45000
            ),
        )

        panel = await overview(scope)

        assert panel.receivable_cents == 0
        assert panel.debtors == ()

    async def test_debtors_are_grouped_by_client_and_the_oldest_comes_first(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§7.4: ordenada por antigüedad descendente.

        La antigüedad se cuenta desde el alta del trabajo más viejo con saldo:
        un abono reciente no rejuvenece un adeudo de hace meses.
        """
        neighbour = await make_client(db, account, full_name="Luis Mendoza", phone="5511112222")
        recent = await make_service(
            db,
            account,
            customer,
            code="SRV-9001",
            concept="Trabajo reciente",
            agreed_total_cents=100000,
            created_at=now_utc() - timedelta(days=3),
        )
        ancient = await make_service(
            db,
            account,
            neighbour,
            code="SRV-9002",
            concept="Trabajo viejo",
            agreed_total_cents=200000,
            created_at=now_utc() - timedelta(days=95),
        )
        await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, service_id=recent.id, payment_amount_cents=40000),
        )
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=neighbour.id, service_id=ancient.id, payment_amount_cents=50000
            ),
        )

        panel = await overview(scope)

        assert [debtor.client.full_name for debtor in panel.debtors] == [
            "Luis Mendoza",
            "Ana Ramírez",
        ]
        assert [debtor.balance_cents for debtor in panel.debtors] == [150000, 60000]
        assert panel.debtors[0].days == 95
        assert panel.receivable_cents == 210000

    async def test_the_dashboard_prints_the_age_in_words(
        self,
        signed_in: AsyncClient,
        db: AsyncSession,
        scope: AccountScope,
        account: Account,
        customer: Client,
    ) -> None:
        """«3 meses» pesa distinto que «hace 95 días» cuando hay que marcarle."""
        service = await make_service(
            db,
            account,
            customer,
            code="SRV-9003",
            agreed_total_cents=100000,
            created_at=now_utc() - timedelta(days=95),
        )
        await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, service_id=service.id, payment_amount_cents=40000),
        )

        response = await signed_in.get("/")

        assert "3 meses" in visible_text(response)

    async def test_an_account_without_debtors_says_so(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/")
        assert "Nadie te debe nada" in visible_text(response)


# ---------------------------------------------------------------------------
# RN-17 — cierre por incobrable
# ---------------------------------------------------------------------------


@pytest.fixture
async def owing(scope: AccountScope, account: Account, customer: Client):
    """Un trabajo de $2,300 con $1,800 abonados: quedan $500 sin cobrar."""
    return await charge(
        scope,
        account,
        ChargeRequest(
            client_id=customer.id,
            concept="Instalación de contactos",
            agreed_total_cents=230000,
            payment_amount_cents=180000,
        ),
    )


class TestClosingAsUncollected:
    async def test_the_balance_becomes_the_amount_given_up(
        self, scope: AccountScope, owing
    ) -> None:
        await close_uncollected(scope, owing.service, reason="El cliente se mudó y no contesta")

        assert owing.service.status is ServiceStatus.CLOSED_UNCOLLECTED
        assert owing.service.uncollected_cents == 50000
        assert owing.service.closed_reason == "El cliente se mudó y no contesta"
        assert owing.service.closed_at is not None

    async def test_the_amount_is_not_captured_by_hand(self, scope: AccountScope, owing) -> None:
        """No hay campo de monto y no debe haberlo.

        §9.1 hace del dinero perdido y del por cobrar dos mitades de la misma
        cuenta. Un `uncollected_cents` distinto del saldo las descuadraría y
        nadie sabría en cuál de las dos está el error.
        """
        totals_before = await load_totals(scope, owing.service)
        await close_uncollected(scope, owing.service, reason="Incobrable")

        assert owing.service.uncollected_cents == totals_before.balance_cents

    async def test_rn17_a_closure_without_a_reason_is_refused(
        self, db: AsyncSession, scope: AccountScope, owing
    ) -> None:
        """Y no escribe nada al fallar.

        La ruta cuenta con eso: al rechazar un cierre vuelve a renderizar la
        ficha sin deshacer la transacción, porque no hay nada que deshacer. Si
        alguien reordenara el servicio para asignar antes de validar, la ficha
        empezaría a guardar lo que acaba de rechazar y esta prueba es la que se
        entera.
        """
        with pytest.raises(ValidationError, match="por qué"):
            await close_uncollected(scope, owing.service, reason="   ")

        assert owing.service.status is ServiceStatus.OPEN
        await db.flush()
        stored = await db.scalar(select(Service.status).where(Service.id == owing.service.id))
        assert stored is ServiceStatus.OPEN, "la validación no dejó rastro en la base"

    async def test_a_settled_job_has_nothing_to_give_up(
        self, scope: AccountScope, account: Account, customer: Client
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

        with pytest.raises(BusinessRuleViolation, match="RN-17"):
            await close_uncollected(scope, settled.service, reason="Ya no lo cobro")

    async def test_a_job_without_an_agreed_total_cannot_be_closed(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        single = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, concept="Cambio de apagador", payment_amount_cents=45000
            ),
        )

        with pytest.raises(BusinessRuleViolation, match="pago único"):
            await close_uncollected(scope, single.service, reason="Ya no lo cobro")

    async def test_closing_twice_is_refused(self, scope: AccountScope, owing) -> None:
        await close_uncollected(scope, owing.service, reason="Incobrable")

        with pytest.raises(ConflictError, match="ya estaba cerrado"):
            await close_uncollected(scope, owing.service, reason="Otra vez")

    async def test_the_lost_money_leaves_the_receivable_and_shows_apart(
        self, scope: AccountScope, owing
    ) -> None:
        """RN-17 y §9.1: sale de "por cobrar" y **nunca** se mezcla con el ingreso."""
        before = await overview(scope)
        assert before.receivable_cents == 50000

        await close_uncollected(scope, owing.service, reason="El cliente se mudó")
        after = await overview(scope)

        assert after.receivable_cents == 0
        assert after.uncollected_cents == 50000
        assert after.income_cents == before.income_cents == 180000, "lo cobrado sigue cobrado"

    async def test_the_issued_receipt_is_untouched(self, scope: AccountScope, owing) -> None:
        frozen = dict(owing.receipt.snapshot)

        await close_uncollected(scope, owing.service, reason="El cliente se mudó")

        assert owing.receipt.snapshot == frozen
        assert owing.receipt.status.value == "issued"

    async def test_the_monthly_report_lists_what_was_given_up(
        self, scope: AccountScope, owing
    ) -> None:
        await close_uncollected(scope, owing.service, reason="El cliente se mudó")

        report = await monthly_report(scope, (await overview(scope)).period)

        assert report.uncollected_cents == 50000
        assert [service.code for service in report.uncollected_services] == ["SRV-0001"]


class TestClosingFromTheWeb:
    async def test_the_form_closes_the_job(
        self, signed_in: AsyncClient, scope: AccountScope, owing
    ) -> None:
        response = await signed_in.post(
            f"/trabajos/{owing.service.id}/cerrar",
            data={
                "reason": "El cliente se mudó y ya no contesta",
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert owing.service.status is ServiceStatus.CLOSED_UNCOLLECTED
        assert owing.service.uncollected_cents == 50000

    async def test_closing_without_a_reason_returns_the_form_with_the_error(
        self, signed_in: AsyncClient, scope: AccountScope, owing
    ) -> None:
        response = await signed_in.post(
            f"/trabajos/{owing.service.id}/cerrar",
            data={"reason": "", "csrf_token": csrf_token_of(signed_in)},
        )

        assert response.status_code == 400
        assert "por qué das este trabajo por perdido" in visible_text(response)
        assert owing.service.status is ServiceStatus.OPEN

    async def test_it_requires_the_csrf_token(self, signed_in: AsyncClient, owing) -> None:
        response = await signed_in.post(
            f"/trabajos/{owing.service.id}/cerrar", data={"reason": "Sin token"}
        )
        assert response.status_code == 403

    async def test_a_closed_job_no_longer_offers_the_form(
        self, signed_in: AsyncClient, scope: AccountScope, owing
    ) -> None:
        await close_uncollected(scope, owing.service, reason="El cliente se mudó")

        response = await signed_in.get(f"/trabajos/{owing.service.id}")

        text = visible_text(response)
        assert "Cerrar como no cobrado" not in text
        assert "El cliente se mudó" in text, "el motivo queda a la vista"


# ---------------------------------------------------------------------------
# Las pantallas (§7.4, §9.2)
# ---------------------------------------------------------------------------


class TestTheDashboardScreen:
    async def test_it_answers_the_two_questions_of_section_7_4(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        response = await signed_in.get("/")

        text = visible_text(response)
        assert "Ganado en" in text
        assert "$1,800.00" in text, "cuánto llevo ganado este mes"
        assert "Por cobrar" in text
        assert "$500.00" in text, "cuánto me falta por cobrar"
        assert "Ana Ramírez" in text, "quién me debe"

    async def test_the_six_month_chart_is_drawn_without_javascript(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """§10 y §3.2: la CSP no admite scripts de terceros, y no hacen falta."""
        await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Trabajo", payment_amount_cents=50000),
        )

        response = await signed_in.get("/")

        assert response.text.count('class="chart__row') == 6
        assert 'class="chart__bar" style="width: 100%"' in response.text
        assert "<script" not in response.text

    async def test_a_brand_new_account_does_not_divide_by_zero(
        self, signed_in: AsyncClient
    ) -> None:
        """El primer día no hay nada que graficar, y eso no es un error."""
        response = await signed_in.get("/")

        assert response.status_code == 200
        assert "$0.00" in visible_text(response)

    async def test_another_account_income_is_invisible(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        """AC-13, en la pantalla que suma dinero de toda la cuenta."""
        other = await make_account(db, display_name="Cuenta ajena")
        other_client = await make_client(db, other, full_name="Cliente Ajeno")
        await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Trabajo ajeno", payment_amount_cents=999900
            ),
        )

        response = await signed_in.get("/")

        text = visible_text(response)
        assert "$9,999.00" not in text
        assert "Cliente Ajeno" not in text


class TestTheMonthlyReportScreen:
    async def test_it_lists_the_payments_of_the_month(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=180000,
            ),
        )

        response = await signed_in.get("/reportes")

        text = visible_text(response)
        assert "RB-0001" in text
        assert "SRV-0001" in text
        assert "Ana Ramírez" in text
        assert "Instalación de contactos" in text
        assert "Efectivo" in text

    async def test_an_invented_period_falls_back_to_the_current_month(
        self, signed_in: AsyncClient
    ) -> None:
        """El periodo viaja en la barra de direcciones: no puede ser un 500."""
        response = await signed_in.get("/reportes?periodo=no-es-un-mes")
        assert response.status_code == 200

    async def test_an_empty_month_says_so(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reportes?periodo=2020-01")
        assert "No hubo pagos en este mes" in visible_text(response)


class TestTheCsv:
    @pytest.fixture(autouse=True)
    async def a_payment(self, scope: AccountScope, account: Account, customer: Client):
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

    async def test_it_downloads_as_a_file_named_after_the_period(
        self, signed_in: AsyncClient, scope: AccountScope
    ) -> None:
        period = (await overview(scope)).period

        response = await signed_in.get(f"/reportes/mensual.csv?periodo={period}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert f'filename="ingresos-{period}.csv"' in response.headers["content-disposition"]

    async def test_it_carries_the_byte_order_mark_that_excel_needs(
        self, signed_in: AsyncClient
    ) -> None:
        """Sin BOM, Excel lee «Ramírez» como «RamÃ­rez»."""
        response = await signed_in.get("/reportes/mensual.csv")

        assert response.content.startswith(b"\xef\xbb\xbf")
        assert "Ana Ramírez" in response.content.decode("utf-8-sig")

    async def test_the_columns_are_the_ones_section_9_2_asks_for(
        self, signed_in: AsyncClient
    ) -> None:
        response = await signed_in.get("/reportes/mensual.csv")

        header, *_ = read_csv(response.content)
        assert header == [
            "Fecha",
            "Folio",
            "Cliente",
            "Trabajo",
            "Concepto",
            "Monto",
            "Material",
            "Ingreso neto",
            "Método",
            "Estado",
        ]

    async def test_the_amounts_enter_as_numbers_not_as_text(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reportes/mensual.csv")

        _, row = read_csv(response.content)
        assert row[5:8] == ["1000.00", "300.00", "700.00"]

    async def test_the_row_identifies_the_receipt_and_the_job(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reportes/mensual.csv")

        _, row = read_csv(response.content)
        assert row[1] == "RB-0001"
        assert row[2] == "Ana Ramírez"
        assert row[3] == "SRV-0001"
        assert row[8:10] == ["Efectivo", "Activo"]

    async def test_the_client_name_is_the_one_frozen_in_the_receipt(
        self, signed_in: AsyncClient, scope: AccountScope, customer: Client
    ) -> None:
        """AC-03: el reporte no puede contradecir al papel que el cliente tiene.

        Si el emisor renombra al cliente después, el recibo sigue diciendo el
        nombre viejo. Un reporte con el nombre nuevo junto al mismo folio
        pondría en duda el comprobante.
        """
        from receipt.services.clients import update_client

        await update_client(
            scope, customer, full_name="Ana María Ramírez Torres", phone="5598765432"
        )

        response = await signed_in.get("/reportes/mensual.csv")

        _, row = read_csv(response.content)
        assert row[2] == "Ana Ramírez"

    async def test_it_only_carries_the_own_account_payments(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        other = await make_account(db, display_name="Cuenta ajena")
        other_client = await make_client(db, other, full_name="Cliente Ajeno")
        await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Trabajo ajeno", payment_amount_cents=999900
            ),
        )

        response = await signed_in.get("/reportes/mensual.csv")

        _, *rows = read_csv(response.content)
        assert len(rows) == 1
        assert "Cliente Ajeno" not in response.content.decode("utf-8-sig")


class TestTheEnglishSurfaceOfSection6:
    """§6 documenta rutas en inglés; §7 nombra las pantallas en español.

    Las dos secciones tienen que resolver, y la redirección no puede perder el
    periodo por el camino.
    """

    async def test_dashboard_redirects_to_the_panel(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/dashboard", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/"

    async def test_reports_monthly_keeps_its_period(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reports/monthly?period=2026-01", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/reportes?period=2026-01"

    async def test_the_documented_url_lands_on_the_right_month(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=60000,
                paid_at=datetime(2026, 1, 15, 12, 0, tzinfo=LOCAL_TZ),
            ),
        )

        response = await signed_in.get("/reports/monthly?period=2026-01", follow_redirects=True)

        assert response.status_code == 200
        assert "enero de 2026" in visible_text(response).lower()
        assert "$600.00" in visible_text(response)

    async def test_reports_monthly_csv_downloads(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reports/monthly.csv?period=2026-01", follow_redirects=True)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="ingresos-2026-01.csv"' in response.headers["content-disposition"]
