"""RN-13, RN-14 y §8.4 sin base de datos."""

import pytest

from receipt.domain.calculations import (
    balance_cents,
    choose_template,
    current_total_cents,
    net_income_cents,
    resolve_service_status,
)
from receipt.domain.enums import ReceiptTemplate, ServiceStatus


class TestRn13CurrentTotalAndBalance:
    def test_current_total_is_agreed_plus_adjustments(self) -> None:
        assert current_total_cents(230000, [10000]) == 240000

    def test_current_total_without_agreed_total_is_undefined(self) -> None:
        assert current_total_cents(None, []) is None

    def test_balance_subtracts_active_payments(self) -> None:
        assert balance_cents(230000, [], [180000]) == 50000

    def test_ac04_service_of_2300_with_payment_of_1800_leaves_500(self) -> None:
        assert balance_cents(230000, [], [180000]) == 50000

    def test_ac05_adjustment_of_100_and_payment_of_600_settles_the_service(self) -> None:
        assert current_total_cents(230000, [10000]) == 240000
        assert balance_cents(230000, [10000], [180000, 60000]) == 0

    def test_balance_without_agreed_total_is_undefined(self) -> None:
        assert balance_cents(None, [], [60000]) is None


class TestRn14NetIncome:
    def test_ac06_payment_of_1000_with_300_of_material_yields_700(self) -> None:
        assert net_income_cents(100000, 30000) == 70000

    def test_material_may_equal_the_whole_payment(self) -> None:
        assert net_income_cents(100000, 100000) == 0

    def test_material_above_the_payment_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="exceder"):
            net_income_cents(100000, 100001)

    def test_negative_material_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="negativo"):
            net_income_cents(100000, -1)


class TestServiceStatus:
    def test_ac04_partial_payment_leaves_the_service_open(self) -> None:
        assert (
            resolve_service_status(
                agreed_total_cents=230000,
                adjustment_cents=[],
                active_payment_cents=[180000],
                current_status=ServiceStatus.OPEN,
            )
            is ServiceStatus.OPEN
        )

    def test_ac05_full_payment_settles_the_service(self) -> None:
        assert (
            resolve_service_status(
                agreed_total_cents=230000,
                adjustment_cents=[10000],
                active_payment_cents=[180000, 60000],
                current_status=ServiceStatus.OPEN,
            )
            is ServiceStatus.SETTLED
        )

    def test_overpayment_also_settles(self) -> None:
        assert (
            resolve_service_status(
                agreed_total_cents=230000,
                adjustment_cents=[],
                active_payment_cents=[240000],
                current_status=ServiceStatus.OPEN,
            )
            is ServiceStatus.SETTLED
        )

    def test_service_without_agreed_total_settles_on_its_first_payment(self) -> None:
        """Regla derivada: sin esto, un pago único quedaría `open` para siempre
        con saldo indefinido y contaminaría el "por cobrar" de §9.1."""
        assert (
            resolve_service_status(
                agreed_total_cents=None,
                adjustment_cents=[],
                active_payment_cents=[60000],
                current_status=ServiceStatus.OPEN,
            )
            is ServiceStatus.SETTLED
        )

    def test_rn17_closed_uncollected_is_never_reopened_by_recalculation(self) -> None:
        assert (
            resolve_service_status(
                agreed_total_cents=230000,
                adjustment_cents=[],
                active_payment_cents=[],
                current_status=ServiceStatus.CLOSED_UNCOLLECTED,
            )
            is ServiceStatus.CLOSED_UNCOLLECTED
        )

    def test_rn22_cancelling_the_only_payment_reopens_the_service(self) -> None:
        """Un pago cancelado deja de contar para el saldo."""
        assert (
            resolve_service_status(
                agreed_total_cents=230000,
                adjustment_cents=[],
                active_payment_cents=[],
                current_status=ServiceStatus.SETTLED,
            )
            is ServiceStatus.OPEN
        )


class TestTemplateChoice:
    def test_agreed_total_implies_detailed(self) -> None:
        assert (
            choose_template(agreed_total_cents=230000, payment_count=1) is ReceiptTemplate.DETAILED
        )

    def test_several_payments_imply_detailed(self) -> None:
        assert choose_template(agreed_total_cents=None, payment_count=2) is ReceiptTemplate.DETAILED

    def test_single_payment_without_agreed_total_is_single(self) -> None:
        assert choose_template(agreed_total_cents=None, payment_count=1) is ReceiptTemplate.SINGLE
