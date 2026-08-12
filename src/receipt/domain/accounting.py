"""Aritmética del panel y del reporte (E7, §9).

§9.1 fija tres definiciones y la más delicada es la del ingreso, porque depende
de **dos** estados a la vez: el del pago y el de su recibo. Esa regla vive aquí,
pura y probada sin base de datos, para que el panel y el CSV la respondan igual.
Si divergieran, el emisor vería un total en pantalla y otro en su archivo, y no
tendría manera de saber cuál creer.

El cálculo de montos no se repite: `current_total`, `balance` e ingreso neto de
un pago ya viven en `domain.calculations` (RN-13, RN-14). Aquí solo está lo que
la contabilidad agrega encima.
"""

from collections.abc import Sequence

from receipt.domain.enums import PaymentStatus, ReceiptStatus

SERIES_MONTHS = 6
"""§7.4: la comparación del panel abarca seis meses."""


def counts_as_income(
    *,
    payment_status: PaymentStatus,
    receipt_status: ReceiptStatus | None,
) -> bool:
    """§9.1: aporta al ingreso el pago `active` cuyo recibo no esté `cancelled`.

    Se comprueban los dos estados aunque hoy `cancel_receipt` cancele siempre
    ambos a la vez. La regla está escrita sobre los dos y un pago puede existir
    sin recibo, así que `None` —sin recibo— cuenta como ingreso: el dinero
    entró aunque nadie haya pedido comprobante.
    """
    if payment_status is not PaymentStatus.ACTIVE:
        return False
    return receipt_status is not ReceiptStatus.CANCELLED


def bar_percentages(values: Sequence[int]) -> list[int]:
    """Largo relativo de cada barra, en porcentaje entero del mayor valor.

    Sin JS y sin librería de gráficas: la CSP no admite scripts de terceros
    (§10) y seis barras no justifican ninguna. El porcentaje se calcula con
    enteros porque el sistema no usa `float` (§3.4); aquí no es dinero sino
    ancho, pero la regla se sostiene igual de gratis.

    Un mes sin ingreso queda en 0 y su renglón se sigue dibujando, porque la
    barra vacía **es** el dato: ese mes no entró nada.
    """
    top = max(values, default=0)
    if top <= 0:
        return [0 for _ in values]
    return [max(value, 0) * 100 // top for value in values]
