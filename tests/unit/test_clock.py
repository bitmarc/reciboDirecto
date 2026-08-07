"""Corte contable en hora de CDMX (§3.4, AC-07)."""

from datetime import UTC, datetime

import pytest

from receipt.domain.clock import (
    LOCAL_TZ,
    format_long_date_es,
    month_bounds_utc,
    period_of,
    recent_periods,
    shift_period,
)


class TestAc07MonthBoundary:
    def test_payment_at_23_30_on_january_31_belongs_to_january(self) -> None:
        """El caso literal de AC-07.

        En UTC ese instante ya es 1 de febrero; el corte usa la fecha local.
        """
        paid_at = datetime(2026, 1, 31, 23, 30, tzinfo=LOCAL_TZ)

        assert paid_at.astimezone(UTC).month == 2, "el instante ya es febrero en UTC"
        assert period_of(paid_at) == "2026-01"

    def test_payment_at_00_30_on_february_1_belongs_to_february(self) -> None:
        paid_at = datetime(2026, 2, 1, 0, 30, tzinfo=LOCAL_TZ)
        assert period_of(paid_at) == "2026-02"

    def test_january_bounds_cover_the_boundary_payment(self) -> None:
        start, end = month_bounds_utc("2026-01")
        paid_at = datetime(2026, 1, 31, 23, 30, tzinfo=LOCAL_TZ).astimezone(UTC)
        assert start <= paid_at < end

    def test_bounds_are_half_open_so_months_never_overlap(self) -> None:
        _, january_end = month_bounds_utc("2026-01")
        february_start, _ = month_bounds_utc("2026-02")
        assert january_end == february_start


class TestPeriods:
    def test_naive_datetimes_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="ingenuo"):
            period_of(datetime(2026, 1, 31, 23, 30))

    def test_december_rolls_into_january(self) -> None:
        assert shift_period("2025-12", 1) == "2026-01"

    def test_january_rolls_back_into_december(self) -> None:
        assert shift_period("2026-01", -1) == "2025-12"

    def test_recent_periods_returns_the_six_month_window_of_the_dashboard(self) -> None:
        assert recent_periods("2026-03", 6) == [
            "2025-10",
            "2025-11",
            "2025-12",
            "2026-01",
            "2026-02",
            "2026-03",
        ]

    def test_invalid_period_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Periodo inválido"):
            month_bounds_utc("2026/01")

    def test_month_out_of_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="fuera de rango"):
            month_bounds_utc("2026-13")


class TestPresentation:
    def test_receipt_header_date_is_written_in_spanish(self) -> None:
        moment = datetime(2026, 7, 7, 13, 42, tzinfo=LOCAL_TZ)
        assert format_long_date_es(moment) == "7 de julio de 2026"
