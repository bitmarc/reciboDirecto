"""Dinero.

§3.4: enteros en centavos, nunca `float`. Ninguna operación de este módulo
convierte a punto flotante, ni siquiera para formatear.
"""

from num2words import num2words

CURRENCY = "MXN"


def format_pesos(cents: int) -> str:
    """`230000` → `"$2,300.00"`."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{fraction:02d}"


def format_plain(cents: int) -> str:
    """`230000` → `"2300.00"`. El formato de dinero del CSV (§9.2).

    Sin símbolo y sin separador de miles: `$2,300.00` entra a una hoja de
    cálculo como texto y deja de poder sumarse, que es justo para lo que el
    emisor abre el archivo.
    """
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole}.{fraction:02d}"


def amount_in_words(cents: int) -> str:
    """Monto en letra para la plantilla `single` (§8.2).

    `230000` → `"DOS MIL TRESCIENTOS PESOS 00/100 M.N."`
    """
    if cents < 0:
        raise ValueError("No existen montos negativos en un recibo")

    whole, fraction = divmod(cents, 100)
    words = num2words(whole, lang="es")

    # num2words devuelve "uno"/"veintiuno"; frente a un sustantivo, el español
    # apocopa a "un"/"veintiún".
    if words.endswith("veintiuno"):
        words = f"{words[: -len('veintiuno')]}veintiún"
    elif words.endswith("uno"):
        words = f"{words[: -len('uno')]}un"

    noun = "PESO" if whole == 1 else "PESOS"
    return f"{words.upper()} {noun} {fraction:02d}/100 M.N."
