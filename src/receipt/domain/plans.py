"""Qué habilita cada plan (E8, §11.1).

La tabla de §11.1 vive aquí, en un solo lugar y en forma de datos. Cada vez que
una pantalla decide por su cuenta si algo es de pago, esa tabla se duplica y
empieza a divergir: el panel diría una cosa, `/planes` otra y el emisor no
sabría cuál creer.

Todo es puro: recibe el plan y un instante, devuelve permisos. Que la cuenta
esté vencida no se corrige mutando la fila —no hay proceso que recorra cuentas
cada noche— sino evaluando `plan_valid_until` cada vez que se pregunta. Es lo
que ya hacía RN-34 con el logo y lo que hace que un plan caducado deje de dar
funciones sin que nadie tenga que enterarse a tiempo.
"""

from dataclasses import dataclass
from datetime import datetime

from receipt.domain.enums import Plan

FREE_MONTHLY_RECEIPTS = 10
"""§11.1: hasta 10 recibos al mes en gratuito, luego bloqueo (RN-33)."""

FREE_ANALYTICS_MONTHS = 1
"""Ventana de analítica del plan gratuito (RN-35).

§11.1 la deja como "ventana limitada" sin fijar el tamaño; el responsable del
producto la fijó en 1 mes. Solo acota las funciones **derivadas** —la gráfica y
el reporte—: el historial de recibos, trabajos y clientes se conserva completo
(`AC-17`), y las ligas públicas ya emitidas siguen resolviendo (RN-31).
"""

FREE_WARNING_FROM = 8
"""A partir de este consumo, `/cobrar` avisa cuánto queda.

§1.4 pone al emisor de pie, con el efectivo en la mano. Enterarse del muro en
ese momento no produce una conversión, produce un abandono (§14.3). El aviso
llega antes, cuando todavía puede decidir con calma.
"""


@dataclass(frozen=True)
class Entitlements:
    """Lo que una cuenta puede hacer en un instante dado.

    `monthly_receipt_limit` en `None` significa ilimitado, no cero.
    """

    paid: bool
    monthly_receipt_limit: int | None
    analytics_months: int | None
    can_download_report: bool
    can_use_logo: bool


_PAID = Entitlements(
    paid=True,
    monthly_receipt_limit=None,
    analytics_months=None,
    can_download_report=True,
    can_use_logo=True,
)

_FREE = Entitlements(
    paid=False,
    monthly_receipt_limit=FREE_MONTHLY_RECEIPTS,
    analytics_months=FREE_ANALYTICS_MONTHS,
    # Decisión del responsable del producto: el CSV es función de pago. Va un
    # paso más allá de §9.2, que dice "la consulta se limita" y no que
    # desaparezca; queda anotado aquí para que se pueda revisar con datos.
    can_download_report=False,
    can_use_logo=False,
)


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


def entitlements_for(plan: Plan, plan_valid_until: datetime | None, at: datetime) -> Entitlements:
    """La fila de §11.1 que le toca a esta cuenta ahora mismo."""
    return _PAID if has_paid_features(plan, plan_valid_until, at) else _FREE


@dataclass(frozen=True)
class Allowance:
    """Cuánto le queda a la cuenta este mes (§11.2).

    Es lo que necesitan a la vez el aviso de `/cobrar`, la pantalla de planes y
    el bloqueo del motor de cobro. Uno solo, para que los tres cuenten igual.
    """

    period: str
    issued: int
    limit: int | None

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(self.limit - self.issued, 0)

    @property
    def blocked(self) -> bool:
        """RN-33: al llegar al límite se bloquea la emisión."""
        return self.limit is not None and self.issued >= self.limit

    @property
    def warning(self) -> bool:
        """Cerca del muro, pero todavía se puede emitir."""
        return not self.blocked and self.limit is not None and self.issued >= FREE_WARNING_FROM

    @property
    def attempted_number(self) -> int:
        """Ordinal del recibo que se está intentando emitir.

        §11.2 lo exige en el registro del bloqueo: con 10 emitidos, el que topa
        el muro es el 11.
        """
        return self.issued + 1
