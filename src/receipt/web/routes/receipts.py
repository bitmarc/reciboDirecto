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

from receipt.domain.enums import ReceiptStatus
from receipt.models import Receipt
from receipt.security.csrf import verify_csrf
from receipt.services.errors import DomainError
from receipt.services.receipts import cancel_receipt, history_statement, load_context
from receipt.web.dependencies import DbSession, Scope, not_found
from receipt.web.templating import render

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


async def _detail_context(scope: Scope, receipt: Receipt) -> dict[str, object]:
    context = await load_context(scope, receipt)
    if context is None:  # pragma: no cover - las FK lo impiden
        raise not_found()
    return {
        "receipt": receipt,
        # El contenido se lee del snapshot; el contexto vivo solo sirve para
        # enlazar al trabajo y al cliente actuales.
        "snapshot": receipt.snapshot,
        "amounts": receipt.snapshot.get("amounts", {}),
        "service": context.service,
        "client": context.client,
    }


@router.get("/recibos/{receipt_id}")
async def detail(request: Request, scope: Scope, receipt_id: int) -> Response:
    receipt = await _load(scope, receipt_id)
    return render(request, "receipts/detail.html", await _detail_context(scope, receipt))


@router.post("/recibos/{receipt_id}/cancelar")
async def cancel(
    request: Request,
    scope: Scope,
    db: DbSession,
    receipt_id: int,
    reason: str = Form(""),
) -> Response:
    """Cancela el recibo y su pago (RN-22). El motivo es obligatorio (`AC-08`)."""
    receipt = await _load(scope, receipt_id)
    try:
        await cancel_receipt(scope, receipt, reason=reason)
    except DomainError as exc:
        context = await _detail_context(scope, receipt)
        context["error"] = exc.message
        return render(
            request, "receipts/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST
        )

    await db.commit()
    return RedirectResponse(f"/recibos/{receipt.id}", status_code=status.HTTP_303_SEE_OTHER)
