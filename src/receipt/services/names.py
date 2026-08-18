"""Nombres de personas: normalización y la regla de «nombre + apellido».

Vive fuera de `clients.py` y de `accounts.py` porque las dos capas necesitan la
**misma regla** con copy distinto: el cliente es a quién le cobraste (§4.2) y el
emisor es quien firma (§8.1). Dejarla en `clients.py` y llamarla desde
`accounts.py` cerraría un ciclo de importación —`clients` ya importa
`normalize_email` de `accounts`— y duplicarla la dejaría desincronizada a la
primera edición.

El mensaje llega por parámetro y no se arma aquí: una regla común no puede
elegir el copy de dos pantallas que hablan de personas distintas.
"""

import re

from receipt.services.errors import ValidationError

_WHITESPACE = re.compile(r"\s+")

MIN_NAME_WORDS = 2
"""Nombre y al menos un apellido."""


def normalize_name(raw: str) -> str:
    """Recorta y colapsa espacios internos.

    "  Ana   Ramírez " y "Ana Ramírez" son la misma persona; sin normalizar, el
    buscador y el conteo de palabras verían dos cosas distintas.
    """
    return _WHITESPACE.sub(" ", raw).strip()


def validate_person_name(raw: str, *, missing: str, incomplete: str) -> str:
    """Obligatorio, mínimo nombre + un apellido. Devuelve el nombre normalizado.

    Un nombre de pila suelto no identifica a nadie meses después, que es justo
    cuando se consulta el comprobante.
    """
    name = normalize_name(raw)
    if not name:
        raise ValidationError(missing)
    if len(name.split(" ")) < MIN_NAME_WORDS:
        raise ValidationError(incomplete)
    return name
