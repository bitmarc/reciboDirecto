"""Trabajos y montos extra (E3).

El módulo se llama `work` y no `services` porque vive dentro del paquete
`services/`: `receipt.services.services` sería ilegible. La entidad conserva su
nombre del glosario (`Service`) y la pantalla, el suyo en español (`/trabajos`).

Aquí vive lo que RN-13 exige saber de un trabajo —total vigente, pagado y
saldo— y las tres formas de cerrar su ciclo: crear uno nuevo, agregarle un monto
extra o darlo por perdido. RN-16 deja la primera elección al emisor y el sistema
no decide por él.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, func, select

from receipt.domain.calculations import (
    balance_cents,
    current_total_cents,
    resolve_service_status,
)
from receipt.domain.clock import now_utc
from receipt.domain.enums import PaymentStatus, ServiceStatus
from receipt.models import Client, Payment, Service, ServiceAdjustment
from receipt.repositories import AccountScope
from receipt.services.errors import (
    BusinessRuleViolation,
    ConflictError,
    ValidationError,
)


@dataclass(frozen=True)
class ServiceTotals:
    """Fotografía de los montos de un trabajo, según RN-13.

    Conserva los importes uno por uno porque el motor de cobro los necesita así:
    el snapshot lista cada ajuste con su motivo (§8.3) y la elección de plantilla
    depende de cuántos pagos hay (§8.4).

    El cálculo se delega en `domain`, que es donde vive la regla y donde se
    prueba sin base de datos.
    """

    agreed_total_cents: int | None
    adjustment_cents: tuple[int, ...]
    active_payment_cents: tuple[int, ...]

    @property
    def current_total_cents(self) -> int | None:
        return current_total_cents(self.agreed_total_cents, self.adjustment_cents)

    @property
    def paid_cents(self) -> int:
        return sum(self.active_payment_cents)

    @property
    def balance_cents(self) -> int | None:
        """`None` cuando el trabajo nació sin total acordado (RN-15).

        No se puede deber contra un total que nunca se declaró, y esos trabajos
        no aportan a "por cobrar" (§9.1).
        """
        return balance_cents(
            self.agreed_total_cents, self.adjustment_cents, self.active_payment_cents
        )

    @property
    def payment_count(self) -> int:
        return len(self.active_payment_cents)


@dataclass(frozen=True)
class ServiceSummary:
    """Montos de un trabajo ya agregados en SQL, para listados.

    Es un tipo aparte y no un `ServiceTotals` a medio llenar: un listado no
    necesita los importes uno por uno, y fabricar una lista falsa para encajarlos
    daría un `payment_count` inventado que §8.4 usa para elegir plantilla.
    Expone la misma interfaz de lectura, así que las plantillas sirven a ambos.
    """

    agreed_total_cents: int | None
    adjustment_total_cents: int
    paid_cents: int
    payment_count: int

    # Las funciones de dominio suman los términos que reciban: pasar el agregado
    # en una sola posición es exactamente la misma cuenta de RN-13.

    @property
    def current_total_cents(self) -> int | None:
        return current_total_cents(self.agreed_total_cents, (self.adjustment_total_cents,))

    @property
    def balance_cents(self) -> int | None:
        return balance_cents(
            self.agreed_total_cents, (self.adjustment_total_cents,), (self.paid_cents,)
        )


async def load_totals(scope: AccountScope, service: Service) -> ServiceTotals:
    """Lee ajustes y pagos activos del trabajo, ya acotados por cuenta."""
    adjustments = await scope.session.scalars(
        select(ServiceAdjustment.amount_cents)
        .where(ServiceAdjustment.service_id == service.id)
        .order_by(ServiceAdjustment.created_at, ServiceAdjustment.id)
    )
    payments = await scope.session.scalars(
        select(Payment.amount_cents)
        .where(Payment.service_id == service.id)
        .where(Payment.status == PaymentStatus.ACTIVE)
        .order_by(Payment.paid_at, Payment.id)
    )
    return ServiceTotals(
        agreed_total_cents=service.agreed_total_cents,
        adjustment_cents=tuple(adjustments),
        active_payment_cents=tuple(payments),
    )


async def load_adjustments(scope: AccountScope, service: Service) -> Sequence[ServiceAdjustment]:
    """Ajustes en orden de captura: así es como se listan en el recibo (§8.3)."""
    return await scope.list(
        scope.select(ServiceAdjustment)
        .where(ServiceAdjustment.service_id == service.id)
        .order_by(ServiceAdjustment.created_at, ServiceAdjustment.id)
    )


def validate_concept(raw: str) -> str:
    """El concepto describe el trabajo y encabeza el cuerpo del recibo (§8.1)."""
    concept = " ".join(raw.split())
    if not concept:
        raise ValidationError("Escribe qué trabajo hiciste: es lo que dirá el recibo.")
    return concept


def validate_agreed_total(cents: int | None) -> int | None:
    """Opcional dentro del formulario de cobro (RN-15); positivo si se declara."""
    if cents is None:
        return None
    if cents <= 0:
        raise ValidationError("El total acordado debe ser mayor que cero.")
    return cents


def _optional_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = " ".join(raw.split())
    return cleaned or None


async def create_service(
    scope: AccountScope,
    *,
    client: Client,
    code: str,
    concept: str,
    location_alias: str | None = None,
    location_address: str | None = None,
    agreed_total_cents: int | None = None,
) -> Service:
    """Da de alta un trabajo.

    RN-15: un trabajo se crea **en el momento del primer pago**, nunca en una
    pantalla previa. Por eso no hay una ruta `POST /trabajos`: el único que llama
    aquí es el motor de cobro.
    """
    service = Service(
        account_id=scope.account_id,
        client_id=client.id,
        code=code,
        concept=validate_concept(concept),
        location_alias=_optional_text(location_alias),
        location_address=_optional_text(location_address),
        agreed_total_cents=validate_agreed_total(agreed_total_cents),
        status=ServiceStatus.OPEN,
    )
    scope.session.add(service)
    await scope.session.flush()
    return service


async def add_adjustment(
    scope: AccountScope,
    service: Service,
    *,
    amount_cents: int,
    reason: str,
) -> ServiceAdjustment:
    """Registra un monto extra sobre un trabajo ya iniciado (RN-12).

    El motivo es obligatorio y aparece textualmente en los recibos posteriores:
    es lo que impide que el aumento de precio se convierta, meses después, en el
    conflicto de memoria de §1.2.
    """
    if amount_cents <= 0:
        raise ValidationError("El monto extra debe ser mayor que cero.")

    cleaned_reason = " ".join(reason.split())
    if not cleaned_reason:
        raise ValidationError(
            "Escribe por qué subió el precio. Aparecerá en el recibo, y es lo que "
            "evita el reclamo de después."
        )

    if service.status is ServiceStatus.CLOSED_UNCOLLECTED:
        raise BusinessRuleViolation(
            "RN-17",
            "Este trabajo se cerró como no cobrado. Crea uno nuevo para el trabajo extra.",
        )

    if service.agreed_total_cents is None:
        # Sin total acordado no hay a qué sumar: `current_total` quedaría `None`
        # y el extra desaparecería del saldo en lugar de reclamarse (RN-13).
        raise BusinessRuleViolation(
            "RN-13",
            "Este trabajo se cobró como pago único, sin total acordado. "
            "Para cobrar algo más, crea un trabajo aparte.",
        )

    adjustment = ServiceAdjustment(
        service_id=service.id,
        amount_cents=amount_cents,
        reason=cleaned_reason,
    )
    scope.session.add(adjustment)
    await scope.session.flush()
    return adjustment


async def refresh_status(scope: AccountScope, service: Service) -> ServiceTotals:
    """Recalcula el estado del trabajo tras un pago, un ajuste o una cancelación.

    Es el punto por el que `AC-09` se sostiene: al cancelar un recibo su pago
    deja de contar y el trabajo vuelve a `open` con el saldo de antes.
    """
    totals = await load_totals(scope, service)
    service.status = resolve_service_status(
        agreed_total_cents=totals.agreed_total_cents,
        adjustment_cents=totals.adjustment_cents,
        active_payment_cents=totals.active_payment_cents,
        current_status=service.status,
    )
    await scope.session.flush()
    return totals


async def close_uncollected(scope: AccountScope, service: Service, *, reason: str) -> Service:
    """Cierra un trabajo dando su saldo por perdido (RN-17).

    El monto **no se captura**: es el saldo del trabajo en este instante. Dejar
    que el emisor escribiera otro rompería §9.1, donde el dinero perdido y el
    por cobrar son dos mitades de la misma cuenta; un `uncollected_cents`
    distinto del saldo haría que la suma de ambos dejara de dar el total vigente
    y nadie sabría en cuál de los dos está el error.

    No es reversible, y `resolve_service_status` lo respeta: un trabajo cerrado
    así no vuelve a abrirse solo aunque después entre un pago. Es una decisión
    del emisor, no un estado calculado.
    """
    cleaned = " ".join(reason.split())
    if not cleaned:
        # RN-17 lo exige y un CHECK de la base lo vuelve a exigir, por si el
        # cierre llegara alguna vez por otra vía.
        raise ValidationError(
            "Escribe por qué das este trabajo por perdido. Es lo que te va a "
            "explicar el número dentro de seis meses."
        )

    if service.status is ServiceStatus.CLOSED_UNCOLLECTED:
        raise ConflictError("Este trabajo ya estaba cerrado como no cobrado.")

    totals = await load_totals(scope, service)
    balance = totals.balance_cents

    if balance is None:
        raise BusinessRuleViolation(
            "RN-17",
            "Este trabajo se cobró como pago único, sin total acordado: "
            "no hay saldo que dar por perdido.",
        )
    if balance <= 0:
        raise BusinessRuleViolation(
            "RN-17", "Este trabajo está liquidado: no queda saldo que dar por perdido."
        )

    service.status = ServiceStatus.CLOSED_UNCOLLECTED
    service.closed_at = now_utc()
    service.closed_reason = cleaned
    service.uncollected_cents = balance
    await scope.session.flush()
    return service


def history_statement(
    scope: AccountScope,
    *,
    status: ServiceStatus | None = None,
    client_id: int | None = None,
) -> Select[tuple[Service]]:
    """Listado de `/trabajos`, filtrable por estado (§6, §7.5)."""
    statement = scope.select(Service)
    if status is not None:
        statement = statement.where(Service.status == status)
    if client_id is not None:
        statement = statement.where(Service.client_id == client_id)
    return statement.order_by(Service.created_at.desc(), Service.id.desc())


def open_services_statement(scope: AccountScope, client_id: int) -> Select[tuple[Service]]:
    """Trabajos abiertos de un cliente, para "Continuar un trabajo" (§7.2).

    Si la lista sale vacía, esa opción no se muestra en la pantalla de cobro.
    """
    return (
        scope.select(Service)
        .where(Service.client_id == client_id)
        .where(Service.status == ServiceStatus.OPEN)
        .order_by(Service.created_at.desc())
    )


async def summaries_for(
    scope: AccountScope, services: Sequence[Service]
) -> dict[int, ServiceSummary]:
    """Montos de varios trabajos en dos consultas, indexados por `service.id`.

    El listado de `/trabajos` muestra el saldo de cada renglón; calcularlo
    trabajo por trabajo convierte la pantalla en N+1 consultas en cuanto el
    emisor acumula historial.
    """
    if not services:
        return {}

    ids = [service.id for service in services]

    adjustment_rows = await scope.session.execute(
        select(ServiceAdjustment.service_id, func.sum(ServiceAdjustment.amount_cents))
        .where(ServiceAdjustment.service_id.in_(ids))
        .group_by(ServiceAdjustment.service_id)
    )
    adjustments = {service_id: int(total) for service_id, total in adjustment_rows.all()}

    payment_rows = await scope.session.execute(
        select(
            Payment.service_id,
            func.sum(Payment.amount_cents),
            func.count(Payment.id),
        )
        .where(Payment.service_id.in_(ids))
        .where(Payment.status == PaymentStatus.ACTIVE)
        .group_by(Payment.service_id)
    )
    payments = {row[0]: (int(row[1]), int(row[2])) for row in payment_rows.all()}

    summaries: dict[int, ServiceSummary] = {}
    for service in services:
        paid, count = payments.get(service.id, (0, 0))
        summaries[service.id] = ServiceSummary(
            agreed_total_cents=service.agreed_total_cents,
            adjustment_total_cents=adjustments.get(service.id, 0),
            paid_cents=paid,
            payment_count=count,
        )
    return summaries
