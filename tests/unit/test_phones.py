"""Normalización de teléfonos para `wa.me` (E6), sin base de datos.

El teléfono se guarda tal como se escribió (§4.2): es lo que aparece en el
recibo y lo que el emisor reconoce. Normalizar es cosa de quien construye la
liga.
"""

import pytest

from receipt.domain.phones import to_international


class TestNumbersPeopleActuallyType:
    @pytest.mark.parametrize(
        "typed",
        [
            "5512345678",
            "55 1234 5678",
            "55-1234-5678",
            "(55) 1234 5678",
            "+52 55 1234 5678",
            "5215512345678",
            "525512345678",
        ],
    )
    def test_they_all_resolve_to_the_same_international_number(self, typed: str) -> None:
        assert to_international(typed) == "525512345678"

    def test_the_legacy_one_after_the_country_code_is_dropped(self) -> None:
        """El 1 de "+52 1" es la forma vieja de marcar a celular.

        Sigue apareciendo en agendas viejas y WhatsApp ya no lo usa: dejarlo
        abriría un chat con un número que no existe.
        """
        assert to_international("+52 1 55 1234 5678") == "525512345678"


class TestUnusableInput:
    @pytest.mark.parametrize("typed", ["", "   ", "123", "no tengo", "5512345", "5512345678901234"])
    def test_it_refuses_instead_of_guessing(self, typed: str) -> None:
        """Devolver `None` es deliberado: una liga a un número inventado abre
        una conversación con un desconocido."""
        assert to_international(typed) is None

    def test_a_landline_of_ten_digits_is_accepted(self) -> None:
        """Los fijos también tienen 10 dígitos desde 2019, y el cliente puede
        haber dado el de su casa. Si no tiene WhatsApp, lo dirá la aplicación,
        no nosotros."""
        assert to_international("55 5555 5555") == "525555555555"
