"""Liga pública del recibo — `/r/{token}` (§3.3, §7.3, E5).

Es la única superficie del producto para el cliente del emisor. Él no instala
nada, no crea cuenta y no inicia sesión (§1.5): abre una liga y ve su
comprobante.

**Por qué la liga apunta aquí y no al blob** (§3.3, las tres razones son
bloqueantes): un blob es estático y no conoce el estado del recibo, así que
sería imposible mostrar el sello de cancelado (RN-24); un contenedor público
expondría todo el contenedor y un SAS de larga vida es una llave que no se puede
revocar; y se perdería la capacidad de saber si el cliente abrió el recibo.

**Esta es la única consulta del sistema que no pasa por `AccountScope`.** No es
un descuido: aquí no hay sesión de la que sacar un `account_id`, y el token *es*
la credencial. Por eso el token lleva 32 bytes de entropía generados con
`secrets` y está prohibido derivarlo del ID (§10), la ruta va limitada por IP
contra la enumeración, y un token inválido responde 404 sin distinguirse de uno
que nunca existió (`AC-12`).
"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from starlette.requests import Request

from receipt.models import Receipt
from receipt.security.rate_limit import client_key
from receipt.services.documents import PDF_CONTENT_TYPE, pdf_filename, receipt_pdf
from receipt.services.rendering import build_document
from receipt.web.dependencies import Blobs, DbSession, client_ip, public_receipt_limiter
from receipt.web.templating import render, render_receipt_pdf_html, templates

router = APIRouter(tags=["public"], include_in_schema=False)

# Sin `no-store`: el comprobante es inmutable salvo por la cancelación, y una
# caché privada corta permite reabrirlo con mala señal. `private` impide que un
# proxy compartido guarde el recibo de alguien.
_PUBLIC_HEADERS = {"Cache-Control": "private, max-age=60"}


def _not_found() -> HTTPException:
    """404 idéntico para un token inválido y uno inexistente (`AC-12`).

    No se distingue "mal formado" de "no existe" ni en el código ni en el
    cuerpo: cualquier diferencia observable convierte la ruta en un oráculo para
    quien barre tokens.
    """
    return HTTPException(status.HTTP_404_NOT_FOUND, "Recibo no encontrado")


def _enforce_limit(request: Request) -> None:
    key = client_key("recibo-publico", client_ip(request))
    if public_receipt_limiter.is_blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiadas consultas. Espera un momento y vuelve a intentarlo.",
        )
    public_receipt_limiter.record_attempt(key)


async def _load(db: DbSession, token: str, request: Request) -> Receipt:
    _enforce_limit(request)

    # Consulta directa por token, sin `AccountScope`: ver el encabezado.
    receipt = await db.scalar(select(Receipt).where(Receipt.public_token == token).limit(1))
    if receipt is None:
        raise _not_found()
    return receipt


@router.get("/r/{token}")
async def public_receipt(request: Request, db: DbSession, blobs: Blobs, token: str) -> Response:
    """La página del comprobante, renderizada desde el snapshot.

    La liga no caduca mientras exista la cuenta (RN-30) y sigue funcionando
    aunque el emisor deje de pagar la suscripción (RN-31): un comprobante no
    puede dejar de existir porque su emisor dejó de ser cliente.

    Un recibo cancelado responde 200 y muestra el sello con el motivo (RN-24,
    `AC-10`). Desaparecer sería peor: el cliente que guardó la liga concluiría
    que le mintieron, en vez de leer que el cobro se anuló.
    """
    receipt = await _load(db, token, request)
    document = await build_document(blobs, receipt)
    response = render(request, "receipt/public.html", {"doc": document})
    response.headers.update(_PUBLIC_HEADERS)
    return response


@router.get("/r/{token}/pdf")
async def public_receipt_pdf(request: Request, db: DbSession, blobs: Blobs, token: str) -> Response:
    """Descarga del PDF sin sesión (`AC-14`).

    El PDF se sirve **a través de la aplicación**, nunca por URL al blob: el
    contenedor es privado y las firmas que van dentro son datos personales
    (§10). Se genera la primera vez que se pide y se cachea; ver
    `services/documents.py`.
    """
    receipt = await _load(db, token, request)
    pdf = await receipt_pdf(db, blobs, receipt, render_receipt_pdf_html)
    # El PDF recién generado se cachea en la fila; sin commit se perdería y cada
    # descarga volvería a renderizar.
    await db.commit()

    return Response(
        content=pdf,
        media_type=PDF_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename(receipt)}"',
            **_PUBLIC_HEADERS,
        },
    )


@router.get("/r/{token}/imprimir")
async def public_receipt_print(
    request: Request, db: DbSession, blobs: Blobs, token: str
) -> Response:
    """El comprobante desnudo, sin el cromo de la página.

    Es el tercer destino de §3.2 y usa la misma plantilla del documento: quien
    prefiera imprimir desde el navegador en lugar de descargar el PDF obtiene lo
    mismo que saldría en papel.
    """
    receipt = await _load(db, token, request)
    document = await build_document(blobs, receipt)
    html = templates.get_template("receipt/pdf.html").render(doc=document)
    return Response(content=html, media_type="text/html; charset=utf-8", headers=_PUBLIC_HEADERS)
