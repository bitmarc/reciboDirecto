"""Cálculo de totales, saldos e ingreso.

Lógica pura: recibe números y devuelve números. Es donde viven RN-13, RN-14 y
las definiciones contables de §9.1, y es lo que permite probarlas sin base de
datos.
"""

from collections.abc import Sequence

from receipt.domain.enums import ReceiptTemplate, ServiceStatus


def current_total_cents(
    agreed_total_cents: int | None,
    adjustment_cents: Sequence[int],
) -> int | None:
    """RN-13: `current_total = agreed_total + Σ adjustments`.

    Devuelve `None` cuando el servicio no declaró total acordado (RN-15, pago
    único): sin total no hay total vigente que calcular.
    """
    if agreed_total_cents is None:
        return None
    return agreed_total_cents + sum(adjustment_cents)


def balance_cents(
    agreed_total_cents: int | None,
    adjustment_cents: Sequence[int],
    active_payment_cents: Sequence[int],
) -> int | None:
    """RN-13: `balance = current_total - Σ pagos activos`.

    `None` cuando no hay total acordado: no se puede deber contra un total que
    nunca se declaró. Esos servicios no aportan a "por cobrar" (§9.1).
    """
    total = current_total_cents(agreed_total_cents, adjustment_cents)
    if total is None:
        return None
    return total - sum(active_payment_cents)


def net_income_cents(amount_cents: int, material_cents: int) -> int:
    """RN-14: el ingreso contable de un pago es `amount - material`.

    El material es registro interno y **nunca** aparece en el recibo.
    """
    if material_cents < 0:
        raise ValueError("El material no puede ser negativo")
    if material_cents > amount_cents:
        raise ValueError("El material no puede exceder el monto del pago")
    return amount_cents - material_cents


def resolve_service_status(
    *,
    agreed_total_cents: int | None,
    adjustment_cents: Sequence[int],
    active_payment_cents: Sequence[int],
    current_status: ServiceStatus,
) -> ServiceStatus:
    """Estado que corresponde a un servicio tras recalcular sus montos.

    Un cierre por incobrable (RN-17) es una decisión explícita del emisor y no
    se revierte sola.

    Regla derivada, no explícita en el PRD: un servicio **sin total acordado**
    queda `settled` con su primer pago. De otro modo se quedaría `open` para
    siempre con saldo indefinido, contaminando el "por cobrar" de §9.1.
    """
    if current_status is ServiceStatus.CLOSED_UNCOLLECTED:
        return ServiceStatus.CLOSED_UNCOLLECTED

    if agreed_total_cents is None:
        return ServiceStatus.SETTLED if active_payment_cents else ServiceStatus.OPEN

    balance = balance_cents(agreed_total_cents, adjustment_cents, active_payment_cents)
    assert balance is not None  # garantizado por la rama anterior
    return ServiceStatus.SETTLED if balance <= 0 else ServiceStatus.OPEN


def choose_template(
    *,
    agreed_total_cents: int | None,
    payment_count: int,
) -> ReceiptTemplate:
    """§8.4: `detailed` si hay total acordado o más de un pago; `single` si no.

    El emisor puede forzar la otra desde la vista previa; esto es solo el valor
    por omisión.
    """
    if agreed_total_cents is not None or payment_count > 1:
        return ReceiptTemplate.DETAILED
    return ReceiptTemplate.SINGLE
