"""Dinero en centavos y monto en letra (§3.4, §8.2)."""

import pytest

from receipt.domain.money import amount_in_words, format_pesos


class TestFormatting:
    @pytest.mark.parametrize(
        ("cents", "expected"),
        [
            (0, "$0.00"),
            (1, "$0.01"),
            (100, "$1.00"),
            (50000, "$500.00"),
            (230000, "$2,300.00"),
            (123456789, "$1,234,567.89"),
        ],
    )
    def test_cents_render_as_pesos(self, cents: int, expected: str) -> None:
        assert format_pesos(cents) == expected

    def test_no_rounding_error_creeps_in(self) -> None:
        """El motivo de prohibir `float`: 0.1 + 0.2 no da 0.3 en binario."""
        assert format_pesos(10 + 20) == "$0.30"


class TestAmountInWords:
    @pytest.mark.parametrize(
        ("cents", "expected"),
        [
            (230000, "DOS MIL TRESCIENTOS PESOS 00/100 M.N."),
            (100, "UN PESO 00/100 M.N."),
            (150, "UN PESO 50/100 M.N."),
            (2100, "VEINTIÚN PESOS 00/100 M.N."),
            (3100, "TREINTA Y UN PESOS 00/100 M.N."),
            (60050, "SEISCIENTOS PESOS 50/100 M.N."),
            (0, "CERO PESOS 00/100 M.N."),
        ],
    )
    def test_amounts_are_written_in_mexican_convention(self, cents: int, expected: str) -> None:
        assert amount_in_words(cents) == expected

    def test_negative_amounts_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="negativos"):
            amount_in_words(-100)
