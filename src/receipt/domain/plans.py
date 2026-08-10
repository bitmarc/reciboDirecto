"""Qué habilita cada plan (E8).

El hito 6 construye la pasarela, el contador de uso y el bloqueo por límite.
Aquí vive solo lo que el motor de cobro necesita hoy: RN-34 se decide **en el
momento de emitir**, porque el logo queda congelado en el snapshot del recibo.
"""

from datetime import datetime

from receipt.domain.enums import Plan


def has_paid_features(plan: Plan, plan_valid_until: datetime | None, at: datetime) -> bool:
    """¿La cuenta tiene funciones de pago vigentes en este instante?

    RN-34: el logo solo se aplica en plan de pago. Al evaluarse en la emisión y
    no en el renderizado, una cuenta que baja a gratuito conserva el logo en los
    recibos ya emitidos y deja de llevarlo en los nuevos, que es exactamente lo
    que pide la regla.
    """
    if plan is not Plan.PAID:
        return False
    return plan_valid_until is None or plan_valid_until > at
