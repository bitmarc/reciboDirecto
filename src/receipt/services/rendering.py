"""Armado del documento del recibo, a partir del snapshot (§4.3, §8).

Una sola plantilla HTML+CSS sirve a los tres destinos —página pública, PDF e
impresión— y este módulo le prepara el contexto (§3.2). No debe haber dos
plantillas divergentes, así que tampoco debe haber dos formas de construir lo
que reciben.

**Todo el contenido sale del `snapshot`.** Del registro vivo se leen solo tres
cosas, y las tres tienen justificación:

- el **folio**, que vive en la misma fila y nunca cambia;
- el **estado**, que es la excepción declarada en §4.3: la liga de un recibo
  cancelado sigue resolviendo y muestra el sello (RN-24);
- la **firma del cliente**, que es evidencia de entrega y no contenido del
  comprobante: se captura después de emitir y por eso nunca estuvo en el
  snapshot.

Las imágenes se incrustan como `data:` URI y no como URL. Es lo que WeasyPrint
necesita para el PDF, y además el contenedor de blobs es privado: una URL al
blob no resolvería, y una que resolviera expondría un dato personal (§10).
"""

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt.domain.clock import format_datetime_es, format_long_date_es
from receipt.infra.blob import BlobNotFound, BlobStore
from receipt.models import Receipt


@dataclass(frozen=True)
class ReceiptDocument:
    """Todo lo que la plantilla del comprobante necesita, ya resuelto."""

    receipt: Receipt
    snapshot: dict[str, Any]
    issued_on_text: str
    """«Ciudad de México, 7 de julio de 2026» (§8.1)."""

    paid_on_text: str | None
    """Solo cuando la fecha de pago no es la de emisión.

    Con captura en el sitio son la misma y no se imprime nada. Cuando difieren
    —captura en diferido, lo que §17 vigila— el comprobante tiene que decir
    cuándo se pagó, que es el hecho que documenta.
    """

    cancelled_at_text: str | None
    client_signed_at_text: str | None
    logo_data_uri: str | None
    issuer_signature_data_uri: str | None
    client_signature_data_uri: str | None

    @property
    def amounts(self) -> dict[str, Any]:
        amounts: dict[str, Any] = self.snapshot.get("amounts", {})
        return amounts

    @property
    def is_cancelled(self) -> bool:
        return self.receipt.status.value == "cancelled"


async def _data_uri(blobs: BlobStore, path: str | None) -> str | None:
    """Bytes del blob como `data:image/png;base64,…`.

    Un blob ausente no puede tumbar el render: un comprobante sin logo se lee
    perfectamente, mientras que un error 500 deja al cliente sin su prueba de
    pago. La ausencia se trata como "no hay imagen", que es lo que es.
    """
    if not path:
        return None
    try:
        raw = await blobs.download(path)
    except BlobNotFound:
        return None
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"


def _parse_local(value: str | None) -> datetime | None:
    """Las fechas del snapshot son ISO 8601 con desplazamiento, no objetos.

    Se guardaron ya convertidas a hora de CDMX (§4.3), así que aquí no se vuelve
    a convertir nada: reinterpretar la zona sería reescribir el recibo.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def build_document(blobs: BlobStore, receipt: Receipt) -> ReceiptDocument:
    snapshot = receipt.snapshot or {}
    issuer = snapshot.get("issuer", {})

    issued_at = _parse_local(snapshot.get("issued_at_local"))
    paid_at = _parse_local(snapshot.get("paid_at_local"))

    issued_on = format_long_date_es(issued_at) if issued_at else ""
    city = snapshot.get("city") or ""
    issued_on_text = f"{city}, {issued_on}" if city and issued_on else issued_on or city

    paid_on_text = None
    if paid_at and issued_at and paid_at.date() != issued_at.date():
        paid_on_text = format_long_date_es(paid_at)

    return ReceiptDocument(
        receipt=receipt,
        snapshot=snapshot,
        issued_on_text=issued_on_text,
        paid_on_text=paid_on_text,
        cancelled_at_text=(
            format_datetime_es(receipt.cancelled_at) if receipt.cancelled_at else None
        ),
        client_signed_at_text=(
            format_datetime_es(receipt.client_signature_signed_at)
            if receipt.client_signature_signed_at
            else None
        ),
        logo_data_uri=await _data_uri(blobs, issuer.get("logo_blob_path")),
        issuer_signature_data_uri=await _data_uri(blobs, issuer.get("signature_blob_path")),
        client_signature_data_uri=await _data_uri(blobs, receipt.client_signature_blob_path),
    )
