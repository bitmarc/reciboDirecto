"""Trabajos — `/trabajos` (§7.5).

Pantalla de consulta, no de captura. Un trabajo se crea **en el momento del
primer pago** (RN-15), así que aquí no hay formulario de alta: eso es la pantalla
de cobro del hito 4. Lo que sí se hace desde aquí son las dos cosas que le pasan
a un trabajo fuera del momento de cobro: registrar un monto extra (RN-12),
porque el emisor no siempre lo recuerda mientras cobra, y darlo por perdido
(RN-17), que nunca ocurre en la puerta de una casa sino semanas después.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.domain.enums import ServiceStatus
from receipt.models import Client, Payment, Service
from receipt.security.csrf import verify_csrf
from receipt.services.errors import DomainError
from receipt.services.receipts import by_payment
from receipt.services.work import (
    add_adjustment,
    close_uncollected,
    history_statement,
    load_adjustments,
    load_totals,
    refresh_status,
    summaries_for,
)
from receipt.web.dependencies import DbSession, Scope, not_found
from receipt.web.templating import render

router = APIRouter(tags=["work"], dependencies=[Depends(verify_csrf)])


def _parse_status(raw: str) -> ServiceStatus | None:
    """Filtro de la barra de direcciones. Un valor inventado no es un error 500."""
    try:
        return ServiceStatus(raw)
    except ValueError:
        return None


async def _load(scope: Scope, service_id: int) -> Service:
    service = await scope.get(Service, service_id)
    if service is None:
        raise not_found()
    return service


@router.get("/trabajos")
async def index(request: Request, scope: Scope, estado: str = "") -> Response:
    selected = _parse_status(estado)
    services = await scope.list(history_statement(scope, status=selected))
    summaries = await summaries_for(scope, services)

    clients = {
        client.id: client
        for client in await scope.list(
            scope.select(Client).where(Client.id.in_({s.client_id for s in services}))
        )
    }

    return render(
        request,
        "work/index.html",
        {
            "services": services,
            "summaries": summaries,
            "clients": clients,
            "estado": estado,
        },
    )


async def _detail_context(scope: Scope, service: Service) -> dict[str, object]:
    """Todo lo que muestra la ficha del trabajo.

    Vive aparte porque el alta de un monto extra vuelve a esta misma pantalla
    cuando falla la validación, y dos copias del contexto se desincronizan.
    """
    payments = await scope.list(
        scope.select(Payment)
        .where(Payment.service_id == service.id)
        .order_by(Payment.paid_at, Payment.id)
    )
    return {
        "service": service,
        "client": await scope.get(Client, service.client_id),
        "totals": await load_totals(scope, service),
        "adjustments": await load_adjustments(scope, service),
        "payments": payments,
        "receipts": await by_payment(scope, [payment.id for payment in payments]),
    }


@router.get("/trabajos/{service_id}")
async def detail(request: Request, scope: Scope, service_id: int) -> Response:
    service = await _load(scope, service_id)
    return render(request, "work/detail.html", await _detail_context(scope, service))


@router.post("/trabajos/{service_id}/extras")
async def create_adjustment(
    request: Request,
    scope: Scope,
    db: DbSession,
    service_id: int,
    amount_cents: int = Form(...),
    reason: str = Form(...),
) -> Response:
    """Registra un monto extra (RN-12).

    No emite recibo: el extra cambia el saldo del trabajo y aparecerá en el
    **siguiente** recibo, que es cuando el cliente tiene que enterarse (§8.3).
    """
    service = await _load(scope, service_id)
    try:
        await add_adjustment(scope, service, amount_cents=amount_cents, reason=reason)
    except DomainError as exc:
        context = await _detail_context(scope, service)
        context["error"] = exc.message
        context["values"] = {"amount_cents": amount_cents, "reason": reason}
        return render(request, "work/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST)

    # Un extra sobre un trabajo ya liquidado lo devuelve a `open` (RN-13).
    await refresh_status(scope, service)
    await db.commit()
    return RedirectResponse(f"/trabajos/{service.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/trabajos/{service_id}/cerrar")
async def close(
    request: Request,
    scope: Scope,
    db: DbSession,
    service_id: int,
    reason: str = Form(""),
) -> Response:
    """Cierra el trabajo como no cobrado (RN-17).

    El saldo sale de "por cobrar" y pasa al reporte de dinero perdido, que §9.1
    muestra por separado y nunca mezclado con el ingreso. Los recibos ya
    emitidos no se tocan: lo cobrado se cobró.
    """
    service = await _load(scope, service_id)
    try:
        await close_uncollected(scope, service, reason=reason)
    except DomainError as exc:
        # Sin rollback, igual que el alta de un monto extra: `close_uncollected`
        # valida **antes** de tocar el objeto, así que no hay nada escrito que
        # deshacer y la ficha se puede renderizar sobre el estado real. La
        # prueba `test_rn17_a_closure_without_a_reason_is_refused` sostiene esa
        # invariante contra la base de datos; el día que se rompa, falla ahí y
        # no en producción.
        context = await _detail_context(scope, service)
        context["error"] = exc.message
        context["values"] = {"close_reason": reason}
        return render(request, "work/detail.html", context, status_code=status.HTTP_400_BAD_REQUEST)

    await db.commit()
    return RedirectResponse(f"/trabajos/{service.id}", status_code=status.HTTP_303_SEE_OTHER)
