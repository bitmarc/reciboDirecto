"""Validación de los datos del cliente (§4.2). Lógica pura, sin base de datos."""

import pytest

from receipt.services.clients import (
    validate_full_name,
    validate_optional_email,
    validate_phone,
)
from receipt.services.errors import ValidationError
from receipt.services.names import normalize_name


class TestFullName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ana Ramírez", "Ana Ramírez"),
            ("  Ana   Ramírez  ", "Ana Ramírez"),
            ("María de los Ángeles Ruiz", "María de los Ángeles Ruiz"),
            ("Óscar Muñoz Peña", "Óscar Muñoz Peña"),
        ],
    )
    def test_valid_names_are_normalized(self, raw: str, expected: str) -> None:
        assert validate_full_name(raw) == expected

    @pytest.mark.parametrize("raw", ["Ana", "  Ana  ", "Juan", ""])
    def test_a_single_word_is_rejected(self, raw: str) -> None:
        """§4.2 lo exige: el recibo dice "Recibí de {cliente}" y un nombre de
        pila suelto no identifica a nadie meses después."""
        with pytest.raises(ValidationError):
            validate_full_name(raw)

    def test_collapsing_spaces_does_not_turn_one_word_into_two(self) -> None:
        with pytest.raises(ValidationError):
            validate_full_name("   Ana   ")

    def test_normalize_name_collapses_inner_whitespace(self) -> None:
        assert normalize_name("Ana\t\n  Ramírez") == "Ana Ramírez"


class TestPhone:
    @pytest.mark.parametrize(
        "raw", ["5512345678", "55 1234 5678", "+52 55 1234 5678", "(55) 1234-5678"]
    )
    def test_phones_are_accepted_however_they_are_written(self, raw: str) -> None:
        assert validate_phone(raw)

    def test_the_phone_is_stored_as_it_was_typed(self) -> None:
        """El teléfono aparece en el recibo tal cual lo capturó el emisor."""
        assert validate_phone("55 1234 5678") == "55 1234 5678"

    @pytest.mark.parametrize("raw", ["", "   ", "123", "no-es-un-telefono"])
    def test_missing_or_incomplete_phones_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            validate_phone(raw)


class TestEmail:
    def test_it_is_optional(self) -> None:
        assert validate_optional_email(None) is None
        assert validate_optional_email("") is None
        assert validate_optional_email("   ") is None

    def test_a_valid_email_is_normalized(self) -> None:
        assert validate_optional_email("  Ana@Example.MX ") == "ana@example.mx"

    def test_an_invalid_email_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_optional_email("no-es-un-correo")
