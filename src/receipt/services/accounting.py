"""Contabilidad: panel, comparación mensual y reporte (E7, §9).

Este módulo **solo lee**. Nada de aquí escribe, y esa es su única garantía
interesante: la contabilidad no puede corregir un dato que no le gusta, solo
reflejarlo. Lo que sí escribe —el cierre por incobrable de RN-17— vive en
`services.work`, junto al resto del ciclo de vida del trabajo.

Dos decisiones de forma que conviene explicar, porque a primera vista parecen
ingenuas:

**Se agrega en Python, no en SQL.** Las consultas traen entidades por
`AccountScope` y las sumas se hacen aquí. Un `SUM()` con `GROUP BY
date_trunc(... AT TIME ZONE ...)` sería una segunda implementación del corte
contable de §3.4, escrita en otro lenguaje y sin las pruebas de `AC-07`.
Prefiero que el corte lo decida `period_of()`, que es el que ya está probado.
El volumen lo permite con holgura: el usuario de validación hace 15–20 pagos al
mes, así que la ventana de seis meses del panel son ~120 filas.

**El ingreso se decide con `counts_as_income`**, la misma función pura que usa
el CSV. §9.1 depende de dos estados —el del pago y el de su recibo— y tener esa
condición escrita dos veces es la manera más fácil de que el panel y el archivo
descargado dejen de coincidir.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from receipt.domain.accounting import SERIES_MONTHS, bar_percentages, counts_as_income
from receipt.domain.calculations import net_income_cents
from receipt.domain.clock import (
    current_period,
    days_between,
    format_month_es,
    month_bounds_utc,
    now_utc,
    period_of,
    recent_periods,
    shift_period,
)
from receipt.domain.enums import PaymentMethod, PaymentStatus, ReceiptStatus, ServiceStatus
from receipt.models import Client, Payment, Receipt, Service
from receipt.repositories import AccountScope
from receipt.services.receipts import by_payment
from receipt.services.work import history_statement, summaries_for


@dataclass(frozen=True)
class MonthBar:
    """Un mes de la comparación de §7.4."""

    period: str
    label: str
    income_cents: int
    percentage: int
    is_current: bool


@dataclass(frozen=True)
class Debtor:
    """Un renglón de "quién me debe" (§7.4).

    Agrupa por cliente y no por trabajo: al emisor le va a marcar una persona,
    no un folio. `days` se cuenta desde el alta del trabajo **más antiguo** con
    saldo, que es lo que hace incómoda la llamada; un abono parcial reciente no
    rejuvenece un adeudo de hace tres meses.
    """

    client: Client
    balance_cents: int
    oldest_at: datetime
    days: int
    open_services: int


@dataclass(frozen=True)
class Overview:
    """Lo que responde el panel, en el orden en que §7.4 lo pide."""

    period: str
    label: str
    income_cents: int
    receivable_cents: int
    uncollected_cents: int
    months: tuple[MonthBar, ...]
    debtors: tuple[Debtor, ...]


@dataclass(frozen=True)
class ReportRow:
    """Una fila del reporte mensual: un pago (§9.2).

    `income_cents` va en 0 cuando el pago no cuenta —cancelado, o con su recibo
    cancelado—, y `amount_cents` conserva el monto real. Así el renglón sigue
    siendo el registro histórico que exige RN-22 ("visible en el historial y
    fuera de la contabilidad") y, a la vez, sumar la columna contable en una
    hoja de cálculo da exactamente el ingreso que muestra el panel.
    """

    paid_at: datetime
    folio: str | None
    client_name: str
    service_code: str
    concept: str
    amount_cents: int
    material_cents: int
    income_cents: int
    method: PaymentMethod
    payment_status: PaymentStatus
    receipt_status: ReceiptStatus | None

    @property
    def counted(self) -> bool:
        return counts_as_income(
            payment_status=self.payment_status,
            receipt_status=self.receipt_status,
        )


@dataclass(frozen=True)
class MonthlyReport:
    """El reporte de un periodo, tal como lo ven la pantalla y el CSV."""

    period: str
    label: str
    rows: tuple[ReportRow, ...]
    income_cents: int
    material_cents: int
    uncollected_cents: int
    uncollected_services: tuple[Service, ...]

    @property
    def previous_period(self) -> str:
        return shift_period(self.period, -1)

    @property
    def next_period(self) -> str | None:
        """`None` estando en el mes en curso: no hay reporte del futuro."""
        following = shift_period(self.period, 1)
        return following if following <= current_period() else None


# ---------------------------------------------------------------------------
# Ingreso (§9.1)
# ---------------------------------------------------------------------------


async def _payments_between(
    scope: AccountScope, start: datetime, end: datetime
) -> Sequence[Payment]:
    """Pagos de la cuenta con `paid_at` en `[start, end)`.

    Los límites llegan ya convertidos desde el calendario local (`month_bounds_utc`),
    que es lo que hace que el corte caiga donde el emisor lo espera (`AC-07`).
    """
    return await scope.list(
        scope.select(Payment)
        .where(Payment.paid_at >= start)
        .where(Payment.paid_at < end)
        .order_by(Payment.paid_at, Payment.id)
    )


async def _receipts_of(scope: AccountScope, payments: Sequence[Payment]) -> dict[int, Receipt]:
    return await by_payment(scope, [payment.id for payment in payments])


def _income_of(payment: Payment, receipt: Receipt | None) -> int:
    """Lo que este pago aporta al ingreso del mes. Cero si no cuenta (§9.1)."""
    if not counts_as_income(
        payment_status=payment.status,
        receipt_status=receipt.status if receipt else None,
    ):
        return 0
    return net_income_cents(payment.amount_cents, payment.material_cents)


async def income_series(
    scope: AccountScope, *, period: str, months: int = SERIES_MONTHS
) -> list[MonthBar]:
    """Ingreso de los `months` periodos que terminan en `period` (§7.4).

    Una sola consulta cubre toda la ventana y el reparto por mes lo hace
    `period_of()` en hora de CDMX. El mes en curso es el último de la lista, así
    que el panel obtiene de aquí sus dos datos: la barra más reciente y la cifra
    grande de "ganado este mes".
    """
    periods = recent_periods(period, months)
    start, _ = month_bounds_utc(periods[0])
    _, end = month_bounds_utc(periods[-1])

    payments = await _payments_between(scope, start, end)
    receipts = await _receipts_of(scope, payments)

    totals = dict.fromkeys(periods, 0)
    for payment in payments:
        bucket = period_of(payment.paid_at)
        if bucket in totals:  # la ventana lo garantiza; el guardo evita un 500
            totals[bucket] += _income_of(payment, receipts.get(payment.id))

    percentages = bar_percentages([totals[key] for key in periods])
    return [
        MonthBar(
            period=key,
            label=format_month_es(key),
            income_cents=totals[key],
            percentage=percentage,
            is_current=key == period,
        )
        for key, percentage in zip(periods, percentages, strict=True)
    ]


# ---------------------------------------------------------------------------
# Por cobrar y dinero perdido (§9.1)
# ---------------------------------------------------------------------------


async def receivables(scope: AccountScope) -> tuple[list[Debtor], int]:
    """ "Quién me debe" y el total por cobrar (§7.4, §9.1).

    Solo trabajos `open`: los `closed_uncollected` salen del "por cobrar" por
    RN-17, y los que nunca declararon total no tienen saldo que reclamar
    (RN-15), así que tampoco aportan.
    """
    services = await scope.list(history_statement(scope, status=ServiceStatus.OPEN))
    summaries = await summaries_for(scope, services)

    owed: dict[int, list[tuple[Service, int]]] = {}
    for service in services:
        balance = summaries[service.id].balance_cents
        if balance is None or balance <= 0:
            continue
        owed.setdefault(service.client_id, []).append((service, balance))

    if not owed:
        return [], 0

    clients = {
        client.id: client
        for client in await scope.list(scope.select(Client).where(Client.id.in_(owed)))
    }

    at = now_utc()
    debtors = []
    for client_id, rows in owed.items():
        client = clients.get(client_id)
        if client is None:  # pragma: no cover - la FK lo impide
            continue
        oldest_at = min(service.created_at for service, _ in rows)
        debtors.append(
            Debtor(
                client=client,
                balance_cents=sum(balance for _, balance in rows),
                oldest_at=oldest_at,
                days=days_between(oldest_at, at),
                open_services=len(rows),
            )
        )

    # §7.4: por antigüedad descendente, es decir, el adeudo más viejo primero.
    debtors.sort(key=lambda debtor: (debtor.oldest_at, debtor.client.full_name))
    return debtors, sum(debtor.balance_cents for debtor in debtors)


async def uncollected_services(
    scope: AccountScope, *, period: str | None = None
) -> Sequence[Service]:
    """Trabajos cerrados como no cobrados (RN-17).

    Con `period`, solo los cerrados dentro de ese mes: el reporte mensual habla
    de lo que se dio por perdido en el periodo, no de lo acumulado de siempre.
    """
    statement = scope.select(Service).where(Service.status == ServiceStatus.CLOSED_UNCOLLECTED)
    if period is not None:
        start, end = month_bounds_utc(period)
        statement = statement.where(Service.closed_at >= start).where(Service.closed_at < end)
    return await scope.list(statement.order_by(Service.closed_at, Service.id))


def _uncollected_total(services: Sequence[Service]) -> int:
    return sum(service.uncollected_cents or 0 for service in services)


def _frozen_client_name(receipt: Receipt | None) -> str | None:
    """Nombre del cliente tal como quedó congelado en el recibo (§4.3).

    Se comprueba la forma en lugar de confiar en ella: el `snapshot` es JSONB y
    su versión puede cambiar (`SNAPSHOT_VERSION`). Un reporte que reventara por
    un recibo viejo con otra estructura sería un fallo desproporcionado.
    """
    if receipt is None:
        return None
    block = receipt.snapshot.get("client")
    if not isinstance(block, dict):
        return None
    name = block.get("full_name")
    return name if isinstance(name, str) and name else None


# ---------------------------------------------------------------------------
# Panel (§7.4)
# ---------------------------------------------------------------------------


async def overview(scope: AccountScope, *, period: str | None = None) -> Overview:
    """El panel completo. `period` solo se pasa desde las pruebas."""
    current = period or current_period()
    months = await income_series(scope, period=current)
    debtors, receivable_cents = await receivables(scope)

    return Overview(
        period=current,
        label=format_month_es(current),
        income_cents=months[-1].income_cents,
        receivable_cents=receivable_cents,
        # §9.1: el dinero perdido se muestra por separado, nunca mezclado con el
        # ingreso. Aquí va el acumulado de siempre, porque la pregunta que
        # responde es "cuánto he dado por perdido", no "cuánto este mes".
        uncollected_cents=_uncollected_total(await uncollected_services(scope)),
        months=tuple(months),
        debtors=tuple(debtors),
    )


# ---------------------------------------------------------------------------
# Reporte mensual (§9.2)
# ---------------------------------------------------------------------------


async def monthly_report(scope: AccountScope, period: str) -> MonthlyReport:
    """Una fila por pago del periodo, más los totales que la pantalla resume.

    El nombre del cliente sale del **snapshot** del recibo cuando lo hay, y de
    la tabla viva cuando el pago no llegó a tener comprobante. Es a propósito:
    si el emisor renombró al cliente después, `AC-03` garantiza que el recibo no
    cambió, y un reporte que contradijera al papel que el cliente tiene en la
    mano sería peor que inútil.
    """
    start, end = month_bounds_utc(period)
    payments = await _payments_between(scope, start, end)
    receipts = await _receipts_of(scope, payments)

    services = {
        service.id: service
        for service in await scope.list(
            scope.select(Service).where(Service.id.in_({p.service_id for p in payments}))
        )
    }
    clients = {
        client.id: client
        for client in await scope.list(
            scope.select(Client).where(
                Client.id.in_({s.client_id for s in services.values()}),
            )
        )
    }

    rows = []
    for payment in payments:
        service = services.get(payment.service_id)
        if service is None:  # pragma: no cover - la FK lo impide
            continue
        receipt = receipts.get(payment.id)
        client = clients.get(service.client_id)

        rows.append(
            ReportRow(
                paid_at=payment.paid_at,
                folio=receipt.folio if receipt else None,
                client_name=_frozen_client_name(receipt) or (client.full_name if client else ""),
                service_code=service.code,
                concept=service.concept,
                amount_cents=payment.amount_cents,
                material_cents=payment.material_cents,
                income_cents=_income_of(payment, receipt),
                method=payment.method,
                payment_status=payment.status,
                receipt_status=receipt.status if receipt else None,
            )
        )

    closed = await uncollected_services(scope, period=period)
    return MonthlyReport(
        period=period,
        label=format_month_es(period),
        rows=tuple(rows),
        income_cents=sum(row.income_cents for row in rows),
        # Material solo de lo que cuenta: el de un pago cancelado no es un gasto
        # que el emisor tenga que descontar de nada.
        material_cents=sum(row.material_cents for row in rows if row.counted),
        uncollected_cents=_uncollected_total(closed),
        uncollected_services=tuple(closed),
    )
