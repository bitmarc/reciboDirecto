"""Registro, inicio y cierre de sesión (E1).

Las rutas visibles al usuario van en español (§7); las de §6 se conservan como
alias para que la superficie de API del PRD resuelva.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from receipt.security.csrf import verify_csrf
from receipt.security.sessions import clear_session, issue_session
from receipt.services.accounts import authenticate, register_account
from receipt.services.errors import DomainError
from receipt.web.dependencies import DbSession
from receipt.web.templating import render

router = APIRouter(tags=["auth"])


@router.get("/entrar")
async def login_form(request: Request, next: str = "/") -> Response:
    return render(request, "auth/login.html", {"next": next})


@router.post("/auth/login")
async def login(
    request: Request,
    db: DbSession,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
) -> Response:
    user = await authenticate(db, email=email, password=password)
    if user is None:
        return render(
            request,
            "auth/login.html",
            {"error": "Correo o contraseña incorrectos.", "email": email, "next": next},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    await db.commit()

    # Solo rutas internas: un `next` absoluto sería un redirector abierto.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user_id=user.id, account_id=user.account_id)
    return response


@router.get("/registro")
async def register_form(request: Request) -> Response:
    return render(request, "auth/register.html", {})


@router.post("/auth/register")
async def register(
    request: Request,
    db: DbSession,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    phone: str = Form(...),
    default_city: str = Form("Ciudad de México"),
    occupation: str = Form(""),
) -> Response:
    try:
        user = await register_account(
            db,
            email=email,
            password=password,
            display_name=display_name,
            phone=phone,
            default_city=default_city,
            occupation=occupation,
        )
    except DomainError as exc:
        return render(
            request,
            "auth/register.html",
            {
                "error": exc.message,
                "email": email,
                "display_name": display_name,
                "phone": phone,
                "default_city": default_city,
                "occupation": occupation,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await db.commit()
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user_id=user.id, account_id=user.account_id)
    return response


@router.post("/auth/logout", dependencies=[Depends(verify_csrf)])
async def logout() -> Response:
    response = RedirectResponse("/entrar", status_code=status.HTTP_303_SEE_OTHER)
    clear_session(response)
    return response
