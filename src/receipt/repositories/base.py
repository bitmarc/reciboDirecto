"""Acceso a datos con aislamiento por cuenta.

§10: toda consulta filtra por `account_id` y nunca se confía en un ID que venga
del cliente. AC-13 exige que un usuario de la cuenta A no pueda leer ningún
recurso de la cuenta B **por ninguna ruta**.

El mecanismo es un registro explícito: cada modelo declara cómo se ata a una
cuenta. Un modelo sin regla registrada no se puede consultar a través de este
scope — falla al arrancar la consulta, no en producción. Es deliberado: hace
imposible olvidar el filtro al agregar una tabla nueva.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import (
    Account,
    AccountCounter,
    AuthToken,
    Base,
    Client,
    CouponRedemption,
    IdempotencyKey,
    Payment,
    PlanBlockEvent,
    Receipt,
    Service,
    ServiceAdjustment,
    UsageCounter,
    User,
)

ModelT = TypeVar("ModelT", bound=Base)


@dataclass(frozen=True)
class _ScopeRule:
    """Cómo se ata un modelo a una cuenta."""

    joins: tuple[type[Base], ...]
    predicate: Callable[[int], ColumnElement[bool]]


_RULES: dict[type[Base], _ScopeRule] = {
    Account: _ScopeRule((), lambda account_id: Account.id == account_id),
    User: _ScopeRule((), lambda account_id: User.account_id == account_id),
    AccountCounter: _ScopeRule((), lambda account_id: AccountCounter.account_id == account_id),
    Client: _ScopeRule((), lambda account_id: Client.account_id == account_id),
    Service: _ScopeRule((), lambda account_id: Service.account_id == account_id),
    Receipt: _ScopeRule((), lambda account_id: Receipt.account_id == account_id),
    UsageCounter: _ScopeRule((), lambda account_id: UsageCounter.account_id == account_id),
    PlanBlockEvent: _ScopeRule((), lambda account_id: PlanBlockEvent.account_id == account_id),
    IdempotencyKey: _ScopeRule((), lambda account_id: IdempotencyKey.account_id == account_id),
    CouponRedemption: _ScopeRule((), lambda account_id: CouponRedemption.account_id == account_id),
    # Los tokens cuelgan del usuario, que es quien conoce la cuenta. El flujo de
    # recuperación de contraseña los consulta sin sesión y por eso no pasa por
    # aquí, pero cualquier lectura autenticada sí debe quedar acotada.
    AuthToken: _ScopeRule((User,), lambda account_id: User.account_id == account_id),
    # Pagos y ajustes cuelgan del servicio, que es quien conoce la cuenta.
    Payment: _ScopeRule((Service,), lambda account_id: Service.account_id == account_id),
    ServiceAdjustment: _ScopeRule((Service,), lambda account_id: Service.account_id == account_id),
}


class AccountScope:
    """Puerta de entrada a los datos de una cuenta.

    Se construye con el `account_id` de la sesión y no acepta otro. Todas las
    consultas salen ya filtradas.
    """

    def __init__(self, session: AsyncSession, account_id: int) -> None:
        self.session = session
        self.account_id = account_id

    def select(self, model: type[ModelT]) -> Select[tuple[ModelT]]:
        """`SELECT` sobre `model`, filtrado por la cuenta de la sesión."""
        rule = _RULES.get(model)
        if rule is None:
            raise LookupError(
                f"{model.__name__} no tiene regla de alcance por cuenta registrada en "
                f"receipt.repositories.base._RULES. Agrégala antes de consultarlo: "
                f"una tabla sin alcance es una fuga entre cuentas (AC-13)."
            )
        statement: Select[tuple[ModelT]] = select(model)
        for join_target in rule.joins:
            statement = statement.join(join_target)
        return statement.where(rule.predicate(self.account_id))

    async def get(self, model: type[ModelT], entity_id: int) -> ModelT | None:
        """Carga por ID **dentro de la cuenta**.

        Un recurso de otra cuenta devuelve `None`, indistinguible de uno que no
        existe: la ruta responde 404 y no filtra información (AC-12).
        """
        statement = self.select(model).where(model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(statement.limit(1))
        return result.scalars().first()

    async def list(self, statement: Select[tuple[ModelT]]) -> Sequence[ModelT]:
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def scalar(self, statement: Select[Any]) -> Any:
        return await self.session.scalar(statement)


def registered_models() -> frozenset[type[Base]]:
    """Modelos con alcance declarado. La suite lo usa para detectar tablas nuevas
    sin regla, que serían una fuga entre cuentas."""
    return frozenset(_RULES)
