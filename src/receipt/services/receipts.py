"""Emisión y cancelación de recibos (E4).

Dos reglas gobiernan este módulo y conviene tenerlas juntas:

- **RN-10** — un recibo emitido nunca se recalcula. Todo su contenido se congela
  en `snapshot` al emitirlo y nada de aquí lo vuelve a tocar. Lo único mutable
  es el estado de cancelación.
- **RN-22** — cancelar nunca borra. El recibo queda `cancelled` con motivo
  obligatorio, su pago deja de contar para el saldo del trabajo y sale de la
  contabilidad, pero su liga pública sigue resolviendo (RN-24).
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, or_

from receipt.domain.calculations import choose_template
from receipt.domain.clock import now_utc, period_of
from receipt.domain.enums import (
    CounterKind,
    PaymentStatus,
    ReceiptStatus,
    ReceiptTemplate,
)
from receipt.domain.plans import has_paid_features
from receipt.domain.snapshot import (
    AdjustmentFacts,
    ClientFacts,
    IssuerFacts,
    ServiceFacts,
    build_snapshot,
)
from receipt.models import Account, Client, Payment, Receipt, Service, ServiceAdjustment
from receipt.repositories import AccountScope
from receipt.services.clients import folded, normalize_name
from receipt.services.counters import allocate_folio
from receipt.services.errors import ConflictError, ValidationError
from receipt.services.usage import record_issuance
from receipt.services.work import refresh_status

PUBLIC_TOKEN_BYTES = 32
"""§3.4 y §10: `secrets.token_urlsafe(32)`. Prohibido derivarlo del ID."""


async def issue_receipt(
    scope: AccountScope,
    *,
    account: Account,
    client: Client,
    service: Service,
    payment: Payment,
    adjustments: Sequence[ServiceAdjustment],
    previous_paid_cents: int,
    payment_count: int,
    template: ReceiptTemplate | None = None,
    issued_at: datetime | None = None,
) -> Receipt:
    """Emite el recibo de un pago y congela su contenido.

    `previous_paid_cents` y `payment_count` llegan calculados desde antes de
    insertar el pago, contándolo a él: en este punto el pago nuevo ya está en la
    base de datos y volver a derivarlos de ahí lo contaría dos veces. Ambos son
    del llamador porque solo él sabe si el pago se sumó a un trabajo existente.

    El contador de uso se incrementa aquí y **no** se decrementa al cancelar
    (RN-33, `AC-16`). Lo hace sobre el periodo de **emisión**, no el del pago: el
    límite del plan gratuito acota cuántos recibos se generan al mes, mientras
    que el ingreso de §9.1 se corta por `paid_at`. Casi siempre son el mismo mes;
    cuando no, cada uno cuenta donde le toca.
    """
    moment = issued_at or now_utc()

    snapshot = build_snapshot(
        issued_at=moment,
        paid_at=payment.paid_at,
        city=account.default_city,
        issuer=IssuerFacts(
            display_name=account.display_name,
            phone=account.phone,
            occupation=account.occupation,
            logo_blob_path=account.logo_blob_path,
            signature_blob_path=account.signature_blob_path,
        ),
        client=ClientFacts(full_name=client.full_name, phone=client.phone),
        service=ServiceFacts(
            code=service.code,
            concept=service.concept,
            agreed_total_cents=service.agreed_total_cents,
            location_alias=service.location_alias,
            location_address=service.location_address,
        ),
        adjustments=[
            AdjustmentFacts(amount_cents=item.amount_cents, reason=item.reason)
            for item in adjustments
        ],
        previous_paid_cents=previous_paid_cents,
        payment_cents=payment.amount_cents,
        material_cents=payment.material_cents,
        method=payment.method,
        # RN-34: se decide al emitir, porque el logo queda congelado.
        include_logo=has_paid_features(account.plan, account.plan_valid_until, moment),
    )

    # §8.4 elige la plantilla sola; `template` solo llega cuando el emisor fuerza
    # la otra desde la vista previa, que es del hito 4.
    chosen = template or choose_template(
        agreed_total_cents=service.agreed_total_cents,
        payment_count=payment_count,
    )

    receipt = Receipt(
        account_id=account.id,
        payment_id=payment.id,
        folio=await allocate_folio(scope.session, account.id, CounterKind.RECEIPT),
        public_token=secrets.token_urlsafe(PUBLIC_TOKEN_BYTES),
        template=chosen,
        snapshot=snapshot,
        status=ReceiptStatus.ISSUED,
        issued_at=moment,
    )
    scope.session.add(receipt)

    await record_issuance(scope.session, account.id, period_of(moment))
    await scope.session.flush()
    return receipt


async def cancel_receipt(scope: AccountScope, receipt: Receipt, *, reason: str) -> Receipt:
    """Cancela un recibo y su pago (RN-22).

    No borra nada: el recibo sigue en el historial, su liga pública sigue
    resolviendo con el sello de cancelado (RN-24) y su folio no se reutiliza
    (RN-23, `AC-11`), porque el contador de la cuenta solo avanza.
    """
    cleaned = " ".join(reason.split())
    if not cleaned:
        # AC-08. La base de datos lo vuelve a exigir con un CHECK, por si la
        # cancelación llegara alguna vez por otra vía.
        raise ValidationError("Escribe por qué se cancela este recibo.")

    if receipt.status is ReceiptStatus.CANCELLED:
        raise ConflictError("Este recibo ya estaba cancelado.")

    receipt.status = ReceiptStatus.CANCELLED
    receipt.cancelled_at = now_utc()
    receipt.cancellation_reason = cleaned

    payment = await scope.get(Payment, receipt.payment_id)
    if payment is None:  # pragma: no cover - la FK lo impide
        raise ConflictError("El recibo no tiene pago asociado.")
    payment.status = PaymentStatus.CANCELLED

    service = await scope.get(Service, payment.service_id)
    if service is not None:
        # AC-09: el pago deja de contar y el trabajo vuelve a su saldo anterior.
        await refresh_status(scope, service)

    await scope.session.flush()
    return receipt


@dataclass(frozen=True)
class ReceiptContext:
    """Lo que la pantalla de un recibo necesita además del snapshot.

    El contenido se lee del snapshot; esto es el estado **vivo**, que es la única
    excepción de §4.3: el sello de cancelado y las ligas a las entidades actuales.
    """

    receipt: Receipt
    payment: Payment
    service: Service
    client: Client


async def load_context(scope: AccountScope, receipt: Receipt) -> ReceiptContext | None:
    payment = await scope.get(Payment, receipt.payment_id)
    if payment is None:  # pragma: no cover - la FK lo impide
        return None
    service = await scope.get(Service, payment.service_id)
    if service is None:  # pragma: no cover - la FK lo impide
        return None
    client = await scope.get(Client, service.client_id)
    if client is None:  # pragma: no cover - la FK lo impide
        return None
    return ReceiptContext(receipt=receipt, payment=payment, service=service, client=client)


async def by_payment(scope: AccountScope, payment_ids: Sequence[int]) -> dict[int, Receipt]:
    """Recibo de cada pago, indexado por `payment_id`.

    La relación es `0..1` (§4.1), así que un pago sin recibo simplemente no
    aparece en el diccionario.
    """
    if not payment_ids:
        return {}
    receipts = await scope.list(scope.select(Receipt).where(Receipt.payment_id.in_(payment_ids)))
    return {receipt.payment_id: receipt for receipt in receipts}


def _snapshot_client_name() -> ColumnElement[str]:
    """Nombre del cliente tal como quedó congelado en el recibo.

    Se busca sobre el snapshot y no sobre la tabla viva a propósito: el emisor
    busca por el nombre que **dice el recibo**, que es el que le enseñó al
    cliente. Si después renombró a esa persona, `AC-03` garantiza que el recibo
    no cambió, y el buscador tiene que ser coherente con eso.
    """
    # `astext` sobre un `JSONB` no está tipado en SQLAlchemy y llega como `Any`.
    extracted: ColumnElement[str] = Receipt.snapshot["client"]["full_name"].astext
    return extracted


def history_statement(
    scope: AccountScope,
    *,
    status: ReceiptStatus | None = None,
    query: str = "",
) -> Select[tuple[Receipt]]:
    """Historial de `/recibos`: búsqueda y filtro por estado (§7.5)."""
    statement = scope.select(Receipt)

    if status is not None:
        statement = statement.where(Receipt.status == status)

    term = normalize_name(query)
    if term:
        pattern = folded(f"%{term}%")
        statement = statement.where(
            or_(
                folded(Receipt.folio).like(pattern),
                folded(_snapshot_client_name()).like(pattern),
            )
        )

    return statement.order_by(Receipt.issued_at.desc(), Receipt.id.desc())
