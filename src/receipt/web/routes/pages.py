"""Pantallas del emisor (§7.5).

Las rutas visibles van en español porque son lo que el emisor ve en la barra de
direcciones y §0 exige que todo lo visible esté en español de México. La
superficie en inglés de §6 se conserva como alias de redirección, de modo que
ambas secciones del PRD resuelven.

Las pantallas de contenido llegan en sus hitos: `/clientes` y `/ajustes` en el 1,
`/trabajos` y `/recibos` en el 2, `/cobrar` en el 4.
"""

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from receipt.web.dependencies import CurrentAccount
from receipt.web.templating import render

router = APIRouter(tags=["pages"])

# §6 define la superficie en inglés; §7 nombra las pantallas en español.
_API_ALIASES = {
    "/settings": "/ajustes",
    "/clients": "/clientes",
    "/services": "/trabajos",
    "/receipts": "/recibos",
}


@router.get("/")
async def dashboard(request: Request, account: CurrentAccount) -> Response:
    return render(request, "dashboard.html", {"account": account})


for source, target in _API_ALIASES.items():

    def _alias(_target: str = target) -> RedirectResponse:
        return RedirectResponse(_target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    router.add_api_route(source, _alias, methods=["GET"], include_in_schema=False)
