"""Compartir el recibo — copiar liga, WhatsApp y correo (E6).

**RN-32: el envío es siempre manual.** El emisor copia la liga, comparte desde
su propio WhatsApp o manda un correo si el cliente dio uno. Nunca se usa la
WhatsApp Business API ni ninguna integración con Meta, que §2.2 declara
no-objetivo permanente.

Compartir por WhatsApp es, por tanto, una **liga `wa.me`**: abre la aplicación
del emisor con el mensaje ya escrito y él pulsa enviar. El mensaje sale de su
número, no del nuestro, que es justo lo que el cliente espera recibir de alguien
a quien acaba de pagar en efectivo.
"""

from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from receipt.config import get_settings
from receipt.domain.money import format_pesos
from receipt.domain.phones import to_international
from receipt.infra.blob import BlobStore
from receipt.infra.mail import Attachment, Mailer, Message
from receipt.models import Client, Receipt
from receipt.services.documents import PDF_CONTENT_TYPE, HtmlRenderer, pdf_filename, receipt_pdf
from receipt.services.errors import BusinessRuleViolation, ValidationError


def public_url(receipt: Receipt) -> str:
    """La liga que se entrega al cliente. No caduca mientras exista la cuenta (RN-30)."""
    return f"{get_settings().public_base_url.rstrip('/')}/r/{receipt.public_token}"


def share_text(receipt: Receipt, *, issuer_name: str) -> str:
    """El mensaje que se precarga en WhatsApp o encabeza el correo.

    Lleva el monto y el folio porque el cliente lo lee en la lista de chats sin
    abrir nada, y porque un mensaje con una liga pelada parece un fraude.
    """
    amount = format_pesos(receipt.snapshot.get("amounts", {}).get("payment_cents", 0))
    return (
        f"Aquí está tu comprobante de pago por {amount} ({receipt.folio}) "
        f"de {issuer_name}: {public_url(receipt)}"
    )


def whatsapp_url(receipt: Receipt, client: Client, *, issuer_name: str) -> str | None:
    """Liga `wa.me` con el mensaje ya escrito. `None` si el teléfono no sirve.

    Devolver `None` en vez de abrir WhatsApp sin destinatario es deliberado: es
    mejor que el botón explique que el teléfono no se entiende a que el emisor
    descubra que le escribió a un número equivocado.
    """
    phone = to_international(client.phone)
    if phone is None:
        return None
    return f"https://wa.me/{phone}?text={quote(share_text(receipt, issuer_name=issuer_name))}"


async def email_receipt(
    session: AsyncSession,
    mailer: Mailer,
    blobs: BlobStore,
    receipt: Receipt,
    client: Client,
    render_html: HtmlRenderer,
    *,
    issuer_name: str,
) -> str:
    """Envía el comprobante al correo del cliente. Devuelve la dirección usada.

    Va la liga **y** el PDF adjunto. La liga siempre refleja el estado real,
    sello de cancelado incluido (RN-24); el adjunto es la copia que el cliente
    conserva aunque nunca haga clic, que es lo que le pide §7.3.
    """
    if not client.email:
        # §7.2: la acción se muestra deshabilitada con explicación. Que además
        # falle aquí protege contra un envío disparado por otra vía.
        raise ValidationError(
            f"{client.full_name} no tiene correo registrado. Agrégalo en su ficha "
            f"para poder enviarle el recibo."
        )

    if receipt.status.value == "cancelled":
        raise BusinessRuleViolation(
            "RN-22",
            "Este recibo está cancelado: no tiene sentido enviárselo al cliente.",
        )

    pdf = await receipt_pdf(session, blobs, receipt, render_html)

    await mailer.send(
        Message(
            to=client.email,
            subject=f"Tu comprobante de pago {receipt.folio} — {issuer_name}",
            body=(
                f"Hola, {client.full_name}:\n\n"
                f"{share_text(receipt, issuer_name=issuer_name)}\n\n"
                "Te adjuntamos también el PDF para que lo guardes.\n\n"
                "Este es un comprobante interno sin efectos fiscales. "
                "No es un CFDI ni sustituye a uno."
            ),
            attachments=(
                Attachment(
                    filename=pdf_filename(receipt),
                    content=pdf,
                    content_type=PDF_CONTENT_TYPE,
                ),
            ),
        )
    )
    return client.email
