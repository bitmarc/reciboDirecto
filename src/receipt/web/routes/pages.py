"""Panel del emisor y alias de la superficie en inglés (§6, §7.4).

Las rutas visibles van en español porque son lo que el emisor ve en la barra de
direcciones y §0 exige que todo lo visible esté en español de México. La
superficie en inglés de §6 se conserva como alias de redirección, de modo que
ambas secciones del PRD resuelven.

El panel responde **dos preguntas, en este orden y con este peso visual**
(§7.4): cuánto llevo ganado este mes y cuánto me falta por cobrar. Todo lo
demás de la pantalla va debajo y en menor tamaño, porque §15 recuerda que el
panel se mira una vez por semana mientras el recibo se emite veinte veces al
mes.
"""

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from receipt.services.accounting import overview
from receipt.web.dependencies import CurrentAccount, Scope
from receipt.web.templating import render

router = APIRouter(tags=["pages"])

# §6 define la superficie en inglés; §7 nombra las pantallas en español.
_API_ALIASES = {
    "/settings": "/ajustes",
    "/clients": "/clientes",
    "/services": "/trabajos",
    "/receipts": "/recibos",
    "/dashboard": "/",
    "/reports/monthly": "/reportes",
    "/reports/monthly.csv": "/reportes/mensual.csv",
}


@router.get("/")
async def dashboard(request: Request, scope: Scope, account: CurrentAccount) -> Response:
    return render(
        request,
        "dashboard.html",
        {"account": account, "overview": await overview(scope)},
    )


for source, target in _API_ALIASES.items():

    def _alias(request: Request, _target: str = target) -> RedirectResponse:
        # La consulta se conserva: `/reports/monthly?period=2026-07` perdería su
        # periodo si el alias la descartara, y §6 lo documenta con parámetro.
        query = request.url.query
        return RedirectResponse(
            f"{_target}?{query}" if query else _target,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    router.add_api_route(source, _alias, methods=["GET"], include_in_schema=False)
