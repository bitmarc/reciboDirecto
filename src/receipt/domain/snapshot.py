"""Construcción del `snapshot` del recibo (§4.3) — el corazón del sistema.

Cuando se emite un recibo se congela aquí **todo** lo necesario para
renderizarlo, sin depender de ninguna otra tabla. La página pública y el PDF se
renderizan exclusivamente desde este diccionario, nunca desde las tablas vivas
(RN-10). Si mañana el emisor cambia su logo, su teléfono o el nombre del
cliente, los recibos ya emitidos no cambian. Es lo único que sostiene su valor
como prueba (§1.2).

El módulo es puro a propósito: recibe datos planos y devuelve un `dict`. Eso
permite probar el contenido congelado —incluido lo que **no** debe aparecer,
como el material de RN-14— sin base de datos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt.domain.calculations import balance_cents, current_total_cents
from receipt.domain.clock import to_local
from receipt.domain.enums import PaymentMethod

SNAPSHOT_VERSION = 1
"""Versión del formato congelado.

No está en §4.3, pero un recibo emitido hoy se renderiza dentro de cinco años
con el código de entonces. Sin una marca de formato, evolucionar la estructura
obliga a adivinar la forma de cada fila, y RN-10 prohíbe reescribirlas.
"""

MATERIAL_CONCEPT_SUFFIX = "mano de obra y material"
"""§8.3: cuando el pago incluye material, el concepto se redacta de forma
agregada y **no se desglosa el monto** (RN-14)."""


@dataclass(frozen=True)
class IssuerFacts:
    """Datos del emisor tal como estaban al emitir (§8.1)."""

    display_name: str
    phone: str
    occupation: str | None = None
    logo_blob_path: str | None = None
    signature_blob_path: str | None = None


@dataclass(frozen=True)
class ClientFacts:
    full_name: str
    phone: str


@dataclass(frozen=True)
class ServiceFacts:
    code: str
    concept: str
    agreed_total_cents: int | None = None
    location_alias: str | None = None
    location_address: str | None = None


@dataclass(frozen=True)
class AdjustmentFacts:
    """RN-12: el motivo aparece textualmente en los recibos posteriores."""

    amount_cents: int
    reason: str


def concept_line(concept: str, *, includes_material: bool) -> str:
    """Concepto tal como se lee en el recibo (§8.3).

    El monto del material no aparece nunca; lo que cambia es la redacción, para
    que el cliente entienda qué cubre lo que pagó sin ver el desglose interno.
    """
    if not includes_material:
        return concept
    return f"{concept} — {MATERIAL_CONCEPT_SUFFIX}"


def build_snapshot(
    *,
    issued_at: datetime,
    paid_at: datetime,
    city: str,
    issuer: IssuerFacts,
    client: ClientFacts,
    service: ServiceFacts,
    adjustments: Sequence[AdjustmentFacts],
    previous_paid_cents: int,
    payment_cents: int,
    material_cents: int,
    method: PaymentMethod,
    include_logo: bool,
) -> dict[str, Any]:
    """Congela el contenido del recibo.

    `previous_paid_cents` es la suma de los pagos **activos anteriores a este**.
    Se recibe ya calculada en lugar de derivarla: en el momento de emitir, el
    pago nuevo ya está en la base de datos y contarlo dos veces sería un error
    que ninguna prueba de la capa de dominio podría detectar.

    `include_logo` decide RN-34 fuera de aquí, porque depende del plan vigente y
    este módulo no conoce cuentas.
    """
    if material_cents < 0 or material_cents > payment_cents:
        raise ValueError("El material debe estar entre cero y el monto del pago (RN-14)")

    adjustment_cents = [adjustment.amount_cents for adjustment in adjustments]
    accumulated = previous_paid_cents + payment_cents

    return {
        "version": SNAPSHOT_VERSION,
        "issued_at_local": to_local(issued_at).isoformat(),
        # Añadido sobre la forma de §4.3. Casi siempre coincide con la emisión,
        # porque el recibo se genera con el dinero en la mano (§1.4). Cuando no
        # coincide —captura en diferido, que §17 vigila— el comprobante tiene que
        # poder decir **cuándo se pagó**, que es el hecho que documenta; sin este
        # campo, esa fecha no existiría en ningún lado del snapshot y RN-10
        # impide reconstruirla después.
        "paid_at_local": to_local(paid_at).isoformat(),
        "city": city,
        "issuer": {
            "display_name": issuer.display_name,
            "phone": issuer.phone,
            "occupation": issuer.occupation,
            # RN-34: en plan gratuito el logo simplemente no entra al snapshot.
            "logo_blob_path": issuer.logo_blob_path if include_logo else None,
            "signature_blob_path": issuer.signature_blob_path,
        },
        "client": {
            "full_name": client.full_name,
            "phone": client.phone,
        },
        "service": {
            "code": service.code,
            "concept": service.concept,
            "concept_line": concept_line(service.concept, includes_material=material_cents > 0),
            "location_alias": service.location_alias,
            "location_address": service.location_address,
        },
        "amounts": {
            "agreed_total_cents": service.agreed_total_cents,
            "adjustments": [
                {"amount_cents": adjustment.amount_cents, "reason": adjustment.reason}
                for adjustment in adjustments
            ],
            "current_total_cents": current_total_cents(
                service.agreed_total_cents, adjustment_cents
            ),
            "previous_paid_cents": previous_paid_cents,
            "payment_cents": payment_cents,
            "accumulated_paid_cents": accumulated,
            "balance_cents": balance_cents(
                service.agreed_total_cents,
                adjustment_cents,
                [previous_paid_cents, payment_cents],
            ),
            # `material_cents` NO se guarda: RN-14 lo declara registro interno y
            # el snapshot es justo lo que se le entrega al cliente (AC-06).
        },
        "method": method.value,
    }
