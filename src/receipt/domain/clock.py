"""Tiempo y calendario.

§3.4: todo se almacena en UTC. La zona de presentación **y de corte contable**
es `America/Mexico_City`. Ningún cálculo de mes usa UTC: un pago del 31 a las
23:30 hora de CDMX pertenece a ese mes y no al siguiente (AC-07).
"""

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


def format_long_date_es(moment: datetime) -> str:
    """Fecha de emisión del recibo: "7 de julio de 2026" (§8.1)."""
    local = to_local(moment)
    return f"{local.day} de {_MONTHS_ES[local.month - 1]} de {local.year}"


def format_datetime_es(moment: datetime) -> str:
    """Fecha y hora para la firma del cliente: "7 de julio de 2026, 13:42"."""
    local = to_local(moment)
    return f"{format_long_date_es(moment)}, {local:%H:%M}"
