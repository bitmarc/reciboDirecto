"""Historial de recibos y pantalla de resultado — `/recibos` (§7.2, §7.5).

Vista del **emisor**: encontrar un recibo, ver lo que quedó congelado en él,
compartirlo, firmarlo y cancelarlo.

La ficha de un recibo es también la **pantalla de resultado** de `/cobrar`
(§7.2). No son dos páginas: las tres acciones de compartir —WhatsApp, copiar
liga, correo— sirven igual justo después de cobrar que tres días más tarde,
cuando el cliente pide otra vez su comprobante. El parámetro `listo` solo cambia
el encabezado.

Todo lo que se muestra del contenido sale del `snapshot`, no de las tablas
vivas (RN-10). Es lo que hace que `AC-02` y `AC-03` se sostengan: cambiar el
logo, el teléfono o el nombre de un cliente no altera un recibo ya emitido.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.domain.enums import ReceiptStatus, ReceiptTemplate, SignerCapacity
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
from receipt.services.sharing import email_receipt, public_url, share_text, whatsapp_url
from receipt.web.dependencies import (
    Blobs,
    CurrentAccount,
    DbSession,
    MailerDep,
    Scope,
    not_found,
)
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


async def _detail_context(
    scope: Scope, blobs: Blobs, account: CurrentAccount, receipt: Receipt
) -> dict[str, object]:
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
        # Compartir (§7.2, RN-32): siempre manual, siempre desde el teléfono del
        # emisor. `whatsapp` sale `None` si el teléfono del cliente no se
        # entiende, y entonces el botón lo explica en vez de abrir un chat con
        # un número equivocado.
        "public_url": public_url(receipt),
        "whatsapp_url": whatsapp_url(receipt, context.client, issuer_name=account.display_name),
        "share_text": share_text(receipt, issuer_name=account.display_name),
    }


@router.get("/recibos/{receipt_id}")
async def detail(
    request: Request,
    scope: Scope,
    blobs: Blobs,
    account: CurrentAccount,
    receipt_id: int,
    listo: bool = False,
) -> Response:
    """Ficha del recibo y, con `listo`, pantalla de resultado de `/cobrar`."""
    receipt = await _load(scope, receipt_id)
    context = await _detail_context(scope, blobs, account, receipt)
    context["listo"] = listo
    return render(request, "receipts/detail.html", context)


@router.post("/recibos/{receipt_id}/enviar")
async def send_by_email(
    request: Request,
    scope: Scope,
    db: DbSession,
    blobs: Blobs,
    mailer: MailerDep,
    account: CurrentAccount,
    receipt_id: int,
) -> Response:
    """Envía el comprobante al correo del cliente (§6, RN-32).

    Es una acción que el emisor dispara a mano, nunca un envío automático: §2.2
    declara no-objetivo los recordatorios y RN-32 fija que el envío es siempre
    manual.
    """
    receipt = await _load(scope, receipt_id)
    context = await load_context(scope, receipt)
    if context is None:  # pragma: no cover - las FK lo impiden
        raise not_found()

    try:
        await email_receipt(
            db,
            mailer,
            blobs,
            receipt,
            context.client,
            render_receipt_pdf_html,
            issuer_name=account.display_name,
        )
    except DomainError as exc:
        detail_context = await _detail_context(scope, blobs, account, receipt)
        detail_context["error"] = exc.message
        return render(
            request,
            "receipts/detail.html",
            detail_context,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()
    return RedirectResponse(
        f"/recibos/{receipt.id}?enviado=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/recibos/{receipt_id}/plantilla")
async def switch_template(
    scope: Scope, db: DbSession, receipt_id: int, template: str = Form(...)
) -> Response:
    """§8.4: el emisor puede forzar la otra plantilla desde la vista previa.

    No choca con RN-10. La plantilla decide **cómo se presentan** los mismos
    montos congelados; ninguna cifra cambia. El PDF cacheado sí deja de
    corresponder, así que se suelta.
    """
    receipt = await _load(scope, receipt_id)
    try:
        chosen = ReceiptTemplate(template)
    except ValueError:
        raise not_found() from None

    if receipt.template is not chosen:
        receipt.template = chosen
        invalidate_pdf(receipt)
        await db.commit()

    return RedirectResponse(f"/recibos/{receipt.id}", status_code=status.HTTP_303_SEE_OTHER)


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
    account: CurrentAccount,
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
        context = await _detail_context(scope, blobs, account, receipt)
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
    account: CurrentAccount,
    receipt_id: int,
    reason: str = Form(""),
) -> Response:
    """Cancela el recibo y su pago (RN-22). El motivo es obligatorio (`AC-08`)."""
    receipt = await _load(scope, receipt_id)
    try:
        await cancel_receipt(scope, receipt, reason=reason)
    except DomainError as exc:
        context = await _detail_context(scope, blobs, account, receipt)
        context["error"] = exc.message
        return render(
            request, "receipts/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST
        )

    await db.commit()
    return RedirectResponse(f"/recibos/{receipt.id}", status_code=status.HTTP_303_SEE_OTHER)
