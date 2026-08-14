"""Plan y cupones — `/planes` (E8, §7.5, §11).

Una sola pantalla: en qué plan estás, cuánto llevas del mes, qué te daría el
plan de pago y dónde escribir un cupón. Es también la pantalla que §11.2 exige
cuando el muro muerde, y por eso la ruta de cobro redirige aquí con el motivo.

**No hay pasarela.** §14.5 sigue abierta y §11.4 recomienda no construirla hasta
saber si alguien quiere pagar. Mientras tanto el plan de pago se concede con un
cupón de meses gratis o a mano desde la base de datos, que es lo que esa misma
sección propone para la validación. Cuando exista la pasarela, el botón de
suscripción entra en esta pantalla y nada más cambia.
"""

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from receipt.domain.plans import FREE_ANALYTICS_MONTHS, FREE_MONTHLY_RECEIPTS
from receipt.security.csrf import verify_csrf
from receipt.services.errors import DomainError
from receipt.services.plans import allowance_of, entitlements, redeem_coupon
from receipt.web.dependencies import CurrentAccount, DbSession, Scope
from receipt.web.templating import render

router = APIRouter(tags=["plans"], dependencies=[Depends(verify_csrf)])


async def _context(db: DbSession, account: CurrentAccount) -> dict[str, object]:
    return {
        "account": account,
        "rights": entitlements(account),
        "allowance": await allowance_of(db, account),
        "free_monthly_receipts": FREE_MONTHLY_RECEIPTS,
        "free_analytics_months": FREE_ANALYTICS_MONTHS,
    }


@router.get("/planes")
async def plans_page(
    request: Request,
    db: DbSession,
    account: CurrentAccount,
    aviso: str = "",
    error: str = "",
    bloqueo: bool = False,
) -> Response:
    """`bloqueo` la marca la ruta de cobro al rebotar por RN-33."""
    context = await _context(db, account)
    context["blocked_now"] = bloqueo
    context["notice"] = aviso or None
    context["error"] = error or None
    return render(request, "plans/index.html", context)


@router.post("/planes/cupon")
async def redeem(
    scope: Scope,
    db: DbSession,
    account: CurrentAccount,
    code: str = Form(""),
) -> Response:
    """Canjea un cupón de meses gratis (§11.3)."""
    try:
        result = await redeem_coupon(scope, account, code)
    except DomainError as exc:
        # Sin rollback: `redeem_coupon` deshace su propio savepoint al chocar
        # con el canje repetido, y las demás validaciones ocurren antes de
        # tocar la cuenta.
        return RedirectResponse(
            f"/planes?error={exc.message}", status_code=status.HTTP_303_SEE_OTHER
        )

    await db.commit()
    meses = "mes" if result.months_granted == 1 else "meses"
    return RedirectResponse(
        f"/planes?aviso=Listo: {result.months_granted} {meses} de plan de pago.",
        status_code=status.HTTP_303_SEE_OTHER,
    )
