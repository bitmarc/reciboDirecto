"""Cabeceras de seguridad (§10).

La página pública del recibo lleva `noindex` y una CSP sin terceros: §7.3 exige
que no haya tracking de terceros en `/r/{token}`.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

PUBLIC_RECEIPT_PREFIX = "/r/"

# Todo el JS y CSS se sirve desde el propio origen. `signature_pad` se instala
# como archivo estático local, no desde un CDN: un CDN en la CSP es un tercero
# con permiso de ejecutar código sobre datos de cobro.
_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")

        if request.url.path.startswith(PUBLIC_RECEIPT_PREFIX):
            # §7.3: la liga del recibo no debe indexarse. Va también como
            # cabecera además del <meta>, porque el PDF no tiene <head>.
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")

        return response
