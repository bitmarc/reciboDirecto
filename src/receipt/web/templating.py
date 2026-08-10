"""Jinja2 con los ayudantes de presentación del dominio.

Todo el texto visible va en español de México (§0). El formateo de dinero y
fechas se expone aquí para que ninguna plantilla lo reimplemente a mano.
"""

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from receipt.domain.clock import format_datetime_es, format_long_date_es, to_local
from receipt.domain.money import amount_in_words, format_pesos
from receipt.security.csrf import CSRF_FIELD_NAME
from receipt.security.sessions import read_session
from receipt.web import labels

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field_name"] = CSRF_FIELD_NAME

# Etiquetas del dominio, disponibles en toda plantilla sin pasarlas por contexto.
templates.env.globals["estado_trabajo"] = labels.SERVICE_STATUS
templates.env.globals["estado_recibo"] = labels.RECEIPT_STATUS
templates.env.globals["estado_pago"] = labels.PAYMENT_STATUS
templates.env.globals["metodo_pago"] = labels.PAYMENT_METHOD_BY_VALUE
templates.env.filters["pesos"] = format_pesos
templates.env.filters["pesos_en_letra"] = amount_in_words
templates.env.filters["fecha_larga"] = format_long_date_es
templates.env.filters["fecha_hora"] = format_datetime_es
templates.env.filters["hora_local"] = to_local


def render(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> Response:
    """Renderiza inyectando el token CSRF de la sesión, si la hay."""
    merged: dict[str, Any] = {"request": request}
    session = read_session(request)
    merged["csrf_token"] = session.csrf_token if session else ""
    merged["is_authenticated"] = session is not None
    if context:
        merged.update(context)
    return templates.TemplateResponse(request, template_name, merged, status_code=status_code)
