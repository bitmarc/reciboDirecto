"""Números de teléfono para compartir por WhatsApp (E6).

Lógica pura: recibe lo que el emisor tecleó y devuelve dígitos. El teléfono se
guarda **tal como se escribió** (§4.2) porque es lo que aparece en el recibo y
lo que el emisor reconoce; normalizar es cosa de quien construye la liga, no de
quien guarda el dato.

México y solo México: §2.2 descarta la internacionalización y el multi-país, así
que la lada 52 se asume sin preguntar. El día que eso cambie, cambia aquí.
"""

import re

MEXICO_COUNTRY_CODE = "52"
NATIONAL_DIGITS = 10
"""Los números mexicanos tienen 10 dígitos desde la homologación de 2019."""

_NON_DIGITS = re.compile(r"\D")


def to_international(raw: str) -> str | None:
    """Dígitos con lada de país, listos para `wa.me`. `None` si no es utilizable.

    Acepta lo que la gente escribe de verdad: "55 1234 5678", "+52 55 1234
    5678", "(55) 1234-5678". Devolver `None` en lugar de adivinar es
    deliberado — una liga de WhatsApp a un número inventado abre una
    conversación con un desconocido.
    """
    digits = _NON_DIGITS.sub("", raw or "")

    if len(digits) == NATIONAL_DIGITS:
        return f"{MEXICO_COUNTRY_CODE}{digits}"

    if (
        digits.startswith(MEXICO_COUNTRY_CODE)
        and len(digits) == len(MEXICO_COUNTRY_CODE) + NATIONAL_DIGITS
    ):
        return digits

    # El 1 después de la lada es la forma vieja de marcar a celular. Sigue
    # apareciendo en agendas viejas y WhatsApp ya no lo usa.
    legacy = f"{MEXICO_COUNTRY_CODE}1"
    if digits.startswith(legacy) and len(digits) == len(legacy) + NATIONAL_DIGITS:
        return f"{MEXICO_COUNTRY_CODE}{digits[len(legacy) :]}"

    return None
