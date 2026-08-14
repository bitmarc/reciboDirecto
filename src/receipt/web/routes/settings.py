"""Perfil del emisor, firma y logo — `/ajustes` (E1, §7.5)."""

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.infra.blob import BlobNotFound
from receipt.infra.mail import MailDeliveryError
from receipt.security.csrf import verify_csrf
from receipt.services.auth_tokens import send_email_verification
from receipt.services.errors import DomainError
from receipt.services.media import remove_logo, store_logo, store_signature
from receipt.services.plans import entitlements
from receipt.web.dependencies import (
    Blobs,
    CurrentAccount,
    CurrentUser,
    DbSession,
    MailerDep,
    not_found,
)
from receipt.web.templating import render

router = APIRouter(tags=["settings"], dependencies=[Depends(verify_csrf)])

# Tamaño máximo que se lee del cuerpo antes de validar. La validación real la
# hace domain/images.py; esto solo evita cargar en memoria un archivo enorme.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024


def _back(message: str | None = None, error: str | None = None) -> RedirectResponse:
    """Redirección tras POST, para que recargar no reenvíe el formulario."""
    params = []
    if message:
        params.append(f"aviso={message}")
    if error:
        params.append(f"error={error}")
    suffix = f"?{'&'.join(params)}" if params else ""
    return RedirectResponse(f"/ajustes{suffix}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/ajustes")
async def settings_page(
    request: Request,
    account: CurrentAccount,
    user: CurrentUser,
    aviso: str = "",
    error: str = "",
) -> Response:
    rights = entitlements(account)
    return render(
        request,
        "settings/index.html",
        {
            "account": account,
            "user": user,
            # RN-34 se decide con la vigencia, no solo con la columna `plan`:
            # una cuenta con `plan = paid` y fecha vencida ya no tiene funciones
            # de pago, y ofrecerle subir un logo que no se estamparía sería
            # mentirle. La misma función que usa el motor de cobro al emitir.
            "can_use_logo": rights.can_use_logo,
            "rights": rights,
            "notice": aviso or None,
            "error": error or None,
        },
    )


@router.post("/ajustes")
async def update_profile(
    account: CurrentAccount,
    db: DbSession,
    display_name: str = Form(...),
    phone: str = Form(...),
    default_city: str = Form(...),
    occupation: str = Form(""),
) -> Response:
    """Actualiza el perfil que aparece como emisor en el recibo.

    `AC-02` exige que esto **no** altere los recibos ya emitidos. Lo garantiza
    el snapshot del hito 2, no esta ruta: aquí los datos vivos cambian con
    normalidad.

    §6 lo especifica como `PATCH /settings`. Se usa POST porque los formularios
    HTML solo hablan GET y POST; añadir un PATCH que nadie llama sería código
    muerto.
    """
    if not display_name.strip() or not phone.strip() or not default_city.strip():
        return _back(error="Nombre, teléfono y ciudad son obligatorios.")

    account.display_name = display_name.strip()
    account.phone = phone.strip()
    account.default_city = default_city.strip()
    account.occupation = occupation.strip() or None
    await db.commit()
    return _back("Datos actualizados.")


@router.post("/ajustes/firma")
async def save_signature(
    account: CurrentAccount,
    db: DbSession,
    blobs: Blobs,
    signature_data: str = Form(...),
) -> Response:
    """Guarda la firma dibujada en el canvas (RN-20).

    Cada guardado escribe un blob nuevo; el anterior nunca se sobrescribe.
    """
    try:
        await store_signature(db, blobs, account, data_uri=signature_data)
    except DomainError as exc:
        return _back(error=exc.message)

    await db.commit()
    return _back("Firma guardada.")


@router.post("/ajustes/logo")
async def save_logo(
    account: CurrentAccount,
    db: DbSession,
    blobs: Blobs,
    logo: UploadFile = File(...),
) -> Response:
    raw = await logo.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return _back(error="El archivo es demasiado grande.")

    try:
        await store_logo(db, blobs, account, raw=raw)
    except DomainError as exc:
        return _back(error=exc.message)

    await db.commit()
    return _back("Logo guardado.")


@router.post("/ajustes/logo/quitar")
async def delete_logo(account: CurrentAccount, db: DbSession) -> Response:
    await remove_logo(db, account)
    await db.commit()
    return _back("Logo quitado.")


@router.post("/ajustes/verificar-correo")
async def resend_verification(user: CurrentUser, db: DbSession, mailer: MailerDep) -> Response:
    if user.email_verified_at is not None:
        return _back("Tu correo ya está confirmado.")

    try:
        await send_email_verification(db, mailer, user)
    except MailDeliveryError:
        # Aquí el error sí se dice. No hay nada que filtrar: el usuario está
        # autenticado y es su propia dirección, así que callar solo lo dejaría
        # esperando una liga que nunca pidió dos veces.
        return _back(error="No pudimos enviar el correo. Inténtalo en unos minutos.")

    await db.commit()
    return _back("Te enviamos la liga de confirmación.")


# ---------------------------------------------------------------------------
# Las imágenes se sirven a través de la aplicación, nunca por URL directa al
# blob: §10 clasifica el PNG de una firma como dato personal. El contenedor es
# privado (§3.3), así que estas rutas son el único camino.
#
# La página pública del recibo (hito 3) no puede usar estas rutas porque exigen
# sesión: el renderizador traerá los bytes e incrustará un `data:` URI, que es
# además lo que WeasyPrint necesita para el PDF.
# ---------------------------------------------------------------------------

_IMAGE_HEADERS = {
    # La ruta es estable mientras el blob cambia con cada recaptura, así que no
    # se puede cachear por tiempo. `private` impide además que un proxy guarde
    # la firma de alguien.
    "Cache-Control": "private, no-cache, max-age=0, must-revalidate"
}


async def _serve(blobs: Blobs, path: str | None) -> Response:
    if not path:
        raise not_found()
    try:
        data = await blobs.download(path)
    except BlobNotFound as exc:
        raise not_found() from exc
    return Response(content=data, media_type="image/png", headers=_IMAGE_HEADERS)


@router.get("/ajustes/firma.png")
async def signature_image(account: CurrentAccount, blobs: Blobs) -> Response:
    return await _serve(blobs, account.signature_blob_path)


@router.get("/ajustes/logo.png")
async def logo_image(account: CurrentAccount, blobs: Blobs) -> Response:
    return await _serve(blobs, account.logo_blob_path)
