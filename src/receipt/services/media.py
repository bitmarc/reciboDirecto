"""Firma del emisor y logo de la cuenta (E1).

RN-20: la firma se captura en la configuración de la cuenta y se estampa
automáticamente en todos los recibos. Nunca se pide dibujarla al emitir.

RN-34: el logo solo se aplica en plan de pago.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import Plan
from receipt.domain.images import ImageRejected, logo_png, signature_png_from_data_uri
from receipt.infra.blob import BlobStore, new_path
from receipt.models import Account
from receipt.services.errors import BusinessRuleViolation, ValidationError


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


async def remove_logo(session: AsyncSession, account: Account) -> None:
    """Deja de usar el logo, sin borrar el blob.

    El blob sobrevive porque los recibos emitidos con logo lo referencian desde
    su snapshot (RN-34: "los recibos ya emitidos conservan el logo").
    """
    account.logo_blob_path = None
    await session.flush()
