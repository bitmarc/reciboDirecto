"""Planes, límite mensual, instrumentación del muro y cupones (E8, §11).

Tres responsabilidades, y conviene ver por qué viven juntas:

- **Cuánto le queda a la cuenta este mes.** Lo consultan el aviso de `/cobrar`,
  la pantalla de planes y el motor de cobro. Con una sola fuente, los tres
  cuentan igual.
- **Registrar el muro cuando muerde.** §11.2 declara esta instrumentación
  obligatoria y `AC-15` exige que el evento quede registrado. El detalle que
  hace falta acertar está en `record_block`.
- **Canjear cupones.** Sin pasarela todavía (§11.4), y por eso solo se canjean
  los de meses gratis: ver `redeem_coupon`.

**Lo que este módulo no hace: cobrar.** §14.5 sigue abierta y §11.4 es
explícita —construir la pasarela antes de saber si alguien quiere pagar gasta
las horas del presupuesto más escaso—. El plan de pago se concede con cupón o a
mano desde la base de datos, que es lo que esa misma sección propone para la
validación.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import add_months, now_utc, period_of, recent_periods
from receipt.domain.enums import DiscountType, Plan
from receipt.domain.plans import (
    Allowance,
    Entitlements,
    entitlements_for,
)
from receipt.models import Account, Coupon, CouponRedemption, PlanBlockEvent
from receipt.repositories import AccountScope
from receipt.services.errors import BusinessRuleViolation, ConflictError, ValidationError
from receipt.services.usage import issued_in_period


class PlanLimitReached(BusinessRuleViolation):
    """RN-33: el plan gratuito topó su límite mensual.

    Lleva el consumo consigo porque la capa web necesita dos cosas del mismo
    error: pintar la pantalla de suscripción de §11.2 y registrar el evento con
    el ordinal del recibo que se intentó emitir.
    """

    def __init__(self, allowance: Allowance) -> None:
        super().__init__(
            "RN-33",
            f"Llegaste a los {allowance.limit} recibos de este mes en el plan gratuito.",
        )
        self.allowance = allowance


# ---------------------------------------------------------------------------
# Qué puede hacer la cuenta
# ---------------------------------------------------------------------------


def entitlements(account: Account, *, at: datetime | None = None) -> Entitlements:
    """Los permisos de §11.1 que le tocan a la cuenta ahora."""
    return entitlements_for(account.plan, account.plan_valid_until, at or now_utc())


async def allowance_of(
    session: AsyncSession, account: Account, *, at: datetime | None = None
) -> Allowance:
    """Consumo del periodo en curso frente al límite del plan."""
    moment = at or now_utc()
    period = period_of(moment)
    return Allowance(
        period=period,
        issued=await issued_in_period(session, account.id, period),
        limit=entitlements(account, at=moment).monthly_receipt_limit,
    )


async def assert_can_issue(
    session: AsyncSession, account: Account, *, at: datetime | None = None
) -> Allowance:
    """Puerta de RN-33, **antes** de escribir nada.

    Se comprueba al entrar al cobro y no al emitir el recibo: el emisor no puede
    llenar el formulario completo, con el cliente de pie enfrente, para toparse
    el muro al final. Que no quede nada a medias es además lo que hace que la
    negativa se pueda instrumentar sin ensuciar el directorio.

    La carrera —dos emisiones simultáneas que pasen las dos la comprobación—
    está acotada por la idempotencia de §6, que es de donde vendría el doble
    envío en la práctica. Con un usuario por cuenta (§4.1) no hay otro origen.
    """
    allowance = await allowance_of(session, account, at=at)
    if allowance.blocked:
        raise PlanLimitReached(allowance)
    return allowance


async def record_block(
    session: AsyncSession, account: Account, allowance: Allowance, *, at: datetime | None = None
) -> PlanBlockEvent:
    """Deja constancia de que el muro mordió (§11.2, `AC-15`).

    Se llama **desde la capa web y en una transacción propia**, no desde
    `charge()`. El cobro se rechaza y su transacción se descarta entera; si el
    evento se insertara ahí dentro, se iría con ella y el registro que §11.2
    declara obligatorio no existiría nunca. El dato que se pierde es justamente
    el que permitirá distinguir "el producto no le gustó" de "topó el muro"
    (§14.3).
    """
    event = PlanBlockEvent(
        account_id=account.id,
        occurred_at=at or now_utc(),
        period=allowance.period,
        attempted_receipt_number=allowance.attempted_number,
    )
    session.add(event)
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# Ventana de analítica (RN-35)
# ---------------------------------------------------------------------------


def visible_periods(rights: Entitlements, *, period: str, span: int) -> list[str]:
    """Los periodos que la cuenta puede mirar, del más viejo al más nuevo.

    En plan de pago son los `span` que pida la pantalla; en gratuito, los que
    permita la ventana de RN-35. Nunca devuelve una lista vacía: el mes en curso
    siempre se ve, porque RN-35 limita la analítica, no el acceso.
    """
    months = span if rights.analytics_months is None else min(span, rights.analytics_months)
    return recent_periods(period, max(months, 1))


def earliest_period(rights: Entitlements, *, at: datetime | None = None) -> str | None:
    """Periodo más antiguo consultable, o `None` si no hay límite."""
    if rights.analytics_months is None:
        return None
    return recent_periods(period_of(at or now_utc()), rights.analytics_months)[0]


def assert_period_visible(rights: Entitlements, period: str, *, at: datetime | None = None) -> None:
    """RN-35: el reporte de un mes fuera de la ventana pide plan de pago.

    No es una fuga de `AC-17`: el historial de recibos, trabajos y clientes
    sigue completo y sin condiciones. Lo que se acota es la función derivada.
    """
    floor = earliest_period(rights, at=at)
    if floor is not None and period < floor:
        raise BusinessRuleViolation(
            "RN-35",
            "El plan gratuito muestra el mes en curso. Tus recibos y trabajos "
            "siguen completos en su historial.",
        )


# ---------------------------------------------------------------------------
# Cupones (§11.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Redemption:
    """Resultado de canjear un cupón."""

    coupon: Coupon
    months_granted: int
    valid_until: datetime


def normalize_code(raw: str) -> str:
    """Los códigos se dictan por WhatsApp: ni el espacio ni la caja importan."""
    return "".join(raw.split()).upper()


async def redeem_coupon(
    scope: AccountScope, account: Account, raw_code: str, *, at: datetime | None = None
) -> Redemption:
    """Canjea un código y concede meses de plan de pago (§11.3).

    Solo se canjean los de **meses gratis**. Los de porcentaje y monto fijo
    descuentan sobre un precio, y sin pasarela no hay precio: aceptarlos sería
    guardar un descuento que nadie puede aplicar todavía, que es el andamiaje
    preparatorio que §2.2 prohíbe. El primer uso declarado en §11.3 —regalar a
    los primeros usuarios que den feedback— es exactamente `free_months`.

    Los meses se suman sobre la vigencia que ya tenga la cuenta, no sobre hoy:
    canjear un cupón a mitad de una suscripción no debe borrar lo pagado.
    """
    moment = at or now_utc()
    code = normalize_code(raw_code)
    if not code:
        raise ValidationError("Escribe el código del cupón.")

    # `Coupon` es catálogo global y no pasa por `AccountScope`: no pertenece a
    # ninguna cuenta, así que no hay `account_id` por el que filtrarlo. El canje
    # sí queda atado a la cuenta, y esa tabla sí tiene su regla de alcance.
    coupon = await scope.session.scalar(select(Coupon).where(Coupon.code == code).limit(1))
    if coupon is None or not coupon.active:
        # Un código inexistente y uno desactivado responden igual: distinguirlos
        # convierte el formulario en un oráculo para adivinar códigos válidos.
        raise ValidationError("Ese código no es válido.")

    if coupon.valid_until is not None and coupon.valid_until <= moment:
        raise ValidationError("Ese código ya venció.")

    if coupon.discount_type is not DiscountType.FREE_MONTHS:
        raise BusinessRuleViolation(
            "§11.3",
            "Ese código es un descuento sobre la suscripción y todavía no hay "
            "pantalla de pago. Guárdalo para cuando la haya.",
        )

    if coupon.max_redemptions is not None:
        used = await scope.session.scalar(
            select(func.count(CouponRedemption.id)).where(CouponRedemption.coupon_id == coupon.id)
        )
        if (used or 0) >= coupon.max_redemptions:
            raise ConflictError("Ese código ya se agotó.")

    try:
        # La restricción única es la que decide, no una consulta previa: dos
        # envíos del mismo formulario no pueden regalar el doble de meses.
        #
        # El `add` va **dentro** del savepoint, igual que en `charging.py`. Si
        # se hace fuera, el rollback del savepoint no expulsa el objeto de la
        # sesión: queda pendiente, envenena el siguiente flush y la petición que
        # venga después revienta con `PendingRollbackError` por un error que ya
        # se había atendido.
        async with scope.session.begin_nested():
            scope.session.add(
                CouponRedemption(coupon_id=coupon.id, account_id=account.id, redeemed_at=moment)
            )
            await scope.session.flush()
    except IntegrityError as exc:
        raise ConflictError("Ya canjeaste ese código.") from exc

    months = coupon.discount_value
    account.plan = Plan.PAID
    # Se suma sobre la vigencia que ya tenga, no sobre hoy: canjear un cupón a
    # mitad de una suscripción no puede borrar lo que ya estaba pagado.
    running = account.plan_valid_until
    base = running if running is not None and running > moment else moment
    account.plan_valid_until = add_months(base, months)
    await scope.session.flush()

    return Redemption(coupon=coupon, months_granted=months, valid_until=account.plan_valid_until)


async def redemptions_of(scope: AccountScope, account: Account) -> Sequence[CouponRedemption]:
    """Cupones que esta cuenta ya canjeó, para que `/planes` los muestre."""
    return await scope.list(
        scope.select(CouponRedemption)
        .where(CouponRedemption.account_id == account.id)
        .order_by(CouponRedemption.redeemed_at.desc())
    )
