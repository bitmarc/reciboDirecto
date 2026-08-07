"""Planes, límites, cupones e instrumentación de bloqueos (E8)."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from receipt.domain.enums import DiscountType
from receipt.models.base import Base, CreatedAtMixin, PrimaryKeyMixin, pg_enum


class UsageCounter(Base):
    """Consumo mensual del plan gratuito (§4.2).

    Los recibos cancelados **sí** cuentan (RN-33): si no, el límite sería
    evadible cancelando.
    """

    __tablename__ = "usage_counter"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    period: Mapped[str] = mapped_column(Text, primary_key=True)
    """`YYYY-MM` en zona `America/Mexico_City`, no en UTC."""

    receipts_issued: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PlanBlockEvent(Base, PrimaryKeyMixin):
    """Cada vez que el límite del plan gratuito bloquea una emisión.

    §11.2 declara esta instrumentación **obligatoria** y AC-15 exige que el
    evento quede registrado. Es el dato que permitirá distinguir "el producto no
    le gustó" de "topó el muro" cuando haya que revisar §14.3 con datos.
    """

    __tablename__ = "plan_block_event"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period: Mapped[str] = mapped_column(Text)
    attempted_receipt_number: Mapped[int] = mapped_column(Integer)
    """Ordinal del recibo que se intentó emitir dentro del periodo."""


class Coupon(Base, PrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "coupon"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupon_code"),
        CheckConstraint("discount_value > 0", name="discount_value_positive"),
        CheckConstraint(
            "max_redemptions IS NULL OR max_redemptions > 0",
            name="max_redemptions_positive",
        ),
    )

    code: Mapped[str] = mapped_column(Text)
    discount_type: Mapped[DiscountType] = mapped_column(pg_enum(DiscountType, "discount_type"))
    discount_value: Mapped[int] = mapped_column(Integer)
    """Porcentaje, centavos o número de meses según `discount_type`."""

    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class CouponRedemption(Base, PrimaryKeyMixin):
    """§4.2 nombra esta tabla pero no define sus columnas; se definen aquí.

    La restricción única impide que una misma cuenta canjee dos veces el mismo
    código.
    """

    __tablename__ = "coupon_redemption"
    __table_args__ = (
        UniqueConstraint(
            "coupon_id", "account_id", name="uq_coupon_redemption_coupon_id_account_id"
        ),
    )

    coupon_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("coupon.id"), index=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
