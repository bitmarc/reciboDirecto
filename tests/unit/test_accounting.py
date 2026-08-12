"""Aritmética del panel (§7.4, §9.1), sin base de datos."""

import pytest

from receipt.domain.accounting import SERIES_MONTHS, bar_percentages, counts_as_income
from receipt.domain.clock import format_age_es, format_month_es
from receipt.domain.enums import PaymentStatus, ReceiptStatus
from receipt.domain.money import format_plain


class TestCountsAsIncome:
    """§9.1 depende de dos estados a la vez, y ese es todo el riesgo."""

    def test_an_active_payment_with_an_issued_receipt_counts(self) -> None:
        assert counts_as_income(
            payment_status=PaymentStatus.ACTIVE,
            receipt_status=ReceiptStatus.ISSUED,
        )

    def test_a_payment_without_a_receipt_counts(self) -> None:
        """El dinero entró aunque nadie haya pedido comprobante."""
        assert counts_as_income(payment_status=PaymentStatus.ACTIVE, receipt_status=None)

    def test_a_cancelled_payment_does_not_count(self) -> None:
        assert not counts_as_income(
            payment_status=PaymentStatus.CANCELLED,
            receipt_status=ReceiptStatus.CANCELLED,
        )

    def test_an_active_payment_with_a_cancelled_receipt_does_not_count(self) -> None:
        """La combinación que hoy no se produce sola.

        `cancel_receipt` cancela siempre las dos cosas, así que este estado solo
        aparecería por una ruta futura que cancelara el recibo sin tocar el
        pago. §9.1 exige las dos condiciones y la prueba las exige también: si
        alguien "simplifica" la función a mirar solo el pago, esto falla.
        """
        assert not counts_as_income(
            payment_status=PaymentStatus.ACTIVE,
            receipt_status=ReceiptStatus.CANCELLED,
        )


class TestBarPercentages:
    def test_the_largest_month_fills_the_track(self) -> None:
        assert bar_percentages([50000, 100000, 25000]) == [50, 100, 25]

    def test_a_month_without_income_stays_at_zero(self) -> None:
        assert bar_percentages([0, 100000]) == [0, 100]

    def test_six_empty_months_do_not_divide_by_zero(self) -> None:
        """Una cuenta recién creada abre el panel con la gráfica en blanco."""
        assert bar_percentages([0] * SERIES_MONTHS) == [0] * SERIES_MONTHS

    def test_an_empty_series_is_not_an_error(self) -> None:
        assert bar_percentages([]) == []

    def test_percentages_are_integers(self) -> None:
        """§3.4: nada de `float`. Aquí no es dinero, pero la regla sale gratis."""
        assert all(isinstance(value, int) for value in bar_percentages([333, 1000]))


class TestFormatAgeEs:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (0, "hoy"),
            (1, "1 día"),
            (2, "2 días"),
            (59, "59 días"),
            (60, "2 meses"),
            (95, "3 meses"),
        ],
    )
    def test_reads_like_a_person_would_say_it(self, days: int, expected: str) -> None:
        assert format_age_es(days) == expected

    def test_a_negative_age_does_not_produce_nonsense(self) -> None:
        """Un reloj desfasado no debe imprimir "-3 días" en el panel."""
        assert format_age_es(-3) == "hoy"


class TestFormatMonthEs:
    def test_names_the_month_in_spanish(self) -> None:
        assert format_month_es("2026-07") == "julio de 2026"


class TestFormatPlain:
    """El formato de dinero del CSV (§9.2): tiene que entrar como número."""

    @pytest.mark.parametrize(
        ("cents", "expected"),
        [(230000, "2300.00"), (0, "0.00"), (5, "0.05"), (100, "1.00")],
    )
    def test_writes_pesos_with_two_decimals(self, cents: int, expected: str) -> None:
        assert format_plain(cents) == expected

    def test_has_no_symbol_or_thousands_separator(self) -> None:
        """`$1,234.56` entra a una hoja de cálculo como texto y deja de sumarse."""
        written = format_plain(123456)
        assert "$" not in written
        assert "," not in written
