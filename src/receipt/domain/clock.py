"""Tiempo y calendario.

§3.4: todo se almacena en UTC. La zona de presentación **y de corte contable**
es `America/Mexico_City`. Ningún cálculo de mes usa UTC: un pago del 31 a las
23:30 hora de CDMX pertenece a ese mes y no al siguiente (AC-07).
"""

from calendar import monthrange
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Mexico_City")

_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_local(moment: datetime) -> datetime:
    """Convierte a hora de CDMX. Rechaza datetimes ingenuos a propósito."""
    if moment.tzinfo is None:
        raise ValueError("Se esperaba un datetime con zona horaria; llegó uno ingenuo")
    return moment.astimezone(LOCAL_TZ)


def period_of(moment: datetime) -> str:
    """Periodo contable `YYYY-MM` al que pertenece un instante, en hora local."""
    local = to_local(moment)
    return f"{local.year:04d}-{local.month:02d}"


def current_period() -> str:
    return period_of(now_utc())


def parse_period(period: str) -> tuple[int, int]:
    try:
        year_text, month_text = period.split("-")
        year, month = int(year_text), int(month_text)
    except ValueError as exc:
        raise ValueError(f"Periodo inválido: {period!r}. Se esperaba YYYY-MM") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"Mes fuera de rango en el periodo {period!r}")
    return year, month


def month_bounds_utc(period: str) -> tuple[datetime, datetime]:
    """Límites `[inicio, fin)` de un periodo, en UTC.

    Los límites se construyen sobre el calendario **local** y luego se convierten,
    que es lo que hace que el corte mensual caiga donde el usuario lo espera.
    """
    year, month = parse_period(period)
    start_local = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end_local = datetime(next_year, next_month, 1, tzinfo=LOCAL_TZ)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def add_months(moment: datetime, months: int) -> datetime:
    """Suma meses de calendario, contados sobre el calendario local.

    Un cupón de "3 meses gratis" concede tres meses de calendario, no noventa
    días. Cuando el día no existe en el mes destino se recorta al último: el 31
    de enero más un mes es el 28 de febrero, porque el 31 de febrero no existe.
    """
    local = to_local(moment)
    total = local.year * 12 + (local.month - 1) + months
    year, month = total // 12, total % 12 + 1
    day = min(local.day, monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(UTC)


def shift_period(period: str, months: int) -> str:
    """Desplaza un periodo. `shift_period("2026-01", -1) == "2025-12"`."""
    year, month = parse_period(period)
    total = year * 12 + (month - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def recent_periods(period: str, count: int) -> list[str]:
    """Los `count` periodos que terminan en `period`, del más antiguo al más nuevo."""
    if count < 1:
        raise ValueError("count debe ser al menos 1")
    return [shift_period(period, offset) for offset in range(-(count - 1), 1)]


def days_between(start: datetime, end: datetime) -> int:
    """Días completos entre dos instantes, contados sobre el calendario local.

    La antigüedad de un adeudo se cuenta en días de calendario y no en periodos
    de 24 horas: un trabajo de ayer por la noche tiene un día, no cero.
    """
    return (to_local(end).date() - to_local(start).date()).days


def format_age_es(days: int) -> str:
    """Antigüedad de un adeudo, en palabras (§7.4).

    Hasta dos meses se dicen los días, porque es el plazo en que el emisor aún
    recuerda el trabajo. Más allá, el número exacto deja de importar y lo que
    pesa es el "van tres meses" que hace incómoda la llamada de cobro.
    """
    if days <= 0:
        return "hoy"
    if days == 1:
        return "1 día"
    if days < 60:
        return f"{days} días"
    return f"{days // 30} meses"


def format_long_date_es(moment: datetime) -> str:
    """Fecha de emisión del recibo: "7 de julio de 2026" (§8.1)."""
    local = to_local(moment)
    return f"{local.day} de {_MONTHS_ES[local.month - 1]} de {local.year}"


def format_month_es(period: str) -> str:
    """`"2026-07"` → `"julio de 2026"`. Encabeza el panel y el reporte."""
    year, month = parse_period(period)
    return f"{_MONTHS_ES[month - 1]} de {year}"


def format_local_date(moment: datetime) -> str:
    """`"2026-07-31"` en hora de CDMX: la fecha de corte del CSV (§9.2).

    En ISO y no en `31/07/2026` porque así ordena bien en cualquier hoja de
    cálculo y no se confunde con el formato de otro país.
    """
    return f"{to_local(moment):%Y-%m-%d}"


def format_datetime_es(moment: datetime) -> str:
    """Fecha y hora para la firma del cliente: "7 de julio de 2026, 13:42"."""
    local = to_local(moment)
    return f"{format_long_date_es(moment)}, {local:%H:%M}"
