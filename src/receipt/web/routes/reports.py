"""Reporte mensual — `/reportes` (§6, §9.2).

Dos salidas del mismo dato: una pantalla para mirar el mes y un CSV para
llevárselo. El archivo se arma **aquí** y no en `services.accounting` porque sus
encabezados y sus etiquetas son copy en español, y el copy vive en la capa web
(`web.labels`), igual que el HTML del PDF (§3.2). El servicio devuelve filas; la
web decide cómo se leen.
"""

import csv
import io

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from starlette.requests import Request

from receipt.domain.clock import current_period, format_local_date, parse_period
from receipt.domain.money import format_plain
from receipt.security.csrf import verify_csrf
from receipt.services.accounting import MonthlyReport, monthly_report
from receipt.services.errors import DomainError
from receipt.services.plans import assert_period_visible, earliest_period, entitlements
from receipt.web import labels
from receipt.web.dependencies import CurrentAccount, Scope
from receipt.web.templating import render

router = APIRouter(tags=["reports"], dependencies=[Depends(verify_csrf)])

CSV_CONTENT_TYPE = "text/csv; charset=utf-8"

_HEADERS = (
    "Fecha",
    "Folio",
    "Cliente",
    "Trabajo",
    "Concepto",
    "Monto",
    "Material",
    "Ingreso neto",
    "Método",
    "Estado",
)


def _period(*candidates: str) -> str:
    """Periodo pedido, o el mes en curso.

    Un `YYYY-MM` inventado en la barra de direcciones no es un error del
    sistema: se cae al mes actual en lugar de responder 500.
    """
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parse_period(candidate)
        except ValueError:
            continue
        return candidate
    return current_period()


def render_monthly_csv(report: MonthlyReport) -> bytes:
    """El reporte como CSV, listo para abrirse en una hoja de cálculo (§9.2).

    Tres decisiones de formato, todas por el mismo motivo —que el archivo se
    abre en Excel y ahí se suma una columna—:

    - **BOM UTF-8.** Sin él, Excel lee `Ramírez` como `RamÃ­rez`.
    - **Terminador `\\r\\n`**, que es lo que dice el RFC 4180.
    - **Montos como `2300.00`**, sin símbolo ni separador de miles, para que
      entren como número y no como texto.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_HEADERS)

    for row in report.rows:
        writer.writerow(
            (
                format_local_date(row.paid_at),
                row.folio or "",
                row.client_name,
                row.service_code,
                row.concept,
                format_plain(row.amount_cents),
                format_plain(row.material_cents),
                # 0 cuando el renglón no cuenta: así sumar esta columna da
                # exactamente el ingreso del panel (§9.1).
                format_plain(row.income_cents),
                labels.PAYMENT_METHOD[row.method],
                # La misma etiqueta que imprime la columna «Estado» de la
                # pantalla: el archivo y la vista no pueden decir cosas
                # distintas del mismo pago.
                labels.accounting_status(row.payment_status, row.receipt_status),
            )
        )

    return buffer.getvalue().encode("utf-8-sig")


@router.get("/reportes")
async def monthly(
    request: Request,
    scope: Scope,
    account: CurrentAccount,
    periodo: str = "",
    period: str = "",
) -> Response:
    """§6 documenta `?period=YYYY-MM`; la pantalla en español usa `?periodo=`.

    Se aceptan los dos porque el alias de §6 redirige aquí conservando la
    consulta original, y ninguna de las dos superficies debería quedar rota.
    """
    chosen = _period(periodo, period)
    rights = entitlements(account)

    try:
        assert_period_visible(rights, chosen)
    except DomainError as exc:
        # RN-35. No es un 404 ni un 403 seco: §11.2 pide una pantalla clara que
        # ofrezca la suscripción, y el mes que quiso ver es justamente el mejor
        # argumento para enseñársela.
        return render(
            request,
            "plans/locked.html",
            {"account": account, "rights": rights, "message": exc.message, "period": chosen},
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
        )

    report = await monthly_report(scope, chosen)
    return render(
        request,
        "reports/monthly.html",
        {
            "report": report,
            "rights": rights,
            "earliest_period": earliest_period(rights),
        },
    )


@router.get("/reportes/mensual.csv")
async def monthly_csv(
    request: Request,
    scope: Scope,
    account: CurrentAccount,
    periodo: str = "",
    period: str = "",
) -> Response:
    chosen = _period(periodo, period)
    rights = entitlements(account)

    if not rights.can_download_report:
        # Decisión del responsable del producto: la descarga es función de pago.
        # §9.2 solo dice que en gratuito "la consulta se limita", así que esto va
        # un paso más allá del PRD y queda anotado para revisarse con datos.
        return render(
            request,
            "plans/locked.html",
            {
                "account": account,
                "rights": rights,
                "message": (
                    "La descarga del reporte es del plan de pago. En pantalla "
                    "puedes ver el mes en curso completo."
                ),
                "period": chosen,
            },
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
        )

    report = await monthly_report(scope, chosen)
    return Response(
        content=render_monthly_csv(report),
        media_type=CSV_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="ingresos-{chosen}.csv"'},
    )
