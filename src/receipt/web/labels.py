"""Cómo se llama en español cada valor del dominio (§0, §16).

Los identificadores van en inglés y el copy en español. La traducción vive aquí
y no en cada plantilla ni en cada ruta: un mismo estado tiene que leerse igual en
`/trabajos`, en `/recibos` y en el detalle del cliente. `templating` las registra
como globales de Jinja, así que ninguna ruta necesita pasarlas en el contexto.
"""

from receipt.domain.enums import (
    PaymentMethod,
    PaymentStatus,
    ReceiptStatus,
    ServiceStatus,
    SignerCapacity,
)

LEGAL_NOTICE = "Comprobante interno sin efectos fiscales. No es un CFDI ni sustituye a uno."
"""§8.1 y §16. Va al pie de los dos formatos de recibo, sin excepción: es lo que
impide que el comprobante se confunda con un CFDI."""

SERVICE_STATUS = {
    ServiceStatus.OPEN: "Abierto",
    ServiceStatus.SETTLED: "Liquidado",
    ServiceStatus.CLOSED_UNCOLLECTED: "No cobrado",
}

RECEIPT_STATUS = {
    ReceiptStatus.ISSUED: "Emitido",
    ReceiptStatus.CANCELLED: "Cancelado",
}

PAYMENT_STATUS = {
    PaymentStatus.ACTIVE: "Activo",
    PaymentStatus.CANCELLED: "Cancelado",
}

PAYMENT_METHOD = {
    PaymentMethod.CASH: "Efectivo",
    PaymentMethod.TRANSFER: "Transferencia",
}

# El snapshot guarda el método como texto plano (§4.3), así que la plantilla del
# recibo lo busca por su valor y no por el miembro del enum.
PAYMENT_METHOD_BY_VALUE = {method.value: label for method, label in PAYMENT_METHOD.items()}


def accounting_status(payment_status: PaymentStatus, receipt_status: ReceiptStatus | None) -> str:
    """Por qué un renglón del reporte cuenta o no cuenta para el ingreso (§9.2).

    Distingue el pago cancelado del recibo cancelado aunque hoy se cancelen
    juntos: si el emisor abre el reporte para entender por qué le falta dinero,
    la respuesta tiene que estar en la fila y no en su memoria.
    """
    if receipt_status is ReceiptStatus.CANCELLED:
        return "Recibo cancelado"
    return PAYMENT_STATUS[payment_status]


# §14.1, resuelto: en qué calidad firma quien firma. Se imprime bajo su nombre,
# porque sin ese dato se reproduce el "yo no fui" cuando firma un tercero.
SIGNER_CAPACITY = {
    SignerCapacity.HOLDER: "Titular",
    SignerCapacity.FAMILY: "Familiar del titular",
    SignerCapacity.MANAGER: "Encargado del lugar",
    SignerCapacity.OTHER: "Otra persona autorizada",
}
