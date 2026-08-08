"""Directorio de clientes — `/clientes` (E2, §7.5).

El buscador de este módulo es el mismo widget que el bloque 1 de la pantalla de
cobro (§7.2). El parcial `clients/_results.html` se escribe aquí para que el
hito 4 lo reutilice tal cual, en lugar de escribir dos buscadores que se
comportan distinto.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.models import Client
from receipt.security.csrf import verify_csrf
from receipt.services.clients import (
    archive_client,
    create_client,
    find_possible_duplicates,
    restore_client,
    search_statement,
    update_client,
)
from receipt.services.errors import DomainError
from receipt.web.dependencies import DbSession, Scope, not_found
from receipt.web.templating import render

router = APIRouter(tags=["clients"], dependencies=[Depends(verify_csrf)])


async def _load(scope: Scope, client_id: int) -> Client:
    """Carga dentro de la cuenta de la sesión.

    Un cliente de otra cuenta devuelve 404, indistinguible de uno inexistente
    (`AC-12`, `AC-13`).
    """
    client = await scope.get(Client, client_id)
    if client is None:
        raise not_found()
    return client


# Las rutas literales van antes que `/{client_id}`: FastAPI resuelve por orden
# de registro y, al revés, "nuevo" intentaría parsearse como entero.


@router.get("/clientes")
async def index(request: Request, scope: Scope, q: str = "", archivados: bool = False) -> Response:
    clients = await scope.list(search_statement(scope, query=q, include_archived=archivados))
    return render(
        request,
        "clients/index.html",
        {"clients": clients, "q": q, "archivados": archivados},
    )


@router.get("/clientes/buscar")
async def search_fragment(
    request: Request, scope: Scope, q: str = "", archivados: bool = False
) -> Response:
    """Parcial que HTMX inyecta mientras el emisor teclea."""
    clients = await scope.list(search_statement(scope, query=q, include_archived=archivados))
    return render(
        request,
        "clients/_results.html",
        {"clients": clients, "q": q, "archivados": archivados},
    )


@router.get("/clientes/nuevo")
async def new_form(request: Request) -> Response:
    return render(request, "clients/form.html", {"client": None})


@router.post("/clientes")
async def create(
    request: Request,
    scope: Scope,
    db: DbSession,
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
) -> Response:
    try:
        client = await create_client(scope, full_name=full_name, phone=phone, email=email)
    except DomainError as exc:
        return render(
            request,
            "clients/form.html",
            {
                "client": None,
                "error": exc.message,
                "values": {"full_name": full_name, "phone": phone, "email": email},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()
    return RedirectResponse(f"/clientes/{client.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/clientes/{client_id}")
async def detail(request: Request, scope: Scope, client_id: int) -> Response:
    client = await _load(scope, client_id)
    duplicates = await find_possible_duplicates(scope, phone=client.phone, exclude_id=client.id)
    return render(request, "clients/detail.html", {"client": client, "duplicates": duplicates})


@router.get("/clientes/{client_id}/editar")
async def edit_form(request: Request, scope: Scope, client_id: int) -> Response:
    client = await _load(scope, client_id)
    return render(request, "clients/form.html", {"client": client})


@router.post("/clientes/{client_id}")
async def update(
    request: Request,
    scope: Scope,
    db: DbSession,
    client_id: int,
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
) -> Response:
    client = await _load(scope, client_id)
    try:
        await update_client(scope, client, full_name=full_name, phone=phone, email=email)
    except DomainError as exc:
        return render(
            request,
            "clients/form.html",
            {
                "client": client,
                "error": exc.message,
                "values": {"full_name": full_name, "phone": phone, "email": email},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()
    return RedirectResponse(f"/clientes/{client.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/clientes/{client_id}/archivar")
async def archive(scope: Scope, db: DbSession, client_id: int) -> Response:
    client = await _load(scope, client_id)
    await archive_client(scope, client)
    await db.commit()
    return RedirectResponse(f"/clientes/{client.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/clientes/{client_id}/restaurar")
async def restore(scope: Scope, db: DbSession, client_id: int) -> Response:
    client = await _load(scope, client_id)
    await restore_client(scope, client)
    await db.commit()
    return RedirectResponse(f"/clientes/{client.id}", status_code=status.HTTP_303_SEE_OTHER)
