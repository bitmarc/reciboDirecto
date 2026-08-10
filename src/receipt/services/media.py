"""Firma del emisor y logo de la cuenta (E1).

RN-20: la firma se captura en la configuración de la cuenta y se estampa
automáticamente en todos los recibos. Nunca se pide dibujarla al emitir.

RN-34: el logo solo se aplica en plan de pago.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import now_utc
from receipt.domain.enums import Plan, ReceiptStatus, SignerCapacity
from receipt.domain.images import ImageRejected, logo_png, signature_png_from_data_uri
from receipt.infra.blob import BlobStore, new_path
from receipt.models import Account, Receipt
from receipt.services.errors import BusinessRuleViolation, ConflictError, ValidationError


async def store_signature(
    session: AsyncSession,
    blobs: BlobStore,
    account: Account,
    *,
    data_uri: str,
) -> str:
    """Guarda una firma nueva y apunta la cuenta a ella.

    Escribe **siempre una ruta nueva**. La firma anterior no se borra ni se
    sobrescribe: los recibos ya emitidos guardan su ruta en el snapshot y deben
    seguir mostrando la firma con la que se emitieron (`AC-02`).
    """
    try:
        png = signature_png_from_data_uri(data_uri)
    except ImageRejected as exc:
        raise ValidationError(str(exc)) from exc

    path = await blobs.upload(new_path("signature", account.id), png)
    account.signature_blob_path = path
    await session.flush()
    return path


async def store_logo(
    session: AsyncSession,
    blobs: BlobStore,
    account: Account,
    *,
    raw: bytes,
) -> str:
    """Guarda un logo nuevo. Solo en plan de pago (RN-34).

    La comprobación de plan va antes de tocar el blob: no tiene sentido gastar
    almacenamiento en una imagen que la cuenta no puede usar.
    """
    if account.plan is not Plan.PAID:
        raise BusinessRuleViolation("RN-34", "el logo solo está disponible en el plan de pago")

    try:
        png = logo_png(raw)
    except ImageRejected as exc:
        raise ValidationError(str(exc)) from exc

    path = await blobs.upload(new_path("logo", account.id), png)
    account.logo_blob_path = path
    await session.flush()
    return path


async def store_client_signature(
    session: AsyncSession,
    blobs: BlobStore,
    receipt: Receipt,
    *,
    data_uri: str,
    signer_name: str = "",
    capacity: SignerCapacity = SignerCapacity.HOLDER,
) -> str:
    """Estampa la firma del cliente en un recibo (RN-21, §14.1).

    Es **opcional** y la decide el emisor recibo por recibo: es evidencia de
    entrega, no validación legal. Por eso vive en su propia columna y no en el
    `snapshot` — no es contenido del comprobante, y añadirla después no
    reescribe nada (RN-10).

    §14.1, resuelto: se guarda **quién firmó y en qué calidad**. Cuando firma el
    titular no se pide teclear nada: el nombre se toma del propio recibo, porque
    exigir un campo más frente a la puerta de una casa es justo lo que §1.4
    prohíbe. Cuando firma un tercero, el nombre es obligatorio; sin él se
    reproduce el "yo no fui" que motiva pedir la firma.
    """
    if receipt.status is ReceiptStatus.CANCELLED:
        raise ConflictError("Este recibo está cancelado: ya no puede firmarse.")

    name = " ".join(signer_name.split())
    if capacity is SignerCapacity.HOLDER and not name:
        name = (receipt.snapshot or {}).get("client", {}).get("full_name", "")
    if not name:
        raise ValidationError("Escribe el nombre de quien está firmando.")

    try:
        png = signature_png_from_data_uri(data_uri)
    except ImageRejected as exc:
        raise ValidationError(str(exc)) from exc

    # Ruta nueva siempre. Volver a firmar —una captura salió mal, cosa normal
    # en un canvas sostenido con una mano— no destruye la anterior.
    path = await blobs.upload(new_path("signature", receipt.account_id), png)

    receipt.client_signature_blob_path = path
    receipt.client_signature_signer_name = name
    receipt.client_signature_signer_capacity = capacity
    receipt.client_signature_signed_at = now_utc()
    await session.flush()
    return path


async def remove_logo(session: AsyncSession, account: Account) -> None:
    """Deja de usar el logo, sin borrar el blob.

    El blob sobrevive porque los recibos emitidos con logo lo referencian desde
    su snapshot (RN-34: "los recibos ya emitidos conservan el logo").
    """
    account.logo_blob_path = None
    await session.flush()
