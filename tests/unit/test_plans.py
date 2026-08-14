"""La tabla de §11.1 y el conteo del muro, sin base de datos."""

from datetime import datetime, timedelta

import pytest

from receipt.domain.clock import LOCAL_TZ, add_months, now_utc
from receipt.domain.enums import Plan
from receipt.domain.plans import (
    FREE_MONTHLY_RECEIPTS,
    Allowance,
    entitlements_for,
    has_paid_features,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=LOCAL_TZ)


def free(issued: int) -> Allowance:
    return Allowance(period="2026-08", issued=issued, limit=FREE_MONTHLY_RECEIPTS)


def paid(issued: int) -> Allowance:
    return Allowance(period="2026-08", issued=issued, limit=None)


class TestHasPaidFeatures:
    def test_a_free_account_never_has_them(self) -> None:
        assert not has_paid_features(Plan.FREE, None, NOW)

    def test_a_paid_account_without_an_end_date_has_them(self) -> None:
        assert has_paid_features(Plan.PAID, None, NOW)

    def test_an_expired_paid_account_does_not(self) -> None:
        """No hay proceso nocturno que baje cuentas: la vigencia se evalúa al
        preguntar, y por eso una fila `paid` vencida deja de dar funciones."""
        assert not has_paid_features(Plan.PAID, NOW - timedelta(days=1), NOW)

    def test_the_boundary_belongs_to_the_past(self) -> None:
        assert not has_paid_features(Plan.PAID, NOW, NOW)
        assert has_paid_features(Plan.PAID, NOW + timedelta(seconds=1), NOW)


class TestEntitlements:
    def test_the_free_row_of_section_11_1(self) -> None:
        rights = entitlements_for(Plan.FREE, None, NOW)

        assert not rights.paid
        assert rights.monthly_receipt_limit == 10
        assert rights.analytics_months == 1
        assert not rights.can_download_report
        assert not rights.can_use_logo

    def test_the_paid_row_has_no_ceilings(self) -> None:
        rights = entitlements_for(Plan.PAID, None, NOW)

        assert rights.paid
        assert rights.monthly_receipt_limit is None
        assert rights.analytics_months is None
        assert rights.can_download_report
        assert rights.can_use_logo

    def test_an_expired_paid_account_falls_back_to_the_free_row(self) -> None:
        assert entitlements_for(Plan.PAID, NOW - timedelta(days=1), NOW) == entitlements_for(
            Plan.FREE, None, NOW
        )


class TestAllowance:
    def test_a_fresh_month_has_the_whole_quota(self) -> None:
        assert free(0).remaining == 10
        assert not free(0).blocked
        assert not free(0).warning

    def test_ac15_the_eleventh_receipt_of_the_month_is_blocked(self) -> None:
        """Con 10 emitidos, el siguiente topa el muro y es el número 11."""
        allowance = free(10)

        assert allowance.blocked
        assert allowance.remaining == 0
        assert allowance.attempted_number == 11

    def test_the_tenth_still_goes_through(self) -> None:
        assert not free(9).blocked
        assert free(9).remaining == 1

    def test_the_warning_appears_before_the_wall_and_stops_at_it(self) -> None:
        """§14.3: el aviso tiene que llegar antes de tener el efectivo en la mano."""
        assert not free(7).warning
        assert free(8).warning
        assert free(9).warning
        # Ya bloqueado no es un aviso, es un muro: la pantalla es otra.
        assert not free(10).warning

    def test_a_paid_account_is_never_blocked_nor_warned(self) -> None:
        assert not paid(500).blocked
        assert not paid(500).warning
        assert paid(500).remaining is None
        assert paid(500).unlimited

    def test_remaining_never_goes_negative(self) -> None:
        """Una cuenta que bajó de plan puede llevar más recibos que el límite."""
        assert free(14).remaining == 0
        assert free(14).blocked


class TestAddMonths:
    def test_it_adds_calendar_months(self) -> None:
        start = datetime(2026, 8, 12, 10, 0, tzinfo=LOCAL_TZ)
        assert add_months(start, 3).astimezone(LOCAL_TZ).date().isoformat() == "2026-11-12"

    def test_it_crosses_the_year(self) -> None:
        start = datetime(2026, 11, 12, 10, 0, tzinfo=LOCAL_TZ)
        assert add_months(start, 3).astimezone(LOCAL_TZ).date().isoformat() == "2027-02-12"

    def test_it_clips_a_day_that_does_not_exist_in_the_target_month(self) -> None:
        """El 31 de enero más un mes es el 28 de febrero: el 31 no existe."""
        start = datetime(2026, 1, 31, 10, 0, tzinfo=LOCAL_TZ)
        assert add_months(start, 1).astimezone(LOCAL_TZ).date().isoformat() == "2026-02-28"

    def test_it_keeps_the_time_of_day(self) -> None:
        start = datetime(2026, 8, 12, 23, 45, tzinfo=LOCAL_TZ)
        assert add_months(start, 1).astimezone(LOCAL_TZ).strftime("%H:%M") == "23:45"

    def test_zero_months_changes_nothing(self) -> None:
        moment = now_utc()
        assert add_months(moment, 0).timestamp() == pytest.approx(moment.timestamp(), abs=1)
