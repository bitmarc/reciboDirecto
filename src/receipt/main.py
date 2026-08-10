"""Punto de entrada de la aplicación."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from receipt.config import get_settings
from receipt.db import dispose_engine
from receipt.logging_config import configure_logging
from receipt.security.headers import SecurityHeadersMiddleware
from receipt.web.dependencies import NotAuthenticated, redirect_to_login
from receipt.web.routes import auth, charge, clients, pages, public, receipts, work
from receipt.web.routes import settings as settings_routes
from receipt.web.templating import STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.debug)

    app = FastAPI(
        title="Recibo",
        description="Comprobantes de pago no fiscales",
        version="0.1.0",
        lifespan=lifespan,
        # La documentación interactiva no se publica: la superficie es una app
        # web, no una API para terceros.
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(auth.router)
    app.include_router(settings_routes.router)
    app.include_router(clients.router)
    app.include_router(charge.router)
    app.include_router(work.router)
    app.include_router(receipts.router)
    # La liga pública no lleva sesión ni CSRF: el token es la credencial (§3.3).
    app.include_router(public.router)
    # Las páginas van al final: registran los alias de §6, que son comodines
    # respecto de las rutas concretas de arriba.
    app.include_router(pages.router)

    @app.exception_handler(NotAuthenticated)
    async def _handle_not_authenticated(request: Request, exc: NotAuthenticated) -> Response:
        return redirect_to_login(request)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


app = create_app()
