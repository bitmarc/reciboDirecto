"""Servicios (trabajos), sus montos extra y sus pagos.

Invariante estructural de §4.1: `Service (1) → Payment (N) → Receipt (0..1)`.
Los recibos no son entidades sueltas.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from receipt.domain.enums import PaymentMethod, PaymentStatus, ServiceStatus
from receipt.models.base import (
    Base,
    CreatedAtMixin,
    PrimaryKeyMixin,
    TimestampMixin,
    pg_enum,
)


class Service(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "service"
    __table_args__ = (
        UniqueConstraint("account_id", "code", name="uq_service_account_id_code"),
        Index("ix_service_account_id_status", "account_id", "status"),
        # RN-17: el motivo de cierre por incobrable es obligatorio.
        CheckConstraint(
            "status <> 'closed_uncollected'"
            " OR (closed_reason IS NOT NULL AND btrim(closed_reason) <> '')",
            name="closed_uncollected_requires_reason",
        ),
        CheckConstraint(
            "agreed_total_cents IS NULL OR agreed_total_cents > 0",
            name="agreed_total_positive",
        ),
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("client.id"), index=True)

    code: Mapped[str] = mapped_column(Text)
    """Folio del servicio, `SRV-0001`. Secuencial por cuenta (RN-25)."""

    concept: Mapped[str] = mapped_column(Text)
    location_alias: Mapped[str | None] = mapped_column(Text)
    location_address: Mapped[str | None] = mapped_column(Text)

    agreed_total_cents: Mapped[int | None] = mapped_column(BigInteger)
    """INMUTABLE tras su creación (RN-11). No existe endpoint de edición y un
    trigger de PostgreSQL rechaza cualquier UPDATE que lo cambie, incluso por SQL
    directo (AC-01). `NULL` = pago único sin total declarado (RN-15)."""

    status: Mapped[ServiceStatus] = mapped_column(
        pg_enum(ServiceStatus, "service_status"),
        default=ServiceStatus.OPEN,
        server_default=ServiceStatus.OPEN.value,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(Text)
    uncollected_cents: Mapped[int | None] = mapped_column(BigInteger)
    """Monto dado por perdido al cerrar. Sale de "por cobrar" pero se conserva
    para el reporte de dinero perdido (RN-17, §9.1)."""

    adjustments: Mapped[list["ServiceAdjustment"]] = relationship(
        back_populates="service", order_by="ServiceAdjustment.created_at"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="service", order_by="Payment.paid_at"
    )


class ServiceAdjustment(Base, PrimaryKeyMixin, CreatedAtMixin):
    """Monto extra agregado a un servicio ya iniciado (RN-12)."""

    __tablename__ = "service_adjustment"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
    )

    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.id", ondelete="CASCADE"), index=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    """Obligatorio. Aparece textualmente en los recibos posteriores (RN-12)."""

    service: Mapped[Service] = relationship(back_populates="adjustments")


class Payment(Base, PrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "payment"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        # RN-14: el material se resta del ingreso, así que nunca puede excederlo.
        CheckConstraint(
            "material_cents >= 0 AND material_cents <= amount_cents",
            name="material_within_amount",
        ),
        Index("ix_payment_service_id_status", "service_id", "status"),
        Index("ix_payment_paid_at", "paid_at"),
    )

    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.id", ondelete="CASCADE"), index=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    material_cents: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    """Registro interno. Se resta del ingreso (RN-14) y **nunca** se muestra en
    el recibo ni se desglosa (§8.3)."""

    method: Mapped[PaymentMethod] = mapped_column(pg_enum(PaymentMethod, "payment_method"))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """Fecha de pago = fecha de corte contable (RN-13, §9.1). La diferencia
    contra `created_at` mide si el emisor captura en sitio o en diferido (§17)."""

    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        default=PaymentStatus.ACTIVE,
        server_default=PaymentStatus.ACTIVE.value,
    )
    """Un pago se cancela junto con su recibo y deja de contar para el saldo
    (RN-22)."""

    service: Mapped[Service] = relationship(back_populates="payments")
