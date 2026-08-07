"""Enumeraciones del dominio.

Viven aquí, no en los modelos, porque las reglas de negocio se razonan y se
prueban sin base de datos. Los modelos de SQLAlchemy las importan.
"""

from enum import StrEnum


class Plan(StrEnum):
    FREE = "free"
    PAID = "paid"


class ServiceStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"
    CLOSED_UNCOLLECTED = "closed_uncollected"


class PaymentMethod(StrEnum):
    CASH = "cash"
    TRANSFER = "transfer"


class PaymentStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class ReceiptTemplate(StrEnum):
    SINGLE = "single"
    DETAILED = "detailed"


class ReceiptStatus(StrEnum):
    ISSUED = "issued"
    CANCELLED = "cancelled"


class SignerCapacity(StrEnum):
    """Calidad de quien firma el recibo (§14.1, decidido).

    Sin este dato se reproduce el problema de "yo no fui", que es justamente uno
    de los escenarios que motivan pedir firma.
    """

    HOLDER = "holder"
    FAMILY = "family"
    MANAGER = "manager"
    OTHER = "other"


class CounterKind(StrEnum):
    """Contadores secuenciales por cuenta (RN-25)."""

    SERVICE = "service"
    RECEIPT = "receipt"


class AuthTokenPurpose(StrEnum):
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"


class DiscountType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"
    FREE_MONTHS = "free_months"
