"""Aviso de privacidad — `/privacidad` (§10).

**Sin dependencia de sesión**, y es lo importante de esta ruta: quien todavía no
tiene cuenta necesita leer el aviso *antes* de registrarse, y quien pide que
borren sus datos suele haber perdido el acceso. Un aviso detrás del inicio de
sesión no cumple su función.

`render()` ya resuelve `is_authenticated`, así que la barra de navegación
aparece sola cuando hay sesión y desaparece cuando no, sin código aquí.

Los términos (`/terminos`) todavía no existen: dependen de §14.2, que sigue
abierta. Ver `docs/pendientes.md`.
"""

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response

from receipt.web.legal import AVISO_VIGENTE_DESDE, DE_LOS_CLIENTES, DEL_EMISOR, TECNICOS, identidad
from receipt.web.templating import render

router = APIRouter(tags=["legal"])


@router.get("/privacidad")
async def privacy_notice(request: Request) -> Response:
    return render(
        request,
        "legal/privacidad.html",
        {
            "identidad": identidad(),
            "del_emisor": DEL_EMISOR,
            "de_los_clientes": DE_LOS_CLIENTES,
            "tecnicos": TECNICOS,
            "vigente_desde": AVISO_VIGENTE_DESDE,
        },
    )
