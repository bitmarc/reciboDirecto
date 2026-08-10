"""Historial de recibos — `/recibos` (§7.5).

La página pública `/r/{token}`, el PDF y la plantilla del comprobante llegan en
el hito 3. Lo que existe aquí es la vista del **emisor**: encontrar un recibo,
ver lo que quedó congelado en él y cancelarlo.

Todo lo que se muestra del contenido sale del `snapshot`, no de las tablas
vivas (RN-10). Es lo que hace que `AC-02` y `AC-03` se sostengan: cambiar el
logo, el teléfono o el nombre de un cliente no altera un recibo ya emitido.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.domain.enums import ReceiptStatus, SignerCapacity
from receipt.models import Receipt
from receipt.security.csrf import verify_csrf
from receipt.services.documents import (
    PDF_CONTENT_TYPE,
    invalidate_pdf,
    pdf_filename,
    receipt_pdf,
)
from receipt.services.errors import DomainError
from receipt.services.media import store_client_signature
from receipt.services.receipts import cancel_receipt, history_statement, load_context
from receipt.services.rendering import build_document
from receipt.web.dependencies import Blobs, DbSession, Scope, not_found
from receipt.web.templating import render, render_receipt_pdf_html

router = APIRouter(tags=["receipts"], dependencies=[Depends(verify_csrf)])


def _parse_status(raw: str) -> ReceiptStatus | None:
    try:
        return ReceiptStatus(raw)
    except ValueError:
        return None


async def _load(scope: Scope, receipt_id: int) -> Receipt:
    receipt = await scope.get(Receipt, receipt_id)
    if receipt is None:
        raise not_found()
    return receipt


@router.get("/recibos")
async def index(request: Request, scope: Scope, q: str = "", estado: str = "") -> Response:
    selected = _parse_status(estado)
    receipts = await scope.list(history_statement(scope, status=selected, query=q))
    return render(
        request,
        "receipts/index.html",
        {
            "receipts": receipts,
            "q": q,
            "estado": estado,
        },
    )


async def _detail_context(scope: Scope, blobs: Blobs, receipt: Receipt) -> dict[str, object]:
    context = await load_context(scope, receipt)
    if context is None:  # pragma: no cover - las FK lo impiden
        raise not_found()
    return {
        "receipt": receipt,
        # El comprobante se renderiza con LA plantilla, la misma que sirve a la
        # página pública y al PDF (§3.2). Antes esta ficha reimprimía los montos
        # en una lista propia, que es exactamente la segunda plantilla
        # divergente que esa sección prohíbe.
        "doc": await build_document(blobs, receipt),
        # El contexto vivo solo sirve para enlazar al trabajo y al cliente
        # actuales, nunca para el contenido del recibo.
        "service": context.service,
        "client": context.client,
        "signer_capacities": list(SignerCapacity),
    }


@router.get("/recibos/{receipt_id}")
async def detail(request: Request, scope: Scope, blobs: Blobs, receipt_id: int) -> Response:
    receipt = await _load(scope, receipt_id)
    return render(request, "receipts/detail.html", await _detail_context(scope, blobs, receipt))


@router.get("/recibos/{receipt_id}/pdf")
async def download_pdf(scope: Scope, db: DbSession, blobs: Blobs, receipt_id: int) -> Response:
    """Descarga autenticada (§6). El mismo PDF que sirve la liga pública."""
    receipt = await _load(scope, receipt_id)
    pdf = await receipt_pdf(db, blobs, receipt, render_receipt_pdf_html)
    await db.commit()

    return Response(
        content=pdf,
        media_type=PDF_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{pdf_filename(receipt)}"'},
    )


@router.post("/recibos/{receipt_id}/firma")
async def sign(
    request: Request,
    scope: Scope,
    db: DbSession,
    blobs: Blobs,
    receipt_id: int,
    signature_data: str = Form(...),
    signer_name: str = Form(""),
    signer_capacity: str = Form(SignerCapacity.HOLDER.value),
) -> Response:
    """Estampa la firma del cliente (RN-21, §14.1).

    El hito 4 captura esto dentro de `/cobrar`, con el mismo componente. Aquí
    existe además como acción sobre un recibo del historial, porque la firma es
    evidencia de entrega y la entrega no siempre ocurre en el instante del
    cobro.
    """
    receipt = await _load(scope, receipt_id)
    try:
        capacity = SignerCapacity(signer_capacity)
    except ValueError:
        capacity = SignerCapacity.HOLDER

    try:
        await store_client_signature(
            db, blobs, receipt, data_uri=signature_data, signer_name=signer_name, capacity=capacity
        )
    except DomainError as exc:
        context = await _detail_context(scope, blobs, receipt)
        context["error"] = exc.message
        return render(
            request, "receipts/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST
        )

    # El PDF cacheado ya no refleja el documento: la próxima descarga regenera.
    invalidate_pdf(receipt)
    await db.commit()
    return RedirectResponse(f"/recibos/{receipt.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/recibos/{receipt_id}/cancelar")
async def cancel(
    request: Request,
    scope: Scope,
    db: DbSession,
    blobs: Blobs,
    receipt_id: int,
    reason: str = Form(""),
) -> Response:
    """Cancela el recibo y su pago (RN-22). El motivo es obligatorio (`AC-08`)."""
    receipt = await _load(scope, receipt_id)
    try:
        await cancel_receipt(scope, receipt, reason=reason)
    except DomainError as exc:
        context = await _detail_context(scope, blobs, receipt)
        context["error"] = exc.message
        return render(
            request, "receipts/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST
        )

    await db.commit()
    return RedirectResponse(f"/recibos/{receipt.id}", status_code=status.HTTP_303_SEE_OTHER)
