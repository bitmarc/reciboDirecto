"""Cómo se llama en español cada valor del dominio (§0, §16).

Los identificadores van en inglés y el copy en español. La traducción vive aquí
y no en cada plantilla ni en cada ruta: un mismo estado tiene que leerse igual en
`/trabajos`, en `/recibos` y en el detalle del cliente. `templating` las registra
como globales de Jinja, así que ninguna ruta necesita pasarlas en el contexto.
"""

from receipt.domain.enums import PaymentMethod, PaymentStatus, ReceiptStatus, ServiceStatus

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
