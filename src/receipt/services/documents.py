"""El PDF del recibo: se genera bajo demanda y se guarda para la próxima (E5).

§6 dice "encolar la generación del PDF". No hay cola, y montarla es
infraestructura que el presupuesto de §13 no tiene. En su lugar, el PDF se
genera **la primera vez que alguien lo pide** y se cachea en el blob.

Por qué es la opción correcta y no solo la más barata:

- No añade nada al momento de cobro. §1.4 da un minuto para todo el flujo, y en
  la pantalla de resultado lo que se comparte es una **liga**, no un archivo
  (§7.2, RN-32). El PDF hace falta cuando alguien lo descarga, no antes.
- Un fallo del renderizador no puede tirar un cobro que ya ocurrió en la puerta
  de una casa. Generar dentro de la transacción de cobro sí lo tiraría.
- Solo se almacena lo que de verdad se descarga, y el almacenamiento de PDFs es
  el único costo variable real del sistema (§13).

**El PDF sigue al estado del recibo.** Cancelar o firmar sueltan
`pdf_blob_path`, así que la siguiente descarga regenera. Es lo que impide que un
recibo cancelado circule como PDF sin sello: un comprobante anulado que se ve
válido es peor que no tener PDF (RN-24).
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from receipt.infra.blob import BlobNotFound, BlobStore, new_path
from receipt.infra.pdf import render_pdf
from receipt.models import Receipt
from receipt.services.rendering import ReceiptDocument, build_document

PDF_CONTENT_TYPE = "application/pdf"

HtmlRenderer = Callable[[ReceiptDocument], str]
"""Convierte el documento en HTML autocontenido.

Se inyecta en lugar de importarse porque las plantillas Jinja viven en la capa
web, y las dependencias van en un solo sentido: `domain` ← `services` ← `web`.
Un `import` desde aquí a `web.templating` invertiría la flecha, y de paso haría
que probar el PDF exigiera arrancar la aplicación.
"""


def pdf_filename(receipt: Receipt) -> str:
    """`recibo-RB-0001.pdf`. Lo que el cliente ve al guardarlo en su celular."""
    return f"recibo-{receipt.folio}.pdf"


def invalidate_pdf(receipt: Receipt) -> None:
    """Olvida el PDF cacheado, sin borrar el blob.

    El blob anterior sobrevive, igual que las firmas y los logos: nada se
    sobrescribe nunca en este sistema. Queda huérfano, que es correcto y
    esperado.
    """
    receipt.pdf_blob_path = None


async def receipt_pdf(
    session: AsyncSession,
    blobs: BlobStore,
    receipt: Receipt,
    render_html: HtmlRenderer,
) -> bytes:
    """Devuelve el PDF del recibo, generándolo si hace falta.

    Bajo concurrencia, dos peticiones simultáneas pueden generar dos veces y
    subir a rutas distintas: una gana la columna y la otra deja un blob
    huérfano. Se acepta a propósito — el contenido es idéntico y la alternativa
    es un bloqueo de fila en una ruta pública, que es justo donde no conviene
    tener uno.
    """
    if receipt.pdf_blob_path:
        try:
            return await blobs.download(receipt.pdf_blob_path)
        except BlobNotFound:
            # La fila apunta a un blob que ya no está. Regenerar es preferible a
            # dejar sin comprobante a quien lo pide.
            receipt.pdf_blob_path = None

    pdf = render_pdf(render_html(await build_document(blobs, receipt)))

    path = await blobs.upload(
        new_path("receipt-pdf", receipt.account_id, "pdf"),
        pdf,
        content_type=PDF_CONTENT_TYPE,
    )
    receipt.pdf_blob_path = path
    await session.flush()
    return pdf
