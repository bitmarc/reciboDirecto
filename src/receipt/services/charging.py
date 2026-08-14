"""Motor de cobro — la transacción central del producto (§6).

Un solo formulario, un solo botón, una sola transacción. `charge()` resuelve de
una vez lo que §6 describe para `POST /payments`:

- selecciona el cliente,
- crea el trabajo o se adjunta a uno abierto (RN-15, RN-16),
- registra el monto extra si lo hay (RN-12),
- registra el pago,
- emite el recibo con su snapshot y su folio (RN-10, RN-25),
- suma el consumo del mes (RN-33).

Antes de todo eso comprueba el límite del plan y, si topó, no escribe nada.

Nada de esto se commitea aquí: la transacción es del llamador, igual que en el
resto de la capa de servicios. O sale todo, o no sale nada — un pago sin recibo,
o un recibo sin pago, son estados que el producto no sabe explicar.

La generación del PDF llega en el hito 3 y se encola después de emitir; no es
parte de esta transacción a propósito, porque un fallo del renderizador no puede
tirar un cobro que ya ocurrió en la puerta de una casa.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from receipt.domain.clock import now_utc
from receipt.domain.enums import CounterKind, PaymentMethod, ReceiptTemplate, ServiceStatus
from receipt.domain.money import format_pesos
from receipt.models import Account, Client, IdempotencyKey, Payment, Receipt, Service
from receipt.repositories import AccountScope
from receipt.services.clients import create_client
from receipt.services.counters import allocate_folio
from receipt.services.errors import BusinessRuleViolation, ConflictError, ValidationError
from receipt.services.plans import assert_can_issue
from receipt.services.receipts import issue_receipt
from receipt.services.work import (
    add_adjustment,
    create_service,
    load_adjustments,
    load_totals,
    refresh_status,
)


@dataclass(frozen=True)
class ChargeRequest:
    """El formulario del momento de cobro (§7.2), ya convertido a centavos.

    Es un objeto plano y no un modelo de Pydantic porque la pantalla de cobro es
    un formulario HTML, no una API JSON: la conversión de pesos a centavos y de
    texto a enteros ocurre en la capa web, y aquí llega el dato ya limpio.
    """

    payment_amount_cents: int
    method: PaymentMethod = PaymentMethod.CASH

    # Cliente: o se elige del directorio, o se da de alta en el mismo envío.
    # §6 lo pide así —"crear o seleccionar el cliente"— y §7.2 lo dibuja: dos
    # campos, sin salir de la pantalla. Crearlo dentro de la transacción evita
    # que un formulario abandonado deje clientes sueltos en el directorio.
    client_id: int | None = None
    new_client_name: str = ""
    new_client_phone: str = ""

    # Trabajo: o se continúa uno abierto, o se crea uno nuevo (RN-16).
    service_id: int | None = None
    concept: str = ""
    location_alias: str | None = None
    location_address: str | None = None
    agreed_total_cents: int | None = None

    # Monto extra sobre un trabajo en curso (RN-12).
    adjustment_amount_cents: int | None = None
    adjustment_reason: str | None = None

    material_cents: int = 0
    paid_at: datetime | None = None
    template: ReceiptTemplate | None = None
    idempotency_key: str | None = None

    def fingerprint(self) -> str:
        """Huella del contenido, para distinguir un reintento de una clave reusada.

        Se calcula sobre lo que mandó el cliente y no sobre lo ya resuelto: dos
        envíos idénticos del mismo formulario tienen que dar la misma huella,
        incluso si `paid_at` viene vacío y cada intento lo resolvería a un
        instante distinto.
        """
        payload = {
            "client_id": self.client_id,
            "new_client_name": self.new_client_name,
            "new_client_phone": self.new_client_phone,
            "payment_amount_cents": self.payment_amount_cents,
            "method": self.method.value,
            "service_id": self.service_id,
            "concept": self.concept,
            "location_alias": self.location_alias,
            "location_address": self.location_address,
            "agreed_total_cents": self.agreed_total_cents,
            "adjustment_amount_cents": self.adjustment_amount_cents,
            "adjustment_reason": self.adjustment_reason,
            "material_cents": self.material_cents,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "template": self.template.value if self.template else None,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChargeResult:
    receipt: Receipt
    payment: Payment
    service: Service
    client: Client
    replayed: bool = False
    """`True` cuando la petición era un doble envío y se devolvió el recibo ya
    emitido en lugar de cobrar otra vez (`AC-18`)."""


async def charge(scope: AccountScope, account: Account, request: ChargeRequest) -> ChargeResult:
    """Cobra y emite. Ver el módulo para el contrato completo."""
    fingerprint = request.fingerprint()
    key_row: IdempotencyKey | None = None

    if request.idempotency_key:
        existing = await _lookup_key(scope, request.idempotency_key)
        if existing is not None:
            return await _replay(scope, existing, fingerprint)

    # RN-33, y el orden importa dos veces. Después del reenvío, porque repetir
    # un cobro ya emitido no consume cupo nuevo y bloquearlo dejaría al emisor
    # sin poder recuperar su propio recibo. Antes de reservar la clave y de
    # tocar cliente, trabajo o pago, porque un cobro rechazado no puede dejar
    # nada a medias: ni un cliente huérfano en el directorio ni una clave de
    # idempotencia quemada que impida reintentar tras suscribirse.
    #
    # Se mide contra el periodo de **hoy** y no contra `paid_at`: el cupo cuenta
    # recibos emitidos, no pagos recibidos, igual que `record_issuance`. Un pago
    # capturado en diferido consume el cupo del mes en que se emite el
    # comprobante, que es el mismo mes contra el que se comprobó aquí.
    await assert_can_issue(scope.session, account)

    if request.idempotency_key:
        try:
            # Se reserva la clave **antes** de trabajar. Si dos peticiones
            # gemelas llegan a la vez, la segunda choca contra el índice único
            # en lugar de cobrar dos veces; el savepoint deja la sesión utilizable
            # tras el choque.
            async with scope.session.begin_nested():
                key_row = IdempotencyKey(
                    account_id=scope.account_id,
                    key=request.idempotency_key,
                    request_hash=fingerprint,
                )
                scope.session.add(key_row)
        except IntegrityError:
            winner = await _lookup_key(scope, request.idempotency_key)
            if winner is None:  # pragma: no cover - el choque implica que existe
                raise
            return await _replay(scope, winner, fingerprint)

    client = await _resolve_client(scope, request)
    service = await _resolve_service(scope, account, client, request)

    if request.adjustment_amount_cents is not None:
        await add_adjustment(
            scope,
            service,
            amount_cents=request.adjustment_amount_cents,
            reason=request.adjustment_reason or "",
        )

    # Los totales se leen **antes** de insertar el pago: son el "antes" que el
    # recibo detallado muestra como "pagos anteriores" (§8.3).
    totals = await load_totals(scope, service)
    _validate_payment(request, balance_before=totals.balance_cents)

    payment = Payment(
        service_id=service.id,
        amount_cents=request.payment_amount_cents,
        material_cents=request.material_cents,
        method=request.method,
        paid_at=request.paid_at or now_utc(),
    )
    scope.session.add(payment)
    await scope.session.flush()

    # RN-13: el trabajo queda `settled` cuando el saldo llega a cero.
    await refresh_status(scope, service)

    receipt = await issue_receipt(
        scope,
        account=account,
        client=client,
        service=service,
        payment=payment,
        adjustments=await load_adjustments(scope, service),
        previous_paid_cents=totals.paid_cents,
        payment_count=totals.payment_count + 1,
        template=request.template,
    )

    if key_row is not None:
        key_row.receipt_id = receipt.id
        await scope.session.flush()

    return ChargeResult(receipt=receipt, payment=payment, service=service, client=client)


# ---------------------------------------------------------------------------
# Resolución de las partes
# ---------------------------------------------------------------------------


async def _resolve_client(scope: AccountScope, request: ChargeRequest) -> Client:
    """Elige el cliente del directorio o lo da de alta en el mismo envío (§6).

    El alta va aquí y no en una llamada aparte para que un formulario que se
    abandona a medias no deje un cliente huérfano en el directorio.
    """
    if request.client_id is not None:
        client = await scope.get(Client, request.client_id)
        if client is None:
            # Un cliente de otra cuenta y uno inexistente responden igual (AC-13).
            raise ValidationError("Selecciona un cliente de tu directorio.")
        return client

    if request.new_client_name.strip() or request.new_client_phone.strip():
        # `create_client` valida nombre y teléfono con las mismas reglas del
        # directorio: el recibo dirá "Recibí de {cliente}" y un nombre de pila
        # suelto no identifica a nadie meses después (§4.2).
        return await create_client(
            scope,
            full_name=request.new_client_name,
            phone=request.new_client_phone,
        )

    raise ValidationError("Elige un cliente o da de alta uno nuevo.")


async def _resolve_service(
    scope: AccountScope, account: Account, client: Client, request: ChargeRequest
) -> Service:
    """Continúa un trabajo abierto o crea uno nuevo. La elección es del emisor (RN-16)."""
    if request.service_id is None:
        if request.adjustment_amount_cents is not None:
            # §7.2 solo ofrece "monto extra" al continuar un trabajo: sobre uno
            # nuevo, el extra es simplemente parte del total acordado.
            raise ValidationError(
                "Un trabajo nuevo no lleva monto extra: inclúyelo en el total acordado."
            )
        return await create_service(
            scope,
            client=client,
            code=await allocate_folio(scope.session, account.id, CounterKind.SERVICE),
            concept=request.concept,
            location_alias=request.location_alias,
            location_address=request.location_address,
            agreed_total_cents=request.agreed_total_cents,
        )

    service = await scope.get(Service, request.service_id)
    if service is None:
        raise ValidationError("Ese trabajo no existe.")
    if service.client_id != client.id:
        raise ValidationError("Ese trabajo es de otro cliente.")
    if service.status is ServiceStatus.CLOSED_UNCOLLECTED:
        raise BusinessRuleViolation(
            "RN-17",
            "Este trabajo se cerró como no cobrado. Si al final te pagaron, "
            "regístralo como un trabajo nuevo.",
        )
    return service


def _validate_payment(request: ChargeRequest, *, balance_before: int | None) -> None:
    if request.payment_amount_cents <= 0:
        raise ValidationError("El monto recibido debe ser mayor que cero.")

    if request.material_cents < 0:
        raise ValidationError("El material no puede ser negativo.")
    if request.material_cents > request.payment_amount_cents:
        # RN-14: el ingreso es `monto - material`; un material mayor que el pago
        # produciría un ingreso negativo que no describe nada real.
        raise ValidationError("El material no puede ser mayor que el monto recibido.")

    if balance_before is None:
        # Trabajo sin total acordado (RN-15): no hay saldo contra el cual medir.
        return

    if request.payment_amount_cents > balance_before:
        # Decisión de producto: un sobrepago es un aumento de precio sin
        # documentar. RN-12 exige que todo aumento lleve motivo, y §7.2 tiene el
        # campo a un toque de distancia, en la misma pantalla.
        raise BusinessRuleViolation(
            "RN-12",
            f"El pago es mayor que el saldo de {format_pesos(balance_before)}. "
            f"Si hiciste trabajo extra, agrégalo como monto extra con su motivo; "
            f"si no, corrige el monto recibido.",
        )


# ---------------------------------------------------------------------------
# Idempotencia (§6, AC-18)
# ---------------------------------------------------------------------------


async def _lookup_key(scope: AccountScope, key: str) -> IdempotencyKey | None:
    statement = scope.select(IdempotencyKey).where(IdempotencyKey.key == key)
    result = await scope.session.execute(statement.limit(1))
    return result.scalars().first()


async def _replay(scope: AccountScope, existing: IdempotencyKey, fingerprint: str) -> ChargeResult:
    """Devuelve el recibo ya emitido en lugar de volver a cobrar.

    Un electricista con mala señal va a tocar el botón dos veces, y un recibo
    duplicado con un cobro duplicado es un fallo grave (§6).
    """
    if existing.request_hash != fingerprint:
        raise ConflictError(
            "Esa clave ya se usó para un cobro distinto. Vuelve a intentarlo desde "
            "la pantalla de cobro."
        )
    if existing.receipt_id is None:
        raise ConflictError("El cobro anterior no llegó a completarse. Inténtalo de nuevo.")

    receipt = await scope.get(Receipt, existing.receipt_id)
    if receipt is None:  # pragma: no cover - la FK lo impide
        raise ConflictError("No se encontró el recibo de ese cobro.")

    payment = await scope.get(Payment, receipt.payment_id)
    assert payment is not None  # la FK lo garantiza
    service = await scope.get(Service, payment.service_id)
    assert service is not None
    client = await scope.get(Client, service.client_id)
    assert client is not None

    return ChargeResult(
        receipt=receipt, payment=payment, service=service, client=client, replayed=True
    )
