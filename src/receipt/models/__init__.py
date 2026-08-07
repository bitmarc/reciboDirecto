"""Modelos de SQLAlchemy.

Este módulo importa todas las tablas para que `Base.metadata` esté completo
cuando Alembic autogenere migraciones. Un modelo que no se importe aquí es un
modelo que Alembic no ve.
"""

from receipt.models.account import Account, AccountCounter, AuthToken, User
from receipt.models.base import Base
from receipt.models.billing import (
    Coupon,
    CouponRedemption,
    PlanBlockEvent,
    UsageCounter,
)
from receipt.models.client import Client
from receipt.models.receipt import IdempotencyKey, Receipt
from receipt.models.service import Payment, Service, ServiceAdjustment

__all__ = [
    "Account",
    "AccountCounter",
    "AuthToken",
    "Base",
    "Client",
    "Coupon",
    "CouponRedemption",
    "IdempotencyKey",
    "Payment",
    "PlanBlockEvent",
    "Receipt",
    "Service",
    "ServiceAdjustment",
    "UsageCounter",
    "User",
]
