"""Pantalla de cobro — `/cobrar` (§7.1, §7.2).

**Es la única pantalla que importa.** Todo lo demás puede ser feo y funcional;
esta tiene que ser rápida, con objetivos grandes, legible al sol y utilizable
con el pulgar de una sola mano. El emisor está de pie, en la puerta de una casa,
con el celular en una mano y las manos sucias, y si el flujo completo no se
resuelve en menos de un minuto el producto no se usa (§1.4).

De ahí las tres decisiones de esta ruta:

- **Un solo formulario y un solo POST.** No hay wizard. Lo que se revela y se
  oculta lo hace `cobrar.js` en el navegador, sin ida y vuelta al servidor
  (`AC-19`). Lo único que va por HTMX es el buscador de clientes y la lista de
  trabajos abiertos, que necesitan datos.
- **La clave de idempotencia la genera el servidor** al pintar el formulario y
  viaja en un campo oculto. Así el doble toque manda la misma clave sin depender
  de que el JavaScript haya cargado, que es precisamente lo que falla cuando hay
  mala señal (`AC-18`).
- **El cliente nuevo no se crea aparte.** Sus dos campos son parte del mismo
  formulario y `charge()` lo da de alta dentro de la transacción (§6), así que
  un cobro abandonado no deja clientes sueltos.
"""

import secrets

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.domain.enums import PaymentMethod, SignerCapacity
from receipt.models import Client
from receipt.security.csrf import verify_csrf
from receipt.services.charging import ChargeRequest, charge
from receipt.services.clients import search_statement
from receipt.services.errors import DomainError
from receipt.services.media import store_client_signature
from receipt.services.work import load_totals, open_services_statement
from receipt.web.dependencies import Blobs, CurrentAccount, DbSession, Scope
from receipt.web.templating import render

router = APIRouter(tags=["charge"], dependencies=[Depends(verify_csrf)])


def _new_idempotency_key() -> str:
    return secrets.token_urlsafe(16)


def _cents(pesos: str) -> int | None:
    """Convierte lo que se teclea en el campo de monto a centavos.

    Acepta "2300", "2,300", "2300.50" y "$2,300.50": el emisor teclea con el
    pulgar y no va a pelearse con el formato. Devuelve `None` si no hay nada
    utilizable, para que la capa de servicio decida si eso es un error.

    Nunca pasa por `float`: §3.4 lo prohíbe y un centavo perdido al redondear es
    un centavo que no cuadra en la contabilidad de §9.
    """
    cleaned = (pesos or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        return None

    whole, _, fraction = cleaned.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None

    return int(whole) * 100 + int((fraction + "00")[:2])


@router.get("/cobrar")
async def form(request: Request, scope: Scope, account: CurrentAccount) -> Response:
    return render(
        request,
        "charge/form.html",
        {
            "default_city": account.default_city,
            "idempotency_key": _new_idempotency_key(),
            "signer_capacities": list(SignerCapacity),
            "values": {},
        },
    )


@router.get("/cobrar/clientes")
async def client_picker(request: Request, scope: Scope, q: str = "") -> Response:
    """Buscador del bloque 1, por HTMX.

    Reutiliza `search_statement` del hito 1 —la misma búsqueda que ignora
    acentos— y solo cambia la presentación: aquí los resultados **seleccionan**
    en lugar de navegar.
    """
    clients = await scope.list(search_statement(scope, query=q))
    return render(request, "clients/_results.html", {"clients": clients, "q": q, "picker": True})


@router.get("/cobrar/trabajos")
async def open_jobs(request: Request, scope: Scope, client_id: int) -> Response:
    """Trabajos abiertos del cliente elegido (§7.2, bloque 2).

    Si la lista sale vacía, la opción "Continuar un trabajo" no se muestra: una
    opción que no lleva a ningún lado es una pulsación desperdiciada.
    """
    client = await scope.get(Client, client_id)
    if client is None:
        return render(request, "charge/_jobs.html", {"services": [], "totals": {}})

    services = await scope.list(open_services_statement(scope, client.id))
    return render(
        request,
        "charge/_jobs.html",
        {
            "client": client,
            "services": services,
            "totals": {s.id: await load_totals(scope, s) for s in services},
        },
    )


@router.post("/cobrar")
async def submit(
    request: Request,
    scope: Scope,
    db: DbSession,
    blobs: Blobs,
    account: CurrentAccount,
    idempotency_key: str = Form(""),
    client_id: str = Form(""),
    new_client_name: str = Form(""),
    new_client_phone: str = Form(""),
    service_id: str = Form(""),
    concept: str = Form(""),
    location_alias: str = Form(""),
    location_address: str = Form(""),
    agreed_total: str = Form(""),
    adjustment_amount: str = Form(""),
    adjustment_reason: str = Form(""),
    amount: str = Form(""),
    material: str = Form(""),
    method: str = Form(PaymentMethod.CASH.value),
    signature_data: str = Form(""),
    signer_name: str = Form(""),
    signer_capacity: str = Form(SignerCapacity.HOLDER.value),
) -> Response:
    """Emite el recibo. Un solo POST para todo el momento de cobro (§6)."""
    default_city = account.default_city

    charge_request = ChargeRequest(
        payment_amount_cents=_cents(amount) or 0,
        method=_enum_or(PaymentMethod, method, PaymentMethod.CASH),
        client_id=int(client_id) if client_id.isdigit() else None,
        new_client_name=new_client_name,
        new_client_phone=new_client_phone,
        service_id=int(service_id) if service_id.isdigit() else None,
        concept=concept,
        location_alias=location_alias or None,
        location_address=location_address or None,
        agreed_total_cents=_cents(agreed_total),
        adjustment_amount_cents=_cents(adjustment_amount),
        adjustment_reason=adjustment_reason or None,
        material_cents=_cents(material) or 0,
        idempotency_key=idempotency_key or None,
    )

    try:
        result = await charge(scope, account, charge_request)

        # La firma se estampa fuera de `charge()` para no meter escritura de
        # blobs dentro de la transacción de cobro, y solo si el envío no fue un
        # reintento: reestampar la firma de un recibo ya emitido escribiría un
        # blob nuevo sin que nadie lo haya pedido.
        if signature_data and not result.replayed:
            await store_client_signature(
                db,
                blobs,
                result.receipt,
                data_uri=signature_data,
                signer_name=signer_name,
                capacity=_enum_or(SignerCapacity, signer_capacity, SignerCapacity.HOLDER),
            )
    except DomainError as exc:
        # Deshacer explícitamente, no confiar en que cerrar la sesión lo haga.
        # `charge()` puede haber dado de alta al cliente antes de que fallara el
        # pago, y ese cliente no debe quedar en el directorio: el emisor no
        # llegó a cobrarle. Dejarlo al ciclo de vida de la sesión funcionaría
        # hoy y se rompería el día que esta ruta escriba algo más después.
        await db.rollback()
        return render(
            request,
            "charge/form.html",
            {
                "default_city": default_city,
                # La misma clave vuelve al formulario: si el fallo fue de red y
                # el cobro sí entró, el reintento lo detecta en lugar de cobrar
                # dos veces.
                "idempotency_key": idempotency_key or _new_idempotency_key(),
                "signer_capacities": list(SignerCapacity),
                "error": exc.message,
                "values": {
                    "client_id": client_id,
                    "new_client_name": new_client_name,
                    "new_client_phone": new_client_phone,
                    "service_id": service_id,
                    "concept": concept,
                    "location_alias": location_alias,
                    "location_address": location_address,
                    "agreed_total": agreed_total,
                    "adjustment_amount": adjustment_amount,
                    "adjustment_reason": adjustment_reason,
                    "amount": amount,
                    "material": material,
                    "method": method,
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()
    # Redirección tras POST: recargar la pantalla de resultado no reenvía el
    # formulario. `listo` solo cambia el encabezado; las acciones de compartir
    # están siempre, porque un recibo se comparte también días después.
    return RedirectResponse(
        f"/recibos/{result.receipt.id}?listo=1", status_code=status.HTTP_303_SEE_OTHER
    )


def _enum_or(enum_type, raw: str, fallback):  # type: ignore[no-untyped-def]
    """Un valor inventado en el formulario no es un error 500."""
    try:
        return enum_type(raw)
    except ValueError:
        return fallback
